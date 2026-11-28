"""动态 change details (owner, 2026-11-28: "in 动态, admin should be able to click into there and see exactly what
changed"): each line keeps the records it changed with only the fields that changed, before → after, and the stock
it moved from place to place with each count before → after. Only the admin opens a line. Lines from before keep no
details and say so. Nothing secret is kept: no password, PIN, calendar token, pickup code, address or GPS position."""
import json
import unittest
from contextlib import closing

from support import IsolatedApiCase  # first: it puts popcore_app on the path

import activity_changes
import test_stock_moves
from blueprints import settings as settings_blueprint


def load_tests(loader, tests, pattern):
    """Only this file's tests: the stock fixture's own run from test_stock_moves."""
    suite = unittest.TestSuite()
    for case in (StockChangeTests, RecordChangeTests):
        suite.addTests(case(name) for name in loader.getTestCaseNames(case) if name in case.__dict__)
    return suite


class Lines:
    def line(self, endpoint):
        with closing(self.connect()) as con:
            row = con.execute('SELECT id, changes FROM activity_log WHERE endpoint=? ORDER BY id DESC LIMIT 1',
                              (endpoint,)).fetchone()
        self.assertIsNotNone(row, endpoint)
        return row['id'], json.loads(row['changes']) if row['changes'] else None

    def opened(self, entry_id, role='admin', status=200):
        response = self.client.get(f'/api/activity/{entry_id}', headers=self.headers(role))
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    @staticmethod
    def fields(changes, kind=None):
        return {(record['op'], field['label'], field['before'], field['after'])
                for record in changes['records'] if kind in (None, record['type']) for field in record['fields']}


