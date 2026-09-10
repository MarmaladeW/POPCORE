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
