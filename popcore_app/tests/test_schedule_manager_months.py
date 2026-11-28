"""店长/副店长 see only this month and next in 排班 (owner, 2026-11-27: "the manager can only see the schedule for that
month, not all of them"; picked this month + next). Admins see every month; everyone keeps their own months."""
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
            self.shifts = {}
            for day in ('2026-10-20', '2026-11-10', '2026-12-20', '2027-01-15'):
                self.shifts[day] = con.execute(
                    """INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                       VALUES (?,?,'12:00','20:00','manager',?)""", (self.employee, day, self.store_id)).lastrowid
            for day in ('2026-10-21', '2026-11-11'):
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

    def test_manager_reads_only_this_month_and_next(self):
        self.assertEqual(self.dates('manager'), ['2026-11-10', '2026-12-20'])
        self.assertEqual(self.dates('manager', store='ALL'), ['2026-11-10', '2026-12-20'])
        self.assertEqual(sorted(r['date'] for r in self.client.get(f'{SHIFTS}?store_code=DT', headers=self.headers('manager')).get_json()),
                         ['2026-11-10', '2026-12-20'])
        self.assertEqual(self.dates('manager', '/api/schedule/availability'), ['2026-11-11'])
        self.assertEqual(self.dates('admin'), ['2026-10-20', '2026-11-10', '2026-12-20', '2027-01-15'])
        self.assertEqual(self.dates('admin', '/api/schedule/availability'), ['2026-10-21', '2026-11-11'])
        # Everyone's own shifts stay theirs in every month.
        self.assertEqual(self.dates('staff'), ['2026-10-20', '2026-11-10', '2026-12-20', '2027-01-15'])
        own = self.client.get('/api/schedule/shifts/me?store_code=ALL', headers=self.headers('staff')).get_json()
        self.assertEqual(len(own), 4)

    def test_window_reaches_back_to_the_wage_month_and_the_cycle_being_worked(self):
        self.at(2026, 12, 2)  # the November wage month (Nov 4 – Dec 3) is still running
        self.assertEqual(self.dates('manager'), ['2026-11-10', '2026-12-20', '2027-01-15'])
        config = self.client.get('/api/schedule/config', headers=self.headers('manager')).get_json()
        self.assertEqual(config['manager_window'], {'first': '2026-11-04', 'last': '2027-01-31', 'months': ['2026-11', '2026-12']})
        self.at(2026, 12, 5)  # the two-week cycle being worked began on Nov 23: it stays whole
        config = self.client.get('/api/schedule/config', headers=self.headers('manager')).get_json()
        self.assertEqual(config['manager_window']['first'], '2026-11-23')
        self.at(2026, 11, 27)
        config = self.client.get('/api/schedule/config', headers=self.headers('manager')).get_json()
        self.assertEqual(config['manager_window'], {'first': '2026-11-01', 'last': '2026-12-31', 'months': ['2026-11', '2026-12']})
        for role in ('admin', 'staff'):
            self.assertIsNone(self.client.get('/api/schedule/config', headers=self.headers(role)).get_json()['manager_window'])

    def closed(self, response):
        self.assertEqual((response.status_code, response.get_json().get('code')), (403, 'schedule_month_closed'))

    def test_manager_changes_only_this_month_and_next(self):
        manager, admin = self.headers('manager'), self.headers('admin')
        body = {'employee_id': self.employee, 'store_code': 'DT', 'start_time': '12:00', 'end_time': '18:00'}
        self.closed(self.client.post(SHIFTS, json={**body, 'date': '2026-10-30'}, headers=manager))
        self.closed(self.client.post(SHIFTS, json={**body, 'date': '2027-01-02'}, headers=manager))
        self.assertEqual(self.client.post(SHIFTS, json={**body, 'date': '2026-12-30'}, headers=manager).status_code, 201)
        old = self.shifts['2026-10-20']
        self.closed(self.client.patch(f'{SHIFTS}/{old}', json={'end_time': '19:00'}, headers=manager))
        self.closed(self.client.delete(f'{SHIFTS}/{old}', headers=manager))
        self.closed(self.client.get(f'/api/schedule/conflicts?employee_id={self.employee}&date=2026-10-30&store_code=DT',
                                    headers=manager))
        self.assertEqual(self.client.patch(f'{SHIFTS}/{self.shifts["2026-11-10"]}', json={'end_time': '19:00'},
                                           headers=manager).status_code, 200)
        self.assertEqual(self.client.patch(f'{SHIFTS}/{old}', json={'end_time': '19:00'}, headers=admin).status_code, 200)
        self.assertEqual(self.client.delete(f'{SHIFTS}/{old}', headers=admin).status_code, 200)

    def test_notes_and_checklist_follow_the_window(self):
        manager = self.headers('manager')
        self.closed(self.client.get('/api/schedule/notes?key=month:2026-10', headers=manager))
        self.closed(self.client.get('/api/schedule/checklist?key=fortnight:2026-10-05', headers=manager))
        self.closed(self.client.put('/api/schedule/notes', json={'period_key': 'month:2027-01', 'content': 'x'}, headers=manager))
        self.closed(self.client.put('/api/schedule/checklist', json={'period_key': 'day:2026-10-31', 'employee_id': self.employee,
                                                                     'considered': True}, headers=manager))
        self.assertEqual(self.client.get('/api/schedule/notes?key=month:2026-11', headers=manager).status_code, 200)
        self.assertEqual(self.client.get('/api/schedule/notes?key=week:2026-10-26', headers=manager).status_code, 200)  # to Nov 1
        self.assertEqual(self.client.get('/api/schedule/notes?key=month:2026-13', headers=manager).status_code, 400)
        self.assertEqual(self.client.get('/api/schedule/notes?key=month:2026-10', headers=self.headers('admin')).status_code, 200)

    def test_monthly_hours_only_this_wage_month_and_next(self):
        manager, admin = self.headers('manager'), self.headers('admin')
        for path in ('/api/schedule/reports/monthly?store_code=DT', f'/api/schedule/reports/monthly/{self.employee}?store_code=DT',
                     f'/api/schedule/employees/{self.employee}/hours?'):
            self.closed(self.client.get(f'{path}&year=2026&month=10', headers=manager))
            self.closed(self.client.get(f'{path}&year=2027&month=1', headers=manager))
            for month in (11, 12):
                self.assertEqual(self.client.get(f'{path}&year=2026&month={month}', headers=manager).status_code, 200, path)
            self.assertEqual(self.client.get(f'{path}&year=2026&month=10', headers=admin).status_code, 200, path)
        # One's own month report stays one's own in every month.
        own = self.client.get('/api/schedule/reports/monthly/me?year=2026&month=10', headers=self.headers('staff'))
        self.assertEqual(own.status_code, 200)
        self.at(2026, 12, 2)  # still the November wage month: November and December open
        self.assertEqual(self.client.get('/api/schedule/reports/monthly?store_code=DT&year=2026&month=11', headers=manager).status_code, 200)
        self.closed(self.client.get('/api/schedule/reports/monthly?store_code=DT&year=2027&month=1', headers=manager))
        self.at(2026, 12, 10)  # the December wage month: December and January open, across the year end
        self.assertEqual(self.client.get('/api/schedule/reports/monthly?store_code=DT&year=2027&month=1', headers=manager).status_code, 200)
        self.closed(self.client.get('/api/schedule/reports/monthly?store_code=DT&year=2026&month=11', headers=manager))
