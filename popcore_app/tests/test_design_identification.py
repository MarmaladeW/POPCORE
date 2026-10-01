from contextlib import closing
import sqlite3
from unittest.mock import patch

from test_receiving import ReceivingFixture


class DesignIdentificationTests(ReceivingFixture):
    tables = ('inventory_balances', 'inventory_documents', 'inventory_document_lines',
              'inventory_movements', 'inventory_open_sets', 'operation_requests',
              'stock', 'stock_transactions', 'stock_movements', 'trade_units')

    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.series_id = con.execute("INSERT INTO product_series(name) VALUES ('Identification Series')").lastrowid
            con.execute("UPDATE products SET series_id=?,stock_form='random_box',stock_unit='box' WHERE id=?",
                        (self.series_id, self.product_id))
            self.design_ids = [con.execute(
                """INSERT INTO products(sku,jizhanming,name_cn_en,series_id,stock_form,stock_unit,design_name,identity_status)
                   VALUES (?,?,?,?,'confirmed_design','piece',?,'verified')""",
                (f'DESIGN-{name}', f'Design {name}', f'Design {name}', self.series_id, name),
            ).lastrowid for name in ('A', 'B')]
            for product_id, quantity in ((self.product_id, 5), (self.design_ids[1], 7)):
                con.execute("INSERT INTO inventory_balances(product_id,location_id,disposition,quantity,version) VALUES (?,?,'saleable',?,1)",
                            (product_id, self.floor, quantity))
            con.commit()

    def body(self):
        return dict(source_product_id=self.product_id, design_product_id=self.design_ids[0],
                    location_id=self.floor, quantity=2, source_version=1, target_version=0,
                    business_date='2026-09-30', reason='Opened and visually confirmed Design A')

    def identify(self, body=None, key='identify-1', role='staff'):
        return self.client.post('/api/goods/identify', json=self.body() if body is None else body,
                                headers={**self.headers(role), 'Idempotency-Key': key})

    def quantities(self):
        with closing(self.connect()) as con:
            return {row['product_id']: row['quantity'] for row in con.execute(
                "SELECT product_id,quantity FROM inventory_balances WHERE location_id=? AND disposition='saleable'",
                (self.floor,),
            )}

    def test_identification_conserves_stock_and_replays_linked_documents(self):
        response = self.identify()
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()
        self.assertEqual(set(result), {'consume_document_id', 'receipt_document_id'})
        self.assertEqual(self.quantities(), {self.product_id: 3, self.design_ids[0]: 2, self.design_ids[1]: 7})
        with closing(self.connect()) as con:
            documents = [dict(row) for row in con.execute('SELECT id,kind,source_type,source_id FROM inventory_documents ORDER BY id')]
            self.assertEqual([row['kind'] for row in documents], ['consume', 'receipt'])
            self.assertEqual([row['source_type'] for row in documents], ['design_identification'] * 2)
            self.assertEqual([row['source_id'] for row in documents], ['identify-1:consume', 'identify-1:receipt'])
            self.assertEqual([row['id'] for row in documents], [result['consume_document_id'], result['receipt_document_id']])
            self.assertEqual(con.execute('SELECT sum(quantity) FROM inventory_movements').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT count(*) FROM trade_units').fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT count(*) FROM inventory_balances WHERE disposition='trade'").fetchone()[0], 0)
        snapshot = self.snapshot(self.tables)
        replay = self.identify()
        self.assertEqual((replay.status_code, replay.get_json()), (200, result))
        changed = self.identify({**self.body(), 'quantity': 1})
        self.assertEqual((changed.status_code, changed.get_json()['code']), (409, 'idempotency_conflict'))
        self.assertEqual(self.snapshot(self.tables), snapshot)

    def test_stale_versions_or_shortage_leave_both_balances_unchanged(self):
        before = self.snapshot(self.tables)
        for changes, code in (({'source_version': 0}, 'stale_version'),
                              ({'target_version': 1}, 'stale_version'),
                              ({'quantity': 6}, 'insufficient_stock')):
            with self.subTest(changes=changes):
                response = self.identify({**self.body(), **changes})
                self.assertEqual((response.status_code, response.get_json()['code']), (409, code))
                self.assertEqual(self.snapshot(self.tables), before)

    def test_second_post_failure_rolls_back_source_and_compatibility_projection(self):
        from goods_operations import _post_inventory_in_transaction
        before = self.snapshot(self.tables)
        def fail_receipt(con, payload, **kwargs):
            if payload['kind'] == 'receipt':
                raise RuntimeError('Receipt interrupted')
            return _post_inventory_in_transaction(con, payload, **kwargs)
        with patch('goods_operations._post_inventory_in_transaction', side_effect=fail_receipt):
            with self.assertRaisesRegex(RuntimeError, 'Receipt interrupted'):
                self.identify()
        self.assertEqual(self.snapshot(self.tables), before)
        self.assertEqual(self.identify().status_code, 200)

    def test_invalid_input_and_identity_do_not_write_stock(self):
        before = self.snapshot(self.tables)
        for field, value in (('quantity', 0), ('quantity', -1), ('quantity', True), ('quantity', 1.5),
                             ('source_version', None), ('target_version', -1), ('reason', ''),
                             ('reason', ['A']), ('business_date', 'yesterday'), ('location_id', 0)):
            with self.subTest(field=field, value=value):
                response = self.identify({**self.body(), field: value})
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertEqual(self.snapshot(self.tables), before)
        for field, value in (('source_product_id', self.design_ids[1]), ('design_product_id', self.product_id)):
            response = self.identify({**self.body(), field: value})
            self.assertEqual((response.status_code, response.get_json()['code']), (409, 'design_identity_mismatch'))
            self.assertEqual(self.snapshot(self.tables), before)
        with closing(self.connect()) as con:
            other = con.execute("INSERT INTO product_series(name) VALUES ('Other Series')").lastrowid
            con.execute('UPDATE products SET series_id=? WHERE id=?', (other, self.design_ids[0]))
            con.commit()
        response = self.identify()
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'design_identity_mismatch'))
        self.assertEqual(self.snapshot(self.tables), before)

    def test_unverified_or_unassigned_products_cannot_be_identified(self):
        before = self.snapshot(self.tables)
        for product_id in (self.product_id, self.design_ids[0]):
            for field, value in (('identity_status', 'unverified'), ('series_id', None)):
                with self.subTest(product_id=product_id, field=field):
                    with closing(self.connect()) as con:
                        old = con.execute(f'SELECT {field} FROM products WHERE id=?', (product_id,)).fetchone()[0]
                        con.execute(f'UPDATE products SET {field}=? WHERE id=?', (value, product_id))
                        con.commit()
                    response = self.identify()
                    self.assertEqual((response.status_code, response.get_json()['code']), (409, 'design_identity_mismatch'))
                    self.assertEqual(self.snapshot(self.tables), before)
                    with closing(self.connect()) as con:
                        con.execute(f'UPDATE products SET {field}=? WHERE id=?', (old, product_id))
                        con.commit()

    def test_legacy_verified_design_without_a_name_cannot_receive_identified_stock(self):
        before = self.snapshot(self.tables)
        for name in (None, '', '  '):
            with self.subTest(name=name):
                with closing(self.connect()) as con:
                    con.execute('UPDATE products SET design_name=? WHERE id=?', (name, self.design_ids[0]))
                    con.commit()
                response = self.identify()
                self.assertEqual((response.status_code, response.get_json().get('code')), (409, 'design_identity_mismatch'))
                self.assertEqual(self.snapshot(self.tables), before)

    def test_unreviewed_and_unauthorized_scopes_are_rejected_including_replay(self):
        before = self.snapshot(self.tables)
        denied = self.identify(role='staff:outsider')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.snapshot(self.tables), before)
        with closing(self.connect()) as con:
            con.execute('UPDATE inventory_scope_state SET opening_verified=0 WHERE location_id=?', (self.floor,))
            con.commit()
        response = self.identify()
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'opening_unverified'))
        self.assertEqual(self.snapshot(self.tables), before)
        with closing(self.connect()) as con:
            con.execute('UPDATE inventory_scope_state SET opening_verified=1 WHERE location_id=?', (self.floor,))
            con.execute("UPDATE inventory_mode SET mode='legacy' WHERE id=1")
            con.commit()
        response = self.identify()
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'inventory_legacy_mode'))
        self.assertEqual(self.snapshot(self.tables), before)
        with closing(self.connect()) as con:
            con.execute("UPDATE inventory_mode SET mode='authoritative' WHERE id=1")
            con.commit()
        self.assertEqual(self.identify().status_code, 200)
        with closing(self.connect()) as con:
            con.execute("DELETE FROM inventory_access WHERE auth0_sub='auth0|staff'")
            con.commit()
        after = self.snapshot(self.tables)
        self.assertEqual(self.identify().status_code, 403)
        self.assertEqual(self.snapshot(self.tables), after)

    def test_selected_opened_set_is_consumed_only_once(self):
        with closing(self.connect()) as con:
            set_id = con.execute("""INSERT INTO products(sku,series_id,stock_form,stock_unit,identity_status)
                                  VALUES ('IDENTIFY-SET',?,'sealed_set','set','verified')""", (self.series_id,)).lastrowid
            conversion = con.execute('INSERT INTO product_conversions(source_product_id,target_product_id,output_per_input,version) VALUES (?,?,2,1)',
                                     (set_id, self.product_id)).lastrowid
            con.execute("INSERT INTO inventory_balances(product_id,location_id,disposition,quantity,version) VALUES (?,?,'saleable',1,1)",
                        (set_id, self.floor))
            con.commit()
        opened = self.client.post('/api/inventory/commands', headers={**self.headers(), 'Idempotency-Key': 'open-known-set'}, json={
            'kind': 'open_set', 'business_date': '2026-09-30', 'reason': 'Open reviewed pack',
            'lines': [{'product_id': set_id, 'unit': 'set', 'quantity': 1, 'conversion_id': conversion,
                       'conversion_factor': 2, 'purpose': 'customer_tray', 'from_location_id': self.floor,
                       'from_disposition': 'saleable', 'to_location_id': self.floor, 'to_disposition': 'saleable',
                       'expected_versions': {'from': 1, 'to': 1}}],
        })
        self.assertEqual(opened.status_code, 201, opened.get_json())
        with closing(self.connect()) as con:
            opened_id = con.execute('SELECT id FROM inventory_open_sets').fetchone()[0]
        before = self.snapshot(self.tables)
        denied = self.identify({**self.body(), 'quantity': 6, 'source_version': 2})
        self.assertEqual((denied.status_code, denied.get_json()['code']), (409, 'fresh_set_selection_required'))
        self.assertEqual(self.snapshot(self.tables), before)
        missing_set = self.identify({**self.body(), 'quantity': 1, 'source_version': 2, 'open_set_id': 99999})
        self.assertEqual((missing_set.status_code, missing_set.get_json()['code']), (409, 'insufficient_stock'))
        self.assertEqual(self.snapshot(self.tables), before)
        body = {**self.body(), 'quantity': 1, 'source_version': 2, 'open_set_id': opened_id}
        response = self.identify(body)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.identify(body).get_json(), response.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT remaining_qty FROM inventory_open_sets WHERE id=?', (opened_id,)).fetchone()[0], 1)
        self.assertEqual(self.quantities()[self.product_id], 6)
        self.assertEqual(self.quantities()[self.design_ids[0]], 1)
        snapshot = self.snapshot(self.tables)
        for field, expected_set in (('consume_document_id', opened_id), ('receipt_document_id', None)):
            document_id = response.get_json()[field]
            detail = self.client.get(f'/api/inventory/documents/{document_id}', headers=self.headers())
            self.assertEqual(detail.status_code, 200, detail.get_json())
            self.assertEqual(detail.get_json().get('reason'), body['reason'])
            self.assertIn('open_set_id', detail.get_json()['lines'][0])
            self.assertEqual(detail.get_json()['lines'][0]['open_set_id'], expected_set)
            self.assertEqual(self.client.get(f'/api/inventory/documents/{document_id}', headers=self.headers('staff:outsider')).status_code, 403)
            with closing(self.connect()) as con:
                for statement in ('UPDATE inventory_documents SET reason=NULL WHERE id=?',
                                  'UPDATE inventory_document_lines SET open_set_id=NULL WHERE document_id=?'):
                    with self.assertRaises(sqlite3.IntegrityError):
                        con.execute(statement, (document_id,))
                    con.rollback()
        changed = self.identify({**body, 'reason': 'Different tray claim'})
        self.assertEqual((changed.status_code, changed.get_json()['code']), (409, 'idempotency_conflict'))
        self.assertEqual(self.snapshot(self.tables), snapshot)

    def test_sale_of_design_a_does_not_consume_design_b_or_random_boxes(self):
        self.assertEqual(self.identify().status_code, 200)
        created = self.client.post('/api/sale-documents', headers={**self.headers(), 'Idempotency-Key': 'design-sale-create'}, json={
            'store_id': self.store_id, 'business_date': '2026-09-30', 'entry_mode': 'planned_entry',
            'source': {'system': 'manual', 'account': 'DT', 'reference': 'DESIGN-A-SALE'},
            'subtotal_cents': 2000, 'source_tax_cents': 260, 'gross_cents': 2260,
            'reduction_cents': 0, 'rounding_cents': 0, 'collected_cents': 2260,
            'lines': [{'product_id': self.design_ids[0], 'unit': 'piece', 'quantity': 1,
                       'unit_price_cents': 2000, 'source_tax_cents': 260}],
        })
        self.assertEqual(created.status_code, 201, created.get_json())
        sale = created.get_json()
        posted = self.client.post(f"/api/sale-documents/{sale['sale_id']}/post", json={'expected_version': sale['version']},
                                  headers={**self.headers(), 'Idempotency-Key': 'design-sale-post'})
        self.assertEqual(posted.status_code, 200, posted.get_json())
        self.assertEqual(posted.get_json()['allocation_status'], 'allocated')
        self.assertEqual(self.quantities(), {self.product_id: 3, self.design_ids[0]: 1, self.design_ids[1]: 7})
