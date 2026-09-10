from contextlib import closing

from test_receiving import ReceivingFixture


class CountTests(ReceivingFixture):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)",
                (self.store_id,),
            )
            con.execute(
                """INSERT INTO inventory_balances
                   (product_id, location_id, disposition, quantity, version)
                   VALUES (?, ?, 'saleable', 10, 1)""",
                (self.product_id, self.floor),
            )
            con.commit()

    def test_approved_count_posts_observed_minus_captured_expected_once(self):
        created = self.client.post('/api/goods/counts', headers={
            **self.headers(), 'Idempotency-Key': 'count-create-1'
        }, json={
            'location_id': self.floor, 'business_date': '2026-09-08',
            'lines': [{'product_id': self.product_id, 'unit': 'piece', 'observed_quantity': 9}],
        })
        self.assertEqual(created.status_code, 201, created.get_json())
        count_id = created.get_json()['id']
        submitted = self.client.post(
            f'/api/goods/counts/{count_id}/submit',
            headers={**self.headers(), 'Idempotency-Key': 'count-submit-1'},
            json={'expected_version': 1},
        )
        self.assertEqual(submitted.status_code, 200, submitted.get_json())
        approved = self.client.post(
            f'/api/goods/counts/{count_id}/approve',
            headers={**self.headers('manager'), 'Idempotency-Key': 'count-approve-1'},
            json={'expected_version': 2, 'reason': 'Reviewed physical count'},
        )
        self.assertEqual(approved.status_code, 200, approved.get_json())
        replay = self.client.post(
            f'/api/goods/counts/{count_id}/approve',
            headers={**self.headers('manager'), 'Idempotency-Key': 'count-approve-1'},
            json={'expected_version': 2, 'reason': 'Reviewed physical count'},
        )
        self.assertEqual(replay.get_json(), approved.get_json())
        with closing(self.connect()) as con:
            row = con.execute(
                """SELECT quantity, version FROM inventory_balances
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (self.product_id, self.floor),
            ).fetchone()
        self.assertEqual(tuple(row), (9, 2))

    def test_restock_suggestion_is_bounded_by_back_stock(self):
        with closing(self.connect()) as con:
            back = con.execute(
                "SELECT id FROM inventory_locations WHERE store_id=? AND code='upstairs'",
                (self.store_id,),
            ).fetchone()[0]
            con.execute(
                """UPDATE inventory_balances SET quantity=2
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (self.product_id, self.floor),
            )
            con.execute(
                """INSERT INTO inventory_balances
                   (product_id, location_id, disposition, quantity, version)
                   VALUES (?, ?, 'saleable', 3, 1)""",
                (self.product_id, back),
            )
            con.commit()
        saved = self.client.put('/api/goods/targets', headers={
            **self.headers('manager'), 'Idempotency-Key': 'target-1'
        }, json={
            'location_id': self.floor, 'product_id': self.product_id,
            'min_quantity': 4, 'max_quantity': 8, 'expected_version': 0,
        })
        self.assertEqual(saved.status_code, 200, saved.get_json())
        suggestions = self.client.get(
            f'/api/goods/restock-suggestions?location_id={self.floor}',
            headers=self.headers(),
        )
        self.assertEqual(suggestions.status_code, 200, suggestions.get_json())
        self.assertEqual(suggestions.get_json()['items'][0]['suggested_quantity'], 3)

    def test_return_keeps_submitted_observation_and_creates_recount(self):
        created = self.client.post('/api/goods/counts', headers={
            **self.headers(), 'Idempotency-Key': 'count-return-create'
        }, json={
            'location_id': self.floor, 'business_date': '2026-09-08',
            'lines': [{
                'product_id': self.product_id, 'unit': 'piece',
                'observed_quantity': 8,
            }],
        }).get_json()
        submitted = self.client.post(
            f"/api/goods/counts/{created['id']}/submit",
            headers={**self.headers(), 'Idempotency-Key': 'count-return-submit'},
            json={'expected_version': created['version']},
        ).get_json()
        returned = self.client.post(
            f"/api/goods/counts/{created['id']}/return",
            headers={**self.headers('manager'), 'Idempotency-Key': 'count-return'},
            json={'expected_version': submitted['version'], 'reason': 'Recount shelf'},
        )
        self.assertEqual(returned.status_code, 200, returned.get_json())
        with closing(self.connect()) as con:
            original = con.execute(
                'SELECT status FROM inventory_counts WHERE id=?', (created['id'],)
            ).fetchone()['status']
            replacement = con.execute(
                'SELECT status FROM inventory_counts WHERE id=?',
                (returned.get_json()['recount_id'],),
            ).fetchone()['status']
        self.assertEqual((original, replacement), ('returned', 'draft'))

    def test_count_cannot_erase_protected_opened_set_units(self):
        with closing(self.connect()) as con:
            con.execute(
                "UPDATE products SET stock_form='random_box', stock_unit='box' WHERE id=?",
                (self.product_id,),
            )
            opening_document_id = con.execute(
                """INSERT INTO inventory_documents
                   (kind, request_key, payload_hash, actor_sub, business_date,
                    posted_at, status, stored_result)
                   VALUES ('open_set', 'count-protected-fixture', 'fixture',
                           'auth0|staff', '2026-09-08', datetime('now'),
                           'posted', '{}')"""
            ).lastrowid
            con.execute(
                """INSERT INTO inventory_open_sets
                   (opening_document_id, random_product_id, location_id,
                    purpose, remaining_qty)
                   VALUES (?, ?, ?, 'replenishment', 8)""",
                (opening_document_id, self.product_id, self.floor),
            )
            con.commit()
        created = self.client.post('/api/goods/counts', headers={
            **self.headers(), 'Idempotency-Key': 'protected-count-create'
        }, json={
            'location_id': self.floor, 'business_date': '2026-09-08',
            'lines': [{
                'product_id': self.product_id, 'unit': 'box',
                'observed_quantity': 1,
            }],
        }).get_json()
        submitted = self.client.post(
            f"/api/goods/counts/{created['id']}/submit",
            headers={**self.headers(), 'Idempotency-Key': 'protected-count-submit'},
            json={'expected_version': created['version']},
        ).get_json()
        approved = self.client.post(
            f"/api/goods/counts/{created['id']}/approve",
            headers={**self.headers('manager'), 'Idempotency-Key': 'protected-count-approve'},
            json={'expected_version': submitted['version'], 'reason': 'Reviewed'},
        )
        self.assertEqual(approved.status_code, 409, approved.get_json())
        self.assertEqual(approved.get_json()['code'], 'provenance_reconciliation_required')
