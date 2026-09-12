from contextlib import closing

from test_receiving import ReceivingFixture


class RestockLifecycleTests(ReceivingFixture):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.back = con.execute(
                "SELECT id FROM inventory_locations WHERE store_id=? AND code='upstairs'",
                (self.store_id,),
            ).fetchone()[0]
            con.execute(
                """INSERT INTO inventory_balances
                   (product_id, location_id, disposition, quantity, version)
                   VALUES (?, ?, 'saleable', 10, 1)""",
                (self.product_id, self.back),
            )
            session = con.execute(
                """INSERT INTO restock_sessions(date, status, store_id)
                   VALUES ('2026-09-08', 'picking', ?)""", (self.store_id,)
            ).lastrowid
            con.execute(
                """INSERT INTO restock_items
                   (session_id, product_id, requested_qty, found_qty, pick_status)
                   VALUES (?, ?, 5, 4, 'found')""", (session, self.product_id)
            )
            con.commit()
            self.session_id = session

    def test_pick_four_receive_three_retains_one_in_transit(self):
        picked = self.client.post(
            f'/api/restock/session/{self.session_id}/pick',
            headers={**self.headers(), 'Idempotency-Key': 'restock-pick-1'},
            json={'expected_version': 1,
                  'lines': [{'line_no': 1, 'quantity': 4}]},
        )
        self.assertEqual(picked.status_code, 200, picked.get_json())
        received = self.client.post(
            f'/api/restock/session/{self.session_id}/receive',
            headers={**self.headers(), 'Idempotency-Key': 'restock-receive-1'},
            json={'expected_version': 2,
                  'lines': [{'line_no': 1, 'quantity': 3}]},
        )
        self.assertEqual(received.status_code, 200, received.get_json())
        detail = self.client.get(
            f'/api/restock/session/{self.session_id}', headers=self.headers()
        ).get_json()
        self.assertEqual(detail['delivery']['lines'][0]['outstanding_transit'], 1)
        self.assertEqual(detail['status'], 'picking')
        with closing(self.connect()) as con:
            rows = {
                (r['location_id'], r['disposition']): r['quantity']
                for r in con.execute(
                    "SELECT location_id, disposition, quantity FROM inventory_balances WHERE product_id=?",
                    (self.product_id,),
                )
            }
        self.assertEqual(rows[(self.back, 'saleable')], 6)
        self.assertEqual(rows[(self.back, 'transit')], 1)
        self.assertEqual(rows[(self.floor, 'saleable')], 3)

    def test_authoritative_restock_routes_require_store_access(self):
        with closing(self.connect()) as con:
            pending_id = con.execute(
                """INSERT INTO restock_sessions(date, status, store_id)
                   VALUES ('2026-09-08', 'pending', ?)""", (self.store_id,)
            ).lastrowid
            pending_item_id = con.execute(
                """INSERT INTO restock_items
                   (session_id, product_id, requested_qty)
                   VALUES (?, ?, 2)""", (pending_id, self.product_id)
            ).lastrowid
            picking_item_id = con.execute(
                'SELECT id FROM restock_items WHERE session_id=?',
                (self.session_id,),
            ).fetchone()[0]
            con.commit()

        headers = {'Authorization': 'Bearer staff:outsider'}
        requests = (
            ('get', '/api/restock/sessions/today?store_code=DT', None),
            ('post', '/api/restock/sessions', {'store_code': 'DT'}),
            ('delete', f'/api/restock/session/{pending_id}', None),
            ('get', '/api/restock/session/today?store_code=DT', None),
            ('get', f'/api/restock/session/{self.session_id}', None),
            ('post', '/api/restock/items', {
                'session_id': pending_id, 'product_id': self.product_id,
                'requested_qty': 3,
            }),
            ('delete', f'/api/restock/items/{pending_item_id}', None),
            ('post', f'/api/restock/session/{pending_id}/submit', None),
            ('get', f'/api/restock/session/{self.session_id}/picking-list', None),
            ('patch', f'/api/restock/items/{picking_item_id}/pick', {
                'pick_status': 'found', 'found_qty': 1,
            }),
            ('post', f'/api/restock/session/{self.session_id}/complete', None),
            ('post', f'/api/restock/session/{self.session_id}/pick', {
                'expected_version': 1,
                'lines': [{'line_no': 1, 'quantity': 1}],
            }),
        )
        for method, path, body in requests:
            with self.subTest(method=method, path=path):
                response = self.client.open(
                    path, method=method.upper(), headers=headers, json=body,
                )
                self.assertEqual(response.status_code, 403, response.get_json())
                self.assertEqual(response.get_json()['code'], 'inventory_forbidden')
