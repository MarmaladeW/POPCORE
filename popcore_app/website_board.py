"""网站订单 Web orders: the order fulfilment screen on the shop computer (owner, 2026-11-19: "we just want to show the
information and not be able to change too many stuff"). Built from the popcore.ca website session's brief of
2026-11-19; see docs/web-orders-board.md.

- The board shows the paid web orders of the stores a user has 门店操作权限 Store operations access for, in five
  columns by kind and website status, with the orders that need a manager and those done today under them. Deliveries
  all go out of WEBSITE_DELIVERY_STORE (DT). It never holds the pickup code, and the only personal thing in it is the
  name each delivery goes to (owner, 2026-11-25), read live from the website on every refresh and never stored.
- Two buttons, both pickup only: Ready (processing → ready-pickup; the website emails the customer the code) and
  Handed over (ready-pickup or processing → completed), which needs the code the customer gives at the counter. Each
  reads the order's status live first, waits for the website's 200 before anything shows as done, and leaves a
  private note saying who did it. An order already there counts as done.
- The code box reads the customer's first name and the optional collector's name live from the website for that one
  order. They are never stored or logged.
- The board never cancels, refunds or edits an order, sends an email, books FleetNow, moves stock or sets a delivery
  order's status.
"""
import json
from datetime import datetime, time, timezone

from website_sync import (BATCH, CODE_META, TORONTO, WebsiteError, _meta, call, clean_code)

# (column, kind, website status), left to right (brief, 2026-11-19).
COLUMNS = (('pick', 'pickup', 'processing'), ('ready', 'pickup', 'ready-pickup'),
           ('pack', 'delivery', 'processing'), ('booked', 'delivery', 'dispatched'),
           ('out', 'delivery', 'out-for-delivery'))
CLOSED = ('completed', 'cancelled', 'refunded')
# Orders POPCORE could not take out of stock, or put back after a cancel: a manager settles them first.
WAITING = ('needs_review', 'cancel_failed')
# What each button moves a pickup order to, and from.
STEPS = {'ready': ('ready-pickup', ('processing',), 'Ready'),
         'handed_over': ('completed', ('ready-pickup', 'processing'), 'Handed over')}
COLLECTOR_META = '_pcc_collector_name'
# Owner, 2026-11-25: "we should be able to see the name for the delivery orders, as well as searching up the name."
# Only these four fields are asked for; nothing else of the customer's details leaves the website.
NAME_FIELDS = 'id,shipping.first_name,shipping.last_name,billing.first_name,billing.last_name'


class BoardError(Exception):
    def __init__(self, message, code, status=409):
        super().__init__(message)
        self.code, self.status = code, status


def kind(row):
    return 'pickup' if row['fulfilment'] == 'pickup' else 'delivery'


def store_of(row, cfg):
    """The store whose board shows the order: its pickup store, or the delivery store for a delivery."""
    return (row['pickup_store'] or cfg['delivery_store']) if kind(row) == 'pickup' else cfg['delivery_store']


def user_stores(con, sub):
    """The store codes the user has 门店操作权限 Store operations access for."""
    return {row[0] for row in con.execute(
        """SELECT s.code FROM inventory_access a JOIN stores s ON s.id=a.store_id
           WHERE a.auth0_sub=? AND s.is_active=1""", (sub,))}


def _today_starts():
    """Today's Toronto midnight as a website GMT time."""
    local = datetime.combine(datetime.now(TORONTO).date(), time(), TORONTO)
    return local.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')


def _picks(con, document_id):
    """{product id: [{store, place, quantity}]} the order's consume document took, in its order."""
    picks = {}
    if not document_id:
        return picks
    for row in con.execute(
            """SELECT d.product_id, d.quantity, l.code AS place, s.code AS store FROM inventory_document_lines d
               JOIN inventory_locations l ON l.id=d.from_location_id JOIN stores s ON s.id=l.store_id
               WHERE d.document_id=? ORDER BY d.line_no""", (document_id,)):
        picks.setdefault(row['product_id'], []).append(
            {'store': row['store'], 'place': row['place'], 'quantity': row['quantity']})
    return picks


