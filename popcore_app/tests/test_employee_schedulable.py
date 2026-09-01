import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

os.environ.setdefault('AUTH0_DOMAIN', 'test.invalid')

import auth  # noqa: E402
import db  # noqa: E402
from blueprints import schedule, users  # noqa: E402


MANAGER_PAYLOAD = {
    'sub': 'auth0|manager',
    auth.ROLE_CLAIM: 'manager',
}
AUTH_HEADERS = {'Authorization': 'Bearer test-token'}


class EmployeeSchedulableApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = str(Path(self.tempdir.name) / 'test.db')
        self._create_schema()

        app = Flask(__name__)
        app.config.update(TESTING=True)
        app.register_blueprint(users.bp)
        app.register_blueprint(schedule.bp)
        app.teardown_appcontext(db.close_db)
        self.client = app.test_client()
        self.auth_patch = patch.object(auth, '_decode_token', return_value=MANAGER_PAYLOAD)
        self.auth_patch.start()

    def tearDown(self):
        self.auth_patch.stop()
        db.DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def _connect(self):
        connection = sqlite3.connect(db.DB_PATH)
        connection.row_factory = sqlite3.Row
        return connection

    def _create_schema(self):
        connection = self._connect()
        connection.executescript(
            '''
            CREATE TABLE employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                auth0_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                color TEXT NOT NULL DEFAULT '#6366f1',
                is_trainee INTEGER NOT NULL DEFAULT 0,
                is_schedulable INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE employee_stores (
                employee_id INTEGER NOT NULL,
                store_id INTEGER NOT NULL,
                PRIMARY KEY (employee_id, store_id)
            );
            CREATE TABLE shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                assigned_by TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                store_id INTEGER,
                position TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(employee_id, date)
            );
            INSERT INTO stores (id, code, name) VALUES (1, 'DT', 'Downtown Toronto');
            INSERT INTO employees
                (id, auth0_id, name, email, is_active, color, is_schedulable)
            VALUES
                (1, 'auth0|enabled', 'Enabled', 'enabled@example.com', 1, '#3D74C4', 1),
                (2, 'auth0|disabled', 'Disabled', 'disabled@example.com', 1, '#2E7FA3', 0),
                (3, 'auth0|inactive', 'Inactive', 'inactive@example.com', 0, '#2C8A86', 1);
            INSERT INTO employee_stores (employee_id, store_id) VALUES (1, 1), (2, 1);
            INSERT INTO shifts
                (id, employee_id, date, start_time, end_time, assigned_by, store_id)
            VALUES
                (10, 2, '2026-09-01', '12:00', '22:00', 'auth0|manager', 1);
            '''
        )
        connection.commit()
        connection.close()

    def test_employee_store_listing_includes_schedulable(self):
        response = self.client.get('/api/employees/stores', headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200)
        employees = {row['employee_id']: row for row in response.get_json()}
        self.assertIn('is_schedulable', employees[1])
        self.assertIn('is_schedulable', employees[2])
        self.assertEqual(employees[1]['is_schedulable'], 1)
        self.assertEqual(employees[2]['is_schedulable'], 0)
        self.assertNotIn(3, employees)

    def test_schedule_employee_listing_includes_schedulable(self):
        response = self.client.get('/api/schedule/employees', headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200)
        employees = {row['id']: row for row in response.get_json()}
        self.assertEqual(employees[1]['is_schedulable'], 1)
        self.assertEqual(employees[2]['is_schedulable'], 0)
        self.assertNotIn(3, employees)

    def test_patch_schedulable_accepts_boolean_and_zero_or_one(self):
        for value, expected in ((False, 0), (True, 1), (0, 0), (1, 1)):
            with self.subTest(value=value):
                response = self.client.patch(
                    '/api/employees/1/schedulable',
                    headers=AUTH_HEADERS,
                    json={'is_schedulable': value},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()['is_schedulable'], expected)

    def test_patch_schedulable_rejects_invalid_values(self):
        for value in ('1', 2, None, [], {}):
            with self.subTest(value=value):
                response = self.client.patch(
                    '/api/employees/1/schedulable',
                    headers=AUTH_HEADERS,
                    json={'is_schedulable': value},
                )
                self.assertEqual(response.status_code, 400)

    def test_patch_schedulable_rejects_non_object_json_bodies(self):
        for value in ('invalid', 1, True, [1]):
            with self.subTest(value=value):
                response = self.client.patch(
                    '/api/employees/1/schedulable',
                    headers=AUTH_HEADERS,
                    json=value,
                )
                self.assertEqual(response.status_code, 400)

    def test_patch_schedulable_rejects_missing_or_inactive_employee(self):
        for employee_id in (3, 999):
            with self.subTest(employee_id=employee_id):
                response = self.client.patch(
                    f'/api/employees/{employee_id}/schedulable',
                    headers=AUTH_HEADERS,
                    json={'is_schedulable': True},
                )
                self.assertEqual(response.status_code, 404)

    def test_create_shift_rejects_disabled_employee(self):
        response = self.client.post(
            '/api/schedule/shifts',
            headers=AUTH_HEADERS,
            json={
                'employee_id': 2,
                'date': '2026-09-02',
                'start_time': '12:00',
                'end_time': '22:00',
                'store_code': 'DT',
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()['error'],
            'Employee is disabled for shift assignment',
        )

    def test_create_shift_accepts_enabled_employee(self):
        response = self.client.post(
            '/api/schedule/shifts',
            headers=AUTH_HEADERS,
            json={
                'employee_id': 1,
                'date': '2026-09-02',
                'start_time': '12:00',
                'end_time': '22:00',
                'store_code': 'DT',
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()['employee_id'], 1)

    def test_existing_disabled_shift_can_still_be_updated_and_deleted(self):
        update = self.client.patch(
            '/api/schedule/shifts/10',
            headers=AUTH_HEADERS,
            json={'start_time': '13:00', 'notes': 'Updated'},
        )
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.get_json()['start_time'], '13:00')

        delete = self.client.delete('/api/schedule/shifts/10', headers=AUTH_HEADERS)
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(delete.get_json(), {'ok': True})


class EmployeeSchedulableMigrationTests(unittest.TestCase):
    def test_migration_adds_default_and_is_idempotent(self):
        connection = sqlite3.connect(':memory:')
        connection.row_factory = sqlite3.Row
        cursor = connection.cursor()
        cursor.executescript(
            '''
            CREATE TABLE employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                auth0_id TEXT NOT NULL
            );
            CREATE TABLE _migrations (name TEXT PRIMARY KEY);
            INSERT INTO employees (auth0_id) VALUES ('auth0|existing');
            '''
        )

        migration = getattr(db, '_migration_add_is_schedulable_to_employees', None)
        self.assertIsNotNone(migration, 'schedulable migration must be registered')
        migration(connection, cursor)
        migration(connection, cursor)

        columns = {
            row['name']: row for row in connection.execute('PRAGMA table_info(employees)')
        }
        employee = connection.execute(
            'SELECT is_schedulable FROM employees WHERE id = 1'
        ).fetchone()
        migration_count = connection.execute(
            "SELECT COUNT(*) AS count FROM _migrations "
            "WHERE name = 'add_is_schedulable_to_employees'"
        ).fetchone()['count']

        self.assertIn('is_schedulable', columns)
        self.assertEqual(columns['is_schedulable']['notnull'], 1)
        self.assertEqual(employee['is_schedulable'], 1)
        self.assertEqual(migration_count, 1)
        connection.close()


if __name__ == '__main__':
    unittest.main()
