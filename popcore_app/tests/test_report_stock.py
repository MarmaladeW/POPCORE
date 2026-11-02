"""A live store's daily report takes its sales off the floor (owner, 2026-10-29): Clover is not
connected yet, so the pasted report is how sales leave stock. A changed report moves only the
difference, the floor never goes below zero, a set size is never assumed, only managers and admins
paste reports, and every mismatch waits on the manager home until it is settled or checked."""
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

    def report(self, sales, *, day=LIVE_DATE, store='DT', role='manager'):
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

    def notices(self, role='manager'):
        response = self.client.get('/api/sales/report-stock', headers=self.headers(role))
        self.assertEqual(response.status_code, 200, response.get_json())
        return {(row['product_id'], row['reason']): row for row in response.get_json()['notices']}

    def check(self, notice):
        response = self.client.post(f"/api/sales/report-stock/{notice['id']}/check", headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())

    def retry(self, day=LIVE_DATE):
        response = self.client.post('/api/sales/report-stock/retry', json={'date': day, 'store_code': 'DT'},
                                    headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['stock']

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
        notices = self.notices()
        self.assertEqual({key: row['quantity'] for key, row in notices.items()},
                         {(self.item, 'not_enough_on_floor'): 2, (self.product_id, 'not_counted'): 1})
        self.assertEqual((notices[(self.item, 'not_enough_on_floor')]['floor'],
                          notices[(self.item, 'not_enough_on_floor')]['store_code']), (0, 'DT'))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("SELECT qty_sold FROM daily_sales WHERE product_id=? AND date=?",
                                         (self.item, LIVE_DATE)).fetchone()[0], 5)
            post_inventory(con, {'kind': 'receipt', 'business_date': LIVE_DATE, 'lines': [
                {'product_id': self.item, 'quantity': 2, 'unit': 'piece', 'to_location_id': self.locations['floor'],
                 'to_disposition': 'saleable', 'expected_versions': {'to': 2}}]},
                actor=ADMIN, request_key='found-two')
        again = self.retry()
        self.assertEqual((again['taken'], [row['product_id'] for row in again['short']]), (2, [self.product_id]))
        self.assertEqual(self.floor(self.item), 0)
        self.assertEqual(list(self.notices()), [(self.product_id, 'not_counted')])

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
            }, headers=self.headers('manager'))
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(self.floor(self.box), left)

    def test_days_before_going_live_and_stores_not_live_move_nothing(self):
        before = self.report([(self.box, 3)], day='2026-10-31')
        self.assertEqual((before.status_code, before.get_json()['stock']), (200, None))
        markham = self.report([(self.box, 3)], store='MK', role='admin')
        self.assertEqual((markham.status_code, markham.get_json()['stock']), (200, None))
        self.assertEqual((self.floor(self.box), self.documents()), (30, []))

    def test_only_managers_and_admins_paste_reports(self):
        day = {'date': LIVE_DATE, 'store_code': 'DT'}
        for path, body in (('/api/sales/parse_report', {'text': 'POS:\nEcho 1', 'store_code': 'DT'}),
                           ('/api/sales/submit_daily_report', {**day, 'classification': 'summary_only', 'items': []}),
                           ('/api/sales/upsert', {**day, 'product_id': self.box, 'qty_pos': 1}),
                           ('/api/sales/batch_upsert', {'store_code': 'DT', 'items': [
                               {'product_id': self.box, 'date': LIVE_DATE, 'qty_pos': 1}]}),
                           ('/api/sales/report-stock/retry', day)):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, json=body, headers=self.headers('staff')).status_code, 403)
        self.assertEqual(self.client.get('/api/sales/report-stock', headers=self.headers('staff')).status_code, 403)
        self.assertEqual(self.report([(self.box, 1)], role='admin').status_code, 200)
        self.assertEqual(self.floor(self.box), 29)

    def test_who_pastes_a_live_report_needs_store_access(self):
        with closing(self.connect()) as con:
            con.execute("DELETE FROM inventory_access WHERE auth0_sub='auth0|manager'")
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) SELECT 'auth0|manager', id FROM stores WHERE code='MK'")
            con.commit()
        refused = self.report([(self.box, 3)])
        self.assertEqual((refused.status_code, refused.get_json()['code']), (403, 'inventory_forbidden'))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM daily_sales WHERE date=?', (LIVE_DATE,)).fetchone()[0], 0)
        self.assertEqual(self.floor(self.box), 30)

    def test_a_set_with_no_set_size_takes_nothing_until_one_is_recorded(self):
        with closing(self.connect()) as con:
            bare = self._product(con, 'BARE-SET', 'Bare 整端', 'sealed_set', 'set')
            con.commit()
        stock = self.report([(bare, 1)]).get_json()['stock']
        self.assertEqual((stock['taken'], stock['short']), (0, [
            {'product_id': bare, 'sku': 'BARE-SET', 'name': 'Bare 整端', 'quantity': 1, 'reason': 'set_size_unknown'}]))
        self.assertEqual((self.floor(self.box), self.documents()), (30, []))
        self.assertEqual(list(self.notices()), [(bare, 'set_size_unknown')])
        with closing(self.connect()) as con:
            con.execute("""INSERT INTO product_conversions(source_product_id, target_product_id, output_per_input, version)
                           VALUES (?, ?, 10, 1)""", (bare, self.box))
            con.commit()
        self.assertEqual((self.retry()['taken'], self.floor(self.box), self.notices()), (10, 20, {}))

    def test_mismatches_wait_on_the_manager_home_until_checked(self):
        self.report([(self.item, 5)])
        sections = self.client.get('/api/today', query_string={'store_code': 'DT', 'business_date': LIVE_DATE},
                                   headers=self.headers('manager')).get_json()['sections']
        self.assertEqual([(row['type'], row['status'], row['label']) for row in sections['report_stock']['rows']],
                         [('report_stock', 'not_enough_on_floor', 'Bear 吊卡 × 2')])
        staff = self.client.get('/api/today', query_string={'store_code': 'DT', 'business_date': LIVE_DATE},
                                headers=self.headers('staff')).get_json()['sections']
        self.assertNotIn('report_stock', staff)
        notice = self.notices()[(self.item, 'not_enough_on_floor')]
        self.check(notice)
        again = self.client.post(f"/api/sales/report-stock/{notice['id']}/check", headers=self.headers('manager'))
        self.assertEqual((again.status_code, again.get_json()['code']), (409, 'notice_closed'))
        # Checked: the floor count already left those two out, so the same report never takes them.
        same = self.report([(self.item, 5)]).get_json()['stock']
        self.assertEqual((same['taken'], same['short'], self.notices(), self.floor(self.item)), (0, [], {}, 0))
        more = self.report([(self.item, 6)]).get_json()['stock']
        self.assertEqual([(row['quantity'], row['reason']) for row in more['short']], [(1, 'not_enough_on_floor')])
        self.assertEqual(self.notices()[(self.item, 'not_enough_on_floor')]['quantity'], 1)
        # Fewer sold: what was checked off goes first, since it never moved.
        self.report([(self.item, 3)])
        self.assertEqual((self.floor(self.item), self.notices()), (0, {}))
        self.report([(self.item, 1)])
        self.assertEqual(self.floor(self.item), 2)

    def test_a_floor_counted_the_same_day_is_flagged(self):
        def count(when):
            with closing(self.connect()) as con:
                count_id = con.execute(
                    """INSERT INTO inventory_counts(store_id, location_id, business_date, status, created_by, created_at)
                       VALUES (?, ?, ?, 'approved', 'auth0|staff', datetime('now', ?))""",
                    (self.dt, self.locations['floor'], LIVE_DATE, when)).lastrowid
                con.execute("""INSERT INTO inventory_count_lines(count_id, line_no, product_id, native_unit,
                                   expected_quantity, observed_quantity, captured_balance_version)
                               VALUES (?, 1, ?, 'box', 30, 30, 1)""", (count_id, self.box))
                con.commit()
        count('-1 hour')
        stock = self.report([(self.box, 3), (self.item, 1)]).get_json()['stock']
        self.assertEqual([(row['product_id'], row['quantity']) for row in stock['counted_same_day']], [(self.box, 3)])
        self.assertEqual(self.floor(self.box), 27)
        notice = self.notices()[(self.box, 'counted_same_day')]
        self.assertTrue(notice['counted_at'])
        self.check(notice)
        self.assertEqual(self.report([(self.box, 4)]).get_json()['stock']['counted_same_day'], [])
        count('+1 minute')
        self.assertEqual(self.report([(self.box, 6)]).get_json()['stock']['counted_same_day'][0]['quantity'], 2)
        self.assertEqual(self.notices()[(self.box, 'counted_same_day')]['quantity'], 2)
