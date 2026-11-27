"""网站订单 Web orders: the order fulfilment screen on the shop computer (owner, 2026-11-19: "we just want to show the
information and not be able to change too many stuff"). Built from the popcore.ca website session's brief of
2026-11-19; see docs/web-orders-board.md.

- The board shows the paid web orders of the stores a user has 门店操作权限 Store operations access for, in five
  columns by kind and website status, with the orders that need a manager and those done today under them. A delivery
  shows at the store it leaves from (owner, 2026-11-26, popcore-work#121: the courier collects from the store the
  order was filled from): the store the website books FleetNow from once it has one, else the store POPCORE took it
  from; see delivery_stores. It never holds the pickup code, and the only personal thing in it is the
  name each delivery goes to (owner, 2026-11-25), read live from the website on every refresh and never stored.
- Pickup buttons: Ready (processing → ready-pickup; the website emails the customer the code) and Handed over
  (ready-pickup or processing → completed), which needs the code the customer gives at the counter. Each reads the
  order's status live first, waits for the website's 200 before anything shows as done, and leaves a private note
  saying who did it. An order already there counts as done.
- Delivery buttons (owner, 2026-11-27, popcore-work#139): 打包好了 Packed moves a delivery from 待打包 to 等快递,
  and 撤销 Undo moves it back. POPCORE keeps who and when; the website gets only a private note. Deliveries go by
  packing, not booking: one booked before it is packed stays in 待打包, showing the courier's pickup window.
- The code box reads the customer's first name and the optional collector's name live from the website for that one
  order. They are never stored or logged.
- The board never cancels, refunds or edits an order, sends an email, books FleetNow, moves stock or sets a delivery
  order's status.
"""
import json
from datetime import datetime, time, timezone

from website_sync import (BATCH, CODE_META, TORONTO, WebsiteError, _meta, call, clean_code, taken_stores)

# The five columns, left to right (brief, 2026-11-19). Pickups go by website status. Deliveries go by packing (owner,
# 2026-11-27, popcore-work#139): 'pack' until Packed, then 'booked' (等快递 packed, waiting for the courier; the key
# is kept so a page opened before this still reads the board) until FleetNow picks it up ('out').
COLUMNS = ('pick', 'ready', 'pack', 'booked', 'out')
PICKUP_COLUMNS = {'processing': 'pick', 'ready-pickup': 'ready'}
# The website statuses a delivery is packed (or unpacked) in: paid, with a courier booked or not.
PACKABLE = ('processing', 'dispatched')
CLOSED = ('completed', 'cancelled', 'refunded')
# Orders POPCORE could not take out of stock, or put back after a cancel: a manager settles them first.
WAITING = ('needs_review', 'cancel_failed')
# What each button moves a pickup order to, and from.
STEPS = {'ready': ('ready-pickup', ('processing',), 'Ready'),
         'handed_over': ('completed', ('ready-pickup', 'processing'), 'Handed over')}
# Packed and its Undo on a delivery: the words of the website note.
PACK_STEPS = {'packed': 'Packed', 'unpack': 'Packed undone'}
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


def column_of(row):
    """The column of an open order, or None when its website status is none of the board's."""
    status = row['wc_status']
    if kind(row) == 'pickup':
        return PICKUP_COLUMNS.get(status)
    if status == 'out-for-delivery':
        return 'out'
    if status in PACKABLE:
        return 'booked' if row['packed_at'] else 'pack'
    return None


def store_of(row, cfg):
    """A pickup's store: its pickup store. (For a delivery, the delivery store; its boards are delivery_stores.)"""
    return (row['pickup_store'] or cfg['delivery_store']) if kind(row) == 'pickup' else cfg['delivery_store']


def collect_store(row, cfg):
    """The store the courier collects a delivery from, or None while the website has not booked it: the store the
    website names (`_fleetnow_pickup_store`, FleetNow 1.2.13), else WEBSITE_DELIVERY_STORE for an order FleetNow has
    a number for (every booking went from there before, and still does while the website's switch is off)."""
    delivery = json.loads(row['delivery'] or '{}')
    named = str(delivery.get('collect') or '').strip().upper()
    return named or (cfg['delivery_store'] if delivery.get('fleetnow_no') else None)


