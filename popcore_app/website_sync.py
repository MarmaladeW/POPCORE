"""POPCORE owns the website's stock (owner, 2026-10-25 and 2026-11-01).

popcore.ca (WooCommerce) takes its stock from POPCORE. Every website product and variation is
linked by SKU to the POPCORE product with the same SKU: the Single Box variation or a simple
product carries the POPCORE ID, the Whole Set variation `<ID>-SET` (the sealed set) and a design
`<ID>-NN` (the confirmed design). An item whose SKU is shaped like a POPCORE ID that POPCORE does
not have shows sold out, since POPCORE holds none of it (a Whole Set nobody counted as sets).
Other SKUs (barcodes) and items without one are left alone.

- Stock: the website number is the saleable stock of the live stores' shelves (floor, upstairs,
  warehouse; never display pieces or the claw machine), less boxes kept in opened sets, less what
  the shop may have sold since its last daily report (below), less the units of paid web orders
  POPCORE could not record yet, less WEBSITE_KEEP_BACK (0 by default). A store that is not live
  yet adds nothing, and is not offered for pickup.
- The shop's sales reach POPCORE only with the pasted daily report until Clover is connected. Until
  a shop day's report is in, each product holds back, at that store, what the store sold of it on
  its best day of the last 30 reports, once for every shop day not reported; a shop day starts at
  WEBSITE_SHOP_OPENS (owner, 2026-11-02). Once the report is in, nothing is held until the shop opens.
- Pickup: a product's `_popcore_pickup_stores` lists the live stores that have it.
- Orders: a paid web order takes its items out of the filling store's stock (the pickup store, or
  WEBSITE_DELIVERY_STORE for delivery), back stock first and the floor last, as a separate
  Website order outside the store's sales and closing (owner, 2026-11-01). A cancelled order puts
  them back. An order POPCORE cannot fill waits for a manager and holds its units off the website.
- Prices stay on the website (owner, 2026-11-01).

Nothing is written to the website until an admin turns the sync on. The background job runs every
30 seconds: it reads new and changed orders first, then pushes only what changed since POPCORE
last set it. A full reread of the website's catalogue every day catches edits made there.
"""
import json
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

import db
from auth import ROLE_CLAIM
from blueprints.products import BRAND_CODES
from goods_operations import _balance
from inventory_commands import InventoryError, _post_inventory_in_transaction, live_store_ids
from report_stock import _stock_product, live_since

TORONTO = ZoneInfo('America/Toronto')
# The website's own actor. It never comes from a login: Auth0 subjects always contain '|'.
WEBSITE_SUB = 'popcore:website'
WEBSITE_NAME = 'Website'
SOURCE_ORDER = 'website_order'
SOURCE_CANCEL = 'website_order_cancel'
ORDER_REASON = '网站订单 Website order'
CANCEL_REASON = '网站订单取消 Website order cancelled'
SHELF_LOCATIONS = ('floor', 'upstairs', 'warehouse')
# Web orders are picked from back stock first, so the floor stays as the shop's daily report expects.
PICK_ORDER = ('upstairs', 'warehouse', 'floor')
PICKUP_META = '_popcore_pickup_stores'
# A paid order is one with date_paid; these statuses never take stock.
NOT_TAKEN = ('pending', 'failed', 'checkout-draft', 'trash')
POPCORE_ID = re.compile(f"(?:{'|'.join(BRAND_CODES)})[0-9]{{4}}(?:-SET|-[0-9]{{2}})?")
REFRESH_EVERY = timedelta(hours=20)
BATCH = 100
# Report days the shop's best day is taken from.
REPORT_DAYS = 30


class WebsiteError(Exception):
    def __init__(self, message, code='website_error', status=None):
        super().__init__(message)
        self.code, self.status = code, status


def website_actor():
    return {'sub': WEBSITE_SUB, ROLE_CLAIM: 'manager'}


# ── Settings and the WooCommerce REST API ───────────────────────────────────

def config():
    """Website settings from the environment, or None when no website key is set."""
    url = os.environ.get('WEBSITE_URL', '').strip().rstrip('/')
    key = os.environ.get('WEBSITE_KEY', '').strip()
    secret = os.environ.get('WEBSITE_SECRET', '').strip()
    if not (url and key and secret):
        return None
    parts = urlsplit(url)
    loopback = parts.hostname in ('127.0.0.1', 'localhost')
    if (parts.scheme not in ('https', 'http') or (parts.scheme == 'http' and not loopback)
            or parts.username or parts.password or parts.query or parts.fragment):
        raise ValueError('WEBSITE_URL must be the https address of the website')
    keep_back = os.environ.get('WEBSITE_KEEP_BACK', '0').strip() or '0'
    if not keep_back.isdigit():
        raise ValueError('WEBSITE_KEEP_BACK must be a whole number')
    delivery = os.environ.get('WEBSITE_DELIVERY_STORE', 'DT').strip().upper() or 'DT'
    try:  # DT opens at noon (popcore.ca's store hours)
        opens = datetime.strptime(os.environ.get('WEBSITE_SHOP_OPENS', '12:00').strip() or '12:00', '%H:%M').time()
    except ValueError:
        raise ValueError('WEBSITE_SHOP_OPENS must be a time such as 12:00') from None
    return {'url': url, 'key': key, 'secret': secret, 'keep_back': int(keep_back), 'delivery_store': delivery,
            'opens': opens}


