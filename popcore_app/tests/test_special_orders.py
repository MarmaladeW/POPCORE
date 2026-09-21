from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier
from unittest.mock import patch

from support import IsolatedApiCase


class SpecialOrderTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.clock = patch(
            'special_order_operations.business_date', return_value='2026-09-21'
        )
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.create_number = 0
        with closing(self.connect()) as con:
            for subject, name, position, shift_date in (
                ('creator', 'Creator', 'Front', '2026-09-21'),
                ('cashier', 'Cashier', ' cashier ', '2026-09-21'),
                ('manager', 'Manager', 'Front', '2026-09-21'),
                ('admin', 'Admin', '', '2026-09-21'),
            ):
                employee_id = con.execute(
                    'INSERT INTO employees(auth0_id,name) VALUES (?,?)',
                    (f'auth0|{subject}', name),
                ).lastrowid
                if position:
                    con.execute(
                        """INSERT INTO shifts
                           (employee_id,date,start_time,end_time,assigned_by,store_id,position)
                           VALUES (?,?,?,?, 'test', ?, ?)""",
                        (employee_id, shift_date, '00:01', '00:02',
                         self.store_id, position),
                    )
            con.commit()

    def post(self, path, body, key, role='staff:creator'):
        return self.client.post(
            '/api/special-orders' + path,
            json=body,
            headers={**self.headers(role), 'Idempotency-Key': key},
        )

    def patch_order(self, order_id, body, key, role='admin'):
        return self.client.patch(
            f'/api/special-orders/{order_id}',
            json=body,
            headers={**self.headers(role), 'Idempotency-Key': key},
        )

    def create_order(self, initial_paid_cents=2000, role='staff:creator'):
        self.create_number += 1
        response = self.post('', {
            'customer_name': 'Maya Chen',
            'customer_phone': '416-555-0148',
            'item_description': 'Smiski Museum Series - The Source',
            'total_cents': 10000,
            'initial_paid_cents': initial_paid_cents,
        }, f'create-order-{self.create_number}', role)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def add_payment(self, order, amount, key='payment', role='staff:creator'):
        response = self.post(f"/{order['id']}/payments", {
            'expected_version': order['version'], 'amount_cents': amount,
        }, key, role)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def complete(self, order, key='complete', role='staff:creator'):
        response = self.post(f"/{order['id']}/complete", {
            'expected_version': order['version'],
        }, key, role)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_separate_payments_require_full_payment_before_completion(self):
        order = self.create_order()
        blocked = self.post(f"/{order['id']}/complete", {
            'expected_version': order['version'],
        }, 'complete-too-soon')
        self.assertEqual(blocked.status_code, 409, blocked.get_json())
        paid = self.add_payment(order, 8000, 'final-payment')
        self.assertEqual(
            [payment['amount_cents'] for payment in paid['payments']],
            [2000, 8000],
        )
        self.assertEqual(paid['paid_cents'], 10000)
        self.assertEqual(paid['remaining_cents'], 0)
        completed = self.complete(paid, 'complete-paid')
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(completed['completed_by'], 'auth0|creator')
        self.assertEqual(completed['completed_by_name'], 'Creator')
        self.assertTrue(completed['completed_at'].endswith('Z'))

    def test_phone_is_projected_by_role_and_todays_cashier_position(self):
        order = self.create_order()
        path = f"/api/special-orders/{order['id']}"
        creator = self.client.get(path, headers=self.headers('staff:creator'))
        cashier = self.client.get(path, headers=self.headers('staff:cashier'))
        manager = self.client.get(path, headers=self.headers('manager'))
        admin = self.client.get(path, headers=self.headers('admin'))
        self.assertIsNone(creator.get_json()['customer_phone'])
        for response in (cashier, manager, admin):
            self.assertEqual(response.get_json()['customer_phone'], '416-555-0148')
        with closing(self.connect()) as con:
            con.execute("DELETE FROM employees WHERE auth0_id='auth0|admin'")
            con.commit()
        admin_without_profile = self.client.get(path, headers=self.headers('admin'))
        self.assertEqual(
            admin_without_profile.get_json()['customer_phone'], '416-555-0148'
        )

    def test_cashier_phone_access_uses_toronto_day_not_store_or_shift_hours(self):
        order = self.create_order()
        path = f"/api/special-orders/{order['id']}"
        visible = self.client.get(path, headers=self.headers('staff:cashier'))
        self.assertEqual(visible.get_json()['customer_phone'], '416-555-0148')
        with closing(self.connect()) as con:
            con.execute(
                "UPDATE shifts SET date='2026-09-20' WHERE employee_id="
                "(SELECT id FROM employees WHERE auth0_id='auth0|cashier')"
            )
            con.commit()
        hidden = self.client.get(path, headers=self.headers('staff:cashier'))
        self.assertIsNone(hidden.get_json()['customer_phone'])
        with closing(self.connect()) as con:
            con.execute(
                "UPDATE shifts SET date='2026-09-22' WHERE employee_id="
                "(SELECT id FROM employees WHERE auth0_id='auth0|cashier')"
            )
            con.commit()
        hidden = self.client.get(path, headers=self.headers('staff:cashier'))
        self.assertIsNone(hidden.get_json()['customer_phone'])

    def test_zero_initial_payment_and_status_lists(self):
        order = self.create_order(0)
        self.assertEqual(order['payments'], [])
        self.assertEqual(order['paid_cents'], 0)
        self.assertEqual(order['remaining_cents'], 10000)
        open_orders = self.client.get(
            '/api/special-orders?status=open', headers=self.headers('staff:creator')
        ).get_json()
        self.assertEqual([row['id'] for row in open_orders], [order['id']])
        order = self.add_payment(order, 10000)
        self.complete(order)
        completed = self.client.get(
            '/api/special-orders?status=completed',
            headers=self.headers('staff:creator'),
        ).get_json()
        self.assertEqual([row['id'] for row in completed], [order['id']])

    def test_invalid_inputs_and_overpayment_are_atomic(self):
        valid = {
            'customer_name': 'Maya', 'customer_phone': '416-555-0148',
            'item_description': 'Requested figure', 'total_cents': 10000,
            'initial_paid_cents': 0,
        }
        invalid = [
            {**valid, 'customer_name': ''},
            {**valid, 'customer_phone': []},
            {**valid, 'item_description': ' '},
            {**valid, 'total_cents': 0},
            {**valid, 'total_cents': 1.5},
            {**valid, 'initial_paid_cents': -1},
            {**valid, 'initial_paid_cents': 10001},
        ]
        for index, body in enumerate(invalid):
            with self.subTest(body=body):
                response = self.post('', body, f'invalid-{index}')
                self.assertEqual(response.status_code, 400, response.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM special_orders').fetchone()[0], 0)
        order = self.create_order()
        before = self.snapshot(('special_orders', 'special_order_payments'))
        for index, amount in enumerate((0, -1, 8001, 1.5, 'five')):
            response = self.post(f"/{order['id']}/payments", {
                'expected_version': order['version'], 'amount_cents': amount,
            }, f'bad-payment-{index}')
            self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(
            self.snapshot(('special_orders', 'special_order_payments')), before
        )

    def test_retry_is_exactly_once_and_stale_version_conflicts(self):
        order = self.create_order()
        body = {'expected_version': order['version'], 'amount_cents': 1000}
        first = self.post(f"/{order['id']}/payments", body, 'same-payment')
        replay = self.post(f"/{order['id']}/payments", body, 'same-payment')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json(), replay.get_json())
        changed = self.post(f"/{order['id']}/payments", {
            **body, 'amount_cents': 1001,
        }, 'same-payment')
        self.assertEqual(changed.status_code, 409)
        stale = self.post(f"/{order['id']}/payments", body, 'stale-payment')
        self.assertEqual(stale.status_code, 409)
        with closing(self.connect()) as con:
            amounts = [row[0] for row in con.execute(
                'SELECT amount_cents FROM special_order_payments ORDER BY id'
            )]
        self.assertEqual(amounts, [2000, 1000])

    def test_completed_order_rejects_payments(self):
        order = self.complete(self.create_order(10000))
        before = self.snapshot(('special_orders', 'special_order_payments'))
        response = self.post(f"/{order['id']}/payments", {
            'expected_version': order['version'], 'amount_cents': 1,
        }, 'late-payment')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            self.snapshot(('special_orders', 'special_order_payments')), before
        )

    def test_admin_corrections_change_attribution_and_dates_only(self):
        order = self.create_order(10000)
        denied = self.patch_order(order['id'], {
            'expected_version': order['version'],
            'created_by': 'auth0|cashier',
            'created_at': '2026-09-20T14:00:00Z',
        }, 'manager-correct', 'manager')
        self.assertEqual(denied.status_code, 403)
        corrected = self.patch_order(order['id'], {
            'expected_version': order['version'],
            'created_by': 'auth0|cashier',
            'created_at': '2026-09-20T14:00:00Z',
        }, 'admin-correct').get_json()
        self.assertEqual(corrected['created_by'], 'auth0|cashier')
        self.assertEqual(corrected['created_by_name'], 'Cashier')
        self.assertEqual(corrected['created_at'], '2026-09-20T14:00:00Z')
        completed = self.complete(corrected, 'complete-corrected', 'admin')
        corrected = self.patch_order(completed['id'], {
            'expected_version': completed['version'],
            'completed_by': 'auth0|manager',
            'completed_at': '2026-09-21T18:30:00Z',
        }, 'admin-completion-correct').get_json()
        self.assertEqual(corrected['completed_by'], 'auth0|manager')
        self.assertEqual(corrected['completed_by_name'], 'Manager')
        self.assertEqual(corrected['completed_at'], '2026-09-21T18:30:00Z')
        invalid = self.patch_order(corrected['id'], {
            'expected_version': corrected['version'],
            'completed_by': None,
        }, 'broken-pair')
        self.assertEqual(invalid.status_code, 400)

    def test_competing_payments_cannot_exceed_total(self):
        order = self.create_order(2000)
        barrier = Barrier(2)

        def request(key):
            with self.app.test_client() as client:
                barrier.wait(timeout=5)
                response = client.post(
                    f"/api/special-orders/{order['id']}/payments",
                    json={'expected_version': order['version'], 'amount_cents': 8000},
                    headers={**self.headers('staff:creator'), 'Idempotency-Key': key},
                )
                return response.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(future.result(timeout=10) for future in (
                pool.submit(request, 'race-a'), pool.submit(request, 'race-b')
            ))
        self.assertEqual(statuses, [200, 409])
        with closing(self.connect()) as con:
            self.assertEqual(
                con.execute('SELECT sum(amount_cents) FROM special_order_payments').fetchone()[0],
                10000,
            )
            self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_viewer_cannot_read_or_create_special_orders(self):
        self.assertEqual(
            self.client.get('/api/special-orders', headers=self.headers('viewer')).status_code,
            403,
        )
        response = self.post('', {
            'customer_name': 'Maya', 'customer_phone': '416-555-0148',
            'item_description': 'Requested figure', 'total_cents': 10000,
            'initial_paid_cents': 0,
        }, 'viewer-create', 'viewer')
        self.assertEqual(response.status_code, 403)
