"""The Clover connector: order feed, device bridge, exact totals and POPCORE writes."""
import base64
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import requests

from scripts.clover_sandbox import Unsupported, clover_calc, create_app


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

    def test_device_line_item_codes_are_kept_and_validated(self):
        app = create_app({**self.app.config, 'PUBLIC_URL': 'http://127.0.0.1/clover-sandbox',
                          'SESSION_SECRET': 's' * 32, 'ADMIN_PASSWORD': 'p' * 32,
                          'WEBHOOK_PATH_KEY': 'w' * 32, 'DATA_DIR': self.directory.name,
                          'CLOVER_MERCHANT_ID': 'M' * 13,
                          'DEVICE_BRIDGE_SECRET': 'b' * 32, 'DEVICE_BRIDGE_ID': 'device-1'})
        client = app.test_client()
        item = {'id': '1' * 13, 'name': 'Scanned item', 'price': 1000,
                'itemCode': '00123', 'itemId': 'I' * 13}
        order = {'id': 'O' * 13, 'currency': 'CAD', 'createdTime': 1790000000000,
                 'total': 1000, 'items': [item], 'discounts': []}

        def submit(sequence, value):
            return client.post('/clover-sandbox/device-snapshot', json={
                'merchantId': 'M' * 13, 'deviceId': 'device-1', 'sequence': sequence, 'order': value},
                headers={'Authorization': 'Bearer ' + 'b' * 32})

        self.assertEqual(submit(1, order).status_code, 200)
        with patch('scripts.clover_sandbox.requests.request',
                   return_value=provider_response({'elements': []})):
            rows = client.get('/clover-sandbox/orders', headers=self.headers).json['orders']
        self.assertEqual(rows[0]['items'][0]['itemCode'], '00123')
        self.assertEqual(rows[0]['items'][0]['itemId'], 'I' * 13)
        self.assertEqual(submit(2, {**order, 'items': [{**item, 'itemCode': 'x' * 81}]}).status_code, 400)
        self.assertEqual(submit(2, {**order, 'items': [{**item, 'itemId': 'short'}]}).status_code, 400)
        self.assertEqual(submit(2, {**order, 'items': [{**item, 'itemCode': None, 'itemId': None}]}).status_code, 200)

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

    def test_one_order_read_keeps_the_fresher_device_items(self):
        app = create_app({'PUBLIC_URL': 'http://127.0.0.1/clover-sandbox',
                          'SESSION_SECRET': 's' * 32, 'ADMIN_PASSWORD': 'p' * 32,
                          'WEBHOOK_PATH_KEY': 'w' * 32, 'DATA_DIR': self.directory.name,
                          'CLOVER_MERCHANT_ID': 'M' * 13,
                          'DEVICE_BRIDGE_SECRET': 'b' * 32, 'DEVICE_BRIDGE_ID': 'device-1'})
        app.extensions['clover_save_tokens']({'access_token': 'token', 'access_token_expiration': time.time() + 3600})
        client = app.test_client()
        first = {'id': 'I' * 13, 'name': 'Test item', 'price': 1000, 'unitQty': 1000}
        second = {'id': 'J' * 13, 'name': 'Second item', 'price': 1000, 'unitQty': 1000}
        client.post('/clover-sandbox/device-snapshot', json={
            'merchantId': 'M' * 13, 'deviceId': 'device-1', 'sequence': 1,
            'order': {'id': 'O' * 13, 'currency': 'CAD', 'createdTime': 1790000000000, 'total': 2000,
                      'items': [first, second], 'discounts': []}},
            headers={'Authorization': 'Bearer ' + 'b' * 32})
        cloud = {'id': 'O' * 13, 'currency': 'CAD', 'total': 1000, 'paymentState': 'OPEN',
                 'createdTime': 1790000000000, 'lineItems': {'elements': [first]}, 'payments': {'elements': []}}

        def clover(method, url, **kwargs):
            if url.endswith('/line_items'):
                return provider_response({'elements': [{**first, 'taxRates': {'elements': TAX}}]})
            if url.endswith('/tenders'):
                return provider_response({'elements': []})
            return provider_response(cloud)
        url = '/clover-sandbox/orders/' + 'O' * 13
        # Clover's cloud copy still has one item; the device already has two, so the device copy wins,
        # exactly as in the order list, and nothing flips back to the shorter cloud order.
        with patch('scripts.clover_sandbox.requests.request', side_effect=clover):
            ahead = client.get(url, headers=self.headers).json
        self.assertEqual((ahead['order']['source'], len(ahead['order']['items']), ahead['pricing']['verified']),
                         ('device', 2, False))
        # A device copy nobody re-sent for over 30 s no longer hides the cloud order.
        later = time.time() + 60
        with patch('scripts.clover_sandbox.requests.request', side_effect=clover), \
                patch('scripts.clover_sandbox.time.time', return_value=later):
            stale = client.get(url, headers=self.headers).json
        self.assertEqual((stale['order']['source'], len(stale['order']['items'])), ('cloud', 1))
        # Once the cloud catches up (after the connector's 2 s read cache), the cloud copy is used again.
        cloud['lineItems']['elements'].append(second)
        cloud['total'] = 2000
        with patch('scripts.clover_sandbox.requests.request', side_effect=clover), \
                patch('scripts.clover_sandbox.time.monotonic', return_value=999999999):
            caught_up = client.get(url, headers=self.headers).json
        self.assertEqual((caught_up['order']['source'], len(caught_up['order']['items'])), ('cloud', 2))

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
            retry = self.client.get('/clover-sandbox/orders/' + 'O' * 13, headers=self.headers)
            self.assertEqual(retry.status_code, 502)
            self.assertEqual(provider.call_count, 1)
            clock.return_value = 107.01
            recovered = self.client.get('/clover-sandbox/orders', headers=self.headers)
            self.assertEqual(recovered.status_code, 200)
            self.assertEqual(provider.call_count, 2)

    def test_tender_labels_come_from_one_cached_list(self):
        tender_reads = 0

        def provider(method, url, **kwargs):
            nonlocal tender_reads
            if url.endswith('/orders'):
                return provider_response(self.orders)
            tender_reads += 1
            if tender_reads > 1:
                return provider_response({}, 429)
            return provider_response({'elements': [{'id': 'T' * 13, 'label': 'E-transfer'}]})

        with patch('scripts.clover_sandbox.requests.request', side_effect=provider):
            for _ in range(50):
                response = self.client.get('/clover-sandbox/orders', headers=self.headers)
                self.assertEqual(response.status_code, 200)
                payments = response.json['orders'][0]['payments']
                if all(payment['tenderLabel'] == 'E-transfer' for payment in payments):
                    break
                time.sleep(.02)
            self.assertEqual([payment['tenderLabel'] for payment in payments], ['E-transfer', 'E-transfer'])
            self.assertEqual([payment['amount'] for payment in payments], [1000, 1000])
            self.client.get('/clover-sandbox/orders', headers=self.headers)
        self.assertEqual(tender_reads, 1)

    def test_tender_list_failures_never_hide_or_delay_orders(self):
        release = Event()

        def provider(method, url, **kwargs):
            if url.endswith('/orders'):
                return provider_response(self.orders)
            release.wait(1)
            return provider_response({}, 429)

        with patch('scripts.clover_sandbox.requests.request', side_effect=provider):
            try:
                started = time.monotonic()
                response = self.client.get('/clover-sandbox/orders', headers=self.headers)
                self.assertLess(time.monotonic() - started, .3)
            finally:
                release.set()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['orders'][0]['payments']), 2)
        self.assertIsNone(response.json['orders'][0]['payments'][0]['tenderLabel'])

    def test_one_malformed_order_does_not_hide_the_others(self):
        bad = {'id': 'X' * 13, 'total': 100, 'currency': 'CAD',
               'payments': {'elements': [{'id': 'short', 'amount': 100, 'result': 'SUCCESS'}]}}
        good = {'id': 'O' * 13, 'total': 100, 'currency': 'CAD', 'lineItems': {'elements': []}}
        with patch('scripts.clover_sandbox.requests.request',
                   return_value=provider_response({'elements': [bad, good]})):
            response = self.client.get('/clover-sandbox/orders', headers=self.headers)
        self.assertEqual([row['id'] for row in response.json['orders']], ['O' * 13])


