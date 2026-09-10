import sys
import unittest
from contextlib import closing
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from support import IsolatedApiCase


class IntegerValidationTests(unittest.TestCase):
    def test_accepts_json_integers_and_unsigned_decimal_strings(self):
        from validation import read_int

        for value, expected in ((0, 0), (12, 12), ('0', 0), ('12', 12)):
            with self.subTest(value=value):
                self.assertEqual(read_int(value, 'qty'), expected)

    def test_rejects_values_that_are_not_bounded_integers(self):
        from validation import read_int

        invalid = (True, False, 1.0, -1, '-1', '+1', '', None, [], {}, 2**63)
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'qty'):
                    read_int(value, 'qty')

    def test_enforces_positive_minimum(self):
        from validation import read_int

        with self.assertRaisesRegex(ValueError, 'qty'):
            read_int(0, 'qty', minimum=1)


class SalesRouteValidationTests(IsolatedApiCase):
    def test_negative_sale_leaves_all_rows_unchanged(self):
        before = self.snapshot(('daily_sales', 'stock', 'stock_transactions'))
        response = self.client.post(
            '/api/sales/upsert',
            headers=self.headers(),
            json={
                'product_id': self.product_id,
                'store_code': 'DT',
                'date': '2026-09-08',
                'qty_sold': -5,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'invalid_input')
        self.assertEqual(
            self.snapshot(('daily_sales', 'stock', 'stock_transactions')),
            before,
        )

    def test_fractional_sale_is_not_truncated(self):
        response = self.client.post(
            '/api/sales/upsert',
            headers=self.headers(),
            json={
                'product_id': self.product_id,
                'store_code': 'DT',
                'date': '2026-09-08',
                'qty_cash': 1.5,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'qty_cash')

    def test_present_empty_or_null_quantity_is_not_defaulted(self):
        for value in ('', None):
            with self.subTest(value=value):
                response = self.client.post(
                    '/api/sales/upsert',
                    headers=self.headers(),
                    json={
                        'product_id': self.product_id,
                        'store_code': 'DT',
                        'date': '2026-09-08',
                        'qty_cash': value,
                    },
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()['field'], 'qty_cash')

    def test_batch_with_negative_line_is_atomic(self):
        before = self.snapshot(('daily_sales',))
        response = self.client.post(
            '/api/sales/batch_upsert',
            headers=self.headers(),
            json={
                'store_code': 'DT',
                'items': [
                    {'product_id': self.product_id, 'qty_cash': 2},
                    {'product_id': self.product_id, 'qty_cash': -1},
                ],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['line'], 2)
        self.assertEqual(self.snapshot(('daily_sales',)), before)

    def test_invalid_business_date_is_rejected(self):
        response = self.client.post(
            '/api/sales/upsert',
            headers=self.headers(),
            json={
                'product_id': self.product_id,
                'store_code': 'DT',
                'date': '09/08/2026',
                'qty_cash': 1,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'date')

    def test_invalid_and_all_store_writes_change_nothing(self):
        before = self.snapshot(('daily_sales',))
        for store_code in ('MISSING', 'ALL'):
            with self.subTest(store_code=store_code):
                response = self.client.post(
                    '/api/sales/upsert',
                    headers=self.headers(),
                    json={
                        'product_id': self.product_id,
                        'store_code': store_code,
                        'date': '2026-09-08',
                        'qty_cash': 1,
                    },
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.snapshot(('daily_sales',)), before)

    def test_report_unit_multiplication_overflow_is_rejected(self):
        before = self.snapshot(('daily_sales', 'stock', 'stock_transactions'))
        response = self.client.post(
            '/api/sales/submit_daily_report',
            headers=self.headers(),
            json={
                'store_code': 'DT',
                'date': '2026-09-08',
                'items': [{
                    'product_id': self.product_id,
                    'section': 'stock_in',
                    'box_size': 2**62,
                    'num_boxes': 4,
                }],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'total_units')
        self.assertEqual(
            self.snapshot(('daily_sales', 'stock', 'stock_transactions')),
            before,
        )

    def test_report_sales_aggregation_overflow_is_rejected(self):
        with closing(self.connect()) as con:
            con.execute(
                """INSERT INTO daily_sales
                   (product_id, date, store, qty_pos, qty_sold)
                   VALUES (?, '2026-09-08', 'DT', ?, ?)""",
                (self.product_id, 2**63 - 1, 2**63 - 1),
            )
            con.commit()
        before = self.snapshot(('daily_sales',))

        response = self.client.post(
            '/api/sales/submit_daily_report',
            headers=self.headers(),
            json={
                'store_code': 'DT',
                'date': '2026-09-08',
                'mode': 'append',
                'items': [{
                    'product_id': self.product_id,
                    'section': 'pos',
                    'qty_pos': 1,
                }],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'invalid_input')
        self.assertEqual(self.snapshot(('daily_sales',)), before)

    def test_negative_report_stock_line_cannot_create_stock(self):
        before = self.snapshot(('daily_sales', 'stock', 'stock_transactions'))
        response = self.client.post(
            '/api/sales/submit_daily_report',
            headers=self.headers(),
            json={
                'store_code': 'DT',
                'date': '2026-09-08',
                'items': [{
                    'product_id': self.product_id,
                    'section': 'stock_out',
                    'qty': -3,
                }],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['line'], 1)
        self.assertEqual(
            self.snapshot(('daily_sales', 'stock', 'stock_transactions')),
            before,
        )


class StockRouteValidationTests(IsolatedApiCase):
    def test_missing_product_stock_move_is_not_a_server_error(self):
        before = self.snapshot(('stock', 'stock_transactions'))
        response = self.client.post(
            '/api/stock/restock_upstairs',
            headers=self.headers(),
            json={
                'product_id': 999999,
                'store_code': 'DT',
                'date': '2026-09-08',
                'qty': 1,
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['code'], 'not_found')
        self.assertEqual(self.snapshot(('stock', 'stock_transactions')), before)

    def test_fractional_stock_move_is_not_truncated(self):
        before = self.snapshot(('stock', 'stock_transactions'))
        response = self.client.post(
            '/api/stock/ru_dian',
            headers=self.headers(),
            json={
                'product_id': self.product_id,
                'store_code': 'DT',
                'date': '2026-09-08',
                'qty': 1.5,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'qty')
        self.assertEqual(self.snapshot(('stock', 'stock_transactions')), before)

    def test_batch_stock_operation_rejects_all_lines_atomically(self):
        before = self.snapshot(('stock', 'stock_transactions'))
        response = self.client.post(
            '/api/stock/batch_operation',
            headers=self.headers(),
            json={
                'store_code': 'DT',
                'date': '2026-09-08',
                'operation': 'restock_upstairs',
                'items': [
                    {'product_id': self.product_id, 'qty': 2},
                    {'product_id': self.product_id, 'qty': -1},
                ],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['line'], 2)
        self.assertEqual(self.snapshot(('stock', 'stock_transactions')), before)

    def test_fractional_restock_request_is_rejected(self):
        with closing(self.connect()) as con:
            cursor = con.execute(
                """INSERT INTO restock_sessions (date, status, store_id)
                   VALUES ('2026-09-08', 'pending', ?)""",
                (self.store_id,),
            )
            session_id = cursor.lastrowid
            con.commit()
        response = self.client.post(
            '/api/restock/items',
            headers=self.headers(),
            json={
                'session_id': session_id,
                'product_id': self.product_id,
                'requested_qty': 1.5,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'requested_qty')

    def test_missing_product_restock_item_is_not_a_server_error(self):
        with closing(self.connect()) as con:
            cursor = con.execute(
                """INSERT INTO restock_sessions (date, status, store_id)
                   VALUES ('2026-09-08', 'pending', ?)""",
                (self.store_id,),
            )
            session_id = cursor.lastrowid
            con.commit()
        response = self.client.post(
            '/api/restock/items',
            headers=self.headers(),
            json={
                'session_id': session_id,
                'product_id': 999999,
                'requested_qty': 1,
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['code'], 'not_found')


if __name__ == '__main__':
    unittest.main()
