"""POPCORE owns the website's stock (owner, 2026-10-25 and 2026-11-01) and the regular prices of its
boxes and items (owner, 2026-11-10).

popcore.ca (WooCommerce) takes its stock from POPCORE. Every website product and variation is
linked by its POPCORE ID, the website's own field `_popcore_id` (owner, 2026-11-05 and 2026-11-07),
to the POPCORE product with that ID: the Single Box variation or a simple product carries the
product's ID, the Whole Set variation `<ID>-SET` (the sealed set) and a design `<ID>-NN` (the
confirmed design). A variable parent never holds one. The website's SKU holds barcodes: POPCORE
never reads it as the ID and never writes it. An item whose POPCORE ID POPCORE does not have, or
whose ID is on more than one item, shows sold out. Items without a POPCORE ID are left alone.

- Stock: the website number is the saleable stock of the live stores' shelves (floor, upstairs,
  warehouse; never display pieces or the claw machine), less boxes kept in opened sets, less what
  the shop may have sold since its last daily report (below), less the units of paid web orders
  POPCORE could not record yet, less WEBSITE_KEEP_BACK (0 by default). A store that is not live
  yet adds nothing, and is not offered for pickup.
- Whole Sets (owner, 2026-11-10): a Whole Set counts the store's sealed sets (整端) and, since the
  floor keeps blind boxes only as single boxes, the whole sets its floor boxes are sure to have: with
  N boxes per set, b floor boxes (after boxes kept in opened sets and the shop hold) hold at least
  ceil(b / N) - 2 whole sets, never below 0 (N = 12: 25 boxes mean at least 1, 37 at least 2). That
  minimum is what counts, never an estimate of the floor's sets. Store by store; with N unknown the
  floor adds none. A web Whole Set order is still filled from sealed sets only: with
  none, it waits for a manager.
- The shop's sales reach POPCORE only with the pasted daily report until Clover is connected. Until
  a shop day's report is in, each product holds back, at that store, what the store sold of it on
  its best day of the last 30 reports, once for every shop day not reported; a shop day starts at
  WEBSITE_SHOP_OPENS (owner, 2026-11-02). Once the report is in, nothing is held until the shop opens.
- Pickup: a product's `_popcore_pickup_stores` lists the live stores that have it.
- Orders: a paid web order takes its items out of the filling store's stock (the pickup store, or
  WEBSITE_DELIVERY_STORE for delivery), back stock first and the floor last, as a separate
  Website order outside the store's sales and closing (owner, 2026-11-01). A cancelled order puts
  them back. An order POPCORE cannot fill waits for a manager and holds its units off the website.
- Prices (owner, 2026-11-10: "it should be POPCORE automatically sending price to the website too";
  until then prices stayed on the website, owner 2026-11-01). A linked Single Box or simple product
  gets its POPCORE product's price, which is its Clover price; a Whole Set gets boxes per set × that
  box's price, since Clover stores no set price (owner, 2026-11-10). POPCORE writes the regular price
  only, never the sale price. A confirmed design gets only a price a manager set for it in POPCORE
  (owner, 2026-11-12: design prices are set in bulk in POPCORE; design_prices.py), never the box price it
  was created with; a design with no such price, and a website Vault product that is not a POPCORE design,
  keep the website's price. An item whose POPCORE record is not a verified Clover item, or has no price,
  keeps its price.
- A price change waits for a manager's OK on the Website page when it is a first price, when it is
  over 30%, when the box or item has no Clover item name, when the website's Whole Set does not cost
  POPCORE's boxes per set times its Single Box, or when the item's sale price would end or start
  discounting. A design's change waits only when it is over 30% from its own website price (the one
  customers see, or the one POPCORE wrote) or moves a sale: a design with no price of its own there, such
  as a draft at its box's price, gets its first price without an OK (owner, 2026-11-12). The other
  options of the same product wait with it, and an OK covers only the change it was given for. A Daily
  Shop product (`_popcore_daily_pool`) is repriced only in the hour after the daily reset
  (WEBSITE_DAILY_RESET, 20:00), so a deal in a customer's bag keeps its price that day.
  Just before a price is written, its items are read again, so these checks see the website as it is.

Nothing is written to the website until an admin turns the sync on. The background job runs every
30 seconds: it reads new and changed orders first, then pushes only what changed since POPCORE
last set it. A full reread of the website's catalogue every day catches edits made there.
"""
import json
import math
import os
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

import db
import design_prices
from auth import ROLE_CLAIM
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
ID_META = '_popcore_id'
DAILY_META = '_popcore_daily_pool'
# Marks of a website Vault product (popcore.ca's vault.md): its price is the owner's, never POPCORE's.
VAULT_META, KIND_META = '_popcore_is_vault', '_popcore_kind'
# POPCORE prices these from their Clover price. A confirmed design is priced only from a price a manager
# set for it (design_prices.py): the price it was created with only repeats its box's.
PRICED_FORMS = ('random_box', 'ordinary', 'sealed_set')
# A price change of more than this percentage waits for a manager's OK.
PRICE_LOOK_PCT = 30
# A Daily Shop product is repriced only this long after the daily reset.
DAILY_WINDOW = timedelta(hours=1)
# A paid order is one with date_paid; these statuses never take stock.
NOT_TAKEN = ('pending', 'failed', 'checkout-draft', 'trash')
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
    return {'url': url, 'key': key, 'secret': secret, 'keep_back': int(keep_back), 'delivery_store': delivery,
            # DT opens at noon (popcore.ca's store hours); the Daily Shop resets at 20:00 Toronto time.
            'opens': _clock('WEBSITE_SHOP_OPENS', '12:00'), 'daily_reset': _clock('WEBSITE_DAILY_RESET', '20:00')}