TAX = [{'id': 'HST000000013', 'rate': 1300000}]


class CloverCalcTests(unittest.TestCase):
    """Clover's documented order total, reproduced exactly."""

    def test_matches_clover_on_the_sandbox_and_documented_examples(self):
        lines = [{'price': 1770, 'taxRates': TAX}, {'price': 1999, 'taxRates': TAX}]
        self.assertEqual(clover_calc(lines, []), (4259, 490, 3769))
        self.assertEqual(clover_calc(lines, [], 52), (4200, 483, 3717))
        self.assertEqual(clover_calc([{'price': 1000, 'taxRates': TAX}], [], 27), (1099, 126, 973))
        rewardup = [{'name': 'RewardUp', 'amount': -200}]
        self.assertEqual(clover_calc([{'price': 1770, 'taxRates': TAX}], rewardup), (1774, 204, 1570))
        self.assertEqual(clover_calc([{'price': 1770, 'taxRates': TAX}], rewardup, 66)[0], 1700)
        # docs.clover.com "Calculate order totals": line discounts, 15% order discount, two rates.
        documented = [
            {'price': 1400, 'taxRates': [{'id': 'TENPERCENT01', 'rate': 1000000}],
             'discounts': [{'name': 'Lunch deal', 'amount': -100}]},
            {'price': 1200, 'taxRates': [{'id': 'FIVEPERCENT1', 'rate': 500000}],
             'discounts': [{'name': '25%', 'percentage': 25}]},
        ]
        self.assertEqual(clover_calc(documented, [{'name': '15%', 'percentage': 15}]), (2019, 149, 1870))
        self.assertEqual(clover_calc([{'price': 500}], []), (500, 0, 500))

    def test_refuses_what_it_cannot_reproduce(self):
        for line in ({'price': 1000, 'unitQty': 1500}, {'price': 1000, 'refunded': True},
                     {'price': 1000, 'modifications': [{'id': 'M' * 13}]}, {'price': None}):
            with self.assertRaises(Unsupported):
                clover_calc([line], [])
        with self.assertRaises(Unsupported):
            clover_calc([{'price': 1000}], [{'percentage': 10}, {'percentage': 5}])


