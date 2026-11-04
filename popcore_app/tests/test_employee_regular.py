"""转正 dates and the per-employee monthly hours breakdown."""
from contextlib import closing

from support import IsolatedApiCase
from blueprints import users


class EmployeeRegularTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(users.bp)
        with closing(self.connect()) as con:
            self.mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            self.employee = con.execute(
                "INSERT INTO employees(auth0_id,name,color) VALUES ('auth0|jessi','Jessi','#5b7fa6')"
            ).lastrowid
            self.other = con.execute(
                "INSERT INTO employees(auth0_id,name) VALUES ('auth0|amy','Amy')"
            ).lastrowid
            # September 2026 wage period runs Sep 4 – Oct 3 (month start day 4).
            for employee, date, start, end, actual, store in (
                (self.employee, '2026-09-03', '12:00', '20:00', None, self.store_id),   # previous period
                (self.employee, '2026-09-05', '12:00', '20:00', None, self.store_id),
                (self.employee, '2026-09-08', '11:00', '19:00', '19:20', self.store_id),
                (self.employee, '2026-09-09', '12:00', '16:00', None, self.mk),
                (self.employee, '2026-09-21', '12:00', '22:00', '00:30', self.store_id),
                (self.other,    '2026-09-08', '12:00', '20:00', None, self.store_id),
            ):
                con.execute(
                    """INSERT INTO shifts(employee_id,date,start_time,end_time,actual_end_time,assigned_by,store_id)
                       VALUES (?,?,?,?,?,'manager',?)""", (employee, date, start, end, actual, store))
            con.commit()

    def set_regular(self, value, role='manager', employee=None):
        return self.client.patch(f'/api/employees/{employee or self.employee}/regular',
                                 json={'regular_since': value}, headers=self.headers(role))

    def detail(self, store='ALL'):
        response = self.client.get(
            f'/api/schedule/reports/monthly/{self.employee}?year=2026&month=9&store_code={store}',
            headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_managers_set_and_clear_the_regular_date(self):
        saved = self.set_regular('2026-09-15')
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(saved.get_json()['regular_since'], '2026-09-15')
        stores = self.client.get('/api/employees/stores', headers=self.headers('manager')).get_json()
        self.assertEqual({row['name']: row['regular_since'] for row in stores}, {'Jessi': '2026-09-15', 'Amy': None})
        self.assertEqual(self.set_regular(None).get_json()['regular_since'], None)
        with closing(self.connect()) as con:
            self.assertIsNone(con.execute('SELECT regular_since FROM employees WHERE id=?', (self.employee,)).fetchone()[0])

    def test_regular_date_is_validated_and_manager_only(self):
        for bad in ('2026-9-15', '2026-02-30', 20260915, '', 'soon'):
            self.assertEqual(self.set_regular(bad).status_code, 400, bad)
        missing = self.client.patch(f'/api/employees/{self.employee}/regular', json={}, headers=self.headers('manager'))
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(self.set_regular('2026-09-15', role='staff').status_code, 403)
        self.assertEqual(self.set_regular('2026-09-15', employee=9999).status_code, 404)
        with closing(self.connect()) as con:
            self.assertIsNone(con.execute('SELECT regular_since FROM employees WHERE id=?', (self.employee,)).fetchone()[0])

    def test_shifts_and_report_rows_carry_the_regular_date(self):
        self.set_regular('2026-09-15')
        shifts = self.client.get('/api/schedule/shifts?start=2026-09-01&end=2026-09-30&store_code=ALL',
                                 headers=self.headers('manager')).get_json()
        self.assertEqual({row['employee_name']: row['regular_since'] for row in shifts},
                         {'Jessi': '2026-09-15', 'Amy': None})
        report = self.client.get('/api/schedule/reports/monthly?year=2026&month=9&store_code=ALL',
                                 headers=self.headers('manager')).get_json()
        rows = {row['name']: row for row in report['employees']}
        self.assertEqual(rows['Jessi']['regular_since'], '2026-09-15')
        self.assertIsNone(rows['Amy']['regular_since'])

    def test_detail_lists_each_shift_with_the_hours_it_counts(self):
        self.set_regular('2026-09-15')
        detail = self.detail()
        self.assertEqual((detail['period_start'], detail['period_end'], detail['store_code']),
                         ('2026-09-04', '2026-10-03', 'ALL'))
        self.assertEqual(detail['employee']['name'], 'Jessi')
        self.assertEqual(detail['employee']['color'], '#5b7fa6')
        self.assertEqual(detail['employee']['regular_since'], '2026-09-15')
        self.assertEqual(
            [(s['date'], s['store_code'], s['start_time'], s['end_time'], s['actual_end_time'], s['hours'])
             for s in detail['shifts']],
            [('2026-09-05', 'DT', '12:00', '20:00', None, 8.0),
             ('2026-09-08', 'DT', '11:00', '19:00', '19:20', 8.33),
             ('2026-09-09', 'MK', '12:00', '16:00', None, 4.0),
             # Finished after midnight: counts to 00:30 the next day.
             ('2026-09-21', 'DT', '12:00', '22:00', '00:30', 12.5)])
        self.assertEqual(detail['total_hours'], 32.83)
        # The report row for the same period and scope adds up to the same total.
        report = self.client.get('/api/schedule/reports/monthly?year=2026&month=9&store_code=ALL',
                                 headers=self.headers('manager')).get_json()
        self.assertEqual(next(r for r in report['employees'] if r['name'] == 'Jessi')['total_hours'], 32.83)

    def test_detail_follows_the_report_store_scope(self):
        detail = self.detail('MK')
        self.assertEqual(detail['store_code'], 'MK')
        self.assertEqual([s['date'] for s in detail['shifts']], ['2026-09-09'])
        self.assertEqual(detail['total_hours'], 4.0)

    def test_detail_needs_a_manager_store_and_known_employee(self):
        url = f'/api/schedule/reports/monthly/{self.employee}?year=2026&month=9'
        self.assertEqual(self.client.get(url + '&store_code=ALL', headers=self.headers('staff')).status_code, 403)
        self.assertEqual(self.client.get(url, headers=self.headers('manager')).status_code, 400)
        self.assertEqual(self.client.get('/api/schedule/reports/monthly/9999?year=2026&month=9&store_code=ALL',
                                         headers=self.headers('manager')).status_code, 404)