class StockChangeTests(Lines, test_stock_moves.StockMoveTests):
    """DT live: 30 Echo boxes and 3 Bear on the floor, 2 Echo sets (12 boxes each) upstairs."""

    def test_a_move_keeps_each_place_before_and_after_and_a_set_opened_as_its_boxes(self):
        self.move('floor', 'display', [{'product_id': self.box, 'units': 2}], 'm-1')
        entry_id, changes = self.line('stock_moves.stock_move_create')
        [line] = changes['stock']
        self.assertEqual((line['qty'], line['from'], line['to']),
                         (2, {'place': 'DT 楼下', 'before': 30, 'after': 28}, {'place': 'DT Display', 'before': 0, 'after': 2}))
        self.assertIn('Echo', line['product'])
        # A set brought down lands as its boxes: one set off upstairs, twelve boxes onto the floor.
        self.move('upstairs', 'floor', [{'product_id': self.box, 'sets': 1}], 'm-2')
        _, changes = self.line('stock_moves.stock_move_create')
        [line] = changes['stock']
        self.assertEqual((line['qty'], line['from']['place'], line['from']['before'], line['from']['after']), (1, 'DT 楼上', 2, 1))
        self.assertEqual((line['to']['place'], line['to']['before'], line['to']['after']), ('DT 楼下', 28, 40))
        self.assertRegex(line['into'], r'×12$')

        opened = self.opened(entry_id)
        self.assertEqual((opened['summary'], opened['store_code'], opened['actor_name']),
                         ('移货 Moved stock · 楼下 → Display', 'DT', 'Zimo'))
        self.assertEqual(opened['changes']['stock'][0]['to']['after'], 2)
        for role in ('manager', 'staff', 'viewer'):
            self.opened(entry_id, role, 403)
        self.assertEqual(self.client.get('/api/activity/999999', headers=self.headers('admin')).status_code, 404)

    def test_an_undo_shows_the_goods_going_back_and_a_refused_move_keeps_nothing(self):
        move = self.move('floor', 'claw', [{'product_id': self.box, 'units': 4}], 'm-1')
        self.undo(move, 'u-1')
        _, changes = self.line('stock_moves.stock_move_undo')
        [line] = changes['stock']
        self.assertEqual((line['from']['place'], line['from']['before'], line['from']['after'], line['to']['place'],
                          line['to']['before'], line['to']['after']), ('DT 娃娃机', 4, 0, 'DT 楼下', 26, 30))
        with closing(self.connect()) as con:
            before = con.execute('SELECT count(*) FROM activity_log').fetchone()[0]
        self.move('floor', 'upstairs', [{'product_id': self.box, 'sets': 1}], 'm-2', expect=409)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM activity_log').fetchone()[0], before)

    def test_a_pasted_report_shows_each_sale_taken_off_the_floor_and_a_change_putting_back(self):
        self.assertEqual(self.report([(self.box, 3), (self.item, 1)]).status_code, 200)
        _, changes = self.line('sales.submit_daily_report')
        self.assertEqual({(line['qty'], line['from']['place'], line['from']['before'], line['from']['after'],
                           line['to']['place']) for line in changes['stock']},
                         {(3, 'DT 楼下', 30, 27, '卖出 Sold'), (1, 'DT 楼下', 3, 2, '卖出 Sold')})
        self.assertTrue(all(line['why'].startswith('日报') for line in changes['stock']), changes['stock'])
        # Pasted again with one Echo and no Bear: two Echo and the Bear go back on the floor.
        self.report([(self.box, 1)])
        _, changes = self.line('sales.submit_daily_report')
        self.assertEqual({(line['qty'], line['from']['place'], line['to']['place'], line['to']['before'], line['to']['after'])
                          for line in changes['stock']}, {(2, '调整 Adjusted', 'DT 楼下', 27, 29), (1, '调整 Adjusted', 'DT 楼下', 2, 3)})

    def test_a_document_rolled_back_is_never_shown(self):
        """A document rolled back with its request never shows, even once another document has its id."""
        self.move('floor', 'display', [{'product_id': self.box, 'units': 1}], 'm-1')
        with closing(self.connect()) as con:
            document = con.execute("SELECT id, request_key FROM inventory_documents ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(len(activity_changes.stock(con, [tuple(document)])), 1)
            self.assertEqual(activity_changes.stock(con, [(document['id'], 'a-request-rolled-back')]), [])
            self.assertEqual(activity_changes.stock(con, [(document['id'] + 50, document['request_key'])]), [])


class RecordChangeTests(Lines, IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(settings_blueprint.bp)
        self.open_every_month()
        with closing(self.connect()) as con:
            self.zimo = con.execute("""INSERT INTO employees(auth0_id, name, email, is_schedulable, pin_hash, pin_salt, ical_token)
                                       VALUES ('auth0|staff', 'Zimo', 'zimo@example.test', 1, 'HASH-1', 'SALT-1', 'TOKEN-1')""").lastrowid
            con.execute("UPDATE products SET price=19.99, brand='POPMART' WHERE id=?", (self.product_id,))
            con.commit()

    def test_a_product_edit_keeps_only_what_changed(self):
        # The edit form sends every field; only the price and the notes moved.
        response = self.client.patch(f'/api/products/{self.product_id}', headers=self.headers('manager'), json={
            'jizhanming': 'Test Product', 'name_cn_en': 'Test Product', 'brand': 'POPMART', 'price': 20.99,
            'notes': '新到 New in', 'boxes_per_dan': 1})
        self.assertEqual(response.status_code, 200, response.get_json())
        entry_id, changes = self.line('products.update_product')
        [record] = changes['records']
        self.assertEqual((record['type'], record['op'], record['label'], record['link']),
                         ('product', 'edit', 'TEST-1 Test Product', '/products'))
        self.assertEqual(self.fields(changes), {('edit', '价格 Price', '$19.99', '$20.99'),
                                                ('edit', '备注 Notes', None, '新到 New in')})
        self.assertEqual(self.opened(entry_id)['changes'], changes)
        # The line itself says what moved, not every field the form sent.
        self.assertEqual(self.opened(entry_id)['detail'], '价格 Price $19.99 → $20.99、备注 Notes — → 新到 New in')
        # Nothing changed: the line is still there, with nothing to show.
        self.client.patch(f'/api/products/{self.product_id}', headers=self.headers('manager'), json={'price': 20.99})
        self.assertIsNone(self.line('products.update_product')[1])

    def test_designs_added_and_an_other_name_show_as_new(self):
        response = self.client.post(f'/api/products/{self.product_id}/designs', json={'design_names': ['Sun', 'Moon']},
                                    headers={**self.headers('manager'), 'Idempotency-Key': 'designs-1'})
        self.assertEqual(response.status_code, 201, response.get_json())
        _, changes = self.line('products.add_product_designs')
        self.assertEqual([(record['op'], record['type']) for record in changes['records']], [('add', 'product')] * 2)
        self.assertLessEqual({('add', '款式 Design', None, 'Sun'), ('add', '款式 Design', None, 'Moon')}, self.fields(changes))
        sun = response.get_json()['product_ids'][0]
        self.client.post(f'/api/products/{sun}/design/edit', json={'design_name': 'Sunny', 'price': 25},
                         headers=self.headers('manager'))
        _, changes = self.line('products.edit_product_design')
        self.assertLessEqual({('edit', '款式 Design', 'Sun', 'Sunny'), ('edit', '价格 Price', '$19.99', '$25.00')},
                             self.fields(changes))
        self.client.post('/api/products/aliases', json={'product_id': self.product_id, 'alias': '测试'},
                         headers=self.headers('manager'))
        _, changes = self.line('products.save_alias')
        self.assertEqual(self.fields(changes), {('edit', '别名 Other names', None, '测试')})

    def test_a_shift_change_and_removal(self):
        made = self.client.post('/api/schedule/shifts', headers=self.headers('admin'), json={
            'employee_id': self.zimo, 'date': '2026-11-20', 'start_time': '12:00', 'end_time': '22:00', 'store_code': 'DT'})
        self.assertEqual(made.status_code, 201, made.get_json())
        shift = made.get_json()['id']
        _, changes = self.line('schedule.schedule_shifts_create')
        self.assertLessEqual({('add', '员工 Person', None, 'Zimo'), ('add', '结束 End', None, '22:00'),
                              ('add', '门店 Store', None, 'DT')}, self.fields(changes))
        changed = self.client.patch(f'/api/schedule/shifts/{shift}', headers=self.headers('admin'),
                                    json={'start_time': '12:00', 'end_time': '18:00', 'notes': ''})
        self.assertEqual(changed.status_code, 200, changed.get_json())
        _, changes = self.line('schedule.schedule_shifts_update')
        self.assertEqual(changes['records'][0]['label'], 'Zimo 2026-11-20')
        self.assertEqual(self.fields(changes), {('edit', '结束 End', '22:00', '18:00')})
        self.client.delete(f'/api/schedule/shifts/{shift}', headers=self.headers('admin'))
        _, changes = self.line('schedule.schedule_shifts_delete')
        self.assertLessEqual({('remove', '开始 Start', '12:00', None), ('remove', '结束 End', '18:00', None)},
                             self.fields(changes))

    def test_an_employee_change_never_keeps_their_pin_token_or_login(self):
        self.client.patch(f'/api/schedule/employees/{self.zimo}', json={'name': 'Zimo L', 'email': 'zimo@example.test'},
                          headers=self.headers('admin'))
        _, changes = self.line('schedule.schedule_employee_patch')
        self.assertEqual(self.fields(changes), {('edit', '名字 Name', 'Zimo', 'Zimo L')})
        with closing(self.connect()) as con:
            kept = ' '.join(row[0] or '' for row in con.execute('SELECT changes FROM activity_log'))
        for secret in ('HASH-1', 'SALT-1', 'TOKEN-1', 'auth0|staff'):
            self.assertNotIn(secret, kept)

    def test_a_new_password_is_kept_as_a_word_only(self):
        fields = activity_changes.secret_fields('users.update_user', {'password': 'hunter2-long', 'role': 'manager'})
        self.assertEqual(fields, [{'label': '角色 Role', 'before': None, 'after': 'manager'},
                                  {'label': '密码 Password', 'before': None, 'after': '已改 Changed'}])
        self.assertNotIn('hunter2', json.dumps(fields))

    def test_settings_keep_only_the_keys_that_changed(self):
        with closing(self.connect()) as con:
            con.executemany('INSERT OR REPLACE INTO app_settings(key, value) VALUES (?, ?)',
                            [('schedule_month_start_day', '4'), ('staff_checkout', 'all')])
            con.commit()
        sent = self.client.put('/api/settings', headers=self.headers('admin'),
                               json={'schedule_month_start_day': '4', 'staff_checkout': 'clover'})
        self.assertEqual(sent.status_code, 200, sent.get_json())
        _, changes = self.line('settings.put_settings')
        self.assertEqual((changes['records'][0]['label'], changes['records'][0]['link']), ('设置 Settings', '/settings'))
        self.assertEqual(self.fields(changes), {('edit', '员工收银 Staff checkout', 'all', 'clover')})

    def test_a_web_order_keeps_its_state_never_its_pickup_code_or_address(self):
        with closing(self.connect()) as con:
            con.execute("""INSERT INTO website_orders(wc_order_id, number, wc_status, state, store_id, lines, pickup_code, delivery)
                           VALUES (501, '1234', 'processing', 'recorded', ?, '[]', '8642', '{"name": "Ann", "address": "1 Main St"}')""",
                        (self.store_id,))
            con.commit()
            before = activity_changes.read(con, 'website.web_orders_step', {'wc_order_id': 501}, {'code': '8642'})
            con.execute("UPDATE website_orders SET wc_status='ready', packed_by='DT 柜台' WHERE wc_order_id=501")
            after = activity_changes.read(con, 'website.web_orders_step', {'wc_order_id': 501}, {'code': '8642'})
        changes = {'records': activity_changes.diff(before, after)}
        self.assertEqual(changes['records'][0]['label'], '#1234')
        self.assertEqual(self.fields(changes), {('edit', '网站状态 Website status', 'processing', 'ready'),
                                                ('edit', '打包 Packed by', None, 'DT 柜台')})
        for secret in ('8642', 'Ann', 'Main St'):
            self.assertNotIn(secret, repr(before) + repr(after))

    def test_a_line_from_before_opens_and_says_nothing_was_kept(self):
        with closing(self.connect()) as con:
            entry_id = con.execute("""INSERT INTO activity_log(business_date, store_id, actor_sub, actor_name, kind, endpoint, summary)
                                      VALUES ('2026-11-25', ?, 'auth0|staff', 'Zimo', 'schedule', 'schedule.schedule_shifts_update',
                                              '改班 Changed a shift · Zimo 2026-11-20')""", (self.store_id,)).lastrowid
            con.commit()
        opened = self.opened(entry_id)
        self.assertIsNone(opened['changes'])
        self.assertEqual((opened['actor_name'], opened['kind_name'], opened['store_code']), ('Zimo', '排班 Schedule', 'DT'))


if __name__ == '__main__':
    unittest.main()
