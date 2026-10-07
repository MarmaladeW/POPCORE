from contextlib import closing
from datetime import datetime, timezone
from unittest.mock import patch

from support import IsolatedApiCase, db


REPORT = '/api/schedule/reports/monthly?year=2026&month=9&store_code=ALL'


class ActualFinishTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.employee = con.execute(
                "INSERT INTO employees(auth0_id,name) VALUES ('auth0|staff','Cashier')"
            ).lastrowid
            self.shift = con.execute(
                """INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                   VALUES (?,'2026-09-16','12:00','20:00','manager',?)""",
                (self.employee, self.store_id),
            ).lastrowid
            self.future = con.execute(
                """INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                   VALUES (?,'2026-09-18','12:00','20:00','manager',?)""",
                (self.employee, self.store_id),
            ).lastrowid
            con.commit()
        clock_patch = patch('blueprints.schedule._dt.datetime')
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)
        # 22:00 Toronto on 2026-09-16
        self.clock.now.return_value = datetime(2026, 9, 17, 2, tzinfo=timezone.utc)

    def patch_shift(self, data, role='manager', shift=None):
        return self.client.patch(
            f'/api/schedule/shifts/{shift or self.shift}', json=data, headers=self.headers(role),
        )

    def hours(self):
        report = self.client.get(REPORT, headers=self.headers('manager')).get_json()
        employee = self.client.get(
            f'/api/schedule/employees/{self.employee}/hours?year=2026&month=9',
            headers=self.headers('manager'),
        ).get_json()
        row = report['employees'][0]
        return row['total_hours'], row['weeks']['2026-W38']['days']['2026-09-16'], employee['total_hours'], employee['by_store']['DT']

    def test_hours_follow_the_actual_finish_and_fall_back_to_planned(self):
        self.assertEqual(self.hours(), (16.0, 8.0, 16.0, 16.0))
        late = self.patch_shift({'actual_end_time': '20:45'})
        self.assertEqual(late.status_code, 200, late.get_json())
        self.assertEqual((late.get_json()['end_time'], late.get_json()['actual_end_time']), ('20:00', '20:45'))
        self.assertEqual(self.hours(), (16.75, 8.75, 16.75, 16.75))
        early = self.patch_shift({'actual_end_time': '18:30'}, role='admin')
        self.assertEqual(early.status_code, 200, early.get_json())
        self.assertEqual(self.hours(), (14.5, 6.5, 14.5, 14.5))
        # Other edits keep the recorded finish.
        self.assertEqual(self.patch_shift({'notes': 'left early'}).get_json()['actual_end_time'], '18:30')
        # Up to 01:00 is the next day: 12:00 to 00:45 is 12.75 hours.
        self.assertEqual(self.patch_shift({'actual_end_time': '00:45'}).status_code, 200)
        self.assertEqual(self.hours(), (20.75, 12.75, 20.75, 20.75))
        self.assertEqual(self.patch_shift({'actual_end_time': '01:00'}).status_code, 200)
        self.assertEqual(self.hours(), (21.0, 13.0, 21.0, 21.0))
        self.assertEqual(self.patch_shift({'start_time': '13:00'}).status_code, 200)
        self.assertEqual(self.hours(), (20.0, 12.0, 20.0, 20.0))
        self.assertEqual(self.patch_shift({'start_time': '12:00'}).status_code, 200)
        cleared = self.patch_shift({'actual_end_time': None})
        self.assertEqual(cleared.status_code, 200, cleared.get_json())
        self.assertIsNone(cleared.get_json()['actual_end_time'])
        self.assertEqual(self.hours(), (16.0, 8.0, 16.0, 16.0))

    def test_only_managers_record_and_invalid_finishes_change_nothing(self):
        before = self.snapshot(['shifts'])
        self.assertEqual(self.patch_shift({'actual_end_time': '20:30'}, role='staff').status_code, 403)
        for value in ['8:30', '24:00', '', 2030, '12:00', '11:59', '01:01', '06:00']:
            result = self.patch_shift({'actual_end_time': value})
            self.assertEqual(result.status_code, 400, value)
        future = self.patch_shift({'actual_end_time': '20:30'}, shift=self.future)
        self.assertEqual(future.status_code, 400)
        self.assertEqual(self.snapshot(['shifts']), before)
        self.assertEqual(self.patch_shift({'actual_end_time': '13:00'}).status_code, 200)
        before = self.snapshot(['shifts'])
        # Moving the start past the recorded finish is rejected.
        self.assertEqual(self.patch_shift({'start_time': '13:00'}).status_code, 400)
        self.assertEqual(self.snapshot(['shifts']), before)

    def test_migration_is_repeatable_and_keeps_recorded_finishes(self):
        self.assertEqual(self.patch_shift({'actual_end_time': '19:15'}).status_code, 200)
        with closing(self.connect()) as con:
            con.execute("DELETE FROM _migrations WHERE name='add_actual_end_time_to_shifts'")
            con.commit()
        db.migrate_db(); db.migrate_db()
        with closing(self.connect()) as con:
            self.assertEqual(
                con.execute('SELECT actual_end_time FROM shifts WHERE id=?', (self.shift,)).fetchone()[0],
                '19:15',
            )
