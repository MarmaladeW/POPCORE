from contextlib import closing

from test_sales_posting import SalePostingTests


class PaymentTests(SalePostingTests):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)",
                (self.store_id,),
            )
            con.commit()

    def posted_sale(self, reference='payment-sale'):
        draft = self.create(self.sale_body(reference=reference), f'{reference}-create').get_json()
        return self.post(draft, f'{reference}-post').get_json()

    def add_payments(self, sale, payments, key='payments-add'):
        return self.client.post(
            f"/api/sale-documents/{sale['sale_id']}/payments",
            headers={**self.headers(), 'Idempotency-Key': key},
            json={'expected_version': sale['version'], 'payments': payments},
        )

    def test_five_tenders_and_split_total_remain_separate(self):
        sale = self.posted_sale()
        payments = [
            {'tender': 'cash', 'amount_cents': 1000},
            {'tender': 'card', 'amount_cents': 2000},
            {'tender': 'e_transfer', 'amount_cents': 500},
            {'tender': 'wechat', 'amount_cents': 500},
            {'tender': 'alipay', 'amount_cents': 520},
        ]
        response = self.add_payments(sale, payments)
        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        self.assertEqual(body['payment_total_cents'], 4520)
        self.assertEqual(body['payment_difference_cents'], 0)
        self.assertEqual(
            [(item['tender'], item['amount_cents']) for item in body['payments']],
            [('cash', 1000), ('card', 2000), ('e_transfer', 500),
             ('wechat', 500), ('alipay', 520)],
        )

    def test_unknown_amount_is_not_coerced_to_zero(self):
        sale = self.posted_sale('unknown-payment')
        response = self.add_payments(sale, [
            {'tender': 'cash', 'amount_cents': None},
            {'tender': 'card', 'amount_cents': 4520},
        ], 'unknown-payment-add')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIsNone(response.get_json()['payment_total_cents'])
        self.assertIsNone(response.get_json()['payment_difference_cents'])
        self.assertIsNone(response.get_json()['payments'][0]['effective_amount_cents'])

    def test_review_and_refund_are_append_only_and_bounded(self):
        sale = self.posted_sale('review-payment')
        detail = self.add_payments(sale, [{
            'tender': 'e_transfer', 'amount_cents': 4000,
            'source_system': 'bank', 'source_account': 'DT',
            'source_reference': 'etransfer-1',
        }], 'review-add').get_json()
        payment_id = detail['payments'][0]['id']
        verified = self.client.post(
            f'/api/payments/{payment_id}/verify',
            headers={**self.headers('manager'), 'Idempotency-Key': 'verify-1'},
            json={'expected_version': detail['version'], 'reason': 'Matched deposit'},
        )
        self.assertEqual(verified.status_code, 200, verified.get_json())
        refund = self.client.post(
            f'/api/payments/{payment_id}/events',
            headers={**self.headers('manager'), 'Idempotency-Key': 'refund-1'},
            json={'expected_version': verified.get_json()['version'],
                  'event_type': 'refund', 'amount_cents': 1000,
                  'reason': 'Recorded processor refund'},
        )
        self.assertEqual(refund.status_code, 200, refund.get_json())
        excessive = self.client.post(
            f'/api/payments/{payment_id}/events',
            headers={**self.headers('manager'), 'Idempotency-Key': 'refund-too-large'},
            json={'expected_version': refund.get_json()['version'],
                  'event_type': 'refund', 'amount_cents': 3001,
                  'reason': 'Too much'},
        )
        self.assertEqual(excessive.status_code, 409, excessive.get_json())
        detail = self.client.get(
            f"/api/sale-documents/{sale['sale_id']}", headers=self.headers('manager')
        ).get_json()
        self.assertEqual(detail['payments'][0]['effective_amount_cents'], 3000)
        self.assertEqual([event['event_type'] for event in detail['payments'][0]['events']],
                         ['verify', 'refund'])

    def test_correction_and_refund_require_a_positive_amount(self):
        sale = self.posted_sale('missing-event-amount')
        detail = self.add_payments(sale, [
            {'tender': 'card', 'amount_cents': 4520},
        ], 'missing-event-payment').get_json()
        payment_id = detail['payments'][0]['id']
        for event_type in ('correction', 'refund'):
            with self.subTest(event_type=event_type):
                body = {
                    'expected_version': detail['version'],
                    'event_type': event_type,
                    'reason': 'Amount omitted',
                }
                if event_type == 'correction':
                    body['direction'] = 'increase'
                response = self.client.post(
                    f'/api/payments/{payment_id}/events',
                    headers={**self.headers('manager'),
                             'Idempotency-Key': f'missing-{event_type}'},
                    json=body,
                )
                self.assertEqual(response.status_code, 400, response.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM payment_events').fetchone()[0], 0)

    def test_physical_return_posts_stock_without_changing_payment(self):
        sale = self.posted_sale('return-sale')
        paid = self.add_payments(sale, [
            {'tender': 'card', 'amount_cents': 4520},
        ], 'return-payment').get_json()
        returned = self.client.post(
            f"/api/sale-documents/{sale['sale_id']}/returns",
            headers={**self.headers('manager'), 'Idempotency-Key': 'return-1'},
            json={'expected_version': paid['version'], 'line_no': 1, 'quantity': 1,
                  'disposition': 'saleable', 'reason': 'Unopened return'},
        )
        self.assertEqual(returned.status_code, 200, returned.get_json())
        with closing(self.connect()) as con:
            balance = con.execute(
                """SELECT quantity FROM inventory_balances
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (self.product_id, self.floor),
            ).fetchone()[0]
            payment = con.execute(
                'SELECT amount_cents FROM sale_payments WHERE sale_id=?',
                (sale['sale_id'],),
            ).fetchone()[0]
        self.assertEqual((balance, payment), (4, 4520))
