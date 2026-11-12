"""popcore.ca takes its stock from POPCORE (owner, 2026-10-25 and 2026-11-01): website items are
linked by their POPCORE ID field `_popcore_id`, never by SKU (owner, 2026-11-07), the live stores'
shelves set the website number, paid web orders take their items
out of the filling store's back stock first and stay out of its closing, a cancel puts them back,
and nothing is written to the website before an admin turns the sync on. Since 2026-11-10 POPCORE
also writes the regular prices of linked boxes, Whole Sets and items, never sale prices; big or doubtful
changes wait for a manager's OK, and Daily Shop products wait for the reset. A design gets only a price a
manager set for it (owner, 2026-11-12), never the box price it was created with: test_design_prices.py."""
import io
import json
import sys
from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from support import APP_DIR, IsolatedApiCase, db

ROOT = APP_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import closing_operations  # noqa: E402
import website_sync  # noqa: E402
from blueprints import website  # noqa: E402
from inventory_commands import post_inventory  # noqa: E402
from scripts.inventory_opening import main as _opening_cli  # noqa: E402

LIVE_DATE = '2026-11-01'
ADMIN = {'sub': 'auth0|admin', 'https://popcore/role': 'admin'}
def _ago(minutes):
    """A website time (GMT) some minutes ago: the sync rereads only the last few minutes."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%S')


ENV = {'WEBSITE_URL': 'https://shop.test', 'WEBSITE_KEY': 'ck_test', 'WEBSITE_SECRET': 'cs_test'}


class Response:
    def __init__(self, status, data, headers=None):
        self.status_code, self._data, self.headers = status, data, headers or {}

    def json(self):
        return self._data


def _meta(popcore_id):
    """REST meta_data as WooCommerce returns it: the POPCORE ID is its own field, written by popcore-catalog."""
    return [{'id': 1, 'key': '_popcore_id', 'value': popcore_id}] if popcore_id else []


class FakeWoo:
    """Just enough of the WooCommerce REST API: products, variations, batch updates and orders. As on
    popcore.ca, an item's POPCORE ID is in meta_data and its SKU is a barcode or empty."""

    def __init__(self):
        self.products = {}
        self.variations = {}  # parent id -> {id: variation}
        self.orders = {}
        self.writes = []
        self.reads = []  # (path, params) of every GET, never cleared
        self.every_write = []  # never cleared
        self.refuse = False
        self.refuse_ids = set()  # items a batch write reports an error for

    def product(self, wc_id, popcore_id='', *, sku='', kind='simple', in_stock=True, pickup=None, name=None,
                status='publish', price='', sale='', meta=()):
        """meta: more (key, value) fields, such as the Daily Shop pool or the Vault mark. A variable
        parent's prices are always empty, as WooCommerce reports them."""
        meta = (_meta(popcore_id) + ([] if pickup is None else [{'key': '_popcore_pickup_stores', 'value': pickup}])
                + [{'key': key, 'value': value} for key, value in meta])
        self.products[wc_id] = {'id': wc_id, 'type': kind, 'sku': sku, 'name': name or popcore_id or f'Item {wc_id}',
                                'status': status, 'manage_stock': False, 'stock_quantity': None,
                                'stock_status': 'instock' if in_stock else 'outofstock', 'meta_data': meta,
                                'regular_price': price, 'sale_price': sale}
        if kind == 'variable':
            self.variations[wc_id] = {}

    def variation(self, parent, wc_id, popcore_id, option, *, sku='', status='publish', price='', sale=''):
        self.variations[parent][wc_id] = {'id': wc_id, 'sku': sku, 'status': status, 'manage_stock': False,
                                          'stock_quantity': None, 'stock_status': 'instock', 'meta_data': _meta(popcore_id),
                                          'attributes': [{'name': 'Size', 'option': option}],
                                          'regular_price': price, 'sale_price': sale}

    def set_price(self, wc_id, price=None, sale=None):
        """The website edits an item's regular or sale price by hand ('' clears it)."""
        item = self.item(wc_id)
        item.update({key: value for key, value in (('regular_price', price), ('sale_price', sale)) if value is not None})

    def set_id(self, wc_id, popcore_id):
        """The website edits an item's POPCORE ID ('' removes it)."""
        item = self.item(wc_id)
        item['meta_data'] = [meta for meta in item['meta_data'] if meta['key'] != '_popcore_id'] + _meta(popcore_id)

    def parent_of(self, wc_id):
        return next((parent for parent, variations in self.variations.items() if wc_id in variations), None)

    def order(self, order_id, lines, *, status='processing', paid=None, modified=None, pickup=None):
        """lines: (website item id, quantity). A line names its product and variation, and carries the
        item's SKU (a barcode), as WooCommerce's line_items do."""
        paid = paid or _ago(1)
        meta = [{'key': '_pcc_fulfillment_type', 'value': 'pickup' if pickup else 'delivery'}]
        if pickup:
            meta.append({'key': '_popcore_pickup_store_id', 'value': pickup})
        line_items = []
        for wc_id, quantity in lines:
            parent, item = self.parent_of(wc_id), self.item(wc_id) or {}
            line_items.append({'sku': item.get('sku', ''), 'name': item.get('name') or f'Item {wc_id}',
                               'quantity': quantity, 'total': '20.00', 'product_id': parent or wc_id,
                               'variation_id': wc_id if parent else 0, 'meta_data': []})
        self.orders[order_id] = {
            'id': order_id, 'number': str(order_id), 'status': status, 'date_paid_gmt': paid,
            'date_modified_gmt': modified or paid, 'total': '45.20', 'total_tax': '5.20',
            'payment_method': 'stripe', 'payment_method_title': 'Credit Card (Stripe)', 'transaction_id': 'pi_123',
            'billing': {'email': 'someone@example.com', 'first_name': 'Private'}, 'meta_data': meta,
            'line_items': line_items}

    def item(self, wc_id):
        if wc_id in self.products:
            return self.products[wc_id]
        for variations in self.variations.values():
            if wc_id in variations:
                return variations[wc_id]
        return None

    def request(self, method, url, params=None, json=None, auth=None, **kwargs):
        if self.refuse or auth != ('ck_test', 'cs_test'):
            return Response(401, {'code': 'woocommerce_rest_cannot_view', 'message': 'Sorry, you cannot list resources.'})
        path = urlsplit(url).path.removeprefix('/wp-json/wc/v3')
        params = params or {}
        if method == 'GET':
            self.reads.append((path, dict(params)))
            parts = path.strip('/').split('/')
            if len(parts) in (2, 4) and parts[0] == 'products':  # one product, or one variation
                item = (self.products.get(int(parts[1])) if len(parts) == 2
                        else self.variations.get(int(parts[1]), {}).get(int(parts[3])))
                if item is None:
                    return Response(404, {'code': 'woocommerce_rest_product_invalid_id', 'message': 'Invalid ID.'})
                return Response(200, dict(item))
            if path == '/products':
                rows = list(self.products.values())
            elif path.startswith('/products/') and path.endswith('/variations'):
                rows = list(self.variations[int(path.split('/')[2])].values())
            elif path == '/orders':
                after = params.get('modified_after', '')
                rows = sorted((order for order in self.orders.values() if order['date_modified_gmt'] > after),
                              key=lambda order: order['date_modified_gmt'])
            else:
                return Response(404, {'message': 'No route'})
            if params.get('include'):
                wanted = {int(wc_id) for wc_id in str(params['include']).split(',')}
                rows = [row for row in rows if row['id'] in wanted]
            size, page = params.get('per_page', 10), params.get('page', 1)
            pages = max(1, -(-len(rows) // size))
            return Response(200, [dict(row) for row in rows[(page - 1) * size:page * size]],
                            {'X-WP-TotalPages': str(pages)})
        if method == 'POST' and path.endswith('/batch'):
            results = []
            for update in json['update']:
                self.writes.append((path, dict(update)))
                self.every_write.append((path, dict(update)))
                item = self.item(update['id'])
                if item is None:
                    results.append({'id': 0, 'error': {'message': 'Invalid ID.'}})
                    continue
                if update['id'] in self.refuse_ids:
                    results.append({'id': update['id'], 'error': {'message': 'Not saved.'}})
                    continue
                if 'stock_quantity' in update:
                    item.update(manage_stock=True, stock_quantity=update['stock_quantity'],
                                stock_status='instock' if update['stock_quantity'] > 0 else 'outofstock')
                if 'meta_data' in update:
                    item['meta_data'] = [meta for meta in item.get('meta_data', ())
                                         if meta['key'] != '_popcore_pickup_stores'] + update['meta_data']
                for key in ('regular_price', 'sale_price'):
                    if key in update:
                        item[key] = update[key]
                results.append(dict(item))
            return Response(200, {'update': results})
        return Response(404, {'message': 'No route'})


class WebsiteSyncTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(website.bp)
        with closing(self.connect()) as con:
            self.dt = con.execute("SELECT id FROM stores WHERE code='DT'").fetchone()[0]
            self.locations = {row[0]: row[1] for row in con.execute(
                'SELECT code, id FROM inventory_locations WHERE store_id=?', (self.dt,))}
            self.box = self._product(con, 'PM0100', 'Echo', 'random_box', 'box')
            self.set = self._product(con, 'PM0100-SET', 'Echo 整端', 'sealed_set', 'set')
            con.execute("""INSERT INTO product_conversions(source_product_id, target_product_id, output_per_input, version)
                           VALUES (?, ?, 12, 1)""", (self.set, self.box))
            self.design = self._product(con, 'PM0100-03', 'Echo Rain', 'confirmed_design', 'piece')
            self.item = self._product(con, 'PM0200', 'Bear 吊卡', 'ordinary', 'piece')
            self.mk_only = self._product(con, 'DR0001', 'Smiski', 'ordinary', 'piece')
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|admin', ?)", (self.dt,))
            for code, product, form, quantity in (
                    ('floor', self.box, 'random_box', 10), ('upstairs', self.box, 'random_box', 4),
                    ('upstairs', self.set, 'sealed_set', 2), ('display', self.design, 'confirmed_design', 1),
                    ('floor', self.item, 'ordinary', 3), ('upstairs', self.item, 'ordinary', 1),
                    ('floor', self.design, 'confirmed_design', 0)):
                con.execute("""INSERT INTO opening_counts(location_id, product_id, stock_form, quantity, counted_by)
                               VALUES (?, ?, ?, ?, 'auth0|admin')""", (self.locations[code], product, form, quantity))
            con.commit()
        with redirect_stdout(io.StringIO()):
            _opening_cli(['intake', '--db', db.DB_PATH, '--from-app', '--store', 'DT', '--empty-location', 'DT-claw',
                          '--business-date', LIVE_DATE, '--actor-sub', 'auth0|admin',
                          '--apply', '--backup', str(Path(self.tempdir.name) / 'before-dt.db')])
        self.woo = FakeWoo()
        self.woo.product(10, kind='variable', name='Echo')  # a variable parent holds no ID
        self.woo.variation(10, 11, 'PM0100', 'Single Box', sku='6941848263001')
        self.woo.variation(10, 12, 'PM0100-SET', 'Whole Set')
        self.woo.product(20, 'PM0200', sku='6941848263018', pickup=['mk'])
        self.woo.product(30, 'PM0100-03', in_stock=False)
        self.woo.product(40, sku='6941848289407', name='Gift card')  # a barcode and no POPCORE ID: left alone
        self.woo.product(50, name='Tote bag')                       # nothing at all: left alone
        self.woo.product(60, 'DR0001')         # only MK has it, and MK is not live
        self.woo.product(70, 'PM0100-07')      # a POPCORE ID POPCORE does not have: none in stock
        # A SKU shaped like a POPCORE ID is never read as one: no POPCORE ID, left alone.
        self.woo.product(80, sku='PM0200', name='Bear 吊卡 old listing')
        env = patch.dict('os.environ', ENV)
        env.start()
        self.addCleanup(env.stop)
        network = patch.object(website_sync.requests, 'request', side_effect=self.woo.request)
        network.start()
        self.addCleanup(network.stop)

    @staticmethod
    def _product(con, sku, name, form, unit):
        return con.execute(
            """INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, identity_status)
               VALUES (?, ?, ?, ?, ?, 'verified')""", (sku, name, name, form, unit)).lastrowid

    def run_sync(self):
        website_sync.background_sync()

    def post(self, kind, product, location_id, quantity, unit):
        """Receive stock onto a live shelf, or consume it from one."""
        side = 'to' if kind == 'receipt' else 'from'
        with closing(self.connect()) as con:
            row = con.execute("""SELECT version FROM inventory_balances WHERE product_id=? AND location_id=?
                                 AND disposition='saleable'""", (product, location_id)).fetchone()
            post_inventory(con, {'kind': kind, 'business_date': LIVE_DATE, 'lines': [{
                'product_id': product, 'unit': unit, 'quantity': quantity, f'{side}_location_id': location_id,
                f'{side}_disposition': 'saleable', 'expected_versions': {side: row[0] if row else 0}}]},
                actor=ADMIN, request_key=f'test:{kind}:{product}:{location_id}:{quantity}')

    def open_mk(self, floor_boxes):
        """MK goes live with `floor_boxes` Echo boxes on its floor and nothing else."""
        with closing(self.connect()) as con:
            mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            locations = {row[0]: row[1] for row in con.execute(
                'SELECT code, id FROM inventory_locations WHERE store_id=?', (mk,))}
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|admin', ?)", (mk,))
            con.execute("""INSERT INTO opening_counts(location_id, product_id, stock_form, quantity, counted_by)
                           VALUES (?, ?, 'random_box', ?, 'auth0|admin')""", (locations['floor'], self.box, floor_boxes))
            con.commit()
        empty = [arg for code in sorted(locations) if code != 'floor' for arg in ('--empty-location', f'MK-{code}')]
        with redirect_stdout(io.StringIO()):
            _opening_cli(['intake', '--db', db.DB_PATH, '--from-app', '--store', 'MK', *empty,
                          '--business-date', LIVE_DATE, '--actor-sub', 'auth0|admin',
                          '--apply', '--backup', str(Path(self.tempdir.name) / 'before-mk.db')])
        return mk, locations

    def preview(self):
        return self.client.get('/api/website/preview', headers=self.headers('manager')).get_json()

    def change(self, preview, wc_id):
        return next(change for change in preview['changes'] if change['wc_id'] == wc_id)

    def balance(self, product, code):
        with closing(self.connect()) as con:
            row = con.execute("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                                 AND disposition='saleable'""", (product, self.locations[code])).fetchone()
            return row[0] if row else 0

    def state(self, key):
        with closing(self.connect()) as con:
            return website_sync.state(con, key)

    def order_row(self, order_id):
        with closing(self.connect()) as con:
            row = con.execute('SELECT * FROM website_orders WHERE wc_order_id=?', (order_id,)).fetchone()
            return dict(row) if row else None

    def turn_on(self):
        self.assertEqual(self.client.post('/api/website/refresh', headers=self.headers('admin')).status_code, 202)
        self.run_sync()
        response = self.client.post('/api/website/switch', json={'on': True}, headers=self.headers('admin'))
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(self.connect()) as con:  # orders from now on count
            website_sync.set_state(con, 'since', _ago(60))
            website_sync.set_state(con, 'orders_after', _ago(60))
            con.commit()
        self.run_sync()
        self.woo.writes.clear()

    def popcore_price(self, product, price, clover_name=None, boxes=None):
        """POPCORE's price for a product; a Clover item name says Clover sells it at that price."""
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET price=?, clover_item_name=?, boxes_per_dan=COALESCE(?, boxes_per_dan) WHERE id=?',
                        (price, clover_name, boxes, product))
            con.commit()

    def prices_written(self, every=False):
        return {update['id']: update['regular_price'] for _, update in (self.woo.every_write if every else self.woo.writes)
                if 'regular_price' in update}

    def preview(self):
        return self.client.get('/api/website/preview', headers=self.headers('manager')).get_json()

    def ok_prices(self, wc_id, key=None, role='manager'):
        """A manager's OK, with the key the page was given for that product unless another is named."""
        if key is None:
            key = next(group['ok_key'] for group in self.preview()['held_prices'] if group['wc_id'] == wc_id)
        return self.client.post(f'/api/website/prices/{wc_id}/ok', json={'key': key}, headers=self.headers(role))

    def held_reasons(self):
        return {option['wc_id']: option['price_reasons'] for group in self.preview()['held_prices']
                for option in group['options']}

    def test_nothing_is_written_before_an_admin_turns_it_on(self):
        self.assertEqual(self.client.post('/api/website/refresh', headers=self.headers('manager')).status_code, 403)
        self.assertEqual(self.client.get('/api/website/preview', headers=self.headers('manager')).status_code, 409)
        self.assertEqual(self.client.post('/api/website/refresh', headers=self.headers('admin')).status_code, 202)
        self.run_sync()
        self.run_sync()
        self.assertEqual(self.woo.writes, [])
        preview = self.client.get('/api/website/preview', headers=self.headers('manager')).get_json()
        self.assertEqual((preview['items'], preview['unknown_id'], preview['shared_id'], preview['no_id']), (5, 1, 0, 3))
        self.assertEqual([(row['popcore_id'], row['shared']) for row in preview['unknown_ids']], [('PM0100-07', False)])
        self.assertEqual([(row['wc_id'], row['sku']) for row in preview['no_ids']],
                         [(80, 'PM0200'), (40, '6941848289407'), (50, '')])
        wanted = {change['popcore_id']: change['quantity'] for change in preview['changes']}
        # Floor and upstairs, never display; MK is not live, so its products show none.
        self.assertEqual(wanted, {'PM0100': 14, 'PM0100-SET': 2, 'PM0200': 4, 'PM0100-03': 0, 'DR0001': 0,
                                  'PM0100-07': 0})
        self.assertEqual(preview['back_in_stock'], 0)
        self.assertEqual(preview['sold_out'], 2)  # DR0001 and PM0100-07 are in stock on the website today
        self.assertEqual({row['popcore_id']: row['wanted'] for row in preview['pickup']},
                         {'': ['dt'], 'PM0200': ['dt']})
        self.assertEqual(self.client.post('/api/website/switch', json={'on': True},
                                          headers=self.headers('manager')).status_code, 403)

    def test_turning_on_pushes_stock_and_pickup_then_only_changes(self):
        self.turn_on()
        self.assertEqual(self.client.get('/api/website/status', headers=self.headers('manager')).get_json()['enabled'],
                         True)
        # The first pass after turning on already wrote everything; nothing changed since.
        self.run_sync()
        self.assertEqual(self.woo.writes, [])
        self.assertEqual(self.woo.item(11)['stock_quantity'], 14)
        self.assertTrue(self.woo.item(11)['manage_stock'])
        self.assertEqual(self.woo.item(12)['stock_quantity'], 2)
        self.assertEqual(self.woo.item(20)['stock_quantity'], 4)
        self.assertEqual(self.woo.item(30)['stock_status'], 'outofstock')
        self.assertEqual(self.woo.item(60)['stock_status'], 'outofstock')
        self.assertEqual(self.woo.item(70)['stock_quantity'], 0)
        self.assertFalse(self.woo.item(40)['manage_stock'])
        self.assertFalse(self.woo.item(50)['manage_stock'])
        self.assertFalse(self.woo.item(80)['manage_stock'])
        # Only items with a POPCORE ID are written, and never their SKU (it holds barcodes).
        self.assertEqual({update['id'] for _, update in self.woo.every_write}, {10, 11, 12, 20, 30, 60, 70})
        self.assertFalse(any('sku' in update for _, update in self.woo.every_write))
        self.assertEqual(self.woo.item(11)['sku'], '6941848263001')
        self.assertEqual(website_sync._pickup(self.woo.item(20)['meta_data']), ['dt'])
        self.assertEqual(website_sync._pickup(self.woo.item(10)['meta_data']), ['dt'])
        # A sale in the shop changes one number, and only that one is written.
        with closing(self.connect()) as con:
            version = con.execute("""SELECT version FROM inventory_balances WHERE product_id=? AND location_id=?
                                     AND disposition='saleable'""", (self.item, self.locations['floor'])).fetchone()[0]
            post_inventory(con, {'kind': 'consume', 'business_date': LIVE_DATE, 'lines': [{
                'product_id': self.item, 'unit': 'piece', 'quantity': 1, 'from_location_id': self.locations['floor'],
                'from_disposition': 'saleable', 'expected_versions': {'from': version}}]},
                actor={'sub': 'auth0|admin', 'https://popcore/role': 'admin'}, request_key='shop-sale')
        self.run_sync()
        self.assertEqual(self.woo.writes, [('/products/batch', {'id': 20, 'manage_stock': True, 'stock_quantity': 3})])

    def test_a_paid_order_takes_back_stock_first_and_stays_out_of_closing(self):
        self.turn_on()
        self.woo.order(500, [(11, 6), (20, 2)])
        self.woo.products[20]['stock_quantity'] = 2  # the website lowered its own number at payment
        self.run_sync()
        order = self.order_row(500)
        self.assertEqual((order['state'], order['store_id'], order['problem']), ('recorded', self.dt, None))
        self.assertEqual([(line['popcore_id'], line['product_id']) for line in json.loads(order['lines'])],
                         [('PM0100', self.box), ('PM0200', self.item)])
        self.assertNotIn('someone@example.com', json.dumps(order))
        # Upstairs first (4 boxes), the floor for the rest.
        self.assertEqual((self.balance(self.box, 'upstairs'), self.balance(self.box, 'floor')), (0, 8))
        self.assertEqual((self.balance(self.item, 'upstairs'), self.balance(self.item, 'floor')), (0, 2))
        written = {update['id']: update.get('stock_quantity') for _, update in self.woo.writes}
        self.assertEqual(written, {11: 8, 20: 2})
        with closing(self.connect()) as con:
            document = con.execute('SELECT * FROM inventory_documents WHERE id=?',
                                   (order['inventory_document_id'],)).fetchone()
            self.assertEqual((document['kind'], document['source_type'], document['actor_sub']),
                             ('consume', 'website_order', website_sync.WEBSITE_SUB))
            session = {'id': 0, 'version': 1, 'status': 'open', 'intake_complete': 0, 'store_id': self.dt,
                       'business_date': document['business_date']}
            facts = closing_operations._source_facts(con, session)
            self.assertNotIn(order['inventory_document_id'], [row[0] for row in facts['inventory_documents']])
        # Read again in the overlap: nothing moves twice.
        self.run_sync()
        self.assertEqual(self.balance(self.box, 'floor'), 8)
        # Cancelled on the website: the items go back where they came from.
        self.woo.orders[500].update(status='cancelled', date_modified_gmt=_ago(0))
        self.run_sync()
        order = self.order_row(500)
        self.assertEqual(order['state'], 'cancelled')
        self.assertEqual((self.balance(self.box, 'upstairs'), self.balance(self.box, 'floor')), (4, 10))
        self.assertEqual((self.balance(self.item, 'upstairs'), self.balance(self.item, 'floor')), (1, 3))

    def test_an_order_popcore_cannot_fill_waits_and_holds_its_units(self):
        self.turn_on()
        self.woo.order(501, [(12, 3)])   # only 2 sets
        self.woo.order(502, [(50, 1), (20, 1)], modified=_ago(0))  # the tote bag has no POPCORE ID
        self.run_sync()
        self.assertEqual((self.order_row(501)['state'], self.order_row(501)['problem']), ('needs_review', 'not_enough_stock'))
        self.assertEqual((self.order_row(502)['state'], self.order_row(502)['problem']), ('needs_review', 'unknown_item'))
        self.assertEqual(self.balance(self.set, 'upstairs'), 2)
        # The website already took them off; POPCORE's number must not put them back.
        self.assertEqual(self.woo.item(12)['stock_quantity'], 0)
        self.assertEqual(self.woo.item(20)['stock_quantity'], 3)
        orders = self.client.get('/api/website/orders', headers=self.headers('manager')).get_json()['orders']
        self.assertEqual({row['wc_order_id'] for row in orders[:2]}, {501, 502})  # waiting orders first
        self.assertEqual(self.client.post('/api/website/orders/501/dismiss', json={},
                                          headers=self.headers('manager')).status_code, 400)
        response = self.client.post('/api/website/orders/501/dismiss', json={'reason': 'Third set came from MK'},
                                    headers=self.headers('manager'))
        self.assertEqual(response.get_json()['state'], 'dismissed')
        self.run_sync()
        self.assertEqual(self.woo.item(12)['stock_quantity'], 2)
        # The website gives the item a POPCORE ID: Try again reads it from that item, without waiting
        # for the next catalogue read, and links the line (any case).
        with closing(self.connect()) as con:
            tote = self._product(con, 'DR0002', 'Tote bag', 'ordinary', 'piece')
            con.commit()
        self.woo.set_id(50, 'dr0002')
        self.assertEqual(self.client.post('/api/website/orders/502/retry', headers=self.headers('manager')).get_json(),
                         {'state': 'needs_review'})  # DT has none of it
        order = self.order_row(502)
        self.assertEqual(order['problem'], 'not_enough_stock')
        self.assertEqual([(line['popcore_id'], line['product_id']) for line in json.loads(order['lines'])],
                         [('dr0002', tote), ('PM0200', self.item)])

    def test_mk_pickup_is_filled_from_dt_until_mk_goes_live(self):
        self.turn_on()
        self.woo.order(503, [(20, 1)], pickup='mk')
        self.run_sync()
        self.assertEqual((self.order_row(503)['state'], self.order_row(503)['store_id']), ('recorded', self.dt))

    def test_orders_paid_before_the_switch_are_ignored(self):
        self.turn_on()
        self.woo.order(504, [(20, 1)], paid=_ago(120), modified=_ago(1))
        self.run_sync()
        self.assertIsNone(self.order_row(504))
        self.assertEqual(self.balance(self.item, 'floor'), 3)

    def test_an_order_for_an_item_added_since_the_last_read_reads_its_id(self):
        self.turn_on()
        # A new listing takes over PM0200 on the website after POPCORE last read the catalogue.
        self.woo.set_id(20, '')
        self.woo.product(90, 'PM0200', sku='6941848263025', name='Bear 吊卡')
        self.woo.order(505, [(90, 1)])
        self.woo.order(506, [(999, 1)], modified=_ago(0))  # deleted from the website since
        self.run_sync()
        order = self.order_row(505)
        self.assertEqual((order['state'], json.loads(order['lines'])[0]['popcore_id']), ('recorded', 'PM0200'))
        self.assertEqual((self.balance(self.item, 'upstairs'), self.balance(self.item, 'floor')), (0, 3))
        self.assertEqual((self.order_row(506)['state'], self.order_row(506)['problem']), ('needs_review', 'unknown_item'))
        self.assertIsNone(self.state('last_error'))

    def test_ids_link_in_any_case_and_an_id_on_two_items_links_neither(self):
        self.woo.set_id(20, ' pm0200 ')             # trimmed, and matched in any case
        self.woo.set_id(10, 'PM0100')               # a variable parent's stray ID is never read
        self.woo.product(95, 'pm0100-03', name='Echo Rain again')  # the same ID as item 30
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        preview = self.client.get('/api/website/preview', headers=self.headers('manager')).get_json()
        self.assertEqual((preview['items'], preview['unknown_id'], preview['shared_id']), (4, 1, 2))
        self.assertEqual([(row['wc_id'], row['shared']) for row in preview['unknown_ids']],
                         [(30, True), (95, True), (70, False)])
        wanted = {change['wc_id']: change['quantity'] for change in preview['changes']}
        self.assertEqual((wanted[11], wanted[20], wanted[30], wanted[95]), (14, 4, 0, 0))
        with closing(self.connect()) as con:
            rows = {row[0]: (row[1], row[2]) for row in con.execute(
                'SELECT wc_id, popcore_id, product_id FROM website_items WHERE wc_id IN (10, 20, 30, 95)')}
        self.assertEqual(rows, {10: ('', None), 20: ('pm0200', self.item), 30: ('PM0100-03', None),
                                95: ('pm0100-03', None)})

    def test_drafts_and_disabled_options_with_an_id_are_kept_in_step_too(self):
        """WooCommerce's REST API lists drafts and disabled (private) options with status=any. Their
        numbers are written like any item's, so they are right the moment the website publishes them."""
        self.woo.products[20].update(status='draft')
        self.woo.products[20]['meta_data'].append({'key': '_popcore_source', 'value': 'clover-2026-11'})
        self.woo.variations[10][12].update(status='private')
        self.turn_on()
        self.assertEqual((self.woo.item(20)['stock_quantity'], self.woo.item(12)['stock_quantity']), (4, 2))
        self.assertEqual((self.woo.item(20)['status'], self.woo.item(12)['status']), ('draft', 'private'))

    def test_links_made_by_sku_are_dropped_on_upgrade(self):
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        with closing(self.connect()) as con:  # the table as the SKU link left it
            con.execute('ALTER TABLE website_items DROP COLUMN popcore_id')
            con.execute("DELETE FROM _migrations WHERE name='website_items_popcore_id'")
            con.commit()
        db.migrate_db()
        db.migrate_db()  # safe to re-run
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM website_items').fetchone()[0], 0)
            self.assertIn('popcore_id', {row[1] for row in con.execute('PRAGMA table_info(website_items)')})
        # The products must be read again before the preview or turning on.
        self.assertEqual(self.client.get('/api/website/preview', headers=self.headers('manager')).status_code, 409)
        self.assertEqual(self.client.post('/api/website/switch', json={'on': True},
                                          headers=self.headers('admin')).status_code, 409)
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        self.assertEqual(self.client.get('/api/website/preview', headers=self.headers('manager')).get_json()['items'], 5)

    def test_floor_sets_follow_the_owners_rule(self):
        """Owner, 2026-11-10: with 12 boxes per set, 25 floor boxes mean at least one whole set and 37 at
        least two. The rule gives that minimum, not how many sets there are."""
        cases = {(0, 12): 0, (24, 12): 0, (25, 12): 1, (36, 12): 1, (37, 12): 2, (49, 12): 3,
                 (13, 6): 1, (12, 6): 0, (100, None): 0, (100, 0): 0, (-3, 12): 0}
        self.assertEqual({case: website_sync.floor_sets(*case) for case in cases}, cases)

    def test_a_whole_set_counts_its_sealed_sets_and_the_sets_its_floor_boxes_are_sure_to_have(self):
        self.post('receipt', self.box, self.locations['floor'], 15, 'box')  # 25 boxes on DT's floor
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        preview = self.preview()
        whole = self.change(preview, 12)
        self.assertEqual((whole['quantity'], whole['sealed_sets'], whole['floor_sets'], whole['set_size']), (3, 2, 1, 12))
        # The Single Box still counts every loose box; other items carry no set facts.
        single = self.change(preview, 11)
        self.assertEqual((single['quantity'], single['sealed_sets'], single['floor_sets']), (29, None, None))
        self.assertEqual(preview['floor_sets'], {'items': 1, 'sets': 1, 'unknown_size': 0})
        # Boxes kept in an opened set are not loose: 25 - 1 = 24 floor boxes are not sure to have a whole set.
        with closing(self.connect()) as con:
            document = con.execute("SELECT id FROM inventory_documents WHERE kind='opening'").fetchone()[0]
            con.execute("""INSERT INTO inventory_open_sets(opening_document_id, random_product_id, location_id, purpose,
                               remaining_qty) VALUES (?, ?, ?, 'customer_tray', 1)""",
                        (document, self.box, self.locations['floor']))
            con.commit()
        self.assertEqual(self.change(self.preview(), 12)['floor_sets'], 0)

    def test_floor_sets_are_counted_store_by_store_after_the_shop_hold(self):
        self.post('receipt', self.box, self.locations['floor'], 15, 'box')  # DT floor: 25 boxes
        mk, _ = self.open_mk(25)
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        # At least one set at each store: 2, not the at least 3 that 50 boxes in one place would be sure of.
        whole = self.change(self.preview(), 12)
        self.assertEqual((whole['sealed_sets'], whole['floor_sets'], whole['quantity']), (2, 2, 4))
        # DT's best report day sold 2 boxes and today is not reported: DT's 23 floor boxes are not sure of a set.
        with closing(self.connect()) as con:
            con.execute("INSERT INTO daily_sales(product_id, date, store, qty_pos, qty_sold) VALUES (?, '2026-05-20', 'DT', 2, 2)",
                        (self.box,))
            con.commit()
        moment = datetime(2026, 11, 1, 13, 0, tzinfo=website_sync.TORONTO).astimezone(timezone.utc)
        with patch.object(website_sync, '_now', return_value=moment):
            whole = self.change(self.preview(), 12)
        self.assertEqual((whole['sealed_sets'], whole['floor_sets'], whole['quantity']), (2, 1, 3))

    def test_a_whole_set_with_an_unknown_set_size_counts_no_floor_sets(self):
        with closing(self.connect()) as con:
            box = self._product(con, 'PM0400', 'Moon', 'random_box', 'box')
            self._product(con, 'PM0400-SET', 'Moon 整端', 'sealed_set', 'set')  # no conversion recorded
            con.commit()
        self.woo.product(15, kind='variable', name='Moon')
        self.woo.variation(15, 16, 'PM0400-SET', 'Whole Set')
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        preview = self.preview()
        whole = self.change(preview, 16)
        self.assertEqual((whole['quantity'], whole['sealed_sets'], whole['floor_sets'], whole['set_size']), (0, 0, 0, None))
        self.assertEqual(preview['floor_sets']['unknown_size'], 1)
        # Without a conversion, the box's own boxes per set is the set size.
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET boxes_per_dan=6 WHERE id=?', (box,))
            con.commit()
        self.assertEqual(self.change(self.preview(), 16)['set_size'], 6)

    def test_when_the_only_sets_are_floor_sets_an_order_waits_for_a_manager(self):
        """Owner, 2026-11-10: a web Whole Set order the store cannot fill from a sealed set asks a manager."""
        # A listing with only the Whole Set option, so its pickup stores come from sets alone.
        self.woo.variations[10].pop(12)
        self.woo.product(15, kind='variable', name='Echo whole set')
        self.woo.variation(15, 12, 'PM0100-SET', 'Whole Set')
        self.post('consume', self.set, self.locations['upstairs'], 2, 'set')
        self.turn_on()
        self.assertEqual((self.woo.item(12)['stock_quantity'], website_sync._pickup(self.woo.item(15)['meta_data'])),
                         (0, []))
        self.post('receipt', self.box, self.locations['floor'], 15, 'box')  # 25 floor boxes: at least one set
        self.run_sync()
        self.assertEqual((self.woo.item(12)['stock_quantity'], website_sync._pickup(self.woo.item(15)['meta_data'])),
                         (1, ['dt']))
        self.woo.order(507, [(12, 1)])
        self.run_sync()
        order = self.order_row(507)
        self.assertEqual((order['state'], order['problem']), ('needs_review', 'not_enough_stock'))
        # Nothing is taken, and the waiting order holds its set off the website.
        self.assertEqual((self.balance(self.box, 'floor'), self.balance(self.set, 'upstairs')), (25, 0))
        self.assertEqual(self.woo.item(12)['stock_quantity'], 0)
        self.assertEqual(self.woo.item(11)['stock_quantity'], 29)

    def test_keep_back_and_opened_sets_lower_the_website_number(self):
        with closing(self.connect()) as con:
            document = con.execute("SELECT id FROM inventory_documents WHERE kind='opening'").fetchone()[0]
            con.execute("""INSERT INTO inventory_open_sets(opening_document_id, random_product_id, location_id, purpose,
                               remaining_qty) VALUES (?, ?, ?, 'customer_tray', 5)""",
                        (document, self.box, self.locations['floor']))
            con.commit()
        with patch.dict('os.environ', {'WEBSITE_KEEP_BACK': '1'}):
            self.turn_on()
        self.assertEqual(self.woo.item(11)['stock_quantity'], 14 - 5 - 1)
        self.assertEqual(self.woo.item(12)['stock_quantity'], 1)

    def test_the_shop_keeps_its_best_day_until_its_report_is_in(self):
        """Owner, 2026-11-02: the shop's sales arrive with the pasted daily report, so until a shop
        day's report is in each item holds back the shop's best day of the last 30 reports."""
        with closing(self.connect()) as con:
            # Earlier reports: Bear 吊卡's best day was 3; Echo's was one whole set (12 boxes) and a box.
            for day, product, sold in (('2026-05-20', self.item, 3), ('2026-05-21', self.item, 1),
                                       ('2026-05-21', self.set, 1), ('2026-05-21', self.box, 1),
                                       ('2026-05-22', self.box, 5)):
                con.execute("INSERT INTO daily_sales(product_id, date, store, qty_pos, qty_sold) VALUES (?, ?, 'DT', ?, ?)",
                            (product, day, sold, sold))
            con.commit()
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()

        def at(local, opens=None):
            moment = datetime.fromisoformat(local).replace(tzinfo=website_sync.TORONTO).astimezone(timezone.utc)
            with patch.object(website_sync, '_now', return_value=moment), \
                    patch.dict('os.environ', {'WEBSITE_SHOP_OPENS': opens} if opens else {}):
                preview = self.client.get('/api/website/preview', headers=self.headers('manager')).get_json()
                status = self.client.get('/api/website/status', headers=self.headers('manager')).get_json()
            numbers = {change['popcore_id']: change['quantity'] for change in preview['changes']
                       if change['popcore_id'] in ('PM0100', 'PM0100-SET', 'PM0200')}
            pickup = {row['popcore_id']: row['wanted'] for row in preview['pickup']}.get('PM0200')
            return numbers, pickup, preview['shop_hold'], status['report']

        # The opening count holds everything before go-live; the shop has not opened yet.
        numbers, pickup, hold, report = at('2026-11-01T11:59')
        self.assertEqual(numbers, {'PM0100': 14, 'PM0100-SET': 2, 'PM0200': 4})
        self.assertEqual(hold, {'items': 0, 'units': 0})
        self.assertEqual(report, [{'store': 'DT', 'reported_through': '2026-10-31', 'days_behind': 0}])
        # Once the shop opens, its best day comes off; sealed sets are not what the floor sells.
        numbers, pickup, hold, report = at('2026-11-01T12:00')
        self.assertEqual(numbers, {'PM0100': 1, 'PM0100-SET': 2, 'PM0200': 1})
        self.assertEqual((pickup, hold), (['dt'], {'items': 2, 'units': 16}))
        self.assertEqual(report[0]['days_behind'], 1)
        # A day later with no report, two shop days come off.
        numbers, pickup, hold, report = at('2026-11-02T13:00')
        self.assertEqual(numbers, {'PM0100': 0, 'PM0100-SET': 2, 'PM0200': 0})
        self.assertEqual((pickup, hold, report[0]['days_behind']), ([], {'items': 2, 'units': 18}, 2))
        # Nov 1's report is in: nothing is held overnight, and the next shop day holds one day again.
        with closing(self.connect()) as con:
            con.execute("INSERT INTO daily_report_metadata(date, store) VALUES ('2026-11-01', 'DT')")
            con.commit()
        numbers, _, _, report = at('2026-11-02T09:00')
        self.assertEqual(numbers, {'PM0100': 14, 'PM0100-SET': 2, 'PM0200': 4})
        self.assertEqual(report, [{'store': 'DT', 'reported_through': '2026-11-01', 'days_behind': 0}])
        self.assertEqual(at('2026-11-02T11:00', opens='10:30')[0], {'PM0100': 1, 'PM0100-SET': 2, 'PM0200': 1})
        with patch.dict('os.environ', {'WEBSITE_SHOP_OPENS': 'noon'}):
            with self.assertRaises(ValueError):
                website_sync.config()

    def test_a_refused_key_is_reported(self):
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.woo.refuse = True
        self.run_sync()
        status = self.client.get('/api/website/status', headers=self.headers('manager')).get_json()
        self.assertEqual(status['last_error']['code'], 'website_auth')
        self.assertIsNone(status['refreshed_at'])
        self.woo.refuse = False
        self.run_sync()
        status = self.client.get('/api/website/status', headers=self.headers('manager')).get_json()
        self.assertIsNone(status['last_error'])
        self.assertIsNotNone(status['refreshed_at'])

    # ── Prices (owner, 2026-11-10) ──────────────────────────────────────────

    def test_prices_follow_popcore_for_boxes_sets_and_items_never_designs(self):
        """POPCORE sends a linked box's or item's Clover price, and to the Whole Set the boxes per set times
        that price: Clover stores no set price (owner, 2026-11-10). It never sends a design the box price the
        design was created with, nor prices a Vault product that is not a POPCORE design, never touches a sale price, and leaves an item alone when its POPCORE record is an old
        (unverified) one or has no price."""
        self.popcore_price(self.box, 20.99, 'Echo Clover', boxes=12)
        self.popcore_price(self.set, 999.99, boxes=12)  # the set record's own price is never read
        self.popcore_price(self.design, 20.99)  # POPCORE repeats the box price in a design
        self.popcore_price(self.item, 15.00, 'Bear Clover')
        with closing(self.connect()) as con:
            con.execute("INSERT INTO products(sku, name_cn_en, price, identity_status) VALUES ('PM0300', 'Old record', 9.99, 'unverified')")
            blue = self._product(con, 'PM0400', 'Bear 吊卡 blue', 'ordinary', 'piece')
            con.commit()
        self.woo.set_price(11, '19.99')
        self.woo.set_price(12, '239.88')
        self.woo.set_price(20, '14.00')
        self.woo.set_price(30, '95.00')  # a design's own price
        self.woo.set_price(60, '9.99')   # DR0001 has no POPCORE price
        self.woo.product(85, 'PM0300', price='12.99')
        self.woo.product(36, 'PM0400', price='199.99', meta=[('_popcore_is_vault', '1')])  # marked Vault on the website
        self.popcore_price(blue, 30.00, 'Bear blue Clover')
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        preview = self.preview()
        self.assertEqual({row['popcore_id']: (row['regular_cents'], row['price'], row['price_state'])
                          for row in preview['prices']},
                         {'PM0100': (1999, 2099, 'send'), 'PM0100-SET': (23988, 25188, 'send'),
                          'PM0200': (1400, 1500, 'send')})
        self.assertEqual((preview['price_changes'], preview['price_send'], preview['price_held'], preview['price_daily']),
                         (3, 3, 0, 0))
        self.assertEqual(preview['price_left'], {'design': 2, 'not_clover': 1, 'no_price': 1})
        self.assertEqual(self.woo.every_write, [])

        self.turn_on()
        self.assertEqual(self.prices_written(every=True), {11: '20.99', 12: '251.88', 20: '15.00'})
        self.assertFalse(any('sale_price' in update for _, update in self.woo.every_write))
        self.assertEqual([self.woo.item(wc_id)['regular_price'] for wc_id in (30, 36, 60, 85)],
                         ['95.00', '199.99', '9.99', '12.99'])
        with closing(self.connect()) as con:  # every write is kept with the price before
            log = [tuple(row) for row in con.execute(
                'SELECT wc_id, popcore_id, old_cents, new_cents, approved_by FROM website_price_writes ORDER BY wc_id')]
        self.assertEqual(log, [(11, 'PM0100', 1999, 2099, None), (12, 'PM0100-SET', 23988, 25188, None),
                               (20, 'PM0200', 1400, 1500, None)])
        # Only what changed since POPCORE last set it: a new Clover price writes that one price.
        self.run_sync()
        self.assertEqual(self.woo.writes, [])
        self.popcore_price(self.item, 15.50, 'Bear Clover')
        self.run_sync()
        self.assertEqual(self.woo.writes, [('/products/batch', {'id': 20, 'regular_price': '15.50'})])
        # A price edited on the website is put back at the next full read of the catalogue.
        self.woo.writes.clear()
        self.woo.set_price(20, '13.00')
        self.run_sync()
        self.assertEqual(self.woo.writes, [])
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        self.assertEqual(self.woo.writes, [('/products/batch', {'id': 20, 'regular_price': '15.50'})])
        status = self.client.get('/api/website/status', headers=self.headers('manager')).get_json()
        self.assertEqual([(row['wc_id'], row['old_cents'], row['new_cents']) for row in status['price_writes'][:2]],
                         [(20, 1300, 1550), (20, 1500, 1550)])

    def test_big_unchecked_or_mismatched_changes_wait_for_a_managers_ok(self):
        """A change over 30%, a box or item with no Clover item name, or a Whole Set the website prices as a
        different number of boxes (Marvel Deadpool: 9 on the website, 12 in POPCORE) waits for a manager's
        OK, and the other options of the same product wait with it."""
        self.popcore_price(self.box, 20.99, 'Echo Clover', boxes=12)
        self.popcore_price(self.set, 251.88, boxes=12)
        self.popcore_price(self.item, 15.00)  # no Clover item name: never checked against Clover
        self.woo.set_price(11, '19.99')
        self.woo.set_price(12, '179.91')  # 9 boxes of 19.99
        self.woo.set_price(20, '14.00')
        self.turn_on()
        self.assertEqual(self.prices_written(every=True), {})
        self.assertEqual(self.woo.item(11)['stock_quantity'], 14)  # stock is written as before
        preview = self.preview()
        self.assertEqual((preview['price_send'], preview['price_held']), (0, 3))
        groups = {group['wc_id']: group for group in preview['held_prices']}
        self.assertEqual({wc_id: (group['name'], [(option['wc_id'], option['price']) for option in group['options']])
                          for wc_id, group in groups.items()},
                         {10: ('Echo', [(11, 2099), (12, 25188)]), 20: ('PM0200', [(20, 1500)])})
        self.assertEqual(self.held_reasons(),
                         {11: [{'code': 'other_option'}],
                          12: [{'code': 'big_change', 'percent': 40},
                               {'code': 'set_size', 'popcore': 12, 'website': 9, 'single_cents': 1999}],
                          20: [{'code': 'no_clover_name'}]})

        self.assertEqual(self.ok_prices(10, role='staff').status_code, 403)
        self.assertEqual(self.ok_prices(10, key=2099).status_code, 400)
        self.assertEqual(self.ok_prices(10, key=groups[20]['ok_key']).get_json()['code'], 'prices_changed')
        self.assertEqual(self.ok_prices(30, key=groups[10]['ok_key']).get_json()['code'], 'not_waiting')
        self.assertEqual(self.ok_prices(10).get_json(), {'ok': True})
        self.run_sync()
        self.assertEqual(self.prices_written(), {11: '20.99', 12: '251.88'})
        with closing(self.connect()) as con:
            self.assertEqual({tuple(row) for row in con.execute('SELECT wc_id, approved_by FROM website_price_writes')},
                             {(11, 'auth0|manager'), (12, 'auth0|manager')})
        # An OK is for exactly the change shown: POPCORE changing the price again asks again.
        self.assertEqual(self.ok_prices(20).status_code, 200)
        self.popcore_price(self.item, 16.00)
        self.woo.writes.clear()
        self.run_sync()
        self.assertEqual(self.prices_written(), {})
        self.assertEqual([[option['price'] for option in group['options']] for group in self.preview()['held_prices']],
                         [[1600]])
        # So does a new website price: an OK given and used once never approves a later change.
        self.ok_prices(20)
        self.run_sync()
        self.assertEqual(self.prices_written(), {20: '16.00'})
        self.woo.writes.clear()
        self.woo.set_price(20, '10.00', '14.50')  # edited by hand on the website
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        self.assertEqual(self.prices_written(), {})
        self.assertEqual(self.held_reasons(), {20: [{'code': 'big_change', 'percent': 60}, {'code': 'no_clover_name'},
                                                    {'code': 'sale_starts', 'sale_cents': 1450}]})

    def test_a_first_price_and_a_set_unlike_its_box_wait_for_a_managers_ok(self):
        """A website item with no price (or $0) cannot be bought today, so a first price waits for an OK.
        A Whole Set waits, with its box, when its website price is not POPCORE's boxes per set times its
        Single Box (DR0051: 6 per set; a box of 11 on the website against POPCORE's 12 here)."""
        self.popcore_price(self.mk_only, 12.99, 'Smiski Clover')
        self.woo.set_price(60, '')
        self.popcore_price(self.box, 20.99, 'Echo Clover')
        self.popcore_price(self.set, None, boxes=12)
        self.woo.set_price(11, '19.99')
        self.woo.set_price(12, '219.89')  # 11 boxes of 19.99 on the website
        self.turn_on()
        self.assertEqual(self.prices_written(every=True), {})
        self.assertEqual(self.held_reasons(),
                         {60: [{'code': 'first_price'}], 11: [{'code': 'other_option'}],
                          12: [{'code': 'set_size', 'popcore': 12, 'website': 11, 'single_cents': 1999}]})
        # The website's set fixed to 12 boxes: box and set go together.
        self.woo.set_price(12, '239.88')
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        self.assertEqual(self.prices_written(), {11: '20.99', 12: '251.88'})

    def test_a_set_refused_by_the_website_goes_once_its_box_has(self):
        """A batch the website takes only in part can write a box and not its set. The set's website price
        is then checked against the box's price POPCORE replaced, so it goes in a later pass."""
        self.popcore_price(self.box, 20.99, 'Echo Clover')
        self.popcore_price(self.set, None, boxes=12)
        self.woo.set_price(11, '19.99')
        self.woo.set_price(12, '239.88')
        self.woo.refuse_ids = {12}
        self.turn_on()
        self.assertEqual(self.woo.item(11)['regular_price'], '20.99')
        self.assertEqual(self.woo.item(12)['regular_price'], '239.88')
        self.woo.refuse_ids = set()
        self.run_sync()
        self.assertEqual(self.prices_written(), {12: '251.88'})

    def test_prices_are_checked_against_the_website_just_before_they_are_written(self):
        """The catalogue is read once a day. Just before a price goes, POPCORE reads those items again: a
        sale or a Daily Shop mark added on the website since then counts, and an item that has lost or
        changed its POPCORE ID gets no price until the whole catalogue is read again."""
        self.popcore_price(self.item, 15.00, 'Bear Clover')
        self.popcore_price(self.box, 20.99, 'Echo Clover', boxes=12)
        self.woo.set_price(20, '14.00')
        self.woo.set_price(11, '19.99')
        self.woo.set_price(12, '239.88')
        self.turn_on()
        self.assertEqual(self.prices_written(every=True), {20: '15.00', 11: '20.99', 12: '251.88'})
        self.assertIn(('/products', {'include': '10,20', 'status': 'any', 'per_page': 100, 'page': 1}), self.woo.reads)
        # After that read, the website puts Bear 吊卡 on sale and Echo in the Daily Shop.
        self.woo.set_price(20, sale='15.50')
        self.woo.products[10]['meta_data'].append({'key': '_popcore_daily_pool', 'value': 'yes'})
        self.popcore_price(self.item, 15.25, 'Bear Clover')
        self.popcore_price(self.box, 21.99, 'Echo Clover')
        moment = datetime.fromisoformat('2026-11-10T15:00').replace(tzinfo=website_sync.TORONTO).astimezone(timezone.utc)
        with patch.object(website_sync, '_now', return_value=moment):
            self.run_sync()
            self.assertEqual(self.prices_written(), {})
            self.assertEqual(self.held_reasons(), {20: [{'code': 'sale_stops', 'sale_cents': 1550}]})
            self.assertEqual(self.preview()['price_daily'], 2)
        # At 20:10 Echo's box may go, but the website has since given its options each other's POPCORE IDs:
        # nothing is priced until the catalogue is read again, and then the options are linked anew.
        self.woo.set_id(11, 'PM0100-SET')
        self.woo.set_id(12, 'PM0100')
        with patch.object(website_sync, '_now', return_value=moment + timedelta(hours=5, minutes=10)):
            self.run_sync()
            self.assertEqual(self.prices_written(), {})
            self.assertEqual(self.state('refresh_requested'), '1')
            self.run_sync()
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT popcore_id FROM website_items WHERE wc_id=11').fetchone()[0], 'PM0100-SET')
        self.assertIsNone(self.state('refresh_requested'))

    def test_a_sale_that_would_stop_or_start_waits_for_a_managers_ok(self):
        """POPCORE never writes a sale price. A regular price at or below the sale price leaves a sale that
        no longer discounts (popcore.ca #21007: sale $66.99, Clover $54.99), and one above a sale that does
        not discount today starts it again, which emails the product's Dex followers."""
        self.popcore_price(self.item, 54.99, 'Bear Clover')
        self.popcore_price(self.box, 22.99, 'Echo Clover')
        self.popcore_price(self.mk_only, 12.99, 'Smiski Clover')
        self.woo.set_price(20, '69.99', '66.99')  # the sale would stop
        self.woo.set_price(11, '20.99', '21.99')  # a sale above today's price would start again
        self.woo.set_price(60, '11.99', '9.99')   # a sale before and after: the change goes
        self.turn_on()
        self.assertEqual(self.prices_written(every=True), {60: '12.99'})
        self.assertEqual(self.woo.item(60)['sale_price'], '9.99')
        self.assertEqual({option['wc_id']: option['price_reasons'] for group in self.preview()['held_prices']
                          for option in group['options']},
                         {20: [{'code': 'sale_stops', 'sale_cents': 6699}],
                          11: [{'code': 'sale_starts', 'sale_cents': 2199}]})
        # The sale is cleared on the website: after the next read the change goes without an OK.
        self.woo.set_price(20, sale='')
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        self.assertEqual(self.prices_written(), {20: '54.99'})

    def test_daily_shop_products_are_repriced_only_in_the_hour_after_the_reset(self):
        """popcore.ca's Daily Shop prices a deal from the product's price when it is read, so a reprice
        during the day changes prices in customers' bags: pooled products wait for the reset (20:00)."""
        self.popcore_price(self.box, 20.99, 'Echo Clover', boxes=12)
        self.popcore_price(self.item, 15.00, 'Bear Clover')
        self.woo.products[10]['meta_data'].append({'key': '_popcore_daily_pool', 'value': 'yes'})  # its options follow
        self.woo.set_price(11, '19.99')
        self.woo.set_price(12, '239.88')
        self.woo.set_price(20, '14.00')  # not in the pool: goes at once

        def at(local):
            return datetime.fromisoformat(local).replace(tzinfo=website_sync.TORONTO).astimezone(timezone.utc)
        moment = [at('2026-11-10T15:00')]
        with patch.object(website_sync, '_now', side_effect=lambda: moment[0]):
            self.turn_on()
            self.assertEqual(self.prices_written(every=True), {20: '15.00'})
            self.assertEqual((self.preview()['price_daily'], self.preview()['price_send']), (2, 0))
            moment[0] = at('2026-11-10T19:59')
            self.run_sync()
            self.assertEqual(self.prices_written(), {})
            moment[0] = at('2026-11-10T20:00')
            self.run_sync()
            self.assertEqual(self.prices_written(), {11: '20.99', 12: '251.88'})
        cfg = website_sync.config()
        for local, inside in (('2026-11-10T20:59', True), ('2026-11-10T21:00', False), ('2026-11-11T08:00', False)):
            with patch.object(website_sync, '_now', return_value=at(local)):
                self.assertEqual(website_sync._daily_window(cfg), inside, local)
        with patch.dict('os.environ', {'WEBSITE_DAILY_RESET': '23:30'}):
            cfg = website_sync.config()
        with patch.object(website_sync, '_now', return_value=at('2026-11-11T00:10')):
            self.assertTrue(website_sync._daily_window(cfg))
        with patch.dict('os.environ', {'WEBSITE_DAILY_RESET': '8pm'}):
            with self.assertRaises(ValueError):
                website_sync.config()

    def test_the_price_upgrade_reads_the_products_again(self):
        """The catalogue read saved before the upgrade holds no prices, so nothing is written, and no
        change is judged, until the products are read again."""
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        with closing(self.connect()) as con:  # the tables as they were before prices
            for column in ('regular_cents', 'sale_cents', 'daily_pool', 'vault'):
                con.execute(f'ALTER TABLE website_items DROP COLUMN {column}')
            con.execute('DROP TABLE website_price_oks')
            con.execute('DROP TABLE website_price_writes')
            con.execute("DELETE FROM _migrations WHERE name='website_prices'")
            con.commit()
        db.migrate_db()
        db.migrate_db()  # safe to re-run
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM website_items').fetchone()[0], 0)
            self.assertTrue({'regular_cents', 'sale_cents', 'daily_pool', 'vault'}
                            <= {row[1] for row in con.execute('PRAGMA table_info(website_items)')})
        self.assertIsNone(self.state('refreshed_at'))
        self.assertEqual(self.client.get('/api/website/preview', headers=self.headers('manager')).status_code, 409)
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        self.assertEqual(self.preview()['items'], 5)
