"""popcore.ca takes its stock from POPCORE (owner, 2026-10-25 and 2026-11-01): website items are
linked by SKU, the live stores' shelves set the website number, paid web orders take their items
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
def _ago(minutes):
    """A website time (GMT) some minutes ago: the sync rereads only the last few minutes."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%S')


ENV = {'WEBSITE_URL': 'https://shop.test', 'WEBSITE_KEY': 'ck_test', 'WEBSITE_SECRET': 'cs_test'}


class Response:
    def __init__(self, status, data, headers=None):
        self.status_code, self._data, self.headers = status, data, headers or {}

    def json(self):
        return self._data


class FakeWoo:
    """Just enough of the WooCommerce REST API: products, variations, batch updates and orders."""

    def __init__(self):
        self.products = {}
        self.variations = {}  # parent id -> {id: variation}
        self.orders = {}
        self.writes = []
        self.refuse = False

    def product(self, wc_id, sku, *, kind='simple', in_stock=True, pickup=None, name=None):
        self.products[wc_id] = {'id': wc_id, 'type': kind, 'sku': sku, 'name': name or sku or f'Item {wc_id}',
                                'status': 'publish', 'manage_stock': False, 'stock_quantity': None,
                                'stock_status': 'instock' if in_stock else 'outofstock',
                                'meta_data': [] if pickup is None else [{'key': '_popcore_pickup_stores', 'value': pickup}]}
        if kind == 'variable':
            self.variations[wc_id] = {}

    def variation(self, parent, wc_id, sku, option):
        self.variations[parent][wc_id] = {'id': wc_id, 'sku': sku, 'status': 'publish', 'manage_stock': False,
                                          'stock_quantity': None, 'stock_status': 'instock',
                                          'attributes': [{'name': 'Size', 'option': option}]}

    def order(self, order_id, lines, *, status='processing', paid=None, modified=None, pickup=None):
        paid = paid or _ago(1)
        meta = [{'key': '_pcc_fulfillment_type', 'value': 'pickup' if pickup else 'delivery'}]
        if pickup:
            meta.append({'key': '_popcore_pickup_store_id', 'value': pickup})
        self.orders[order_id] = {
            'id': order_id, 'number': str(order_id), 'status': status, 'date_paid_gmt': paid,
            'date_modified_gmt': modified or paid, 'total': '45.20', 'total_tax': '5.20',
            'payment_method': 'stripe', 'payment_method_title': 'Credit Card (Stripe)', 'transaction_id': 'pi_123',
            'billing': {'email': 'someone@example.com', 'first_name': 'Private'}, 'meta_data': meta,
            'line_items': [{'sku': sku, 'name': sku, 'quantity': quantity, 'total': '20.00', 'product_id': 1,
                            'variation_id': 0} for sku, quantity in lines]}

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
        self.woo.product(10, '', kind='variable', name='Echo')
        self.woo.variation(10, 11, 'PM0100', 'Single Box')
        self.woo.variation(10, 12, 'PM0100-SET', 'Whole Set')
        self.woo.product(20, 'PM0200', pickup=['mk'])
        self.woo.product(30, 'PM0100-03', in_stock=False)
        self.woo.product(40, '6941848289407')  # a barcode, not a POPCORE ID: left alone
        self.woo.product(50, '')               # no SKU: left alone
        self.woo.product(60, 'DR0001')         # only MK has it, and MK is not live
        self.woo.product(70, 'PM0100-07')      # a POPCORE ID POPCORE does not have: none in stock
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
        self.assertEqual((preview['items'], preview['unknown_id'], preview['other_sku'], preview['no_sku']), (5, 1, 1, 1))
        self.assertEqual([row['sku'] for row in preview['unknown_ids']], ['PM0100-07'])
        self.assertEqual([row['sku'] for row in preview['other_skus']], ['6941848289407'])
        wanted = {change['sku']: change['quantity'] for change in preview['changes']}
        # Floor and upstairs, never display; MK is not live, so its products show none.
        self.assertEqual(wanted, {'PM0100': 14, 'PM0100-SET': 2, 'PM0200': 4, 'PM0100-03': 0, 'DR0001': 0,
                                  'PM0100-07': 0})
        self.assertEqual(preview['back_in_stock'], 0)
        self.assertEqual(preview['sold_out'], 2)  # DR0001 and PM0100-07 are in stock on the website today
        self.assertEqual({row['sku']: row['wanted'] for row in preview['pickup']},
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
        self.woo.order(500, [('PM0100', 6), ('PM0200', 2)])
        self.woo.products[20]['stock_quantity'] = 2  # the website lowered its own number at payment
        self.run_sync()
        order = self.order_row(500)
        self.assertEqual((order['state'], order['store_id'], order['problem']), ('recorded', self.dt, None))
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
        self.woo.order(501, [('PM0100-SET', 3)])   # only 2 sets
        self.woo.order(502, [('NOT-A-SKU', 1), ('PM0200', 1)], modified=_ago(0))
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
        # The unknown item gets a POPCORE product; Try again records the order.
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET sku='NOT-A-SKU' WHERE id=?", (self.mk_only,))
            con.commit()
        self.assertEqual(self.client.post('/api/website/orders/502/retry', headers=self.headers('manager')).get_json(),
                         {'state': 'needs_review'})  # MK's product: DT has none
        self.assertEqual(self.order_row(502)['problem'], 'not_enough_stock')

    def test_mk_pickup_is_filled_from_dt_until_mk_goes_live(self):
        self.turn_on()
        self.woo.order(503, [('PM0200', 1)], pickup='mk')
        self.run_sync()
        self.assertEqual((self.order_row(503)['state'], self.order_row(503)['store_id']), ('recorded', self.dt))

    def test_orders_paid_before_the_switch_are_ignored(self):
        self.turn_on()
        self.woo.order(504, [('PM0200', 1)], paid=_ago(120), modified=_ago(1))
        self.run_sync()
        self.assertIsNone(self.order_row(504))
        self.assertEqual(self.balance(self.item, 'floor'), 3)

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
            numbers = {change['sku']: change['quantity'] for change in preview['changes']
                       if change['sku'] in ('PM0100', 'PM0100-SET', 'PM0200')}
            pickup = {row['sku']: row['wanted'] for row in preview['pickup']}.get('PM0200')
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
