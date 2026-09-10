from contextlib import closing

from test_receiving import ReceivingFixture


class SalePostingTests(ReceivingFixture):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            con.execute(
                """INSERT INTO inventory_balances
                   (product_id, location_id, disposition, quantity, version)
                   VALUES (?, ?, 'saleable', 5, 1)""",
                (self.product_id, self.floor),
            )
            con.commit()

    def sale_body(self, *, quantity=2, entry_mode='planned_entry', reference='manual-1'):
        return {
            'store_id': self.store_id,
            'business_date': '2026-09-08',
            'entry_mode': entry_mode,
            'source': {
                'system': 'manual', 'account': 'DT', 'reference': reference,
            },
            'subtotal_cents': 4000,
            'source_tax_cents': 520,
            'gross_cents': 4520,
            'reduction_cents': 0,
            'rounding_cents': 0,
            'collected_cents': 4520,
            'lines': [{
                'product_id': self.product_id, 'unit': 'piece',
                'quantity': quantity, 'unit_price_cents': 2000,
                'source_tax_cents': 520,
            }],
        }

    def create(self, body, key):
        return self.client.post('/api/sale-documents', headers={
            **self.headers(), 'Idempotency-Key': key,
        }, json=body)

    def post(self, sale, key, role='staff'):
        return self.client.post(f"/api/sale-documents/{sale['sale_id']}/post", headers={
            **self.headers(role), 'Idempotency-Key': key,
        }, json={'expected_version': sale['version']})

    def test_known_planned_sale_posts_financial_fact_and_stock_once(self):
        draft = self.create(self.sale_body(), 'sale-create-1')
        self.assertEqual(draft.status_code, 201, draft.get_json())
        posted = self.post(draft.get_json(), 'sale-post-1')
        replay = self.post(draft.get_json(), 'sale-post-1')
        self.assertEqual(posted.status_code, 200, posted.get_json())
        self.assertEqual(replay.get_json(), posted.get_json())
        self.assertEqual(posted.get_json()['allocation_status'], 'allocated')
        with closing(self.connect()) as con:
            quantity = con.execute(
                """SELECT quantity FROM inventory_balances
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (self.product_id, self.floor),
            ).fetchone()[0]
            documents = con.execute(
                "SELECT COUNT(*) FROM inventory_documents WHERE source_type='sale_document'"
            ).fetchone()[0]
        self.assertEqual((quantity, documents), (3, 1))

    def test_planned_shortage_leaves_draft_and_no_movement(self):
        draft = self.create(self.sale_body(quantity=6), 'sale-create-short').get_json()
        response = self.post(draft, 'sale-post-short')
        self.assertEqual(response.status_code, 409, response.get_json())
        with closing(self.connect()) as con:
            row = con.execute(
                'SELECT status FROM sale_documents WHERE id=?', (draft['sale_id'],)
            ).fetchone()
            quantity = con.execute(
                """SELECT quantity FROM inventory_balances
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (self.product_id, self.floor),
            ).fetchone()[0]
        self.assertEqual((row['status'], quantity), ('draft', 5))

    def test_already_paid_shortage_retains_finance_pending_allocation(self):
        draft = self.create(
            self.sale_body(quantity=6, entry_mode='already_paid'), 'paid-create'
        ).get_json()
        posted = self.post(draft, 'paid-post')
        self.assertEqual(posted.status_code, 200, posted.get_json())
        self.assertEqual(posted.get_json()['financial_status'], 'recorded')
        self.assertEqual(posted.get_json()['allocation_status'], 'pending')
        self.assertEqual(posted.get_json()['unresolved_reasons'], ['insufficient_stock'])

    def test_duplicate_source_and_cross_store_are_rejected(self):
        first = self.create(self.sale_body(reference='clover-100'), 'source-first')
        self.assertEqual(first.status_code, 201, first.get_json())
        duplicate = self.create(self.sale_body(reference='clover-100'), 'source-second')
        self.assertEqual(duplicate.status_code, 409, duplicate.get_json())

        with closing(self.connect()) as con:
            mk_id = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
        denied = self.create({**self.sale_body(reference='mk-1'), 'store_id': mk_id},
                             'source-denied')
        self.assertEqual(denied.status_code, 403, denied.get_json())

    def test_staff_cannot_update_or_post_another_staff_members_draft(self):
        draft = self.create(
            self.sale_body(reference='owned-draft'), 'owned-draft-create'
        ).get_json()
        with closing(self.connect()) as con:
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|other', ?)",
                (self.store_id,),
            )
            con.commit()
        changed_body = self.sale_body(reference='owned-draft')
        changed_body['expected_version'] = draft['version']
        updated = self.client.patch(
            f"/api/sale-documents/{draft['sale_id']}",
            headers={**self.headers('staff:other'), 'Idempotency-Key': 'other-update'},
            json=changed_body,
        )
        posted = self.post(draft, 'other-post', role='staff:other')
        self.assertEqual(updated.status_code, 403, updated.get_json())
        self.assertEqual(posted.status_code, 403, posted.get_json())
        with closing(self.connect()) as con:
            sale = con.execute(
                'SELECT status, version FROM sale_documents WHERE id=?',
                (draft['sale_id'],),
            ).fetchone()
        self.assertEqual(tuple(sale), ('draft', 1))

    def test_sale_money_and_line_amounts_must_be_nonnegative(self):
        cases = (
            ('subtotal_cents', 'subtotal_cents'),
            ('source_tax_cents', 'source_tax_cents'),
            ('gross_cents', 'gross_cents'),
            ('collected_cents', 'collected_cents'),
            ('line_unit_price', 'unit_price_cents'),
            ('line_source_tax', 'source_tax_cents'),
        )
        for index, (case, field) in enumerate(cases):
            with self.subTest(case=case):
                body = self.sale_body(reference=f'negative-{index}')
                body.update({
                    'subtotal_cents': None, 'source_tax_cents': None,
                    'gross_cents': None, 'collected_cents': None,
                })
                if case == 'line_unit_price':
                    body['lines'][0]['unit_price_cents'] = -1
                elif case == 'line_source_tax':
                    body['lines'][0]['source_tax_cents'] = -1
                else:
                    body[case] = -1
                response = self.create(body, f'negative-{index}')
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertIn(field, response.get_json()['error'])

    def test_mixed_validity_lines_roll_back_as_one_allocation(self):
        body = self.sale_body(reference='mixed-lines')
        with closing(self.connect()) as con:
            second_product = con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, jizhanming, product_type, boxes_per_dan,
                    stock_unit, identity_status)
                   VALUES ('TEST-2', 'Second Product', 'Second Product',
                           'ordinary', 1, 'piece', 'verified')"""
            ).lastrowid
            con.execute(
                """INSERT INTO inventory_balances
                   (product_id, location_id, disposition, quantity, version)
                   VALUES (?, ?, 'saleable', 1, 1)""",
                (second_product, self.floor),
            )
            con.commit()
        body['lines'].append({
            'product_id': second_product, 'unit': 'piece',
            'quantity': 99, 'unit_price_cents': 2000,
            'source_tax_cents': 0,
        })
        draft = self.create(body, 'mixed-create').get_json()
        response = self.post(draft, 'mixed-post')
        self.assertEqual(response.status_code, 409, response.get_json())
        with closing(self.connect()) as con:
            sale = con.execute(
                'SELECT status FROM sale_documents WHERE id=?', (draft['sale_id'],)
            ).fetchone()
            balance = con.execute(
                """SELECT quantity, version FROM inventory_balances
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (self.product_id, self.floor),
            ).fetchone()
        self.assertEqual(sale['status'], 'draft')
        self.assertEqual(tuple(balance), (5, 1))

    def test_duplicate_product_lines_are_rejected_before_draft_creation(self):
        body = self.sale_body(reference='duplicate-product-lines')
        body['lines'].append(dict(body['lines'][0]))
        response = self.create(body, 'duplicate-product-lines')
        self.assertEqual(response.status_code, 400, response.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM sale_documents').fetchone()[0], 0)

    def test_allocation_mapping_cannot_create_duplicate_product_lines(self):
        body = self.sale_body(entry_mode='already_paid', reference='duplicate-mappings')
        body['lines'] = [
            {'raw_product_text': 'Unknown A', 'quantity': 1},
            {'raw_product_text': 'Unknown B', 'quantity': 1},
        ]
        draft = self.create(body, 'duplicate-mappings-create').get_json()
        pending = self.post(draft, 'duplicate-mappings-post').get_json()
        with closing(self.connect()) as con:
            con.execute(
                "INSERT OR IGNORE INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)",
                (self.store_id,),
            )
            con.commit()
        response = self.client.post(
            f"/api/sale-documents/{draft['sale_id']}/allocate",
            headers={**self.headers('manager'), 'Idempotency-Key': 'duplicate-mappings'},
            json={
                'expected_version': pending['version'], 'reason': 'Mapped after review',
                'mappings': [
                    {'line_no': 1, 'product_id': self.product_id},
                    {'line_no': 2, 'product_id': self.product_id},
                ],
            },
        )
        self.assertEqual(response.status_code, 400, response.get_json())
        with closing(self.connect()) as con:
            rows = con.execute(
                'SELECT product_id FROM sale_lines WHERE sale_id=? ORDER BY line_no',
                (draft['sale_id'],),
            ).fetchall()
        self.assertEqual([row[0] for row in rows], [None, None])

    def test_lost_create_response_replays_same_draft(self):
        first = self.create(self.sale_body(reference='lost-create'), 'lost-key')
        replay = self.create(self.sale_body(reference='lost-create'), 'lost-key')
        self.assertEqual(first.status_code, 201, first.get_json())
        self.assertEqual(replay.get_json(), first.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute(
                'SELECT COUNT(*) FROM sale_documents'
            ).fetchone()[0], 1)

    def test_posted_line_snapshot_survives_catalog_change(self):
        draft = self.create(self.sale_body(reference='snapshot'), 'snapshot-create').get_json()
        posted = self.post(draft, 'snapshot-post')
        self.assertEqual(posted.status_code, 200, posted.get_json())
        with closing(self.connect()) as con:
            con.execute(
                "UPDATE products SET jizhanming='Renamed later' WHERE id=?",
                (self.product_id,),
            )
            con.commit()
        detail = self.client.get(
            f"/api/sale-documents/{draft['sale_id']}", headers=self.headers()
        )
        self.assertEqual(detail.status_code, 200, detail.get_json())
        self.assertEqual(detail.get_json()['lines'][0]['product_name_snapshot'],
                         'Test Product')
        self.assertEqual(detail.get_json()['lines'][0]['unit_price_cents'], 2000)

    def test_pending_allocation_resolves_once_and_old_post_retry_reads_current_state(self):
        draft = self.create(
            self.sale_body(quantity=6, entry_mode='already_paid', reference='resolve'),
            'resolve-create',
        ).get_json()
        pending = self.post(draft, 'resolve-post').get_json()
        self.assertEqual(pending['allocation_status'], 'pending')
        with closing(self.connect()) as con:
            con.execute(
                "INSERT OR IGNORE INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)",
                (self.store_id,),
            )
            con.execute(
                """UPDATE inventory_balances SET quantity=10, version=version+1
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (self.product_id, self.floor),
            )
            con.commit()
        allocated = self.client.post(
            f"/api/sale-documents/{draft['sale_id']}/allocate",
            headers={**self.headers('manager'), 'Idempotency-Key': 'resolve-allocate'},
            json={'expected_version': pending['version'], 'reason': 'Stock received'},
        )
        self.assertEqual(allocated.status_code, 200, allocated.get_json())
        replay = self.post(draft, 'resolve-post')
        self.assertEqual(replay.status_code, 200, replay.get_json())
        self.assertEqual(replay.get_json()['allocation_status'], 'allocated')
        with closing(self.connect()) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM inventory_documents WHERE source_type='sale_document'"
            ).fetchone()[0], 1)
            balance = con.execute(
                """SELECT quantity FROM inventory_balances
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (self.product_id, self.floor),
            ).fetchone()[0]
        self.assertEqual(balance, 4)

    def test_large_random_box_sale_requires_and_atomically_opens_selected_set(self):
        with closing(self.connect()) as con:
            set_id = con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, jizhanming, stock_form, stock_unit, identity_status)
                   VALUES ('SALE-SET-12', 'Sale Set', 'Sale Set', 'sealed_set', 'set',
                           'verified')"""
            ).lastrowid
            random_id = con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, jizhanming, stock_form, stock_unit, identity_status)
                   VALUES ('SALE-RANDOM-12', 'Sale Random', 'Sale Random', 'random_box',
                           'box', 'verified')"""
            ).lastrowid
            conversion_id = con.execute(
                """INSERT INTO product_conversions
                   (source_product_id, target_product_id, output_per_input, version)
                   VALUES (?, ?, 12, 1)""", (set_id, random_id),
            ).lastrowid
            con.execute(
                """INSERT INTO inventory_balances
                   (product_id, location_id, disposition, quantity, version)
                   VALUES (?, ?, 'saleable', 1, 1)""", (set_id, self.floor),
            )
            con.commit()

        body = self.sale_body(quantity=7, reference='needs-set')
        body['lines'] = [{
            'product_id': random_id, 'unit': 'box', 'quantity': 7,
            'unit_price_cents': 100, 'source_tax_cents': 0,
        }]
        draft = self.create(body, 'needs-set-create').get_json()
        denied = self.post(draft, 'needs-set-post')
        self.assertEqual(denied.status_code, 409, denied.get_json())
        self.assertEqual(denied.get_json()['code'], 'fresh_set_selection_required')

        body['source']['reference'] = 'selected-set'
        body['lines'][0]['fresh_set'] = {
            'product_id': set_id, 'conversion_id': conversion_id,
            'conversion_factor': 12,
        }
        selected = self.create(body, 'selected-set-create').get_json()
        posted = self.post(selected, 'selected-set-post')
        self.assertEqual(posted.status_code, 200, posted.get_json())
        self.assertEqual(posted.get_json()['allocation_status'], 'allocated')
        with closing(self.connect()) as con:
            balances = {
                row['product_id']: row['quantity'] for row in con.execute(
                    """SELECT product_id, quantity FROM inventory_balances
                       WHERE location_id=? AND product_id IN (?, ?)""",
                    (self.floor, set_id, random_id),
                )
            }
            remaining = con.execute(
                """SELECT remaining_qty FROM inventory_open_sets
                   WHERE random_product_id=?""", (random_id,),
            ).fetchone()[0]
        self.assertEqual(balances, {set_id: 0, random_id: 5})
        self.assertEqual(remaining, 5)
