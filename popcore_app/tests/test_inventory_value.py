from contextlib import closing

from support import IsolatedApiCase


class InventoryValueTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.grant_inventory_view('auth0|staff')
        with closing(self.connect()) as con:
            self.loc = {code: con.execute('SELECT id FROM inventory_locations WHERE store_id=? AND code=?', (self.store_id, code)).fetchone()[0]
                        for code in ('floor', 'upstairs')}
            for subject in ('auth0|manager', 'auth0|staff'):
                con.execute('INSERT INTO inventory_access VALUES (?,?)', (subject, self.store_id))
            con.execute("UPDATE inventory_mode SET mode='authoritative'")
            con.execute('UPDATE inventory_scope_state SET opening_verified=1 WHERE store_id=?', (self.store_id,))
            series = con.execute("INSERT INTO product_series(name) VALUES ('小王子')").lastrowid
            self.ids = {}
            for key, sku, name, ip, form, unit, price, in_series in [
                    ('box', 'IV-BOX', '小王子', 'Hirono', 'random_box', 'box', 20.0, False),
                    ('rose', 'IV-ROSE', 'Rose', None, 'confirmed_design', 'piece', 20.0, True),
                    ('dream', 'IV-SA', 'SA dream', 'Smiski & SA', 'random_box', 'box', 10.0, False),
                    ('cheer', 'IV-SM', 'Smiski cheer', 'Smiski & SA', 'random_box', 'box', 15.0, False),
                    ('summer', 'IV-SUM', 'Sa summer', None, 'random_box', 'box', 12.0, False),
                    ('mystery', 'IV-X', 'Mystery cube', None, 'ordinary', 'piece', None, False)]:
                self.ids[key] = con.execute(
                    '''INSERT INTO products(sku,jizhanming,name_cn_en,ip_series,stock_form,stock_unit,price,series_id,design_name,identity_status)
                       VALUES (?,?,?,?,?,?,?,?,?,'verified')''',
                    (sku, name, name, ip, form, unit, price, series if in_series else None,
                     name if form == 'confirmed_design' else None)).lastrowid
            con.execute('UPDATE product_series SET product_id=? WHERE id=?', (self.ids['box'], series))
            for key, code, quantity in [('box', 'floor', 3), ('box', 'upstairs', 2), ('rose', 'upstairs', 1), ('dream', 'floor', 4),
                                        ('cheer', 'floor', 2), ('summer', 'upstairs', 5), ('mystery', 'floor', 7)]:
                con.execute("INSERT INTO inventory_balances VALUES (?,?,'saleable',?,1)", (self.ids[key], self.loc[code], quantity))
            con.commit()

    def get(self, query='store_code=DT', role='manager'):
        return self.client.get('/api/inventory/value?' + query, headers=self.headers(role))

    def test_lines_by_value_with_inherited_split_and_guessed_lines(self):
        response = self.get()
        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()
        lines = {item['name']: (item['value_cents'], item['units'], item['guessed_units'], item['unpriced_units']) for item in data['categories']}
        # Hirono: 5 boxes at $20 and the design that takes its series' line; SA dream and Sa summer (by name) are Sonny Angel.
        self.assertEqual(lines, {'Hirono': (12000, 6, 0, 0), 'Sonny Angel': (10000, 9, 5, 0), 'Smiski': (3000, 2, 0, 0),
                                 '未分类': (0, 7, 0, 7)})
        self.assertEqual([item['name'] for item in data['categories']], ['Hirono', 'Sonny Angel', 'Smiski', '未分类'])
        self.assertEqual(data['total']['value_cents'], 25000)
        self.assertEqual({place['key']: place['value_cents'] for place in data['places']}, {'floor': 13000, 'back': 12000, 'display': 0, 'claw': 0})
        self.assertAlmostEqual(data['categories'][0]['share'], 0.48)

    def test_one_place_and_one_line(self):
        floor = self.get('store_code=DT&place=floor').get_json()
        self.assertEqual({item['name']: item['value_cents'] for item in floor['categories']},
                         {'Hirono': 6000, 'Sonny Angel': 4000, 'Smiski': 3000, '未分类': 0})
        self.assertEqual(floor['shown']['value_cents'], 13000)
        line = self.get('store_code=DT&line=Hirono').get_json()['category']
        self.assertEqual((line['value_cents'], line['units']), (12000, 6))
        self.assertEqual({item['key']: item['value_cents'] for item in line['by_place']}, {'floor': 6000, 'back': 6000})
        # The series' blind box and its design are one part, not two.
        self.assertEqual([(group['name'], len(group['members']), group['link']) for group in line['groups']],
                         [('小王子', 2, {'series_id': line['groups'][0]['link']['series_id']})])
        guessed = self.get('store_code=DT&line=Sonny%20Angel').get_json()['category']
        self.assertEqual({group['name']: [member['guessed'] for member in group['members']] for group in guessed['groups']},
                         {'Sa summer': [True], 'SA dream': [False]})
        self.assertEqual(self.get('store_code=DT&line=Nope').get_json()['category']['units'], 0)

    def test_managers_only_and_bad_place(self):
        self.assertEqual(self.get(role='staff').status_code, 403)
        self.assertEqual(self.get('store_code=DT&place=attic').status_code, 400)
        self.assertEqual(self.get('store_code=MK').status_code, 403)  # no MK store grant
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access SELECT 'auth0|manager', id FROM stores WHERE code='MK'")
            con.commit()
        self.assertEqual(self.get('store_code=MK').get_json(), {'live': False})
