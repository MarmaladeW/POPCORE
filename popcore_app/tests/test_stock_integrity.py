import sys
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from support import IsolatedApiCase


class StockIntegrityTests(IsolatedApiCase):
    def test_concurrent_direct_moves_cannot_overdraw_stock(self):
        def move():
            with self.client.application.test_client() as client:
                return client.post(
                    '/api/stock/ru_dian',
                    headers=self.headers(),
                    json={
                        'product_id': self.product_id,
                        'store_code': 'DT',
                        'date': '2026-09-08',
                        'qty': 7,
                    },
                ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _: move(), range(2)))

        self.assertEqual(sorted(statuses), [200, 409])
        with closing(self.connect()) as con:
            stock = con.execute(
                'SELECT upstairs_qty, instore_qty FROM stock WHERE product_id=? AND store_id=?',
                (self.product_id, self.store_id),
            ).fetchone()
            transactions = con.execute(
                "SELECT COUNT(*) FROM stock_transactions WHERE txn_type='ru_dian'"
            ).fetchone()[0]
        self.assertEqual(tuple(stock), (3, 9))
        self.assertEqual(transactions, 1)

    def test_stock_additions_cannot_overflow_sqlite_integer(self):
        with closing(self.connect()) as con:
            con.execute(
                'UPDATE stock SET upstairs_qty=? WHERE product_id=? AND store_id=?',
                (2**63 - 1, self.product_id, self.store_id),
            )
            con.commit()
        before = self.snapshot(('stock', 'stock_transactions'))

        for path, payload in (
            ('/api/stock/restock_upstairs', {
                'product_id': self.product_id, 'store_code': 'DT',
                'date': '2026-09-08', 'qty': 1,
            }),
            ('/api/stock/batch_operation', {
                'store_code': 'DT', 'date': '2026-09-08',
                'operation': 'restock_upstairs',
                'items': [{'product_id': self.product_id, 'qty': 1}],
            }),
        ):
            with self.subTest(path=path):
                response = self.client.post(path, headers=self.headers(), json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()['code'], 'invalid_input')
                self.assertEqual(self.snapshot(('stock', 'stock_transactions')), before)

    def test_stock_transfer_destinations_cannot_overflow(self):
        with closing(self.connect()) as con:
            con.execute(
                '''UPDATE stock SET upstairs_qty=10, instore_qty=?
                   WHERE product_id=? AND store_id=?''',
                (2**63 - 1, self.product_id, self.store_id),
            )
            cursor = con.execute(
                """INSERT INTO restock_sessions (date, status, store_id)
                   VALUES ('2026-09-08', 'picking', ?)""",
                (self.store_id,),
            )
            session_id = cursor.lastrowid
            con.execute(
                """INSERT INTO restock_items
                   (session_id, product_id, requested_qty, found_qty, pick_status)
                   VALUES (?, ?, 1, 1, 'found')""",
                (session_id, self.product_id),
            )
            con.commit()
        tables = ('stock', 'stock_transactions', 'restock_sessions', 'stock_movements')
        before = self.snapshot(tables)

        report = self.client.post(
            '/api/sales/submit_daily_report',
            headers=self.headers(),
            json={
                'store_code': 'DT', 'date': '2026-09-08',
                'items': [{
                    'product_id': self.product_id, 'section': 'stock_in',
                    'box_size': 1, 'num_boxes': 1,
                }],
            },
        )
        self.assertEqual(report.status_code, 400)
        self.assertEqual(report.get_json()['code'], 'invalid_input')
        self.assertEqual(self.snapshot(tables), before)

        completion = self.client.post(
            f'/api/restock/session/{session_id}/complete', headers=self.headers()
        )
        self.assertEqual(completion.status_code, 400)
        self.assertEqual(completion.get_json()['code'], 'invalid_input')
        self.assertEqual(self.snapshot(tables), before)

    def _completed_session(self):
        with closing(self.connect()) as con:
            cursor = con.execute(
                """INSERT INTO restock_sessions
                   (date, status, store_id, completed_at)
                   VALUES ('2026-09-08', 'completed', ?, datetime('now'))""",
                (self.store_id,),
            )
            session_id = cursor.lastrowid
            con.execute(
                """INSERT INTO restock_items
                   (session_id, product_id, requested_qty, found_qty, pick_status)
                   VALUES (?, ?, 5, 5, 'found')""",
                (session_id, self.product_id),
            )
            con.execute(
                """INSERT INTO stock_movements
                   (product_id, session_id, movement_type, qty_change,
                    location, store_id)
                   VALUES (?, ?, 'restock_in', 5, 'store', ?)""",
                (self.product_id, session_id, self.store_id),
            )
            con.execute(
                """INSERT INTO stock_transactions
                   (product_id, txn_type, qty, location, date, notes, store_id)
                   VALUES (?, 'ru_dian', 5, 'upstairs->instore',
                           '2026-09-08', ?, ?)""",
                (self.product_id, f'restock session#{session_id}', self.store_id),
            )
            con.execute(
                """UPDATE stock SET upstairs_qty=5, instore_qty=0
                   WHERE product_id=? AND store_id=?""",
                (self.product_id, self.store_id),
            )
            con.commit()
        return session_id

    def test_completed_restock_deletion_preserves_stock_and_history(self):
        session_id = self._completed_session()
        tables = (
            'stock', 'restock_sessions', 'restock_items',
            'stock_movements', 'stock_transactions',
        )
        before = self.snapshot(tables)

        response = self.client.delete(
            f'/api/restock/session/{session_id}', headers=self.headers()
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()['code'], 'completed_restock_retained'
        )
        self.assertEqual(self.snapshot(tables), before)

    def test_completed_restock_item_cannot_be_deleted(self):
        session_id = self._completed_session()
        with closing(self.connect()) as con:
            item_id = con.execute(
                'SELECT id FROM restock_items WHERE session_id=?', (session_id,)
            ).fetchone()[0]
        tables = ('restock_sessions', 'restock_items', 'stock_movements')
        before = self.snapshot(tables)

        response = self.client.delete(
            f'/api/restock/items/{item_id}', headers=self.headers()
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(tables), before)

    def test_restock_completion_moves_exactly_once_and_records_store(self):
        with closing(self.connect()) as con:
            cursor = con.execute(
                """INSERT INTO restock_sessions (date, status, store_id)
                   VALUES ('2026-09-08', 'picking', ?)""",
                (self.store_id,),
            )
            session_id = cursor.lastrowid
            con.execute(
                """INSERT INTO restock_items
                   (session_id, product_id, requested_qty, found_qty, pick_status)
                   VALUES (?, ?, 5, 5, 'found')""",
                (session_id, self.product_id),
            )
            con.commit()

        response = self.client.post(
            f'/api/restock/session/{session_id}/complete', headers=self.headers()
        )
        self.assertEqual(response.status_code, 200)
        with closing(self.connect()) as con:
            stock = con.execute(
                'SELECT upstairs_qty, instore_qty FROM stock WHERE product_id=? AND store_id=?',
                (self.product_id, self.store_id),
            ).fetchone()
            movement_stores = con.execute(
                'SELECT store_id FROM stock_movements WHERE session_id=?',
                (session_id,),
            ).fetchall()
        self.assertEqual(tuple(stock), (5, 7))
        self.assertEqual([row[0] for row in movement_stores], [self.store_id, self.store_id])

        before = self.snapshot(('stock', 'stock_movements', 'stock_transactions'))
        duplicate = self.client.post(
            f'/api/restock/session/{session_id}/complete', headers=self.headers()
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(
            self.snapshot(('stock', 'stock_movements', 'stock_transactions')), before
        )

    def test_concurrent_restock_completion_moves_stock_once(self):
        with closing(self.connect()) as con:
            cursor = con.execute(
                """INSERT INTO restock_sessions (date, status, store_id)
                   VALUES ('2026-09-08', 'picking', ?)""",
                (self.store_id,),
            )
            session_id = cursor.lastrowid
            con.execute(
                """INSERT INTO restock_items
                   (session_id, product_id, requested_qty, found_qty, pick_status)
                   VALUES (?, ?, 5, 5, 'found')""",
                (session_id, self.product_id),
            )
            con.commit()

        def complete():
            with self.client.application.test_client() as client:
                return client.post(
                    f'/api/restock/session/{session_id}/complete',
                    headers=self.headers(),
                ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _: complete(), range(2)))

        self.assertEqual(sorted(statuses), [200, 400])
        with closing(self.connect()) as con:
            stock = con.execute(
                'SELECT upstairs_qty, instore_qty FROM stock WHERE product_id=? AND store_id=?',
                (self.product_id, self.store_id),
            ).fetchone()
            movement_count = con.execute(
                'SELECT COUNT(*) FROM stock_movements WHERE session_id=?',
                (session_id,),
            ).fetchone()[0]
        self.assertEqual(tuple(stock), (5, 7))
        self.assertEqual(movement_count, 2)

    def test_unposted_restock_states_can_be_deleted(self):
        for status in ('pending', 'submitted', 'picking'):
            with self.subTest(status=status), closing(self.connect()) as con:
                cursor = con.execute(
                    'INSERT INTO restock_sessions (date, status, store_id) VALUES (?, ?, ?)',
                    ('2026-09-08', status, self.store_id),
                )
                session_id = cursor.lastrowid
                con.execute(
                    '''INSERT INTO restock_items
                       (session_id, product_id, requested_qty)
                       VALUES (?, ?, 1)''',
                    (session_id, self.product_id),
                )
                con.commit()
            response = self.client.delete(
                f'/api/restock/session/{session_id}', headers=self.headers()
            )
            self.assertEqual(response.status_code, 200)
            with closing(self.connect()) as con:
                self.assertIsNone(con.execute(
                    'SELECT 1 FROM restock_sessions WHERE id=?', (session_id,)
                ).fetchone())

    def test_report_stock_out_shortage_changes_nothing(self):
        tables = ('daily_sales', 'stock', 'stock_transactions')
        before = self.snapshot(tables)
        response = self.client.post(
            '/api/sales/submit_daily_report',
            headers=self.headers(),
            json={
                'store_code': 'DT',
                'date': '2026-09-08',
                'items': [{
                    'product_id': self.product_id,
                    'section': 'stock_out',
                    'qty': 5,
                }],
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'insufficient_stock')
        self.assertEqual(self.snapshot(tables), before)

    def test_report_with_stock_history_cannot_be_replaced(self):
        with closing(self.connect()) as con:
            con.execute(
                """INSERT INTO daily_sales
                   (product_id, date, store, qty_cash, qty_sold)
                   VALUES (?, '2026-09-08', 'DT', 1, 1)""",
                (self.product_id,),
            )
            con.execute(
                """INSERT INTO stock_transactions
                   (product_id, txn_type, qty, location, date, store_id)
                   VALUES (?, 'report_stock_out', 1, 'instore->out',
                           '2026-09-08', ?)""",
                (self.product_id, self.store_id),
            )
            con.commit()
        tables = ('daily_sales', 'stock', 'stock_transactions')
        before = self.snapshot(tables)

        response = self.client.post(
            '/api/sales/submit_daily_report',
            headers=self.headers(),
            json={'store_code': 'DT', 'date': '2026-09-08', 'items': []},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'reconciliation_required')
        self.assertEqual(self.snapshot(tables), before)

    def test_clear_day_cannot_bypass_report_history_guard(self):
        with closing(self.connect()) as con:
            con.execute(
                """INSERT INTO daily_sales
                   (product_id, date, store, qty_cash, qty_sold)
                   VALUES (?, '2026-09-08', 'DT', 1, 1)""",
                (self.product_id,),
            )
            con.execute(
                """INSERT INTO stock_transactions
                   (product_id, txn_type, qty, location, date, store_id)
                   VALUES (?, 'report_stock_out', 1, 'instore->out',
                           '2026-09-08', ?)""",
                (self.product_id, self.store_id),
            )
            con.commit()
        before = self.snapshot(('daily_sales', 'stock_transactions'))

        response = self.client.delete(
            '/api/sales/clear_day',
            headers=self.headers('manager'),
            query_string={'store_code': 'DT', 'date': '2026-09-08'},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'reconciliation_required')
        self.assertEqual(
            self.snapshot(('daily_sales', 'stock_transactions')), before
        )

    def test_nonempty_stock_row_cannot_be_deleted(self):
        before = self.snapshot(('stock', 'stock_transactions'))
        response = self.client.delete(
            '/api/stock/rows',
            headers=self.headers('manager'),
            json={'store_code': 'DT', 'product_ids': [self.product_id]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'stock_row_retained')
        self.assertEqual(self.snapshot(('stock', 'stock_transactions')), before)

    def test_product_with_stock_history_cannot_be_deleted(self):
        before = self.snapshot(('products', 'stock'))
        response = self.client.post(
            '/api/products/bulk_delete',
            headers=self.headers('manager'),
            json=[self.product_id],
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'product_history_retained')
        self.assertEqual(self.snapshot(('products', 'stock')), before)

    def test_restock_shortage_does_not_partially_complete(self):
        with closing(self.connect()) as con:
            cursor = con.execute(
                """INSERT INTO restock_sessions (date, status, store_id)
                   VALUES ('2026-09-08', 'picking', ?)""",
                (self.store_id,),
            )
            session_id = cursor.lastrowid
            con.execute(
                """INSERT INTO restock_items
                   (session_id, product_id, requested_qty, found_qty, pick_status)
                   VALUES (?, ?, 15, 15, 'found')""",
                (session_id, self.product_id),
            )
            con.commit()
        tables = (
            'stock', 'restock_sessions', 'restock_items',
            'stock_movements', 'stock_transactions',
        )
        before = self.snapshot(tables)

        response = self.client.post(
            f'/api/restock/session/{session_id}/complete',
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'insufficient_stock')
        self.assertEqual(self.snapshot(tables), before)

    def test_batch_shortage_rolls_back_every_line(self):
        tables = ('stock', 'stock_transactions')
        before = self.snapshot(tables)
        response = self.client.post(
            '/api/stock/batch_operation',
            headers=self.headers(),
            json={
                'store_code': 'DT',
                'date': '2026-09-08',
                'operation': 'ru_dian',
                'items': [
                    {'product_id': self.product_id, 'qty': 5},
                    {'product_id': self.product_id, 'qty': 20},
                ],
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'insufficient_stock')
        self.assertEqual(self.snapshot(tables), before)

    def test_sales_row_with_report_stock_history_cannot_be_deleted(self):
        with closing(self.connect()) as con:
            cursor = con.execute(
                """INSERT INTO daily_sales
                   (product_id, date, store, qty_cash, qty_sold)
                   VALUES (?, '2026-09-08', 'DT', 1, 1)""",
                (self.product_id,),
            )
            record_id = cursor.lastrowid
            con.execute(
                """INSERT INTO stock_transactions
                   (product_id, txn_type, qty, location, date, store_id)
                   VALUES (?, 'report_stock_out', 1, 'instore->out',
                           '2026-09-08', ?)""",
                (self.product_id, self.store_id),
            )
            con.commit()
        before = self.snapshot(('daily_sales', 'stock_transactions'))

        response = self.client.delete(
            f'/api/sales/record/{record_id}', headers=self.headers('manager')
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'reconciliation_required')
        self.assertEqual(
            self.snapshot(('daily_sales', 'stock_transactions')), before
        )