class FakeClover:
    """Just enough of Clover's REST API: totals are stored, never recalculated, as on Clover."""

    def __init__(self, test, *, prices=(1770, 1999), total=None, payments=(), tenders=None):
        self.test = test
        self.writes = []
        self.lines = [{'id': f'LINE{n:09d}', 'name': f'Item {n}', 'price': price,
                       'taxRates': {'elements': TAX}} for n, price in enumerate(prices)]
        self.order = {'id': 'O' * 13, 'currency': 'CAD', 'paymentState': 'OPEN', 'state': 'open',
                      'total': total if total is not None else clover_calc(
                          [{'price': p, 'taxRates': TAX} for p in prices], [])[0],
                      'lineItems': {'elements': [{k: v for k, v in line.items() if k != 'taxRates'}
                                                 for line in self.lines]},
                      'discounts': {'elements': []}, 'payments': {'elements': list(payments)}}
        self.tenders = tenders if tenders is not None else [
            {'id': 'CASH000000001', 'label': 'Cash', 'labelKey': 'com.clover.tender.cash'},
            {'id': 'ETRANSFER0001', 'label': 'E-transfer'}]
        self.deleted = False
        self.refuse_note = False
        self.count = 0

    def __call__(self, method, url, **kwargs):
        path = url.split('clover.com', 1)[1]
        base = '/v3/merchants/' + 'M' * 13 + '/orders/' + 'O' * 13
        if self.deleted and path.startswith(base):
            return provider_response({}, 404)
        if method == 'GET' and path == base:
            return provider_response(self.order)
        if method == 'GET' and path == base + '/line_items':
            return provider_response({'elements': self.lines})
        if method == 'GET' and path.endswith('/tenders'):
            return provider_response({'elements': self.tenders})
        self.writes.append((method, path, kwargs.get('json')))
        self.count += 1
        if method == 'POST' and path == base + '/discounts':
            created = {'id': f'DISC{self.count:09d}', **kwargs['json']}
            self.order['discounts']['elements'].append(created)
            return provider_response(created)
        if method == 'DELETE' and path.startswith(base + '/discounts/'):
            self.order['discounts']['elements'] = [d for d in self.order['discounts']['elements']
                                                   if d['id'] != path.rsplit('/', 1)[1]]
            return provider_response({})
        if method == 'POST' and path == base:
            if self.refuse_note and 'note' in kwargs['json']:
                return provider_response({'message': 'Cannot update note on order.'}, 400)
            if 'externalReferenceId' in kwargs['json'] and any(
                    p['result'] == 'SUCCESS' for p in self.order['payments']['elements']):
                return provider_response({'message': 'Cannot update external reference id on order.'}, 400)
            self.order.update(kwargs['json'])
            return provider_response(self.order)
        if method == 'POST' and path == base + '/payments':
            created = {'id': f'PAY{self.count:010d}', **kwargs['json']}
            self.order['payments']['elements'].append(created)
            paid = sum(p['amount'] for p in self.order['payments']['elements'] if p['result'] == 'SUCCESS')
            self.order['paymentState'] = 'PAID' if paid == self.order['total'] else 'PARTIALLY_PAID'
            return provider_response(created)
        if method == 'DELETE' and path == base:
            self.deleted = True
            return provider_response({})
        self.test.fail(f'Unexpected Clover write {method} {path}')

    def expected(self):
        return {'items': [[line['id'], line['price'], line.get('unitQty')]
                          for line in self.order['lineItems']['elements']],
                'discounts': [[d['id'], d.get('amount'), d.get('percentage')]
                              for d in self.order['discounts']['elements']
                              if d.get('name') not in ('Cash discount', 'Rounding')],
                'payments': [[p['id'], p['amount']] for p in self.order['payments']['elements']
                             if not str(p.get('externalPaymentId') or '').startswith('popcore-')
                             and p['result'] == 'SUCCESS']}


