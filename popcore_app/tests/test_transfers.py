from contextlib import closing

from test_receiving import ReceivingFixture


class TransferTests(ReceivingFixture):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.mk_id = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            self.source = con.execute(
                "SELECT id FROM inventory_locations WHERE store_id=? AND code='upstairs'",
                (self.store_id,),
            ).fetchone()[0]
            self.destination = con.execute(
                "SELECT id FROM inventory_locations WHERE store_id=? AND code='warehouse'",
                (self.mk_id,),
            ).fetchone()[0]
            for store_id in (self.store_id, self.mk_id):
                con.execute(
                    "INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)",
                    (store_id,),
                )
            con.execute("UPDATE inventory_scope_state SET opening_verified=1")
            con.execute(
                """INSERT INTO inventory_balances
                   (product_id, location_id, disposition, quantity, version)
                   VALUES (?, ?, 'saleable', 10, 1)""",
                (self.product_id, self.source),
            )
            con.commit()

    def test_partial_transfer_keeps_remaining_quantity_in_owned_transit(self):
        created = self.client.post('/api/goods/transfers', headers={
            **self.headers('manager'), 'Idempotency-Key': 'transfer-create-1'
        }, json={
            'source_location_id': self.source,
            'destination_location_id': self.destination,
            'business_date': '2026-09-08',
            'lines': [{'product_id': self.product_id, 'unit': 'piece', 'requested_quantity': 5}],
        })
        self.assertEqual(created.status_code, 201, created.get_json())
        delivery_id = created.get_json()['id']
        dispatched = self.client.post(
            f'/api/goods/transfers/{delivery_id}/dispatch',
            headers={**self.headers('manager'), 'Idempotency-Key': 'dispatch-1'},
            json={'expected_version': 1, 'lines': [{'line_no': 1, 'quantity': 4}]},
        )
        self.assertEqual(dispatched.status_code, 200, dispatched.get_json())
        received = self.client.post(
            f'/api/goods/transfers/{delivery_id}/receive',
            headers={**self.headers('manager'), 'Idempotency-Key': 'receive-1'},
            json={'expected_version': 2, 'lines': [{'line_no': 1, 'quantity': 3}]},
        )
        self.assertEqual(received.status_code, 200, received.get_json())
        detail = self.client.get(
            f'/api/goods/transfers/{delivery_id}', headers=self.headers('manager')
        ).get_json()
        self.assertEqual(detail['lines'][0]['outstanding_transit'], 1)
        with closing(self.connect()) as con:
            quantities = {
                (row['location_id'], row['disposition']): row['quantity']
                for row in con.execute(
                    "SELECT location_id, disposition, quantity FROM inventory_balances WHERE product_id=?",
                    (self.product_id,),
                )
            }
        self.assertEqual(quantities[(self.source, 'saleable')], 6)
        self.assertEqual(quantities[(self.source, 'transit')], 1)
        self.assertEqual(quantities[(self.destination, 'saleable')], 3)

    def test_selected_open_set_provenance_survives_partial_receive_and_return(self):
        with closing(self.connect()) as con:
            con.execute(
                "UPDATE products SET stock_form='random_box', stock_unit='box' WHERE id=?",
                (self.product_id,),
            )
            opening_document_id = con.execute(
                """INSERT INTO inventory_documents
                   (kind, request_key, payload_hash, actor_sub, business_date,
                    posted_at, status, stored_result)
                   VALUES ('open_set', 'fixture-open-set', 'fixture', 'auth0|staff',
                           '2026-09-08', datetime('now'), 'posted', '{}')"""
            ).lastrowid
            open_set_id = con.execute(
                """INSERT INTO inventory_open_sets
                   (opening_document_id, random_product_id, location_id,
                    purpose, remaining_qty)
                   VALUES (?, ?, ?, 'replenishment', 6)""",
                (opening_document_id, self.product_id, self.source),
            ).lastrowid
            con.commit()

        created = self.client.post('/api/goods/transfers', headers={
            **self.headers('manager'), 'Idempotency-Key': 'protected-create'
        }, json={
            'source_location_id': self.source,
            'destination_location_id': self.destination,
            'business_date': '2026-09-08',
            'lines': [{
                'product_id': self.product_id, 'unit': 'box',
                'requested_quantity': 2, 'open_set_id': open_set_id,
            }],
        }).get_json()
        dispatched = self.client.post(
            f"/api/goods/transfers/{created['id']}/dispatch",
            headers={**self.headers('manager'), 'Idempotency-Key': 'protected-dispatch'},
            json={'expected_version': 1, 'lines': [{'line_no': 1, 'quantity': 2}]},
        ).get_json()
        received = self.client.post(
            f"/api/goods/transfers/{created['id']}/receive",
            headers={**self.headers('manager'), 'Idempotency-Key': 'protected-receive'},
            json={'expected_version': dispatched['version'],
                  'lines': [{'line_no': 1, 'quantity': 1}]},
        ).get_json()
        self.client.post(
            f"/api/goods/transfers/{created['id']}/return",
            headers={**self.headers('manager'), 'Idempotency-Key': 'protected-return'},
            json={'expected_version': received['version'],
                  'lines': [{'line_no': 1, 'quantity': 1}]},
        )

        with closing(self.connect()) as con:
            provenance = {
                row['location_id']: row['remaining_qty'] for row in con.execute(
                    """SELECT location_id, remaining_qty FROM inventory_open_sets
                       WHERE opening_document_id=? AND random_product_id=?""",
                    (opening_document_id, self.product_id),
                )
            }
        self.assertEqual(provenance, {self.source: 5, self.destination: 1})

    def test_source_dispatch_and_destination_receive_keep_store_scopes_separate(self):
        with closing(self.connect()) as con:
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|admin', ?)",
                (self.mk_id,),
            )
            con.commit()
        created = self.client.post('/api/goods/transfers', headers={
            **self.headers('manager'), 'Idempotency-Key': 'scoped-create'
        }, json={
            'source_location_id': self.source,
            'destination_location_id': self.destination,
            'business_date': '2026-09-08',
            'lines': [{
                'product_id': self.product_id, 'unit': 'piece',
                'requested_quantity': 2,
            }],
        }).get_json()
        dispatched = self.client.post(
            f"/api/goods/transfers/{created['id']}/dispatch",
            headers={**self.headers('staff'), 'Idempotency-Key': 'scoped-dispatch'},
            json={'expected_version': 1, 'lines': [{'line_no': 1, 'quantity': 2}]},
        )
        self.assertEqual(dispatched.status_code, 200, dispatched.get_json())
        received = self.client.post(
            f"/api/goods/transfers/{created['id']}/receive",
            headers={**self.headers('admin'), 'Idempotency-Key': 'scoped-receive'},
            json={'expected_version': 2, 'lines': [{'line_no': 1, 'quantity': 2}]},
        )
        self.assertEqual(received.status_code, 200, received.get_json())
        unrelated = self.client.get(
            f"/api/goods/transfers/{created['id']}", headers=self.headers('viewer')
        )
        self.assertEqual(unrelated.status_code, 403)
