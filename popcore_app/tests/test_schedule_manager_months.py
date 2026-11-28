"""店长/副店长 only look at 排班, and only at the next two weeks (2026-11-28: managers must not edit the schedule and
always see the next two weeks, today and the 13 days after it; replaces the wage month and 2026-11-27's this month +
next). 月工时 is one's own (same day). Admins change and see every month; everyone keeps their own."""
from contextlib import closing
from datetime import datetime, timezone
from unittest.mock import patch

from support import IsolatedApiCase


SHIFTS = '/api/schedule/shifts'


class ManagerMonthTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.employee = con.execute(
                "INSERT INTO employees(auth0_id,name) VALUES ('auth0|staff','Cashier')").lastrowid
            self.trainee = con.execute(
                "INSERT INTO employees(auth0_id,name,is_trainee) VALUES ('trainee|one','Trainee',1)").lastrowid
            self.shifts = {}
            for day in ('2026-10-20', '2026-11-10', '2026-11-30', '2026-12-20', '2027-01-15'):
                self.shifts[day] = con.execute(
                    """INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                       VALUES (?,?,'12:00','20:00','admin',?)""", (self.employee, day, self.store_id)).lastrowid
            for day in ('2026-10-21', '2026-11-11', '2026-12-01', '2026-12-21'):
                con.execute("""INSERT INTO availability(employee_id,date,start_time,end_time,store_id)
                               VALUES (?,?,'12:00','20:00',?)""", (self.employee, day, self.store_id))
            con.commit()
        clock = patch('blueprints.schedule._dt.datetime')
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.at(2026, 11, 27)

    def at(self, year, month, day):
        self.clock.now.return_value = datetime(year, month, day, 17, tzinfo=timezone.utc)  # noon in Toronto

    def dates(self, role, path=SHIFTS, store='DT'):
        response = self.client.get(f'{path}?store_code={store}&start=2026-01-01&end=2027-12-31', headers=self.headers(role))
        self.assertEqual(response.status_code, 200)
        return sorted(row['date'] for row in response.get_json())

    def window(self, role='manager'):
        return self.client.get('/api/schedule/config', headers=self.headers(role)).get_json()['manager_window']

    def test_manager_reads_only_the_next_two_weeks(self):
        self.assertEqual(self.dates('manager'), ['2026-11-30'])
        self.assertEqual(self.dates('manager', store='ALL'), ['2026-11-30'])
        self.assertEqual([r['date'] for r in self.client.get(f'{SHIFTS}?store_code=DT', headers=self.headers('manager')).get_json()],
                         ['2026-11-30'])
        self.assertEqual(self.dates('manager', '/api/schedule/availability'), ['2026-12-01'])
        every = ['2026-10-20', '2026-11-10', '2026-11-30', '2026-12-20', '2027-01-15']
        self.assertEqual(self.dates('admin'), every)
        self.assertEqual(self.dates('admin', '/api/schedule/availability'), ['2026-10-21', '2026-11-11', '2026-12-01', '2026-12-21'])
        # Everyone's own shifts stay theirs in every month.
        self.assertEqual(self.dates('staff'), every)
        own = self.client.get('/api/schedule/shifts/me?store_code=ALL', headers=self.headers('staff')).get_json()
        self.assertEqual(len(own), 5)

    def test_the_window_is_today_and_the_next_13_days_in_toronto(self):
        self.assertEqual(self.window(), {'first': '2026-11-27', 'last': '2026-12-10'})
        self.clock.now.return_value = datetime(2026, 11, 28, 3, tzinfo=timezone.utc)  # 22:00 on Nov 27 in Toronto
        self.assertEqual(self.window(), {'first': '2026-11-27', 'last': '2026-12-10'})
        self.at(2026, 12, 1)  # it moves every day; yesterday is gone
        self.assertEqual(self.window(), {'first': '2026-12-01', 'last': '2026-12-14'})
        self.assertEqual(self.dates('manager'), [])
        self.assertEqual(self.dates('manager', '/api/schedule/availability'), ['2026-12-01'])
        self.at(2026, 12, 15)
        self.assertEqual(self.dates('manager'), ['2026-12-20'])
        self.assertEqual(self.dates('manager', '/api/schedule/availability'), ['2026-12-21'])
        self.at(2026, 12, 25)  # across the year end
        self.assertEqual(self.window(), {'first': '2026-12-25', 'last': '2027-01-07'})
        self.at(2027, 1, 5)
        self.assertEqual(self.dates('manager'), ['2027-01-15'])
        for role in ('admin', 'staff'):
            self.assertIsNone(self.window(role))

    def test_manager_changes_nothing(self):
        shift, worked = self.shifts['2026-11-30'], self.shifts['2026-11-10']  # one the manager sees, one already worked
        body = {'employee_id': self.employee, 'store_code': 'DT', 'date': '2026-12-02', 'start_time': '12:00', 'end_time': '18:00'}
        changes = [
            ('post', SHIFTS, body),
            ('patch', f'{SHIFTS}/{shift}', {'end_time': '19:00'}),
            ('patch', f'{SHIFTS}/{worked}', {'actual_end_time': '20:30'}),
            ('delete', f'{SHIFTS}/{shift}', None),
            ('get', f'/api/schedule/conflicts?employee_id={self.employee}&date=2026-12-02&store_code=DT', None),
            ('put', '/api/schedule/notes', {'period_key': 'month:2026-11', 'content': 'x'}),
            ('put', '/api/schedule/checklist', {'period_key': 'month:2026-11', 'employee_id': self.employee, 'considered': True}),
            ('put', '/api/schedule/reminder', {'content': 'Changed'}),
            ('post', '/api/schedule/trainees', {'name': 'New'}),
            ('delete', f'/api/schedule/trainees/{self.trainee}', None),
        ]
        tables = ['shifts', 'schedule_notes', 'schedule_checklist', 'app_settings', 'employees']
        before = self.snapshot(tables)
        for method, path, data in changes:
            with self.subTest(method=method, path=path):
                kwargs = {'json': data} if data is not None else {}
                refused = getattr(self.client, method)(path, headers=self.headers('manager'), **kwargs)
                self.assertEqual((refused.status_code, refused.get_json().get('code')), (403, 'schedule_read_only'))
                self.assertEqual(getattr(self.client, method)(path, headers=self.headers('staff'), **kwargs).status_code, 403)
        self.assertEqual(self.snapshot(tables), before)
        for method, path, data in changes:
            with self.subTest(admin=path):
                kwargs = {'json': data} if data is not None else {}
                self.assertIn(getattr(self.client, method)(path, headers=self.headers('admin'), **kwargs).status_code, (200, 201))
        # A manager's own availability, 打卡 and calendar feed are their own.
        own = self.client.post('/api/schedule/availability', headers=self.headers('manager'),
                               json={'store_code': 'DT', 'date': '2026-11-29', 'start_time': '12:00', 'end_time': '20:00'})
        self.assertEqual(own.status_code, 201, own.get_json())
        self.assertEqual(self.client.post('/api/schedule/calendar-feed/reset', headers=self.headers('manager')).status_code, 200)

    def closed(self, response):
        self.assertEqual((response.status_code, response.get_json().get('code')), (403, 'schedule_month_closed'))

    def test_notes_and_checklist_reads_follow_the_two_weeks(self):
        manager = self.headers('manager')
        for key in ('month:2026-10', 'week:2026-11-16', 'day:2026-11-26', 'day:2026-12-11', 'month:2027-01'):
            self.closed(self.client.get(f'/api/schedule/notes?key={key}', headers=manager))
            self.closed(self.client.get(f'/api/schedule/checklist?key={key}', headers=manager))
        for key in ('month:2026-11', 'month:2026-12', 'week:2026-11-23', 'fortnight:2026-12-07', 'day:2026-12-10'):  # each touches Nov 27 – Dec 10
            self.assertEqual(self.client.get(f'/api/schedule/notes?key={key}', headers=manager).status_code, 200, key)
        self.assertEqual(self.client.get('/api/schedule/notes?key=month:2026-13', headers=manager).status_code, 400)
        self.assertEqual(self.client.get('/api/schedule/notes?key=month:2026-10', headers=self.headers('admin')).status_code, 200)

    def test_monthly_hours_are_ones_own(self):
        manager, admin = self.headers('manager'), self.headers('admin')
        with closing(self.connect()) as con:
            mine = con.execute("INSERT INTO employees(auth0_id,name) VALUES ('auth0|manager','Store Manager')").lastrowid
            con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                           VALUES (?,'2026-10-15','12:00','20:00','admin',?)""", (mine, self.store_id))
            con.commit()
        # The team report, another person's month and another person's hours are the admin's, in every month.
        for path in ('/api/schedule/reports/monthly?store_code=DT', f'/api/schedule/reports/monthly/{self.employee}?store_code=DT',
                     f'/api/schedule/employees/{self.employee}/hours?', f'/api/schedule/employees/{mine}/hours?'):
            for month in (10, 11):
                self.assertEqual(self.client.get(f'{path}&year=2026&month={month}', headers=manager).status_code, 403, path)
                self.assertEqual(self.client.get(f'{path}&year=2026&month={month}', headers=admin).status_code, 200, path)
        # One's own month stays one's own in every month, a manager's as a staff member's.
        own = self.client.get('/api/schedule/reports/monthly/me?year=2026&month=10', headers=manager)
        self.assertEqual((own.status_code, own.get_json()['employee']['id'], own.get_json()['total_hours']), (200, mine, 8.0))
        self.assertEqual(self.client.get('/api/schedule/reports/monthly/me?year=2026&month=10', headers=self.headers('staff'))
                         .get_json()['total_hours'], 8.0)
