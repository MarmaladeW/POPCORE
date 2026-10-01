"""The sandbox order feed must not depend on repeated payment-detail reads."""
import base64
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
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

    def test_device_snapshots_are_paired_ordered_and_not_replaced_by_lagging_cloud(self):
        app = create_app({**self.app.config, 'PUBLIC_URL': 'http://127.0.0.1/clover-sandbox',
                          'SESSION_SECRET': 's' * 32, 'ADMIN_PASSWORD': 'p' * 32,
                          'WEBHOOK_PATH_KEY': 'w' * 32, 'DATA_DIR': self.directory.name,
                          'CLOVER_MERCHANT_ID': 'M' * 13,
                          'DEVICE_BRIDGE_SECRET': 'b' * 32, 'DEVICE_BRIDGE_ID': 'device-1'})
        client = app.test_client()
        path = '/clover-sandbox/device-snapshot'
        order = {'id': 'O' * 13, 'currency': 'CAD', 'createdTime': 1790000000000,
                 'total': 7000, 'items': [
                     {'id': str(i) * 13, 'name': 'Test item', 'price': 1000}
                     for i in range(7)], 'discounts': []}

        def submit(sequence, value=order, **overrides):
            body = {'merchantId': 'M' * 13, 'deviceId': 'device-1', 'sequence': sequence,
                    'order': value, **overrides}
            return client.post(path, json=body,
                               headers={'Authorization': 'Bearer ' + 'b' * 32})

        self.assertEqual(client.post(path, json={}).status_code, 401)
        self.assertEqual(submit(1, deviceId='other-device').status_code, 403)
        self.assertEqual(submit(1, merchantId='N' * 13).status_code, 403)
        self.assertEqual(submit(1).status_code, 200)
        self.assertEqual(submit(1).status_code, 200)
        self.assertEqual(submit(1, {**order, 'items': order['items'][:1]}).status_code, 409)
        self.assertEqual(submit(0).status_code, 400)
        with patch('scripts.clover_sandbox.requests.request', return_value=provider_response({
                'elements': [{'id': 'O' * 13, 'currency': 'CAD', 'total': 1000,
                              'lineItems': {'elements': [order['items'][0]]}}]})):
            rows = client.get('/clover-sandbox/orders', headers=self.headers).json['orders']
        self.assertEqual(rows[0]['source'], 'device')
        self.assertEqual(len(rows[0]['items']), 7)
        self.assertEqual(rows[0]['total'], 7000)
        removed = {**order, 'total': 6000, 'items': order['items'][:6]}
        self.assertEqual(submit(2, removed).status_code, 200)
        self.assertEqual(submit(1).status_code, 409)
        with patch('scripts.clover_sandbox.requests.request', return_value=provider_response({
                'elements': [{'id': 'O' * 13, 'currency': 'CAD', 'total': 1000,
                              'lineItems': {'elements': [order['items'][0]]}}]})), \
                patch('scripts.clover_sandbox.time.monotonic', return_value=999999999):
            rows = client.get('/clover-sandbox/orders', headers=self.headers).json['orders']
        self.assertEqual([item['id'] for item in rows[0]['items']],
                         [item['id'] for item in removed['items']])

    def test_device_cloud_reconciliation_needs_matching_items_and_total(self):
        app = create_app({'PUBLIC_URL': 'http://127.0.0.1/clover-sandbox',
                          'SESSION_SECRET': 's' * 32, 'ADMIN_PASSWORD': 'p' * 32,
                          'WEBHOOK_PATH_KEY': 'w' * 32, 'DATA_DIR': self.directory.name,
                          'CLOVER_MERCHANT_ID': 'M' * 13,
                          'DEVICE_BRIDGE_SECRET': 'b' * 32, 'DEVICE_BRIDGE_ID': 'device-1'})
        client = app.test_client()
        item = {'id': 'I' * 13, 'name': 'Test item', 'price': 1000, 'unitQty': 1000}
        order = {'id': 'O' * 13, 'currency': 'CAD', 'createdTime': 1790000000000,
                 'total': 1000, 'items': [item], 'discounts': []}
        client.post('/clover-sandbox/device-snapshot', json={
            'merchantId': 'M' * 13, 'deviceId': 'device-1', 'sequence': 1, 'order': order},
            headers={'Authorization': 'Bearer ' + 'b' * 32})
        cloud = {'elements': [{'id': 'O' * 13, 'currency': 'CAD', 'total': 1000,
                              'paymentState': 'PAID', 'lineItems': {'elements': [item]},
                              'payments': {'elements': [{'id': 'P' * 13, 'amount': 1000,
                                                        'result': 'SUCCESS'}]}}]}
        with patch('scripts.clover_sandbox.requests.request', return_value=provider_response(cloud)):
            for _ in range(50):
                row = client.get('/clover-sandbox/orders', headers=self.headers).json['orders'][0]
                if row['source'] == 'cloud':
                    break
                time.sleep(.01)
        self.assertEqual(row['source'], 'cloud')
        self.assertEqual(row['paymentState'], 'PAID')
        self.assertEqual(len(row['payments']), 1)
        cloud['elements'][0]['total'] = 2000
        cloud['elements'][0]['lineItems']['elements'].append(
            {'id': 'J' * 13, 'name': 'Second item', 'price': 1000, 'unitQty': 1000})
        with patch('scripts.clover_sandbox.requests.request', return_value=provider_response(cloud)), \
                patch('scripts.clover_sandbox.time.monotonic', return_value=999999999):
            for _ in range(50):
                row = client.get('/clover-sandbox/orders', headers=self.headers).json['orders'][0]
                if len(row['items']) == 2:
                    break
                time.sleep(.01)
        self.assertEqual(row['source'], 'cloud')
        self.assertEqual(len(row['items']), 2)

    def test_unknown_device_total_reconciles_only_matching_cloud_items(self):
        app = create_app({'PUBLIC_URL': 'http://127.0.0.1/clover-sandbox',
                          'SESSION_SECRET': 's' * 32, 'ADMIN_PASSWORD': 'p' * 32,
                          'WEBHOOK_PATH_KEY': 'w' * 32, 'DATA_DIR': self.directory.name,
                          'CLOVER_MERCHANT_ID': 'M' * 13,
                          'DEVICE_BRIDGE_SECRET': 'b' * 32, 'DEVICE_BRIDGE_ID': 'device-1'})
        app.extensions['clover_save_tokens']({'access_token': 'sandbox-test-token',
                                               'access_token_expiration': time.time() + 3600})
        client = app.test_client()
        item = {'id': 'I' * 13, 'name': 'Test item', 'price': 1000, 'unitQty': 1000}
        response = client.post('/clover-sandbox/device-snapshot', json={
            'merchantId': 'M' * 13, 'deviceId': 'device-1', 'sequence': 1,
            'order': {'id': 'O' * 13, 'currency': 'CAD', 'createdTime': 1790000000000,
                      'total': None, 'items': [item], 'discounts': []}},
            headers={'Authorization': 'Bearer ' + 'b' * 32})
        self.assertEqual(response.status_code, 200)
        cloud = {'elements': [{'id': 'O' * 13, 'currency': 'CAD', 'total': 1000,
                               'paymentState': 'PAID', 'lineItems': {'elements': [item]},
                               'payments': {'elements': [{'id': 'P' * 13, 'amount': 1000,
                                                         'result': 'SUCCESS'}]}}]}
        with patch('scripts.clover_sandbox.requests.request', return_value=provider_response(cloud)):
            for _ in range(50):
                row = client.get('/clover-sandbox/orders', headers=self.headers).json['orders'][0]
                if row['source'] == 'cloud':
                    break
                time.sleep(.01)
        self.assertEqual(row['source'], 'cloud')
        self.assertEqual(row['total'], 1000)
        self.assertEqual(row['paymentState'], 'PAID')

    def test_device_items_remain_readable_during_cloud_rate_limit(self):
        app = create_app({'PUBLIC_URL': 'http://127.0.0.1/clover-sandbox',
                          'SESSION_SECRET': 's' * 32, 'ADMIN_PASSWORD': 'p' * 32,
                          'WEBHOOK_PATH_KEY': 'w' * 32, 'DATA_DIR': self.directory.name,
                          'CLOVER_MERCHANT_ID': 'M' * 13,
                          'DEVICE_BRIDGE_SECRET': 'b' * 32, 'DEVICE_BRIDGE_ID': 'device-1'})
        client = app.test_client()
        app.extensions['clover_save_tokens']({'access_token': 'sandbox-test-token',
                                               'access_token_expiration': time.time() + 3600})
        order = {'id': 'O' * 13, 'currency': 'CAD', 'createdTime': 1790000000000,
                 'total': 1000, 'items': [{'id': 'I' * 13, 'name': 'Test item', 'price': 1000}],
                 'discounts': []}
        client.post('/clover-sandbox/device-snapshot', json={
            'merchantId': 'M' * 13, 'deviceId': 'device-1', 'sequence': 1, 'order': order},
            headers={'Authorization': 'Bearer ' + 'b' * 32})
        with patch('scripts.clover_sandbox.requests.request', return_value=provider_response({}, 429)):
            response = client.get('/clover-sandbox/orders', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['cloud_unavailable'])
        self.assertEqual(response.json['orders'][0]['source'], 'device')
        self.assertEqual(response.json['orders'][0]['items'][0]['id'], 'I' * 13)

    def test_fresh_device_snapshot_does_not_wait_for_slow_cloud(self):
        app = create_app({'PUBLIC_URL': 'http://127.0.0.1/clover-sandbox',
                          'SESSION_SECRET': 's' * 32, 'ADMIN_PASSWORD': 'p' * 32,
                          'WEBHOOK_PATH_KEY': 'w' * 32, 'DATA_DIR': self.directory.name,
                          'CLOVER_MERCHANT_ID': 'M' * 13,
                          'DEVICE_BRIDGE_SECRET': 'b' * 32, 'DEVICE_BRIDGE_ID': 'device-1'})
        app.extensions['clover_save_tokens']({'access_token': 'sandbox-test-token',
                                               'access_token_expiration': time.time() + 3600})
        client = app.test_client()
        client.post('/clover-sandbox/device-snapshot', json={
            'merchantId': 'M' * 13, 'deviceId': 'device-1', 'sequence': 1,
            'order': {'id': 'O' * 13, 'currency': 'CAD', 'total': 1000,
                      'createdTime': 1790000000000, 'discounts': [],
                      'items': [{'id': 'I' * 13, 'name': 'Test item', 'price': 1000}]}},
            headers={'Authorization': 'Bearer ' + 'b' * 32})
        entered, release = Event(), Event()

        def slow_provider(method, url, **kwargs):
            entered.set()
            release.wait(2)
            return provider_response({'elements': []})

        with patch('scripts.clover_sandbox.requests.request', side_effect=slow_provider):
            try:
                started = time.monotonic()
                response = client.get('/clover-sandbox/orders', headers=self.headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['orders'][0]['source'], 'device')
                self.assertLess(time.monotonic() - started, .3)
                self.assertTrue(entered.wait(1))
            finally:
                release.set()

    def test_concurrent_readers_share_snapshot_and_next_scan_replaces_it(self):
        entered, release = Event(), Event()
        provider_reads = 0

        def provider(method, url, **kwargs):
            nonlocal provider_reads
            provider_reads += 1
            entered.set()
            release.wait(2)
            clock.return_value += 1.1
            wall_clock.return_value += 1.1
            count = 1 if provider_reads == 1 else 3
            return provider_response({'elements': [{
                'id': 'O' * 13, 'total': count * 1000, 'currency': 'CAD',
                'lineItems': {'elements': [{'id': str(i) * 13, 'name': 'Test item', 'price': 1000}
                                         for i in range(count)]},
            }]})

        def read():
            with self.app.test_client() as client:
                return client.get('/clover-sandbox/orders', headers=self.headers).json

        with patch('scripts.clover_sandbox.time.monotonic', return_value=100) as clock, \
                patch('scripts.clover_sandbox.time.time', return_value=1000) as wall_clock, \
                patch('scripts.clover_sandbox.requests.request', side_effect=provider), \
                ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(read)
            self.assertTrue(entered.wait(1))
            second = executor.submit(read)
            release.set()
            initial, shared = first.result(), second.result()
            self.assertEqual(len(initial['orders'][0]['items']), 1)
            self.assertEqual(initial, shared)
            wall_clock.return_value = 1001.5
            self.assertEqual(read()['fetched_at'], 1001.1)
            self.assertEqual(provider_reads, 1)
            clock.return_value = 102.2
            wall_clock.return_value = 1002.2
            latest = read()
            self.assertEqual(len(latest['orders'][0]['items']), 3)
            self.assertEqual(latest['orders'][0]['total'], 3000)
            self.assertEqual(provider_reads, 2)

    def test_rate_limit_cooldown_is_shared_and_honors_retry_after(self):
        limited = provider_response({}, 429)
        limited.headers['Retry-After'] = '7'
        with patch('scripts.clover_sandbox.time.monotonic', return_value=100) as clock, \
                patch('scripts.clover_sandbox.requests.request', side_effect=[
                    limited, provider_response({'elements': []})]) as provider:
            response = self.client.get('/clover-sandbox/orders', headers=self.headers)
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.headers.get('Retry-After'), '7')
            clock.return_value = 106
            retry = self.client.get('/clover-sandbox/review', headers=self.headers)
            self.assertEqual(retry.status_code, 502)
            self.assertEqual(provider.call_count, 1)
            clock.return_value = 107.01
            recovered = self.client.get('/clover-sandbox/orders', headers=self.headers)
            self.assertEqual(recovered.status_code, 200)
            self.assertEqual(provider.call_count, 2)

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
            for _ in range(20):
                response = self.client.get('/clover-sandbox/orders', headers=self.headers)
                self.assertEqual(response.status_code, 200)
                payments = response.json['orders'][0]['payments']
                if all(payment['tenderLabel'] == 'E-transfer' for payment in payments):
                    break
                time.sleep(.02)
            self.assertEqual([payment['tenderLabel'] for payment in payments], ['E-transfer', 'E-transfer'])
            self.assertEqual([payment['amount'] for payment in payments], [1000, 1000])
            self.client.get('/clover-sandbox/orders', headers=self.headers)
        self.assertEqual(payment_reads, 1)

    def test_tender_lookup_rate_limit_does_not_hide_orders(self):
        lookup_done = Event()
        def provider(method, url, **kwargs):
            if url.endswith('/orders'):
                return provider_response(self.orders)
            lookup_done.set()
            return provider_response({}, 429)

        with patch('scripts.clover_sandbox.requests.request', side_effect=provider):
            response = self.client.get('/clover-sandbox/orders', headers=self.headers)
            self.assertTrue(lookup_done.wait(1))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['orders'][0]['payments']), 2)
        self.assertIsNone(response.json['orders'][0]['payments'][0]['tenderLabel'])

    def test_missing_tender_label_is_not_requested_every_refresh(self):
        payment_reads = 0
        lookup_done = Event()

        def provider(method, url, **kwargs):
            nonlocal payment_reads
            if url.endswith('/orders'):
                return provider_response(self.orders)
            payment_reads += 1
            lookup_done.set()
            return provider_response({'tender': {'id': 'T' * 13}})

        with patch('scripts.clover_sandbox.requests.request', side_effect=provider):
            for _ in range(2):
                response = self.client.get('/clover-sandbox/orders', headers=self.headers)
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.json['orders'][0]['payments'][0]['tenderLabel'])
            self.assertTrue(lookup_done.wait(1))
            self.client.get('/clover-sandbox/orders', headers=self.headers)
        self.assertEqual(payment_reads, 1)

    def test_slow_payment_label_lookup_does_not_delay_new_orders(self):
        release = Event()

        def provider(method, url, **kwargs):
            if url.endswith('/orders'):
                return provider_response(self.orders)
            release.wait(1)
            return provider_response({'tender': {'id': 'T' * 13, 'label': 'E-transfer'}})

        with patch('scripts.clover_sandbox.requests.request', side_effect=provider):
            try:
                started = time.monotonic()
                response = self.client.get('/clover-sandbox/orders', headers=self.headers)
                self.assertEqual(response.status_code, 200)
                self.assertLess(time.monotonic() - started, .3)
            finally:
                release.set()
            for _ in range(20):
                response = self.client.get('/clover-sandbox/orders', headers=self.headers)
                if response.json['orders'][0]['payments'][0]['tenderLabel'] == 'E-transfer':
                    break
                time.sleep(.02)
            self.assertEqual(response.json['orders'][0]['payments'][0]['tenderLabel'], 'E-transfer')


if __name__ == '__main__':
    unittest.main()
