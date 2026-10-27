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

    def _act(self, action, key, version, lines, **extra):
        return self.client.post(
            f'/api/restock/session/{self.session_id}/{action}',
            headers={**self.headers(), 'Idempotency-Key': key},
            json={'expected_version': version, 'lines': lines, **extra},
        )

    def _balances(self):
        with closing(self.connect()) as con:
            return {(r['product_id'], r['location_id'], r['disposition']): r['quantity']
                    for r in con.execute(
                        'SELECT product_id, location_id, disposition, quantity FROM inventory_balances')}

    def test_confirming_the_pick_again_cannot_dispatch_more_than_was_found(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE restock_items SET requested_qty=10 WHERE session_id=?',
                        (self.session_id,))
            con.commit()
        first = self._act('pick', 'confirm-pick-1', 1, [{'line_no': 1, 'quantity': 4}])
        self.assertEqual(first.status_code, 200, first.get_json())
        # The refreshed sheet offered 确认拣货 again, with a new key and the new version.
        again = self._act('pick', 'confirm-pick-2', 2, [{'line_no': 1, 'quantity': 4}])
        self.assertEqual(again.status_code, 409, again.get_json())
        self.assertEqual(again.get_json()['code'], 'delivery_quantity_conflict')
        one_more = self._act('pick', 'confirm-pick-3', 2, [{'line_no': 1, 'quantity': 1}])
        self.assertEqual(one_more.status_code, 409, one_more.get_json())
        balances = self._balances()
        self.assertEqual(balances[(self.product_id, self.back, 'saleable')], 6)
        self.assertEqual(balances[(self.product_id, self.back, 'transit')], 4)
        line = self.client.get(f'/api/restock/session/{self.session_id}',
                               headers=self.headers()).get_json()['delivery']['lines'][0]
        self.assertEqual((line['dispatched_quantity'], line['outstanding_transit']), (4, 4))

    def test_dispatch_into_a_short_closed_quantity_is_a_conflict(self):
        with closing(self.connect()) as con:
            other = con.execute(
                """INSERT INTO products (sku, name_cn_en, jizhanming, product_type,
                                         stock_form, stock_unit, identity_status)
                   VALUES ('TEST-2', 'Second', 'Second', 'ordinary', 'ordinary', 'piece', 'verified')"""
            ).lastrowid
            con.execute(
                """INSERT INTO inventory_balances (product_id, location_id, disposition, quantity, version)
                   VALUES (?, ?, 'saleable', 5, 1)""", (other, self.back))
            con.execute(
                """INSERT INTO restock_items (session_id, product_id, requested_qty, found_qty, pick_status)
                   VALUES (?, ?, 2, 2, 'found')""", (self.session_id, other))
            con.commit()
        steps = (
            ('pick', 1, [{'line_no': 1, 'quantity': 2}, {'line_no': 2, 'quantity': 2}], {}),
            ('receive', 2, [{'line_no': 1, 'quantity': 2}], {}),
            # Line 1 asked for 5: 2 arrived, the other 3 are closed short. Line 2 is still in transit.
            ('short-close', 3, [{'line_no': 1, 'quantity': 3}], {'reason': 'Rest not on the shelf'}),
        )
        for number, (action, version, lines, extra) in enumerate(steps, 1):
            response = self._act(action, f'short-{number}', version, lines, **extra)
            self.assertEqual(response.status_code, 200, response.get_json())
        before = self._balances()
        late = self._act('pick', 'short-late-pick', 4, [{'line_no': 1, 'quantity': 2}])
        self.assertEqual(late.status_code, 409, late.get_json())
        self.assertEqual(late.get_json()['code'], 'delivery_quantity_conflict')
        self.assertEqual(self._balances(), before)

    def test_refused_first_pick_leaves_no_delivery_and_the_sheet_stays_editable(self):
        with closing(self.connect()) as con:
            con.execute(
                """UPDATE inventory_balances SET quantity=2
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (self.product_id, self.back))
            item_id = con.execute('SELECT id FROM restock_items WHERE session_id=?',
                                  (self.session_id,)).fetchone()[0]
            con.commit()
        refused = self._act('pick', 'refused-first-pick', 1, [{'line_no': 1, 'quantity': 4}])
        self.assertEqual(refused.status_code, 409, refused.get_json())
        self.assertEqual(refused.get_json()['code'], 'insufficient_stock')
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM inventory_deliveries').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM operation_requests').fetchone()[0], 0)
        corrected = self.client.patch(f'/api/restock/items/{item_id}/pick', headers=self.headers(),
                                      json={'pick_status': 'found', 'found_qty': 2})
        self.assertEqual(corrected.status_code, 200, corrected.get_json())
        deleted = self.client.delete(f'/api/restock/session/{self.session_id}', headers=self.headers())
        self.assertEqual(deleted.status_code, 200, deleted.get_json())

    def test_public_restock_complete_refuses_a_sheet_whose_goods_are_in_transit(self):
        picked = self._act('pick', 'transit-pick', 1, [{'line_no': 1, 'quantity': 4}])
        self.assertEqual(picked.status_code, 200, picked.get_json())
        before = self._balances()
        command = self.client.post('/api/inventory/commands', headers={
            **self.headers(), 'Idempotency-Key': 'restock-complete-command'}, json={
            'kind': 'restock_complete', 'business_date': '2026-09-08',
            'source_type': 'restock_session', 'source_id': str(self.session_id),
            'lines': [{'product_id': self.product_id, 'unit': 'piece', 'quantity': 4,
                       'from_location_id': self.back, 'from_disposition': 'saleable',
                       'to_location_id': self.floor, 'to_disposition': 'saleable',
                       'expected_versions': {'from': 2, 'to': 0}}]})
        self.assertEqual(command.status_code, 409, command.get_json())
        self.assertEqual(command.get_json()['code'], 'delivery_receipt_required')
        self.assertEqual(self._balances(), before)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT status FROM restock_sessions WHERE id=?',
                                         (self.session_id,)).fetchone()[0], 'picking')
