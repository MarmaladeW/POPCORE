"""Synthetic Clover events exercise real POPCORE ledger and image services."""
import io
import sys
from contextlib import closing
from pathlib import Path
import unittest
from unittest.mock import patch
from datetime import date

from PIL import Image
from werkzeug.datastructures import FileStorage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'popcore_app' / 'tests'))
from test_receiving import ReceivingFixture
from clover_rehearsal import Rehearsal, build_app


class RehearsalTests(ReceivingFixture):
    def setUp(self):
        super().setUp()
        self.demo = Rehearsal(self)

    def order(self, key='order-one', item='BOX-DEMO'):
        return self.demo.create_order({
            'id': key, 'item': item, 'quantity': 3,
            'original_cents': 4134, 'suggested_cents': 4000,
            'subtotal_cents': 3540, 'tax_cents': 460,
        })

    def start(self, order='order-one', key='attempt-one', tender='e_transfer', amount=4000):
        return self.demo.start_attempt({
            'id': key, 'order_id': order, 'tender': tender, 'amount_cents': amount,
        })

    def photo(self, attempt='attempt-one', actor='photo', content=None):
        if content is None:
            stream = io.BytesIO()
            Image.new('RGB', (10, 10), '#3466aa').save(stream, 'PNG')
            content = stream.getvalue()
        return self.demo.upload(attempt, FileStorage(stream=io.BytesIO(content), filename='test.png'), actor)

    def counts(self):
        with closing(self.connect()) as con:
            return tuple(con.execute(sql).fetchone()[0] for sql in (
                "SELECT quantity FROM inventory_balances WHERE disposition='saleable'",
                'SELECT count(*) FROM sale_documents',
                "SELECT count(*) FROM inventory_documents WHERE source_type='sale_document'",
                'SELECT count(*) FROM sale_payments',
            ))

    def test_completed_sale_and_duplicate_delivery_deduct_once(self):
        self.order(); self.start()
        self.assertEqual(self.counts(), (12, 0, 0, 0))
        self.demo.complete('attempt-one')
        self.demo.complete('attempt-one')
        self.demo.reconcile()
        self.assertEqual(self.counts(), (9, 1, 1, 1))
        task = self.demo.state('photo')['tasks'][0]
        self.assertEqual((task['amount_cents'], task['evidence_status']), (4000, 'missing'))
        with closing(self.connect()) as con:
            row = con.execute('SELECT subtotal_cents, source_tax_cents, collected_cents FROM sale_documents').fetchone()
        self.assertEqual(tuple(row), (3540, 460, 4000))

    def test_split_payment_does_not_post_until_fully_paid(self):
        self.order(); self.start(tender='cash', amount=1000)
        self.demo.complete('attempt-one')
        self.assertEqual(self.counts(), (12, 0, 0, 0))
        self.assertEqual(self.demo.state('photo')['tasks'], [])
        self.start(key='attempt-two', amount=3000)
        self.demo.complete('attempt-two')
        self.assertEqual(self.counts(), (9, 1, 1, 2))
        self.assertEqual(len(self.demo.state('photo')['tasks']), 1)

    def test_photo_before_completion_links_once_and_does_not_verify_money(self):
        self.order(); self.start()
        first = self.photo(); replay = self.photo()
        self.assertEqual(first['id'], replay['id'])
        self.demo.complete('attempt-one')
        self.demo.reconcile()
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM payment_evidence').fetchone()[0], 1)
            row = con.execute('SELECT uploader_sub FROM payment_evidence').fetchone()
            self.assertEqual(row[0], 'auth0|photo')
            self.assertEqual(con.execute('SELECT state FROM sale_payments').fetchone()[0], 'recorded')
        self.assertEqual(self.demo.state('photo')['tasks'][0]['evidence_status'], 'attached')

    def test_photo_after_payment_and_refund_return_remain_separate(self):
        self.order(); self.start(); self.demo.complete('attempt-one')
        self.photo(); self.photo()
        with closing(self.connect()) as con:
            sale_id = con.execute('SELECT id FROM sale_documents').fetchone()[0]
            self.assertEqual(con.execute('SELECT count(*) FROM payment_evidence').fetchone()[0], 1)
        path = f'/api/sale-documents/{sale_id}'
        detail = self.demo.api('GET', path, actor='manager')
        payment_id = detail['payments'][0]['id']
        self.demo.api('POST', f'/api/payments/{payment_id}/events', actor='manager', key='rehearsal-refund', body={
            'expected_version': detail['version'], 'event_type': 'refund',
            'amount_cents': 1000, 'reason': 'Synthetic money refund without goods return',
        })
        self.assertEqual(self.counts(), (9, 1, 1, 1))
        self.demo.reconcile()
        detail = self.demo.api('GET', path, actor='manager')
        self.assertEqual(detail['payments'][0]['effective_amount_cents'], 3000)
        self.demo.api('POST', path + '/returns', actor='manager', key='rehearsal-return', body={
            'expected_version': detail['version'], 'line_no': 1, 'quantity': 1,
            'disposition': 'saleable', 'reason': 'Synthetic physical receipt of one returned item',
        })
        self.demo.reconcile()
        self.assertEqual(self.counts(), (10, 1, 1, 1))

    def test_shortage_retains_paid_fact_and_cash_card_create_no_photo_task(self):
        body = self.order()
        self.start(tender='card'); self.demo.complete('attempt-one')
        self.order('cash-order'); self.start('cash-order', 'cash-attempt', tender='cash')
        self.demo.complete('cash-attempt')
        self.assertEqual(self.demo.state('photo')['tasks'], [])
        body.update(id='short-order', quantity=20)
        self.demo.create_order(body)
        self.start('short-order', 'short-attempt'); self.demo.complete('short-attempt')
        self.assertEqual(self.counts(), (6, 3, 2, 3))
        with closing(self.connect()) as con:
            last = con.execute('SELECT financial_status, allocation_status FROM sale_documents ORDER BY id DESC LIMIT 1').fetchone()
            self.assertEqual(tuple(last), ('recorded', 'pending'))

    def test_switching_tender_keeps_old_photo_off_new_payment(self):
        self.order(); self.start(); self.photo()
        self.start(key='attempt-two', tender='alipay')
        self.assertEqual(self.demo.state('photo')['tasks'][0]['id'], 'attempt-two')
        with self.assertRaises(ValueError):
            self.demo.complete('attempt-one')
        with self.assertRaises(ValueError):
            self.photo()
        self.demo.complete('attempt-two')
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM payment_evidence').fetchone()[0], 0)
        self.assertEqual(self.counts(), (9, 1, 1, 1))

    def test_equal_value_orders_use_exact_attempt_identity(self):
        self.order(); self.start()
        self.order('order-two'); self.start('order-two', 'attempt-two')
        self.photo('attempt-two')
        self.demo.complete('attempt-one'); self.demo.complete('attempt-two')
        tasks = {t['id']: t for t in self.demo.state('photo')['tasks']}
        self.assertEqual(tasks['attempt-one']['evidence_status'], 'missing')
        self.assertEqual(tasks['attempt-two']['evidence_status'], 'attached')
        self.assertEqual(self.counts(), (6, 2, 2, 2))

    def test_access_and_invalid_images_do_not_save_evidence(self):
        self.order(); self.start()
        self.assertEqual(self.demo.state('other_store')['tasks'], [])
        self.assertEqual(self.demo.state('other_staff')['tasks'], [])
        with self.assertRaises(PermissionError): self.photo(actor='other_store')
        with self.assertRaises(PermissionError): self.photo(actor='other_staff')
        with self.assertRaises(ValueError): self.photo(content=b'<svg>not a photo</svg>')
        self.assertEqual(self.demo.state('photo')['tasks'][0]['evidence_status'], 'missing')

    def test_cancelled_unpaid_order_does_not_post(self):
        self.order(); self.start(); self.demo.cancel('attempt-one')
        self.demo.reconcile()
        self.assertEqual(self.counts(), (12, 0, 0, 0))
        self.assertEqual(self.demo.state('photo')['tasks'], [])

    def test_reopen_adapter_resumes_after_financial_post_before_payments(self):
        self.order(); self.start()
        original = self.demo.api
        def fail_payment(method, path, **kwargs):
            if path.endswith('/payments'): raise ValueError('Simulated interruption')
            return original(method, path, **kwargs)
        self.demo.api = fail_payment
        with self.assertRaisesRegex(ValueError, 'interruption'):
            self.demo.complete('attempt-one')
        self.assertEqual(self.counts(), (9, 1, 1, 0))
        self.demo = Rehearsal(self)
        self.demo.reconcile()
        self.assertEqual(self.counts(), (9, 1, 1, 1))

    def test_unmapped_paid_sale_retained_without_deducting_stock(self):
        self.order(item='UNKNOWN'); self.start(); self.demo.complete('attempt-one')
        self.assertEqual(self.counts(), (12, 1, 0, 1))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT allocation_status FROM sale_documents').fetchone()[0], 'pending')

    def test_retry_after_midnight_keeps_source_business_date(self):
        with patch('clover_rehearsal.date') as clock:
            clock.today.return_value = date(2026, 9, 14)
            self.order(); self.start()
        api = self.demo.api
        def interrupted(method, path, **kwargs):
            if method == 'POST' and path == '/api/sale-documents':
                raise ValueError('Interrupted before sale creation')
            return api(method, path, **kwargs)
        self.demo.api = interrupted
        with self.assertRaises(ValueError): self.demo.complete('attempt-one')
        self.demo.api = api
        with patch('clover_rehearsal.date') as clock:
            clock.today.return_value = date(2026, 9, 15)
            self.demo.reconcile()
            self.order()  # A lost create response also replays the original source facts.
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT business_date FROM sale_documents').fetchone()[0], '2026-09-14')

    def test_invalid_money_and_reused_identity_are_rejected(self):
        self.order()
        for value in (-1, 0, True, 40.5, '4000'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.start(amount=value)
        self.start()
        with self.assertRaises(ValueError): self.start(amount=3000)
        with self.assertRaises(ValueError): self.order(item='UNKNOWN')
        self.assertEqual(self.counts(), (12, 0, 0, 0))

    def test_web_wrapper_requires_loopback_and_csrf(self):
        client = build_app(self.demo).test_client()
        self.assertEqual(client.get('/', headers={'Host': 'attacker.invalid'}).status_code, 403)
        self.assertEqual(client.get('/', environ_base={'REMOTE_ADDR': '10.0.0.1'}).status_code, 403)
        self.assertEqual(client.post('/action/order', json={}).status_code, 403)
        response = client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Simulated Clover', response.data)
        with client.session_transaction() as cookie:
            csrf = cookie['csrf']
        response = client.post('/action/order', json={
            'id': 'http-order', 'item': 'BOX-DEMO', 'quantity': 3,
            'original_cents': 4134, 'suggested_cents': 4000,
            'subtotal_cents': 3540, 'tax_cents': 460,
        }, headers={'X-Rehearsal-CSRF': csrf})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get('/state?actor=staff').get_json()['orders'][0]['id'], 'http-order')
        self.assertEqual(client.get('/api/sale-documents/1').status_code, 404)


if __name__ == '__main__':
    unittest.main()