def _clock(name, default):
    """A Toronto time of day from the environment."""
    try:
        return datetime.strptime(os.environ.get(name, default).strip() or default, '%H:%M').time()
    except ValueError:
        raise ValueError(f'{name} must be a time such as {default}') from None


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


# ── The website's catalogue, linked by POPCORE ID ───────────────────────────

def _pickup(meta_data):
    for meta in meta_data or ():
        if meta.get('key') == PICKUP_META:
            value = meta.get('value')
            if isinstance(value, dict):
                value = list(value.values())
            return sorted(str(code) for code in value) if isinstance(value, list) else []
    return []


def _popcore_id(meta_data):
    """The item's POPCORE ID from its REST meta_data, trimmed as the website trims it, or ''."""
    for meta in meta_data or ():
        if meta.get('key') == ID_META:
            value = meta.get('value')
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return str(value).replace('\ufeff', '').replace('\xa0', ' ').strip()
            return ''
    return ''


def _flag(item, key):
    """A yes/1 product field from REST meta_data, as 1 or 0."""
    return 1 if str(_meta(item, key) or '').strip().lower() in ('1', 'yes', 'true') else 0


def _price(value):
    """A WooCommerce price string in cents, or None when it has none."""
    if value in (None, '') or isinstance(value, bool):
        return None
    cents = _cents(value)
    return cents if cents is not None and cents >= 0 else None


def _product_for_id(con, popcore_id):
    """The POPCORE product with this POPCORE ID (POPCORE keeps its IDs in products.sku), any case."""
    if not popcore_id:
        return None
    row = con.execute('SELECT id FROM products WHERE sku=?', (popcore_id,)).fetchone() or con.execute(
        'SELECT id FROM products WHERE UPPER(sku)=UPPER(?)', (popcore_id,)).fetchone()
    return row[0] if row else None


def _known_id(con, wc_id):
    """The POPCORE ID the last catalogue read found on a website item, or ''."""
    row = con.execute("SELECT popcore_id FROM website_items WHERE wc_id=? AND kind != 'variable'",
                      (wc_id,)).fetchone() if wc_id else None
    return row[0] if row else ''


def refresh_items(con, cfg):
    """Reread every website product and variation and link each to POPCORE by its POPCORE ID."""
    products = _all(cfg, '/products', {'status': 'any'})
    items = []
    for product in products:
        kind = product.get('type') if product.get('type') in ('simple', 'variable') else 'other'
        # The Daily Shop pool and the Vault are marked on the product; its options follow it.
        pool, vault = _flag(product, DAILY_META), _vault(product)
        items.append({'wc_id': product['id'], 'parent_id': None, 'kind': kind, 'sku': (product.get('sku') or '').strip(),
                      # A variable parent never holds an ID; its options do.
                      'popcore_id': _popcore_id(product.get('meta_data')) if kind != 'variable' else '',
                      'name': product.get('name') or '', 'status': product.get('status') or '',
                      'manage_stock': 1 if product.get('manage_stock') is True else 0,
                      'stock_quantity': product.get('stock_quantity'), 'stock_status': product.get('stock_status'),
                      'pickup': json.dumps(_pickup(product.get('meta_data'))),
                      'regular_cents': _price(product.get('regular_price')), 'sale_cents': _price(product.get('sale_price')),
                      'daily_pool': pool, 'vault': vault})
        if kind != 'variable':
            continue
        for variation in _all(cfg, f"/products/{product['id']}/variations", {}):
            label = ' / '.join(str(attribute.get('option') or '') for attribute in variation.get('attributes') or ())
            items.append({'wc_id': variation['id'], 'parent_id': product['id'], 'kind': 'variation',
                          'sku': (variation.get('sku') or '').strip(),
                          'popcore_id': _popcore_id(variation.get('meta_data')),
                          'name': f"{product.get('name') or ''} — {label}".strip(' —'),
                          'status': variation.get('status') or '',
                          'manage_stock': 1 if variation.get('manage_stock') is True else 0,
                          'stock_quantity': variation.get('stock_quantity'),
                          'stock_status': variation.get('stock_status'), 'pickup': None,
                          'regular_cents': _price(variation.get('regular_price')),
                          'sale_cents': _price(variation.get('sale_price')), 'daily_pool': pool, 'vault': vault})
    # One ID is on at most one item (owner, 2026-11-05). An ID on two items links neither, since
    # both would show the same stock: they show sold out until the website is fixed.
    holders = Counter(item['popcore_id'].upper() for item in items if item['popcore_id'])
    con.execute('BEGIN IMMEDIATE')
    try:
        con.execute('DELETE FROM website_items')
        for item in items:
            linked = item['popcore_id'] and holders[item['popcore_id'].upper()] == 1
            con.execute("""INSERT INTO website_items(wc_id, parent_id, kind, sku, popcore_id, name, status, manage_stock,
                               stock_quantity, stock_status, pickup, product_id, regular_cents, sale_cents,
                               daily_pool, vault)
                           VALUES (:wc_id, :parent_id, :kind, :sku, :popcore_id, :name, :status, :manage_stock,
                               :stock_quantity, :stock_status, :pickup, :product_id, :regular_cents, :sale_cents,
                               :daily_pool, :vault)""",
                        {**item, 'product_id': _product_for_id(con, item['popcore_id']) if linked else None})
        set_state(con, 'refreshed_at', _iso(_now()))
        set_state(con, 'refresh_requested', None)
        con.commit()
    except Exception:
        con.rollback()
        raise
    return len(items)


