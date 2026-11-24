"""Ranks (owner, 2026-11-19): 店长, 副店长, 资深员工, 正式员工, 培训员工. A rank sets the Auth0 role (店长 and 副店长
manager, the others staff), 查看者 Viewer and 管理员 Admin carry none, 培训员工 is 培训中 and 正式员工 and above are 转正,
and every list of people shows 店长 first. Shift positions: End is Back, and Markham has Cashier and Sales."""
import json
import sqlite3
from contextlib import closing
from unittest.mock import Mock, patch

from support import IsolatedApiCase, db
from blueprints import schedule, settings, users
import checkout_access
import ranks

ROLE_IDS = {'admin': 'rol_admin', 'manager': 'rol_manager', 'staff': 'rol_staff', 'viewer': 'rol_viewer'}


class StaffRankTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        for blueprint in (users.bp, schedule.bp, settings.bp):
            if blueprint.name not in self.app.blueprints:
                self.app.register_blueprint(blueprint)
        self.roles = {}  # auth0 id -> its Auth0 role ids, as the fake management API keeps them
        with closing(self.connect()) as con:
            for sub, name, rank in (('auth0|yc', 'YC', 'trainee'), ('auth0|celia', 'Celia', 'regular'),
                                    ('auth0|zimo', 'Zimo', 'senior'), ('auth0|kitty', 'Kitty', 'assistant_manager'),
                                    ('auth0|xiaona', 'Xiao Na', 'store_manager'), ('auth0|amy', 'Amy', None)):
                con.execute('INSERT INTO employees(auth0_id, name, rank) VALUES (?, ?, ?)', (sub, name, rank))
            con.commit()

    def mgmt_get(self, path, **kwargs):
        sub = path.split('/')[1].replace('%7C', '|')
        return Mock(ok=True, json=lambda: [{'id': role} for role in self.roles.get(sub, [])])

    def mgmt_post(self, path, json=None, **kwargs):
        sub = path.split('/')[1].replace('%7C', '|')
        self.roles.setdefault(sub, [])
        self.roles[sub] += [role for role in json['roles'] if role not in self.roles[sub]]
        return Mock(ok=True)

    def mgmt_delete(self, path, json=None, **kwargs):
        sub = path.split('/')[1].replace('%7C', '|')
        self.roles[sub] = [role for role in self.roles.get(sub, []) if role not in json['roles']]
        return Mock(ok=True)

    def save(self, sub, body):
        with patch.object(users, '_get_role_map', return_value=ROLE_IDS), \
                patch.object(users, '_mgmt_get', side_effect=self.mgmt_get), \
                patch.object(users, '_mgmt_post', side_effect=self.mgmt_post), \
                patch.object(users, '_mgmt_delete', side_effect=self.mgmt_delete):
            return self.client.patch(f'/api/users/{sub}', headers=self.headers('admin'), json=body)

    def employee(self, sub):
        with closing(self.connect()) as con:
            row = con.execute('SELECT rank, is_training, regular_since FROM employees WHERE auth0_id=?', (sub,)).fetchone()
            return dict(row) if row else None

    def test_a_rank_sets_the_role_and_the_status(self):
        today = checkout_access.business_date()
        self.assertEqual(self.save('auth0|yc', {'rank': 'assistant_manager'}).status_code, 200)
        self.assertEqual(self.roles['auth0|yc'], ['rol_manager'])
        self.assertEqual(self.employee('auth0|yc'), {'rank': 'assistant_manager', 'is_training': 0, 'regular_since': today})
        self.save('auth0|yc', {'rank': 'senior'})
        self.assertEqual(self.roles['auth0|yc'], ['rol_staff'])
        self.assertEqual(self.employee('auth0|yc')['regular_since'], today)  # kept from when it first came
        self.save('auth0|yc', {'rank': 'trainee'})
        self.assertEqual(self.employee('auth0|yc'), {'rank': 'trainee', 'is_training': 1, 'regular_since': None})
        # A viewer (the store computer) or an admin has no rank, and is never a 培训员工.
        self.save('auth0|yc', {'role': 'viewer'})
        self.assertEqual((self.roles['auth0|yc'], self.employee('auth0|yc')['rank']), (['rol_viewer'], None))
        self.assertEqual(self.save('auth0|yc', {'rank': None, 'role': 'staff'}).status_code, 400)
        self.assertEqual(self.save('auth0|yc', {'rank': 'boss'}).status_code, 400)
        # Someone who never signed in gets their row now, rank included.
        self.save('auth0|new', {'rank': 'regular'})
        self.assertEqual(self.employee('auth0|new')['rank'], 'regular')
        # Only an admin sets a rank.
        response = self.client.patch('/api/users/auth0|celia', headers=self.headers('manager'), json={'rank': 'store_manager'})
        self.assertEqual(response.status_code, 403)

    def test_the_store_computer_viewer_is_never_on_the_schedule(self):
        # Owner, 2026-11-24: the viewer is the store computer's account, "no need for shift".
        def schedulable(sub):
            with closing(self.connect()) as con:
                return con.execute('SELECT is_schedulable FROM employees WHERE auth0_id=?', (sub,)).fetchone()[0]
        self.roles['auth0|celia'] = ['rol_staff']
        self.assertEqual(self.save('auth0|celia', {'role': 'viewer'}).status_code, 200)
        self.assertEqual(schedulable('auth0|celia'), 0)
        # Picking a staff position again puts them back on the schedule; a staff change never does.
        self.save('auth0|celia', {'rank': 'regular'})
        self.assertEqual(schedulable('auth0|celia'), 1)
        with closing(self.connect()) as con:
            con.execute("UPDATE employees SET is_schedulable=0 WHERE auth0_id='auth0|zimo'")
            con.commit()
        self.roles['auth0|zimo'] = ['rol_staff']
        self.save('auth0|zimo', {'rank': 'regular'})
        self.assertEqual(schedulable('auth0|zimo'), 0)
        # A new viewer account starts off the schedule.
        with patch.object(users, 'AUTH0_MGMT_CLIENT_ID', 'id'), patch.object(users, 'AUTH0_MGMT_CLIENT_SECRET', 'secret'), \
                patch.object(users, '_get_role_map', return_value=ROLE_IDS), \
                patch.object(users, '_mgmt_post', side_effect=lambda path, json=None, **kw:
                             Mock(ok=True, json=lambda: {'user_id': 'auth0|dtpc'}) if path == 'users' else self.mgmt_post(path, json)):
            created = self.client.post('/api/users', headers=self.headers('admin'),
                                       json={'username': 'dtpc', 'password': 'counter-pass-1', 'role': 'viewer'})
        self.assertEqual(created.status_code, 201)
        self.assertEqual((self.roles['auth0|dtpc'], schedulable('auth0|dtpc')), (['rol_viewer'], 0))
        # One made before this change comes off the schedule when Users is next read; an account with no role stays.
        with closing(self.connect()) as con:
            con.execute("UPDATE employees SET is_schedulable=1 WHERE auth0_id IN ('auth0|amy', 'auth0|yc')")
            con.commit()
        auth0_users = [{'user_id': sub, 'username': sub.split('|')[1]} for sub in ('auth0|amy', 'auth0|yc', 'auth0|kitty')]
        members = {'rol_admin': [], 'rol_manager': ['auth0|kitty'], 'rol_staff': [], 'rol_viewer': ['auth0|amy']}
        with patch.object(users, 'AUTH0_MGMT_CLIENT_ID', 'id'), patch.object(users, 'AUTH0_MGMT_CLIENT_SECRET', 'secret'), \
                patch.object(users, '_mgmt_get', return_value=Mock(ok=True, json=lambda: auth0_users)), \
                patch.object(users, '_get_role_map', return_value=ROLE_IDS), \
                patch.object(users, '_fetch_role_member_ids', side_effect=lambda role: members[role]):
            listed = self.client.get('/api/users', headers=self.headers('admin')).get_json()
        self.assertEqual({row['username']: row['role'] for row in listed}, {'amy': 'viewer', 'yc': 'viewer', 'kitty': 'manager'})
        self.assertEqual((schedulable('auth0|amy'), schedulable('auth0|yc'), schedulable('auth0|kitty')), (0, 1, 1))

    def test_the_status_keeps_the_rank_in_step(self):
        with closing(self.connect()) as con:
            ids = {row[0]: row[1] for row in con.execute('SELECT auth0_id, id FROM employees')}
        regular = {'status': 'regular', 'regular_since': '2026-11-01'}
        self.client.patch(f"/api/employees/{ids['auth0|yc']}/status", headers=self.headers('manager'), json=regular)
        self.assertEqual(self.employee('auth0|yc')['rank'], 'regular')
        self.client.patch(f"/api/employees/{ids['auth0|zimo']}/status", headers=self.headers('manager'), json=regular)
        self.assertEqual(self.employee('auth0|zimo')['rank'], 'senior')  # a higher rank stays
        self.client.patch(f"/api/employees/{ids['auth0|celia']}/status", headers=self.headers('manager'),
                          json={'status': 'training'})
        self.assertEqual(self.employee('auth0|celia')['rank'], 'trainee')

    def test_people_are_listed_store_manager_first(self):
        expected = ['Xiao Na', 'Kitty', 'Zimo', 'Celia', 'YC', 'Amy']
        employees = self.client.get('/api/schedule/employees', headers=self.headers('manager')).get_json()
        self.assertEqual([row['name'] for row in employees if row['name'] in expected], expected)
        # Shifts carry the person's rank, so the schedule marks them 长 副 资 正 (owner, 2026-11-22).
        with closing(self.connect()) as con:
            for name in ('Zimo', 'Xiao Na'):
                con.execute("""INSERT INTO shifts(employee_id, date, start_time, end_time, assigned_by, store_id)
                               SELECT id, '2026-11-23', '12:00', '22:00', 'test', ? FROM employees WHERE name=?""",
                            (self.store_id, name))
            con.commit()
        shifts = self.client.get('/api/schedule/shifts?store_code=DT&start=2026-11-23&end=2026-11-23',
                                 headers=self.headers('manager')).get_json()
        self.assertEqual([(row['employee_name'], row['rank']) for row in shifts],
                         [('Xiao Na', 'store_manager'), ('Zimo', 'senior')])
        stores = self.client.get('/api/employees/stores', headers=self.headers('manager')).get_json()
        self.assertEqual([row['name'] for row in stores if row['name'] in expected], expected)
        self.assertEqual(stores[0]['rank'], 'store_manager')
        roles = {'auth0|xiaona': 'rol_manager', 'auth0|kitty': 'rol_manager', 'auth0|amy': 'rol_viewer'}
        auth0_users = [{'user_id': sub, 'username': sub.split('|')[1]} for sub in
                       ('auth0|yc', 'auth0|amy', 'auth0|celia', 'auth0|owner', 'auth0|kitty', 'auth0|xiaona', 'auth0|zimo')]
        members = {'rol_admin': ['auth0|owner'], 'rol_manager': ['auth0|xiaona', 'auth0|kitty'],
                   'rol_staff': ['auth0|yc', 'auth0|celia', 'auth0|zimo'], 'rol_viewer': ['auth0|amy']}
        with patch.object(users, 'AUTH0_MGMT_CLIENT_ID', 'id'), patch.object(users, 'AUTH0_MGMT_CLIENT_SECRET', 'secret'), \
                patch.object(users, '_mgmt_get', return_value=Mock(ok=True, json=lambda: auth0_users)), \
                patch.object(users, '_get_role_map', return_value=ROLE_IDS), \
                patch.object(users, '_fetch_role_member_ids', side_effect=lambda role: members[role]):
            listed = self.client.get('/api/users', headers=self.headers('admin')).get_json()
        self.assertEqual([(row['username'], row['role'], row['rank']) for row in listed], [
            ('owner', 'admin', None), ('xiaona', 'manager', 'store_manager'), ('kitty', 'manager', 'assistant_manager'),
            ('zimo', 'staff', 'senior'), ('celia', 'staff', 'regular'), ('yc', 'staff', 'trainee'),
            ('amy', 'viewer', None)])
        self.assertEqual(roles['auth0|amy'], 'rol_viewer')
        # A viewer or an admin the upgrade gave a rank (marked 转正 before) loses it when Users is read.
        with closing(self.connect()) as con:
            con.execute("UPDATE employees SET rank='regular' WHERE auth0_id='auth0|amy'")
            con.execute("INSERT INTO employees(auth0_id, name, rank) VALUES ('auth0|owner', 'Owner', 'regular')")
            con.commit()
        with patch.object(users, 'AUTH0_MGMT_CLIENT_ID', 'id'), patch.object(users, 'AUTH0_MGMT_CLIENT_SECRET', 'secret'), \
                patch.object(users, '_mgmt_get', return_value=Mock(ok=True, json=lambda: auth0_users)), \
                patch.object(users, '_get_role_map', return_value=ROLE_IDS), \
                patch.object(users, '_fetch_role_member_ids', side_effect=lambda role: members[role]):
            self.client.get('/api/users', headers=self.headers('admin'))
        self.assertEqual((self.employee('auth0|amy')['rank'], self.employee('auth0|owner')['rank']), (None, None))
        self.assertEqual(self.employee('auth0|celia')['rank'], 'regular')

    def test_old_statuses_become_ranks_and_positions_move_to_back(self):
        con = sqlite3.connect(':memory:')
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.executescript("""CREATE TABLE _migrations(name TEXT);
            CREATE TABLE employees(id INTEGER PRIMARY KEY, name TEXT, is_training INTEGER DEFAULT 0, regular_since TEXT);
            CREATE TABLE shifts(id INTEGER PRIMARY KEY, position TEXT NOT NULL DEFAULT '');
            CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO employees(name, is_training, regular_since) VALUES ('t', 1, NULL), ('r', 0, '2026-10-01'), ('n', 0, NULL);
            INSERT INTO shifts(position) VALUES ('End'), ('Front'), ('');
            INSERT INTO app_settings VALUES ('schedule_positions', '{"DT": ["Front", "Cashier", "End"]}');""")
        db._migration_employee_ranks(con, cur)
        db._migration_shift_positions_back_and_markham(con, cur)
        self.assertEqual([tuple(row) for row in cur.execute('SELECT name, rank FROM employees ORDER BY id')],
                         [('t', 'trainee'), ('r', 'regular'), ('n', None)])
        self.assertEqual([row[0] for row in cur.execute('SELECT position FROM shifts ORDER BY id')], ['Back', 'Front', ''])
        self.assertEqual(json.loads(cur.execute("SELECT value FROM app_settings").fetchone()[0]),
                         {'DT': ['Front', 'Cashier', 'Back'], 'MK': ['Cashier', 'Sales']})
        self.assertEqual(json.loads(settings.SETTINGS_DEFAULTS['schedule_positions']),
                         {'DT': ['Front', 'Cashier', 'Back'], 'MK': ['Cashier', 'Sales']})
        self.assertEqual(ranks.RANK_ROLE['assistant_manager'], 'manager')
