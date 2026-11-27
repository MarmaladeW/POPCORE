"""Everyone uses the app, and nothing comes off stock twice (owner, 2026-11-20).

Until a store's Clover is connected its sales leave stock through the pasted 日报, so staff checkout and 补录 wait
for Clover there (app_settings staff_checkout='clover'; managers and admins keep both). And a 补货 restock that was
received already brought its goods down, so the same day's 出库入店 lines move only what goes beyond it."""
from contextlib import closing
from unittest.mock import patch

from support import db  # noqa: F401  (puts the app on the path first)
import checkout_access  # noqa: E402
import report_checks  # noqa: E402
import report_stock  # noqa: E402
import test_checkout  # noqa: E402
from blueprints import settings  # noqa: E402
from test_receiving import ReceivingFixture  # noqa: E402
from test_restock_sets_as_boxes import SetsFixture  # noqa: E402


def setting(con, key, value):
    con.execute('INSERT INTO app_settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (key, value))


class StaffCheckoutWaitsForCloverTests(ReceivingFixture):
    call = test_checkout.CheckoutTests.call
    create_checkout = test_checkout.CheckoutTests.create_checkout

    def setUp(self):
        super().setUp()
        self.app.register_blueprint(settings.bp)
        test_checkout.CheckoutTests.seed_checkout_shifts(self)
        with closing(self.connect()) as con:
            con.execute("""INSERT INTO inventory_balances(product_id, location_id, disposition, quantity, version)
                           VALUES (?, ?, 'saleable', 5, 1)""", (self.product_id, self.floor))
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)", (self.store_id,))
            setting(con, 'staff_checkout', 'clover')
            con.commit()

    def body(self, reference):
        return dict(store_id=self.store_id, business_date='2026-09-08', reference=reference,
                    subtotal_cents=4000, source_tax_cents=520, gross_cents=4520, reduction_cents=0, rounding_cents=0,
                    collected_cents=4520, lines=[dict(product_id=self.product_id, unit='piece', quantity=2,
                                                      unit_price_cents=2000, source_tax_cents=520)])

    def stores(self, role):
        return {row['id']: row['checkout'] for row in self.client.get('/api/stores', headers=self.headers(role)).get_json()}

    def test_staff_wait_for_clover_and_managers_keep_checkout(self):
        refused = self.call('', self.body('staff-order'), 'staff-order')
        self.assertEqual(refused.status_code, 403, refused.get_json())
        self.assertEqual(refused.get_json()['code'], 'checkout_needs_clover')
        access = self.client.get('/api/checkouts/access', headers=self.headers()).get_json()
        self.assertEqual([store['id'] for store in access['live_stores']], [self.store_id])
        self.assertEqual(access['checkout_stores'], [])
        self.assertFalse(self.stores('staff')[self.store_id])
        # 补录 Record sale waits too.
        sale = self.client.post('/api/sale-documents', headers={**self.headers(), 'Idempotency-Key': 'staff-sale'}, json={
            **self.body('staff-sale'), 'entry_mode': 'planned_entry',
            'source': {'system': 'manual', 'account': str(self.store_id), 'reference': 'staff-sale'}})
        self.assertEqual((sale.status_code, sale.get_json().get('code')), (403, 'sale_needs_manager'), sale.get_json())
        # A manager takes the checkout as before.
        self.assertTrue(self.stores('manager')[self.store_id])
        managed = self.call('', self.body('manager-order'), 'manager-order', 'manager')
        self.assertEqual(managed.status_code, 201, managed.get_json())
        # Claw and display events keep the shift's store (live_stores is unchanged).
        self.assertTrue(access['live_stores'])

    def test_a_connected_clover_opens_it_for_staff(self):
        with closing(self.connect()) as con:
            self.assertFalse(checkout_access.checkout_open(con, 'staff', self.store_id))
            with patch('clover_checkout.store_config', side_effect=lambda sid: {'store_id': sid} if sid == self.store_id else None):
                self.assertTrue(checkout_access.checkout_open(con, 'staff', self.store_id))
                self.assertFalse(checkout_access.checkout_open(con, 'staff', self.store_id + 100))
            self.assertTrue(checkout_access.checkout_open(con, 'manager', self.store_id))

    def test_an_admin_can_open_it_for_every_store(self):
        bad = self.client.put('/api/settings', json={'staff_checkout': 'sometimes'}, headers=self.headers('admin'))
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.put('/api/settings', json={'staff_checkout': 'clover'}, headers=self.headers('manager')).status_code, 403)
        self.assertEqual(self.client.get('/api/settings', headers=self.headers('admin')).get_json()['staff_checkout'], 'clover')
        opened = self.client.put('/api/settings', json={'staff_checkout': 'all'}, headers=self.headers('admin'))
        self.assertEqual(opened.status_code, 200, opened.get_json())
        self.assertTrue(self.stores('staff')[self.store_id])
        created = self.call('', self.body('staff-order'), 'staff-order')
        self.assertEqual(created.status_code, 201, created.get_json())


