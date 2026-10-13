from contextlib import closing

from test_receiving import ReceivingFixture


class SaleCreateReplayTests(ReceivingFixture):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_balances VALUES (?,?,'saleable',5,1)",
                        (self.product_id, self.floor))
            con.commit()
        self.body = {
            'store_id': self.store_id, 'business_date': '2026-09-30', 'entry_mode': 'planned_entry',
            'source': {'system': 'manual', 'account': 'DT', 'reference': 'RETRY-SALE'},
            'lines': [{'product_id': self.product_id, 'unit': 'piece', 'quantity': 2, 'unit_price_cents': 2000}],
        }
        self.tables = ('sale_documents', 'sale_lines', 'sale_sources', 'operation_requests',
                       'inventory_documents', 'inventory_movements', 'inventory_balances')

    def create(self, body=None, role='staff'):
        return self.client.post('/api/sale-documents', json=self.body if body is None else body,
                                headers={**self.headers(role), 'Idempotency-Key': 'sale-create-retry'})

    def receive(self, product_id, unit):
        response = self.client.post('/api/inventory/commands', json={
            'kind': 'receipt', 'business_date': '2026-09-30', 'reason': 'Another receipt while create response is missing',
            'lines': [{'product_id': product_id, 'unit': unit, 'quantity': 1,
                       'to_location_id': self.floor, 'to_disposition': 'saleable',
                       'expected_versions': {'to': 1}}],
        }, headers={**self.headers(), 'Idempotency-Key': 'stock-change'})
        self.assertEqual(response.status_code, 201, response.get_json())

    def test_unconfirmed_create_replays_after_stock_changes_without_recapturing_version(self):
        created = self.create()
        self.assertEqual(created.status_code, 201, created.get_json())
        self.receive(self.product_id, 'piece')
        before = self.snapshot(self.tables)
        replay = self.create()
        self.assertEqual((replay.status_code, replay.get_json()), (201, created.get_json()))
        self.assertEqual(before, self.snapshot(self.tables))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT captured_balance_version FROM sale_lines').fetchone()[0], 1)

    def test_unconfirmed_fresh_set_create_replays_after_set_stock_changes(self):
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET stock_form='random_box',stock_unit='box' WHERE id=?", (self.product_id,))
            set_id = con.execute("INSERT INTO products(sku,stock_form,stock_unit,identity_status) VALUES ('RETRY-SET','sealed_set','set','verified')").lastrowid
            conversion = con.execute('INSERT INTO product_conversions(source_product_id,target_product_id,output_per_input,version) VALUES (?,?,6,1)',
                                     (set_id, self.product_id)).lastrowid
            con.execute("INSERT INTO inventory_balances VALUES (?,?,'saleable',2,1)", (set_id, self.floor))
            con.commit()
        self.body['lines'][0].update(unit='box', fresh_set={'product_id': set_id, 'conversion_id': conversion, 'conversion_factor': 6})
        created = self.create()
        self.assertEqual(created.status_code, 201, created.get_json())
        self.receive(set_id, 'set')
        before = self.snapshot(self.tables)
        replay = self.create()
        self.assertEqual((replay.status_code, replay.get_json()), (201, created.get_json()))
        self.assertEqual(before, self.snapshot(self.tables))

    def test_replay_keeps_intent_actor_permission_and_source_guards(self):
        self.assertEqual(self.create().status_code, 201)
        self.receive(self.product_id, 'piece')
        before = self.snapshot(self.tables)
        for changes in (
            {'lines': [{**self.body['lines'][0], 'quantity': 3}]},
            {'lines': [{**self.body['lines'][0], 'unit_price_cents': 2500}]},
            {'source': {**self.body['source'], 'reference': 'DIFFERENT'}},
        ):
            response = self.create({**self.body, **changes})
            self.assertEqual((response.status_code, response.get_json()['code']), (409, 'idempotency_conflict'))
        reserved = self.create({**self.body, 'source': {**self.body['source'], 'system': 'popcore_checkout'}})
        self.assertEqual(reserved.status_code, 400, reserved.get_json())
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access VALUES ('auth0|other',?)", (self.store_id,))
            con.commit()
        other = self.create(role='staff:other')
        self.assertEqual((other.status_code, other.get_json()['code']), (409, 'idempotency_conflict'))
        with closing(self.connect()) as con:
            con.execute("DELETE FROM inventory_access WHERE auth0_sub='auth0|staff'")
            con.commit()
        self.assertEqual(self.create().status_code, 403)
        self.assertEqual(before, self.snapshot(self.tables))

    def test_update_replays_after_stock_changes_and_rejects_changed_user_intent(self):
        created = self.create().get_json()
        path = f"/api/sale-documents/{created['sale_id']}"
        body = {**self.body, 'expected_version': created['version'],
                'lines': [{**self.body['lines'][0], 'quantity': 3}]}
        headers = {**self.headers(), 'Idempotency-Key': 'sale-update-retry'}
        updated = self.client.patch(path, json=body, headers=headers)
        self.assertEqual(updated.status_code, 200, updated.get_json())
        self.receive(self.product_id, 'piece')
        before = self.snapshot(self.tables)
        replay = self.client.patch(path, json=body, headers=headers)
        self.assertEqual((replay.status_code, replay.get_json()), (200, updated.get_json()))
        changed = self.client.patch(path, json={**body, 'lines': [{**body['lines'][0], 'quantity': 4}]}, headers=headers)
        self.assertEqual((changed.status_code, changed.get_json()['code']), (409, 'idempotency_conflict'))
        self.assertEqual(before, self.snapshot(self.tables))

    def test_unchanged_legacy_create_hash_still_replays(self):
        from goods_operations import _hash
        from sales_operations import _sale_input
        created = self.create()
        self.assertEqual(created.status_code, 201, created.get_json())
        with closing(self.connect()) as con:
            full_intent = _sale_input(con, self.body, self._decode_test_token('staff'))
            con.execute('UPDATE operation_requests SET payload_hash=? WHERE request_key=?',
                        (_hash(full_intent), 'sale-create-retry'))
            con.commit()
        before = self.snapshot(self.tables)
        replay = self.create()
        self.assertEqual((replay.status_code, replay.get_json()), (201, created.get_json()))
        self.assertEqual(before, self.snapshot(self.tables))
