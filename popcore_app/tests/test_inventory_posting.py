import unittest
from contextlib import closing
from unittest.mock import patch

from support import IsolatedApiCase

from inventory_commands import (
    InventoryConflict,
    InventoryValidationError,
    post_inventory,
    require_inventory_access,
)


class InventoryAccessTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.dt_id = con.execute(
                "SELECT id FROM stores WHERE code='DT'"
            ).fetchone()[0]
            self.mk_id = con.execute(
                "SELECT id FROM stores WHERE code='MK'"
            ).fetchone()[0]
            self.mt_id = con.execute(
                "SELECT id FROM stores WHERE code='MT'"
            ).fetchone()[0]
            for role in ('staff', 'manager', 'viewer', 'admin'):
                con.execute(
                    "INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)",
                    (f'auth0|{role}', self.dt_id),
                )
            con.commit()

    @staticmethod
    def payload(role):
        return {
            'sub': f'auth0|{role}',
            'https://popcore/role': role,
        }

    def test_locations_seed_only_reviewed_dt_and_mk_sites(self):
        with closing(self.connect()) as con:
            rows = [tuple(row) for row in con.execute(
                """SELECT s.code, l.code, l.name
                   FROM inventory_locations l
                   JOIN stores s ON s.id=l.store_id
                   ORDER BY s.code, l.code"""
            )]
            states = [tuple(row) for row in con.execute(
                """SELECT s.code, l.code, ss.opening_verified,
                          ss.opening_document_id
                   FROM inventory_scope_state ss
                   JOIN stores s ON s.id=ss.store_id
                   JOIN inventory_locations l ON l.id=ss.location_id
                   ORDER BY s.code, l.code"""
            )]
        self.assertEqual(rows, [
            ('DT', 'floor', 'Floor'),
            ('DT', 'upstairs', 'Upstairs'),
            ('MK', 'floor', 'Floor'),
            ('MK', 'warehouse', 'Warehouse'),
        ])
        self.assertTrue(all(row[2:] == (0, None) for row in states))
        self.assertNotIn('MT', {row[0] for row in rows})

    def test_role_and_explicit_scope_are_both_required(self):
        with closing(self.connect()) as con:
            require_inventory_access(con, self.payload('staff'),
                                     (self.dt_id,), 'staff')
            require_inventory_access(con, self.payload('manager'),
                                     (self.dt_id,), 'manager')
            require_inventory_access(con, self.payload('admin'),
                                     (self.dt_id,), 'admin')
            with self.assertRaises(PermissionError):
                require_inventory_access(con, self.payload('staff'),
                                         (self.mk_id,), 'staff')
            with self.assertRaises(PermissionError):
                require_inventory_access(con, self.payload('viewer'),
                                         (self.dt_id,), 'staff')
            with self.assertRaises(PermissionError):
                require_inventory_access(con, self.payload('admin'),
                                         (self.mk_id,), 'admin')
            with self.assertRaises(PermissionError):
                require_inventory_access(con, self.payload('unknown'),
                                         (self.dt_id,), 'viewer')

    def test_cross_store_requires_both_scopes(self):
        with closing(self.connect()) as con:
            with self.assertRaises(PermissionError):
                require_inventory_access(
                    con, self.payload('staff'),
                    (self.dt_id, self.mk_id), 'staff',
                )
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)",
                ('auth0|staff', self.mk_id),
            )
            con.commit()
            require_inventory_access(
                con, self.payload('staff'),
                (self.dt_id, self.mk_id), 'staff',
            )

    def test_employee_store_membership_does_not_grant_inventory_access(self):
        with closing(self.connect()) as con:
            employee_id = con.execute(
                """INSERT INTO employees(auth0_id, name, email)
                   VALUES ('auth0|schedule-only', 'Schedule Only',
                           'schedule@example.invalid')"""
            ).lastrowid
            con.execute(
                'INSERT INTO employee_stores(employee_id, store_id) VALUES (?, ?)',
                (employee_id, self.mk_id),
            )
            con.commit()
            with self.assertRaises(PermissionError):
                require_inventory_access(
                    con,
                    {'sub': 'auth0|schedule-only',
                     'https://popcore/role': 'staff'},
                    (self.mk_id,), 'staff',
                )

    def test_location_endpoint_returns_only_explicit_scope(self):
        response = self.client.get(
            '/api/inventory/locations', headers=self.headers('staff')
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual({row['store_code'] for row in body}, {'DT'})
        self.assertTrue(all(row['opening_verified'] is False for row in body))

        denied = self.client.get(
            '/api/inventory/locations?store_code=MK',
            headers=self.headers('staff'),
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.get_json()['code'], 'inventory_forbidden')



class InventoryPostingTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.dt_id = con.execute(
                "SELECT id FROM stores WHERE code='DT'"
            ).fetchone()[0]
            self.mk_id = con.execute(
                "SELECT id FROM stores WHERE code='MK'"
            ).fetchone()[0]
            self.dt_floor = con.execute(
                """SELECT id FROM inventory_locations
                   WHERE store_id=? AND code='floor'""", (self.dt_id,)
            ).fetchone()[0]
            self.mk_floor = con.execute(
                """SELECT id FROM inventory_locations
                   WHERE store_id=? AND code='floor'""", (self.mk_id,)
            ).fetchone()[0]
            con.execute(
                """UPDATE products SET stock_form='ordinary', stock_unit='piece',
                       identity_status='verified' WHERE id=?""",
                (self.product_id,),
            )
            for subject, store_id in (
                ('auth0|admin', self.dt_id),
                ('auth0|staff', self.dt_id),
            ):
                con.execute(
                    'INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)',
                    (subject, store_id),
                )
            con.commit()
        self.admin = {
            'sub': 'auth0|admin', 'https://popcore/role': 'admin'
        }
        self.staff = {
            'sub': 'auth0|staff', 'https://popcore/role': 'staff'
        }

    def opening_payload(self, quantity=5, location_id=None):
        return {
            'kind': 'opening',
            'business_date': '2026-09-08',
            'reason': 'Reviewed fixture opening',
            'lines': [{
                'product_id': self.product_id,
                'quantity': quantity,
                'unit': 'piece',
                'to_location_id': location_id or self.dt_floor,
                'to_disposition': 'saleable',
                'expected_versions': {'to': 0},
            }],
        }

    def consume_payload(self, quantity=4, version=1):
        return {
            'kind': 'consume',
            'business_date': '2026-09-08',
            'source_type': 'fixture_sale',
            'source_id': 'sale-1',
            'reason': 'Fixture sale',
            'lines': [{
                'product_id': self.product_id,
                'quantity': quantity,
                'unit': 'piece',
                'from_location_id': self.dt_floor,
                'from_disposition': 'saleable',
                'expected_versions': {'from': version},
            }],
        }

    def activate_dt(self, con):
        return post_inventory(
            con, self.opening_payload(), actor=self.admin,
            request_key='opening-dt-1',
        )

    def test_opening_then_consume_reconciles_movements_and_balance(self):
        with closing(self.connect()) as con:
            opening = self.activate_dt(con)
            consumed = post_inventory(
                con, self.consume_payload(), actor=self.staff,
                request_key='consume-1',
            )
            balance = con.execute(
                """SELECT quantity, version FROM inventory_balances
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (self.product_id, self.dt_floor),
            ).fetchone()
            movement_sum = con.execute(
                """SELECT SUM(quantity) FROM inventory_movements
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (self.product_id, self.dt_floor),
            ).fetchone()[0]
            statuses = [row[0] for row in con.execute(
                'SELECT status FROM inventory_documents ORDER BY id'
            )]
        self.assertEqual(opening['balances'][0]['quantity'], 5)
        self.assertEqual(consumed['balances'][0]['quantity'], 1)
        self.assertEqual(tuple(balance), (1, 2))
        self.assertEqual(movement_sum, 1)
        self.assertEqual(statuses, ['posted', 'posted'])

    def test_same_key_replays_exact_result_and_changed_intent_conflicts(self):
        with closing(self.connect()) as con:
            self.activate_dt(con)
            payload = self.consume_payload(quantity=2)
            first = post_inventory(
                con, payload, actor=self.staff, request_key='fixture-receipt-1'
            )
            second = post_inventory(
                con, payload, actor=self.staff, request_key='fixture-receipt-1'
            )
            self.assertEqual(second, first)
            self.assertEqual(con.execute(
                'SELECT COUNT(*) FROM inventory_documents WHERE request_key=?',
                ('fixture-receipt-1',),
            ).fetchone()[0], 1)
            changed = self.consume_payload(quantity=3)
            with self.assertRaises(InventoryConflict) as raised:
                post_inventory(
                    con, changed, actor=self.staff,
                    request_key='fixture-receipt-1',
                )
            self.assertEqual(raised.exception.code, 'idempotency_conflict')

    def test_duplicate_source_with_different_key_is_rejected(self):
        with closing(self.connect()) as con:
            self.activate_dt(con)
            post_inventory(
                con, self.consume_payload(quantity=1), actor=self.staff,
                request_key='consume-source-1',
            )
            duplicate = self.consume_payload(quantity=1, version=2)
            with self.assertRaises(InventoryConflict) as raised:
                post_inventory(
                    con, duplicate, actor=self.staff,
                    request_key='consume-source-2',
                )
            self.assertEqual(raised.exception.code, 'source_conflict')

    def test_shortage_and_stale_version_leave_no_partial_effect(self):
        with closing(self.connect()) as con:
            self.activate_dt(con)
            before = [tuple(row) for row in con.execute(
                'SELECT * FROM inventory_balances ORDER BY 1,2,3'
            )]
            with self.assertRaises(InventoryConflict) as shortage:
                post_inventory(
                    con, self.consume_payload(quantity=6), actor=self.staff,
                    request_key='shortage-1',
                )
            self.assertEqual(shortage.exception.code, 'insufficient_stock')
            with self.assertRaises(InventoryConflict) as stale:
                post_inventory(
                    con, self.consume_payload(quantity=1, version=9),
                    actor=self.staff, request_key='stale-1',
                )
            self.assertEqual(stale.exception.code, 'stale_version')
            after = [tuple(row) for row in con.execute(
                'SELECT * FROM inventory_balances ORDER BY 1,2,3'
            )]
            self.assertEqual(after, before)
            self.assertEqual(
                con.execute('SELECT COUNT(*) FROM inventory_documents').fetchone()[0],
                1,
            )

    def test_second_line_failure_rolls_back_first_line(self):
        with closing(self.connect()) as con:
            self.activate_dt(con)
            payload = self.consume_payload(quantity=1)
            payload['source_id'] = 'two-lines'
            payload['lines'].append({
                'product_id': self.product_id,
                'quantity': 99,
                'unit': 'piece',
                'from_location_id': self.dt_floor,
                'from_disposition': 'saleable',
                'expected_versions': {'from': 2},
            })
            with self.assertRaises(InventoryConflict):
                post_inventory(
                    con, payload, actor=self.staff, request_key='two-lines-1'
                )
            row = con.execute(
                """SELECT quantity, version FROM inventory_balances
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (self.product_id, self.dt_floor),
            ).fetchone()
            self.assertEqual(tuple(row), (5, 1))

    def test_replay_rechecks_scope_and_caller_transaction_is_rejected(self):
        with closing(self.connect()) as con:
            self.activate_dt(con)
            payload = self.consume_payload(quantity=1)
            post_inventory(
                con, payload, actor=self.staff, request_key='reauth-1'
            )
            con.execute(
                "DELETE FROM inventory_access WHERE auth0_sub='auth0|staff'"
            )
            con.commit()
            with self.assertRaises(PermissionError):
                post_inventory(
                    con, payload, actor=self.staff, request_key='reauth-1'
                )
            con.execute('BEGIN')
            with self.assertRaises(InventoryValidationError) as raised:
                post_inventory(
                    con, payload, actor=self.admin, request_key='nested-1'
                )
            self.assertEqual(raised.exception.code, 'caller_transaction_active')
            con.rollback()

    def test_cross_store_move_denial_changes_nothing(self):
        with closing(self.connect()) as con:
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)",
                ('auth0|admin', self.mk_id),
            )
            con.commit()
            opening = self.opening_payload()
            opening['lines'].append({
                'product_id': self.product_id,
                'quantity': 1,
                'unit': 'piece',
                'to_location_id': self.mk_floor,
                'to_disposition': 'saleable',
                'expected_versions': {'to': 0},
            })
            post_inventory(
                con, opening, actor=self.admin, request_key='opening-both-1'
            )
            before = {
                table: [tuple(row) for row in con.execute(
                    f'SELECT * FROM {table} ORDER BY 1'
                )]
                for table in ('inventory_documents', 'inventory_movements',
                              'inventory_balances')
            }
            transfer = {
                'kind': 'move', 'business_date': '2026-09-08',
                'reason': 'Cross-store fixture',
                'lines': [{
                    'product_id': self.product_id, 'quantity': 1,
                    'unit': 'piece',
                    'from_location_id': self.dt_floor,
                    'from_disposition': 'saleable',
                    'to_location_id': self.mk_floor,
                    'to_disposition': 'saleable',
                    'expected_versions': {'from': 1, 'to': 1},
                }],
            }
            with self.assertRaises(PermissionError):
                post_inventory(
                    con, transfer, actor=self.staff,
                    request_key='cross-store-denied-1',
                )
            after = {
                table: [tuple(row) for row in con.execute(
                    f'SELECT * FROM {table} ORDER BY 1'
                )]
                for table in before
            }
            self.assertEqual(after, before)

class InventoryConversionTests(InventoryPostingTests):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.series_id = con.execute(
                "INSERT INTO product_series(name) VALUES ('Conversion Series')"
            ).lastrowid
            self.set_id = con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, series_id, stock_form, stock_unit,
                    identity_status)
                   VALUES ('SET-12-C', 'Set 12', ?, 'sealed_set', 'set',
                           'unverified')""",
                (self.series_id,),
            ).lastrowid
            self.random_id = con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, series_id, stock_form, stock_unit,
                    identity_status)
                   VALUES ('RANDOM-12-C', 'Random 12', ?, 'random_box', 'box',
                           'verified')""",
                (self.series_id,),
            ).lastrowid
            from catalog_identity import add_conversion
            conversion = add_conversion(con, self.set_id, self.random_id, 12)
            self.conversion_id = conversion['id']
            con.execute(
                "UPDATE products SET identity_status='verified' WHERE id=?",
                (self.set_id,),
            )
            con.commit()

    def activate_and_receive_set(self, con):
        self.activate_dt(con)
        return post_inventory(con, {
            'kind': 'receipt', 'business_date': '2026-09-08',
            'source_type': 'fixture_receipt', 'source_id': 'receipt-set-1',
            'reason': 'Receive one sealed set',
            'lines': [{
                'product_id': self.set_id, 'quantity': 1, 'unit': 'set',
                'to_location_id': self.dt_floor,
                'to_disposition': 'saleable',
                'expected_versions': {'to': 0},
            }],
        }, actor=self.staff, request_key='receive-set-1')

    def open_payload(self, factor=12, purpose='replenishment'):
        return {
            'kind': 'open_set', 'business_date': '2026-09-08',
            'reason': 'Open reviewed set',
            'lines': [{
                'product_id': self.set_id, 'quantity': 1, 'unit': 'set',
                'conversion_id': self.conversion_id,
                'conversion_factor': factor,
                'purpose': purpose,
                'from_location_id': self.dt_floor,
                'from_disposition': 'saleable',
                'to_location_id': self.dt_floor,
                'to_disposition': 'saleable',
                'expected_versions': {'from': 1, 'to': 0},
            }],
        }

    def test_receive_and_open_set_preserves_equivalent_quantity_and_provenance(self):
        with closing(self.connect()) as con:
            receipt = self.activate_and_receive_set(con)
            before_set = next(
                row for row in receipt['balances'] if row['product_id'] == self.set_id
            )['quantity']
            before_boxes = 0
            opened = post_inventory(
                con, self.open_payload(), actor=self.staff,
                request_key='open-set-1',
            )
            balances = {
                row['product_id']: row['quantity'] for row in opened['balances']
            }
            provenance = con.execute(
                """SELECT purpose, remaining_qty FROM inventory_open_sets
                   WHERE opening_document_id=?""",
                (opened['document_id'],),
            ).fetchone()
            movements = [tuple(row) for row in con.execute(
                """SELECT product_id, quantity FROM inventory_movements
                   WHERE document_id=? ORDER BY id""",
                (opened['document_id'],),
            )]
        self.assertEqual(before_set * 12 + before_boxes, 12)
        self.assertEqual(balances[self.set_id] * 12 + balances[self.random_id], 12)
        self.assertEqual(balances, {self.set_id: 0, self.random_id: 12})
        self.assertEqual(tuple(provenance), ('replenishment', 12))
        self.assertEqual(movements, [(self.set_id, -1), (self.random_id, 12)])

    def test_open_set_replay_shortage_changed_factor_and_wrong_source(self):
        with closing(self.connect()) as con:
            self.activate_and_receive_set(con)
            payload = self.open_payload()
            first = post_inventory(
                con, payload, actor=self.staff, request_key='open-replay-1'
            )
            self.assertEqual(post_inventory(
                con, payload, actor=self.staff, request_key='open-replay-1'
            ), first)
            with self.assertRaises(InventoryConflict) as shortage:
                post_inventory(
                    con, {**payload, 'lines': [{
                        **payload['lines'][0],
                        'expected_versions': {'from': 2, 'to': 1},
                    }]}, actor=self.staff, request_key='open-shortage-1'
                )
            self.assertEqual(shortage.exception.code, 'insufficient_stock')
            with self.assertRaises(InventoryConflict) as changed:
                post_inventory(
                    con, self.open_payload(factor=9), actor=self.staff,
                    request_key='open-factor-1',
                )
            self.assertEqual(changed.exception.code, 'conversion_changed')
            wrong = self.open_payload()
            wrong['lines'][0]['product_id'] = self.random_id
            wrong['lines'][0]['unit'] = 'box'
            with self.assertRaises(InventoryConflict) as invalid_product:
                post_inventory(
                    con, wrong, actor=self.staff, request_key='open-wrong-1'
                )
            self.assertEqual(invalid_product.exception.code,
                             'conversion_product_mismatch')

    def test_failure_between_conversion_sides_rolls_back_everything(self):
        with closing(self.connect()) as con:
            self.activate_and_receive_set(con)
            before = [tuple(row) for row in con.execute(
                'SELECT * FROM inventory_balances ORDER BY 1,2,3'
            )]
            with patch('inventory_commands._apply_positive',
                       side_effect=RuntimeError('fixture failure')):
                with self.assertRaises(RuntimeError):
                    post_inventory(
                        con, self.open_payload(), actor=self.staff,
                        request_key='open-failure-1',
                    )
            after = [tuple(row) for row in con.execute(
                'SELECT * FROM inventory_balances ORDER BY 1,2,3'
            )]
            self.assertEqual(after, before)
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM inventory_documents WHERE request_key='open-failure-1'"
                ).fetchone()[0], 0,
            )

    def test_generic_consumption_preserves_named_fresh_set(self):
        with closing(self.connect()) as con:
            self.activate_and_receive_set(con)
            opened = post_inventory(
                con, self.open_payload(purpose='customer_tray'),
                actor=self.staff, request_key='open-tray-1',
            )
            protected_id = con.execute(
                'SELECT id FROM inventory_open_sets WHERE opening_document_id=?',
                (opened['document_id'],),
            ).fetchone()[0]
            generic = {
                'kind': 'consume', 'business_date': '2026-09-08',
                'reason': 'Generic sale',
                'lines': [{
                    'product_id': self.random_id, 'quantity': 1, 'unit': 'box',
                    'from_location_id': self.dt_floor,
                    'from_disposition': 'saleable',
                    'expected_versions': {'from': 1},
                }],
            }
            with self.assertRaises(InventoryConflict) as needs_selection:
                post_inventory(
                    con, generic, actor=self.staff,
                    request_key='generic-protected-1',
                )
            self.assertEqual(needs_selection.exception.code,
                             'fresh_set_selection_required')
            selected = generic.copy()
            selected['lines'] = [{
                **generic['lines'][0], 'open_set_id': protected_id,
            }]
            post_inventory(
                con, selected, actor=self.staff,
                request_key='selected-protected-1',
            )
            remaining = con.execute(
                'SELECT remaining_qty FROM inventory_open_sets WHERE id=?',
                (protected_id,),
            ).fetchone()[0]
            self.assertEqual(remaining, 11)


class InventoryCorrectionTests(InventoryPostingTests):
    def test_consumed_transfer_cannot_be_reversed_and_original_remains(self):
        with closing(self.connect()) as con:
            for subject in ('auth0|admin', 'auth0|staff'):
                con.execute(
                    'INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)',
                    (subject, self.mk_id),
                )
            con.commit()
            opening = self.opening_payload()
            opening['lines'].append({
                'product_id': self.product_id, 'quantity': 1, 'unit': 'piece',
                'to_location_id': self.mk_floor,
                'to_disposition': 'saleable',
                'expected_versions': {'to': 0},
            })
            post_inventory(
                con, opening, actor=self.admin, request_key='correction-open-1'
            )
            move = post_inventory(con, {
                'kind': 'move', 'business_date': '2026-09-08',
                'reason': 'Move five',
                'lines': [{
                    'product_id': self.product_id, 'quantity': 5, 'unit': 'piece',
                    'from_location_id': self.dt_floor,
                    'from_disposition': 'saleable',
                    'to_location_id': self.mk_floor,
                    'to_disposition': 'saleable',
                    'expected_versions': {'from': 1, 'to': 1},
                }],
            }, actor=self.staff, request_key='move-five-1')
            post_inventory(con, {
                'kind': 'consume', 'business_date': '2026-09-08',
                'reason': 'Consume transferred units',
                'lines': [{
                    'product_id': self.product_id, 'quantity': 5, 'unit': 'piece',
                    'from_location_id': self.mk_floor,
                    'from_disposition': 'saleable',
                    'expected_versions': {'from': 2},
                }],
            }, actor=self.staff, request_key='consume-five-1')
            correction = {
                'kind': 'correction', 'business_date': '2026-09-08',
                'reason': 'Reviewed exact reversal',
                'correction_of': move['document_id'],
                'lines': [{
                    'product_id': self.product_id, 'quantity': 5, 'unit': 'piece',
                    'from_location_id': self.mk_floor,
                    'from_disposition': 'saleable',
                    'to_location_id': self.dt_floor,
                    'to_disposition': 'saleable',
                    'expected_versions': {'from': 3, 'to': 2},
                }],
            }
            with self.assertRaises(InventoryConflict) as raised:
                post_inventory(
                    con, correction, actor={
                        'sub': 'auth0|admin', 'https://popcore/role': 'manager'
                    }, request_key='reverse-five-1',
                )
            self.assertEqual(raised.exception.code, 'insufficient_stock')
            self.assertEqual(con.execute(
                'SELECT status FROM inventory_documents WHERE id=?',
                (move['document_id'],),
            ).fetchone()[0], 'posted')
            self.assertEqual(con.execute(
                'SELECT COUNT(*) FROM inventory_documents WHERE correction_of=?',
                (move['document_id'],),
            ).fetchone()[0], 0)
if __name__ == '__main__':
    unittest.main()