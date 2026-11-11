"""停用 Retire (owner, 2026-11-11): a product POPCORE refuses to delete for its stock or report history
can be retired by a manager once its stock is 0 everywhere. It then leaves the catalog's default list,
the product searches, 盘点 / 补货 cards, 日报 matching (and any name pick of it) and the stock lists,
while its sales days, report rows and movements stay readable. Un-retiring brings it back."""
from contextlib import closing
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from support import IsolatedApiCase
from matcher import load_report_picks, report_identity_signature, report_match_key
from stock_counting import family_anchors, family_members


def days_ago(days):
    return (datetime.now(ZoneInfo('America/Toronto')).date() - timedelta(days=days)).isoformat()


class ProductRetireTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.grant_inventory_view('auth0|staff', 'auth0|manager')
        with closing(self.connect()) as con:
            self.loc = {code: con.execute('SELECT id FROM inventory_locations WHERE store_id=? AND code=?',
                                          (self.store_id, code)).fetchone()[0]
                        for code in ('floor', 'upstairs', 'display')}
            self.mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            for subject in ('auth0|staff', 'auth0|manager'):
                con.execute('INSERT INTO inventory_access VALUES (?, ?)', (subject, self.store_id))
            # DT runs on the ledger; MK is not live yet.
            con.execute("UPDATE inventory_mode SET mode='authoritative'")
            con.execute('UPDATE inventory_scope_state SET opening_verified=1 WHERE store_id=?', (self.store_id,))
            self.series_id = con.execute("INSERT INTO product_series(name) VALUES ('Planet')").lastrowid
            self.ids = {}
            for name, form, unit, in_series in [('Mars', 'ordinary', 'piece', False), ('Blind', 'random_box', 'box', True),
                                                ('Set', 'sealed_set', 'set', False), ('Red', 'confirmed_design', 'piece', True)]:
                self.ids[name] = con.execute(
                    '''INSERT INTO products(sku,jizhanming,name_cn_en,series_id,stock_form,stock_unit,design_name,identity_status)
                       VALUES (?,?,?,?,?,?,?,'verified')''',
                    ('RT-' + name, name + '星球', name + ' Planet', self.series_id if in_series else None, form, unit,
                     name if form == 'confirmed_design' else None)).lastrowid
            con.execute('UPDATE product_series SET product_id=? WHERE id=?', (self.ids['Blind'], self.series_id))
            con.execute('INSERT INTO product_conversions(source_product_id,target_product_id,output_per_input,version) '
                        'VALUES (?,?,12,1)', (self.ids['Set'], self.ids['Blind']))
            # Mars was counted 1 on display, then 0: a balance row at 0 and one report day of history.
            con.execute("INSERT INTO inventory_balances VALUES (?,?,'saleable',0,2)", (self.ids['Mars'], self.loc['display']))
            con.execute("INSERT INTO inventory_balances VALUES (?,?,'saleable',5,1)", (self.ids['Blind'], self.loc['floor']))
            con.execute("INSERT INTO daily_sales(product_id,date,store,qty_sold,qty_pos,raw_name) VALUES (?,?,'DT',1,1,'Mars星球')",
                        (self.ids['Mars'], days_ago(2)))
            con.commit()

    def retire(self, name, role='manager'):
        return self.client.post(f"/api/products/{self.ids[name]}/retire", headers=self.headers(role))

    def unretire(self, name, role='manager'):
        return self.client.post(f"/api/products/{self.ids[name]}/unretire", headers=self.headers(role))

    def search(self, query='', **params):
        response = self.client.get('/api/products/search', query_string={'q': query, **params},
                                   headers=self.headers('staff'))
        self.assertEqual(response.status_code, 200, response.get_json())
        return [row['id'] for row in response.get_json()]

    def retired_at(self, name):
        with closing(self.connect()) as con:
            return con.execute('SELECT retired_at, retired_by FROM products WHERE id=?', (self.ids[name],)).fetchone()

    def set_balance(self, name, code, quantity, disposition='saleable'):
        with closing(self.connect()) as con:
            con.execute('INSERT OR REPLACE INTO inventory_balances VALUES (?,?,?,?,9)',
                        (self.ids[name], self.loc[code], disposition, quantity))
            con.commit()

    def test_a_product_delete_keeps_for_its_history_is_retired_and_its_history_stays(self):
        delete = self.client.post('/api/products/bulk_delete', json=[self.ids['Mars']], headers=self.headers('manager'))
        self.assertEqual((delete.status_code, delete.get_json()['code']), (409, 'product_history_retained'))
        self.assertIn(self.ids['Mars'], self.search('Mars'))
        history = ['products', 'daily_sales', 'inventory_balances', 'inventory_movements', 'inventory_documents']
        before = self.snapshot(history)

        self.assertEqual(self.retire('Mars', role='staff').status_code, 403)
        response = self.retire('Mars')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.retired_at('Mars')['retired_by'], 'auth0|manager')
        self.assertTrue(response.get_json()['retired_at'])
        self.assertEqual(self.retire('Mars').status_code, 200)  # retiring again changes nothing
        # Only the product's own marker changed: no stock, sales or ledger row was touched.
        after = self.snapshot(history)
        self.assertEqual({table: after[table] for table in history[1:]}, {table: before[table] for table in history[1:]})

        # Gone from the catalog's default list and every search...
        self.assertNotIn(self.ids['Mars'], self.search('Mars'))
        self.assertNotIn(self.ids['Mars'], self.search())
        self.assertNotIn(self.ids['Mars'], self.search('Mars', include_stock='1', store_code='DT'))
        # ...but the catalog lists it when asked, with its badge.
        response = self.client.get('/api/products/search', query_string={'q': 'Mars', 'retired': 'include'},
                                   headers=self.headers('staff'))
        rows = {row['id']: row for row in response.get_json()}
        self.assertTrue(rows[self.ids['Mars']]['retired_at'])
        self.assertEqual(self.search(retired='only'), [self.ids['Mars']])
        matched = self.client.post('/api/products/match', json={'queries': ['Mars星球']}, headers=self.headers('staff'))
        self.assertEqual(matched.get_json()['results'][0]['status'], 'unmatched')
        stock = self.client.get('/api/stock', query_string={'store_code': 'DT', 'include_all': '1'},
                                headers=self.headers('staff')).get_json()
        self.assertNotIn(self.ids['Mars'], [row['id'] for row in stock])

        # Its history is still read with it: the product itself, its report day, and delete still refuses.
        product = self.client.get(f"/api/products/{self.ids['Mars']}", headers=self.headers('staff')).get_json()
        self.assertTrue(product['retired_at'])
        day = self.client.get('/api/sales', query_string={'date': days_ago(2), 'store_code': 'DT'},
                              headers=self.headers('manager'))
        self.assertEqual(day.status_code, 200, day.get_json())
        self.assertEqual([row['product_id'] for row in day.get_json()], [self.ids['Mars']])
        delete = self.client.post('/api/products/bulk_delete', json=[self.ids['Mars']], headers=self.headers('manager'))
        self.assertEqual(delete.status_code, 409)

        self.assertEqual(self.unretire('Mars', role='staff').status_code, 403)
        self.assertEqual(self.unretire('Mars').status_code, 200)
        self.assertIsNone(self.retired_at('Mars')['retired_at'])
        self.assertIn(self.ids['Mars'], self.search('Mars'))

    def test_retiring_needs_the_stock_at_0_at_every_place_and_disposition(self):
        response = self.retire('Blind')
        body = response.get_json()
        self.assertEqual((response.status_code, body['code']), (409, 'product_has_stock'))
        self.assertIn('DT floor 5', body['error'])
        self.assertEqual(body['stock'], [{'store': 'DT', 'location': 'floor', 'disposition': 'saleable', 'quantity': 5}])
        self.assertIsNone(self.retired_at('Blind')['retired_at'])

        self.set_balance('Mars', 'upstairs', 1, 'damaged')
        body = self.retire('Mars').get_json()
        self.assertEqual(body['code'], 'product_has_stock')
        self.assertIn('DT upstairs 1 (damaged)', body['error'])
        self.set_balance('Mars', 'upstairs', 0, 'damaged')
        # A store not on the ledger yet still counts its old stock figures.
        with closing(self.connect()) as con:
            con.execute('INSERT INTO stock(product_id,store_id,upstairs_qty,instore_qty,claw_qty) VALUES (?,?,0,2,0)',
                        (self.ids['Mars'], self.mk))
            con.commit()
        body = self.retire('Mars').get_json()
        self.assertEqual(body['code'], 'product_has_stock')
        self.assertIn('MK floor 2', body['error'])
        with closing(self.connect()) as con:
            con.execute('UPDATE stock SET instore_qty=0 WHERE product_id=? AND store_id=?', (self.ids['Mars'], self.mk))
            con.commit()
        self.assertEqual(self.retire('Mars').status_code, 200)
        self.assertEqual(self.client.post('/api/products/999999/retire', headers=self.headers('manager')).status_code, 404)

    def test_a_sealed_set_or_design_follows_its_own_balances(self):
        # The blind box has 5 on the floor; its design and sealed set hold none, so they retire on their own.
        self.assertEqual(self.retire('Red').status_code, 200)
        self.assertEqual(self.retire('Set').status_code, 200)
        with closing(self.connect()) as con:
            _, _, members = family_members(con, self.ids['Blind'])
        self.assertEqual([role for role, *_ in members], ['box'])  # the 盘点 / 补货 card leaves them out
        # The catalog's detail still lists them, marked.
        detail = self.client.get(f"/api/products/{self.ids['Blind']}", headers=self.headers('staff')).get_json()
        self.assertEqual(sorted(bool(item['retired_at']) for item in detail['sub_items']), [True, True])

        # A box is retired after its design and set, once its own stock is 0.
        self.set_balance('Blind', 'floor', 0)
        self.assertEqual(self.unretire('Red').status_code, 200)
        body = self.retire('Blind').get_json()
        self.assertEqual((body['code'], body['sub_items']), ('sub_items_in_use', ['RT-Red']))
        self.assertEqual(self.retire('Red').status_code, 200)
        self.assertEqual(self.retire('Blind').status_code, 200)
        added = self.client.post(f"/api/products/{self.ids['Blind']}/designs", json={'design_names': ['Blue']},
                                 headers={**self.headers('manager'), 'Idempotency-Key': 'retired-designs'})
        self.assertEqual((added.status_code, added.get_json()['code']), (409, 'product_retired'))
        # A design comes back only under a box in use.
        body = self.unretire('Red').get_json()
        self.assertEqual((body['code'], body['parent_id']), ('parent_retired', self.ids['Blind']))
        self.assertEqual(self.unretire('Set').get_json()['code'], 'parent_retired')
        self.assertEqual(self.unretire('Blind').status_code, 200)
        self.assertEqual(self.unretire('Red').status_code, 200)

    def test_retired_products_leave_count_search_and_report_matching(self):
        with closing(self.connect()) as con:
            self.assertIn(self.ids['Mars'], family_anchors(con, 'Mars'))
            # A manager's name pick of Mars, as the paste review saves it.
            product = dict(con.execute('SELECT * FROM products WHERE id=?', (self.ids['Mars'],)).fetchone())
            key = report_match_key('火星')
            con.execute("""INSERT INTO report_match_choices(name_key, note_key, product_id, identity_signature,
                               raw_name, note, picked_by, picked_at)
                           VALUES (?, ?, ?, ?, '火星', '', 'auth0|manager', datetime('now'))""",
                        (*key, self.ids['Mars'], report_identity_signature(product)))
            con.commit()
            self.assertEqual(load_report_picks(con)[key]['product_ids'], [self.ids['Mars']])
        self.assertIn(self.ids['Mars'], self.parsed_ids('Mars星球*1'))
        self.assertIn(self.ids['Mars'], self.parsed_ids('火星*1'))

        self.assertEqual(self.retire('Mars').status_code, 200)
        with closing(self.connect()) as con:
            self.assertEqual(family_anchors(con, 'Mars'), [])
            self.assertNotIn(key, load_report_picks(con))
        self.assertNotIn(self.ids['Mars'], self.parsed_ids('Mars星球*1'))
        self.assertNotIn(self.ids['Mars'], self.parsed_ids('火星*1'))
        count = self.client.get('/api/counting/search', query_string={'store_code': 'DT', 'location': 'display', 'q': 'Mars'},
                                headers=self.headers('staff'))
        self.assertEqual((count.status_code, count.get_json()['results']), (200, []))

        # A new pick of a retired product is refused.
        pick = self.client.post('/api/sales/name-picks', headers=self.headers('manager'), json={
            'name_key': key[0], 'note_key': key[1], 'product_id': self.ids['Mars'], 'replaces': [self.ids['Mars']]})
        self.assertEqual((pick.status_code, pick.get_json()['code']), (409, 'product_retired'))

        # Un-retired, the pick it had is remembered again.
        self.assertEqual(self.unretire('Mars').status_code, 200)
        with closing(self.connect()) as con:
            self.assertEqual(load_report_picks(con)[key]['product_ids'], [self.ids['Mars']])

    def parsed_ids(self, line):
        response = self.client.post('/api/sales/parse_report', headers=self.headers('manager'), json={
            'text': f'2026.9.14 DT\n随手记汇总：\n{line}', 'store_code': 'DT', 'engine': 'rules'})
        self.assertEqual(response.status_code, 200, response.get_json())
        parsed = response.get_json()
        return ({row['product']['id'] for row in parsed['confirmed']}
                | {candidate['id'] for row in parsed['review'] for candidate in row['candidates']})

    def test_the_stock_overview_and_series_list_leave_retired_products_out(self):
        overview = self.client.get('/api/inventory/overview?store_code=DT', headers=self.headers('staff')).get_json()
        self.assertIn('Mars星球', [row['name'] for row in overview['sold_out']])
        not_set_up = overview['not_set_up']
        listed = self.client.get('/api/inventory/series?store_code=DT', headers=self.headers('staff')).get_json()
        self.assertIn(self.ids['Mars'], [row['id'] for row in listed['products']])

        self.assertEqual(self.retire('Mars').status_code, 200)
        with closing(self.connect()) as con:  # the fixture's untyped TEST-1 is retired too (no ledger stock at DT)
            con.execute("UPDATE products SET retired_at=datetime('now') WHERE id=?", (self.product_id,))
            con.commit()
        overview = self.client.get('/api/inventory/overview?store_code=DT', headers=self.headers('staff')).get_json()
        self.assertNotIn('Mars星球', [row['name'] for row in overview['sold_out']])
        self.assertEqual(overview['not_set_up'], not_set_up - 1)
        listed = self.client.get('/api/inventory/series?store_code=DT', headers=self.headers('staff')).get_json()
        self.assertNotIn(self.ids['Mars'], [row['id'] for row in listed['products']])
        # Asked for by id it still opens, and a retired product holding stock again is never hidden.
        one = self.client.get(f"/api/inventory/series?store_code=DT&product_id={self.ids['Mars']}",
                              headers=self.headers('staff')).get_json()
        self.assertEqual([row['id'] for row in one['products']], [self.ids['Mars']])
        self.set_balance('Mars', 'display', 1)
        listed = self.client.get('/api/inventory/series?store_code=DT', headers=self.headers('staff')).get_json()
        self.assertIn(self.ids['Mars'], [row['id'] for row in listed['products']])
