from contextlib import closing

from support import IsolatedApiCase


class ProductSubItemTests(IsolatedApiCase):
    """A product (e.g. a blind box) owns its series; sealed set and confirmed designs are its sub-items."""

    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.box = con.execute(
                """INSERT INTO products (sku, jizhanming, name_cn_en, price, brand, product_type)
                   VALUES ('PM0123', '坐坐', 'THE MONSTERS Have a Seat Vinyl Plush Blind Box', 26.99, 'POPMART', '盲盒')"""
            ).lastrowid
            self.plush = con.execute(
                """INSERT INTO products (sku, jizhanming, name_cn_en, price, stock_form, stock_unit, identity_status)
                   VALUES ('PM0200', '大娃', 'Big Plush', 99, 'ordinary', 'piece', 'verified')"""
            ).lastrowid
            con.commit()

    def post(self, path, body, key, role='manager'):
        headers = {**self.headers(role), 'Idempotency-Key': key}
        return self.client.post(path, json=body, headers=headers)

    def product(self, pid):
        return self.client.get(f'/api/products/{pid}', headers=self.headers()).get_json()

    def test_designs_become_numbered_sub_items_of_the_product(self):
        response = self.post(f'/api/products/{self.box}/designs', {'design_names': ['Sisi', 'Zimomo']}, 'd-1')
        self.assertEqual(response.status_code, 201, response.get_json())
        result = response.get_json()
        with closing(self.connect()) as con:
            series = con.execute('SELECT name, product_id FROM product_series WHERE id=?',
                                 (result['series_id'],)).fetchone()
            rows = con.execute('''SELECT sku, jizhanming, name_cn_en, price, stock_form, stock_unit, design_name,
                                         identity_status, series_id FROM products WHERE id IN (?, ?) ORDER BY sku''',
                               result['product_ids']).fetchall()
            box_series = con.execute('SELECT series_id FROM products WHERE id=?', (self.box,)).fetchone()[0]
        self.assertEqual((series['name'], series['product_id'], box_series), ('坐坐', self.box, result['series_id']))
        self.assertEqual([tuple(row) for row in rows], [
            ('PM0123-01', '坐坐 Sisi', 'THE MONSTERS Have a Seat Vinyl Plush Blind Box · Sisi', 26.99,
             'confirmed_design', 'piece', 'Sisi', 'verified', result['series_id']),
            ('PM0123-02', '坐坐 Zimomo', 'THE MONSTERS Have a Seat Vinyl Plush Blind Box · Zimomo', 26.99,
             'confirmed_design', 'piece', 'Zimomo', 'verified', result['series_id']),
        ])
        # Same key and body replays; adding more continues the numbering; a repeated design is refused.
        self.assertEqual(self.post(f'/api/products/{self.box}/designs', {'design_names': ['Sisi', 'Zimomo']},
                                   'd-1').get_json(), result)
        more = self.post(f'/api/products/{self.box}/designs', {'design_names': ['Tycoco']}, 'd-2')
        self.assertEqual(more.status_code, 201)
        self.assertEqual(self.product(more.get_json()['product_ids'][0])['sku'], 'PM0123-03')
        again = self.post(f'/api/products/{self.box}/designs', {'design_names': [' sisi ']}, 'd-3')
        self.assertEqual((again.status_code, again.get_json()['code']), (409, 'design_conflict'))

    def test_product_detail_lists_sub_items_and_each_sub_item_names_its_product(self):
        designs = self.post(f'/api/products/{self.box}/designs', {'design_names': ['Sisi']}, 'd-1').get_json()
        sealed = self.post(f'/api/products/{self.box}/sealed-set', {'boxes_per_set': 6}, 's-1')
        self.assertEqual(sealed.status_code, 201, sealed.get_json())
        detail = self.product(self.box)
        self.assertIsNone(detail['parent'])
        self.assertEqual([(item['sku'], item['stock_form']) for item in detail['sub_items']],
                         [('PM0123-SET', 'sealed_set'), ('PM0123-01', 'confirmed_design')])
        child = self.product(designs['product_ids'][0])
        self.assertEqual((child['parent']['id'], child['sub_items']), (self.box, []))
        self.assertEqual(self.product(self.plush)['sub_items'], [])

    def test_sealed_set_makes_an_untyped_product_a_blind_box(self):
        response = self.post(f'/api/products/{self.box}/sealed-set', {'boxes_per_set': 6}, 's-1')
        self.assertEqual(response.status_code, 201, response.get_json())
        with closing(self.connect()) as con:
            box = con.execute('SELECT stock_form, stock_unit, identity_status, boxes_per_dan FROM products WHERE id=?',
                              (self.box,)).fetchone()
            sealed = con.execute('''SELECT sku, jizhanming, price, stock_form, stock_unit, identity_status
                                    FROM products WHERE id=?''', (response.get_json()['set_product_id'],)).fetchone()
            conversion = con.execute('SELECT target_product_id, output_per_input FROM product_conversions '
                                     'WHERE source_product_id=?', (response.get_json()['set_product_id'],)).fetchone()
        self.assertEqual(tuple(box), ('random_box', 'box', 'verified', 6))
        self.assertEqual(tuple(sealed), ('PM0123-SET', '坐坐 整端', 161.94, 'sealed_set', 'set', 'verified'))
        self.assertEqual(tuple(conversion), (self.box, 6))
        twice = self.post(f'/api/products/{self.box}/sealed-set', {'boxes_per_set': 6}, 's-2')
        self.assertEqual((twice.status_code, twice.get_json()['code']), (409, 'sealed_set_exists'))

    def test_refusals(self):
        not_box = self.post(f'/api/products/{self.plush}/sealed-set', {'boxes_per_set': 6}, 's-1')
        self.assertEqual((not_box.status_code, not_box.get_json()['code']), (409, 'not_blind_box'))
        design = self.post(f'/api/products/{self.box}/designs', {'design_names': ['Sisi']}, 'd-1').get_json()
        nested = self.post(f"/api/products/{design['product_ids'][0]}/designs", {'design_names': ['X']}, 'd-2')
        self.assertEqual((nested.status_code, nested.get_json()['code']), (409, 'sub_item'))
        self.assertEqual(self.post(f'/api/products/{self.box}/designs', {'design_names': []}, 'd-3').status_code, 400)
        self.assertEqual(self.post(f'/api/products/{self.box}/designs', {'design_names': ['A', 'a']}, 'd-4').status_code, 400)
        self.assertEqual(self.post(f'/api/products/{self.box}/sealed-set', {'boxes_per_set': 0}, 's-2').status_code, 400)
        self.assertEqual(self.post('/api/products/99999/designs', {'design_names': ['A']}, 'd-5').status_code, 404)
        self.assertEqual(self.post(f'/api/products/{self.box}/designs', {'design_names': ['A']}, 'd-6',
                                   role='staff').status_code, 403)

    def test_catalog_hides_sub_items_and_counts_them_on_the_product(self):
        self.post(f'/api/products/{self.box}/designs', {'design_names': ['Sisi', 'Zimomo']}, 'd-1')
        top = self.client.get('/api/products/search?top_level=1', headers=self.headers()).get_json()
        box = next(row for row in top if row['id'] == self.box)
        self.assertEqual((box['sub_item_count'], box['parent_product_id']), (2, None))
        self.assertFalse(any(row['sku'].startswith('PM0123-') for row in top))
        # Other searches (checkout, receiving) still find sub-items, best match first.
        everything = self.client.get('/api/products/search?q=sisi', headers=self.headers()).get_json()
        self.assertEqual((everything[0]['sku'], everything[0]['parent_product_id']), ('PM0123-01', self.box))

    def test_inventory_series_shows_the_owning_products_current_name(self):
        result = self.post(f'/api/products/{self.box}/designs', {'design_names': ['Sisi']}, 'd-1').get_json()
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET jizhanming='坐坐盲盒' WHERE id=?", (self.box,))
            con.execute("INSERT INTO inventory_access VALUES ('auth0|staff',?)", (self.store_id,))
            con.commit()
        response = self.client.get(f"/api/inventory/series?store_code=DT&series_id={result['series_id']}",
                                   headers=self.headers())
        series = response.get_json()['series'][0]
        self.assertEqual((series['name'], series['product_id']), ('坐坐盲盒', self.box))
        self.assertEqual({product['id'] for product in series['products']}, {self.box, *result['product_ids']})