def delivery_stores(con, row, cfg):
    """(the stores a delivery leaves from, the other stores its stock has to be brought from), as store codes.
    - Booked: the store the courier collects from; stock POPCORE took elsewhere has to be brought there.
    - Not booked yet: the store POPCORE took it from, where the courier is expected. Taken from two stores, it is
      both stores' until staff bring it together (the website books no courier for it).
    - Not taken out of stock (it waits for a manager): WEBSITE_DELIVERY_STORE."""
    taken, collect = taken_stores(con, row), collect_store(row, cfg)
    if collect:
        return [collect], [code for code in taken if code != collect]
    return taken or [cfg['delivery_store']], []


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
    if kind(row) == 'pickup':
        # Stock taken from another store has to be brought over to the pickup store.
        stores = [store_of(row, cfg)]
        elsewhere = sorted({pick['store'] for found in picks.values() for pick in found} - set(stores))
    else:
        stores, elsewhere = delivery_stores(con, row, cfg)
    # `stores`: the boards the order is on. `store`: where it is handed over (a delivery in two stores: the first).
    result = {'id': row['wc_order_id'], 'number': row['number'], 'paid_at': row['paid_at'], 'kind': kind(row),
              'store': stores[0], 'stores': stores, 'status': row['wc_status'], 'lines': lines,
              'taken_elsewhere': elsewhere}
    if kind(row) == 'delivery':
        delivery = json.loads(row['delivery'] or '{}')
        result.update(delivery=delivery,
                      # A courier is on its way to the store: FleetNow has a number for it, or the website says so.
                      booked=bool(delivery.get('fleetnow_no')) or row['wc_status'] == 'dispatched',
                      packed_at=row['packed_at'], packed_by=row['packed_by'])
    return result


def board_stores(item):
    """The stores whose boards show a card: a pickup's store; a delivery's, and the stores its stock has to be
    brought from."""
    return {*item['stores'], *(item['taken_elsewhere'] if item['kind'] == 'delivery' else ())}


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
    columns = {name: [] for name in COLUMNS}
    needs_manager, done_today = [], []
    today = _today_starts()
    for row in con.execute(
            """SELECT * FROM website_orders
               WHERE wc_status NOT IN ('completed', 'cancelled', 'refunded') OR state IN ('needs_review', 'cancel_failed')
                  OR modified_at >= ?
               ORDER BY paid_at, wc_order_id""", (today,)):
        item = card(con, row, cfg)
        if not board_stores(item) & allowed:
            continue
        if row['state'] in WAITING:
            needs_manager.append({**item, 'reason': row['state'] if row['state'] == 'cancel_failed' else row['problem']})
        elif row['wc_status'] in CLOSED:
            if (row['modified_at'] or '') >= today:
                done_today.append({**item, 'done_at': row['modified_at']})
        else:
            column = column_of(row)
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
    # The name box is for a board that has deliveries, or is the delivery store's.
    return {'stores': sorted(allowed), 'deliveries': bool(deliveries) or cfg['delivery_store'] in allowed,
            'names_read': names_read,
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
        raise BoardError('Only a pickup order is marked Ready or Handed over; a delivery is marked Packed.', 'not_pickup')
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
    return 'done', _note(con, cfg, wc_order_id, label, by or sub)


def _note(con, cfg, wc_order_id, label, sub):
    """A private note on the website order naming who did it; whether the website took it."""
    try:
        call(cfg, 'POST', f'/orders/{wc_order_id}/notes',
             body={'note': f'{label} in popcore.store by {_actor_name(con, sub)}', 'customer_note': False})
        return True
    except WebsiteError:
        return False


def pack(con, cfg, wc_order_id, action, sub):
    """打包好了 Packed ('packed') or 撤销 Undo ('unpack') on one delivery (owner, 2026-11-27, popcore-work#139): 'done',
    or 'already' when it was so. POPCORE keeps when and who; the website gets a private note and nothing else, so
    the customer hears nothing and FleetNow is not booked. Returns (result, whether the note was added)."""
    row = _row(con, wc_order_id)
    if kind(row) != 'delivery':
        raise BoardError('Only a delivery is marked Packed; a pickup is marked Ready.', 'not_delivery')
    if row['state'] in WAITING:
        raise BoardError('An admin settles this order on the 网站 page first.', 'needs_manager')
    # The store that packs it: the one it leaves from (either, while its stock is in two).
    if not set(delivery_stores(con, row, cfg)[0]) & user_stores(con, sub):
        raise BoardError('This order leaves from a store you have no store operations access for.', 'forbidden', 403)
    status = _live(cfg, wc_order_id)['status']
    if status != row['wc_status']:
        _keep_status(con, wc_order_id, status)
    if status not in PACKABLE:
        raise BoardError(f'The website has this order as “{status}” now, so it cannot be marked here.', 'status_changed')
    packing = action == 'packed'
    if bool(row['packed_at']) == packing:
        return 'already', None
    con.execute("""UPDATE website_orders SET packed_at=CASE WHEN ? THEN strftime('%Y-%m-%dT%H:%M:%S', 'now') END,
                       packed_by=?, updated_at=datetime('now') WHERE wc_order_id=?""",
                (packing, _actor_name(con, sub) if packing else None, wc_order_id))
    con.commit()
    return 'done', _note(con, cfg, wc_order_id, PACK_STEPS[action], sub)
