"""Blocking or deleting a user ends their access at once and keeps their shifts."""
from contextlib import closing
from datetime import date, timedelta
from unittest.mock import Mock, patch

from support import IsolatedApiCase
from blueprints import users
import auth
import checkout_access

TOKEN = 'feed-token-for-cashier-0001'


class RemovedUserTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(users.bp)
        today = date.fromisoformat(checkout_access.business_date())
        self.days = {offset: (today + timedelta(days=offset)).isoformat() for offset in (-3, 0, 2, 9)}
        with closing(self.connect()) as con:
            self.mk_id = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            self.employee = con.execute(
                "INSERT INTO employees(auth0_id,name,ical_token) VALUES ('auth0|staff','Cashier',?)", (TOKEN,)
            ).lastrowid
            for offset, store, start, end in ((-3, self.store_id, '12:00', '20:00'), (9, self.mk_id, '10:00', '18:00'),
                                              (0, self.store_id, '16:00', '21:00'), (2, self.store_id, '12:00', '20:00')):
                con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                               VALUES (?,?,?,?,'auth0|manager',?)""", (self.employee, self.days[offset], start, end, store))
            con.commit()

    def employee_row(self):
        with closing(self.connect()) as con:
            return dict(con.execute('SELECT is_active, ical_token FROM employees WHERE id=?',
                                    (self.employee,)).fetchone())

    def shift_count(self):
        with closing(self.connect()) as con:
            return con.execute('SELECT COUNT(*) FROM shifts WHERE employee_id=?', (self.employee,)).fetchone()[0]

    def assert_disabled(self, response):
        self.assertEqual((response.status_code, response.get_json()['code']), (403, 'account_disabled'))

    def test_an_inactive_employee_is_refused_on_every_route_at_once(self):
        self.assertEqual(self.client.get('/api/stores', headers=self.headers()).status_code, 200)
        with closing(self.connect()) as con:
            con.execute('UPDATE employees SET is_active=0 WHERE id=?', (self.employee,))
            con.commit()
        before = self.snapshot(('daily_sales',))
        self.assert_disabled(self.client.get('/api/stores', headers=self.headers()))  # login_required
        self.assert_disabled(self.client.post('/api/sales/upsert', headers=self.headers(), json={  # role_required
            'store_code': 'DT', 'product_id': self.product_id, 'qty_pos': 1}))
        self.assertEqual(self.snapshot(('daily_sales',)), before)

    def test_accounts_without_an_inactive_row_keep_working(self):
        with closing(self.connect()) as con:
            # A removed trainee's synthetic id never signs in and never blocks anyone else.
            con.execute("INSERT INTO employees(auth0_id,name,is_active,is_trainee) VALUES ('trainee|abc','Old',0,1)")
            con.commit()
        # An active employee and a sign-in whose row is not created yet (rows are created lazily).
        for role in ('staff', 'staff:first-login'):
            with self.subTest(role=role):
                self.assertEqual(self.client.get('/api/stores', headers=self.headers(role)).status_code, 200)
        # A machine-to-machine service subject has no employees row.
        with patch.object(auth, '_decode_token', return_value={'sub': 'svc-client@clients', auth.ROLE_CLAIM: 'manager'}):
            self.assertEqual(self.client.get('/api/stores', headers=self.headers()).status_code, 200)

    def test_blocking_ends_access_and_the_feed_and_unblocking_restores_access(self):
        self.assertEqual(self.client.get(f'/api/schedule/ical/{TOKEN}.ics').status_code, 200)
        with patch.object(users, '_mgmt_patch', return_value=Mock(ok=True)) as provider:
            blocked = self.client.patch('/api/users/auth0|staff', headers=self.headers('admin'), json={'is_active': 0})
        self.assertEqual(blocked.status_code, 200)
        self.assertEqual(provider.call_args.kwargs['json'], {'blocked': True})
        self.assertEqual(self.employee_row(), {'is_active': 0, 'ical_token': None})
        self.assert_disabled(self.client.get('/api/schedule/me', headers=self.headers()))
        self.assertEqual(self.client.get(f'/api/schedule/ical/{TOKEN}.ics').status_code, 404)
        with patch.object(users, '_mgmt_patch', return_value=Mock(ok=True)):
            self.client.patch('/api/users/auth0|staff', headers=self.headers('admin'), json={'is_active': 1})
        self.assertEqual(self.employee_row()['is_active'], 1)
        self.assertEqual(self.client.get('/api/schedule/me', headers=self.headers()).status_code, 200)
        self.assertEqual(self.shift_count(), 4)

    def test_a_failed_provider_block_changes_nothing_locally(self):
        failed = Mock(ok=False, json=Mock(return_value={'message': 'Provider unavailable'}))
        with patch.object(users, '_mgmt_patch', return_value=failed):
            response = self.client.patch('/api/users/auth0|staff', headers=self.headers('admin'), json={'is_active': 0})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.employee_row(), {'is_active': 1, 'ical_token': TOKEN})

    def test_delete_keeps_every_shift_and_retires_the_feed(self):
        with patch.object(users, '_mgmt_delete', return_value=Mock(ok=True, status_code=204)):
            response = self.client.delete('/api/users/auth0|staff', headers=self.headers('admin'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.shift_count(), 4)
        self.assertEqual(self.employee_row(), {'is_active': 0, 'ical_token': None})
        self.assert_disabled(self.client.get('/api/stores', headers=self.headers()))
        self.assertEqual(self.client.get(f'/api/schedule/ical/{TOKEN}.ics').status_code, 404)

    def test_feed_refuses_an_inactive_employee_whose_token_was_never_cleared(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE employees SET is_active=0 WHERE id=?', (self.employee,))
            con.commit()
        self.assertEqual(self.client.get(f'/api/schedule/ical/{TOKEN}.ics').status_code, 404)

    def test_delete_confirmation_lists_shifts_from_today_on(self):
        response = self.client.get('/api/users/auth0|staff/future-shifts', headers=self.headers('admin'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([(s['date'], s['store_code'], s['start_time'], s['end_time']) for s in response.get_json()['shifts']], [
            (self.days[0], 'DT', '16:00', '21:00'), (self.days[2], 'DT', '12:00', '20:00'),
            (self.days[9], 'MK', '10:00', '18:00')])
        self.assertEqual(self.client.get('/api/users/auth0|nobody/future-shifts',
                                         headers=self.headers('admin')).get_json(), {'shifts': []})
        for role in ('manager', 'staff'):
            self.assertEqual(self.client.get('/api/users/auth0|staff/future-shifts',
                                             headers=self.headers(role)).status_code, 403)
