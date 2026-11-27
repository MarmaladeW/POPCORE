from contextlib import closing
from datetime import datetime, timezone
from unittest.mock import patch

from support import IsolatedApiCase, db


URL = '/api/schedule/attendance/today'
AT_STORE = {'lat': 43.6533, 'lng': -79.3831, 'accuracy': 20}


class AttendanceCase(IsolatedApiCase):
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
            con.execute("INSERT INTO app_settings(key, value) VALUES ('punch_places', ?)",
                        ('{"DT": {"lat": 43.6532, "lng": -79.3832, "saved_at": "2026-09-01T12:00:00+00:00"}}',))
            con.commit()
        clock_patch = patch('blueprints.schedule._dt.datetime')
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.clock.now.return_value = datetime(2026, 9, 17, 2, tzinfo=timezone.utc)


class AttendanceTests(AttendanceCase):
    def test_own_shift_server_time_retry_and_retained_record(self):
        status = self.client.get(URL, headers=self.headers()).get_json()
        self.assertEqual(status['business_date'], '2026-09-16')
        self.assertEqual(status['shift']['id'], self.shift)
        self.assertIsNone(status['attendance'])
        punched = self.client.post(URL, json={'shift_id': self.shift, **AT_STORE}, headers=self.headers())
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
        replay = self.client.post(URL, json={'shift_id': self.shift, **AT_STORE}, headers=self.headers())
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
        self.assertEqual(self.client.post(URL, json={'shift_id': self.shift, **AT_STORE}, headers=self.headers()).status_code, 403)

    def test_rejects_inactive_employee_stale_shift_and_client_attribution(self):
        for body in ({}, [], {'shift_id': True}, {'shift_id': -1}, {'shift_id': '1'},
                     {'shift_id': self.shift, 'employee_id': self.employee},
                     {'shift_id': self.shift, 'punched_in_at': '2026-09-16T12:00:00Z'}):
            self.assertEqual(self.client.post(URL, json=body, headers=self.headers()).status_code, 400)
        self.assertEqual(self.client.post(URL, json={'shift_id': self.shift + 1}, headers=self.headers()).status_code, 403)
        with closing(self.connect()) as con:
            con.execute('UPDATE employees SET is_active=0 WHERE id=?', (self.employee,))
            con.commit()
        # A removed employee's sign-in is refused outright.
        status = self.client.get(URL, headers=self.headers())
        self.assertEqual((status.status_code, status.get_json()['code']), (403, 'account_disabled'))
        self.assertEqual(self.client.post(URL, json={'shift_id': self.shift, **AT_STORE}, headers=self.headers()).status_code, 403)

    def test_new_toronto_day_requires_todays_assigned_shift(self):
        self.assertEqual(self.client.post(URL, json={'shift_id': self.shift, **AT_STORE}, headers=self.headers()).status_code, 200)
        self.clock.now.return_value = datetime(2026, 9, 17, 5, tzinfo=timezone.utc)
        status = self.client.get(URL, headers=self.headers()).get_json()
        self.assertEqual(status['business_date'], '2026-09-17')
        self.assertIsNone(status['attendance'])
        self.assertIsNone(status['shift'])
        self.assertEqual(self.client.post(URL, json={'shift_id': self.shift, **AT_STORE}, headers=self.headers()).status_code, 403)
        with closing(self.connect()) as con:
            tomorrow = con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                VALUES (?,'2026-09-17','12:00','20:00','manager',?)""", (self.employee, self.store_id)).lastrowid
            con.commit()
        self.clock.now.return_value = datetime(2026, 9, 17, 15, 30, tzinfo=timezone.utc)
        result = self.client.post(URL, json={'shift_id': tomorrow, **AT_STORE}, headers=self.headers())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()['attendance']['business_date'], '2026-09-17')


class PunchAtStoreTests(AttendanceCase):
    """打卡 counts only at the store (GPS) and from 30 minutes before the shift (owner, 2026-11-27)."""

    def punch(self, **location):
        return self.client.post(URL, json={'shift_id': self.shift, **location}, headers=self.headers())

    def refused(self, response):
        self.assertEqual(response.status_code, 403)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM schedule_attendance').fetchone()[0], 0)
        return response.get_json()['code']

    def test_opens_thirty_minutes_before_the_shift(self):
        self.clock.now.return_value = datetime(2026, 9, 16, 15, 29, tzinfo=timezone.utc)  # 11:29 Toronto
        punch = self.client.get(URL, headers=self.headers()).get_json()['punch']
        self.assertEqual(punch, {'opens_at': '11:30', 'radius_m': 150, 'too_early': True, 'place_set': True})
        response = self.punch(**AT_STORE)
        self.assertEqual(self.refused(response), 'punch_too_early')
        self.assertIn('11:30', response.get_json()['error'])
        self.clock.now.return_value = datetime(2026, 9, 16, 15, 30, tzinfo=timezone.utc)
        self.assertEqual(self.punch(**AT_STORE).status_code, 200)

    def test_only_within_150_metres_of_the_store(self):
        far = self.punch(lat=43.6632, lng=-79.3832, accuracy=10)  # about 1.1 km north
        self.assertEqual(self.refused(far), 'punch_too_far')
        self.assertIn('1.1 公里', far.get_json()['error'])
        self.assertEqual(self.refused(self.punch(lat=43.6546, lng=-79.3832, accuracy=10)), 'punch_too_far')  # ~156 m
        self.assertEqual(self.punch(lat=43.65446, lng=-79.3832, accuracy=10).status_code, 200)  # ~140 m

    def test_rough_or_missing_location_cannot_punch(self):
        self.assertEqual(self.refused(self.punch(lat=43.6532, lng=-79.3832, accuracy=400)), 'punch_location_rough')
        self.assertEqual(self.refused(self.punch()), 'punch_needs_location')
        self.assertEqual(self.punch(**AT_STORE).status_code, 200)
        # A retry of a saved punch answers with it, wherever the phone is now.
        self.assertEqual(self.punch().status_code, 200)
        self.assertEqual(self.punch(lat=0, lng=0, accuracy=5).status_code, 200)

    def test_location_must_be_whole_and_real(self):
        for location in ({'lat': 43.6532}, {'lat': 43.6532, 'lng': -79.3832}, {**AT_STORE, 'lat': '43.6532'},
                         {**AT_STORE, 'lat': 91}, {**AT_STORE, 'lng': -181}, {**AT_STORE, 'accuracy': -1},
                         {**AT_STORE, 'accuracy': True}, {**AT_STORE, 'lat': float('nan')}, {**AT_STORE, 'heading': 0}):
            self.assertEqual(self.punch(**location).status_code, 400, location)

    def test_store_with_no_spot_takes_no_punches(self):
        with closing(self.connect()) as con:
            con.execute("DELETE FROM app_settings WHERE key='punch_places'")
            con.commit()
        self.assertFalse(self.client.get(URL, headers=self.headers()).get_json()['punch']['place_set'])
        self.assertEqual(self.refused(self.punch(**AT_STORE)), 'punch_place_unset')

    def test_coordinates_are_not_kept_only_the_distance(self):
        self.assertEqual(self.punch(**AT_STORE).status_code, 200)
        with closing(self.connect()) as con:
            columns = {row[1] for row in con.execute('PRAGMA table_info(schedule_attendance)')}
            line = con.execute("SELECT summary, detail FROM activity_log WHERE endpoint='schedule.schedule_attendance_today'").fetchone()
        self.assertFalse(columns & {'lat', 'lng', 'accuracy'})
        self.assertEqual(line['detail'], '上班 In · 离店 14 m from the store')
        self.assertNotIn('43.65', line['summary'] + line['detail'])


class PunchPlaceTests(IsolatedApiCase):
    PLACES = '/api/schedule/punch-places'

    def test_admin_saves_and_clears_a_store_spot(self):
        for role in ('staff', 'manager'):
            self.assertEqual(self.client.get(self.PLACES, headers=self.headers(role)).status_code, 403)
            self.assertEqual(self.client.put(f'{self.PLACES}/DT', json={'lat': 43.65, 'lng': -79.38},
                                             headers=self.headers(role)).status_code, 403)
        admin = self.headers('admin')
        view = self.client.get(self.PLACES, headers=admin).get_json()
        self.assertEqual(view['radius_m'], 150)
        saved = {store['code']: store['saved'] for store in view['stores']}
        self.assertIsNone(saved['DT'])
        self.assertIsNone(saved['MK'])
        for body in ({}, {'lat': 43.65}, {'lat': 'x', 'lng': -79.38}, {'lat': 95, 'lng': -79.38},
                     {'lat': 43.65, 'lng': -79.38, 'name': 'DT'}):
            self.assertEqual(self.client.put(f'{self.PLACES}/DT', json=body, headers=admin).status_code, 400, body)
        self.assertEqual(self.client.put(f'{self.PLACES}/XX', json={'lat': 43.65, 'lng': -79.38}, headers=admin).status_code, 404)
        saved = self.client.put(f'{self.PLACES}/DT', json={'lat': 43.65320012, 'lng': -79.3832}, headers=admin).get_json()
        dt = next(store for store in saved['stores'] if store['code'] == 'DT')
        self.assertEqual((dt['saved']['lat'], dt['saved']['lng']), (43.6532, -79.3832))
        cleared = self.client.delete(f'{self.PLACES}/DT', headers=admin).get_json()
        self.assertIsNone(next(store for store in cleared['stores'] if store['code'] == 'DT')['saved'])
        with closing(self.connect()) as con:
            details = [row[0] for row in con.execute(
                "SELECT detail FROM activity_log WHERE endpoint='schedule.punch_place_set' ORDER BY id")]
        self.assertEqual(details, ['43.6532, -79.3832', '清除 Cleared'])
