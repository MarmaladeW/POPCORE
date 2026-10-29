"""A live store's daily report takes its sales off the floor (owner, 2026-10-29): Clover is not
connected yet, so the pasted report is how sales leave stock. A changed report moves only the
difference, the floor never goes below zero, and what it lacks is listed for a recount."""
import io
import sys
from contextlib import closing, redirect_stdout
from pathlib import Path

from support import APP_DIR, IsolatedApiCase, db

ROOT = APP_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inventory_commands import post_inventory  # noqa: E402
from scripts.inventory_opening import main as _opening_cli  # noqa: E402

LIVE_DATE = '2026-11-01'
ADMIN = {'sub': 'auth0|admin', 'https://popcore/role': 'admin'}


class ReportStockTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.dt = con.execute("SELECT id FROM stores WHERE code='DT'").fetchone()[0]
            self.locations = {row[0]: row[1] for row in con.execute(
                'SELECT code, id FROM inventory_locations WHERE store_id=?', (self.dt,))}
            self.box = self._product(con, 'ECHO', 'Echo', 'random_box', 'box')
            self.set = self._product(con, 'ECHO-SET', 'Echo 整端', 'sealed_set', 'set')
            con.execute("""INSERT INTO product_conversions(source_product_id, target_product_id, output_per_input, version)
                           VALUES (?, ?, 12, 1)""", (self.set, self.box))
            self.item = self._product(con, 'BEAR', 'Bear 吊卡', 'ordinary', 'piece')
            for subject in ('auth0|staff', 'auth0|manager', 'auth0|admin'):
                con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)', (subject, self.dt))
            for code, product, form, quantity in (('floor', self.box, 'random_box', 30), ('floor', self.item, 'ordinary', 3),
                                                  ('upstairs', self.set, 'sealed_set', 2)):
                con.execute("""INSERT INTO opening_counts(location_id, product_id, stock_form, quantity, counted_by)
                               VALUES (?, ?, ?, ?, 'auth0|staff')""", (self.locations[code], product, form, quantity))
            con.commit()
        with redirect_stdout(io.StringIO()):
            _opening_cli(['intake', '--db', db.DB_PATH, '--from-app', '--store', 'DT',
                          '--empty-location', 'DT-claw', '--empty-location', 'DT-display',
                          '--business-date', LIVE_DATE, '--actor-sub', 'auth0|admin',
                          '--apply', '--backup', str(Path(self.tempdir.name) / 'before-dt.db')])

    @staticmethod
    def _product(con, sku, name, form, unit):
        return con.execute(
            """INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, identity_status)
               VALUES (?, ?, ?, ?, ?, 'verified')""", (sku, name, name, form, unit)).lastrowid

    def report(self, sales, *, day=LIVE_DATE, store='DT', role='staff'):
        items = [{'product_id': product, 'section': 'pos', 'qty_pos': quantity, 'qty_cash': 0, 'raw_name': str(product)}
                 for product, quantity in sales]
        return self.client.post('/api/sales/submit_daily_report', json={
            'date': day, 'store_code': store, 'classification': 'summary_only', 'items': items,
        }, headers=self.headers(role))

    def floor(self, product):
        with closing(self.connect()) as con:
            row = con.execute("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                                 AND disposition='saleable'""", (product, self.locations['floor'])).fetchone()
            return row[0] if row else 0

    def documents(self):
        with closing(self.connect()) as con:
            return [tuple(row) for row in con.execute(
                "SELECT kind, business_date FROM inventory_documents WHERE source_type='daily_report' ORDER BY id")]

    def test_a_report_takes_its_sales_off_the_floor(self):
        response = self.report([(self.box, 3), (self.item, 1)])
        self.assertEqual(response.status_code, 200, response.get_json())
        stock = response.get_json()['stock']
        self.assertEqual((stock['taken'], stock['returned'], stock['short']), (4, 0, []))
        self.assertEqual((self.floor(self.box), self.floor(self.item)), (27, 2))
        self.assertEqual(self.documents(), [('consume', LIVE_DATE)])
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?""",
                                         (self.set, self.locations['upstairs'])).fetchone()[0], 2)

    def test_what_the_floor_lacks_is_listed_and_tried_again(self):
        response = self.report([(self.item, 5), (self.product_id, 1)])
        self.assertEqual(response.status_code, 200, response.get_json())
        short = {row['product_id']: (row['quantity'], row['reason']) for row in response.get_json()['stock']['short']}
        self.assertEqual(short, {self.item: (2, 'not_enough_on_floor'), self.product_id: (1, 'not_counted')})
        self.assertEqual(self.floor(self.item), 0)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("SELECT qty_sold FROM daily_sales WHERE product_id=? AND date=?",
                                         (self.item, LIVE_DATE)).fetchone()[0], 5)
            post_inventory(con, {'kind': 'receipt', 'business_date': LIVE_DATE, 'lines': [
                {'product_id': self.item, 'quantity': 2, 'unit': 'piece', 'to_location_id': self.locations['floor'],
                 'to_disposition': 'saleable', 'expected_versions': {'to': 2}}]},
                actor=ADMIN, request_key='found-two')
        again = self.report([(self.item, 5), (self.product_id, 1)]).get_json()['stock']
        self.assertEqual((again['taken'], [row['product_id'] for row in again['short']]), (2, [self.product_id]))
        self.assertEqual(self.floor(self.item), 0)

    def test_a_changed_report_moves_only_the_difference(self):
        self.report([(self.box, 3)])
        fewer = self.report([(self.box, 1)]).get_json()['stock']
        self.assertEqual((fewer['taken'], fewer['returned']), (0, 2))
        self.assertEqual(self.floor(self.box), 29)
        same = self.report([(self.box, 1)]).get_json()['stock']
        self.assertEqual((same['taken'], same['returned'], same['documents']), (0, 0, []))
        cleared = self.client.delete('/api/sales/clear_day', query_string={'date': LIVE_DATE, 'store_code': 'DT'},
                                     headers=self.headers('manager'))
        self.assertEqual(cleared.status_code, 200, cleared.get_json())
        self.assertEqual((cleared.get_json()['stock']['returned'], self.floor(self.box)), (1, 30))
        self.assertEqual([kind for kind, _ in self.documents()], ['consume', 'correction', 'correction'])

    def test_a_whole_set_sold_takes_its_boxes_off_the_floor(self):
        stock = self.report([(self.set, 2)]).get_json()['stock']
        self.assertEqual((stock['taken'], stock['short']), (24, []))
        self.assertEqual(self.floor(self.box), 6)

    def test_an_edited_sale_follows_the_day_too(self):
        for quantity, left in ((2, 28), (5, 25), (0, 30)):
            response = self.client.post('/api/sales/upsert', json={
                'product_id': self.box, 'qty_pos': quantity, 'date': LIVE_DATE, 'store_code': 'DT',
            }, headers=self.headers('staff'))
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(self.floor(self.box), left)

    def test_days_before_going_live_and_stores_not_live_move_nothing(self):
        before = self.report([(self.box, 3)], day='2026-10-31')
        self.assertEqual((before.status_code, before.get_json()['stock']), (200, None))
        markham = self.report([(self.box, 3)], store='MK', role='admin')
        self.assertEqual((markham.status_code, markham.get_json()['stock']), (200, None))
        self.assertEqual((self.floor(self.box), self.documents()), (30, []))

    def test_who_pastes_a_live_report_needs_store_access(self):
        with closing(self.connect()) as con:
            con.execute("DELETE FROM inventory_access WHERE auth0_sub='auth0|staff'")
            con.commit()
        refused = self.report([(self.box, 3)])
        self.assertEqual((refused.status_code, refused.get_json()['code']), (403, 'inventory_forbidden'))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM daily_sales WHERE date=?', (LIVE_DATE,)).fetchone()[0], 0)
        self.assertEqual(self.floor(self.box), 30)
