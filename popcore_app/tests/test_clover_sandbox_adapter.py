"""Isolated offline checks for the sandbox checkout adapter."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from flask import Flask, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blueprints import clover_sandbox as adapter


class SandboxAdapterTests(unittest.TestCase):
    def test_blank_open_draft_does_not_block_priced_orders(self):
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'environment': 'sandbox', 'orders': [
            {'id': 'ABCDEFGHIJKLM', 'currency': 'CAD', 'total': 1130,
             'createdTime': 1_790_000_000_000, 'paymentState': 'OPEN',
             'items': [{'name': 'Test item', 'price': 1000}], 'payments': []},
            {'id': 'NOPQRSTUVWXYZ', 'currency': 'CAD', 'total': None,
             'createdTime': 1_790_000_000_000, 'paymentState': 'OPEN',
             'items': [], 'payments': []},
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


if __name__ == '__main__':
    unittest.main()
