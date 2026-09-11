import json
from contextlib import closing

from test_payments import PaymentTests
import db


class ClosingTests(PaymentTests):
    COUNTS = {
        '10000': 0, '5000': 18, '2000': 1, '1000': 1, '500': 1,
        '200': 1, '100': 1, '25': 2, '10': 0, '5': 0,
    }

    def create_session(self, key='closing-create'):
        return self.client.post('/api/closing', headers={
            **self.headers(), 'Idempotency-Key': key,
        }, json={'store_id': self.store_id, 'business_date': '2026-09-08'})

    def cash_fixture(self):
        sale = self.posted_sale('closing-sale')
        paid = self.add_payments(sale, [
            {'tender': 'cash', 'amount_cents': 30000},
            {'tender': 'card', 'amount_cents': 60000},
            {'tender': 'e_transfer', 'amount_cents': 10000},
            {'tender': 'wechat', 'amount_cents': 5000},
            {'tender': 'alipay', 'amount_cents': 5000},
        ], 'closing-payments').get_json()
        cash = next(item for item in paid['payments'] if item['tender'] == 'cash')
        verified = self.client.post(
            f"/api/payments/{cash['id']}/verify",
            headers={**self.headers('manager'), 'Idempotency-Key': 'verify-closing-cash'},
            json={'expected_version': paid['version'], 'reason': 'Counted in drawer'},
        ).get_json()
        session = self.create_session().get_json()
        payout = self.client.post(
            f"/api/closing/{session['closing_id']}/cash-events",
            headers={**self.headers(), 'Idempotency-Key': 'cash-payout'},
            json={'expected_version': session['version'], 'event_type': 'payout',
                  'amount_cents': 3000, 'reason': 'Store expense'},
        ).get_json()
        self.assertEqual(verified['version'], paid['version'] + 1)
        return payout

    def ready_session(self):
        session = self.cash_fixture()
        counted = self.client.post(
            f"/api/closing/{session['closing_id']}/cash-counts",
            headers={**self.headers(), 'Idempotency-Key': 'ready-count'},
            json={'expected_version': session['version'],
                  'source_token': session['source_token'],
                  'opening_coin_cents': 2000, 'retained_coin_cents': 2000,
                  'denomination_counts': self.COUNTS},
        ).get_json()
        return self.client.patch(
            f"/api/closing/{session['closing_id']}",
            headers={**self.headers(), 'Idempotency-Key': 'intake-complete'},
            json={'expected_version': counted['version'], 'intake_complete': True},
        ).get_json()

    def submitted_session(self):
        ready = self.ready_session()
        submitted = self.client.post(
            f"/api/closing/{ready['closing_id']}/submit",
            headers={**self.headers(), 'Idempotency-Key': 'submit-ready'},
            json={'expected_version': ready['version'],
                  'source_token': ready['source_token']},
        )
        self.assertEqual(submitted.status_code, 200, submitted.get_json())
        return submitted.get_json()

    def accepted_exceptions(self, session):
        detail = self.client.get(
            f"/api/closing/{session['closing_id']}", headers=self.headers('manager')
        ).get_json()
        return [{'code': code, 'reason': 'Manager reviewed source'}
                for code in detail['review_exceptions']]

    def test_exact_cash_fixture_and_non_cash_exclusion(self):
        session = self.cash_fixture()
        self.assertIn('sales', session['source_documents'])
        self.assertGreaterEqual(len(session['source_documents']['sales']), 1)
        counted = self.client.post(
            f"/api/closing/{session['closing_id']}/cash-counts",
            headers={**self.headers(), 'Idempotency-Key': 'cash-count-1'},
            json={'expected_version': session['version'],
                  'source_token': session['source_token'],
                  'opening_coin_cents': 2000, 'retained_coin_cents': 2000,
                  'denomination_counts': self.COUNTS},
        )
        self.assertEqual(counted.status_code, 200, counted.get_json())
        result = counted.get_json()['latest_cash_count']
        self.assertEqual(result['expected_cents'], 94000)
        self.assertEqual(result['counted_cents'], 93850)
        self.assertEqual(result['variance_cents'], -150)
        self.assertEqual(result['removal_cents'], 26850)
        self.assertEqual(counted.get_json()['cash']['verified_cash_receipts_cents'], 30000)

    def test_missing_coin_inputs_and_stale_cashier_save_are_rejected(self):
        session = self.cash_fixture()
        incomplete = self.client.post(
            f"/api/closing/{session['closing_id']}/cash-counts",
            headers={**self.headers(), 'Idempotency-Key': 'missing-coins'},
            json={'expected_version': session['version'],
                  'source_token': session['source_token'],
                  'denomination_counts': self.COUNTS},
        )
        self.assertEqual(incomplete.status_code, 400, incomplete.get_json())
        body = {'expected_version': session['version'],
                'source_token': session['source_token'],
                'opening_coin_cents': 2000, 'retained_coin_cents': 2000,
                'denomination_counts': self.COUNTS}
        first = self.client.post(
            f"/api/closing/{session['closing_id']}/cash-counts",
            headers={**self.headers(), 'Idempotency-Key': 'cashier-one'}, json=body,
        )
        with closing(self.connect()) as con:
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|other', ?)",
                (self.store_id,),
            )
            con.commit()
        second = self.client.post(
            f"/api/closing/{session['closing_id']}/cash-counts",
            headers={**self.headers('staff:other'), 'Idempotency-Key': 'cashier-two'}, json=body,
        )
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(second.status_code, 409, second.get_json())

    def test_cash_source_change_requires_a_new_count_before_submission(self):
        session = self.cash_fixture()
        counted = self.client.post(
            f"/api/closing/{session['closing_id']}/cash-counts",
            headers={**self.headers(), 'Idempotency-Key': 'count-before-cash-change'},
            json={'expected_version': session['version'],
                  'source_token': session['source_token'],
                  'opening_coin_cents': 2000, 'retained_coin_cents': 2000,
                  'denomination_counts': self.COUNTS},
        ).get_json()
        changed = self.client.post(
            f"/api/closing/{session['closing_id']}/cash-events",
            headers={**self.headers(), 'Idempotency-Key': 'cash-after-count'},
            json={'expected_version': counted['version'], 'event_type': 'paid_in',
                  'amount_cents': 100, 'reason': 'Late drawer deposit'},
        ).get_json()
        ready = self.client.patch(
            f"/api/closing/{session['closing_id']}",
            headers={**self.headers(), 'Idempotency-Key': 'intake-after-cash-change'},
            json={'expected_version': changed['version'], 'intake_complete': True},
        ).get_json()
        submitted = self.client.post(
            f"/api/closing/{session['closing_id']}/submit",
            headers={**self.headers(), 'Idempotency-Key': 'submit-stale-count'},
            json={'expected_version': ready['version'],
                  'source_token': ready['source_token']},
        )
        self.assertEqual(submitted.status_code, 409, submitted.get_json())
        self.assertEqual(submitted.get_json()['code'], 'closing_stale')

    def test_cash_event_replay_and_float_shortfall_remain_explicit(self):
        session = self.create_session('shortfall-session').get_json()
        body = {'expected_version': session['version'], 'event_type': 'paid_in',
                'amount_cents': 100, 'reason': 'Coin added'}
        first = self.client.post(
            f"/api/closing/{session['closing_id']}/cash-events",
            headers={**self.headers(), 'Idempotency-Key': 'paid-in-once'}, json=body,
        )
        replay = self.client.post(
            f"/api/closing/{session['closing_id']}/cash-events",
            headers={**self.headers(), 'Idempotency-Key': 'paid-in-once'}, json=body,
        )
        self.assertEqual(replay.get_json(), first.get_json())
        zero_counts = {key: 0 for key in self.COUNTS}
        counted = self.client.post(
            f"/api/closing/{session['closing_id']}/cash-counts",
            headers={**self.headers(), 'Idempotency-Key': 'short-count'},
            json={'expected_version': first.get_json()['version'],
                  'source_token': first.get_json()['source_token'],
                  'opening_coin_cents': 0, 'retained_coin_cents': 2000,
                  'denomination_counts': zero_counts},
        ).get_json()['latest_cash_count']
        self.assertIsNone(counted['removal_cents'])
        self.assertLess(counted['variance_cents'], 0)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM cash_events').fetchone()[0], 1)

    def test_staff_projection_excludes_management_tender_totals(self):
        session = self.cash_fixture()
        staff = self.client.get(
            f"/api/closing/{session['closing_id']}", headers=self.headers()
        )
        manager = self.client.get(
            f"/api/closing/{session['closing_id']}", headers=self.headers('manager')
        )
        self.assertNotIn('tender_totals_cents', staff.get_json())
        self.assertIn('tender_totals_cents', manager.get_json())
        self.assertNotIn('payment_evidence', str(staff.get_json()))

    def test_close_is_single_snapshot_and_single_removal_on_retry(self):
        session = self.submitted_session()
        body = {'expected_version': session['version'],
                'source_token': session['source_token'],
                'accepted_exceptions': self.accepted_exceptions(session)}
        first = self.client.post(
            f"/api/closing/{session['closing_id']}/close",
            headers={**self.headers('manager'), 'Idempotency-Key': 'close-once'}, json=body,
        )
        replay = self.client.post(
            f"/api/closing/{session['closing_id']}/close",
            headers={**self.headers('manager'), 'Idempotency-Key': 'close-once'}, json=body,
        )
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(replay.get_json(), first.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM closing_snapshots').fetchone()[0], 1)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM cash_events WHERE event_type='removal'"
            ).fetchone()[0], 1)
            snapshot = json.loads(con.execute(
                'SELECT snapshot_json FROM closing_snapshots'
            ).fetchone()[0])
        self.assertEqual(snapshot['tender_totals_cents']['cash'], 30000)
        self.assertIn('sources', snapshot)
        for key in ('sales', 'sale_sources', 'sale_reconciliations', 'payments',
                    'payment_events', 'evidence', 'cash_events', 'cash_counts',
                    'receipts', 'counts', 'deliveries', 'restocks', 'balances'):
            self.assertIn(key, snapshot['sources'])

    def test_change_after_submit_makes_close_stale(self):
        session = self.submitted_session()
        with closing(self.connect()) as con:
            sale_id = con.execute(
                """SELECT id FROM sale_documents WHERE store_id=? AND business_date=?
                   ORDER BY id LIMIT 1""", (self.store_id, '2026-09-08'),
            ).fetchone()[0]
            con.execute(
                """INSERT INTO sale_payments
                   (sale_id, tender, amount_cents, recorded_by)
                   VALUES (?, 'card', 100, 'auth0|staff')""", (sale_id,),
            )
            con.commit()
        response = self.client.post(
            f"/api/closing/{session['closing_id']}/close",
            headers={**self.headers('manager'), 'Idempotency-Key': 'stale-close'},
            json={'expected_version': session['version'],
                  'source_token': session['source_token'],
                  'accepted_exceptions': self.accepted_exceptions(session)},
        )
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.get_json()['code'], 'closing_stale')
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM closing_snapshots').fetchone()[0], 0)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM cash_events WHERE event_type='removal'"
            ).fetchone()[0], 0)

    def test_late_paid_sale_keeps_snapshot_and_adds_linked_adjustment(self):
        session = self.submitted_session()
        close_body = {'expected_version': session['version'],
                      'source_token': session['source_token'],
                      'accepted_exceptions': self.accepted_exceptions(session)}
        closed = self.client.post(
            f"/api/closing/{session['closing_id']}/close",
            headers={**self.headers('manager'), 'Idempotency-Key': 'close-before-late'},
            json=close_body,
        )
        self.assertEqual(closed.status_code, 200, closed.get_json())
        late = self.create(
            self.sale_body(quantity=1, entry_mode='already_paid', reference='late-sale'),
            'late-create',
        ).get_json()
        late_post = self.post(late, 'late-post')
        self.assertEqual(late_post.status_code, 200, late_post.get_json())
        with closing(self.connect()) as con:
            snapshot = con.execute('SELECT snapshot_json FROM closing_snapshots').fetchone()[0]
            adjustments = con.execute(
                "SELECT source_type, source_id FROM closing_adjustments WHERE source_type='sale'"
            ).fetchall()
        self.assertNotIn(late['sale_id'], json.loads(snapshot)['sale_ids'])
        self.assertEqual([tuple(row) for row in adjustments], [('sale', str(late['sale_id']))])

    def test_build4_migrations_replay_without_relabeling_legacy_sales(self):
        with closing(self.connect()) as con:
            con.execute(
                """INSERT INTO daily_sales
                   (product_id, date, store, qty_sold, qty_pos, qty_cash, unit_price)
                   VALUES (?, '2026-09-06', 'DT', 7, 3, 4, NULL)""",
                (self.product_id,),
            )
            con.commit()
        db.migrate_db()
        db.migrate_db()
        with closing(self.connect()) as con:
            legacy = con.execute(
                """SELECT qty_sold, qty_pos, qty_cash, unit_price FROM daily_sales
                   WHERE date='2026-09-06'"""
            ).fetchone()
            migrations = con.execute(
                """SELECT COUNT(*) FROM _migrations WHERE name IN
                   ('create_sale_documents','add_sale_fresh_set_selection',
                    'create_sale_payments','create_payment_evidence','create_closing',
                    'harden_build4_financial_facts')"""
            ).fetchone()[0]
        self.assertEqual(tuple(legacy), (7, 3, 4, None))
        self.assertEqual(migrations, 6)