class RolloutMigrationTests(ReceivingFixture):
    def rerun(self, con, name):
        con.execute('DELETE FROM _migrations WHERE name=?', (name,))
        getattr(db, f'_migration_{name}')(con, con.cursor())

    def value(self, con, key):
        row = con.execute('SELECT value FROM app_settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def test_a_live_database_starts_with_staff_checkout_waiting(self):
        with closing(self.connect()) as con:
            con.execute("DELETE FROM app_settings WHERE key='staff_checkout'")
            con.execute('UPDATE inventory_scope_state SET opening_verified=0')
            self.rerun(con, 'staff_checkout_until_clover')
            self.assertIsNone(self.value(con, 'staff_checkout'))
            con.execute('UPDATE inventory_scope_state SET opening_verified=1')
            self.rerun(con, 'staff_checkout_until_clover')
            self.assertEqual(self.value(con, 'staff_checkout'), 'clover')

    def test_restock_skips_start_after_the_last_saved_report(self):
        with closing(self.connect()) as con:
            con.execute("DELETE FROM app_settings WHERE key='app_lines_from'")
            con.execute("INSERT INTO daily_report_metadata(date, store) VALUES ('2026-11-18', 'DT'), ('2026-11-12', 'MK')")
            self.rerun(con, 'app_lines_from')
            self.assertEqual(self.value(con, 'app_lines_from'), '2026-11-19')


class RestockCoversRefillLinesTests(SetsFixture):
    def setUp(self):
        super().setUp()
        self.pick()
        received = self.act('receive', 'receive', 2, [
            {'line_no': 1, 'quantity': 1}, {'line_no': 2, 'quantity': 2}, {'line_no': 3, 'quantity': 4}])
        self.assertEqual(received.status_code, 200, received.get_json())
        # The restock brought 1 set as 12 boxes plus 2 loose boxes, and 4 pieces.
        self.starting('2026-11-01')

    def starting(self, day):
        with closing(self.connect()) as con:
            setting(con, 'app_lines_from', day)
            con.commit()

    def plan(self, extra=False):
        lines = [
            (1, {'section': 'refill', 'product_id': self.box, 'box_size': 12, 'num_boxes': 2, 'loose_qty': 0, 'qty': 2,
                 'raw_name': '小野雾中人'}),
            (2, {'section': 'refill', 'product_id': self.box, 'box_size': 1, 'num_boxes': 3, 'loose_qty': 0, 'qty': 3,
                 'raw_name': '小野雾中人'}),
            (3, {'section': 'refill', 'product_id': self.product_id, 'box_size': 1, 'num_boxes': 4, 'loose_qty': 0,
                 'qty': 4, 'raw_name': 'piece', 'extra': extra}),
        ]
        with closing(self.connect()) as con:
            return {(line['kind'], line['to_product_id']): line['wanted']
                    for line in report_stock.plan_report_lines(con, self.store_id, lines, '2026-11-14')}

    def test_a_received_restock_is_not_brought_down_again(self):
        with closing(self.connect()) as con:
            recorded = report_stock.app_moves(con, self.store_id, '2026-11-14')
        self.assertEqual({key: units for key, (units, _) in recorded.items()},
                         {('move', self.back, self.floor, self.box): 14, ('move', self.back, self.floor, self.product_id): 4})
        self.assertTrue(all(label.startswith(f'补货 #{self.session_id}') for _, labels in recorded.values() for label in labels))
        # 12*2: the first set came down with the restock, the second still moves. Of 3 loose boxes the restock's
        # other 2 cover two. The 4 pieces all came with it, unless the manager says they are another batch.
        self.assertEqual(self.plan(), {('correction', self.box): 1, ('move', self.box): 1})
        self.assertEqual(self.plan(extra=True), {('correction', self.box): 1, ('move', self.box): 1, ('move', self.product_id): 4})

    def test_days_before_the_start_keep_their_hand_fixes(self):
        self.starting('2026-11-15')
        self.assertEqual(self.plan(), {('correction', self.box): 2, ('move', self.box): 3, ('move', self.product_id): 4})

    def test_the_paste_says_why_the_line_moves_less(self):
        with closing(self.connect()) as con:
            checks = report_checks.app_checks(con, [
                {'line': 4, 'section': 'refill', 'product': {'id': self.box}, 'qty': 1, 'box_size': 12},
                {'line': 5, 'section': 'refill', 'product': {'id': self.box}, 'qty': 1, 'box_size': 12, 'to_display': True},
                {'line': 6, 'section': 'stock_in', 'product': {'id': self.box}, 'qty': 2, 'box_size': 1},
            ], self.store_id, '2026-11-14')
        self.assertEqual([(check['lines'], check['extra_line']) for check in checks], [([4], 4)])
        self.assertIn(f'补货 #{self.session_id}', checks[0]['problem'])
        self.assertIn('14', checks[0]['problem'])


import test_report_stock  # noqa: E402


class LaterCountsTests(test_report_stock.ReportStockTests):
    """A 盘点 taken after a report's day already shows that day (owner, 2026-11-20): pasting the day later must not
    take its sales off again, nor move a counted place by its stock lines. (The fixture's own tests run there.)"""
    DAY, NEXT = test_report_stock.LIVE_DATE, '2026-11-02'

    def count(self, product, code, observed, day=None):
        with closing(self.connect()) as con:
            count_id = con.execute(
                """INSERT INTO inventory_counts(store_id, location_id, business_date, status, created_by)
                   VALUES (?, ?, ?, 'approved', 'auth0|staff')""", (self.dt, self.locations[code], day or self.NEXT)).lastrowid
            con.execute("""INSERT INTO inventory_count_lines(count_id, line_no, product_id, native_unit,
                               expected_quantity, observed_quantity, captured_balance_version)
                           VALUES (?, 1, ?, 'box', ?, ?, 1)""", (count_id, product, observed, observed))
            con.commit()

    def balance(self, product, code):
        with closing(self.connect()) as con:
            row = con.execute("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                                 AND disposition='saleable'""", (product, self.locations[code])).fetchone()
            return row[0] if row else 0

    def test_sales_already_in_a_later_count_do_not_come_off_again(self):
        self.count(self.box, 'floor', 30)
        stock = self.report([(self.box, 3), (self.item, 1)]).get_json()['stock']
        self.assertEqual([(row['product_id'], row['quantity'], row['counted_on']) for row in stock['counted_later']],
                         [(self.box, 3, self.NEXT)])
        self.assertEqual((stock['taken'], self.floor(self.box), self.floor(self.item)), (1, 30, 2))
        # A re-paste with more or fewer sold still leaves the counted floor alone.
        self.assertEqual(self.report([(self.box, 5), (self.item, 1)]).get_json()['stock']['counted_later'][0]['quantity'], 2)
        self.assertEqual(self.report([(self.box, 1), (self.item, 1)]).get_json()['stock']['counted_later'][0]['quantity'], -4)
        self.assertEqual(self.floor(self.box), 30)
        # A count on the same day still only warns, as before.
        self.assertEqual(self.report([(self.item, 2)], day=self.NEXT).get_json()['stock']['counted_later'], [])

    def test_what_was_taken_before_the_count_stays_and_nothing_more_moves(self):
        self.report([(self.box, 3)])
        self.assertEqual(self.floor(self.box), 27)
        self.count(self.box, 'floor', 27)
        stock = self.report([(self.box, 6)]).get_json()['stock']
        self.assertEqual((stock['taken'], stock['counted_later'][0]['quantity'], self.floor(self.box)), (0, 3, 27))

    def test_a_later_count_settles_the_notice_it_covers(self):
        self.report([(self.item, 5)])
        self.assertIn((self.item, 'not_enough_on_floor'), self.notices())
        self.count(self.item, 'floor', 0)
        self.assertNotIn((self.item, 'not_enough_on_floor'), self.notices())

    def test_a_stock_line_moves_only_the_side_nobody_counted_later(self):
        def body(sets):
            line = {'product_id': self.box, 'section': 'refill', 'raw_name': 'Echo', 'notes': '', 'box_size': 12,
                    'num_boxes': sets, 'loose_qty': 0, 'to_display': False, 'source_bucket': 'confirmed'}
            sale = {'product_id': self.item, 'section': 'pos', 'qty_pos': 1, 'qty_cash': 0, 'raw_name': 'Bear'}
            return {'date': self.DAY, 'store_code': 'DT', 'mode': 'replace', 'classification': 'summary_only',
                    'stock_mode': 'live', 'items': [line] if sets else [sale]}

        def save(sets):
            submit = lambda **extra: self.client.post('/api/sales/submit_daily_report', json={**body(sets), **extra},  # noqa: E731
                                                      headers=self.headers('manager'))
            preview = submit(dry_run=True)
            self.assertEqual(preview.status_code, 200, preview.get_json())
            saved = submit(stock_digest=preview.get_json()['stock_digest'])
            self.assertEqual(saved.status_code, 200, saved.get_json())
            return preview.get_json()['stock']['lines'][0]

        self.count(self.box, 'floor', 30)
        line = save(1)
        # The set still leaves upstairs; the counted floor gets nothing more.
        self.assertEqual((line['settled'], line['one_sided'], line['counted_later']), (1, 1, {'to': self.NEXT}))
        self.assertEqual((self.balance(self.set, 'upstairs'), self.floor(self.box)), (1, 30))
        # The line taken back off the report puts only that side back.
        line = save(0)
        self.assertEqual((line['settled'], line['one_sided']), (-1, -1))
        self.assertEqual((self.balance(self.set, 'upstairs'), self.floor(self.box)), (2, 30))


class OtherScreensTests(test_report_stock.ReportStockTests):
    """收货 receipts, display entries and claw prizes recorded in the app are not moved again by the 日报."""
    DAY = test_report_stock.LIVE_DATE

    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            setting(con, 'app_lines_from', '2026-10-01')
            con.commit()

    def post(self, source_type, source_id, line):
        from goods_operations import _balance
        from inventory_commands import post_inventory
        with closing(self.connect()) as con:
            line['expected_versions'] = {side: _balance(con, line['product_id'], line[f'{side}_location_id'], 'saleable')['version']
                                         for side in ('from', 'to') if f'{side}_location_id' in line}
            post_inventory(con, {'kind': 'receipt' if 'to_location_id' in line and 'from_location_id' not in line
                                 else 'move' if 'to_location_id' in line else 'consume',
                                 'business_date': self.DAY, 'source_type': source_type, 'source_id': source_id,
                                 'lines': [line]}, actor=ADMIN, request_key=f'test:{source_type}:{source_id}')

    def plan(self, *items):
        with closing(self.connect()) as con:
            return {(line['section'], line['kind']): line['wanted'] for line in report_stock.plan_report_lines(
                con, self.dt, list(enumerate(items, 1)), self.DAY)}

    def test_a_receipt_and_a_display_entry_are_not_moved_twice(self):
        self.post('goods_receipt', '7', {'product_id': self.item, 'unit': 'piece', 'quantity': 2,
                                         'to_location_id': self.locations['floor'], 'to_disposition': 'saleable'})
        self.post('store_event', 'k1:display', {'product_id': self.box, 'unit': 'box', 'quantity': 1,
                                                'from_location_id': self.locations['floor'], 'from_disposition': 'saleable',
                                                'to_location_id': self.locations['display'], 'to_disposition': 'saleable'})
        planned = self.plan(
            {'section': 'stock_in', 'product_id': self.item, 'box_size': 1, 'num_boxes': 3, 'loose_qty': 0, 'qty': 3},
            {'section': 'break_display', 'product_id': self.box, 'qty': 1})
        # 入店 3 after a 收货 of 2 moves 1; the 拆display the app already did moves nothing.
        self.assertEqual(planned, {('stock_in', 'receipt'): 1})


ADMIN = test_report_stock.ADMIN

# Only this module's tests run here; the fixture's tests run in test_report_stock.
for _name in [name for name in vars(test_report_stock.ReportStockTests) if name.startswith('test_')]:
    setattr(LaterCountsTests, _name, None)
    setattr(OtherScreensTests, _name, None)


class ActivityLogTests(StaffCheckoutWaitsForCloverTests):
    """动态 Activity: every change leaves one line with who did it, readable like a chat (owner, 2026-11-20)."""

    def feed(self, role='manager', **params):
        response = self.client.get('/api/activity', query_string=params, headers=self.headers(role))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def rows(self):
        with closing(self.connect()) as con:
            return [dict(row) for row in con.execute('SELECT * FROM activity_log ORDER BY id')]

    def test_each_change_leaves_one_line_and_a_retry_leaves_none(self):
        managed = self.call('', self.body('manager-order'), 'manager-order', 'manager')
        self.assertEqual(managed.status_code, 201)
        again = self.call('', self.body('manager-order'), 'manager-order', 'manager')
        self.assertIn(again.status_code, (200, 201))
        refused = self.call('', self.body('staff-order'), 'staff-order')   # refused: no line
        self.assertEqual(refused.status_code, 403)
        rows = self.rows()
        self.assertEqual([(row['endpoint'], row['actor_sub'], row['store_id']) for row in rows],
                         [('checkout.create', 'auth0|manager', self.store_id)])
        self.assertEqual(rows[0]['actor_name'], 'Manager')
        self.assertIn('买单', rows[0]['summary'])

    def test_only_the_admin_reads_everyones_lines(self):
        """Owner, 2026-11-26: only the admin sees 动态. Everyone else, managers included, reads their own lines."""
        self.call('', self.body('manager-order'), 'manager-order', 'manager')
        from activity import _business_date
        day = _business_date()
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|counter', ?)", (self.store_id,))
            con.executemany(
                "INSERT INTO activity_log(business_date, store_id, actor_sub, actor_name, via_sub, kind, endpoint, summary) "
                "VALUES (?, ?, ?, ?, ?, 'stock', 'counting.save', ?)",
                [(day, self.store_id, 'auth0|helper', 'Photo Helper', None, '盘点 Counted'),
                 # Done on the store computer under the staff member's name: theirs, not the shared account's.
                 (day, self.store_id, 'auth0|staff', 'Staff', 'auth0|counter', '盘点 Counted at the counter')])
            con.commit()
        admin = self.feed('admin', date=day, store_code='DT')
        self.assertEqual(len(admin['entries']), 3)
        self.assertLessEqual({'Manager', 'Photo Helper'}, {person['name'] for person in admin['people']})
        manager = self.feed('manager', date=day, store_code='DT')
        self.assertEqual([entry['actor_sub'] for entry in manager['entries']], ['auth0|manager'])
        self.assertEqual([person['sub'] for person in manager['people']], ['auth0|manager'])
        staff = self.feed('staff', date=day, store_code='DT')
        self.assertEqual([entry['summary'] for entry in staff['entries']], ['盘点 Counted at the counter'])
        self.assertEqual(self.feed('staff', date=day, store_code='DT', person='auth0|helper')['entries'], [])
        counter = self.feed('viewer:counter', date=day, store_code='DT')
        self.assertEqual((counter['entries'], counter['people']), ([], []))
        self.assertEqual(self.client.get('/api/activity', query_string={'store_code': 'MK'},
                                         headers=self.headers('staff')).status_code, 403)


from support import IsolatedApiCase  # noqa: E402


class StoreComputerAsksNothingTests(IsolatedApiCase):
    """Owner, 2026-11-26: "PIN not necessary", then "dont need the 谁在操作 on the store computer, we have cameras".
    A change made on the store computer is its shared account's own; nothing asks who."""

    def setUp(self):
        super().setUp()
        from auth import role_required
        from flask import jsonify

        @self.app.post('/api/test/touch')
        @role_required('viewer')
        def touch():  # a change the store computer may make
            return jsonify(ok=True)

        with closing(self.connect()) as con:
            self.zimo = con.execute("INSERT INTO employees(auth0_id, name) VALUES ('auth0|staff', 'Zimo')").lastrowid
            con.execute("INSERT INTO employees(auth0_id, name) VALUES ('auth0|counter', 'DT 柜台')")
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|counter', ?)", (self.store_id,))
            con.commit()

    def test_a_change_on_the_store_computer_is_the_shared_accounts_own(self):
        counter = self.headers('viewer:counter')
        sent = self.client.post('/api/test/touch', json={'store_id': self.store_id}, headers=counter)
        self.assertEqual(sent.status_code, 200, sent.get_json())
        # A page loaded before this still sends a name (and an older one a PIN): nothing reads it.
        for person in (str(self.zimo), f'{self.zimo}:9999', 'nobody'):
            sent = self.client.post('/api/test/touch', json={'store_id': self.store_id},
                                    headers={**counter, 'X-Popcore-Person': person})
            self.assertEqual(sent.status_code, 200, person)
        with closing(self.connect()) as con:
            marks = {tuple(row) for row in con.execute(
                "SELECT actor_sub, actor_name, via_sub FROM activity_log WHERE endpoint='touch'")}
        self.assertEqual(marks, {('auth0|counter', 'DT 柜台', None)})

    def test_the_pin_and_the_name_list_are_gone(self):
        """Their routes answer 404, and the migration wiped what was kept of every PIN."""
        for method, path in (('get', '/api/me/pin'), ('post', '/api/me/pin'), ('post', '/api/counter/check'),
                             ('get', '/api/counter/people'), ('delete', f'/api/employees/{self.zimo}/pin')):
            for role in ('viewer:counter', 'staff', 'admin'):
                self.assertEqual(getattr(self.client, method)(path, headers=self.headers(role)).status_code, 404, (method, path))
        import db as database
        with closing(self.connect()) as con:
            con.execute("""UPDATE employees SET pin_hash='hash', pin_salt='salt', pin_set_at='2026-11-20', pin_failures=3,
                               pin_locked_until='2026-11-21' WHERE id=?""", (self.zimo,))
            con.execute("DELETE FROM _migrations WHERE name='employee_pins_cleared'")
            con.commit()
            database._migration_employee_pins_cleared(con, con.cursor())
            con.commit()
            self.assertEqual(tuple(con.execute("""SELECT pin_hash, pin_salt, pin_set_at, pin_failures, pin_locked_until
                                                   FROM employees WHERE id=?""", (self.zimo,)).fetchone()),
                             (None, None, None, 0, None))


class RestockInOneGoTests(SetsFixture):
    """搬下来了 Done: one person does a whole restock in one tap; 动态 shows it arriving, not every step."""

    def test_the_quiet_steps_leave_one_arrival_line_naming_the_goods(self):
        quiet = {**self.headers(), 'X-Popcore-Quiet': '1'}
        with closing(self.connect()) as con:
            con.execute("UPDATE restock_sessions SET status='pending' WHERE id=?", (self.session_id,))
            con.execute("UPDATE restock_items SET pick_status='pending', found_qty=NULL WHERE session_id=?", (self.session_id,))
            con.commit()
        self.assertEqual(self.client.post(f'/api/restock/session/{self.session_id}/submit', headers=quiet).status_code, 200)
        for item in self.items:
            self.assertEqual(self.client.patch(f'/api/restock/items/{item}/pick', json={'pick_status': 'found', 'found_qty': 1},
                                               headers=quiet).status_code, 200)
        sent = self.client.post(f'/api/restock/session/{self.session_id}/pick', json={
            'expected_version': 1, 'lines': [{'line_no': n, 'quantity': 1} for n in (1, 2, 3)]},
            headers={**quiet, 'Idempotency-Key': 'sent'})
        self.assertEqual(sent.status_code, 200, sent.get_json())
        got = self.act('receive', 'got', 2, [{'line_no': n, 'quantity': 1} for n in (1, 2, 3)])
        self.assertEqual(got.status_code, 200, got.get_json())
        with closing(self.connect()) as con:
            rows = [dict(row) for row in con.execute('SELECT endpoint, summary, detail FROM activity_log ORDER BY id')]
        self.assertEqual([row['endpoint'] for row in rows], ['restock.restock_goods_action'])
        self.assertIn(f'补货 #{self.session_id}', rows[0]['summary'])
        self.assertIn('收到 Received', rows[0]['detail'])
        self.assertIn('小野雾中人', rows[0]['detail'])


class LiveEveningCountTests(test_report_stock.ReportStockTests):
    """晚盘 at a live store, as WeChat writes it: sets and loose boxes on the floor only; ✅ or 🥲 against POPCORE's
    floor, saved again the same night, nothing moves (owner, 2026-11-20/21)."""

    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET is_bestseller=1 WHERE id IN (?, ?)', (self.box, self.item))
            con.execute("INSERT INTO employees(auth0_id, name) VALUES ('auth0|staff', 'Zimo')")
            con.commit()

    def today(self, role='staff'):
        response = self.client.get('/api/inventory-check/today?store_code=DT', headers=self.headers(role))
        self.assertEqual(response.status_code, 200, response.get_json())
        return {item['product_id']: item for item in response.get_json()['items']}

    def submit(self, checks):
        return self.client.post('/api/inventory-check/submit', json={'store_code': 'DT', 'checks': checks},
                                headers=self.headers('staff'))

    def test_staff_count_sets_and_loose_and_see_whether_it_matches(self):
        items = self.today()
        self.assertEqual((items[self.box]['per_set'], items[self.box]['expected_sets']), (12, None))  # no 库存查看
        manager = self.today('manager')
        # Upstairs' 2 sets are not part of 晚盘: the floor holds 30 boxes.
        self.assertEqual((manager[self.box]['expected_sets'], manager[self.box]['expected_loose']), (0, 30))
        # Two unopened sets of 12 on the floor shelf and 6 loose boxes are the floor's 30.
        saved = self.submit([{'product_id': self.box, 'sets': 2, 'loose': 6}, {'product_id': self.item, 'loose': 2}])
        self.assertEqual(saved.status_code, 201, saved.get_json())
        self.assertEqual(saved.get_json()['differ'], 1)
        items = {item['product_id']: item for item in saved.get_json()['items']}
        self.assertEqual((items[self.box]['saved']['matches'], items[self.item]['saved']['matches']), (True, False))
        self.assertEqual(items[self.box]['saved']['by'], 'Zimo')
        # Counted again the same night: the latest count stands. Nothing moved.
        again = self.submit([{'product_id': self.item, 'loose': 3}]).get_json()
        self.assertTrue({item['product_id']: item for item in again['items']}[self.item]['saved']['matches'])
        self.assertEqual((self.floor(self.box), self.floor(self.item)), (30, 3))
        with closing(self.connect()) as con:
            line = con.execute("SELECT detail FROM activity_log WHERE endpoint='inventory.submit_inventory_check' "
                               "ORDER BY id").fetchall()
        self.assertEqual([row[0] for row in line], ['2 项 items · 1 🥲', '1 项 items · ✅'])


for _name in [name for name in vars(test_report_stock.ReportStockTests) if name.startswith('test_')]:
    setattr(LiveEveningCountTests, _name, None)
