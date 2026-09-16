from contextlib import closing
from datetime import datetime, timezone
from unittest.mock import patch

from support import IsolatedApiCase, db


URL = '/api/schedule/attendance/today'


class AttendanceTests(IsolatedApiCase):
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
            con.commit()
        clock_patch = patch('blueprints.schedule._dt.datetime')
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.clock.now.return_value = datetime(2026, 9, 17, 2, tzinfo=timezone.utc)

    def test_own_shift_server_time_retry_and_retained_record(self):
        status = self.client.get(URL, headers=self.headers()).get_json()
        self.assertEqual(status['business_date'], '2026-09-16')
        self.assertEqual(status['shift']['id'], self.shift)
        self.assertIsNone(status['attendance'])
        punched = self.client.post(URL, json={'shift_id': self.shift}, headers=self.headers())
        self.assertEqual(punched.status_code, 200)
        record = punched.get_json()['attendance']
        self.assertEqual(record['employee_id'], self.employee)
        self.assertEqual(record['store_id'], self.store_id)
        self.assertEqual(record['punched_in_at'], '2026-09-17T02:00:00+00:00')
        other = self.client.get(URL, headers=self.headers('staff:other')).get_json()
        self.assertIsNone(other['attendance'])
        db.migrate_db()
        self.assertEqual(self.client.get(URL, headers=self.headers()).get_json()['attendance'], record)
        self.clock.now.return_value = datetime(2026, 9, 17, 3, tzinfo=timezone.utc)
        replay = self.client.post(URL, json={'shift_id': self.shift}, headers=self.headers())
        self.assertEqual(replay.get_json()['attendance'], record)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM schedule_attendance').fetchone()[0], 1)
            con.execute('DELETE FROM shifts WHERE id=?', (self.shift,))
            con.commit()
        retained = self.client.get(URL, headers=self.headers()).get_json()
        self.assertIsNone(retained['shift'])
        self.assertEqual(retained['attendance']['punched_in_at'], record['punched_in_at'])

    def test_cannot_punch_for_other_people_or_from_availability(self):
        for role in ('staff:other', 'admin:other'):
            status = self.client.get(URL, headers=self.headers(role)).get_json()
            self.assertIsNone(status['shift'])
            self.assertIsNone(status['attendance'])
            self.assertEqual(self.client.post(URL, json={'shift_id': self.shift}, headers=self.headers(role)).status_code, 403)
        self.assertEqual(self.client.get(URL, headers=self.headers('viewer')).status_code, 403)
        self.assertEqual(self.client.post(URL, json={'shift_id': self.shift}).status_code, 401)
        with closing(self.connect()) as con:
            con.execute('DELETE FROM shifts')
            con.execute("""INSERT INTO availability(employee_id,date,start_time,end_time,store_id)
                           VALUES (?,'2026-09-16','12:00','20:00',?)""", (self.employee, self.store_id))
            con.commit()
        self.assertIsNone(self.client.get(URL, headers=self.headers()).get_json()['shift'])
        self.assertEqual(self.client.post(URL, json={'shift_id': self.shift}, headers=self.headers()).status_code, 403)

    def test_rejects_inactive_employee_stale_shift_and_client_attribution(self):
        for body in ({}, [], {'shift_id': True}, {'shift_id': -1}, {'shift_id': '1'},
                     {'shift_id': self.shift, 'employee_id': self.employee},
                     {'shift_id': self.shift, 'punched_in_at': '2026-09-16T12:00:00Z'}):
            self.assertEqual(self.client.post(URL, json=body, headers=self.headers()).status_code, 400)
        self.assertEqual(self.client.post(URL, json={'shift_id': self.shift + 1}, headers=self.headers()).status_code, 403)
        with closing(self.connect()) as con:
            con.execute('UPDATE employees SET is_active=0 WHERE id=?', (self.employee,))
            con.commit()
        self.assertIsNone(self.client.get(URL, headers=self.headers()).get_json()['shift'])
        self.assertEqual(self.client.post(URL, json={'shift_id': self.shift}, headers=self.headers()).status_code, 403)

    def test_new_toronto_day_requires_todays_assigned_shift(self):
        self.assertEqual(self.client.post(URL, json={'shift_id': self.shift}, headers=self.headers()).status_code, 200)
        self.clock.now.return_value = datetime(2026, 9, 17, 5, tzinfo=timezone.utc)
        status = self.client.get(URL, headers=self.headers()).get_json()
        self.assertEqual(status['business_date'], '2026-09-17')
        self.assertIsNone(status['attendance'])
        self.assertIsNone(status['shift'])
        self.assertEqual(self.client.post(URL, json={'shift_id': self.shift}, headers=self.headers()).status_code, 403)
        with closing(self.connect()) as con:
            tomorrow = con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                VALUES (?,'2026-09-17','12:00','20:00','manager',?)""", (self.employee, self.store_id)).lastrowid
            con.commit()
        result = self.client.post(URL, json={'shift_id': tomorrow}, headers=self.headers())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()['attendance']['business_date'], '2026-09-17')
