"""Isolated offline checks for the sandbox checkout adapter."""
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import requests
from flask import Flask, request
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blueprints import clover_sandbox as adapter, settings
from checkout_access import business_date
from support import IsolatedApiCase


class SandboxAdapterTests(unittest.TestCase):
    def test_device_snapshot_keeps_distinct_items_and_never_confirms_payment(self):
        device = {'id': 'ABCDEFGHIJKLM', 'currency': 'CAD', 'total': 3000,
                  'createdTime': 1_790_000_000_000, 'paymentState': 'OPEN',
                  'source': 'device', 'reconciled': False, 'seen_at': time.time(),
                  'items': [{'id': str(i) * 13, 'name': 'Test item', 'price': 1000}
                            for i in range(3)], 'discounts': [], 'payments': []}
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'environment': 'sandbox', 'fetched_at': time.time(),
                                        'cloud_unavailable': True,
                                        'orders': [device]}).encode()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'CLOVER_SANDBOX_CHECKOUT_DIR': directory, 'CLOVER_SANDBOX_STORE_ID': '7',
            'CLOVER_SANDBOX_PROBE_PASSWORD': 'test-only',
        }), patch.object(adapter.requests, 'get', return_value=response), \
                Flask(__name__).test_request_context():
            request.jwt_payload = {'sub': 'cashier'}
            access = {'live_stores': [{'id': 7}], 'role': 'staff'}
            with adapter.database() as con:
                adapter.sync(con)
                row = adapter.order_row(con, 1)
                value = adapter.detail(con, row, access)
                self.assertEqual(len(value['order']['lines']), 3)
                self.assertEqual(value['order']['gross_cents'], 3000)
                self.assertEqual(value['status'], 'open')
                self.assertEqual(value['source_detail'], 'device')
                self.assertTrue(value['source_fresh'])
                self.assertFalse(value['can_process'])
                self.assertEqual(con.execute('SELECT seen_at FROM orders').fetchone()[0], device['seen_at'])
                self.assertIn('Clover cloud unavailable', con.execute('SELECT error FROM feed').fetchone()[0])

    def test_device_order_without_total_is_visible_without_payment_guidance(self):
        device = {'id': 'ABCDEFGHIJKLM', 'currency': 'CAD', 'total': None,
                  'createdTime': 1_790_000_000_000, 'paymentState': 'OPEN',
                  'source': 'device', 'reconciled': False, 'seen_at': time.time(),
                  'items': [{'id': 'I' * 13, 'name': 'Test item', 'price': 1000}],
                  'discounts': [], 'payments': []}
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'environment': 'sandbox', 'fetched_at': time.time(),
                                        'orders': [device]}).encode()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'CLOVER_SANDBOX_CHECKOUT_DIR': directory, 'CLOVER_SANDBOX_STORE_ID': '7',
            'CLOVER_SANDBOX_PROBE_PASSWORD': 'test-only',
        }), patch.object(adapter.requests, 'get', return_value=response), \
                Flask(__name__).test_request_context():
            request.jwt_payload = {'sub': 'cashier'}
            access = {'live_stores': [{'id': 7}], 'role': 'staff'}
            with adapter.database() as con:
                adapter.sync(con)
                value = adapter.detail(con, adapter.order_row(con, 1), access)
                self.assertEqual(len(value['order']['lines']), 1)
                self.assertTrue(value['total_pending'])
                self.assertFalse(value['can_process'])
                self.assertFalse(value['quote_available'])

    def test_cached_probe_snapshot_keeps_its_age_and_rate_limit_wait(self):
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'environment': 'sandbox', 'fetched_at': 995,
            'orders': [{'id': 'ABCDEFGHIJKLM', 'currency': 'CAD', 'total': 1130,
                        'createdTime': 1_790_000_000_000, 'paymentState': 'OPEN',
                        'items': [{'name': 'Test item', 'price': 1000}], 'payments': []}]}).encode()
        limited = requests.Response()
        limited.status_code = 502
        limited.headers['Retry-After'] = '7'
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'CLOVER_SANDBOX_CHECKOUT_DIR': directory, 'CLOVER_SANDBOX_STORE_ID': '7',
            'CLOVER_SANDBOX_PROBE_PASSWORD': 'test-only',
        }), patch.object(adapter.time, 'time', return_value=1000) as clock, \
                patch.object(adapter.requests, 'get', side_effect=[response, limited]) as provider:
            with adapter.database() as con:
                adapter.sync(con)
                self.assertEqual(con.execute('SELECT seen_at FROM orders').fetchone()[0], 995)
                self.assertEqual(con.execute('SELECT fetched_at FROM feed').fetchone()[0], 995)
                clock.return_value = 1001
                adapter.sync(con)
                clock.return_value = 1003
                adapter.sync(con)
                self.assertEqual(provider.call_count, 2)
                feed = con.execute('SELECT * FROM feed').fetchone()
                self.assertEqual(feed['next_poll'], 1008)
                self.assertTrue(feed['error'])

    def test_unpriced_open_draft_with_items_does_not_block_priced_orders(self):
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'environment': 'sandbox', 'fetched_at': time.time(), 'orders': [
            {'id': 'ABCDEFGHIJKLM', 'currency': 'CAD', 'total': 1130,
             'createdTime': 1_790_000_000_000, 'paymentState': 'OPEN',
             'items': [{'name': 'Test item', 'price': 1000}], 'payments': []},
            {'id': 'NOPQRSTUVWXYZ', 'currency': 'CAD', 'total': None,
             'createdTime': 1_790_000_000_000, 'paymentState': 'OPEN',
             'items': [{'name': 'Pending item'}], 'payments': []},
        ]}).encode()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'CLOVER_SANDBOX_CHECKOUT_DIR': directory,
            'CLOVER_SANDBOX_STORE_ID': '7',
            'CLOVER_SANDBOX_PROBE_PASSWORD': 'test-only',
        }), patch.object(adapter.requests, 'get', return_value=response):
            with adapter.database() as con:
                adapter.sync(con)
                self.assertEqual(con.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 1)
                self.assertEqual(con.execute('SELECT error FROM feed').fetchone()[0], '')

    def test_payment_confirmation_is_isolated_and_conservative(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'CLOVER_SANDBOX_CHECKOUT_DIR': directory,
            'CLOVER_SANDBOX_STORE_ID': '7',
        }), Flask(__name__).test_request_context():
            request.jwt_payload = {'sub': 'cashier'}
            access = {'live_stores': [{'id': 7}], 'role': 'staff'}
            order = {'id': 'ABCDEFGHIJKLM', 'currency': 'CAD', 'total': 1130,
                     'createdTime': 1_790_000_000_000, 'paymentState': 'PAID', 'discounts': [],
                     'items': [{'name': 'Test item', 'price': 1000}], 'payments': []}
            adapter.validate_order(order)
            with adapter.database() as con:
                con.execute("UPDATE feed SET fetched_at=?,error=''", (time.time(),))
                con.execute('''INSERT INTO orders(source_id,payload,seen_at,cashier_sub,cashier_name)
                    VALUES (?,?,?,?,?)''', (order['id'], json.dumps(order), time.time(), 'cashier', 'Cashier'))
                con.commit()
                row = adapter.order_row(con, 1)
                self.assertEqual(adapter.detail(con, row, access)['status'], 'open')
                con.execute('''INSERT INTO payments(order_id,source_id,tender,amount,result)
                    VALUES (1,'NOPQRSTUVWXYZ','e_transfer',1130,'FAIL')''')
                self.assertEqual(adapter.detail(con, row, access)['received_cents'], 0)
                con.execute("UPDATE payments SET result='SUCCESS'")
                result = adapter.detail(con, row, access)
                self.assertEqual(result['status'], 'completed')
                self.assertIsNone(result['sale_id'])
                self.assertEqual(result['order']['source_tax_cents'], 130)
                self.assertTrue(result['attempts'][0]['can_upload'])
                con.execute('UPDATE payments SET amount=500')
                self.assertEqual(adapter.detail(con, row, access)['remaining_cents'], 630)
                con.execute('UPDATE payments SET amount=1200')
                self.assertEqual(adapter.detail(con, row, access)['status'], 'open')
                con.execute('UPDATE orders SET seen_at=0')
                self.assertFalse(adapter.detail(con, adapter.order_row(con, 1), access)['can_process'])
                self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='sale_documents'").fetchone())
                self.assertEqual(adapter.tender('E-transfer'), 'e_transfer')
                self.assertEqual(adapter.tender('WeChat Pay'), 'wechat')
                self.assertEqual(adapter.tender('Check'), 'Check')
                request.jwt_payload = {'sub': 'someone-else'}
                with self.assertRaises(PermissionError):
                    adapter.detail(con, row, {'live_stores': [], 'role': 'staff'})


class SandboxAccessTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(adapter.bp)
        self.app.register_blueprint(settings.bp)
        environment = patch.dict(os.environ, {
            'CLOVER_SANDBOX_CHECKOUT_DIR': str(Path(self.tempdir.name) / 'sandbox'),
            'CLOVER_SANDBOX_STORE_ID': str(self.store_id),
            'CLOVER_SANDBOX_PROBE_PASSWORD': 'test-only',
        })
        environment.start()
        self.addCleanup(environment.stop)
        with closing(self.connect()) as con:
            employee = con.execute("INSERT INTO employees(auth0_id,name) VALUES ('auth0|staff','Cashier')").lastrowid
            con.execute("INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id) VALUES (?,?,'00:01','00:02','test',?)",
                        (employee, business_date(), self.store_id))
            con.commit()
        self.order = {'id': 'ABCDEFGHIJKLM', 'currency': 'CAD', 'total': 1130,
                      'createdTime': 1_790_000_000_000, 'paymentState': 'OPEN', 'discounts': [],
                      'items': [{'name': 'Test item', 'price': 1000}], 'payments': []}
        with adapter.database() as con:
            con.execute("UPDATE feed SET fetched_at=?,error=''", (time.time(),))
            con.execute('INSERT INTO orders(source_id,payload,seen_at) VALUES (?,?,?)',
                        (self.order['id'], json.dumps(self.order), time.time()))
            con.commit()

    def test_cash_discount_flag_clears_when_clover_removes_it(self):
        cases = [
            ([{'id': 'TESTDISCOUNT1', 'name': 'CA$0.89 Off', 'amount': -89, 'percentage': None}], True),
            ([{'id': 'TESTDISCOUNT1', 'name': 'CA$0.89 Off', 'amount': -90, 'percentage': None}], False),
            ([{'id': 'TESTDISCOUNT1', 'name': 'RewardUp Loyalty', 'amount': -89, 'percentage': None}], False),
            (None, False),
            ([], False),
        ]
        for discounts, expected in cases:
            with self.subTest(discounts=discounts):
                self.order['discounts'] = discounts
                with adapter.database() as con:
                    con.execute('UPDATE orders SET payload=?,seen_at=? WHERE id=1',
                                (json.dumps(self.order), time.time()))
                    con.commit()
                response = self.client.get('/api/clover-sandbox/checkouts/1', headers=self.headers())
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertIs(response.get_json()['cash_discount_applied'], expected)

    def test_cny_rate_is_admin_set_and_available_to_cashier(self):
        self.assertEqual(self.client.get('/api/clover-sandbox/checkouts/access',
                                         headers=self.headers()).get_json()['cny_per_cad'], '')
        for rate in ('0', '5.12345', '100.0001', 'bad'):
            response = self.client.put('/api/settings', json={'checkout_cny_per_cad':rate},
                                       headers=self.headers('admin'))
            self.assertEqual(response.status_code, 400, rate)
        saved = self.client.put('/api/settings', json={'checkout_cny_per_cad':'5.2500'},
                                headers=self.headers('admin'))
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(self.client.get('/api/clover-sandbox/checkouts/access',
                                         headers=self.headers()).get_json()['cny_per_cad'], '5.2500')
        self.assertEqual(self.client.put('/api/settings', json={'checkout_cny_per_cad':'6'},
                                         headers=self.headers()).status_code, 403)

    def test_note_stays_with_order_after_resync_and_rejects_other_cashier(self):
        self.client.post('/api/clover-sandbox/checkouts/1/claim', headers=self.headers())
        path = '/api/clover-sandbox/checkouts/1/notes'
        self.assertEqual(self.client.post(path, json={'note':'Other','expected_note':''},
                                          headers=self.headers('staff:other')).status_code, 403)
        saved = self.client.post(path, json={'note':'Special CNY arrangement','expected_note':''},
                                 headers=self.headers()).get_json()
        self.assertEqual(saved['note'], 'Special CNY arrangement')
        self.assertEqual(self.client.post(path, json={'note':'Overwrite','expected_note':''},
                                          headers=self.headers()).status_code, 409)
        with adapter.database() as con:
            con.execute('UPDATE feed SET next_poll=0')
            con.commit()
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'environment': 'sandbox', 'fetched_at': time.time(), 'orders': [self.order]}).encode()
        with patch.object(adapter.requests, 'get', return_value=response):
            refreshed = self.client.get('/api/clover-sandbox/checkouts/1', headers=self.headers())
        self.assertEqual(refreshed.get_json()['note'], 'Special CNY arrangement')

    def test_shifted_staff_can_claim_without_inventory_grant(self):
        response = self.client.post('/api/clover-sandbox/checkouts/1/claim', headers=self.headers())
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['cashier_sub'], 'auth0|staff')
        with adapter.database() as con:
            self.assertEqual(con.execute('SELECT cashier_sub FROM orders WHERE id=1').fetchone()[0], 'auth0|staff')

    def test_stale_snapshot_can_be_removed_without_losing_evidence_and_returns_if_seen_again(self):
        self.assertEqual(self.client.post('/api/clover-sandbox/checkouts/1/claim', headers=self.headers()).status_code, 200)
        self.assertEqual(self.client.post('/api/clover-sandbox/checkouts/1/hide', headers=self.headers()).status_code, 409)
        with adapter.database() as con:
            con.execute('UPDATE orders SET seen_at=0 WHERE id=1')
            con.execute('UPDATE feed SET next_poll=?', (time.time() + 60,))
            payment_id = con.execute("INSERT INTO payments(order_id,source_id,tender,amount,result) VALUES (1,'NOPQRSTUVWXYZ','card',1130,'SUCCESS')").lastrowid
            con.execute('INSERT INTO evidence(payment_id,content_hash,mime_type,content,uploader_sub) VALUES (?,?,?,?,?)',
                        (payment_id, 'test-hash', 'image/png', b'photo', 'auth0|staff'))
            con.commit()
        removed = self.client.post('/api/clover-sandbox/checkouts/1/hide', headers=self.headers())
        self.assertEqual(removed.status_code, 200, removed.get_json())
        queue = self.client.get(f'/api/clover-sandbox/checkouts?store_id={self.store_id}&view=live', headers=self.headers())
        self.assertEqual(queue.get_json()['orders'], [])
        with adapter.database() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM evidence').fetchone()[0], 1)
            con.execute('UPDATE feed SET next_poll=0')
            con.commit()
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'environment': 'sandbox', 'fetched_at': time.time(), 'orders': [self.order]}).encode()
        with patch.object(adapter.requests, 'get', return_value=response):
            queue = self.client.get(f'/api/clover-sandbox/checkouts?store_id={self.store_id}&view=live', headers=self.headers())
        self.assertEqual(len(queue.get_json()['orders']), 1)
        with adapter.database() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM evidence').fetchone()[0], 1)

    def test_owner_can_upload_sandbox_payment_evidence_without_inventory_grant(self):
        self.order['paymentState'] = 'PAID'
        self.order['payments'] = [{'id': 'NOPQRSTUVWXYZ', 'amount': 1130,
                                   'result': 'SUCCESS', 'tenderLabel': 'Card'}]
        with adapter.database() as con:
            con.execute("UPDATE orders SET payload=?,cashier_sub='auth0|staff' WHERE id=1", (json.dumps(self.order),))
            con.execute("INSERT INTO payments(order_id,source_id,tender,amount,result) VALUES (1,'NOPQRSTUVWXYZ','card',1130,'SUCCESS')")
            con.commit()
        image = io.BytesIO()
        Image.new('RGB', (2, 2), 'red').save(image, format='PNG')
        image.seek(0)
        response = self.client.post('/api/clover-sandbox/checkouts/1/attempts/1/evidence',
                                    headers=self.headers(), data={'image': (image, 'proof.png')})
        self.assertEqual(response.status_code, 201, response.get_json())
        with adapter.database() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM evidence').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
