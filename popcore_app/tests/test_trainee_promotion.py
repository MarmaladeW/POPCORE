from contextlib import closing
from unittest.mock import patch

from support import IsolatedApiCase
from blueprints import users


class TraineePromotionTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(users.bp)
        response = self.client.post('/api/schedule/trainees', headers=self.headers('manager'), json={'name': 'Jessi'})
        self.trainee_id = response.get_json()['id']
        with closing(self.connect()) as con:
            con.execute('''INSERT INTO shifts (employee_id, store_id, date, start_time, end_time, assigned_by)
                VALUES (?, ?, '2026-09-16', '12:00', '20:00', 'fixture')''', (self.trainee_id, self.store_id))
            con.commit()

    def test_promotion_keeps_employee_id_and_wage_hours(self):
        class Response:
            ok = True
            def json(self):
                return {'user_id': 'auth0|jessi'}

        with patch.object(users, 'AUTH0_MGMT_CLIENT_ID', 'test'), patch.object(users, 'AUTH0_MGMT_CLIENT_SECRET', 'test'), \
                patch.object(users, '_mgmt_post', return_value=Response()), patch.object(users, '_get_role_map', return_value={'staff': 'staff-role'}):
            result = self.client.post('/api/users', headers=self.headers('admin'), json={
                'username': 'jessi', 'password': 'password123', 'role': 'staff', 'trainee_id': self.trainee_id,
            })

        self.assertEqual(result.status_code, 201, result.get_json())
        with closing(self.connect()) as con:
            employees = con.execute('SELECT id, auth0_id, name, is_trainee FROM employees').fetchall()
            self.assertEqual(len(employees), 1)
            self.assertEqual(tuple(employees[0]), (self.trainee_id, 'auth0|jessi', 'Jessi', 0))
        report = self.client.get('/api/schedule/reports/monthly?year=2026&month=9&store_code=ALL', headers=self.headers('manager'))
        self.assertEqual(report.get_json()['employees'][0]['total_hours'], 8.0)
        self.assertEqual(report.get_json()['employees'][0]['id'], self.trainee_id)
        signed_in = self.client.get('/api/schedule/me', headers=self.headers('staff:jessi'))
        self.assertEqual(signed_in.get_json()['id'], self.trainee_id)

    def test_invalid_trainee_does_not_create_provider_account(self):
        class Response:
            ok = True
            def json(self):
                return {'user_id': 'auth0|jessi'}

        with patch.object(users, 'AUTH0_MGMT_CLIENT_ID', 'test'), patch.object(users, 'AUTH0_MGMT_CLIENT_SECRET', 'test'), \
                patch.object(users, '_mgmt_post', return_value=Response()) as provider:
            result = self.client.post('/api/users', headers=self.headers('admin'), json={
                'username': 'jessi', 'password': 'password123', 'role': 'staff', 'trainee_id': 99999,
            })
        self.assertEqual(result.status_code, 404)
        provider.assert_not_called()

    def test_manager_cannot_promote_trainee(self):
        result = self.client.post('/api/users', headers=self.headers('manager'), json={
            'username': 'jessi', 'password': 'password123', 'role': 'staff', 'trainee_id': self.trainee_id,
        })
        self.assertEqual(result.status_code, 403)

    def test_provider_failure_leaves_trainee_and_hours_unchanged(self):
        class Response:
            ok = False
            status_code = 503
            def json(self):
                return {'message': 'Provider unavailable'}

        with patch.object(users, 'AUTH0_MGMT_CLIENT_ID', 'test'), patch.object(users, 'AUTH0_MGMT_CLIENT_SECRET', 'test'), \
                patch.object(users, '_mgmt_post', return_value=Response()):
            result = self.client.post('/api/users', headers=self.headers('admin'), json={
                'username': 'jessi', 'password': 'password123', 'role': 'staff', 'trainee_id': self.trainee_id,
            })
        self.assertEqual(result.status_code, 502)
        with closing(self.connect()) as con:
            employee = con.execute('SELECT auth0_id, is_trainee FROM employees WHERE id = ?', (self.trainee_id,)).fetchone()
            self.assertTrue(employee['auth0_id'].startswith('trainee|'))
            self.assertEqual(employee['is_trainee'], 1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM shifts WHERE employee_id = ?', (self.trainee_id,)).fetchone()[0], 1)
