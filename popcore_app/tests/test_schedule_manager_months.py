"""店长/副店长 only look at 排班, and only at this month (owner, 2026-11-28: managers must not edit the schedule and see
one month; replaces 2026-11-27's this month + next). Admins change and see every month; everyone keeps their own."""
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
            for day in ('2026-10-20', '2026-11-10', '2026-12-20', '2027-01-15'):
                self.shifts[day] = con.execute(
                    """INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                       VALUES (?,?,'12:00','20:00','admin',?)""", (self.employee, day, self.store_id)).lastrowid
            for day in ('2026-10-21', '2026-11-11', '2026-12-21'):
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

    def test_manager_reads_only_this_month(self):
        self.assertEqual(self.dates('manager'), ['2026-11-10'])
        self.assertEqual(self.dates('manager', store='ALL'), ['2026-11-10'])
        self.assertEqual([r['date'] for r in self.client.get(f'{SHIFTS}?store_code=DT', headers=self.headers('manager')).get_json()],
                         ['2026-11-10'])
        self.assertEqual(self.dates('manager', '/api/schedule/availability'), ['2026-11-11'])
        self.assertEqual(self.dates('admin'), ['2026-10-20', '2026-11-10', '2026-12-20', '2027-01-15'])
        self.assertEqual(self.dates('admin', '/api/schedule/availability'), ['2026-10-21', '2026-11-11', '2026-12-21'])
        # Everyone's own shifts stay theirs in every month.
        self.assertEqual(self.dates('staff'), ['2026-10-20', '2026-11-10', '2026-12-20', '2027-01-15'])
        own = self.client.get('/api/schedule/shifts/me?store_code=ALL', headers=self.headers('staff')).get_json()
        self.assertEqual(len(own), 4)

    def test_the_month_is_the_calendar_month_in_toronto(self):
        self.assertEqual(self.window(), {'first': '2026-11-01', 'last': '2026-11-30', 'months': ['2026-11']})
        self.at(2026, 12, 2)  # December's calendar; 月工时 is still the November wage month (Nov 4 – Dec 3)
        self.assertEqual(self.window(), {'first': '2026-12-01', 'last': '2026-12-31', 'months': ['2026-11']})
        self.assertEqual(self.dates('manager'), ['2026-12-20'])
        self.clock.now.return_value = datetime(2027, 1, 1, 3, tzinfo=timezone.utc)  # 22:00 on Dec 31 in Toronto
        self.assertEqual(self.window()['first'], '2026-12-01')
        self.at(2027, 1, 15)  # across the year end
        self.assertEqual(self.window(), {'first': '2027-01-01', 'last': '2027-01-31', 'months': ['2027-01']})
        self.assertEqual(self.dates('manager'), ['2027-01-15'])
        for role in ('admin', 'staff'):
            self.assertIsNone(self.window(role))

    def test_manager_changes_nothing(self):
        shift = self.shifts['2026-11-10']  # inside the month a manager sees
        body = {'employee_id': self.employee, 'store_code': 'DT', 'date': '2026-11-20', 'start_time': '12:00', 'end_time': '18:00'}
        changes = [
            ('post', SHIFTS, body),
            ('patch', f'{SHIFTS}/{shift}', {'end_time': '19:00'}),
            ('patch', f'{SHIFTS}/{shift}', {'actual_end_time': '20:30'}),
            ('delete', f'{SHIFTS}/{shift}', None),
            ('get', f'/api/schedule/conflicts?employee_id={self.employee}&date=2026-11-20&store_code=DT', None),
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

    def test_notes_and_checklist_reads_follow_the_month(self):
        manager = self.headers('manager')
        for key in ('month:2026-10', 'month:2026-12', 'fortnight:2026-10-12', 'day:2026-12-01'):
            self.closed(self.client.get(f'/api/schedule/notes?key={key}', headers=manager))
            self.closed(self.client.get(f'/api/schedule/checklist?key={key}', headers=manager))
        self.assertEqual(self.client.get('/api/schedule/notes?key=month:2026-11', headers=manager).status_code, 200)
        self.assertEqual(self.client.get('/api/schedule/notes?key=week:2026-10-26', headers=manager).status_code, 200)  # to Nov 1
        self.assertEqual(self.client.get('/api/schedule/notes?key=month:2026-13', headers=manager).status_code, 400)
        self.assertEqual(self.client.get('/api/schedule/notes?key=month:2026-10', headers=self.headers('admin')).status_code, 200)

    def test_monthly_hours_only_this_wage_month(self):
        manager, admin = self.headers('manager'), self.headers('admin')
        for path in ('/api/schedule/reports/monthly?store_code=DT', f'/api/schedule/reports/monthly/{self.employee}?store_code=DT',
                     f'/api/schedule/employees/{self.employee}/hours?'):
            for month in (10, 12):
                self.closed(self.client.get(f'{path}&year=2026&month={month}', headers=manager))
                self.assertEqual(self.client.get(f'{path}&year=2026&month={month}', headers=admin).status_code, 200, path)
            self.assertEqual(self.client.get(f'{path}&year=2026&month=11', headers=manager).status_code, 200, path)
        # One's own month report stays one's own in every month.
        own = self.client.get('/api/schedule/reports/monthly/me?year=2026&month=10', headers=self.headers('staff'))
        self.assertEqual(own.status_code, 200)
        self.at(2026, 12, 2)  # still the November wage month
        self.assertEqual(self.client.get('/api/schedule/reports/monthly?store_code=DT&year=2026&month=11', headers=manager).status_code, 200)
        self.closed(self.client.get('/api/schedule/reports/monthly?store_code=DT&year=2026&month=12', headers=manager))
        self.at(2026, 12, 10)  # the December wage month
        self.assertEqual(self.client.get('/api/schedule/reports/monthly?store_code=DT&year=2026&month=12', headers=manager).status_code, 200)
        self.closed(self.client.get('/api/schedule/reports/monthly?store_code=DT&year=2026&month=11', headers=manager))