def call(cfg, method, path, *, params=None, body=None, timeout=(5, 60)):
    """One WooCommerce REST call; returns (data, headers)."""
    try:
        response = requests.request(method, f"{cfg['url']}/wp-json/wc/v3{path}", params=params, json=body,
                                    auth=(cfg['key'], cfg['secret']), timeout=timeout, allow_redirects=False,
                                    headers={'User-Agent': 'POPCORE stock sync'})
    except requests.RequestException as exc:
        raise WebsiteError('The website cannot be reached.', 'website_unavailable') from exc
    try:
        data = response.json()
    except ValueError:
        data = None
    if response.status_code in (401, 403):
        # WooCommerce ignores a correct key when WordPress believes the request came over plain
        # HTTP (Cloudflare Flexible SSL); see docs/website-sync.md.
        raise WebsiteError('The website refused POPCORE’s key. Check the key, and that WordPress sees HTTPS '
                           '(Site Health → “Your site is using HTTPS”).', 'website_auth', response.status_code)
    if not 200 <= response.status_code < 300:
        message = data.get('message') if isinstance(data, dict) else None
        raise WebsiteError(str(message or f'The website answered HTTP {response.status_code}.')[:300],
                           'website_error', response.status_code)
    return data, response.headers


def _all(cfg, path, params):
    rows, page = [], 1
    while True:
        data, headers = call(cfg, 'GET', path, params={**params, 'per_page': BATCH, 'page': page})
        rows += data if isinstance(data, list) else []
        if page >= int(headers.get('X-WP-TotalPages') or 1):
            return rows
        page += 1


