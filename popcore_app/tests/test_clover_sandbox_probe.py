"""The sandbox order feed must not depend on repeated payment-detail reads."""
import base64
import json
import tempfile
import time
import unittest
from unittest.mock import patch

import requests

from scripts.clover_sandbox import create_app


def provider_response(payload, status=200):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()
    return response


class SandboxProbeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app({
            'PUBLIC_URL': 'http://127.0.0.1/clover-sandbox',
            'SESSION_SECRET': 's' * 32,
            'ADMIN_PASSWORD': 'p' * 32,
            'WEBHOOK_PATH_KEY': 'w' * 32,
            'DATA_DIR': self.directory.name,
            'CLOVER_MERCHANT_ID': 'M' * 13,
        })
        self.app.extensions['clover_save_tokens']({
            'access_token': 'sandbox-test-token',
            'access_token_expiration': time.time() + 3600,
        })
        self.client = self.app.test_client()
        credentials = base64.b64encode(('popcore:' + 'p' * 32).encode()).decode()
        self.headers = {'Authorization': 'Basic ' + credentials}
        self.orders = {'elements': [{'id': 'O' * 13, 'total': 2000, 'currency': 'CAD',
                                     'paymentState': 'PAID', 'payments': {'elements': [
                                         {'id': payment_id, 'amount': 1000, 'result': 'SUCCESS',
                                          'clientCreatedTime': 1, 'createdTime': 1,
                                          'modifiedTime': 1, 'taxAmount': 0,
                                          'device': {'id': 'D' * 13}, 'order': {'id': 'O' * 13},
                                          'tender': {'id': 'T' * 13}, 'employee': {'id': 'E' * 13}}
                                         for payment_id in ('A' * 13, 'B' * 13)]}}]}

    def test_repeated_reads_reuse_tender_label_without_rate_limiting_orders(self):
        payment_reads = 0

        def provider(method, url, **kwargs):
            nonlocal payment_reads
            if url.endswith('/orders'):
                return provider_response(self.orders)
            payment_reads += 1
            if payment_reads > 1:
                return provider_response({}, 429)
            return provider_response({'tender': {'id': 'T' * 13, 'label': 'E-transfer'}})

        with patch('scripts.clover_sandbox.requests.request', side_effect=provider):
            for _ in range(2):
                response = self.client.get('/clover-sandbox/orders', headers=self.headers)
                self.assertEqual(response.status_code, 200)
                payments = response.json['orders'][0]['payments']
                self.assertEqual([payment['tenderLabel'] for payment in payments],
                                 ['E-transfer', 'E-transfer'])
                self.assertEqual([payment['amount'] for payment in payments], [1000, 1000])
        self.assertEqual(payment_reads, 1)

    def test_tender_lookup_rate_limit_does_not_hide_orders(self):
        def provider(method, url, **kwargs):
            return provider_response(self.orders) if url.endswith('/orders') else provider_response({}, 429)

        with patch('scripts.clover_sandbox.requests.request', side_effect=provider):
            response = self.client.get('/clover-sandbox/orders', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['orders'][0]['payments']), 2)
        self.assertIsNone(response.json['orders'][0]['payments'][0]['tenderLabel'])

    def test_missing_tender_label_is_not_requested_every_refresh(self):
        payment_reads = 0

        def provider(method, url, **kwargs):
            nonlocal payment_reads
            if url.endswith('/orders'):
                return provider_response(self.orders)
            payment_reads += 1
            return provider_response({'tender': {'id': 'T' * 13}})

        with patch('scripts.clover_sandbox.requests.request', side_effect=provider):
            for _ in range(2):
                response = self.client.get('/clover-sandbox/orders', headers=self.headers)
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.json['orders'][0]['payments'][0]['tenderLabel'])
        self.assertEqual(payment_reads, 1)


if __name__ == '__main__':
    unittest.main()
