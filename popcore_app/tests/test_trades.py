from contextlib import closing

from support import IsolatedApiCase

from inventory_commands import InventoryConflict, post_inventory


class TradeTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.series_id = con.execute(
                "INSERT INTO product_series(name) VALUES ('Trade Series')"
            ).lastrowid
            self.random_id = con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, series_id, stock_form, stock_unit,
                    identity_status)
                   VALUES ('TRADE-RANDOM', 'Trade Random', ?, 'random_box',
                           'box', 'verified')""",
                (self.series_id,),
            ).lastrowid
            self.design_id = con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, series_id, stock_form, stock_unit,
                    design_name, identity_status)
                   VALUES ('TRADE-DESIGN', 'Trade Design', ?,
                           'confirmed_design', 'piece', 'Design A', 'verified')""",
                (self.series_id,),
            ).lastrowid
            self.floor_id = con.execute(
                """SELECT id FROM inventory_locations
                   WHERE store_id=? AND code='floor'""", (self.store_id,)
            ).fetchone()[0]
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)",
                ('auth0|staff', self.store_id),
            )
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)",
                ('auth0|manager', self.store_id),
            )
            con.execute(
                "UPDATE inventory_scope_state SET opening_verified=1 WHERE location_id=?",
                (self.floor_id,),
            )
            con.execute("UPDATE inventory_mode SET mode='authoritative' WHERE id=1")
            con.execute(
                """INSERT INTO inventory_balances
                   (product_id, location_id, disposition, quantity, version)
                   VALUES (?, ?, 'saleable', 5, 1)""",
                (self.random_id, self.floor_id),
            )
            con.commit()

    def create_slot(self):
        response = self.client.post(
            '/api/trade-slots', headers={
                **self.headers('manager'), 'Idempotency-Key': 'slot-create-1',
            }, json={
                'store_id': self.store_id,
                'series_id': self.series_id,
                'location_id': self.floor_id,
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def open_slot(self, slot, key='slot-open-helper'):
        response = self.client.post(
            f"/api/trade-slots/{slot['slot_id']}/open",
            headers={**self.headers('staff'), 'Idempotency-Key': key},
            json={
                'expected_version': slot['version'],
                'random_product_id': self.random_id,
                'design_product_id': self.design_id,
                'random_balance_version': 1, 'trade_balance_version': 0,
                'condition_disclosure': 'Complete, light shelf wear',
                'business_date': '2026-09-08',
            },
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_slot_opening_is_atomic_and_replays(self):
        slot = self.create_slot()
        payload = {
            'expected_version': slot['version'],
            'random_product_id': self.random_id,
            'design_product_id': self.design_id,
            'random_balance_version': 1,
            'trade_balance_version': 0,
            'condition_disclosure': 'Inspected: box and accessories present',
        }
        headers = {**self.headers('staff'), 'Idempotency-Key': 'slot-open-1'}
        first = self.client.post(
            f"/api/trade-slots/{slot['slot_id']}/open", headers=headers,
            json=payload,
        )
        self.assertEqual(first.status_code, 200, first.get_json())
        replay = self.client.post(
            f"/api/trade-slots/{slot['slot_id']}/open", headers=headers,
            json=payload,
        )
        self.assertEqual(replay.status_code, 200, replay.get_json())
        self.assertEqual(replay.get_json()['event_id'], first.get_json()['event_id'])
        with closing(self.connect()) as con:
            balances = {
                (row['product_id'], row['disposition']): row['quantity']
                for row in con.execute(
                    """SELECT product_id, disposition, quantity
                       FROM inventory_balances WHERE location_id=?""",
                    (self.floor_id,),
                )
            }
            self.assertEqual(balances[(self.random_id, 'saleable')], 4)
            self.assertEqual(balances[(self.design_id, 'trade')], 1)
            self.assertEqual(
                con.execute('SELECT COUNT(*) FROM trade_events').fetchone()[0], 1
            )

        detail = self.client.get(
            f"/api/trade-slots/{slot['slot_id']}", headers=self.headers('staff')
        )
        self.assertEqual(detail.status_code, 200, detail.get_json())
        self.assertEqual(detail.get_json()['occupant']['unit_id'], first.get_json()['unit_id'])
        forms = {item['stock_form'] for item in detail.get_json()['products']}
        self.assertEqual(forms, {'random_box', 'confirmed_design'})

    def test_duplicate_slot_and_unscoped_store_are_rejected(self):
        self.create_slot()
        duplicate = self.client.post(
            '/api/trade-slots', headers={
                **self.headers('manager'), 'Idempotency-Key': 'slot-create-2',
            }, json={
                'store_id': self.store_id, 'series_id': self.series_id,
                'location_id': self.floor_id,
            },
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.get_json()['code'], 'trade_slot_exists')

        mk = self.client.post(
            '/api/trade-slots', headers={
                **self.headers('manager'), 'Idempotency-Key': 'slot-create-mk',
            }, json={'store_id': 2, 'series_id': self.series_id, 'location_id': 3},
        )
        self.assertEqual(mk.status_code, 403)

    def test_inspection_requires_proof_and_rejects_confirmed_purchase(self):
        slot = self.create_slot()
        missing = self.client.post(
            f"/api/trade-slots/{slot['slot_id']}/inspections",
            headers={**self.headers('staff'), 'Idempotency-Key': 'inspect-missing'},
            json={'expected_version': slot['version'], 'design_product_id': self.design_id},
        )
        self.assertEqual(missing.status_code, 400)

        confirmed = self.client.post(
            f"/api/trade-slots/{slot['slot_id']}/inspections",
            headers={**self.headers('staff'), 'Idempotency-Key': 'inspect-purchase'},
            json={
                'expected_version': slot['version'],
                'design_product_id': self.design_id,
                'origin': 'confirmed_purchase', 'proof_kind': 'original_receipt',
                'proof_reference': 'receipt-1', 'box_checked': True,
                'accessories_checked': True, 'condition': 'sealed',
                'disclosure': 'No visible damage',
            },
        )
        self.assertEqual(confirmed.status_code, 409)
        self.assertEqual(confirmed.get_json()['code'], 'trade_ineligible')

        unchecked = self.client.post(
            f"/api/trade-slots/{slot['slot_id']}/inspections",
            headers={**self.headers('staff'), 'Idempotency-Key': 'inspect-unchecked'},
            json={
                'expected_version': slot['version'],
                'design_product_id': self.design_id,
                'origin': 'inspected_trade', 'proof_kind': 'legacy_sticker',
                'proof_reference': 'sticker-1', 'box_checked': False,
                'accessories_checked': True, 'condition': 'good',
                'disclosure': 'Box missing', 'decision': 'accepted',
            },
        )
        self.assertEqual(unchecked.status_code, 409)
        self.assertEqual(unchecked.get_json()['code'], 'trade_inspection_rejected')

        disguised_receipt = self.client.post(
            f"/api/trade-slots/{slot['slot_id']}/inspections",
            headers={**self.headers('staff'), 'Idempotency-Key': 'inspect-disguised'},
            json={'expected_version': slot['version'], 'design_product_id': self.design_id,
                  'origin': 'inspected_trade', 'proof_kind': 'original_receipt',
                  'proof_reference': 'receipt-2', 'box_checked': True,
                  'accessories_checked': True, 'condition': 'good',
                  'disclosure': 'Complete', 'decision': 'accepted'},
        )
        self.assertEqual(disguised_receipt.status_code, 409)
        self.assertEqual(disguised_receipt.get_json()['code'], 'trade_ineligible')

    def test_prior_trade_reuses_existing_physical_unit_identity(self):
        slot = self.open_slot(self.create_slot(), 'prior-open')
        with closing(self.connect()) as con:
            unit_id = con.execute(
                """INSERT INTO trade_units
                   (design_product_id,series_id,origin,eligibility,custody,
                    condition_disclosure,created_by)
                   VALUES (?,?,'inspected_trade','eligible','customer','Complete','auth0|staff')""",
                (self.design_id, self.series_id),
            ).lastrowid
            before = con.execute('SELECT COUNT(*) FROM trade_units').fetchone()[0]
            con.commit()
        response = self.client.post(
            f"/api/trade-slots/{slot['slot_id']}/inspections",
            headers={**self.headers('staff'), 'Idempotency-Key': 'prior-inspect'},
            json={'expected_version':slot['version'],'design_product_id':self.design_id,
                  'incoming_unit_id':unit_id,'origin':'inspected_trade','proof_kind':'prior_trade',
                  'proof_reference':str(unit_id),'box_checked':True,'accessories_checked':True,
                  'condition':'good','disclosure':'Complete','decision':'accepted'},
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()['incoming_unit_id'], unit_id)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM trade_units').fetchone()[0], before)

    def test_opening_failure_rolls_back_both_inventory_sides(self):
        slot = self.create_slot()
        failed = self.client.post(
            f"/api/trade-slots/{slot['slot_id']}/open",
            headers={**self.headers('staff'), 'Idempotency-Key': 'open-rollback'},
            json={
                'expected_version': slot['version'],
                'random_product_id': self.random_id,
                'design_product_id': self.design_id,
                'random_balance_version': 1, 'trade_balance_version': 1,
                'condition_disclosure': 'Inspected complete item',
                'business_date': '2026-09-08',
            },
        )
        self.assertEqual(failed.status_code, 409)
        with closing(self.connect()) as con:
            self.assertEqual(
                con.execute(
                    """SELECT quantity FROM inventory_balances
                       WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                    (self.random_id, self.floor_id),
                ).fetchone()[0], 5,
            )
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM inventory_documents WHERE source_type='trade_opening'"
                ).fetchone()[0], 0,
            )
            self.assertIsNone(
                con.execute(
                    'SELECT occupant_unit_id FROM trade_slots WHERE id=?',
                    (slot['slot_id'],),
                ).fetchone()[0]
            )

    def test_public_inventory_cannot_edit_trade_balance(self):
        actor = {'sub': 'auth0|staff', 'https://popcore/role': 'staff'}
        with closing(self.connect()) as con:
            with self.assertRaises(InventoryConflict) as raised:
                post_inventory(con, {
                    'kind': 'receipt', 'business_date': '2026-09-08',
                    'source_type': 'fixture', 'source_id': 'forged-trade',
                    'lines': [{
                        'product_id': self.design_id, 'quantity': 1,
                        'unit': 'piece', 'to_location_id': self.floor_id,
                        'to_disposition': 'trade',
                        'expected_versions': {'to': 0},
                    }],
                }, actor=actor, request_key='forged-trade')
            self.assertEqual(raised.exception.code, 'trade_context_required')

    def test_same_series_swap_is_atomic_versioned_and_replayed(self):
        slot = self.open_slot(self.create_slot())
        with closing(self.connect()) as con:
            incoming_design = con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, series_id, stock_form, stock_unit,
                    design_name, identity_status)
                   VALUES ('TRADE-DESIGN-B', 'Trade Design B', ?,
                           'confirmed_design', 'piece', 'Design B', 'verified')""",
                (self.series_id,),
            ).lastrowid
            con.commit()
        inspection = self.client.post(
            f"/api/trade-slots/{slot['slot_id']}/inspections",
            headers={**self.headers('staff'), 'Idempotency-Key': 'swap-inspection'},
            json={
                'expected_version': slot['version'],
                'design_product_id': incoming_design,
                'origin': 'inspected_trade', 'proof_kind': 'legacy_sticker',
                'proof_reference': 'sticker-B', 'box_checked': True,
                'accessories_checked': True, 'condition': 'good',
                'disclosure': 'Complete, minor wear', 'decision': 'accepted',
            },
        ).get_json()
        body = {
            'expected_version': slot['version'],
            'inspection_id': inspection['inspection_id'],
            'outgoing_balance_version': 1, 'incoming_balance_version': 0,
            'business_date': '2026-09-08',
        }
        headers = {**self.headers('staff'), 'Idempotency-Key': 'swap-1'}
        swapped = self.client.post(
            f"/api/trade-slots/{slot['slot_id']}/swap", headers=headers, json=body,
        )
        replay = self.client.post(
            f"/api/trade-slots/{slot['slot_id']}/swap", headers=headers, json=body,
        )
        self.assertEqual(swapped.status_code, 200, swapped.get_json())
        self.assertEqual(replay.get_json(), swapped.get_json())
        self.assertEqual(swapped.get_json()['incoming_unit_id'], inspection['incoming_unit_id'])
        with closing(self.connect()) as con:
            old = con.execute(
                'SELECT custody, eligibility FROM trade_units WHERE id=?',
                (slot['unit_id'],),
            ).fetchone()
            new = con.execute(
                'SELECT custody FROM trade_units WHERE id=?',
                (inspection['incoming_unit_id'],),
            ).fetchone()
            self.assertEqual(tuple(old), ('customer', 'eligible'))
            self.assertEqual(new['custody'], 'slot')
            self.assertEqual(
                con.execute("SELECT SUM(quantity) FROM inventory_balances WHERE disposition='trade'").fetchone()[0],
                1,
            )

    def test_direct_sale_consumes_exact_slot_unit_and_leaves_replacement_pending(self):
        slot = self.open_slot(self.create_slot(), 'sale-open')
        sale = self.client.post(
            '/api/sale-documents',
            headers={**self.headers('staff'), 'Idempotency-Key': 'trade-sale-draft'},
            json={
                'store_id': self.store_id, 'business_date': '2026-09-08',
                'entry_mode': 'planned_entry',
                'source': {'system': 'manual', 'account': 'DT', 'reference': 'trade-sale-1'},
                'subtotal_cents': 2000, 'source_tax_cents': 260,
                'gross_cents': 2260, 'reduction_cents': 0,
                'rounding_cents': 0, 'collected_cents': 2260,
                'lines': [{
                    'product_id': self.design_id, 'unit': 'piece', 'quantity': 1,
                    'unit_price_cents': 2000, 'source_tax_cents': 260,
                }],
            },
        ).get_json()
        sold = self.client.post(
            f"/api/trade-slots/{slot['slot_id']}/sale",
            headers={**self.headers('staff'), 'Idempotency-Key': 'trade-sale-post'},
            json={
                'expected_version': slot['version'], 'sale_id': sale['sale_id'],
                'sale_expected_version': sale['version'],
                'trade_balance_version': 1,
            },
        )
        self.assertEqual(sold.status_code, 200, sold.get_json())
        self.assertIsNone(sold.get_json()['occupant_unit_id'])
        with closing(self.connect()) as con:
            self.assertEqual(
                con.execute('SELECT status FROM sale_documents WHERE id=?', (sale['sale_id'],)).fetchone()[0],
                'posted',
            )
            line = con.execute(
                'SELECT condition_disclosure FROM sale_lines WHERE sale_id=? AND line_no=1',
                (sale['sale_id'],),
            ).fetchone()
            self.assertEqual(line[0], 'Complete, light shelf wear')
            self.assertEqual(
                con.execute("SELECT quantity FROM inventory_balances WHERE product_id=? AND disposition='trade'", (self.design_id,)).fetchone()[0],
                0,
            )
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM trade_events WHERE event_type='open'",).fetchone()[0],
                1,
            )
