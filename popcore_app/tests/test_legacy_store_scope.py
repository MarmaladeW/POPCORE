"""Legacy stock, daily-report, restock and 晚盘 writes are scoped to the stores a person works at."""
from contextlib import closing
from datetime import datetime, timezone
from unittest.mock import patch

from support import IsolatedApiCase

TODAY = '2026-10-26'
# 01:30 UTC on Oct 27 is still Oct 26 in Toronto, the store's business day.
SHIFT_DAY = datetime(2026, 10, 27, 1, 30, tzinfo=timezone.utc)
NEXT_DAY = datetime(2026, 10, 27, 16, 0, tzinfo=timezone.utc)
DENIED = (403, 'inventory_forbidden')
TABLES = ('stock', 'stock_transactions', 'daily_sales', 'daily_report_metadata',
          'restock_sessions', 'restock_items', 'inventory_checks', 'stock_movements')


class _Clock(datetime):
    now_utc = SHIFT_DAY

    @classmethod
    def now(cls, tz=None):
        return cls.now_utc.astimezone(tz) if tz else cls.now_utc.replace(tzinfo=None)


class LegacyStoreScopeTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        _Clock.now_utc = SHIFT_DAY
        clock = patch('checkout_access.datetime', _Clock)
        clock.start()
        self.addCleanup(clock.stop)
        with closing(self.connect()) as con:
            self.mk_id = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            # Works at Markham only: Schedule membership and inventory access for MK.
            for subject in ('mkstaff', 'mkmanager'):
                employee = con.execute('INSERT INTO employees(auth0_id,name) VALUES (?,?)',
                                       (f'auth0|{subject}', subject)).lastrowid
                con.execute('INSERT INTO employee_stores(employee_id,store_id) VALUES (?,?)', (employee, self.mk_id))
                con.execute('INSERT INTO inventory_access(auth0_sub,store_id) VALUES (?,?)',
                            (f'auth0|{subject}', self.mk_id))
            # Three ways in at Downtown: a grant, a Schedule membership, a shift today.
            con.execute("INSERT INTO inventory_access(auth0_sub,store_id) VALUES ('auth0|granted',?)", (self.store_id,))
            member = con.execute("INSERT INTO employees(auth0_id,name) VALUES ('auth0|member','Member')").lastrowid
            con.execute('INSERT INTO employee_stores(employee_id,store_id) VALUES (?,?)', (member, self.store_id))
            on_shift = con.execute("INSERT INTO employees(auth0_id,name) VALUES ('auth0|onshift','On shift')").lastrowid
            con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                           VALUES (?,?,'12:00','21:00','manager',?)""", (on_shift, TODAY, self.store_id))
            con.execute("""INSERT INTO daily_sales(product_id,date,store,qty_pos,qty_sold,unit_price)
                           VALUES (?,?,'DT',3,3,89.0)""", (self.product_id, TODAY))
            self.record_id = con.execute('SELECT id FROM daily_sales').fetchone()[0]
            self.pending = con.execute("INSERT INTO restock_sessions(date,status,store_id) VALUES (?,'pending',?)",
                                       (TODAY, self.store_id)).lastrowid
            self.pending_item = con.execute(
                'INSERT INTO restock_items(session_id,product_id,requested_qty) VALUES (?,?,2)',
                (self.pending, self.product_id)).lastrowid
            self.submitted = con.execute("INSERT INTO restock_sessions(date,status,store_id) VALUES (?,'submitted',?)",
                                         (TODAY, self.store_id)).lastrowid
            self.submitted_item = con.execute(
                'INSERT INTO restock_items(session_id,product_id,requested_qty) VALUES (?,?,2)',
                (self.submitted, self.product_id)).lastrowid
            con.commit()

    def dt_writes(self):
        pid, day = self.product_id, TODAY
        return [
            ('post', '/api/stock/ru_dian', {'store_code': 'DT', 'product_id': pid, 'qty': 1}, 'staff'),
            ('post', '/api/stock/restock_upstairs', {'store_code': 'DT', 'product_id': pid, 'qty': 1}, 'staff'),
            ('post', '/api/stock/adjust', {'store_code': 'DT', 'product_id': pid, 'new_qty': 0,
                                           'location': 'upstairs', 'notes': 'count'}, 'staff'),
            ('patch', f'/api/stock/{pid}', {'store_code': 'DT', 'notes': 'moved'}, 'staff'),
            ('post', '/api/stock/batch_operation', {'store_code': 'DT', 'operation': 'ru_dian',
                                                    'items': [{'product_id': pid, 'qty': 1}]}, 'staff'),
            ('delete', '/api/stock/rows', {'store_code': 'DT', 'product_ids': [pid]}, 'manager'),
            ('post', '/api/sales/upsert', {'store_code': 'DT', 'product_id': pid, 'date': day,
                                           'qty_pos': 0, 'qty_cash': 0}, 'staff'),
            ('post', '/api/sales/add_product', {'store_code': 'DT', 'product_id': pid, 'date': '2026-10-25'}, 'staff'),
            ('post', '/api/sales/batch_upsert', {'store_code': 'DT', 'items': [
                {'product_id': pid, 'date': day, 'qty_pos': 9}]}, 'staff'),
            ('post', '/api/sales/submit_daily_report', {'store_code': 'DT', 'date': day,
                'classification': 'summary_only', 'items': [{'product_id': pid, 'section': 'pos', 'qty_pos': 1}]}, 'staff'),
            ('delete', f'/api/sales/clear_day?date={day}&store_code=DT', None, 'manager'),
            ('delete', f'/api/sales/record/{self.record_id}', None, 'manager'),
            ('post', '/api/restock/sessions', {'store_code': 'DT'}, 'staff'),
            ('post', '/api/restock/items', {'session_id': self.pending, 'product_id': pid, 'requested_qty': 5}, 'staff'),
            ('delete', f'/api/restock/items/{self.pending_item}', None, 'staff'),
            ('post', f'/api/restock/session/{self.pending}/submit', None, 'staff'),
            ('patch', f'/api/restock/items/{self.submitted_item}/pick', {'pick_status': 'not_found'}, 'staff'),
            ('post', f'/api/restock/session/{self.submitted}/complete', None, 'staff'),
            ('delete', f'/api/restock/session/{self.pending}', None, 'staff'),
            ('post', '/api/inventory-check/submit', {'store_code': 'DT',
                                                     'checks': [{'product_id': pid, 'actual_qty': 1}]}, 'staff'),
        ]

    def call(self, method, path, body, headers):
        return getattr(self.client, method)(path, json=body, headers=headers)

    def test_markham_staff_cannot_change_downtown_legacy_records(self):
        before = self.snapshot(TABLES)
        for method, path, body, role in self.dt_writes():
            subject = 'mkstaff' if role == 'staff' else 'mkmanager'
            with self.subTest(path=path):
                response = self.call(method, path, body, self.headers(f'{role}:{subject}'))
                self.assertEqual((response.status_code, (response.get_json() or {}).get('code')), DENIED)
        # Audit repro: a Markham-only staff member used to set Downtown upstairs 40 -> 0.
        self.assertEqual(self.snapshot(TABLES), before)

    def test_viewers_cannot_write_any_store(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access(auth0_sub,store_id) VALUES ('auth0|viewer',?)", (self.store_id,))
            con.commit()
        before = self.snapshot(TABLES)
        for method, path, body, _role in self.dt_writes():
            with self.subTest(path=path):
                self.assertEqual(self.call(method, path, body, self.headers('viewer')).status_code, 403)
        self.assertEqual(self.snapshot(TABLES), before)

    def test_grant_membership_shift_or_admin_each_let_people_write_their_store(self):
        for n, subject in enumerate(('staff:granted', 'staff:member', 'staff:onshift', 'admin:anyadmin'), 1):
            with self.subTest(subject=subject):
                headers = self.headers(subject)
                adjust = self.client.post('/api/stock/adjust', headers=headers, json={
                    'store_code': 'DT', 'product_id': self.product_id, 'new_qty': 10 + n,
                    'location': 'upstairs', 'notes': 'count'})
                self.assertEqual(adjust.status_code, 200, adjust.get_json())
                report = self.client.post('/api/sales/submit_daily_report', headers=headers, json={
                    'store_code': 'DT', 'date': f'2026-10-0{n}', 'classification': 'summary_only',
                    'items': [{'product_id': self.product_id, 'section': 'pos', 'qty_pos': n}]})
                self.assertEqual(report.status_code, 200, report.get_json())
                self.assertEqual(self.client.post('/api/restock/sessions', headers=headers,
                                                  json={'store_code': 'DT'}).status_code, 201)
        evening = self.client.post('/api/inventory-check/submit', headers=self.headers('staff:onshift'), json={
            'store_code': 'DT', 'checks': [{'product_id': self.product_id, 'actual_qty': 1}]})
        self.assertEqual(evening.status_code, 201, evening.get_json())

    def test_a_shift_opens_only_that_store_on_that_toronto_day(self):
        headers = self.headers('staff:onshift')

        def adjust(store_code, qty):
            return self.client.post('/api/stock/adjust', headers=headers, json={
                'store_code': store_code, 'product_id': self.product_id, 'new_qty': qty,
                'location': 'upstairs', 'notes': 'count'})

        self.assertEqual(adjust('DT', 7).status_code, 200)
        other = adjust('MK', 7)
        self.assertEqual((other.status_code, other.get_json()['code']), DENIED)
        _Clock.now_utc = NEXT_DAY  # Oct 27 in Toronto: no shift that day
        later = adjust('DT', 8)
        self.assertEqual((later.status_code, later.get_json()['code']), DENIED)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT upstairs_qty FROM stock WHERE product_id=? AND store_id=?',
                                         (self.product_id, self.store_id)).fetchone()[0], 7)

    def test_legacy_restock_sheets_stay_readable(self):
        response = self.client.get('/api/restock/sessions/today?store_code=DT', headers=self.headers('viewer'))
        self.assertEqual(response.status_code, 200)

    def test_daily_sales_reads_are_manager_only(self):
        for path in (f'/api/sales?date={TODAY}&store_code=ALL', '/api/sales/summary?store_code=ALL',
                     '/api/sales/recorded-dates?month=2026-10&store=ALL'):
            with self.subTest(path=path):
                # Audit repro: a viewer could read every store's sales with prices.
                for role in ('viewer', 'staff:granted'):
                    self.assertEqual(self.client.get(path, headers=self.headers(role)).status_code, 403)
                self.assertEqual(self.client.get(path, headers=self.headers('manager')).status_code, 200)
        rows = self.client.get(f'/api/sales?date={TODAY}&store_code=DT', headers=self.headers('manager')).get_json()
        self.assertEqual([(row['store'], row['qty_sold'], row['unit_price']) for row in rows], [('DT', 3, 89.0)])