class ConnectorWriteTests(unittest.TestCase):
    """The connector's only writes: completing an order POPCORE settled, deleting an unpaid one."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = {'PUBLIC_URL': 'http://127.0.0.1/clover-sandbox', 'SESSION_SECRET': 's' * 32,
                       'ADMIN_PASSWORD': 'p' * 32, 'WEBHOOK_PATH_KEY': 'w' * 32,
                       'DATA_DIR': self.directory.name, 'CLOVER_MERCHANT_ID': 'M' * 13,
                       'POPCORE_WRITER_KEY': 'k' * 32}
        self.app = create_app(self.config)
        self.app.extensions['clover_save_tokens']({'access_token': 'token',
                                                   'access_token_expiration': time.time() + 3600})
        self.client = self.app.test_client()
        credentials = base64.b64encode(('popcore:' + 'p' * 32).encode()).decode()
        self.headers = {'Authorization': 'Basic ' + credentials, 'X-Popcore-Writer': 'k' * 32}
        self.url = '/clover-sandbox/orders/' + 'O' * 13

    def settle(self, clover, **body):
        with patch('scripts.clover_sandbox.requests.request', side_effect=clover):
            return self.client.post(self.url + '/settle', headers=self.headers, json={
                'expected': clover.expected(), 'reference': {'title': 'Paid 2026-10-20 11:23'}, **body})

    def test_rounded_cash_writes_discount_total_reference_and_one_tender_once(self):
        clover = FakeClover(self, prices=(1000,))
        body = {'target_total': 1100, 'discount_name': 'Cash discount',
                'payments': [{'key': 'popcore-pc-7-9', 'tender': 'cash', 'amount': 1100}]}
        response = self.settle(clover, **body)
        self.assertEqual(response.status_code, 200, response.json)
        # $11.00 is unreachable with 13% HST: Clover lands on $10.99 and the cash tender says so.
        self.assertEqual(clover.writes, [
            ('POST', '/v3/merchants/MMMMMMMMMMMMM/orders/OOOOOOOOOOOOO/discounts', {'name': 'Cash discount', 'amount': -27}),
            ('POST', '/v3/merchants/MMMMMMMMMMMMM/orders/OOOOOOOOOOOOO', {'total': 1099}),
            ('POST', '/v3/merchants/MMMMMMMMMMMMM/orders/OOOOOOOOOOOOO', {'title': 'Paid 2026-10-20 11:23'}),
            ('POST', '/v3/merchants/MMMMMMMMMMMMM/orders/OOOOOOOOOOOOO/payments',
             {'amount': 1099, 'tender': {'id': 'CASH000000001'}, 'externalPaymentId': 'popcore-pc-7-9',
              'result': 'SUCCESS', 'note': 'Paid 2026-10-20 11:23', 'taxAmount': 126})])
        self.assertEqual((response.json['total'], response.json['paid'], response.json['difference_cents']),
                         (1099, True, 1))
        again = self.settle(clover, **body)
        self.assertEqual(again.status_code, 200, again.json)
        self.assertEqual(len(clover.writes), 4)
        self.assertEqual(again.json['payments'][0]['id'], response.json['payments'][0]['id'])

    def test_split_completes_after_the_card_and_shares_the_tax(self):
        card = {'id': 'CARDPAYMENT01', 'amount': 2200, 'result': 'SUCCESS', 'taxAmount': 253,
                'tender': {'id': 'CREDITCARD001'}}
        clover = FakeClover(self, payments=[card])
        response = self.settle(clover, target_total=4200, discount_name='Rounding',
                               payments=[{'key': 'popcore-pc-7-10', 'tender': 'cash', 'amount': 2000}])
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([w[2] for w in clover.writes[:3]], [{'name': 'Rounding', 'amount': -52}, {'total': 4200},
                                                             {'title': 'Paid 2026-10-20 11:23'}])
        self.assertEqual((clover.writes[3][2]['amount'], clover.writes[3][2]['taxAmount']), (2000, 483 - 253))
        self.assertEqual(clover.writes[3][2]['note'], 'Paid 2026-10-20 11:23')
        self.assertEqual(clover.order['paymentState'], 'PAID')

    def test_a_clover_refusal_shows_only_its_short_reason(self):
        card = {'id': 'CARDPAYMENT01', 'amount': 2200, 'result': 'SUCCESS', 'taxAmount': 253,
                'tender': {'id': 'CREDITCARD001'}}
        clover = FakeClover(self, payments=[card])

        def refusing(method, url, **kwargs):
            if method == 'POST' and url.endswith('/discounts'):
                return provider_response({'message': 'Order is locked  by payments', 'trace': 'internal-123'}, 400)
            return clover(method, url, **kwargs)
        with patch('scripts.clover_sandbox.requests.request', side_effect=refusing):
            response = self.client.post(self.url + '/settle', headers=self.headers, json={
                'expected': clover.expected(), 'target_total': 4200, 'discount_name': 'Rounding',
                'payments': [{'key': 'popcore-pc-7-10', 'tender': 'cash', 'amount': 2000}]})
        self.assertEqual(response.status_code, 502)
        self.assertIn('Clover says: Order is locked by payments', response.json['error'])
        self.assertNotIn('internal-123', response.json['error'])

    def test_card_only_order_is_only_stamped(self):
        clover = FakeClover(self, payments=[{'id': 'CARDPAYMENT01', 'amount': 4259, 'result': 'SUCCESS'}])
        clover.order['paymentState'] = 'PAID'
        response = self.settle(clover, target_total=4259, discount_name=None, payments=[])
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(clover.writes, [('POST', '/v3/merchants/MMMMMMMMMMMMM/orders/OOOOOOOOOOOOO',
                                          {'title': 'Paid 2026-10-20 11:23'})])

    def test_refusals_write_nothing(self):
        clover = FakeClover(self, prices=(1000,))
        changed = dict(target_total=1100, discount_name='Cash discount',
                       payments=[{'key': 'popcore-pc-7-9', 'tender': 'cash', 'amount': 1100}])
        with patch('scripts.clover_sandbox.requests.request', side_effect=clover):
            stale = self.client.post(self.url + '/settle', headers=self.headers, json={
                **changed, 'expected': {'items': [], 'discounts': [], 'payments': []}})
        self.assertEqual((stale.status_code, stale.json['code']), (409, 'clover_changed'))
        missing = FakeClover(self, prices=(1000,), tenders=[{'id': 'CASH000000001', 'labelKey': 'com.clover.tender.cash'}])
        wechat = self.settle(missing, target_total=1100, discount_name='Cash discount',
                             payments=[{'key': 'popcore-pc-7-9', 'tender': 'wechat', 'amount': 1100}])
        self.assertEqual((wechat.status_code, wechat.json['code']), (409, 'tender_missing'))
        self.assertIn('Wechat Pay', wechat.json['error'])
        over = self.settle(FakeClover(self, prices=(1000,)), target_total=1200, discount_name=None,
                           payments=[{'key': 'popcore-pc-7-9', 'tender': 'cash', 'amount': 1200}])
        self.assertEqual((over.status_code, over.json['code']), (409, 'overpaid'))
        weighted = FakeClover(self, prices=(1000,))
        weighted.lines[0]['unitQty'] = 1500
        weighted.order['lineItems']['elements'][0]['unitQty'] = 1500
        refused = self.settle(weighted, target_total=1600, discount_name='Cash discount',
                              payments=[{'key': 'popcore-pc-7-9', 'tender': 'cash', 'amount': 1600}])
        self.assertEqual((refused.status_code, refused.json['code']), (409, 'calc_unverified'))
        for clover_ in (clover, missing, weighted):
            self.assertEqual(clover_.writes, [])

    def test_writes_need_the_writer_key_and_valid_bodies(self):
        clover = FakeClover(self, prices=(1000,))
        with patch('scripts.clover_sandbox.requests.request', side_effect=clover):
            no_key = self.client.post(self.url + '/settle', headers={**self.headers, 'X-Popcore-Writer': 'x'},
                                      json={})
            self.assertEqual(no_key.status_code, 403)
            for bad in ({'target_total': 1100, 'expected': {}, 'payments': [{'key': 'other', 'tender': 'cash',
                                                                              'amount': 1}]},
                        {'target_total': 1100, 'expected': {}, 'payments': [{'key': 'popcore-pc-1-2',
                                                                              'tender': 'card', 'amount': 1}]},
                        {'target_total': -1, 'expected': {}, 'payments': []},
                        {'target_total': 1100, 'expected': {}, 'payments': [], 'discount_name': 'Other'}):
                self.assertEqual(self.client.post(self.url + '/settle', headers=self.headers,
                                                  json=bad).status_code, 400, bad)
        app = create_app({**self.config, 'POPCORE_WRITER_KEY': ''})
        app.extensions['clover_save_tokens']({'access_token': 'token', 'access_token_expiration': time.time() + 3600})
        self.assertEqual(app.test_client().post(self.url + '/settle', headers=self.headers,
                                                json={}).status_code, 404)
        self.assertEqual(clover.writes, [])

    def test_label_marks_an_open_order_paid_without_paying_it(self):
        clover = FakeClover(self, prices=(1000,))
        clover.order['note'] = 'Gift wrap'
        label = {'label': 'Paid 2026-10-20 11:23'}
        with patch('scripts.clover_sandbox.requests.request', side_effect=clover):
            first = self.client.post(self.url + '/label', headers=self.headers, json=label)
            again = self.client.post(self.url + '/label', headers=self.headers, json=label)
        self.assertEqual((first.status_code, first.json['written']), (200, ['note', 'title']))
        self.assertEqual((again.status_code, again.json['written']), (200, []))
        self.assertEqual(clover.writes, [('POST', '/v3/merchants/MMMMMMMMMMMMM/orders/OOOOOOOOOOOOO',
                                          {'title': 'Paid 2026-10-20 11:23', 'note': 'Gift wrap\nPaid 2026-10-20 11:23'})])
        self.assertEqual((clover.order['paymentState'], clover.order['payments']['elements']), ('OPEN', []))
        # Clover keeping its note never stops the title from marking the order.
        refusing = FakeClover(self, prices=(1000,))
        refusing.refuse_note = True
        with patch('scripts.clover_sandbox.requests.request', side_effect=refusing):
            titled = self.client.post(self.url + '/label', headers=self.headers, json=label)
        self.assertEqual(titled.status_code, 200, titled.json)
        self.assertEqual(refusing.order['title'], 'Paid 2026-10-20 11:23')
        with patch('scripts.clover_sandbox.requests.request', side_effect=clover):
            for bad in ({}, {'label': ''}, {'label': 'x' * 41}, {'label': 'Paid\u2028today'}, {'label': 7}):
                self.assertEqual(self.client.post(self.url + '/label', headers=self.headers, json=bad).status_code,
                                 400, bad)
            self.assertEqual(self.client.post(self.url + '/label', headers={**self.headers, 'X-Popcore-Writer': 'x'},
                                              json=label).status_code, 403)

    def test_references_are_a_popcore_number_a_title_or_both(self):
        from scripts.clover_sandbox import valid_reference
        self.assertTrue(valid_reference(None))
        self.assertTrue(valid_reference({'title': 'Paid 2026-10-20 11:23'}))
        self.assertTrue(valid_reference({'external_reference_id': 'POPCORE-7', 'title': 'POPCORE 7'}))
        for bad in ({}, {'external_reference_id': 'CLOVER-7'}, {'title': 'x' * 41}, {'title': 5}, 'POPCORE-7'):
            self.assertFalse(valid_reference(bad), bad)

    def test_delete_only_removes_an_unpaid_order(self):
        paid = FakeClover(self, payments=[{'id': 'CARDPAYMENT01', 'amount': 100, 'result': 'SUCCESS'}])
        with patch('scripts.clover_sandbox.requests.request', side_effect=paid):
            refused = self.client.delete(self.url, headers=self.headers)
        self.assertEqual((refused.status_code, refused.json['code']), (409, 'has_payments'))
        self.assertEqual(paid.writes, [])
        unpaid = FakeClover(self)
        with patch('scripts.clover_sandbox.requests.request', side_effect=unpaid):
            deleted = self.client.delete(self.url, headers=self.headers)
            again = self.client.delete(self.url, headers=self.headers)
        self.assertEqual(deleted.json, {'deleted': True, 'missing': False})
        self.assertEqual(again.json, {'deleted': True, 'missing': True})

    def test_single_order_reports_whether_its_total_can_be_reproduced(self):
        clover = FakeClover(self)
        with patch('scripts.clover_sandbox.requests.request', side_effect=clover):
            response = self.client.get(self.url, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['order']['total'], 4259)
        self.assertEqual(response.json['pricing'], {'verified': True, 'tax_cents': 490})
        drifted = FakeClover(self, total=4300)
        with patch('scripts.clover_sandbox.requests.request', side_effect=drifted), \
                patch('scripts.clover_sandbox.time.monotonic', return_value=999999999):
            response = self.client.get(self.url, headers=self.headers)
        self.assertFalse(response.json['pricing']['verified'])
        drifted.deleted = True
        with patch('scripts.clover_sandbox.requests.request', side_effect=drifted), \
                patch('scripts.clover_sandbox.time.monotonic', return_value=999999999 * 2):
            gone = self.client.get(self.url, headers=self.headers)
        self.assertEqual((gone.status_code, gone.json['code']), (404, 'not_found'))



class FakeCloverOrders:
    """Clover's order creation: an empty open order, then bulk lines, a discount and the total."""

    def __init__(self, test, *, tax_rates=None, drop_tax=False, prefix='NEW'):
        self.test, self.orders, self.lines, self.writes, self.prefix = test, {}, {}, [], prefix
        self.tax_rates = tax_rates if tax_rates is not None else [
            {'id': 'HST0000000013', 'name': 'HST', 'rate': 1300000, 'isDefault': True},
            {'id': 'NOTDEFAULT001', 'name': 'Other', 'rate': 500000, 'isDefault': False}]
        self.drop_tax = drop_tax

    def __call__(self, method, url, **kwargs):
        path = url.split('clover.com', 1)[1]
        root = '/v3/merchants/' + 'M' * 13
        if method == 'GET' and path == root + '/tax_rates':
            return provider_response({'elements': self.tax_rates})
        if method == 'GET' and path == root + '/orders':
            return provider_response({'elements': [{k: o[k] for k in ('id', 'externalReferenceId')}
                                                   for o in self.orders.values()]})
        order_id = path[len(root + '/orders/'):].split('/')[0] if path.startswith(root + '/orders/') else None
        if method == 'GET' and order_id and path.endswith('/line_items'):
            return provider_response({'elements': self.lines[order_id]})
        if method == 'GET' and order_id:
            if order_id not in self.orders:
                return provider_response({}, 404)
            return provider_response({**self.orders[order_id],
                                      'lineItems': {'elements': [{k: v for k, v in line.items() if k != 'taxRates'}
                                                                 for line in self.lines[order_id]]}})
        self.writes.append((method, path, kwargs.get('json')))
        if method == 'POST' and path == root + '/orders':
            order_id = f'{self.prefix}{len(self.orders):010d}'
            self.orders[order_id] = {'id': order_id, 'currency': 'CAD', 'paymentState': 'OPEN', 'total': 0,
                                     'createdTime': 1, 'discounts': {'elements': []}, 'payments': {'elements': []},
                                     **kwargs['json']}
            self.lines[order_id] = []
            return provider_response(self.orders[order_id])
        if method == 'POST' and path.endswith('/bulk_line_items'):
            for item in kwargs['json']['items']:
                rates = [] if self.drop_tax else [r for r in self.tax_rates if r['id'] in {t['id'] for t in item['taxRates']}]
                self.lines[order_id].append({'id': f'LINE{len(self.lines[order_id]):09d}', 'name': item['name'],
                                             'price': item['price'], 'taxRates': {'elements': rates}})
            return provider_response(self.lines[order_id])  # Clover answers with a bare list
        if method == 'POST' and path.endswith('/discounts'):
            created = {'id': 'DISC000000001', **kwargs['json']}
            self.orders[order_id]['discounts']['elements'].append(created)
            return provider_response(created)
        if method == 'POST' and order_id and path == root + '/orders/' + order_id:
            self.orders[order_id].update(kwargs['json'])
            return provider_response(self.orders[order_id])
        self.test.fail(f'Unexpected Clover write {method} {path}')


