from contextlib import closing

from test_sales_posting import SalePostingTests


class SaleReconciliationTests(SalePostingTests):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)",
                (self.store_id,),
            )
            con.commit()

    def posted_sale(self):
        draft = self.create(self.sale_body(reference='manual-reconcile'), 'reconcile-create').get_json()
        return self.post(draft, 'reconcile-post').get_json()

    def test_exact_source_link_does_not_duplicate_sale_or_stock(self):
        sale = self.posted_sale()
        response = self.client.post(
            f"/api/sale-documents/{sale['sale_id']}/source-links",
            headers={**self.headers('manager'), 'Idempotency-Key': 'source-link-1'},
            json={'expected_version': sale['version'], 'source_system': 'clover',
                  'source_account': 'DT', 'source_reference': 'clover-order-10',
                  'reason': 'Exact receipt identity'},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM sale_documents').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM sale_sources').fetchone()[0], 2)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM inventory_documents WHERE source_type='sale_document'"
            ).fetchone()[0], 1)

    def test_summary_reconciliation_is_review_fact_only(self):
        sale = self.posted_sale()
        before = self.snapshot(('sale_documents', 'inventory_documents', 'inventory_movements'))
        response = self.client.post('/api/sale-reconciliations', headers={
            **self.headers('manager'), 'Idempotency-Key': 'summary-reconcile-1',
        }, json={
            'store_id': self.store_id, 'business_date': '2026-09-08',
            'intent': 'summary_only', 'source_system': 'clover_report',
            'source_account': 'DT', 'source_reference': 'close-2026-09-08',
            'notes': 'Compared totals only; no transaction import',
        })
        self.assertEqual(response.status_code, 201, response.get_json())
        after = self.snapshot(('sale_documents', 'inventory_documents', 'inventory_movements'))
        self.assertEqual(after, before)
        self.assertEqual(sale['allocation_status'], 'allocated')

    def test_missing_transactions_require_stable_sale_identity(self):
        response = self.client.post('/api/sale-reconciliations', headers={
            **self.headers('manager'), 'Idempotency-Key': 'missing-no-sale',
        }, json={
            'store_id': self.store_id, 'business_date': '2026-09-08',
            'intent': 'missing_transactions', 'source_system': 'clover_report',
            'source_account': 'DT', 'source_reference': 'missing-batch',
            'notes': 'Unresolved batch',
        })
        self.assertEqual(response.status_code, 400, response.get_json())

    def test_stock_document_requires_exact_sale_coverage_and_can_be_claimed_once(self):
        first = self.posted_sale()
        second_draft = self.create(
            self.sale_body(quantity=1, reference='other-reconcile-sale'),
            'other-reconcile-create',
        ).get_json()
        second = self.post(second_draft, 'other-reconcile-post').get_json()
        with closing(self.connect()) as con:
            document_id = con.execute(
                'SELECT inventory_document_id FROM sale_documents WHERE id=?',
                (first['sale_id'],),
            ).fetchone()[0]
        base = {
            'store_id': self.store_id, 'business_date': '2026-09-08',
            'intent': 'stock_already_posted', 'source_system': 'clover_report',
            'source_account': 'DT', 'inventory_document_id': document_id,
            'notes': 'Reviewed exact movement coverage',
        }
        mismatch = self.client.post('/api/sale-reconciliations', headers={
            **self.headers('manager'), 'Idempotency-Key': 'mismatch-coverage',
        }, json={**base, 'source_reference': 'mismatch', 'sale_id': second['sale_id']})
        self.assertEqual(mismatch.status_code, 400, mismatch.get_json())
        accepted = self.client.post('/api/sale-reconciliations', headers={
            **self.headers('manager'), 'Idempotency-Key': 'exact-coverage',
        }, json={**base, 'source_reference': 'exact', 'sale_id': first['sale_id']})
        self.assertEqual(accepted.status_code, 201, accepted.get_json())
        competing = self.client.post('/api/sale-reconciliations', headers={
            **self.headers('manager'), 'Idempotency-Key': 'competing-coverage',
        }, json={**base, 'source_reference': 'competing', 'sale_id': first['sale_id']})
        self.assertEqual(competing.status_code, 409, competing.get_json())

    def test_legacy_record_deletion_does_not_touch_new_sale(self):
        sale = self.posted_sale()
        with closing(self.connect()) as con:
            legacy_id = con.execute(
                """INSERT INTO daily_sales
                   (product_id, date, store, qty_sold, qty_pos, qty_cash)
                   VALUES (?, '2026-09-07', 'DT', 1, 1, 0)""",
                (self.product_id,),
            ).lastrowid
            con.commit()
        deleted = self.client.delete(
            f'/api/sales/record/{legacy_id}', headers=self.headers('manager')
        )
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        with closing(self.connect()) as con:
            self.assertIsNotNone(con.execute(
                'SELECT id FROM sale_documents WHERE id=?', (sale['sale_id'],)
            ).fetchone())

    def test_legacy_report_requires_summary_classification_and_never_posts_stock(self):
        payload = {
            'store_code': 'DT', 'date': '2026-09-08', 'mode': 'append',
            'items': [{'product_id': self.product_id, 'section': 'pos',
                       'qty_pos': 2, 'qty_cash': 0, 'qty': 2}],
        }
        unclassified = self.client.post(
            '/api/sales/submit_daily_report', headers=self.headers(), json=payload,
        )
        self.assertEqual(unclassified.status_code, 409, unclassified.get_json())
        classified = self.client.post(
            '/api/sales/submit_daily_report', headers=self.headers(),
            json={**payload, 'classification': 'summary_only'},
        )
        self.assertEqual(classified.status_code, 200, classified.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM daily_sales').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM sale_documents').fetchone()[0], 0)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM inventory_documents WHERE source_type='sale_document'"
            ).fetchone()[0], 0)
