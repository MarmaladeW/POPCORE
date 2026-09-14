import datetime as dt
from contextlib import closing

from support import IsolatedApiCase, db


class SchedulePeriodTests(IsolatedApiCase):
    def test_managers_and_admins_assign_without_submission(self):
        for role in ('manager', 'admin'):
            employee = self.client.get('/api/schedule/me', headers=self.headers('staff:' + role)).json['id']
            shift = {'employee_id': employee, 'store_code': 'DT', 'date': '2026-09-14',
                     'start_time': '12:00', 'end_time': '22:00', 'require_availability': True}
            self.assertEqual(self.client.post('/api/schedule/shifts', json=shift, headers=self.headers('staff')).status_code, 403)
            result = self.client.post('/api/schedule/shifts', json=shift, headers=self.headers(role))
            self.assertEqual(result.status_code, 201, result.json)
            self.assertEqual(self.client.post('/api/schedule/shifts', json=shift, headers=self.headers(role)).status_code, 409)

    def test_old_cycle_hours_are_preserved_for_resubmission(self):
        employee = self.client.get('/api/schedule/me', headers=self.headers()).json['id']
        with closing(self.connect()) as con:
            con.execute('''INSERT INTO availability(employee_id,store_id,date,start_time,end_time,notes)
                VALUES (?,?,'2026-09-21','12:00','17:00','Keep these hours')''', (employee, self.store_id))
            con.execute('''INSERT INTO availability_submissions(employee_id,store_id,period_start,version,submitted_at)
                VALUES (?,?,'2026-09-21',1,'2026-09-10T12:00:00Z')''', (employee, self.store_id))
            con.commit()
        result = self.get()
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json['submitted_at'])
        self.assertEqual(result.json['days'][0]['notes'], 'Keep these hours')
        self.assertIsNone(result.json['days'][0]['submitted_at'])
        self.assertEqual(self.put(self.payload(period_start='2026-09-21')).status_code, 400)

    def payload(self, **changes):
        data = {'period_start': '2026-09-14', 'store_code': 'DT', 'version': 0,
                'days': [{'date': str(dt.date(2026, 9, 14) + dt.timedelta(days=i)),
                          'status': 'available' if i < 5 else 'unavailable',
                          'start_time': '09:00' if i < 5 else '',
                          'end_time': '18:00' if i < 5 else '', 'notes': ''}
                         for i in range(14)]}
        data.update(changes)
        return data

    def put(self, data=None, role='staff'):
        return self.client.put('/api/schedule/availability/period', json=data or self.payload(), headers=self.headers(role))

    def get(self, role='staff', store='DT'):
        return self.client.get('/api/schedule/availability/period', query_string={'period_start': '2026-09-14', 'store_code': store}, headers=self.headers(role))

    def test_submission_resubmission_scope_and_metadata(self):
        self.assertEqual(self.get().status_code, 200)
        self.assertEqual(self.get().json['version'], 0)
        result = self.put()
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json['version'], 1)
        self.assertEqual(len(result.json['days']), 14)
        self.assertEqual(result.json['period_end'], '2026-09-27')
        self.assertTrue(result.json['submitted_at'])
        self.assertEqual(self.get('staff:other').json['days'], [])
        self.assertEqual(self.client.get('/api/schedule/availability?store_code=DT', headers=self.headers()).status_code, 403)
        rows = self.client.get('/api/schedule/availability?store_code=DT', headers=self.headers('manager')).json
        self.assertEqual(rows[0]['submission_version'], 1)
        self.assertTrue(rows[0]['submitted_at'])
        self.assertEqual(self.put().status_code, 409)
        ids = [r['id'] for r in result.json['days']]
        self.assertEqual([r['id'] for r in self.put(self.payload(version=1)).json['days']], ids)
        self.assertEqual(self.put(self.payload(employee_id=999), 'staff:other').status_code, 400)

    def test_invalid_submissions_are_atomic(self):
        variants = [self.payload(days=self.payload()['days'][:-1]), self.payload(period_start='2026-09-22'),
                    self.payload(store_code='ALL'), self.payload(store_code='unknown'), self.payload(version=True)]
        for field, value in [('date', '2026-02-30'), ('date', '20260921'), ('status', 'maybe'),
                             ('start_time', '9:00'), ('end_time', '24:00'), ('end_time', '08:00'), ('notes', [])]:
            data = self.payload()
            data['days'][0][field] = value
            variants.append(data)
        data = self.payload(); data['days'][1] = data['days'][0].copy(); variants.append(data)
        for data in variants:
            with self.subTest(data=data):
                self.assertEqual(self.put(data).status_code, 400)
        self.assertEqual(self.snapshot(['availability'])['availability'], [])

    def test_store_separation_and_legacy_invalidation(self):
        self.assertEqual(self.put().status_code, 200)
        with closing(self.connect()) as con:
            con.execute("INSERT INTO stores(code,name) VALUES ('OTHER','Other')"); con.commit()
        self.assertEqual(self.put(self.payload(store_code='OTHER')).status_code, 200)
        day = self.payload()['days'][0] | {'store_code': 'DT'}
        result = self.client.post('/api/schedule/availability', json=day, headers=self.headers())
        self.assertEqual(result.status_code, 201)
        self.assertEqual(self.get().json['version'], 2)
        self.assertIsNone(self.get().json['submitted_at'])
        self.assertEqual(self.get(store='OTHER').json['version'], 1)
        self.assertEqual(self.put(self.payload(version=1)).status_code, 409)
        self.assertEqual(self.put(self.payload(version=2)).status_code, 200)
        row = self.get().json['days'][0]
        self.assertEqual(self.client.delete(f"/api/schedule/availability/{row['id']}", headers=self.headers('staff:other')).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/schedule/availability/{row['id']}", headers=self.headers()).status_code, 200)
        self.assertEqual(self.get().json['version'], 4)
        self.assertIsNone(self.get().json['submitted_at'])

    def test_shift_assignment_guards_preserve_shifts(self):
        employee = self.client.get('/api/schedule/me', headers=self.headers()).json['id']
        shift = {'employee_id': employee, 'store_code': 'DT', 'date': '2026-09-14', 'start_time': '10:00', 'end_time': '17:00', 'require_availability': True}
        def create(data):
            return self.client.post('/api/schedule/shifts', json=data, headers=self.headers('manager'))
        self.assertEqual(self.put().status_code, 200)
        self.assertEqual(create(shift | {'start_time': '08:00'}).status_code, 409)
        self.assertEqual(create(shift | {'date': '2026-09-19'}).status_code, 409)
        result = create(shift)
        self.assertEqual(result.status_code, 201, result.json)
        before = self.snapshot(['shifts'])
        self.assertEqual(create(shift).status_code, 409)
        with closing(self.connect()) as con:
            con.execute("INSERT INTO stores(code,name) VALUES ('OTHER','Other')"); con.commit()
        self.assertEqual(create(shift | {'store_code': 'OTHER', 'require_availability': False}).status_code, 409)
        data = self.payload(version=1); data['days'][0].update(status='unavailable', start_time='', end_time='')
        self.assertEqual(self.put(data).status_code, 200)
        self.assertEqual(self.snapshot(['shifts']), before)
        url = f"/api/schedule/shifts/{result.json['id']}"
        self.assertEqual(self.client.patch(url, json={'end_time': '08:00'}, headers=self.headers('manager')).status_code, 400)
        self.assertEqual(self.client.patch(url, json={'notes': 'x', 'require_availability': True}, headers=self.headers('manager')).status_code, 409)
        self.assertEqual(self.client.patch(url, json={'notes': 'manual'}, headers=self.headers('manager')).status_code, 200)
        for change in [{'date': '2026-02-30'}, {'start_time': '9:00'}, {'end_time': '09:00'}]:
            self.assertEqual(create(shift | change).status_code, 400)
        trainee = self.client.post('/api/schedule/trainees', json={'name': 'Trainee'}, headers=self.headers('manager')).json
        self.assertEqual(create(shift | {'employee_id': trainee['id'], 'require_availability': False}).status_code, 201)

    def test_migration_preserves_legacy_ids_and_is_repeatable(self):
        employee = self.client.get('/api/schedule/me', headers=self.headers()).json['id']
        with closing(self.connect()) as con:
            con.execute('DROP TABLE availability')
            con.execute('''CREATE TABLE availability(id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL REFERENCES employees(id), date TEXT NOT NULL,
                start_time TEXT NOT NULL, end_time TEXT NOT NULL, notes TEXT DEFAULT '',
                created_at TEXT, updated_at TEXT, store_id INTEGER NOT NULL REFERENCES stores(id),
                UNIQUE(employee_id,date))''')
            con.execute("INSERT INTO availability VALUES (42,?,'2026-09-14','09:00','17:00','old','created','updated',?)", (employee,self.store_id))
            con.execute("UPDATE sqlite_sequence SET seq=99 WHERE name='availability'")
            con.execute("DELETE FROM _migrations WHERE name='availability_period_submissions'")
            con.commit()
        db.migrate_db(); db.migrate_db()
        result = self.get()
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json['submitted_at'])
        row = result.json['days'][0]
        self.assertEqual((row['id'],row['notes'],row['status'],row['created_at']), (42,'old','available','created'))
        submitted = self.put().json['days']
        self.assertEqual(submitted[0]['id'], 42)
        self.assertGreater(submitted[1]['id'], 99)

    def test_malformed_write_payloads_return_validation_errors(self):
        employee = self.client.get('/api/schedule/me', headers=self.headers()).json['id']
        shift = {'employee_id': employee, 'store_code': 'DT', 'date': '2026-09-14', 'start_time': '10:00', 'end_time': '17:00'}
        result = self.client.post('/api/schedule/shifts', json=shift, headers=self.headers('manager'))
        self.assertEqual(result.status_code, 201)
        for method, path, role in [('put', '/api/schedule/availability/period', 'staff'),
                                   ('post', '/api/schedule/availability', 'staff'),
                                   ('post', '/api/schedule/shifts', 'manager'),
                                   ('patch', f"/api/schedule/shifts/{result.json['id']}", 'manager')]:
            with self.subTest(path=path):
                self.assertEqual(getattr(self.client, method)(path, json=['bad'], headers=self.headers(role)).status_code, 400)
        self.assertEqual(self.client.post('/api/schedule/shifts', json=shift | {'notes': []}, headers=self.headers('manager')).status_code, 400)
        self.assertEqual(self.client.patch(f"/api/schedule/shifts/{result.json['id']}", json={'notes': []}, headers=self.headers('manager')).status_code, 400)

    def test_period_requires_authenticated_active_identity(self):
        self.assertEqual(self.client.put('/api/schedule/availability/period', json=self.payload()).status_code, 401)
        with closing(self.connect()) as con:
            employee = self.client.get('/api/schedule/me', headers=self.headers()).json['id']
            con.execute('UPDATE employees SET is_active=0 WHERE id=?', (employee,)); con.commit()
        self.assertEqual(self.put().status_code, 403)
        from unittest.mock import patch
        import auth
        with patch.object(auth, '_decode_token', return_value={auth.ROLE_CLAIM: 'staff'}):
            self.assertEqual(self.put().status_code, 401)

    def test_fortnight_notes_and_checklist_have_separate_keys(self):
        employee = self.client.get('/api/schedule/me', headers=self.headers()).json['id']
        for resource, fields in [('notes', {'content': 'Two weeks'}), ('checklist', {'employee_id': employee, 'considered': True})]:
            path = f'/api/schedule/{resource}'
            result = self.client.put(path, json=fields | {'period_key': 'fortnight:2026-09-14'}, headers=self.headers('manager'))
            self.assertEqual(result.status_code, 200, result.json)
            result = self.client.get(path, query_string={'key': 'fortnight:2026-09-14'}, headers=self.headers('manager'))
            self.assertEqual(result.status_code, 200)
            weekly = self.client.get(path, query_string={'key': 'week:2026-09-14'}, headers=self.headers('manager')).json
            self.assertEqual(weekly['content'] if resource == 'notes' else weekly, '' if resource == 'notes' else [])

    def test_availability_lists_keep_rows_and_submission_in_one_snapshot(self):
        from unittest.mock import patch
        from blueprints import schedule

        read_rows = schedule._availability_rows
        for path, role in [('/api/schedule/availability/me', 'staff'),
                           ('/api/schedule/availability', 'manager')]:
            with self.subTest(path=path):
                version = self.get().json['version']
                baseline = self.put(self.payload(version=version)).json
                changed = self.payload(version=baseline['version'])
                changed['days'][0].update(status='unavailable', start_time='', end_time='')
                interleaved = False

                def submit_between_rows_and_metadata(con, rows):
                    nonlocal interleaved
                    if not interleaved:
                        interleaved = True
                        # A separate request/connection commits after the list fetched its days.
                        with self.app.app_context():
                            submitted = self.put(changed)
                            self.assertEqual(submitted.status_code, 200, submitted.json)
                    return read_rows(con, rows)

                with patch.object(schedule, '_availability_rows', side_effect=submit_between_rows_and_metadata):
                    response = self.client.get(path, query_string={'store_code': 'DT'}, headers=self.headers(role))
                self.assertEqual(response.status_code, 200)
                row = next(day for day in response.json if day['date'] == '2026-09-14')
                self.assertEqual(row['status'], 'available')
                self.assertEqual(row['submission_version'], baseline['version'])
                self.assertEqual(row['submitted_at'], baseline['submitted_at'])
                current = self.get().json
                self.assertEqual(current['days'][0]['status'], 'unavailable')
                self.assertEqual(current['version'], baseline['version'] + 1)
