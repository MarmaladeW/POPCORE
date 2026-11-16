"""A floor that runs out of loose boxes opens a sealed set by itself (owner, 2026-11-15).

Angry Molly at DT, Nov 14: the store closed Nov 13 with 3 sealed sets upstairs and no loose box. One sold, so
staff opened a set, but the report had no 出库入店 line, and POPCORE flagged 1 short although the shelf was fine
(2 sets + 7). Now the paste opens as few of the box's own sets as cover the sale, after the day's own lines
moved, says so per product before it saves, and a re-paste opens nothing more for the same sale."""
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

DAY = '2026-11-14'
ADMIN = {'sub': 'auth0|admin', 'https://popcore/role': 'admin'}


class ReportOpenSetTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.dt = con.execute("SELECT id FROM stores WHERE code='DT'").fetchone()[0]
            self.at = {row[0]: row[1] for row in con.execute(
                'SELECT code, id FROM inventory_locations WHERE store_id=?', (self.dt,))}
            self.molly = self._product(con, 'PM0767', 'Angry Molly', 'random_box', 'box')
            self.molly_set = self._set(con, self.molly, 'PM0767', 8)
            # A box whose sets sit both on the floor and upstairs.
            self.echo = self._product(con, 'ECHO', 'Echo', 'random_box', 'box')
            self.echo_set = self._set(con, self.echo, 'ECHO', 12)
            # A box with no sealed set at all.
            self.plain = self._product(con, 'PLAIN', 'Plain', 'random_box', 'box')
            # A confirmed design (子项) of Angry Molly's series, never counted anywhere.
            series = con.execute('INSERT INTO product_series(name, product_id) VALUES (?, ?)',
                                 ('Angry Molly', self.molly)).lastrowid
            con.execute('UPDATE products SET series_id=? WHERE id=?', (series, self.molly))
            self.design = con.execute(
                """INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, identity_status, series_id,
                                        design_name)
                   VALUES ('PM0767-D1', 'Angry Molly Red', 'Angry Molly Red', 'confirmed_design', 'piece', 'verified',
                           ?, 'Red')""", (series,)).lastrowid
            for subject in ('auth0|manager', 'auth0|admin'):
                con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)', (subject, self.dt))
            for code, product, form, quantity in (
                    ('floor', self.molly, 'random_box', 0), ('upstairs', self.molly_set, 'sealed_set', 3),
                    ('floor', self.echo, 'random_box', 2), ('floor', self.echo_set, 'sealed_set', 1),
                    ('upstairs', self.echo_set, 'sealed_set', 4), ('floor', self.plain, 'random_box', 0),
                    ('floor', self.design, 'confirmed_design', 0)):
                con.execute("""INSERT INTO opening_counts(location_id, product_id, stock_form, quantity, counted_by)
                               VALUES (?, ?, ?, ?, 'auth0|admin')""", (self.at[code], product, form, quantity))
            con.commit()
        with redirect_stdout(io.StringIO()):
            _opening_cli(['intake', '--db', db.DB_PATH, '--from-app', '--store', 'DT',
                          '--empty-location', 'DT-claw', '--empty-location', 'DT-display',
                          '--business-date', DAY, '--actor-sub', 'auth0|admin',
                          '--apply', '--backup', str(Path(self.tempdir.name) / 'before-dt.db')])

    @staticmethod
    def _product(con, sku, name, form, unit):
        return con.execute(
            """INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, identity_status)
               VALUES (?, ?, ?, ?, ?, 'verified')""", (sku, name, name, form, unit)).lastrowid

    def _set(self, con, box, sku, per):
        whole = self._product(con, f'{sku}-SET', f'{sku} 整端', 'sealed_set', 'set')
        con.execute("""INSERT INTO product_conversions(source_product_id, target_product_id, output_per_input, version)
                       VALUES (?, ?, ?, 1)""", (whole, box, per))
        return whole

    # ── the manager's paste ──────────────────────────────────────────────────

    def body(self, sales=(), lines=()):
        items = [{'product_id': product, 'section': 'pos', 'qty_pos': quantity, 'qty_cash': 0, 'raw_name': str(product)}
                 for product, quantity in sales]
        items += lines
        return {'date': DAY, 'store_code': 'DT', 'classification': 'summary_only', 'items': items,
                'stock_mode': 'live'}

    def post(self, body, **extra):
        return self.client.post('/api/sales/submit_daily_report', json={**body, **extra}, headers=self.headers('manager'))

    def preview(self, body):
        response = self.post(body, dry_run=True)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def save(self, body):
        """Preview, then save with what the manager confirmed."""
        preview = self.preview(body)
        response = self.post(body, stock_digest=preview['stock_digest'])
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['stock']

    def balance(self, product, code):
        with closing(self.connect()) as con:
            row = con.execute("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                                 AND disposition='saleable'""", (product, self.at[code])).fetchone()
            return row[0] if row else 0

    def notices(self):
        response = self.client.get('/api/sales/report-stock', headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        return {(row['product_id'], row['reason']): row['quantity'] for row in response.get_json()['notices']}

    def documents(self):
        with closing(self.connect()) as con:
            return con.execute("SELECT COUNT(*) FROM inventory_documents WHERE source_type='daily_report'").fetchone()[0]

    def receive(self, product, code, quantity, unit):
        with closing(self.connect()) as con:
            version = con.execute("""SELECT COALESCE(MAX(version), 0) FROM inventory_balances WHERE product_id=?
                                     AND location_id=? AND disposition='saleable'""", (product, self.at[code])).fetchone()[0]
            post_inventory(con, {'kind': 'receipt', 'business_date': DAY, 'lines': [
                {'product_id': product, 'quantity': quantity, 'unit': unit, 'to_location_id': self.at[code],
                 'to_disposition': 'saleable', 'expected_versions': {'to': version}}]},
                actor=ADMIN, request_key=f'arrived:{product}:{code}:{quantity}')

    # ── the cases ────────────────────────────────────────────────────────────

    def test_angry_molly_one_sold_opens_one_set_and_nothing_is_short(self):
        body = self.body([(self.molly, 1)])
        preview = self.preview(body)
        # Nothing saved yet, and the manager must confirm the opening.
        self.assertTrue(preview['needs_confirmation'])
        self.assertEqual((self.balance(self.molly_set, 'upstairs'), self.balance(self.molly, 'floor')), (3, 0))
        opened = preview['stock']['sets_opened']
        self.assertEqual([(row['product_id'], row['location'], row['sets'], row['boxes']) for row in opened],
                         [(self.molly, 'upstairs', 1, 8)])
        self.assertIn('开了 1 端 Opened 1 set from 楼上 Upstairs (日报没写 出库入店 no 出库入店 line)', opened[0]['text'])
        self.assertEqual(preview['stock']['short'], [])
        # Saving without the confirmation is refused.
        refused = self.post(body)
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.get_json()['code'], 'stock_confirmation_required')

        stock = self.save(body)
        self.assertEqual((stock['taken'], stock['short']), (1, []))
        self.assertEqual(len(stock['sets_opened']), 1)
        self.assertEqual((self.balance(self.molly_set, 'upstairs'), self.balance(self.molly, 'floor')), (2, 7))
        self.assertEqual(self.notices(), {})
        with closing(self.connect()) as con:
            reason = con.execute("""SELECT reason FROM inventory_documents WHERE kind='correction'
                                    AND source_type='daily_report'""").fetchone()[0]
        self.assertIn('自动开端 Set opened', reason)

    def test_a_repaste_opens_nothing_more_and_fewer_sold_counts_the_set_whole_again(self):
        self.save(self.body([(self.molly, 1)]))
        documents = self.documents()
        # The same report again: nothing moves, nothing to confirm.
        again = self.preview(self.body([(self.molly, 1)]))
        self.assertFalse(again['needs_confirmation'])
        self.assertEqual(again['stock']['sets_opened'], [])
        response = self.post(self.body([(self.molly, 1)]))
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.documents(), documents)
        self.assertEqual((self.balance(self.molly_set, 'upstairs'), self.balance(self.molly, 'floor')), (2, 7))
        # One more sold: the opened set's loose boxes cover it.
        stock = self.save(self.body([(self.molly, 2)]))
        self.assertEqual(stock['sets_opened'], [])
        self.assertEqual((self.balance(self.molly_set, 'upstairs'), self.balance(self.molly, 'floor')), (2, 6))
        # The sales were a mistake: the boxes come back and the set counts whole again upstairs.
        stock = self.save(self.body())
        self.assertEqual([(row['location'], row['sets']) for row in stock['sets_opened']], [('upstairs', -1)])
        self.assertEqual((self.balance(self.molly_set, 'upstairs'), self.balance(self.molly, 'floor')), (3, 0))
        # And selling one again opens it again.
        self.save(self.body([(self.molly, 1)]))
        self.assertEqual((self.balance(self.molly_set, 'upstairs'), self.balance(self.molly, 'floor')), (2, 7))
        self.assertEqual(self.notices(), {})

    def test_never_more_sets_than_needed_and_what_sets_cannot_cover_stays_short(self):
        # 3 sets of 8 cover 24 of 30 sold.
        stock = self.save(self.body([(self.molly, 30)]))
        self.assertEqual([(row['sets'], row['boxes']) for row in stock['sets_opened']], [(3, 24)])
        self.assertEqual((self.balance(self.molly_set, 'upstairs'), self.balance(self.molly, 'floor')), (0, 0))
        self.assertEqual(self.notices(), {(self.molly, 'not_enough_on_floor'): 6})

    def test_loose_boxes_first_then_a_set_on_the_floor_then_upstairs(self):
        # 2 loose; 13 more need 2 sets of 12: the one on the floor, then one from upstairs.
        stock = self.save(self.body([(self.echo, 15)]))
        self.assertEqual([(row['location'], row['sets']) for row in stock['sets_opened']], [('floor', 1), ('upstairs', 1)])
        self.assertEqual((self.balance(self.echo_set, 'floor'), self.balance(self.echo_set, 'upstairs'),
                          self.balance(self.echo, 'floor')), (0, 3, 11))
        self.assertEqual(self.notices(), {})

    def test_the_reports_own_refill_line_comes_first(self):
        refill = {'product_id': self.molly, 'section': 'refill', 'box_size': 8, 'num_boxes': 1, 'loose_qty': 0,
                  'raw_name': 'Angry Molly', 'notes': ''}
        stock = self.save(self.body([(self.molly, 1)], [refill]))
        self.assertEqual(stock['sets_opened'], [])
        self.assertEqual((self.balance(self.molly_set, 'upstairs'), self.balance(self.molly, 'floor')), (2, 7))

    def test_a_design_sold_with_none_counted_never_opens_a_set(self):
        stock = self.save(self.body([(self.design, 1)]))
        self.assertEqual(stock['sets_opened'], [])
        self.assertEqual(self.balance(self.molly_set, 'upstairs'), 3)
        self.assertEqual(self.notices(), {(self.design, 'not_enough_on_floor'): 1})

    def test_no_set_to_open_keeps_the_notice_and_take_off_again_opens_one_that_arrived(self):
        response = self.post(self.body([(self.plain, 1)]))
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.notices(), {(self.plain, 'not_enough_on_floor'): 1})
        # A sealed set of it is recorded upstairs afterwards; 再扣一次 Take off again opens it by the same rule.
        with closing(self.connect()) as con:
            whole = self._set(con, self.plain, 'PLAIN', 6)
            con.commit()
        self.receive(whole, 'upstairs', 1, 'set')
        retry = self.client.post('/api/sales/report-stock/retry', json={'date': DAY, 'store_code': 'DT'},
                                 headers=self.headers('manager'))
        self.assertEqual(retry.status_code, 200, retry.get_json())
        stock = retry.get_json()['stock']
        self.assertEqual([(row['location'], row['sets']) for row in stock['sets_opened']], [('upstairs', 1)])
        self.assertEqual((self.balance(whole, 'upstairs'), self.balance(self.plain, 'floor')), (0, 5))
        self.assertEqual(self.notices(), {})

    def test_a_line_taking_boxes_off_the_floor_opens_a_set_too(self):
        out = {'product_id': self.molly, 'section': 'stock_out', 'qty': 2, 'raw_name': 'Angry Molly', 'notes': ''}
        stock = self.save(self.body([], [out]))
        self.assertEqual([(row['location'], row['sets']) for row in stock['sets_opened']], [('upstairs', 1)])
        self.assertEqual([line['short'] for line in stock['lines']], [0])
        self.assertEqual((self.balance(self.molly_set, 'upstairs'), self.balance(self.molly, 'floor')), (2, 6))
        self.assertEqual(self.notices(), {})
        # The line is taken off the report: its boxes come back and the set counts whole again.
        stock = self.save(self.body([(self.echo, 1)]))
        self.assertEqual([(row['location'], row['sets']) for row in stock['sets_opened']], [('upstairs', -1)])
        self.assertEqual((self.balance(self.molly_set, 'upstairs'), self.balance(self.molly, 'floor')), (3, 0))


if __name__ == '__main__':
    import unittest
    unittest.main()