def connect():
    """A connection for background work, configured like get_db()."""
    con = sqlite3.connect(db.DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    con.execute('PRAGMA busy_timeout = 10000')
    return con


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(moment):
    return moment.strftime('%Y-%m-%dT%H:%M:%S')


def state(con, key, default=None):
    row = con.execute('SELECT value FROM website_state WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def set_state(con, key, value):
    if value is None:
        con.execute('DELETE FROM website_state WHERE key=?', (key,))
    else:
        con.execute('INSERT INTO website_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (key, str(value)))


def enabled(con):
    return state(con, 'enabled') == '1'


# ── The website's catalogue, linked by SKU ──────────────────────────────────

def _pickup(meta_data):
    for meta in meta_data or ():
        if meta.get('key') == PICKUP_META:
            value = meta.get('value')
            if isinstance(value, dict):
                value = list(value.values())
            return sorted(str(code) for code in value) if isinstance(value, list) else []
    return []


def _product_for_sku(con, sku):
    if not sku:
        return None
    row = con.execute('SELECT id FROM products WHERE sku=?', (sku,)).fetchone() or con.execute(
        'SELECT id FROM products WHERE UPPER(sku)=UPPER(?)', (sku,)).fetchone()
    return row[0] if row else None


def refresh_items(con, cfg):
    """Reread every website product and variation and link each to POPCORE by SKU."""
    products = _all(cfg, '/products', {'status': 'any'})
    items = []
    for product in products:
        kind = product.get('type') if product.get('type') in ('simple', 'variable') else 'other'
        items.append({'wc_id': product['id'], 'parent_id': None, 'kind': kind, 'sku': (product.get('sku') or '').strip(),
                      'name': product.get('name') or '', 'status': product.get('status') or '',
                      'manage_stock': 1 if product.get('manage_stock') is True else 0,
                      'stock_quantity': product.get('stock_quantity'), 'stock_status': product.get('stock_status'),
                      'pickup': json.dumps(_pickup(product.get('meta_data')))})
        if kind != 'variable':
            continue
        for variation in _all(cfg, f"/products/{product['id']}/variations", {}):
            label = ' / '.join(str(attribute.get('option') or '') for attribute in variation.get('attributes') or ())
            items.append({'wc_id': variation['id'], 'parent_id': product['id'], 'kind': 'variation',
                          'sku': (variation.get('sku') or '').strip(),
                          'name': f"{product.get('name') or ''} — {label}".strip(' —'),
                          'status': variation.get('status') or '',
                          'manage_stock': 1 if variation.get('manage_stock') is True else 0,
                          'stock_quantity': variation.get('stock_quantity'),
                          'stock_status': variation.get('stock_status'), 'pickup': None})
    con.execute('BEGIN IMMEDIATE')
    try:
        con.execute('DELETE FROM website_items')
        for item in items:
            con.execute("""INSERT INTO website_items(wc_id, parent_id, kind, sku, name, status, manage_stock,
                               stock_quantity, stock_status, pickup, product_id)
                           VALUES (:wc_id, :parent_id, :kind, :sku, :name, :status, :manage_stock,
                               :stock_quantity, :stock_status, :pickup, :product_id)""",
                        {**item, 'product_id': _product_for_sku(con, item['sku']) if item['kind'] != 'variable' else None})
        set_state(con, 'refreshed_at', _iso(_now()))
        set_state(con, 'refresh_requested', None)
        con.commit()
    except Exception:
        con.rollback()
        raise
    return len(items)


# ── What the website should show ────────────────────────────────────────────

def _shelf_stock(con):
    """{product_id: {store_id: units a web order may take}} over the live stores' shelves."""
    live = live_store_ids(con)
    if not live:
        return {}
    marks = ','.join('?' * len(live))
    kept = {(row[0], row[1]): row[2] for row in con.execute(
        """SELECT random_product_id, location_id, SUM(remaining_qty) FROM inventory_open_sets
           WHERE remaining_qty>0 GROUP BY random_product_id, location_id""")}
    stock = {}
    for row in con.execute(
            f"""SELECT b.product_id, b.location_id, l.store_id, b.quantity, p.stock_form
                FROM inventory_balances b JOIN inventory_locations l ON l.id=b.location_id
                JOIN products p ON p.id=b.product_id
                WHERE b.disposition='saleable' AND b.quantity>0 AND l.is_active=1
                  AND l.code IN ({','.join('?' * len(SHELF_LOCATIONS))}) AND l.store_id IN ({marks})""",
            (*SHELF_LOCATIONS, *live)):
        units = row['quantity'] - (kept.get((row['product_id'], row['location_id']), 0)
                                   if row['stock_form'] == 'random_box' else 0)
        if units > 0:
            by_store = stock.setdefault(row['product_id'], {})
            by_store[row['store_id']] = by_store.get(row['store_id'], 0) + units
    return stock


def _held(con):
    """Units of paid web orders POPCORE has not recorded yet: the website already took them off."""
    held = {}
    for row in con.execute("SELECT lines FROM website_orders WHERE state='needs_review'"):
        for line in json.loads(row[0] or '[]'):
            if line.get('product_id'):
                held[line['product_id']] = held.get(line['product_id'], 0) + int(line.get('quantity') or 0)
    return held


def _report_state(con, cfg, store_id, code):
    """How far a live store's sales reached POPCORE: its last reported business day (or the day
    before its opening count, when no report came since) and the shop days after it not reported,
    counting today once the shop has opened."""
    row = con.execute("""SELECT MAX(date) FROM (SELECT date FROM daily_sales WHERE store=?
                         UNION SELECT date FROM daily_report_metadata WHERE store=?)""", (code, code)).fetchone()
    since = live_since(con, store_id)
    known = [day for day in (row[0], since and (date.fromisoformat(since) - timedelta(days=1)).isoformat()) if day]
    if not known:
        return {'store': code, 'reported_through': None, 'days_behind': 0}
    local = _now().astimezone(TORONTO)
    days = (local.date() - date.fromisoformat(max(known))).days - (1 if local.time() < cfg['opens'] else 0)
    return {'store': code, 'reported_through': max(known), 'days_behind': max(0, days)}


def _best_days(con, code):
    """{product_id: the most units the store's report took off it in one day} over its last
    REPORT_DAYS report days. A sealed set sold counts as its boxes, as the report takes them."""
    days = [row[0] for row in con.execute(
        'SELECT DISTINCT date FROM daily_sales WHERE store=? ORDER BY date DESC LIMIT ?', (code, REPORT_DAYS))]
    if not days:
        return {}
    by_day = {}
    for row in con.execute(
            f"""SELECT p.*, s.date AS sold_on, SUM(s.qty_sold) AS sold FROM daily_sales s
                JOIN products p ON p.id=s.product_id WHERE s.store=? AND s.date IN ({','.join('?' * len(days))})
                GROUP BY p.id, s.date HAVING SUM(s.qty_sold) > 0""", (code, *days)):
        product, units = _stock_product(con, row)
        if units:
            key = (product['id'], row['sold_on'])
            by_day[key] = by_day.get(key, 0) + row['sold'] * units
    best = {}
    for (product_id, _), units in by_day.items():
        best[product_id] = max(best.get(product_id, 0), units)
    return best


def _shop_hold(con, cfg):
    """{store_id: {product_id: units}}: what each live store may have sold since its last report."""
    codes = {row[0]: row[1] for row in con.execute('SELECT id, code FROM stores')}
    hold = {}
    for store_id in live_store_ids(con):
        days = _report_state(con, cfg, store_id, codes[store_id])['days_behind']
        if days:
            hold[store_id] = {product_id: units * days for product_id, units in _best_days(con, codes[store_id]).items()}
    return hold


def plan(con, cfg):
    """Every linked item's website number and every product's pickup stores, with what changes."""
    stock, held, hold = _shelf_stock(con), _held(con), _shop_hold(con, cfg)
    codes = {row[0]: row[1].lower() for row in con.execute('SELECT id, code FROM stores')}
    items = [dict(row) for row in con.execute(
        "SELECT * FROM website_items WHERE kind IN ('simple', 'variation') ORDER BY wc_id")
        if row['product_id'] is not None or POPCORE_ID.fullmatch(row['sku'].upper())]
    pickup_by_parent = {}
    for item in items:
        by_store = stock.get(item['product_id'], {}) if item['product_id'] else {}
        shown = {store_id: max(0, units - hold.get(store_id, {}).get(item['product_id'], 0))
                 for store_id, units in by_store.items()}
        item['shop_hold'] = sum(by_store.values()) - sum(shown.values())
        item['quantity'] = max(0, sum(shown.values()) - held.get(item['product_id'], 0) - cfg['keep_back'])
        item['stock_change'] = not item['manage_stock'] or item['stock_quantity'] != item['quantity']
        stores = pickup_by_parent.setdefault(item['parent_id'] or item['wc_id'], set())
        if item['quantity'] > 0:
            stores.update(codes[store_id] for store_id, units in shown.items() if units > cfg['keep_back'])
    parents = []
    for row in con.execute("SELECT wc_id, parent_id, kind, name, sku, pickup FROM website_items WHERE kind IN ('simple', 'variable')"):
        if row['wc_id'] not in pickup_by_parent:
            continue
        current, wanted = json.loads(row['pickup'] or '[]'), sorted(pickup_by_parent[row['wc_id']])
        parents.append({'wc_id': row['wc_id'], 'kind': row['kind'], 'name': row['name'], 'sku': row['sku'],
                        'pickup': current, 'wanted': wanted, 'pickup_change': current != wanted})
    return items, parents


def _available(item):
    """Whether the website sells the item now: in stock, with stock left when it counts stock."""
    return item['stock_status'] == 'instock' and (not item['manage_stock'] or (item['stock_quantity'] or 0) > 0)


def preview(con, cfg):
    items, parents = plan(con, cfg)
    changes = [item for item in items if item['stock_change']]
    unknown_ids = [item for item in items if item['product_id'] is None]
    other = [dict(row) for row in con.execute(
        """SELECT wc_id, parent_id, sku, name FROM website_items
           WHERE kind IN ('simple', 'variation') AND product_id IS NULL ORDER BY sku""")
        if not POPCORE_ID.fullmatch(row['sku'].upper())]
    return {
        'items': len(items) - len(unknown_ids), 'unknown_id': len(unknown_ids),
        'no_sku': sum(1 for row in other if not row['sku']), 'other_sku': sum(1 for row in other if row['sku']),
        'stock_changes': len(changes),
        # A stock write from 0 to more emails the item's restock and Dex followers; under 3 emails
        # the website's low-stock recipient, unless those emails are off (docs/website-sync.md).
        'back_in_stock': sum(1 for item in changes if item['quantity'] > 0 and not _available(item)),
        'sold_out': sum(1 for item in changes if item['quantity'] == 0 and _available(item)),
        'low_stock': sum(1 for item in changes if 0 < item['quantity'] <= 2),
        'pickup_changes': sum(1 for parent in parents if parent['pickup_change']),
        'shop_hold': {'items': sum(1 for item in items if item['shop_hold']),
                      'units': sum(item['shop_hold'] for item in items)},
        'changes': [{key: item[key] for key in ('wc_id', 'parent_id', 'sku', 'name', 'product_id', 'manage_stock',
                                                'stock_quantity', 'stock_status', 'quantity')} for item in changes[:500]],
        'pickup': [parent for parent in parents if parent['pickup_change']][:500],
        'unknown_ids': [{key: item[key] for key in ('wc_id', 'parent_id', 'sku', 'name')}
                        for item in sorted(unknown_ids, key=lambda item: item['sku'])][:500],
        'other_skus': [row for row in other if row['sku']][:500],
    }


def push(con, cfg):
    """Write the numbers and pickup stores that changed. Returns how many writes the website took."""
    items, parents = plan(con, cfg)
    updates = {}  # endpoint -> [{id, ...}]
    for item in items:
        if item['stock_change']:
            path = f"/products/{item['parent_id']}/variations/batch" if item['parent_id'] else '/products/batch'
            updates.setdefault(path, {})[item['wc_id']] = {'id': item['wc_id'], 'manage_stock': True,
                                                           'stock_quantity': item['quantity']}
    for parent in parents:
        if parent['pickup_change']:
            entry = updates.setdefault('/products/batch', {}).setdefault(parent['wc_id'], {'id': parent['wc_id']})
            entry['meta_data'] = [{'key': PICKUP_META, 'value': parent['wanted']}]
    wanted = {item['wc_id']: item['quantity'] for item in items}
    pickups = {parent['wc_id']: parent['wanted'] for parent in parents}
    written = 0
    for path, by_id in updates.items():
        rows = list(by_id.values())
        for start in range(0, len(rows), BATCH):
            data, _ = call(cfg, 'POST', path, body={'update': rows[start:start + BATCH]})
            for result in (data or {}).get('update') or ():
                wc_id = result.get('id')
                error = result.get('error')
                if error or wc_id not in by_id:
                    con.execute('UPDATE website_items SET error=? WHERE wc_id=?',
                                (str((error or {}).get('message') or 'Not updated')[:300], wc_id))
                    continue
                sent = by_id[wc_id]
                if 'stock_quantity' in sent:
                    con.execute("""UPDATE website_items SET manage_stock=1, stock_quantity=?, stock_status=?,
                                       error=NULL, pushed_at=datetime('now') WHERE wc_id=?""",
                                (wanted[wc_id], 'instock' if wanted[wc_id] > 0 else 'outofstock', wc_id))
                if 'meta_data' in sent:
                    con.execute("UPDATE website_items SET pickup=?, error=NULL, pushed_at=datetime('now') WHERE wc_id=?",
                                (json.dumps(pickups[wc_id]), wc_id))
                written += 1
            con.commit()
    if written:
        set_state(con, 'last_push_at', _iso(_now()))
        set_state(con, 'last_push_count', written)
        con.commit()
    return written


# ── Paid web orders ─────────────────────────────────────────────────────────

def _cents(value):
    try:
        return int(round(float(value or 0) * 100))
    except (TypeError, ValueError):
        return None


def _meta(order, key):
    for meta in order.get('meta_data') or ():
        if meta.get('key') == key:
            return meta.get('value')
    return None


def _gmt(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc) if value else None


def _line_product(con, sku, wc_id):
    """The POPCORE product of an order line: by its SKU, else by the website item's current link."""
    product_id = _product_for_sku(con, sku)
    if product_id is None and wc_id:
        row = con.execute('SELECT product_id FROM website_items WHERE wc_id=? AND product_id IS NOT NULL',
                          (wc_id,)).fetchone()
        product_id = row[0] if row else None
    return product_id


def _order_facts(con, order):
    """What POPCORE keeps of an order: no names, emails, phones or addresses."""
    lines = []
    for line in order.get('line_items') or ():
        quantity = int(line.get('quantity') or 0)
        if quantity <= 0:
            continue
        sku = (line.get('sku') or '').strip()
        lines.append({'sku': sku, 'name': str(line.get('name') or '')[:200], 'quantity': quantity,
                      'total_cents': _cents(line.get('total')),
                      'product_id': _line_product(con, sku, line.get('variation_id') or line.get('product_id')),
                      'wc_product_id': line.get('product_id'), 'wc_variation_id': line.get('variation_id') or None})
    pickup = str(_meta(order, '_popcore_pickup_store_id') or '').strip().upper() or None
    fulfilment = str(_meta(order, '_pcc_fulfillment_type') or ('pickup' if pickup else 'delivery'))[:40]
    return {'wc_order_id': order['id'], 'number': str(order.get('number') or order['id'])[:40],
            'wc_status': str(order.get('status') or '')[:40], 'paid_at': order.get('date_paid_gmt'),
            'modified_at': order.get('date_modified_gmt'), 'total_cents': _cents(order.get('total')),
            'tax_cents': _cents(order.get('total_tax')),
            'payment': str(order.get('payment_method_title') or order.get('payment_method') or '')[:80],
            'transaction_id': str(order.get('transaction_id') or '')[:120] or None,
            'fulfilment': fulfilment, 'pickup_store': pickup, 'lines': json.dumps(lines)}


def _filling_store(con, cfg, order_row):
    live = live_store_ids(con)
    codes = {row[1]: row[0] for row in con.execute('SELECT id, code FROM stores')}
    if order_row['pickup_store'] and codes.get(order_row['pickup_store']) in live:
        return codes[order_row['pickup_store']]
    store_id = codes.get(cfg['delivery_store'])
    return store_id if store_id in live else None


def _take(con, store_id, lines):
    """Consume lines picked back stock first, or (None, problem) when the store cannot fill them."""
    locations = {row[0]: row[1] for row in con.execute(
        'SELECT code, id FROM inventory_locations WHERE store_id=? AND is_active=1', (store_id,))}
    wanted = {}
    for line in lines:
        wanted[line['product_id']] = wanted.get(line['product_id'], 0) + line['quantity']
    take = []
    for product_id, quantity in wanted.items():
        product = con.execute('SELECT stock_form, stock_unit FROM products WHERE id=?', (product_id,)).fetchone()
        left = quantity
        for code in PICK_ORDER:
            if left == 0 or code not in locations:
                continue
            location_id = locations[code]
            balance = _balance(con, product_id, location_id, 'saleable')
            available = balance['quantity']
            if product['stock_form'] == 'random_box':
                available -= con.execute(
                    """SELECT COALESCE(SUM(remaining_qty), 0) FROM inventory_open_sets
                       WHERE random_product_id=? AND location_id=? AND remaining_qty>0""",
                    (product_id, location_id)).fetchone()[0]
            units = min(left, max(0, available))
            if units:
                take.append({'product_id': product_id, 'unit': product['stock_unit'] or 'piece', 'quantity': units,
                             'from_location_id': location_id, 'from_disposition': 'saleable',
                             'expected_versions': {'from': balance['version']}})
                left -= units
        if left:
            return None, 'not_enough_stock'
    return take, None


def _grant(con):
    """The website may take stock at each live store. A store is granted once, when it is first seen
    live; an admin can revoke it under Users and it is never granted again behind their back."""
    granted = set(json.loads(state(con, 'granted_stores', '[]')))
    for store_id in sorted(live_store_ids(con) - granted):
        con.execute('INSERT OR IGNORE INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)', (WEBSITE_SUB, store_id))
        granted.add(store_id)
    set_state(con, 'granted_stores', json.dumps(sorted(granted)))


def record(con, cfg, wc_order_id):
    """Take a paid web order's items out of stock, or leave it for a manager with the reason.
    Runs in its own write transaction."""
    con.execute('BEGIN IMMEDIATE')
    try:
        row = con.execute('SELECT * FROM website_orders WHERE wc_order_id=?', (wc_order_id,)).fetchone()
        if row is None or row['state'] not in ('new', 'needs_review'):
            con.rollback()
            return row['state'] if row else None
        lines = json.loads(row['lines'] or '[]')
        # A link fixed since (a SKU set on the website, a sealed set added in POPCORE) counts on a retry.
        for line in lines:
            if line['product_id'] is None:
                line['product_id'] = _line_product(con, line['sku'], line.get('wc_variation_id') or line.get('wc_product_id'))
        con.execute('UPDATE website_orders SET lines=? WHERE wc_order_id=?', (json.dumps(lines), wc_order_id))
        problem = None
        store_id = _filling_store(con, cfg, row)
        if not lines:
            problem = 'no_items'
        elif any(line['product_id'] is None for line in lines):
            problem = 'unknown_item'
        elif store_id is None:
            problem = 'no_live_store'
        take = None
        if problem is None:
            take, problem = _take(con, store_id, lines)
        document_id = None
        if problem is None:
            paid = _gmt(row['paid_at']) or _now()
            # A post that fails part way must leave nothing behind; the order row is still updated.
            con.execute('SAVEPOINT website_take')
            try:
                document_id = _post_inventory_in_transaction(con, {
                    'kind': 'consume', 'business_date': paid.astimezone(TORONTO).date().isoformat(),
                    'source_type': SOURCE_ORDER, 'source_id': str(wc_order_id),
                    'reason': f"{ORDER_REASON} #{row['number']}", 'lines': take,
                }, actor=website_actor(), request_key=f'website:order:{wc_order_id}',
                    access_store_ids=(store_id,))['document_id']
            except (InventoryError, PermissionError) as exc:
                con.execute('ROLLBACK TO website_take')
                problem = exc.code if isinstance(exc, InventoryError) else 'no_store_access'
            con.execute('RELEASE website_take')
        if problem:
            con.execute("""UPDATE website_orders SET state='needs_review', problem=?, store_id=?, updated_at=datetime('now')
                           WHERE wc_order_id=?""", (problem, store_id, wc_order_id))
        else:
            con.execute("""UPDATE website_orders SET state='recorded', problem=NULL, store_id=?, inventory_document_id=?,
                               updated_at=datetime('now') WHERE wc_order_id=?""", (store_id, document_id, wc_order_id))
        con.commit()
        return 'needs_review' if problem else 'recorded'
    except Exception:
        con.rollback()
        raise


def _reverse(con, wc_order_id):
    """A cancelled order's items go back where they came from (WooCommerce restores its own number)."""
    con.execute('BEGIN IMMEDIATE')
    try:
        row = con.execute('SELECT * FROM website_orders WHERE wc_order_id=?', (wc_order_id,)).fetchone()
        if row is None or row['state'] not in ('recorded', 'cancel_failed'):
            con.rollback()
            return
        lines = []
        for line in con.execute('SELECT * FROM inventory_document_lines WHERE document_id=? ORDER BY line_no',
                                (row['inventory_document_id'],)):
            balance = _balance(con, line['product_id'], line['from_location_id'], line['from_disposition'])
            lines.append({'product_id': line['product_id'], 'unit': line['native_unit'], 'quantity': line['quantity'],
                          'to_location_id': line['from_location_id'], 'to_disposition': line['from_disposition'],
                          'expected_versions': {'to': balance['version']}})
        document_id = _post_inventory_in_transaction(con, {
            'kind': 'correction', 'business_date': datetime.now(TORONTO).date().isoformat(),
            'source_type': SOURCE_CANCEL, 'source_id': str(wc_order_id), 'correction_of': row['inventory_document_id'],
            'reason': f"{CANCEL_REASON} #{row['number']}", 'lines': lines,
        }, actor=website_actor(), request_key=f'website:cancel:{wc_order_id}', access_store_ids=(row['store_id'],),
            minimum_role='manager')['document_id']
        con.execute("""UPDATE website_orders SET state='cancelled', problem=NULL, reversal_document_id=?,
                           updated_at=datetime('now') WHERE wc_order_id=?""", (document_id, wc_order_id))
        con.commit()
    except (InventoryError, PermissionError) as exc:
        # The website put its number back; POPCORE could not, so a manager retries or settles it.
        con.rollback()
        con.execute("""UPDATE website_orders SET state='cancel_failed', problem=?, updated_at=datetime('now')
                       WHERE wc_order_id=?""", (exc.code if isinstance(exc, InventoryError) else 'no_store_access',
                                                 wc_order_id))
        con.commit()
    except Exception:
        con.rollback()
        raise


def apply_order(con, cfg, order):
    """Bring POPCORE in step with one order from the website."""
    facts = _order_facts(con, order)
    row = con.execute('SELECT * FROM website_orders WHERE wc_order_id=?', (facts['wc_order_id'],)).fetchone()
    status = facts['wc_status']
    if row is not None and row['modified_at'] == facts['modified_at'] and row['state'] != 'new':
        return row['state']  # read again in the overlap, unchanged
    if row is None:
        since = state(con, 'since')
        if not facts['paid_at'] or status in NOT_TAKEN or status == 'cancelled':
            return None
        if since and facts['paid_at'] < since:
            return None  # paid before the sync was turned on: the opening count already holds it
        con.execute("""INSERT INTO website_orders(wc_order_id, number, wc_status, paid_at, modified_at, total_cents,
                           tax_cents, payment, transaction_id, fulfilment, pickup_store, lines, state)
                       VALUES (:wc_order_id, :number, :wc_status, :paid_at, :modified_at, :total_cents, :tax_cents,
                           :payment, :transaction_id, :fulfilment, :pickup_store, :lines, 'new')""", facts)
        con.commit()
        return record(con, cfg, facts['wc_order_id'])
    con.execute("""UPDATE website_orders SET wc_status=?, modified_at=?, total_cents=?, tax_cents=?,
                       refunded=CASE WHEN ?='refunded' THEN 1 ELSE refunded END, updated_at=datetime('now')
                   WHERE wc_order_id=?""",
                (status, facts['modified_at'], facts['total_cents'], facts['tax_cents'], status, facts['wc_order_id']))
    if row['state'] == 'needs_review':
        # Lines are reread so a fixed link or SKU on the website lets the next try fill it.
        con.execute('UPDATE website_orders SET lines=?, pickup_store=? WHERE wc_order_id=?',
                    (facts['lines'], facts['pickup_store'], facts['wc_order_id']))
    con.commit()
    if status == 'cancelled':
        if row['state'] in ('recorded', 'cancel_failed'):
            _reverse(con, facts['wc_order_id'])
        elif row['state'] in ('new', 'needs_review'):
            con.execute("UPDATE website_orders SET state='cancelled', updated_at=datetime('now') WHERE wc_order_id=?",
                        (facts['wc_order_id'],))
            con.commit()
    elif row['state'] == 'cancelled' and status not in NOT_TAKEN:
        # Paid again after a cancel: a manager decides, since the website took its stock again.
        con.execute("""UPDATE website_orders SET state='needs_review', problem='reopened', updated_at=datetime('now')
                       WHERE wc_order_id=?""", (facts['wc_order_id'],))
        con.commit()
    return state_of(con, facts['wc_order_id'])


def state_of(con, wc_order_id):
    row = con.execute('SELECT state FROM website_orders WHERE wc_order_id=?', (wc_order_id,)).fetchone()
    return row[0] if row else None


def poll_orders(con, cfg):
    """Read orders changed since the last read; a little overlap makes sure none is missed."""
    after = _gmt(state(con, 'orders_after')) or _now()
    started = _now()
    orders = _all(cfg, '/orders', {'modified_after': _iso(after - timedelta(minutes=2)), 'dates_are_gmt': 'true',
                                   'orderby': 'modified', 'order': 'asc', 'status': 'any'})
    for order in orders:
        apply_order(con, cfg, order)
    newest = max((_gmt(order.get('date_modified_gmt')) for order in orders if order.get('date_modified_gmt')),
                 default=after)
    # The newest change seen (the website's clock), and never more than a few minutes behind ours,
    # so a quiet shop does not reread the same orders forever.
    set_state(con, 'orders_after', _iso(max(after, newest, started - timedelta(minutes=5))))
    con.commit()
    return len(orders)


# ── Turning it on, and the background job ───────────────────────────────────

def switch(con, on, actor_sub):
    if on:
        if state(con, 'refreshed_at') is None:
            raise WebsiteError('Read the website’s products first.', 'not_refreshed')
        if not enabled(con):
            moment = _iso(_now())
            set_state(con, 'enabled', '1')
            set_state(con, 'since', state(con, 'since') or moment)
            set_state(con, 'orders_after', state(con, 'orders_after') or moment)
            _grant(con)
    else:
        set_state(con, 'enabled', None)
    set_state(con, 'switched_by', actor_sub)
    set_state(con, 'switched_at', _iso(_now()))


def run_once(con, cfg):
    """One pass: reread the catalogue when asked or due, then (when on) orders first, then stock."""
    refreshed = _gmt(state(con, 'refreshed_at'))
    if state(con, 'refresh_requested') or (enabled(con) and (refreshed is None or _now() - refreshed > REFRESH_EVERY)):
        refresh_items(con, cfg)
    if not enabled(con):
        return
    _grant(con)
    con.commit()
    poll_orders(con, cfg)
    for row in con.execute("SELECT wc_order_id FROM website_orders WHERE state='new'").fetchall():
        record(con, cfg, row[0])
    push(con, cfg)


def background_sync():
    """Scheduler job, every 30 seconds."""
    try:
        cfg = config()
    except ValueError:
        return
    if cfg is None:
        return
    con = connect()
    try:
        try:
            run_once(con, cfg)
            set_state(con, 'last_error', None)
        except WebsiteError as exc:
            if con.in_transaction:
                con.rollback()
            set_state(con, 'last_error', json.dumps({'code': exc.code, 'message': str(exc), 'at': _iso(_now())}))
        set_state(con, 'last_run', _iso(_now()))
        con.commit()
    finally:
        con.close()


def status(con):
    try:
        cfg = config()
        problem = None
    except ValueError as exc:
        cfg, problem = None, str(exc)
    error = state(con, 'last_error')
    counts = {row[0]: row[1] for row in con.execute('SELECT state, COUNT(*) FROM website_orders GROUP BY state')}
    codes = {row[0]: row[1] for row in con.execute('SELECT id, code FROM stores')}
    live = live_store_ids(con)
    return {
        'configured': cfg is not None, 'settings_problem': problem,
        'url': cfg['url'] if cfg else None, 'keep_back': cfg['keep_back'] if cfg else None,
        'delivery_store': cfg['delivery_store'] if cfg else None,
        'shop_opens': cfg['opens'].strftime('%H:%M') if cfg else None,
        'report': [_report_state(con, cfg, store_id, codes[store_id])
                   for store_id in sorted(live, key=codes.get)] if cfg else [],
        'enabled': enabled(con), 'since': state(con, 'since'), 'switched_at': state(con, 'switched_at'),
        'refreshed_at': state(con, 'refreshed_at'), 'refresh_requested': bool(state(con, 'refresh_requested')),
        'last_run': state(con, 'last_run'), 'last_push_at': state(con, 'last_push_at'),
        'last_push_count': int(state(con, 'last_push_count', 0) or 0),
        'last_error': json.loads(error) if error else None,
        'live_stores': sorted(codes[store_id] for store_id in live),
        'push_errors': con.execute('SELECT COUNT(*) FROM website_items WHERE error IS NOT NULL').fetchone()[0],
        'orders': {'recorded': counts.get('recorded', 0),
                   'needs_review': counts.get('needs_review', 0) + counts.get('cancel_failed', 0),
                   'cancelled': counts.get('cancelled', 0), 'dismissed': counts.get('dismissed', 0)},
    }


def orders(con, limit=100):
    codes = {row[0]: row[1] for row in con.execute('SELECT id, code FROM stores')}
    rows = []
    for row in con.execute(
            """SELECT * FROM website_orders ORDER BY state NOT IN ('needs_review', 'cancel_failed'), paid_at DESC
               LIMIT ?""", (limit,)):
        item = dict(row)
        item['lines'] = json.loads(item['lines'] or '[]')
        item['store_code'] = codes.get(item['store_id'])
        rows.append(item)
    return rows


def retry_order(con, cfg, wc_order_id):
    if state_of(con, wc_order_id) == 'cancel_failed':
        _reverse(con, wc_order_id)
        return state_of(con, wc_order_id)
    return record(con, cfg, wc_order_id)


def dismiss_order(con, wc_order_id, actor_sub, reason):
    """A manager settles an order POPCORE could not fill (stock recorded by hand, or not stocked)."""
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('A reason is required')
    changed = con.execute("""UPDATE website_orders SET state='dismissed', dismissed_by=?, dismiss_reason=?,
                                 updated_at=datetime('now')
                             WHERE wc_order_id=? AND state IN ('needs_review', 'cancel_failed')""",
                          (actor_sub, reason[:300], wc_order_id)).rowcount
    if not changed:
        raise ValueError('Only an order waiting for review can be settled')