class ConnectorCreateTests(unittest.TestCase):
    """POPCORE-started sales: an open Clover order with custom lines, for card on the device."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app({'PUBLIC_URL': 'http://127.0.0.1/clover-sandbox', 'SESSION_SECRET': 's' * 32,
                               'ADMIN_PASSWORD': 'p' * 32, 'WEBHOOK_PATH_KEY': 'w' * 32,
                               'DATA_DIR': self.directory.name, 'CLOVER_MERCHANT_ID': 'M' * 13,
                               'POPCORE_WRITER_KEY': 'k' * 32})
        self.app.extensions['clover_save_tokens']({'access_token': 'token',
                                                   'access_token_expiration': time.time() + 3600})
        self.client = self.app.test_client()
        credentials = base64.b64encode(('popcore:' + 'p' * 32).encode()).decode()
        self.headers = {'Authorization': 'Basic ' + credentials, 'X-Popcore-Writer': 'k' * 32}
        self.body = {'key': 'popcore-pc-12', 'reference': 'MT261026001', 'title': 'MT-20261026-001', 'expected_total': 4259, 'tax_rate': 1300000,
                     'lines': [{'name': 'PM0209-01 The Prince · The Businessman', 'price': 1770, 'quantity': 1},
                               {'name': 'PM0001 Labubu', 'price': 1999, 'quantity': 1}]}

    def create(self, clover, body=None, headers=None):
        with patch('scripts.clover_sandbox.requests.request', side_effect=clover):
            return self.client.post('/clover-sandbox/orders/create', headers=headers or self.headers,
                                    json=self.body if body is None else body)

    def test_opens_one_taxed_order_with_the_popcore_reference_and_replays_it(self):
        clover = FakeCloverOrders(self)
        first = self.create(clover)
        self.assertEqual(first.status_code, 200, first.json)
        order = first.json['order']
        self.assertEqual((first.json['created'], order['total'], order['externalReferenceId'], order['paymentState']),
                         (True, 4259, 'MT261026001', 'OPEN'))
        self.assertEqual([(i['name'], i['price']) for i in order['items']],
                         [('PM0209-01 The Prince · The Businessman', 1770), ('PM0001 Labubu', 1999)])
        self.assertEqual(first.json['pricing'], {'verified': True, 'tax_cents': 490})
        root = '/v3/merchants/MMMMMMMMMMMMM/orders'
        self.assertEqual(clover.writes, [
            ('POST', root, {'state': 'open', 'externalReferenceId': 'MT261026001', 'title': 'MT-20261026-001'}),
            ('POST', root + '/NEW0000000000/bulk_line_items', {'items': [
                {'name': 'PM0209-01 The Prince · The Businessman', 'price': 1770, 'taxRates': [{'id': 'HST0000000013', 'name': 'HST', 'rate': 1300000}]},
                {'name': 'PM0001 Labubu', 'price': 1999, 'taxRates': [{'id': 'HST0000000013', 'name': 'HST', 'rate': 1300000}]}]}),
            ('POST', root + '/NEW0000000000', {'total': 4259})])
        again = self.create(clover)
        self.assertEqual((again.status_code, again.json['created'], again.json['order']['id']),
                         (200, False, 'NEW0000000000'))
        self.assertEqual(len(clover.writes), 3)
        # Lost connector memory: the order Clover already holds under the reference is reused.
        fresh = create_app({**self.app.config, 'PUBLIC_URL': 'http://127.0.0.1/clover-sandbox',
                            'SESSION_SECRET': 's' * 32, 'ADMIN_PASSWORD': 'p' * 32, 'WEBHOOK_PATH_KEY': 'w' * 32,
                            'DATA_DIR': tempfile.mkdtemp(dir=self.directory.name), 'CLOVER_MERCHANT_ID': 'M' * 13,
                            'POPCORE_WRITER_KEY': 'k' * 32})
        fresh.extensions['clover_save_tokens']({'access_token': 'token', 'access_token_expiration': time.time() + 3600})
        with patch('scripts.clover_sandbox.requests.request', side_effect=clover):
            reused = fresh.test_client().post('/clover-sandbox/orders/create', headers=self.headers, json=self.body)
        self.assertEqual((reused.status_code, reused.json['created'], reused.json['order']['id']),
                         (200, False, 'NEW0000000000'))
        self.assertEqual(len(clover.writes), 3)
        # Another POPCORE copy's sale with the same reference never takes this order over.
        other = self.create(clover, {**self.body, 'key': 'popcore-pc-99'})
        self.assertEqual((other.json['created'], other.json['order']['id']), (True, 'NEW0000000001'))

    def test_quantities_become_single_lines_and_an_agreed_discount_is_kept(self):
        clover = FakeCloverOrders(self)
        body = {**self.body, 'expected_total': 3774, 'discount': {'name': 'Agreed price', 'amount': 200},
                'lines': [{'name': 'PM0209-01 The Businessman', 'price': 1770, 'quantity': 2}]}
        created = self.create(clover, body)
        self.assertEqual(created.status_code, 200, created.json)
        self.assertEqual([i['price'] for i in created.json['order']['items']], [1770, 1770])
        self.assertEqual([w[2] for w in clover.writes[2:]], [{'name': 'Agreed price', 'amount': -200}, {'total': 3774}])

    def test_refuses_without_writing_when_clover_would_charge_another_total(self):
        clover = FakeCloverOrders(self)
        refused = self.create(clover, {**self.body, 'expected_total': 4260})
        self.assertEqual((refused.status_code, refused.json['code'], refused.json['clover_total']),
                         (409, 'amounts_differ', 4259))
        untaxed = self.create(FakeCloverOrders(self, tax_rates=[]))
        self.assertEqual((untaxed.status_code, untaxed.json['code']), (409, 'tax_missing'))
        plain = FakeCloverOrders(self, prefix='PLN', tax_rates=[{'id': 'HST0000000013', 'name': 'HST', 'rate': 1300000}])
        self.assertEqual(self.create(plain, {**self.body, 'key': 'popcore-pc-50'}).status_code, 200)
        twice = FakeCloverOrders(self, tax_rates=[{'id': f'HST000000001{n}', 'name': 'HST', 'rate': 1300000}
                                                  for n in range(2)])
        self.assertEqual(self.create(twice, {**self.body, 'key': 'popcore-pc-51'}).json['rates'], [{'name': 'HST', 'rate': 1300000, 'isDefault': None}] * 2)
        self.assertEqual(clover.writes, [])
        dropped = FakeCloverOrders(self, drop_tax=True, prefix='DRP')
        self.assertEqual(self.create(dropped).json['code'], 'tax_not_applied')

    def test_never_rewrites_an_order_changed_or_paid_on_the_device(self):
        clover = FakeCloverOrders(self)
        self.create(clover)
        clover.lines['NEW0000000000'].pop()
        self.assertEqual(self.create(clover).json['code'], 'clover_changed')
        clover.orders['NEW0000000000']['payments']['elements'].append({'id': 'CARDPAYMENT01', 'amount': 2000,
                                                                       'result': 'SUCCESS'})
        clover.orders['NEW0000000000']['paymentState'] = 'PARTIALLY_PAID'
        writes = len(clover.writes)
        paid = self.create(clover)
        self.assertEqual((paid.status_code, len(clover.writes)), (200, writes))

    def test_create_needs_the_writer_key_and_a_valid_body(self):
        clover = FakeCloverOrders(self)
        self.assertEqual(self.create(clover, headers={**self.headers, 'X-Popcore-Writer': 'x'}).status_code, 403)
        line = self.body['lines'][0]
        for bad in ({}, {**self.body, 'reference': 'MT 1'}, {**self.body, 'reference': 'MT-20261026-1'}, {**self.body, 'key': 'popcore-pc-1-2'}, {**self.body, 'lines': []},
                    {**self.body, 'lines': [{**line, 'price': 0}]}, {**self.body, 'lines': [{**line, 'quantity': 101}]},
                    {**self.body, 'lines': [{**line, 'name': 'x' * 128}]},
                    {**self.body, 'discount': {'name': 'Cash discount', 'amount': 100}},
                    {**self.body, 'expected_total': -1}, {**self.body, 'tax_rate': None}):
            self.assertEqual(self.create(clover, bad).status_code, 400, bad)
        self.assertEqual(clover.writes, [])

if __name__ == '__main__':
    unittest.main()
