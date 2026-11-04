"""补货 asks by series (owner, 2026-11-03): search a product, its series card opens, and one save
sets how many 整端 whole sets, 散盒 single boxes and 确认款 designs the sheet asks for."""
from contextlib import closing

from test_receiving import ReceivingFixture


class RestockSeriesTests(ReceivingFixture):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            product = lambda sku, name, form, unit, **extra: con.execute(  # noqa: E731
                """INSERT INTO products(sku, name_cn_en, jizhanming, product_type, stock_form, stock_unit,
                                        identity_status, series_id, design_name, ip_series)
                   VALUES (?, ?, ?, '盲盒', ?, ?, 'verified', ?, ?, 'Twinkle Twinkle')""",
                (sku, name, name, form, unit, extra.get('series'), extra.get('design'))).lastrowid
            self.box = product('PM0535', '星星人毛绒二代', 'random_box', 'box')
            self.set = product('PM0535-SET', '星星人毛绒二代 整端', 'sealed_set', 'set')
            con.execute("""INSERT INTO product_conversions(source_product_id, target_product_id, output_per_input, version)
                           VALUES (?, ?, 6, 1)""", (self.set, self.box))
            series = con.execute("INSERT INTO product_series(name, product_id) VALUES ('星星人毛绒二代', ?)",
                                 (self.box,)).lastrowid
            con.execute('UPDATE products SET series_id=? WHERE id=?', (series, self.box))
            self.moon = product('PM0535-02', '星星人毛绒二代 月亮', 'confirmed_design', 'box', series=series, design='月亮')
            self.star = product('PM0535-01', '星星人毛绒二代 小星星', 'confirmed_design', 'box', series=series,
                                design='小星星')
            # A blind box with no sealed set yet: nothing upstairs is a whole set.
            self.loose = product('PM0555', '星星人毛绒三代', 'random_box', 'box')
            for item, upstairs, floor in ((self.box, 14, 3), (self.set, 2, 0), (self.star, 4, 1)):
                con.execute('INSERT INTO stock(product_id, store_id, upstairs_qty, instore_qty, claw_qty) VALUES (?, ?, ?, ?, 0)',
                            (item, self.store_id, upstairs, floor))
            self.sheet = con.execute("INSERT INTO restock_sessions(date, status, store_id) VALUES ('2026-11-03', 'pending', ?)",
                                     (self.store_id,)).lastrowid
            con.commit()

    def search(self, text, role='staff'):
        return self.client.get(f'/api/restock/session/{self.sheet}/series-search', query_string={'q': text},
                               headers=self.headers(role))

    def card(self, product_id, role='staff'):
        return self.client.get(f'/api/restock/session/{self.sheet}/series', query_string={'product_id': product_id},
                               headers=self.headers(role))

    def save(self, entries, product_id=None):
        return self.client.put(f'/api/restock/session/{self.sheet}/series', headers=self.headers(),
                               json={'product_id': product_id or self.box, 'entries': entries})

    def lines(self):
        with closing(self.connect()) as con:
            return dict(con.execute('SELECT product_id, requested_qty FROM restock_items WHERE session_id=?',
                                    (self.sheet,)).fetchall())

    def test_search_finds_the_series_once_from_any_of_its_parts(self):
        for text in ('星星人毛绒二代', '月亮', 'PM0535-SET'):
            with self.subTest(text=text):
                response = self.search(text)
                self.assertEqual(response.status_code, 200, response.get_json())
                found = [result for result in response.get_json()['results'] if result['product_id'] == self.box]
                self.assertEqual(len(found), 1)
                self.assertEqual((found[0]['sets'], found[0]['designs'], found[0]['kind']), (1, 2, 'box'))
                self.assertEqual(found[0]['back'], {'box': 18, 'set': 2})

    def test_card_lists_whole_sets_then_single_boxes_then_designs(self):
        self.save([{'product_id': self.star, 'requested_qty': 2}])
        response = self.card(self.moon)
        self.assertEqual(response.status_code, 200, response.get_json())
        card = response.get_json()
        self.assertEqual(card['product_id'], self.box)
        self.assertFalse(card['no_whole_set'])
        self.assertEqual([(item['product_id'], item['role']) for item in card['items']],
                         [(self.set, 'set'), (self.box, 'box'), (self.star, 'design'), (self.moon, 'design')])
        by_id = {item['product_id']: item for item in card['items']}
        self.assertEqual(by_id[self.set]['boxes_per_set'], 6)
        self.assertEqual((by_id[self.box]['upstairs_qty'], by_id[self.box]['instore_qty']), (14, 3))
        self.assertEqual(by_id[self.star]['requested_qty'], 2)
        self.assertEqual(by_id[self.moon]['requested_qty'], 0)
        self.assertTrue(self.card(self.loose).get_json()['no_whole_set'])

    def test_one_save_sets_every_part_and_zero_takes_a_part_off(self):
        saved = self.save([{'product_id': self.set, 'requested_qty': 1}, {'product_id': self.box, 'requested_qty': 4},
                           {'product_id': self.star, 'requested_qty': 2}, {'product_id': self.moon, 'requested_qty': 0}])
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(self.lines(), {self.set: 1, self.box: 4, self.star: 2})
        again = self.save([{'product_id': self.set, 'requested_qty': 0}, {'product_id': self.box, 'requested_qty': 6}],
                          product_id=self.star)
        self.assertEqual(again.status_code, 200, again.get_json())
        self.assertEqual(self.lines(), {self.box: 6, self.star: 2})

        sheet = self.client.get(f'/api/restock/session/{self.sheet}', headers=self.headers()).get_json()
        grouped = {item['product_id']: (item['family_id'], item['family_title'], item['role'], item['design_name'])
                   for item in sheet['items']}
        self.assertEqual(grouped, {self.box: (self.box, '星星人毛绒二代', 'box', None),
                                   self.star: (self.box, '星星人毛绒二代', 'design', '小星星')})

    def test_a_save_outside_the_series_or_on_a_sent_sheet_changes_nothing(self):
        self.save([{'product_id': self.box, 'requested_qty': 3}])
        outside = self.save([{'product_id': self.box, 'requested_qty': 5}, {'product_id': self.loose, 'requested_qty': 1}])
        self.assertEqual((outside.status_code, outside.get_json()['code']), (400, 'not_in_series'))
        for entries in ([], [{'product_id': self.box, 'requested_qty': 1000}],
                        [{'product_id': self.box, 'requested_qty': 1}, {'product_id': self.box, 'requested_qty': 2}]):
            with self.subTest(entries=entries):
                self.assertEqual(self.save(entries).status_code, 400)
        with closing(self.connect()) as con:
            con.execute("UPDATE restock_sessions SET status='submitted' WHERE id=?", (self.sheet,))
            con.commit()
        self.assertEqual(self.save([{'product_id': self.box, 'requested_qty': 5}]).status_code, 403)
        self.assertEqual(self.card(self.box).status_code, 403)
        self.assertEqual(self.lines(), {self.box: 3})

    def test_without_inventory_view_the_card_says_only_in_or_out_of_stock(self):
        with closing(self.connect()) as con:
            con.execute("DELETE FROM inventory_viewers WHERE auth0_sub='auth0|staff'")
            con.commit()
        card = self.card(self.box).get_json()
        by_id = {item['product_id']: item for item in card['items']}
        self.assertEqual((by_id[self.box]['upstairs_qty'], by_id[self.box]['instore_qty']), (None, None))
        self.assertEqual((by_id[self.box]['out_of_stock'], by_id[self.moon]['out_of_stock']), (False, True))
        found = next(result for result in self.search('PM0535').get_json()['results'] if result['product_id'] == self.box)
        self.assertEqual((found['back'], found['out_of_stock'], found['stock_hidden']), (None, False, True))

    def test_series_routes_need_store_access(self):
        headers = {'Authorization': 'Bearer staff:outsider'}
        for method, path, body in (
                ('get', f'/api/restock/session/{self.sheet}/series-search?q=PM0535', None),
                ('get', f'/api/restock/session/{self.sheet}/series?product_id={self.box}', None),
                ('put', f'/api/restock/session/{self.sheet}/series',
                 {'product_id': self.box, 'entries': [{'product_id': self.box, 'requested_qty': 1}]})):
            with self.subTest(path=path):
                response = self.client.open(path, method=method.upper(), headers=headers, json=body)
                self.assertEqual(response.status_code, 403, response.get_json())
                self.assertEqual(response.get_json()['code'], 'inventory_forbidden')
        self.assertEqual(self.lines(), {})