class SealedSetVariationTests(ProductSubItemTests):
    """Owner, 2026-10-26: a sealed set is the blind box's variation (like "Whole Set" on the website);
    only confirmed designs make a product a series."""

    def test_a_sealed_set_alone_makes_no_series_but_sits_under_its_box(self):
        made = self.post(f'/api/products/{self.box}/sealed-set', {'boxes_per_set': 6}, 's-1').get_json()
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM product_series').fetchone()[0], 0)
            self.assertIsNone(con.execute('SELECT series_id FROM products WHERE id=?', (self.box,)).fetchone()[0])
        self.assertEqual([i['sku'] for i in self.product(self.box)['sub_items']], ['PM0123-SET'])
        self.assertEqual(self.product(made['set_product_id'])['parent']['id'], self.box)
        catalog = self.client.get('/api/products/search?top_level=1', headers=self.headers()).get_json()
        row = next(p for p in catalog if p['id'] == self.box)
        self.assertEqual(row['sub_item_count'], 1)
        self.assertNotIn(made['set_product_id'], [p['id'] for p in catalog])

    def test_inventory_lists_the_set_as_its_box_variation(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access VALUES ('auth0|staff',?)", (self.store_id,))
            con.commit()
        made = self.post(f'/api/products/{self.box}/sealed-set', {'boxes_per_set': 6}, 's-1').get_json()
        listed = self.client.get('/api/inventory/series?store_code=DT', headers=self.headers()).get_json()
        box = next(p for p in listed['products'] if p['id'] == self.box)
        self.assertEqual([v['id'] for v in box['variations']], [made['set_product_id']])
        self.assertNotIn(made['set_product_id'], [p['id'] for p in listed['products']])
        one = self.client.get(f'/api/inventory/series?store_code=DT&product_id={self.box}', headers=self.headers()).get_json()
        self.assertEqual([v['sku'] for v in one['products'][0]['variations']], ['PM0123-SET'])

    def test_remove_designs_makes_the_product_plain_again_and_keeps_its_set(self):
        designs = self.post(f'/api/products/{self.box}/designs', {'design_names': ['Sisi', 'Zimomo']}, 'd-1').get_json()
        made = self.post(f'/api/products/{self.box}/sealed-set', {'boxes_per_set': 6}, 's-1').get_json()
        staff = self.client.post(f'/api/products/{self.box}/designs/remove', headers=self.headers('staff'))
        self.assertEqual(staff.status_code, 403)
        done = self.client.post(f'/api/products/{self.box}/designs/remove', headers=self.headers('manager'))
        self.assertEqual((done.status_code, done.get_json()['removed_designs']), (200, 2))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM product_series').fetchone()[0], 0)
            self.assertIsNone(con.execute('SELECT series_id FROM products WHERE id=?', (self.box,)).fetchone()[0])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM products WHERE id IN (?,?)', designs['product_ids']).fetchone()[0], 0)
        self.assertEqual([i['sku'] for i in self.product(self.box)['sub_items']], ['PM0123-SET'])
        again = self.client.post(f'/api/products/{self.box}/designs/remove', headers=self.headers('manager'))
        self.assertEqual((again.status_code, again.get_json()['code']), (409, 'no_designs'))

    def test_remove_designs_refuses_when_a_design_has_records(self):
        designs = self.post(f'/api/products/{self.box}/designs', {'design_names': ['Sisi']}, 'd-1').get_json()
        with closing(self.connect()) as con:
            floor = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'", (self.store_id,)).fetchone()[0]
            con.execute('INSERT INTO inventory_balances VALUES (?,?,?,?,1)', (designs['product_ids'][0], floor, 'saleable', 2))
            con.commit()
        refused = self.client.post(f'/api/products/{self.box}/designs/remove', headers=self.headers('manager'))
        self.assertEqual((refused.status_code, refused.get_json()['code']), (409, 'designs_in_use'))
        self.assertEqual(len(self.product(self.box)['sub_items']), 1)