# ── What the website should show ────────────────────────────────────────────

def _shelf_stock(con):
    """({product_id: {store_id: units a web order may take}} over the live stores' shelves,
    {blind box product_id: {store_id: its loose boxes on the floor}})."""
    live = live_store_ids(con)
    if not live:
        return {}, {}
    marks = ','.join('?' * len(live))
    kept = {(row[0], row[1]): row[2] for row in con.execute(
        """SELECT random_product_id, location_id, SUM(remaining_qty) FROM inventory_open_sets
           WHERE remaining_qty>0 GROUP BY random_product_id, location_id""")}
    stock, floor = {}, {}
    for row in con.execute(
            f"""SELECT b.product_id, b.location_id, l.store_id, l.code, b.quantity, p.stock_form
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
            if row['code'] == 'floor' and row['stock_form'] == 'random_box':
                on_floor = floor.setdefault(row['product_id'], {})
                on_floor[row['store_id']] = on_floor.get(row['store_id'], 0) + units
    return stock, floor


def floor_sets(boxes, per_set):
    """The whole sets a floor of single boxes is sure to have, at least (owner, 2026-11-10): a minimum,
    not how many there are. ceil(boxes / per_set) - 2, never below 0, and none while the set size is
    unknown. With 12 per set: 24 boxes, none for sure; 25, at least 1; 37, at least 2."""
    if not per_set or per_set < 1 or boxes <= 0:
        return 0
    return max(0, math.ceil(boxes / per_set) - 2)


def _set_rule(con, product_id):
    """(blind box product id, boxes per set or None) for a sealed set, else None. The set size comes
    from the set's conversion, else from its box (`<box>-SET`) and the box's boxes per set."""
    product = con.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone() if product_id else None
    if product is None or product['stock_form'] != 'sealed_set':
        return None
    box, size = _stock_product(con, product)
    if size is not None:
        return box['id'], size
    sku = product['sku'] or ''
    box = con.execute("SELECT id, boxes_per_dan FROM products WHERE UPPER(sku)=UPPER(?) AND stock_form='random_box'",
                      (sku[:-4],)).fetchone() if sku.upper().endswith('-SET') else None
    if box is None:
        return None
    try:
        per_set = int(box['boxes_per_dan'] or 0)
    except (TypeError, ValueError):
        per_set = 0
    return box['id'], per_set if per_set >= 1 else None


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
    (stock, floor), held, hold = _shelf_stock(con), _held(con), _shop_hold(con, cfg)
    codes = {row[0]: row[1].lower() for row in con.execute('SELECT id, code FROM stores')}
    # Every item with a POPCORE ID: one POPCORE does not have (or that is on two items) shows sold out.
    items = [dict(row) for row in con.execute(
        "SELECT * FROM website_items WHERE kind IN ('simple', 'variation') AND popcore_id != '' ORDER BY wc_id")]
    pickup_by_parent = {}
    for item in items:
        by_store = stock.get(item['product_id'], {}) if item['product_id'] else {}
        shown = {store_id: max(0, units - hold.get(store_id, {}).get(item['product_id'], 0))
                 for store_id, units in by_store.items()}
        item['shop_hold'] = sum(by_store.values()) - sum(shown.values())
        item['sealed_sets'] = item['floor_sets'] = item['set_size'] = None
        rule = _set_rule(con, item['product_id'])
        if rule is not None:
            # A Whole Set: its sealed sets, plus the whole sets each store's floor boxes are sure to have.
            box_id, size = rule
            from_floor = {store_id: floor_sets(boxes - hold.get(store_id, {}).get(box_id, 0), size)
                          for store_id, boxes in floor.get(box_id, {}).items()}
            item.update(sealed_sets=sum(shown.values()), floor_sets=sum(from_floor.values()), set_size=size)
            for store_id, sets in from_floor.items():
                shown[store_id] = shown.get(store_id, 0) + sets
        item['quantity'] = max(0, sum(shown.values()) - held.get(item['product_id'], 0) - cfg['keep_back'])
        item['stock_change'] = not item['manage_stock'] or item['stock_quantity'] != item['quantity']
        stores = pickup_by_parent.setdefault(item['parent_id'] or item['wc_id'], set())
        if item['quantity'] > 0:
            stores.update(codes[store_id] for store_id, units in shown.items() if units > cfg['keep_back'])
    parents = []
    for row in con.execute("""SELECT wc_id, parent_id, kind, name, popcore_id, pickup FROM website_items
                              WHERE kind IN ('simple', 'variable')"""):
        if row['wc_id'] not in pickup_by_parent:
            continue
        current, wanted = json.loads(row['pickup'] or '[]'), sorted(pickup_by_parent[row['wc_id']])
        parents.append({'wc_id': row['wc_id'], 'kind': row['kind'], 'name': row['name'], 'popcore_id': row['popcore_id'],
                        'pickup': current, 'wanted': wanted, 'pickup_change': current != wanted})
    _plan_prices(con, cfg, items)
    return items, parents


# ── Prices (owner, 2026-11-10) ──────────────────────────────────────────────

def _wanted_price(item, product, products, set_box, set_prices):
    """(cents, None) for the regular price POPCORE gives a linked item, or (None, why it keeps its own).
    set_prices: {design product id: the price a manager set for it, while POPCORE's price still holds it}."""
    if product['stock_form'] == 'confirmed_design':
        # Never the box price a design was created with: only a price a manager set for the design itself
        # (owner, 2026-11-12). Without one, the website keeps its price.
        cents = set_prices.get(product['id'])
        if cents is None:
            return None, 'design'
        if product['identity_status'] != 'verified' or cents != _stored_cents(product['price']):
            return None, 'not_clover' if product['identity_status'] != 'verified' else 'design'
        return cents, None
    if item['vault']:
        return None, 'design'  # a website Vault product that is not a POPCORE design
    if product['identity_status'] != 'verified' or product['stock_form'] not in PRICED_FORMS:
        return None, 'not_clover'
    if product['stock_form'] == 'sealed_set':
        # Clover never stores a Whole Set's price: it is always the boxes per set times the single box's
        # price (owner, 2026-11-10). The set record's own price is never read.
        box = products.get(set_box.get(product['id']))
        boxes = product['boxes_per_dan'] or (box and box['boxes_per_dan'])
        if not (box and box['identity_status'] == 'verified' and boxes):
            return None, 'no_price'
        cents = _cents(box['price'])
        return (boxes * cents, None) if cents and cents > 0 else (None, 'no_price')
    cents = _cents(product['price'])
    return (cents, None) if cents and cents > 0 else (None, 'no_price')


def _price_reasons(item, product, products, set_box, options, before):
    """Why a price change waits for a manager's OK, [] when it can go. before: {website item id: its price
    before POPCORE's last write}."""
    old, new, sale = item['regular_cents'], item['price'], item['sale_cents']
    if product['stock_form'] == 'confirmed_design':
        return _design_reasons(item)
    reasons = []
    if not old:
        # A first price makes an item the website could not sell before buyable.
        reasons.append({'code': 'first_price'})
    elif abs(new - old) * 100 > PRICE_LOOK_PCT * old:
        reasons.append({'code': 'big_change', 'percent': round(100 * (new - old) / old)})
    # A Whole Set's price is its box's times the boxes per set: the box's Clover item name vouches for both.
    box = products.get(set_box.get(product['id'])) if product['stock_form'] == 'sealed_set' else product
    if not (box and (box['clover_item_name'] or '').strip()):
        reasons.append({'code': 'no_clover_name'})
    if product['stock_form'] == 'sealed_set' and box and old:
        single = next((other for other in options if other['product_id'] == box['id']), None)
        boxes = product['boxes_per_dan'] or box['boxes_per_dan']
        # The website's set must cost POPCORE's boxes per set at its Single Box's price: the price now, or the
        # one POPCORE replaced, since the box may have been written in an earlier pass than its set.
        singles = [cents for cents in (single and single['regular_cents'], single and before.get(single['wc_id'])) if cents]
        if singles and boxes and not any(abs(old / cents - boxes) < 0.02 for cents in singles):
            counts = [round(old / cents) for cents in singles if abs(old / cents - round(old / cents)) < 0.02]
            reasons.append({'code': 'set_size', 'popcore': boxes, 'website': counts[0] if counts else None,
                            'single_cents': singles[0]})
    return reasons + _sale_reasons(old, new, sale)


def _sale_reasons(old, new, sale):
    """Unless the sale discounts before and after, the change ends it (WooCommerce deletes a sale price
    at or above the new price) or starts it again (and the Dex emails the product's followers)."""
    if sale is not None and not (old is not None and sale < old and sale < new):
        return [{'code': 'sale_stops' if sale >= new else 'sale_starts', 'sale_cents': sale}]
    return []


def _design_reasons(item):
    """Why a design's new price waits for a manager's OK (owner, 2026-11-12: "Manager OK on big jumps"). A jump
    is measured from the design's own website price (design_base). A design that has none, such as a draft
    at its box's price or with no price, gets its first price: not a jump, so it goes."""
    base, new = item['design_base'], item['price']
    reasons = []
    if base and abs(new - base) * 100 > PRICE_LOOK_PCT * base:
        reasons.append({'code': 'big_change', 'percent': round(100 * (new - base) / base)})
    return reasons + _sale_reasons(item['regular_cents'], new, item['sale_cents'])


def _stored_cents(price):
    return design_prices.cents(price)


def _published(item, statuses):
    """Whether customers can see the item: published, and so is its parent product."""
    return item['status'] == 'publish' and (not item['parent_id'] or statuses.get(item['parent_id']) == 'publish')


def _design_context(con):
    """What a design's own website price is judged by: every website item's status, and the price POPCORE
    last wrote to each item."""
    statuses = {row[0]: row[1] for row in con.execute('SELECT wc_id, status FROM website_items')}
    written = {row[0]: row[1] for row in con.execute(
        """SELECT wc_id, new_cents FROM website_price_writes w
           WHERE id=(SELECT MAX(id) FROM website_price_writes WHERE wc_id=w.wc_id)""")}
    return statuses, written


def _design_base(item, context):
    """The design's own price on the website, or None when it has none there yet (owner, 2026-11-12).
    - A price customers can see (the item is published) is the design's own, whatever it came from.
    - So is a price POPCORE wrote there.
    - A draft's other price is not: on 2026-11-10, 1,473 of the website's 1,521 design drafts carried their box's
      price (often an old one) and 47 none, so a manager's first price for a draft is no jump."""
    statuses, written = context
    old = item['regular_cents']
    if old and (_published(item, statuses) or written.get(item['wc_id']) == old):
        return old
    return None


def _daily_window(cfg):
    """Whether this is the hour after the Daily Shop's reset, when a pooled product may be repriced."""
    local = _now().astimezone(TORONTO)
    reset = datetime.combine(local.date(), cfg['daily_reset'], TORONTO)
    if local < reset:
        reset -= timedelta(days=1)
    return local - reset < DAILY_WINDOW


def _plan_prices(con, cfg, items):
    """Give each linked item its price and, for a change, what happens to it: 'send', 'held' for a
    manager's OK, or 'daily' (a Daily Shop product waiting for the hour after the reset)."""
    products = {row['id']: row for row in con.execute(
        'SELECT id, stock_form, identity_status, price, clover_item_name, boxes_per_dan FROM products')}
    # Design prices a manager set (owner, 2026-11-12); a design without one is never priced.
    set_prices = {product_id: mark['cents'] for product_id, mark in design_prices.confirmed(con).items()}
    context = _design_context(con)
    set_box = {row[0]: row[1] for row in con.execute(
        """SELECT source_product_id, target_product_id FROM product_conversions c
           WHERE version=(SELECT MAX(version) FROM product_conversions WHERE source_product_id=c.source_product_id)""")}
    oks = {row[0]: (row[1], row[2]) for row in con.execute('SELECT wc_id, prices, approved_by FROM website_price_oks')}
    before = {row[0]: row[1] for row in con.execute(
        """SELECT wc_id, old_cents FROM website_price_writes w
           WHERE id=(SELECT MAX(id) FROM website_price_writes WHERE wc_id=w.wc_id)""")}
    by_product = {}
    for item in items:
        by_product.setdefault(item['parent_id'] or item['wc_id'], []).append(item)
    window = _daily_window(cfg)
    for wc_id, options in by_product.items():
        for item in options:
            product = products.get(item['product_id']) if item['product_id'] else None
            design = product is not None and product['stock_form'] == 'confirmed_design'
            item['design_base'] = _design_base(item, context) if design else None
            item['price'], item['price_left'] = (_wanted_price(item, product, products, set_box, set_prices) if product
                                                 else (None, None))
            item['price_change'] = item['price'] is not None and item['price'] != item['regular_cents']
            item['price_reasons'] = (_price_reasons(item, product, products, set_box, options, before)
                                     if item['price_change'] else [])
            # A design's first price of its own: the website had only its box's price, or none.
            item['price_note'] = 'first_design_price' if design and item['price_change'] and item['design_base'] is None else None
            item['price_state'] = item['price_ok_by'] = None
        changes = [item for item in options if item['price_change']]
        # The options of one product wait together, so a box and its Whole Set never part ways.
        held = any(item['price_reasons'] for item in changes)
        ok = oks.get(wc_id)
        approved = ok[1] if held and ok and ok[0] == _ok_key(changes) else None
        for item in changes:
            if held and not item['price_reasons']:
                item['price_reasons'] = [{'code': 'other_option'}]
            item['price_ok_by'] = approved
            item['price_state'] = ('held' if held and not approved
                                   else 'daily' if item['daily_pool'] and not window else 'send')


def _ok_key(changes):
    """What a manager's OK covers: each item's website price and sale price, and POPCORE's new price.
    Any of them changing asks again."""
    return json.dumps(sorted([item['wc_id'], item['regular_cents'], item['sale_cents'], item['price']]
                             for item in changes))


def _money(cents):
    return f'{cents // 100}.{cents % 100:02d}'


def approve_prices(con, cfg, wc_id, key, actor_sub):
    """A manager's OK for one website product's held price changes, exactly as the page showed them."""
    items, _ = plan(con, cfg)
    held = [item for item in items
            if (item['parent_id'] or item['wc_id']) == wc_id and item['price_change'] and item['price_reasons']]
    if not held:
        raise WebsiteError('No price of this product waits for an OK.', 'not_waiting')
    if _ok_key(held) != key:
        raise WebsiteError('This product’s prices changed since the page was loaded. Look again.', 'prices_changed')
    con.execute("""INSERT INTO website_price_oks(wc_id, prices, approved_by) VALUES (?, ?, ?)
                   ON CONFLICT(wc_id) DO UPDATE SET prices=excluded.prices, approved_by=excluded.approved_by,
                       approved_at=datetime('now')""", (wc_id, key, actor_sub))


def _vault(product):
    return 1 if _flag(product, VAULT_META) or _meta(product, KIND_META) == 'confirmed_design' else 0


def _reread_prices(con, cfg, items):
    """Read the items about to be repriced, and their products, from the website just before the write,
    so the checks judge the price, sale price and Daily Shop mark as they are now rather than as the
    last catalogue read found them. False when an item is gone or carries another POPCORE ID since."""
    products = sorted({item['parent_id'] or item['wc_id'] for item in items})
    fresh = {}
    for start in range(0, len(products), BATCH):
        for product in _all(cfg, '/products', {'include': ','.join(map(str, products[start:start + BATCH])),
                                               'status': 'any'}):
            fresh[product['id']] = product
    for parent in sorted({item['parent_id'] for item in items if item['parent_id']}):
        ids = sorted(item['wc_id'] for item in items if item['parent_id'] == parent)
        for variation in _all(cfg, f'/products/{parent}/variations', {'include': ','.join(map(str, ids))}):
            fresh[variation['id']] = variation
    for item in items:
        row, product = fresh.get(item['wc_id']), fresh.get(item['parent_id'] or item['wc_id'])
        if row is None or product is None or _popcore_id(row.get('meta_data')).upper() != item['popcore_id'].upper():
            return False
        # The status too: a design published since the daily read is judged as published.
        con.execute('UPDATE website_items SET regular_cents=?, sale_cents=?, daily_pool=?, vault=?, status=? WHERE wc_id=?',
                    (_price(row.get('regular_price')), _price(row.get('sale_price')), _flag(product, DAILY_META),
                     _vault(product), row.get('status') or '', item['wc_id']))
        if item['parent_id']:
            con.execute('UPDATE website_items SET status=? WHERE wc_id=?', (product.get('status') or '', item['parent_id']))
    con.commit()
    return True


def _available(item):
    """Whether the website sells the item now: in stock, with stock left when it counts stock."""
    return item['stock_status'] == 'instock' and (not item['manage_stock'] or (item['stock_quantity'] or 0) > 0)


def preview(con, cfg):
    items, parents = plan(con, cfg)
    changes = [item for item in items if item['stock_change']]
    holders = Counter(row[0].upper() for row in con.execute(
        "SELECT popcore_id FROM website_items WHERE kind != 'variable' AND popcore_id != ''"))
    unknown_ids = [{**item, 'shared': holders[item['popcore_id'].upper()] > 1} for item in items
                   if item['product_id'] is None]
    no_ids = [dict(row) for row in con.execute(
        """SELECT wc_id, parent_id, sku, name FROM website_items
           WHERE kind IN ('simple', 'variation') AND popcore_id = '' ORDER BY name""")]
    prices = [item for item in items if item['price_change']]
    names = {row[0]: row[1] for row in con.execute("SELECT wc_id, name FROM website_items WHERE kind != 'variation'")}
    held = {}
    for item in prices:
        if item['price_reasons'] and not item['price_ok_by']:
            wc_id = item['parent_id'] or item['wc_id']
            group = held.setdefault(wc_id, {'wc_id': wc_id, 'name': names.get(wc_id, item['name']), 'options': [],
                                            'changes': []})
            group['changes'].append(item)
            group['options'].append({key: item[key] for key in ('wc_id', 'popcore_id', 'name', 'regular_cents',
                                                                 'sale_cents', 'price', 'price_reasons', 'price_note')})
    for group in held.values():
        group['ok_key'] = _ok_key(group.pop('changes'))
    price_keys = ('wc_id', 'parent_id', 'popcore_id', 'name', 'regular_cents', 'sale_cents', 'price', 'price_state',
                  'price_note')
    return {
        # Price changes: sent with the next write, held for a manager's OK, or waiting for the Daily Shop's reset.
        'price_changes': len(prices),
        'price_send': sum(1 for item in prices if item['price_state'] == 'send'),
        'price_held': sum(1 for item in prices if item['price_state'] == 'held'),
        'price_daily': sum(1 for item in prices if item['price_state'] == 'daily'),
        'price_left': {why: sum(1 for item in items if item['price_left'] == why)
                       for why in ('design', 'not_clover', 'no_price')},
        'prices': [{key: item[key] for key in price_keys} for item in prices[:500]],
        'held_prices': sorted(held.values(), key=lambda group: group['name'].lower())[:200],
        'items': len(items) - len(unknown_ids),
        'unknown_id': sum(1 for item in unknown_ids if not item['shared']),
        'shared_id': sum(1 for item in unknown_ids if item['shared']), 'no_id': len(no_ids),
        'stock_changes': len(changes),
        # A stock write from 0 to more emails the item's restock and Dex followers; under 3 emails
        # the website's low-stock recipient, unless those emails are off (docs/website-sync.md).
        'back_in_stock': sum(1 for item in changes if item['quantity'] > 0 and not _available(item)),
        'sold_out': sum(1 for item in changes if item['quantity'] == 0 and _available(item)),
        'low_stock': sum(1 for item in changes if 0 < item['quantity'] <= 2),
        'pickup_changes': sum(1 for parent in parents if parent['pickup_change']),
        'shop_hold': {'items': sum(1 for item in items if item['shop_hold']),
                      'units': sum(item['shop_hold'] for item in items)},
        # Whole Sets that count the sets floor boxes are sure to have, and Whole Sets whose set size is unknown.
        'floor_sets': {'items': sum(1 for item in items if item['floor_sets']),
                       'sets': sum(item['floor_sets'] or 0 for item in items),
                       'unknown_size': sum(1 for item in items if item['sealed_sets'] is not None and not item['set_size'])},
        'changes': [{key: item[key] for key in ('wc_id', 'parent_id', 'popcore_id', 'name', 'product_id', 'manage_stock',
                                                'stock_quantity', 'stock_status', 'quantity', 'sealed_sets', 'floor_sets',
                                                'set_size')} for item in changes[:500]],
        'pickup': [parent for parent in parents if parent['pickup_change']][:500],
        'unknown_ids': [{key: item[key] for key in ('wc_id', 'parent_id', 'popcore_id', 'name', 'shared')}
                        for item in sorted(unknown_ids, key=lambda item: item['popcore_id'].upper())][:500],
        'no_ids': no_ids[:500],
    }


def price_link(con):
    """Whether design prices set in POPCORE can reach the website yet: a key on the server, the catalogue read,
    and the sync turned on."""
    try:
        configured = config() is not None
    except ValueError:
        configured = False
    return {'configured': configured, 'enabled': enabled(con), 'refreshed_at': state(con, 'refreshed_at')}


def design_items(con):
    """{design product id: its linked website items}, each with the design's own website price (base_cents:
    None when it has none there: a draft whose price POPCORE never wrote, or no price)."""
    context, found = _design_context(con), {}
    for row in con.execute("""SELECT w.* FROM website_items w JOIN products p ON p.id=w.product_id
                              WHERE p.stock_form='confirmed_design' AND w.kind IN ('simple', 'variation')
                              ORDER BY w.wc_id"""):
        item = dict(row)
        found.setdefault(item['product_id'], []).append({
            'wc_id': item['wc_id'], 'name': item['name'], 'status': item['status'],
            'published': _published(item, context[0]), 'regular_cents': item['regular_cents'],
            'sale_cents': item['sale_cents'], 'base_cents': _design_base(item, context)})
    return found


def _price_cfg():
    """The settings price judging needs, also while no website key is set."""
    try:
        cfg = config()
    except ValueError:
        cfg = None
    if cfg is None:
        try:
            cfg = {'daily_reset': _clock('WEBSITE_DAILY_RESET', '20:00')}
        except ValueError:
            cfg = {'daily_reset': datetime.strptime('20:00', '%H:%M').time()}
    return cfg


def design_price_effects(con, product_ids):
    """{design product id: what the website sync does with its price as POPCORE holds it now}, judged by the
    same rules as a write (_plan_prices). Run inside the caller's transaction, it judges a change before it is
    saved. state: 'send' (goes with the next write), 'held' (waits for a manager's OK), 'daily' (after the
    Daily Shop's reset), 'same' (the website already has it), 'left' (POPCORE does not price it)."""
    ids = set(product_ids)
    if not ids:
        return {}
    rows = [dict(row) for row in con.execute(
        "SELECT * FROM website_items WHERE kind IN ('simple', 'variation') AND popcore_id != '' ORDER BY wc_id")]
    # The other options of the same website product wait with it, so they are judged together.
    groups = {row['parent_id'] or row['wc_id'] for row in rows if row['product_id'] in ids}
    items = [row for row in rows if (row['parent_id'] or row['wc_id']) in groups]
    _plan_prices(con, _price_cfg(), items)
    statuses = {row[0]: row[1] for row in con.execute('SELECT wc_id, status FROM website_items')}
    found = {}
    for item in items:
        if item['product_id'] not in ids:
            continue
        state_now = item['price_state'] or ('same' if item['price'] is not None else 'left')
        found.setdefault(item['product_id'], []).append({
            'wc_id': item['wc_id'], 'name': item['name'], 'status': item['status'],
            'published': _published(item, statuses), 'regular_cents': item['regular_cents'],
            'sale_cents': item['sale_cents'], 'price': item['price'], 'state': state_now,
            'first': item['design_base'] is None, 'reasons': item['price_reasons']})
    return found


def push(con, cfg):
    """Write the numbers, prices and pickup stores that changed. Returns how many writes the website took."""
    items, parents = plan(con, cfg)
    pricing = [item for item in items if item['price_state'] == 'send']
    if pricing:
        if _reread_prices(con, cfg, pricing):
            reread = {item['wc_id'] for item in pricing}
            items, parents = plan(con, cfg)
            for item in items:
                if item['price_state'] == 'send' and item['wc_id'] not in reread:
                    item['price_state'] = None  # it waits for the next pass and its own read
        else:
            # The website changed under POPCORE: no price goes until the whole catalogue is read again.
            set_state(con, 'refresh_requested', '1')
            con.commit()
            for item in pricing:
                item['price_state'] = None
    updates = {}  # endpoint -> {id: {id, ...}}
    for item in items:
        path = f"/products/{item['parent_id']}/variations/batch" if item['parent_id'] else '/products/batch'
        if item['stock_change']:
            updates.setdefault(path, {}).setdefault(item['wc_id'], {'id': item['wc_id']}).update(
                manage_stock=True, stock_quantity=item['quantity'])
        if item['price_state'] == 'send':
            # The regular price only: a sale price stays the website's.
            updates.setdefault(path, {}).setdefault(item['wc_id'], {'id': item['wc_id']})['regular_price'] = \
                _money(item['price'])
    for parent in parents:
        if parent['pickup_change']:
            entry = updates.setdefault('/products/batch', {}).setdefault(parent['wc_id'], {'id': parent['wc_id']})
            entry['meta_data'] = [{'key': PICKUP_META, 'value': parent['wanted']}]
    by_id = {item['wc_id']: item for item in items}
    wanted = {item['wc_id']: item['quantity'] for item in items}
    pickups = {parent['wc_id']: parent['wanted'] for parent in parents}
    written = 0
    for path, sending in updates.items():
        rows = list(sending.values())
        for start in range(0, len(rows), BATCH):
            data, _ = call(cfg, 'POST', path, body={'update': rows[start:start + BATCH]})
            for result in (data or {}).get('update') or ():
                wc_id = result.get('id')
                error = result.get('error')
                if error or wc_id not in sending:
                    con.execute('UPDATE website_items SET error=? WHERE wc_id=?',
                                (str((error or {}).get('message') or 'Not updated')[:300], wc_id))
                    continue
                sent = sending[wc_id]
                if 'regular_price' in sent:
                    item = by_id[wc_id]
                    con.execute("UPDATE website_items SET regular_cents=?, error=NULL, pushed_at=datetime('now') WHERE wc_id=?",
                                (item['price'], wc_id))
                    con.execute("""INSERT INTO website_price_writes(wc_id, popcore_id, product_id, name, old_cents, new_cents,
                                       approved_by) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                (wc_id, item['popcore_id'], item['product_id'], item['name'], item['regular_cents'],
                                 item['price'], item['price_ok_by']))
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


def _line_item(line):
    """The website item an order line sold: its variation, else its product."""
    return line.get('wc_variation_id') or line.get('wc_product_id')


def _order_facts(con, order):
    """What POPCORE keeps of an order: no names, emails, phones or addresses. A line is linked by its
    item's POPCORE ID as the last catalogue read found it (its SKU is a barcode, never read)."""
    lines = []
    for line in order.get('line_items') or ():
        quantity = int(line.get('quantity') or 0)
        if quantity <= 0:
            continue
        item = {'wc_product_id': line.get('product_id') or None, 'wc_variation_id': line.get('variation_id') or None}
        popcore_id = _known_id(con, _line_item(item))
        lines.append({'popcore_id': popcore_id, 'name': str(line.get('name') or '')[:200], 'quantity': quantity,
                      'total_cents': _cents(line.get('total')), 'product_id': _product_for_id(con, popcore_id), **item})
    pickup = str(_meta(order, '_popcore_pickup_store_id') or '').strip().upper() or None
    fulfilment = str(_meta(order, '_pcc_fulfillment_type') or ('pickup' if pickup else 'delivery'))[:40]
    return {'wc_order_id': order['id'], 'number': str(order.get('number') or order['id'])[:40],
            'wc_status': str(order.get('status') or '')[:40], 'paid_at': order.get('date_paid_gmt'),
            'modified_at': order.get('date_modified_gmt'), 'total_cents': _cents(order.get('total')),
            'tax_cents': _cents(order.get('total_tax')),
            'payment': str(order.get('payment_method_title') or order.get('payment_method') or '')[:80],
            'transaction_id': str(order.get('transaction_id') or '')[:120] or None,
            'fulfilment': fulfilment, 'pickup_store': pickup, 'lines': lines}


def _read_ids(con, cfg, lines):
    """Lines whose item had no POPCORE ID at the last catalogue read (added on the website since, or
    given its ID since) read it from that one item on the website. Never inside a transaction."""
    for line in lines:
        if line.get('popcore_id') or not line.get('wc_product_id'):
            continue
        path = f"/products/{line['wc_product_id']}"
        if line.get('wc_variation_id'):
            path += f"/variations/{line['wc_variation_id']}"
        try:
            item, _ = call(cfg, 'GET', path)
        except WebsiteError as exc:
            if exc.status == 404:
                continue  # gone from the website: the line stays unlinked
            raise
        line['popcore_id'] = _popcore_id((item or {}).get('meta_data')) if isinstance(item, dict) else ''
        line['product_id'] = _product_for_id(con, line['popcore_id'])
    return lines


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
        # A link fixed since (a POPCORE ID set on the website, a sealed set added in POPCORE) counts on a retry.
        for line in lines:
            if line['product_id'] is None:
                line['popcore_id'] = line.get('popcore_id') or _known_id(con, _line_item(line))
                line['product_id'] = _product_for_id(con, line['popcore_id'])
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
        _read_ids(con, cfg, facts['lines'])
        con.execute("""INSERT INTO website_orders(wc_order_id, number, wc_status, paid_at, modified_at, total_cents,
                           tax_cents, payment, transaction_id, fulfilment, pickup_store, lines, state)
                       VALUES (:wc_order_id, :number, :wc_status, :paid_at, :modified_at, :total_cents, :tax_cents,
                           :payment, :transaction_id, :fulfilment, :pickup_store, :lines, 'new')""",
                    {**facts, 'lines': json.dumps(facts['lines'])})
        con.commit()
        return record(con, cfg, facts['wc_order_id'])
    if row['state'] == 'needs_review':
        _read_ids(con, cfg, facts['lines'])  # before the writes below
    con.execute("""UPDATE website_orders SET wc_status=?, modified_at=?, total_cents=?, tax_cents=?,
                       refunded=CASE WHEN ?='refunded' THEN 1 ELSE refunded END, updated_at=datetime('now')
                   WHERE wc_order_id=?""",
                (status, facts['modified_at'], facts['total_cents'], facts['tax_cents'], status, facts['wc_order_id']))
    if row['state'] == 'needs_review':
        # Lines are reread so a fixed link or POPCORE ID on the website lets the next try fill it.
        con.execute('UPDATE website_orders SET lines=?, pickup_store=? WHERE wc_order_id=?',
                    (json.dumps(facts['lines']), facts['pickup_store'], facts['wc_order_id']))
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
        'daily_reset': cfg['daily_reset'].strftime('%H:%M') if cfg else None,
        'report': [_report_state(con, cfg, store_id, codes[store_id])
                   for store_id in sorted(live, key=codes.get)] if cfg else [],
        'enabled': enabled(con), 'since': state(con, 'since'), 'switched_at': state(con, 'switched_at'),
        'refreshed_at': state(con, 'refreshed_at'), 'refresh_requested': bool(state(con, 'refresh_requested')),
        'last_run': state(con, 'last_run'), 'last_push_at': state(con, 'last_push_at'),
        'last_push_count': int(state(con, 'last_push_count', 0) or 0),
        'last_error': json.loads(error) if error else None,
        'live_stores': sorted(codes[store_id] for store_id in live),
        'push_errors': con.execute('SELECT COUNT(*) FROM website_items WHERE error IS NOT NULL').fetchone()[0],
        'price_writes': [dict(row) for row in con.execute(
            """SELECT wc_id, popcore_id, name, old_cents, new_cents, approved_by, written_at FROM website_price_writes
               ORDER BY id DESC LIMIT 30""")],
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
    row = con.execute('SELECT lines FROM website_orders WHERE wc_order_id=?', (wc_order_id,)).fetchone()
    lines = json.loads(row[0] or '[]') if row else []
    if any(not line.get('popcore_id') for line in lines):
        # A POPCORE ID set on the website just now counts, without waiting for the next catalogue read.
        try:
            _read_ids(con, cfg, lines)
        except WebsiteError:
            pass  # the website cannot be read: the order keeps waiting
        con.execute('UPDATE website_orders SET lines=? WHERE wc_order_id=? AND state=?',
                    (json.dumps(lines), wc_order_id, 'needs_review'))
        con.commit()
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
