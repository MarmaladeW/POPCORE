"""popcore.ca takes its stock from POPCORE (owner, 2026-10-25 and 2026-11-01): website items are
linked by their POPCORE ID field `_popcore_id`, never by SKU (owner, 2026-11-07), the live stores'
shelves set the website number, paid web orders take their items
out of the filling store's back stock first and stay out of its closing, a cancel puts them back,
and nothing is written to the website before an admin turns the sync on."""
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
        self.every_write = []  # never cleared
        self.refuse = False

    def product(self, wc_id, popcore_id='', *, sku='', kind='simple', in_stock=True, pickup=None, name=None,
                status='publish'):
        meta = _meta(popcore_id) + ([] if pickup is None else [{'key': '_popcore_pickup_stores', 'value': pickup}])
        self.products[wc_id] = {'id': wc_id, 'type': kind, 'sku': sku, 'name': name or popcore_id or f'Item {wc_id}',
                                'status': status, 'manage_stock': False, 'stock_quantity': None,
                                'stock_status': 'instock' if in_stock else 'outofstock', 'meta_data': meta}
        if kind == 'variable':
            self.variations[wc_id] = {}

    def variation(self, parent, wc_id, popcore_id, option, *, sku='', status='publish'):
        self.variations[parent][wc_id] = {'id': wc_id, 'sku': sku, 'status': status, 'manage_stock': False,
                                          'stock_quantity': None, 'stock_status': 'instock', 'meta_data': _meta(popcore_id),
                                          'attributes': [{'name': 'Size', 'option': option}]}

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
                if 'stock_quantity' in update:
                    item.update(manage_stock=True, stock_quantity=update['stock_quantity'],
                                stock_status='instock' if update['stock_quantity'] > 0 else 'outofstock')
                if 'meta_data' in update:
                    item['meta_data'] = [meta for meta in item.get('meta_data', ())
                                         if meta['key'] != '_popcore_pickup_stores'] + update['meta_data']
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
