"""待盘点 Count next time (owner, 2026-11-22: "mark the eevee as need to be counted next time we count").

A manager marks a product at places of one store, from a report-stock notice or the product; the mark shows first
at the store's next count (盘点, a section count that starts) and closes when a saved count covers it there."""
from contextlib import closing

from support import IsolatedApiCase

MANAGER = 'auth0|manager'
STAFF = 'auth0|staff'
REASON = '日报 11/20 楼下不够 Not enough on the floor × 1'


class CountMarkTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.dt = con.execute("SELECT id FROM stores WHERE code='DT'").fetchone()[0]
            self.mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            self.loc = {row['code']: row['id'] for row in con.execute(
                'SELECT id, code FROM inventory_locations WHERE store_id=?', (self.dt,))}
            con.execute("UPDATE inventory_mode SET mode='authoritative'")
            con.execute('UPDATE inventory_scope_state SET opening_verified=1 WHERE store_id=?', (self.dt,))
            for subject in (MANAGER, STAFF):
                con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)', (subject, self.dt))
            con.execute("INSERT INTO employees(auth0_id, name) VALUES (?, 'Mia Manager')", (MANAGER,))
            con.execute("INSERT INTO employees(auth0_id, name) VALUES (?, 'Sam Staff')", (STAFF,))
            self.counter = con.execute("INSERT INTO employees(auth0_id, name) VALUES ('auth0|amy', 'Amy')").lastrowid
            # 宝可梦自然: a blind box whose design Eevee is 3 upstairs and none on the floor.
            self.box = self._product(con, 'PK0008', '宝可梦自然', 'random_box', 'box')
            series = con.execute("INSERT INTO product_series(name, product_id, designs_checked_at) "
                                 "VALUES ('自然', ?, datetime('now'))", (self.box,)).lastrowid
            con.execute('UPDATE products SET series_id=? WHERE id=?', (series, self.box))
            self.eevee = self._product(con, 'PK0008-02', '宝可梦自然 Eevee', 'confirmed_design', 'piece',
                                       series=series, design='Eevee')
            self.card = self._product(con, 'PK0001', 'Pikachu card', 'ordinary', 'piece', kind='吊卡')
            self.labubu = self._product(con, 'PM0001', 'Labubu', 'random_box', 'box')
            self.labubu_set = self._product(con, 'PM0001-SET', 'Labubu 整端', 'sealed_set', 'set')
            con.execute("""INSERT INTO product_conversions(source_product_id, target_product_id, output_per_input, version)
                           VALUES (?, ?, 6, 1)""", (self.labubu_set, self.labubu))
            for product, place, quantity in [(self.eevee, 'upstairs', 3), (self.card, 'floor', 4),
                                             (self.labubu, 'floor', 5), (self.labubu_set, 'upstairs', 2)]:
                con.execute("INSERT INTO inventory_balances VALUES (?, ?, 'saleable', ?, 1)",
                            (product, self.loc[place], quantity))
            # The Nov 20 report sold one Eevee the floor did not have (notice #141 on the live app).
            self.notice = con.execute(
                """INSERT INTO report_stock_issues(store_id, business_date, product_id, reason, quantity)
                   VALUES (?, '2026-11-20', ?, 'not_enough_on_floor', 1)""", (self.dt, self.eevee)).lastrowid
            con.commit()
        self.grant_inventory_view(STAFF)

    @staticmethod
    def _product(con, sku, name, form, unit, *, series=None, design=None, kind='盲盒'):
        return con.execute(
            """INSERT INTO products(sku, name_cn_en, jizhanming, product_type, stock_form, stock_unit, identity_status,
                                    series_id, design_name, price)
               VALUES (?, ?, ?, ?, ?, ?, 'verified', ?, ?, 20)""",
            (sku, name, name, kind, form, unit, series, design)).lastrowid

    def get(self, path, role='manager', **params):
        return self.client.get(path, query_string={'store_code': 'DT', **params}, headers=self.headers(role))

    def post(self, path, body=None, role='manager', key=None):
        headers = self.headers(role)
        if key:
            headers['Idempotency-Key'] = key
        return self.client.post(path, json=body or {}, headers=headers)

    def mark(self, product, places, role='manager', reason=REASON, **extra):
        return self.post('/api/counting/marks', {'store_code': 'DT', 'product_id': product, 'locations': places,
                                                 'reason': reason, **extra}, role=role)

    def count(self, place, product, numbers, key, role='staff', section_count_id=None):
        card = self.get('/api/counting/card', role=role, location=place, product_id=product).get_json()
        versions = {item['product_id']: item['balance_version'] for item in card['items']}
        return self.post('/api/counting/counts', {
            'store_code': 'DT', 'location': place, 'product_id': product,
            **({'section_count_id': section_count_id} if section_count_id else {}),
            'entries': [{'product_id': pid, 'quantity': qty, 'balance_version': versions[pid]} for pid, qty in numbers]},
            role=role, key=key)

    def marks(self):
        with closing(self.connect()) as con:
            return {(row['location_id'], row['product_id']): dict(row) for row in con.execute(
                'SELECT * FROM count_marks ORDER BY id')}

    def test_a_manager_marks_the_notice_product_where_it_is_and_the_count_shows_it_first(self):
        marked = self.mark(self.eevee, ['floor', 'upstairs'], notice_id=self.notice)
        self.assertEqual(marked.status_code, 200, marked.get_json())
        self.assertEqual(marked.get_json()['added'], ['floor', 'upstairs'])
        # Marking again keeps the first mark (and its reason).
        again = self.mark(self.eevee, ['upstairs'], reason='something else').get_json()
        self.assertEqual((again['added'], again['already']), ([], ['upstairs']))
        self.assertEqual([mark['reason'] for mark in again['marks']], [REASON, REASON])
        saved = self.marks()[(self.loc['floor'], self.eevee)]
        self.assertEqual((saved['marked_by'], saved['source_type'], saved['source_id'], saved['status']),
                         (MANAGER, 'report_stock_issue', self.notice, 'open'))

        # Whoever counts at the store sees them on 盘点: who marked, why, where.
        listed = self.get('/api/counting/marks', role='staff')
        self.assertEqual(listed.status_code, 200, listed.get_json())
        self.assertEqual([(mark['place'], mark['sku'], mark['design_name'], mark['marked_by']) for mark in listed.get_json()['marks']],
                         [('floor', 'PK0008-02', 'Eevee', 'Mia Manager'), ('upstairs', 'PK0008-02', 'Eevee', 'Mia Manager')])
        scope = self.get('/api/counting/scope', role='staff').get_json()
        self.assertEqual({place['code']: place['marked'] for place in scope['locations'] if place['marked']},
                         {'floor': 1, 'upstairs': 1})
        # The card opened from the mark (a design opens its box's card) says which item is to count.
        card = self.get('/api/counting/card', role='staff', location='floor', product_id=self.eevee).get_json()
        self.assertEqual(card['product_id'], self.box)
        self.assertEqual({item['product_id']: (item.get('mark') or {}).get('reason') for item in card['items']},
                         {self.box: None, self.eevee: REASON})
        # The notice shows the marks; it stays open until a manager checks it.
        notice = next(row for row in self.get('/api/sales/report-stock', role='admin:manager', store_code='ALL').get_json()['notices']
                      if row['id'] == self.notice)
        self.assertEqual([mark['place'] for mark in notice['marks']], ['floor', 'upstairs'])
        self.assertEqual(notice['counted_marks'], [])

    def test_a_saved_count_closes_the_mark_where_it_counted_only(self):
        self.mark(self.eevee, ['floor', 'upstairs'], notice_id=self.notice)
        # Counting something else here, or the box but not Eevee, leaves the mark.
        self.assertEqual(self.count('floor', self.box, [(self.box, 0)], 'count-box').status_code, 200)
        self.assertEqual(self.marks()[(self.loc['floor'], self.eevee)]['status'], 'open')
        saved = self.count('floor', self.box, [(self.eevee, 0)], 'count-eevee-floor')
        self.assertEqual(saved.status_code, 200, saved.get_json())
        marks = self.marks()
        floor, upstairs = marks[(self.loc['floor'], self.eevee)], marks[(self.loc['upstairs'], self.eevee)]
        self.assertEqual((floor['status'], floor['closed_by']), ('counted', STAFF))
        self.assertIsNotNone(floor['count_id'])
        self.assertEqual(upstairs['status'], 'open')
        self.assertEqual([mark['place'] for mark in self.get('/api/counting/marks', role='staff').get_json()['marks']],
                         ['upstairs'])
        # The notice says its floor mark was counted, so the manager can check it.
        notice = next(row for row in self.get('/api/sales/report-stock', role='admin:manager', store_code='DT').get_json()['notices']
                      if row['id'] == self.notice)
        self.assertEqual([(mark['place'], mark['closed_by']) for mark in notice['counted_marks']], [('floor', 'Sam Staff')])
        self.assertEqual([mark['place'] for mark in notice['marks']], ['upstairs'])
        # Marking the floor again opens a new mark; the counted one is kept.
        self.assertEqual(self.mark(self.eevee, ['floor']).get_json()['added'], ['floor'])
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM count_marks WHERE product_id=?', (self.eevee,)).fetchone()[0], 3)

    def test_an_approved_inventory_count_closes_the_mark_too(self):
        self.mark(self.eevee, ['upstairs'])
        created = self.client.post('/api/goods/counts', headers={**self.headers(), 'Idempotency-Key': 'goods-count-1'}, json={
            'location_id': self.loc['upstairs'], 'business_date': '2026-11-22',
            'lines': [{'product_id': self.eevee, 'unit': 'piece', 'observed_quantity': 2}]})
        self.assertEqual(created.status_code, 201, created.get_json())
        count_id = created.get_json()['id']
        self.assertEqual(self.post(f'/api/goods/counts/{count_id}/submit', {'expected_version': 1}, role='staff',
                                   key='goods-submit-1').status_code, 200)
        self.assertEqual(self.marks()[(self.loc['upstairs'], self.eevee)]['status'], 'open')
        approved = self.post(f'/api/goods/counts/{count_id}/approve', {'expected_version': 2, 'reason': 'Recounted'},
                             key='goods-approve-1')
        self.assertEqual(approved.status_code, 200, approved.get_json())
        mark = self.marks()[(self.loc['upstairs'], self.eevee)]
        self.assertEqual((mark['status'], mark['closed_by'], mark['count_id']), ('counted', MANAGER, count_id))

    def test_before_go_live_a_recorded_count_closes_the_mark(self):
        with closing(self.connect()) as con:
            con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)', (MANAGER, self.mk))
            con.commit()
        marked = self.post('/api/counting/marks', {'store_code': 'MK', 'product_id': self.card, 'locations': ['floor'],
                                                   'reason': 'Shelf looked short'})
        self.assertEqual(marked.status_code, 200, marked.get_json())
        saved = self.post('/api/counting/counts', {'store_code': 'MK', 'location': 'floor', 'product_id': self.card,
                                                   'entries': [{'product_id': self.card, 'quantity': 3, 'count_version': None}]},
                          role='admin', key='mk-count')
        self.assertEqual(saved.status_code, 200, saved.get_json())
        with closing(self.connect()) as con:
            row = con.execute('SELECT status, count_id FROM count_marks WHERE store_id=?', (self.mk,)).fetchone()
        self.assertEqual(tuple(row), ('counted', None))

    def test_only_a_manager_with_the_stores_access_marks_and_what_cannot_be_marked(self):
        self.assertEqual(self.mark(self.eevee, ['floor'], role='staff').status_code, 403)
        self.assertEqual(self.mark(self.eevee, ['floor'], role='manager:other').status_code, 403)
        refused = {
            'sealed set on the floor': self.mark(self.labubu_set, ['floor']),
            'no reason': self.mark(self.eevee, ['floor'], reason='  '),
            'no place': self.mark(self.eevee, []),
            'unknown place': self.mark(self.eevee, ['roof']),
            'another product\'s notice': self.mark(self.card, ['floor'], notice_id=self.notice),
        }
        self.assertEqual({why: (response.status_code, response.get_json()['code']) for why, response in refused.items()}, {
            'sealed set on the floor': (400, 'whole_sets_upstairs'), 'no reason': (400, 'reason_required'),
            'no place': (400, 'locations_required'), 'unknown place': (400, 'location_unknown'),
            'another product\'s notice': (400, 'notice_mismatch')})
        self.assertEqual(self.marks(), {})
        # A whole set is marked upstairs, where it is counted.
        self.assertEqual(self.mark(self.labubu_set, ['upstairs']).status_code, 200)

    def test_a_manager_takes_a_mark_off(self):
        mark_id = self.mark(self.card, ['floor']).get_json()['marks'][0]['id']
        self.assertEqual(self.post(f'/api/counting/marks/{mark_id}/remove', role='staff').status_code, 403)
        removed = self.post(f'/api/counting/marks/{mark_id}/remove')
        self.assertEqual(removed.status_code, 200, removed.get_json())
        self.assertEqual(removed.get_json()['marks'], [])
        self.assertEqual(self.post(f'/api/counting/marks/{mark_id}/remove').get_json()['code'], 'mark_closed')
        mark = self.marks()[(self.loc['floor'], self.card)]
        self.assertEqual((mark['status'], mark['closed_by']), ('removed', MANAGER))

    def test_a_section_count_that_starts_takes_every_open_mark_first(self):
        # Two sections: the designs, and everything else.
        layout = self.client.put('/api/counting/sections', headers=self.headers('manager'), json={
            'store_code': 'DT', 'version': 0, 'sections': [
                {'name': '确认款 Designs', 'parts': [{'kind': 'design', 'group_key': '*'}]},
                {'name': '其他 The rest', 'parts': [{'kind': kind, 'group_key': '*'}
                                                   for kind in ('display', 'blind_box', 'other')]}]})
        self.assertEqual(layout.status_code, 200, layout.get_json())
        designs, rest = layout.get_json()['sections']
        # Eevee is a design, so not in 其他; on the floor POPCORE has none of it at all.
        self.mark(self.eevee, ['floor'], notice_id=self.notice)
        self.mark(self.card, ['floor'], reason='Price tag missing, count it')
        self.assertEqual(self.get('/api/counting/sections').get_json()['marks'], 2)
        started = self.post('/api/counting/section-counts',
                            {'store_code': 'DT', 'section_id': rest['id'], 'counter_employee_id': self.counter})
        self.assertEqual(started.status_code, 200, started.get_json())
        view = started.get_json()
        cards = {(place['code'], card['product_id']): card for place in view['places'] for card in place['cards']}
        eevee_card = cards[('floor', self.box)]
        self.assertEqual(eevee_card['marks'], [{'product_id': self.eevee, 'name': 'Eevee', 'reason': REASON, 'counted': False}])
        # The box has nothing on the floor, so the card enters only the marked design.
        self.assertEqual((eevee_card['lines'], eevee_card['roles']), (1, ['design']))
        self.assertEqual([mark['name'] for mark in cards[('floor', self.card)]['marks']], ['Pikachu card'])
        self.assertEqual(cards[('upstairs', self.labubu)]['marks'], [])
        # Entering it closes the mark; the summary shows the line was a 待盘点 one.
        saved = self.count('floor', self.box, [(self.eevee, 0)], 'section-eevee', role='manager',
                           section_count_id=view['id'])
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(self.marks()[(self.loc['floor'], self.eevee)]['status'], 'counted')
        view = self.get(f"/api/counting/section-counts/{view['id']}").get_json()
        card = next(card for place in view['places'] for card in place['cards'] if card['product_id'] == self.box
                    and place['code'] == 'floor')
        self.assertEqual(card['marks'][0]['counted'], True)
        summary = self.get(f"/api/counting/section-counts/{view['id']}/summary").get_json()
        rows = {(row['place'], row['product_id']): row for row in summary['rows']}
        self.assertEqual((rows[('floor', self.eevee)]['marked'], rows[('floor', self.eevee)]['expected'],
                          rows[('floor', self.eevee)]['counted']), (True, 0, 0))
        self.assertFalse(rows[('floor', self.card)]['counted'])
        self.assertTrue(rows[('floor', self.card)]['marked'])
        # The designs section, started later, has no mark left to add but the open one.
        with closing(self.connect()) as con:
            lines = con.execute('SELECT COUNT(*) FROM section_count_lines WHERE mark_id IS NOT NULL').fetchone()[0]
        self.assertEqual(lines, 2)
        other = self.post('/api/counting/section-counts',
                          {'store_code': 'DT', 'section_id': designs['id'], 'counter_employee_id': self.counter}).get_json()
        marked = [card for place in other['places'] for card in place['cards'] if card['marks']]
        self.assertEqual([(card['product_id'], card['marks'][0]['name']) for card in marked], [(self.card, 'Pikachu card')])