def card(con, row, cfg):
    """What the board shows of one order: nothing personal, never the pickup code. A push notification is made from
    it too; the delivery name is added by board() only."""
    picks = _picks(con, row['inventory_document_id'])
    lines, shown = [], set()
    for line in json.loads(row['lines'] or '[]'):
        product = con.execute('SELECT jizhanming, name_cn_en FROM products WHERE id=?',
                              (line['product_id'],)).fetchone() if line.get('product_id') else None
        # Staff know products by 记账名, else the official name, else the website's name (owner, 2026-11-19).
        name = ((product['jizhanming'] or '').strip() or product['name_cn_en']) if product else None
        product_id = line.get('product_id')
        lines.append({'name': name or line.get('name') or line.get('popcore_id') or '', 'quantity': line['quantity'],
                      'popcore_id': line.get('popcore_id') or None,
                      # Where it was taken from, on the first line of its product.
                      'picks': picks.get(product_id, []) if product_id not in shown else []})
        shown.add(product_id)
    store = store_of(row, cfg)
    taken = sorted({pick['store'] for found in picks.values() for pick in found})
    result = {'id': row['wc_order_id'], 'number': row['number'], 'paid_at': row['paid_at'], 'kind': kind(row),
              'store': store, 'status': row['wc_status'], 'lines': lines}
    if kind(row) == 'pickup':
        # Stock taken from another store has to be brought over to the pickup store.
        result['taken_elsewhere'] = [code for code in taken if code != store]
    else:
        result['delivery'] = json.loads(row['delivery'] or '{}')
    return result


def _full_name(part):
    part = part if isinstance(part, dict) else {}
    return ' '.join(filter(None, (str(part.get(key) or '').strip() for key in ('first_name', 'last_name'))))


def delivery_names(cfg, ids):
    """{order id: the name the parcel goes to} as the website has it now: the shipping first and last name, else the
    billing name. One request per 100 orders, asking for the names only; POPCORE never stores or logs them."""
    ids, names = sorted(set(ids)), {}
    for start in range(0, len(ids), BATCH):
        part = ids[start:start + BATCH]
        # A short wait: the board shows without names rather than hang on a slow website.
        data, _ = call(cfg, 'GET', '/orders', params={'include': ','.join(map(str, part)), 'status': 'any',
                                                      '_fields': NAME_FIELDS, 'per_page': BATCH}, timeout=(5, 10))
        for order in data if isinstance(data, list) else ():
            name = isinstance(order, dict) and (_full_name(order.get('shipping')) or _full_name(order.get('billing')))
            if name and order.get('id') in part:
                names[order['id']] = name[:120]
    return names


def board(con, cfg, sub, store=None):
    """The board for the stores the user may see (`store`: one of them only). Each delivery card carries the name it
    goes to, read live (`names_read` is False when the website did not answer, and the cards have none)."""
    allowed = user_stores(con, sub)
    if store:
        allowed &= {store.upper()}
    columns = {name: [] for name, _, _ in COLUMNS}
    needs_manager, done_today = [], []
    today = _today_starts()
    for row in con.execute(
            """SELECT * FROM website_orders
               WHERE wc_status NOT IN ('completed', 'cancelled', 'refunded') OR state IN ('needs_review', 'cancel_failed')
                  OR modified_at >= ?
               ORDER BY paid_at, wc_order_id""", (today,)):
        if store_of(row, cfg) not in allowed:
            continue
        item = card(con, row, cfg)
        if row['state'] in WAITING:
            needs_manager.append({**item, 'reason': row['state'] if row['state'] == 'cancel_failed' else row['problem']})
        elif row['wc_status'] in CLOSED:
            if (row['modified_at'] or '') >= today:
                done_today.append({**item, 'done_at': row['modified_at']})
        else:
            column = next((name for name, wanted, status in COLUMNS
                           if wanted == item['kind'] and status == row['wc_status']), None)
            if column:
                columns[column].append(item)
            else:
                needs_manager.append({**item, 'reason': 'status'})
    done_today.sort(key=lambda item: item['done_at'] or '', reverse=True)
    deliveries = [item for cards in (*columns.values(), needs_manager, done_today) for item in cards
                  if item['kind'] == 'delivery']
    names, names_read = {}, True
    if deliveries and cfg.get('url'):
        try:
            names = delivery_names(cfg, [item['id'] for item in deliveries])
        except WebsiteError:
            names_read = False
    for item in deliveries:
        item['name'] = names.get(item['id'])
    return {'stores': sorted(allowed), 'deliveries': cfg['delivery_store'] in allowed, 'names_read': names_read,
            'columns': columns, 'needs_manager': needs_manager, 'done_today': done_today}


def _row(con, wc_order_id):
    row = con.execute('SELECT * FROM website_orders WHERE wc_order_id=?', (wc_order_id,)).fetchone()
    if row is None:
        raise BoardError('This web order is not in POPCORE.', 'not_found', 404)
    return row


def _live(cfg, wc_order_id, names=False):
    """The order's status, pickup code and pickup store as the website has them now, and with `names` the
    customer's first name and the collector's name. Everything else the website sends (the rest of the billing
    details) is dropped here, before anything else can see it."""
    try:
        order, _ = call(cfg, 'GET', f'/orders/{wc_order_id}',
                        params={'_fields': 'id,status,billing,meta_data' if names else 'id,status,meta_data'})
    except WebsiteError as exc:
        if exc.status == 404:
            raise BoardError('The website no longer has this order.', 'not_found', 404) from None
        raise
    order = order if isinstance(order, dict) else {}
    found = {'status': str(order.get('status') or ''), 'code': clean_code(_meta(order, CODE_META)),
             'pickup_store': str(_meta(order, '_popcore_pickup_store_id') or '').strip().upper()}
    if names:
        billing = order.get('billing') if isinstance(order.get('billing'), dict) else {}
        collector = _meta(order, COLLECTOR_META)
        found['first_name'] = str(billing.get('first_name') or '').strip()[:80]
        found['collector'] = collector.strip()[:80] if isinstance(collector, str) else ''
    return found


