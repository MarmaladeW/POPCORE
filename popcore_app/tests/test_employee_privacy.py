"""What an employee's record gives away (owner, 2026-11-26, after the access audit): a PIN's hash and lock state and
the private calendar address never leave the server, nobody but an admin renames themselves, and each employee reads
their own monthly hours and nobody else's."""
from contextlib import closing

from support import IsolatedApiCase
from blueprints import users
import people

TOKEN = 'private-calendar-token-0123456789'


class EmployeePrivacyTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.open_every_month()
        self.app.register_blueprint(users.bp)
        with closing(self.connect()) as con:
            self.mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            self.jessi = con.execute(
                """INSERT INTO employees(auth0_id, name, email, ical_token, pin_hash, pin_salt, pin_set_at, pin_failures,
                                         pin_locked_until)
                   VALUES ('auth0|jessi', 'Jessi', 'jessi@popcore.internal', ?, 'hash', 'salt', '2026-11-20', 3,
                           '2026-11-20T00:05:00+00:00')""", (TOKEN,)).lastrowid
            self.amy = con.execute("INSERT INTO employees(auth0_id, name) VALUES ('auth0|amy', 'Amy')").lastrowid
            self.trainee = con.execute(
                "INSERT INTO employees(auth0_id, name, is_trainee, pin_hash) VALUES ('trainee|1', 'New', 1, 'hash')").lastrowid
            # September 2026 wage period runs Sep 4 – Oct 3 (month start day 4).
            for employee, date, start, end, store in ((self.jessi, '2026-09-05', '12:00', '20:00', self.store_id),
                                                      (self.jessi, '2026-09-09', '12:00', '16:00', self.mk),
                                                      (self.amy, '2026-09-08', '12:00', '20:00', self.store_id)):
                con.execute("""INSERT INTO shifts(employee_id, date, start_time, end_time, assigned_by, store_id)
                               VALUES (?, ?, ?, ?, 'manager', ?)""", (employee, date, start, end, store))
            con.commit()

    def assert_public(self, row):
        self.assertFalse(set(people.PRIVATE_FIELDS) & set(row), row)
        self.assertIn('name', row)

    def test_no_route_sends_a_pin_hash_or_the_calendar_address(self):
        jessi, manager = self.headers('staff:jessi'), self.headers('manager')
        mine = self.client.get('/api/schedule/me', headers=jessi).get_json()
        self.assertEqual((mine['id'], mine['name'], mine['email']), (self.jessi, 'Jessi', 'jessi@popcore.internal'))
        rows = [mine, self.client.patch('/api/schedule/me', json={}, headers=jessi).get_json()]
        rows += self.client.get('/api/schedule/employees', headers=manager).get_json()
        rows += self.client.get('/api/schedule/trainees', headers=manager).get_json()
        rows.append(self.client.post('/api/schedule/trainees', json={'name': 'Newer'}, headers=manager).get_json())
        rows.append(self.client.patch(f'/api/schedule/employees/{self.jessi}', json={'name': 'Jessi W'},
                                      headers=manager).get_json())
        for path, body in (('color', {'color': '#123456'}), ('schedulable', {'is_schedulable': False}),
                           ('status', {'status': 'training'})):
            changed = self.client.patch(f'/api/employees/{self.jessi}/{path}', json=body, headers=manager)
            self.assertEqual(changed.status_code, 200, changed.get_json())
            rows.append(changed.get_json())
        self.assertGreaterEqual(len(rows), 9)
        for row in rows:
            self.assert_public(row)
        # The calendar address still comes from its own route, to its owner only.
        self.assertEqual(self.client.get('/api/schedule/calendar-feed', headers=jessi).get_json()['token'], TOKEN)

    def test_only_an_admin_renames_themselves(self):
        for role in ('staff:jessi', 'manager', 'viewer:jessi'):
            for body in ({'name': 'Owner'}, {'email': 'owner@popcore.internal'}):
                refused = self.client.patch('/api/schedule/me', json=body, headers=self.headers(role))
                self.assertEqual((refused.status_code, refused.get_json()['code']), (403, 'name_locked'), (role, body))
        with closing(self.connect()) as con:
            self.assertEqual(tuple(con.execute('SELECT name, email FROM employees WHERE id=?', (self.jessi,)).fetchone()),
                             ('Jessi', 'jessi@popcore.internal'))
        renamed = self.client.patch('/api/schedule/me', json={'name': 'The Owner'}, headers=self.headers('admin'))
        self.assertEqual((renamed.status_code, renamed.get_json()['name']), (200, 'The Owner'))

    def test_each_employee_reads_their_own_month_and_nobody_elses(self):
        jessi = self.headers('staff:jessi')
        mine = self.client.get('/api/schedule/reports/monthly/me?year=2026&month=9', headers=jessi)
        self.assertEqual(mine.status_code, 200, mine.get_json())
        mine = mine.get_json()
        as_manager = self.client.get(f'/api/schedule/reports/monthly/{self.jessi}?year=2026&month=9&store_code=ALL',
                                     headers=self.headers('manager')).get_json()
        self.assertEqual(mine, as_manager)   # the same shape, from the same code
        self.assertEqual((mine['employee']['id'], mine['store_code'], mine['total_hours']), (self.jessi, 'ALL', 12.0))
        self.assertEqual([shift['date'] for shift in mine['shifts']], ['2026-09-05', '2026-09-09'])
        one_store = self.client.get('/api/schedule/reports/monthly/me?year=2026&month=9&store_code=MK', headers=jessi).get_json()
        self.assertEqual((one_store['store_code'], one_store['total_hours']), ('MK', 4.0))
        # Whatever is asked for, it is the caller's own; other people's stay with managers.
        other = self.client.get(f'/api/schedule/reports/monthly/me?year=2026&month=9&employee_id={self.amy}', headers=jessi)
        self.assertEqual(other.get_json()['employee']['id'], self.jessi)
        for url in (f'/api/schedule/reports/monthly/{self.amy}?year=2026&month=9&store_code=ALL',
                    '/api/schedule/reports/monthly?year=2026&month=9&store_code=ALL'):
            self.assertEqual(self.client.get(url, headers=jessi).status_code, 403)
        self.assertEqual(self.client.get('/api/schedule/reports/monthly/me?year=x', headers=jessi).status_code, 400)
        self.assertEqual(self.client.get('/api/schedule/reports/monthly/me?store_code=XX', headers=jessi).status_code, 400)
        # Someone with no shifts gets an empty month, not an error.
        empty = self.client.get('/api/schedule/reports/monthly/me?year=2026&month=9', headers=self.headers('staff:new')).get_json()
        self.assertEqual((empty['total_hours'], empty['shifts']), (0, []))
