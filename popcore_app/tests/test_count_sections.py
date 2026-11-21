"""分区盘点 Section count (owner, 2026-11-20): a live store is counted one section every two weeks;
the manager enters what an employee counted. Sections follow the shop (display, blind boxes,
confirmed designs, other products), split by Dex IP, and together hold every line once."""
from contextlib import closing

from support import IsolatedApiCase

import count_sections

MANAGER = 'auth0|manager'


class SectionCountTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.dt = con.execute("SELECT id FROM stores WHERE code='DT'").fetchone()[0]
            self.loc = {row['code']: row['id'] for row in con.execute(
                'SELECT id, code FROM inventory_locations WHERE store_id=?', (self.dt,))}
            con.execute("UPDATE inventory_mode SET mode='authoritative'")
            con.execute('UPDATE inventory_scope_state SET opening_verified=1 WHERE store_id=?', (self.dt,))
            for subject in (MANAGER, 'auth0|staff'):
                con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)', (subject, self.dt))
            self.counter = con.execute("INSERT INTO employees(auth0_id, name) VALUES ('auth0|amy', 'Amy')").lastrowid
            con.execute("INSERT INTO employees(auth0_id, name) VALUES (?, 'Mia Manager')", (MANAGER,))
            # The Dex: POP MART → THE MONSTERS and MOLLY.
            company = self._dex(con, 'company', 'POP MART')
            self.monsters = self._dex(con, 'ip', 'THE MONSTERS', company)
            self.molly = self._dex(con, 'ip', 'MOLLY', company)
            series = self._dex(con, 'series', 'Have a Seat', self.monsters)
            # Labubu: a blind box with its whole set and two designs; only the box is in the Dex.
            self.labubu = self._product(con, 'PM0001', 'Labubu Seat', 'random_box', 'box', brand='POPMART')
            self.labubu_set = self._product(con, 'PM0001-SET', 'Labubu Seat 整端', 'sealed_set', 'set', brand='POPMART')
            con.execute("""INSERT INTO product_conversions(source_product_id, target_product_id, output_per_input, version)
                           VALUES (?, ?, 6, 1)""", (self.labubu_set, self.labubu))
            seat = con.execute("INSERT INTO product_series(name, product_id) VALUES ('Seat', ?)", (self.labubu,)).lastrowid
            con.execute('UPDATE products SET series_id=? WHERE id=?', (seat, self.labubu))
            self.sisi = self._product(con, 'PM0001-01', 'Labubu Sisi', 'confirmed_design', 'piece', series=seat, design='Sisi')
            self.zimomo = self._product(con, 'PM0001-02', 'Labubu Zimomo', 'confirmed_design', 'piece', series=seat,
                                        design='Zimomo')
            con.execute('UPDATE product_series SET designs_checked_at=datetime(\'now\') WHERE id=?', (seat,))
            self._dex(con, 'product', 'Have a Seat box', series, product=self.labubu)
            # Molly: not in the Dex; its 系列 names the Dex IP.
            self.molly_box = self._product(con, 'PM0002', 'Molly Garden', 'random_box', 'box', brand='Pop Mart', ip='Molly')
            # A hanging card with only a brand, and an untyped blind box spelled the other way.
            self.pokemon = self._product(con, 'PK0001', 'Pikachu card', 'ordinary', 'piece', brand='Pokemon', kind='吊卡')
            self.loose = self._product(con, 'PM0003', 'Mystery 盲盒', None, None, brand='Pop Mart', kind='盲盒')
            for product, place, quantity in [(self.labubu, 'floor', 10), (self.labubu, 'display', 1),
                                             (self.labubu_set, 'upstairs', 3), (self.sisi, 'upstairs', 2),
                                             (self.zimomo, 'upstairs', 1), (self.molly_box, 'floor', 5),
                                             (self.molly_box, 'display', 1), (self.pokemon, 'floor', 4),
                                             (self.loose, 'floor', 2)]:
                con.execute("INSERT INTO inventory_balances VALUES (?, ?, 'saleable', ?, 1)",
                            (product, self.loc[place], quantity))
            con.commit()

    @staticmethod
    def _dex(con, kind, name, parent=None, product=None):
        return con.execute('INSERT INTO dex_entries(kind, name, parent_id, product_id) VALUES (?, ?, ?, ?)',
                           (kind, name, parent, product)).lastrowid

    @staticmethod
    def _product(con, sku, name, form, unit, *, series=None, design=None, brand=None, ip=None, kind='盲盒'):
        return con.execute(
            """INSERT INTO products(sku, name_cn_en, jizhanming, product_type, stock_form, stock_unit, identity_status,
                                    series_id, design_name, brand, ip_series, price)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 20)""",
            (sku, name, name, kind, form, unit, 'verified' if form else 'unverified', series, design, brand, ip)).lastrowid

    def get(self, path, role='manager', **params):
        return self.client.get(path, query_string={'store_code': 'DT', **params}, headers=self.headers(role))

    def put(self, body, role='manager'):
        return self.client.put('/api/counting/sections', json={'store_code': 'DT', **body}, headers=self.headers(role))

    def post(self, path, body=None, role='manager', key=None):
        headers = self.headers(role)
        if key:
            headers['Idempotency-Key'] = key
        return self.client.post(path, json=body or {}, headers=headers)

    def lines(self):
        with closing(self.connect()) as con:
            lines, _ = count_sections._lines(con, self.dt)
        return {(line['product_id'], line['place']): (line['kind'], line['group'], line['group_name']) for line in lines}

    def save_proposal(self):
        proposal = self.get('/api/counting/sections/proposal').get_json()
        response = self.put({'version': 0, 'sections': [{'name': s['name'], 'parts': s['parts']} for s in proposal['sections']]})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def card(self, place, product):
        return self.get('/api/counting/card', location=place, product_id=product).get_json()

    def count(self, place, product, numbers, section_count_id, key):
        card = self.card(place, product)
        versions = {item['product_id']: item['balance_version'] for item in card['items']}
        return self.post('/api/counting/counts', {
            'store_code': 'DT', 'location': place, 'product_id': product, 'section_count_id': section_count_id,
            'entries': [{'product_id': pid, 'quantity': qty, 'balance_version': versions[pid]} for pid, qty in numbers]},
            key=key)

    def test_each_line_has_a_kind_by_place_and_form_and_a_group_from_the_dex_first(self):
        monsters, molly = f'dex:{self.monsters}', f'dex:{self.molly}'
        self.assertEqual(self.lines(), {
            (self.labubu, 'floor'): ('blind_box', monsters, 'THE MONSTERS'),
            (self.labubu, 'display'): ('display', monsters, 'THE MONSTERS'),
            (self.labubu_set, 'upstairs'): ('blind_box', monsters, 'THE MONSTERS'),
            # A design takes its card's IP: the box is in the Dex, the designs are not.
            (self.sisi, 'upstairs'): ('design', monsters, 'THE MONSTERS'),
            (self.zimomo, 'upstairs'): ('design', monsters, 'THE MONSTERS'),
            (self.molly_box, 'floor'): ('blind_box', molly, 'MOLLY'),
            (self.molly_box, 'display'): ('display', molly, 'MOLLY'),
            (self.pokemon, 'floor'): ('other', 'brand:POKEMON', 'Other POKEMON'),
            # Untyped but typed 盲盒: a blind box. "Pop Mart" and "POPMART" are one brand.
            (self.loose, 'floor'): ('blind_box', 'brand:POPMART', 'Other POP MART'),
        })

    def test_the_proposal_holds_every_line_once_in_about_even_sections(self):
        proposal = self.get('/api/counting/sections/proposal').get_json()
        sections = proposal['sections']
        self.assertEqual(proposal['lines'], 9)
        self.assertEqual(len(sections), 6)
        stars = [part['kind'] for section in sections for part in section['parts'] if part['group_key'] == '*']
        self.assertEqual(sorted(stars), sorted(count_sections.KINDS))
        self.assertEqual(sum(section['lines'] for section in sections), 9)
        self.assertEqual([section['name'] for section in sections],
                         ['展示 Display', '盲盒 Blind boxes A', '盲盒 Blind boxes B', '盲盒 Blind boxes C',
                          '确认款 Confirmed designs', '其他 Other products'])
        saved = self.save_proposal()
        self.assertEqual((saved['lines'], saved['outside'], saved['version']), (9, 0, 1))
        self.assertEqual(sum(section['lines'] for section in saved['sections']), 9)

    def test_a_small_kind_joins_the_smallest_section(self):
        lines = ([{'kind': 'blind_box', 'group': f'dex:{n % 12}', 'group_name': f'IP {n % 12}'} for n in range(60)]
                 + [{'kind': 'display', 'group': f'dex:{n % 6}', 'group_name': f'IP {n % 6}'} for n in range(30)]
                 + [{'kind': 'design', 'group': 'dex:1', 'group_name': 'IP 1'}])
        proposal = count_sections.propose(lines)
        self.assertEqual(len(proposal), 6)
        merged = [section for section in proposal if any(part['kind'] == 'design' for part in section['parts'])]
        self.assertEqual(len(merged), 1)
        self.assertIn('+ 确认款 Confirmed designs', merged[0]['name'])
        self.assertEqual(sum(section['lines'] for section in proposal), 91)
        sizes = [section['lines'] for section in proposal]
        self.assertLessEqual(max(sizes) - min(sizes), 1, sizes)

    def test_a_new_ip_falls_into_its_kinds_section_for_new_products(self):
        self.save_proposal()
        with closing(self.connect()) as con:
            skull = self._dex(con, 'ip', 'SKULLPANDA')
            product = self._product(con, 'PM0004', 'Skullpanda box', 'random_box', 'box', brand='POPMART')
            self._dex(con, 'product', 'Skullpanda box', skull, product=product)
            con.execute("INSERT INTO inventory_balances VALUES (?, ?, 'saleable', 4, 1)", (product, self.loc['floor']))
            con.commit()
        overview = self.get('/api/counting/sections').get_json()
        self.assertEqual((overview['lines'], overview['outside']), (10, 0))
        new = [(section['name'], group['name']) for section in overview['sections'] for group in section['groups']
               if group['new']]
        taker = next(section['name'] for section in overview['sections'] if 'blind_box' in section['takes_new'])
        self.assertEqual(new, [(taker, 'SKULLPANDA')])

    def test_sections_need_one_home_for_new_products_of_each_kind_and_a_fresh_version(self):
        missing = self.put({'version': 0, 'sections': [{'name': 'All', 'parts': [{'kind': 'display', 'group_key': '*'}]}]})
        self.assertEqual((missing.status_code, missing.get_json()['code']), (400, 'sections_incomplete'))
        twice = self.put({'version': 0, 'sections': [
            {'name': 'A', 'parts': [{'kind': kind, 'group_key': '*'} for kind in count_sections.KINDS]},
            {'name': 'B', 'parts': [{'kind': 'display', 'group_key': '*'}]}]})
        self.assertEqual(twice.status_code, 400)
        self.save_proposal()
        stale = self.put({'version': 0, 'sections': [
            {'name': 'All', 'parts': [{'kind': kind, 'group_key': '*'} for kind in count_sections.KINDS]}]})
        self.assertEqual((stale.status_code, stale.get_json()['code']), (409, 'sections_changed'))

    def test_only_managers_with_store_access_at_a_live_store(self):
        self.assertEqual(self.get('/api/counting/sections', role='staff').status_code, 403)
        self.assertEqual(self.get('/api/counting/sections', role='manager:other').status_code, 403)
        not_live = self.client.get('/api/counting/sections', query_string={'store_code': 'MK'},
                                   headers=self.headers('admin'))
        self.assertIn(not_live.status_code, (400, 403))
        with closing(self.connect()) as con:
            mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)", (MANAGER, mk))
            con.commit()
        refused = self.client.get('/api/counting/sections', query_string={'store_code': 'MK'}, headers=self.headers('manager'))
        self.assertEqual((refused.status_code, refused.get_json()['code']), (400, 'store_not_live'))

    def test_the_counter_is_picked_from_active_staff_this_stores_first(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO employees(auth0_id, name) VALUES ('auth0|zoe', 'Zoe')")
            con.execute("INSERT INTO employees(auth0_id, name, is_active) VALUES ('auth0|old', 'Old', 0)")
            zoe = con.execute("SELECT id FROM employees WHERE name='Zoe'").fetchone()[0]
            con.execute('INSERT INTO employee_stores(employee_id, store_id) VALUES (?, ?)', (zoe, self.dt))
            con.commit()
        people = self.get('/api/counting/counters').get_json()['employees']
        self.assertEqual([(person['name'], person['here']) for person in people],
                         [('Zoe', True), ('Amy', False), ('Mia Manager', False)])
        self.assertEqual(self.get('/api/counting/counters', role='staff').status_code, 403)

    def _section_holding(self, overview, kind, group_name):
        return next(section for section in overview['sections']
                    if any(group['kind'] == kind and group['name'] == group_name for group in section['groups']))

    def test_a_section_count_lists_its_cards_and_sums_up_what_the_count_changed(self):
        overview = self.save_proposal()
        section = self._section_holding(overview, 'blind_box', 'THE MONSTERS')
        started = self.post('/api/counting/section-counts',
                            {'store_code': 'DT', 'section_id': section['id'], 'counter_employee_id': self.counter})
        self.assertEqual(started.status_code, 200, started.get_json())
        view = started.get_json()
        self.assertEqual((view['counter'], view['section']['name'], view['counted']), ('Amy', section['name'], 0))
        cards = {(place['code'], card['product_id']): card for place in view['places'] for card in place['cards']}
        self.assertEqual(cards[('floor', self.labubu)]['roles'], ['box', 'set'])
        self.assertEqual(cards[('upstairs', self.labubu)]['lines'], 1)  # the whole set; the designs are 确认款
        again = self.post('/api/counting/section-counts',
                          {'store_code': 'DT', 'section_id': section['id'], 'counter_employee_id': self.counter})
        self.assertEqual((again.status_code, again.get_json()['code'], again.get_json()['section_count_id']),
                         (409, 'section_count_open', view['id']))

        short = self.count('floor', self.labubu, [(self.labubu, 8)], view['id'], 'k-floor')
        self.assertEqual(short.status_code, 200, short.get_json())
        same = self.count('upstairs', self.labubu, [(self.labubu_set, 3)], view['id'], 'k-up')
        self.assertEqual(same.status_code, 200, same.get_json())
        # Found on the shelf although POPCORE had none here: it joins this count's list.
        found = self.count('upstairs', self.labubu, [(self.labubu, 2)], view['id'], 'k-found')
        self.assertEqual(found.status_code, 200, found.get_json())
        view = self.get(f"/api/counting/section-counts/{view['id']}").get_json()
        self.assertEqual((view['lines'], view['counted']), (len(cards) + 1, 3))

        done = self.post(f"/api/counting/section-counts/{view['id']}/finish")
        self.assertEqual(done.status_code, 200, done.get_json())
        summary = done.get_json()
        self.assertEqual((summary['counter'], summary['finished_by'], summary['more'], summary['less']),
                         ('Amy', 'Mia Manager', 2, 2))
        first = summary['rows'][0]
        self.assertEqual((first['difference'], first['expected'], first['counted'], first['entered_by']),
                         (-2, 10, 8, 'Mia Manager'))
        rows = {(row['product_id'], row['place']): row for row in summary['rows']}
        self.assertTrue(rows[(self.labubu, 'upstairs')]['added'])
        self.assertEqual(rows[(self.labubu_set, 'upstairs')]['difference'], 0)
        self.assertEqual((summary['lines'], summary['counted']), (3, 3))
        late = self.count('floor', self.labubu, [(self.labubu, 7)], view['id'], 'k-late')
        self.assertEqual((late.status_code, late.get_json()['code']), (409, 'section_count_closed'))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM section_count_saves').fetchone()[0], 3)

    def test_a_count_without_a_section_count_is_as_before(self):
        response = self.count('floor', self.pokemon, [(self.pokemon, 4)], None, 'plain')
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM section_count_saves').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM inventory_counts').fetchone()[0], 1)

    def test_next_up_is_the_section_counted_longest_ago_due_two_weeks_after_the_last(self):
        overview = self.save_proposal()
        first, second = overview['sections'][0], overview['sections'][1]
        self.assertEqual(overview['next_up']['section_id'], first['id'])
        for section, day in [(first, '2026-11-01'), (second, '2026-11-10')]:
            started = self.post('/api/counting/section-counts',
                                {'store_code': 'DT', 'section_id': section['id'], 'counter_employee_id': self.counter})
            summary = self.post(f"/api/counting/section-counts/{started.get_json()['id']}/finish").get_json()
            # Finished with nothing entered: every line is listed as not counted, at its stock when it started.
            self.assertTrue(summary['rows'])
            self.assertEqual({(row['counted'], row['difference']) for row in summary['rows']}, {(None, None)})
            self.assertTrue(all(row['expected'] > 0 for row in summary['rows']))
            with closing(self.connect()) as con:
                con.execute('UPDATE section_counts SET business_date=? WHERE id=?', (day, started.get_json()['id']))
                con.commit()
        overview = self.get('/api/counting/sections').get_json()
        upcoming = overview['next_up']
        self.assertNotIn(upcoming['section_id'], (first['id'], second['id']))
        self.assertIsNone(upcoming['last_counted'])
        self.assertEqual(upcoming['due_on'], '2026-11-24')
        history = self.get('/api/counting/section-counts').get_json()['counts']
        self.assertEqual([row['section_id'] for row in history], [second['id'], first['id']])

    def test_one_section_far_over_the_average_is_marked_uneven(self):
        everything = [{'kind': kind, 'group_key': '*'} for kind in count_sections.KINDS]
        response = self.put({'version': 0, 'sections': [{'name': 'Most', 'parts': everything},
                                                        {'name': 'Molly display', 'parts': [
                                                            {'kind': 'display', 'group_key': f'dex:{self.molly}'}]}]})
        self.assertEqual(response.status_code, 200, response.get_json())
        sections = {section['name']: section for section in response.get_json()['sections']}
        self.assertEqual((sections['Most']['lines'], sections['Molly display']['lines']), (8, 1))
        self.assertTrue(sections['Most']['uneven'])
        self.assertFalse(sections['Molly display']['uneven'])

    def test_a_section_left_out_is_retired_with_its_counts_but_not_while_it_is_counted(self):
        overview = self.save_proposal()
        last = overview['sections'][-1]
        started = self.post('/api/counting/section-counts',
                            {'store_code': 'DT', 'section_id': last['id'], 'counter_employee_id': self.counter}).get_json()
        everything = [{'kind': kind, 'group_key': '*'} for kind in count_sections.KINDS]
        keep_first = {'version': overview['version'],
                      'sections': [{'id': overview['sections'][0]['id'], 'name': 'Everything', 'parts': everything}]}
        refused = self.put(keep_first)
        self.assertEqual((refused.status_code, refused.get_json()['code']), (409, 'section_count_open'))
        self.post(f"/api/counting/section-counts/{started['id']}/finish")
        saved = self.put(keep_first)
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual([section['name'] for section in saved.get_json()['sections']], ['Everything'])
        history = self.get('/api/counting/section-counts').get_json()['counts']
        self.assertEqual([row['section_name'] for row in history], [last['name']])
