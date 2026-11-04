from contextlib import closing

from support import IsolatedApiCase


class StockLossTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.loc = {code: con.execute('SELECT id FROM inventory_locations WHERE store_id=? AND code=?',
                                          (self.store_id, code)).fetchone()[0] for code in ('floor', 'upstairs')}
            con.execute("INSERT INTO inventory_access VALUES ('auth0|manager',?)", (self.store_id,))
            con.execute("UPDATE inventory_mode SET mode='authoritative'")
            con.execute('UPDATE inventory_scope_state SET opening_verified=1 WHERE store_id=?', (self.store_id,))
            self.ids = {}
            for name, price in [('Labubu Plush', 39.99), ('Squishy', None)]:
                self.ids[name] = con.execute(
                    '''INSERT INTO products(sku,jizhanming,name_cn_en,price,stock_form,stock_unit,identity_status)
                       VALUES (?,?,?,?,'ordinary','piece','verified')''',
                    ('LS-' + name.split()[0].upper(), name, name, price)).lastrowid
            for name, code, quantity in [('Labubu Plush', 'floor', 5), ('Labubu Plush', 'upstairs', 3), ('Squishy', 'floor', 4)]:
                con.execute("INSERT INTO inventory_balances VALUES (?,?,'saleable',?,1)", (self.ids[name], self.loc[code], quantity))
            con.commit()
        self.keys = 0

    def balance(self, name, code, disposition='saleable'):
        with closing(self.connect()) as con:
            row = con.execute('SELECT quantity, version FROM inventory_balances WHERE product_id=? AND location_id=? AND disposition=?',
                              (self.ids[name], self.loc[code], disposition)).fetchone()
        return (row[0], row[1]) if row else (0, 0)

    def post(self, path, body, role='manager', key=None):
        self.keys += 1
        return self.client.post(path, json=body, headers={**self.headers(role), 'Idempotency-Key': key or f'loss-test-{self.keys}'})

    def record(self, name, code, quantity, reason, note=None, role='manager', key=None, version=None):
        body = {'store_code': 'DT', 'product_id': self.ids[name], 'location_id': self.loc[code], 'quantity': quantity,
                'reason': reason, 'expected_version': self.balance(name, code)[1] if version is None else version}
        if note is not None:
            body['note'] = note
        return self.post('/api/losses', body, role=role, key=key)

    def page(self, query='store_code=DT', role='manager'):
        return self.client.get('/api/losses?' + query, headers=self.headers(role))

    def test_broken_goes_to_the_damaged_pile_and_stolen_leaves_stock(self):
        response = self.record('Labubu Plush', 'floor', 2, 'broken', note='dropped by a customer')
        self.assertEqual(response.status_code, 200, response.get_json())
        loss = response.get_json()['loss']
        self.assertEqual((loss['reason'], loss['quantity'], loss['unit_price_cents'], loss['value_cents']), ('broken', 2, 3999, 7998))
        self.assertEqual(loss['note'], 'dropped by a customer')
        self.assertEqual(self.balance('Labubu Plush', 'floor')[0], 3)
        self.assertEqual(self.balance('Labubu Plush', 'floor', 'damaged')[0], 2)
        self.assertEqual(self.record('Squishy', 'floor', 1, 'stolen').status_code, 200)
        self.assertEqual(self.balance('Squishy', 'floor'), (3, 2))
        self.assertEqual(self.balance('Squishy', 'floor', 'damaged')[0], 0)

        data = self.page().get_json()
        self.assertTrue(data['live'])
        self.assertEqual([(row['name'], row['reason']) for row in data['records']], [('Squishy', 'stolen'), ('Labubu Plush', 'broken')])
        self.assertEqual(data['totals']['broken'], {'units': 2, 'value_cents': 7998, 'unpriced_units': 0, 'records': 1})
        self.assertEqual(data['totals']['stolen'], {'units': 1, 'value_cents': 0, 'unpriced_units': 1, 'records': 1})
        self.assertEqual((data['units'], data['value_cents'], data['unpriced_units']), (3, 7998, 1))
        self.assertEqual([(row['name'], row['location_code'], row['quantity'], row['value_cents']) for row in data['pile']],
                         [('Labubu Plush', 'floor', 2, 7998)])
        with closing(self.connect()) as con:
            documents = con.execute("SELECT kind, source_type, reason FROM inventory_documents WHERE source_type='stock_loss' ORDER BY id").fetchall()
        self.assertEqual([tuple(row) for row in documents],
                         [('move', 'stock_loss', '损耗 破损 Broken: dropped by a customer'), ('consume', 'stock_loss', '损耗 被偷 Stolen')])

    def test_undo_puts_the_stock_back_once(self):
        loss = self.record('Labubu Plush', 'upstairs', 2, 'missing').get_json()['loss']
        self.assertEqual(self.balance('Labubu Plush', 'upstairs')[0], 1)
        undone = self.post(f"/api/losses/{loss['id']}/undo", {})
        self.assertEqual(undone.status_code, 200, undone.get_json())
        self.assertTrue(undone.get_json()['loss']['undone'])
        self.assertEqual(self.balance('Labubu Plush', 'upstairs')[0], 3)
        again = self.post(f"/api/losses/{loss['id']}/undo", {})
        self.assertEqual((again.status_code, again.get_json()['code']), (409, 'already_undone'))
        data = self.page().get_json()
        self.assertEqual((data['units'], data['value_cents']), (0, 0))  # an undone record counts for nothing
        self.assertEqual(self.post('/api/losses/999/undo', {}).status_code, 404)

    def test_broken_undo_needs_the_pile_and_the_pile_can_go_back_or_away(self):
        broken = self.record('Labubu Plush', 'floor', 3, 'broken').get_json()['loss']
        pile = lambda action, quantity, key=None: self.post('/api/losses/pile', {
            'store_code': 'DT', 'product_id': self.ids['Labubu Plush'], 'location_id': self.loc['floor'], 'quantity': quantity,
            'action': action, 'expected_version': self.balance('Labubu Plush', 'floor', 'damaged')[1]}, key=key)
        self.assertEqual(pile('restore', 1).status_code, 200)
        self.assertEqual(self.balance('Labubu Plush', 'floor')[0], 3)
        self.assertEqual(pile('discard', 1).status_code, 200)
        self.assertEqual(self.balance('Labubu Plush', 'floor', 'damaged')[0], 1)
        self.assertEqual(self.balance('Labubu Plush', 'floor')[0], 3)
        too_many = pile('discard', 2)
        self.assertEqual((too_many.status_code, too_many.get_json()['code']), (409, 'insufficient_stock'))
        undo = self.post(f"/api/losses/{broken['id']}/undo", {})
        self.assertEqual((undo.status_code, undo.get_json()['code']), (409, 'pile_changed'))
        self.assertEqual(pile('restore', 1).status_code, 200)
        self.assertEqual(self.page().get_json()['pile'], [])

    def test_refusals(self):
        stale = self.record('Labubu Plush', 'floor', 1, 'stolen', version=0)
        self.assertEqual((stale.status_code, stale.get_json()['code']), (409, 'stale_version'))
        short = self.record('Labubu Plush', 'floor', 6, 'stolen')
        self.assertEqual((short.status_code, short.get_json()['code']), (409, 'insufficient_stock'))
        other = self.record('Labubu Plush', 'floor', 1, 'other')
        self.assertEqual((other.status_code, other.get_json()['code']), (400, 'note_required'))
        self.assertEqual(self.record('Labubu Plush', 'floor', 1, 'lost').status_code, 400)
        # Managers and admins only (owner, 2026-11-03), and only with the store's operations access.
        self.assertEqual(self.record('Labubu Plush', 'floor', 1, 'stolen', role='staff').status_code, 403)
        self.assertEqual(self.page(role='staff').status_code, 403)
        self.assertEqual(self.record('Labubu Plush', 'floor', 1, 'stolen', role='manager:other').status_code, 403)
        self.assertEqual(self.page('store_code=MK').get_json()['live'], False)
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access SELECT 'auth0|manager',id FROM stores WHERE code='MK'")
            con.commit()
        not_live = self.post('/api/losses', {'store_code': 'MK', 'product_id': self.ids['Squishy'], 'location_id': self.loc['floor'],
                                             'quantity': 1, 'reason': 'stolen', 'expected_version': 1})
        self.assertEqual((not_live.status_code, not_live.get_json()['code']), (409, 'inventory_legacy_mode'))
        self.assertEqual(self.balance('Labubu Plush', 'floor'), (5, 1))

    def test_the_same_request_key_records_once(self):
        first = self.record('Squishy', 'floor', 1, 'stolen', key='same-key')
        second = self.record('Squishy', 'floor', 1, 'stolen', key='same-key', version=1)
        self.assertEqual(first.get_json(), second.get_json())
        self.assertEqual(self.balance('Squishy', 'floor')[0], 3)
        self.assertEqual(len(self.page().get_json()['records']), 1)

    def test_search_finds_products_with_stock_and_where(self):
        response = self.client.get('/api/losses/search?store_code=DT&q=labubu', headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        results = response.get_json()['results']
        self.assertEqual([(row['name'], row['price_cents']) for row in results], [('Labubu Plush', 3999)])
        self.assertEqual([(place['code'], place['available']) for place in results[0]['places']], [('floor', 5), ('upstairs', 3)])
        self.assertEqual(self.client.get('/api/losses/search?store_code=DT&q=LS-SQUISHY', headers=self.headers('manager'))
                         .get_json()['results'][0]['name'], 'Squishy')

    def test_the_overview_counts_value_for_managers_and_shows_losses(self):
        self.grant_inventory_view('auth0|staff')
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access VALUES ('auth0|staff',?)", (self.store_id,))
            con.commit()
        self.record('Labubu Plush', 'floor', 1, 'broken')
        manager = self.client.get('/api/inventory/overview?store_code=DT', headers=self.headers('manager')).get_json()
        # 7 Labubu at 39.99 are for sale (the broken one is on the damaged pile); the 4 squishies have no price.
        self.assertEqual(manager['value'], {'value_cents': 27993, 'unpriced_units': 4, 'unpriced_products': 1})
        # Each place's worth adds up to the total: 4 Labubu on the floor (+4 unpriced squishies), 3 upstairs.
        self.assertEqual({figure['key']: (figure['value_cents'], figure['unpriced_units']) for figure in manager['figures']},
                         {'floor': (15996, 4), 'back': (11997, 0), 'display': (0, 0), 'claw': (0, 0)})
        self.assertEqual([(row['kind'], row['saleable']) for row in manager['recent_changes']], [('loss', -1)])
        staff = self.client.get('/api/inventory/overview?store_code=DT', headers=self.headers('staff')).get_json()
        self.assertIsNone(staff['value'])
        self.assertEqual({figure['value_cents'] for figure in staff['figures']}, {None})

    def test_a_store_code_that_is_not_text_is_refused(self):
        response = self.post('/api/losses', {'store_code': 1, 'product_id': self.ids['Squishy'], 'location_id': self.loc['floor'],
                                             'quantity': 1, 'reason': 'stolen', 'expected_version': 1})
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.record('Squishy', 'floor', 1, 'stolen').get_json()['loss']['actor_name'])  # no employee row
