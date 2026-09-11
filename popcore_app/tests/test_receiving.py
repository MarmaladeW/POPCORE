from contextlib import closing

from support import IsolatedApiCase


class ReceivingFixture(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            con.execute(
                """UPDATE products SET stock_form='ordinary', stock_unit='piece',
                          identity_status='verified' WHERE id=?""", (self.product_id,)
            )
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|staff', ?)",
                (self.store_id,),
            )
            con.execute("UPDATE inventory_mode SET mode='authoritative' WHERE id=1")
            con.execute(
                "UPDATE inventory_scope_state SET opening_verified=1 WHERE store_id=?",
                (self.store_id,),
            )
            self.floor = con.execute(
                "SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'",
                (self.store_id,),
            ).fetchone()[0]
            con.commit()


class ReceivingTests(ReceivingFixture):

    def test_cancelled_draft_is_idempotent_and_posts_no_stock(self):
        draft = self.client.post('/api/goods/receipts', headers={
            **self.headers(), 'Idempotency-Key': 'receipt-cancel-create'
        }, json={
            'store_id': self.store_id,
            'destination_location_id': self.floor,
            'business_date': '2026-09-08',
            'lines': [{
                'product_id': self.product_id, 'unit': 'piece',
                'saleable_quantity': 2,
            }],
        }).get_json()
        first = self.client.post(
            f"/api/goods/receipts/{draft['id']}/cancel",
            headers={**self.headers(), 'Idempotency-Key': 'receipt-cancel'},
            json={'expected_version': draft['version']},
        )
        replay = self.client.post(
            f"/api/goods/receipts/{draft['id']}/cancel",
            headers={**self.headers(), 'Idempotency-Key': 'receipt-cancel'},
            json={'expected_version': draft['version']},
        )
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(replay.get_json(), first.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute(
                'SELECT COUNT(*) FROM inventory_documents'
            ).fetchone()[0], 0)

    def test_draft_update_requires_current_version_and_replaces_lines(self):
        body = {
            'store_id': self.store_id,
            'destination_location_id': self.floor,
            'business_date': '2026-09-08',
            'lines': [{
                'product_id': self.product_id, 'unit': 'piece',
                'saleable_quantity': 1,
            }],
        }
        draft = self.client.post('/api/goods/receipts', headers={
            **self.headers(), 'Idempotency-Key': 'receipt-update-create'
        }, json=body).get_json()
        updated = self.client.patch(
            f"/api/goods/receipts/{draft['id']}",
            headers={**self.headers(), 'Idempotency-Key': 'receipt-update'},
            json={**body, 'expected_version': 1, 'shipment_reference': 'SHIP-2',
                  'lines': [{**body['lines'][0], 'saleable_quantity': 3}]},
        )
        stale = self.client.patch(
            f"/api/goods/receipts/{draft['id']}",
            headers={**self.headers(), 'Idempotency-Key': 'receipt-update-stale'},
            json={**body, 'expected_version': 1},
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        self.assertEqual(updated.get_json()['version'], 2)
        self.assertEqual(stale.status_code, 409, stale.get_json())
        detail = self.client.get(
            f"/api/goods/receipts/{draft['id']}", headers=self.headers()
        ).get_json()
        self.assertEqual(detail['shipment_reference'], 'SHIP-2')
        self.assertEqual(detail['lines'][0]['saleable_quantity'], 3)

    def test_partial_receipt_posts_actual_dispositions_once(self):
        draft = self.client.post('/api/goods/receipts', headers={
            **self.headers(), 'Idempotency-Key': 'receipt-create-1'
        }, json={
            'store_id': self.store_id,
            'destination_location_id': self.floor,
            'business_date': '2026-09-08',
            'shipment_reference': 'SHIP-1',
            'lines': [{
                'product_id': self.product_id, 'unit': 'piece',
                'expected_quantity': 10, 'saleable_quantity': 7,
                'damaged_quantity': 1, 'hold_quantity': 0,
                'discrepancy_note': 'Two missing',
            }],
        })
        self.assertEqual(draft.status_code, 201, draft.get_json())
        receipt_id = draft.get_json()['id']
        posted = self.client.post(
            f'/api/goods/receipts/{receipt_id}/post',
            headers={**self.headers(), 'Idempotency-Key': 'receipt-post-1'},
            json={'expected_version': 1},
        )
        self.assertEqual(posted.status_code, 200, posted.get_json())
        replay = self.client.post(
            f'/api/goods/receipts/{receipt_id}/post',
            headers={**self.headers(), 'Idempotency-Key': 'receipt-post-1'},
            json={'expected_version': 1},
        )
        self.assertEqual(replay.get_json(), posted.get_json())
        with closing(self.connect()) as con:
            balances = {
                row['disposition']: row['quantity'] for row in con.execute(
                    """SELECT disposition, quantity FROM inventory_balances
                       WHERE product_id=? AND location_id=?""",
                    (self.product_id, self.floor),
                )
            }
        self.assertEqual(balances, {'damaged': 1, 'saleable': 7})

    def test_scanner_preserves_code_and_requires_explicit_unknown_resolution(self):
        with closing(self.connect()) as con:
            con.execute(
                """INSERT INTO product_barcodes
                   (code, product_id, code_kind, input_unit, quantity_per_scan)
                   VALUES ('00123', ?, 'internal', 'piece', 1)""",
                (self.product_id,),
            )
            con.commit()
        matched = self.client.get('/api/goods/barcodes/resolve',
                                  query_string={'code': '00123', 'purpose': 'receive'},
                                  headers=self.headers())
        unknown = self.client.get('/api/goods/barcodes/resolve',
                                  query_string={'code': '00999', 'purpose': 'receive'},
                                  headers=self.headers())
        self.assertEqual(matched.status_code, 200)
        self.assertEqual(matched.get_json()['status'], 'exact')
        self.assertEqual(matched.get_json()['candidates'][0]['quantity_per_scan'], 1)
        self.assertEqual(unknown.get_json(), {'status': 'unknown', 'candidates': []})