def _keep_status(con, wc_order_id, status, changed=False):
    """The board shows a status read live at once; the sync's next read brings the rest. `changed`: POPCORE just set
    it, so the order counts as changed now (a hand-over shows under Done today at once)."""
    con.execute("""UPDATE website_orders SET wc_status=?, updated_at=datetime('now'),
                       modified_at=CASE WHEN ? THEN strftime('%Y-%m-%dT%H:%M:%S', 'now') ELSE modified_at END
                   WHERE wc_order_id=?""", (str(status or '')[:40], changed, wc_order_id))
    con.commit()


def find_code(con, cfg, code, sub, store=None):
    """The pickup order for a code typed at the counter, or why there is none. Only for that order, and only once
    its live status allows a hand-over, the customer's first name and the collector's name are read from the website
    and returned; POPCORE never keeps them."""
    code = clean_code(code)
    # Codes are 6 of 23 characters, so over the years an old closed order may share one: the open order wins.
    row = con.execute("""SELECT * FROM website_orders WHERE pickup_code=?
                         ORDER BY wc_status IN ('completed', 'cancelled', 'refunded'), paid_at DESC LIMIT 1""",
                      (code,)).fetchone() if code else None
    if row is None:
        return {'found': False, 'why': 'not_found'}
    if kind(row) != 'pickup':
        return {'found': False, 'why': 'not_pickup'}
    live = _live(cfg, row['wc_order_id'], names=True)
    if live['code'] != code:
        return {'found': False, 'why': 'not_found'}  # the website gave the order another code since
    if live['status'] != row['wc_status']:
        _keep_status(con, row['wc_order_id'], live['status'])
    if live['status'] in CLOSED:
        return {'found': False, 'why': live['status']}
    pickup = live['pickup_store'] or store_of(row, cfg)
    if pickup not in user_stores(con, sub) or (store and pickup != store.upper()):
        return {'found': False, 'why': 'other_store', 'store': pickup}
    if live['status'] not in STEPS['handed_over'][1]:
        return {'found': False, 'why': 'status', 'status': live['status']}
    return {'found': True, 'order': {**card(con, row, cfg), 'status': live['status'], 'store': pickup},
            'first_name': live['first_name'], 'collector': live['collector'] or None,
            # POPCORE could not record its stock: staff still hand it over, and a manager settles it.
            'needs_manager': row['state'] in WAITING}


def _actor_name(con, sub):
    row = con.execute("SELECT name FROM employees WHERE auth0_id=? AND COALESCE(name, '') != ''", (sub,)).fetchone()
    return row[0] if row else sub


def step(con, cfg, wc_order_id, action, sub, code=None, by=None):
    """Ready or Handed over on one pickup order: 'done', or 'already' when the website was there first. Returns
    (result, whether the note naming who did it was added)."""
    target, starts, label = STEPS[action]
    row = _row(con, wc_order_id)
    if kind(row) != 'pickup':
        raise BoardError('Only a pickup order is marked here; FleetNow moves deliveries.', 'not_pickup')
    if store_of(row, cfg) not in user_stores(con, sub):
        raise BoardError('This order is for a store you have no store operations access for.', 'forbidden', 403)
    if action == 'handed_over' and (not row['pickup_code'] or clean_code(code) != row['pickup_code']):
        raise BoardError('Type the customer’s pickup code first.', 'code_needed', 403)
    live = _live(cfg, wc_order_id)
    status = live['status']
    if action == 'handed_over' and live['code'] != row['pickup_code']:
        raise BoardError('Type the customer’s pickup code first.', 'code_needed', 403)
    if status != row['wc_status']:
        _keep_status(con, wc_order_id, status)
    if status == target:
        return 'already', None
    if status not in starts:
        raise BoardError(f'The website has this order as “{status}” now, so it cannot be marked {label} here.',
                         'status_changed')
    call(cfg, 'PUT', f'/orders/{wc_order_id}', body={'status': target})  # raises unless the website says 2xx
    _keep_status(con, wc_order_id, target, changed=True)
    try:
        call(cfg, 'POST', f'/orders/{wc_order_id}/notes',
             body={'note': f'{label} in popcore.store by {_actor_name(con, by or sub)}', 'customer_note': False})
        noted = True
    except WebsiteError:
        noted = False
    return 'done', noted
