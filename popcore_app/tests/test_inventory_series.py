from contextlib import closing

from support import IsolatedApiCase
from inventory_commands import post_inventory


class InventorySeriesTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.series_id = con.execute("INSERT INTO product_series(name) VALUES ('Garden')").lastrowid
            self.other_store = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            self.floor = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'", (self.store_id,)).fetchone()[0]
            self.back = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='upstairs'", (self.store_id,)).fetchone()[0]
            self.other_floor = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'", (self.other_store,)).fetchone()[0]
            con.execute("INSERT INTO inventory_access VALUES ('auth0|staff',?)", (self.store_id,))
            con.execute("UPDATE inventory_mode SET mode='authoritative'")
            con.execute("UPDATE inventory_scope_state SET opening_verified=1 WHERE location_id=?", (self.floor,))
            self.products = {}
            for name, form, unit, status in [('Blind','random_box','box','verified'),('Red','confirmed_design','piece','verified'),('Blue','confirmed_design','piece','verified'),('Set','sealed_set','set','verified'),('Unknown','confirmed_design','piece','unverified')]:
                pid = con.execute('''INSERT INTO products(sku,jizhanming,name_cn_en,series_id,stock_form,stock_unit,design_name,identity_status)
                    VALUES (?,?,?,?,?,?,?,?)''', ('GARDEN-'+name,'Garden · '+name,'Garden · '+name,self.series_id,form,unit,name if form=='confirmed_design' else None,status)).lastrowid
                self.products[name] = pid
            for name, disposition, count in [('Blind','saleable',8),('Red','saleable',2),('Red','transit',9),('Red','hold',1),('Set','saleable',1),('Unknown','saleable',90)]:
                con.execute('INSERT INTO inventory_balances VALUES (?,?,?,?,4)', (self.products[name],self.floor,disposition,count))
            con.execute('INSERT INTO inventory_balances VALUES (?,?,?,?,7)', (self.products['Red'],self.other_floor,'saleable',99))
            con.commit()

    def get(self, query='store_code=DT', role='staff'):
        return self.client.get('/api/inventory/series?'+query, headers=self.headers(role))

    def setup_roster(self, body, key='roster-1', role='manager'):
        headers = self.headers(role)
        if key is not None:
            headers['Idempotency-Key'] = key
        return self.client.post('/api/product-series/setup',json=body,headers=headers)

    def test_separate_products_dispositions_and_trusted_zero(self):
        before = self.snapshot(['products','inventory_balances','inventory_documents','stock'])
        response = self.get()
        self.assertEqual(response.status_code,200,response.get_json())
        data = response.get_json()
        self.assertEqual(data['mode'],'authoritative')
        self.assertEqual(data['unassigned_count'],1)
        self.assertEqual({row['id'] for row in data['locations']},{self.floor,self.back})
        rows = {row['design_name'] or row['stock_form']:row for row in data['series'][0]['products']}
        def balance(name, location, disposition='saleable'):
            return next(row for row in rows[name]['balances'] if row['location_id']==location and row['disposition']==disposition)
        self.assertEqual(balance('random_box',self.floor)['quantity'],8)
        self.assertEqual(balance('Red',self.floor)['quantity'],2)
        self.assertEqual(balance('Red',self.floor,'transit')['quantity'],9)
        self.assertEqual(balance('Blue',self.floor),{'location_id':self.floor,'disposition':'saleable','quantity':0,'version':0})
        self.assertIsNone(balance('Red',self.back)['quantity'])
        self.assertIsNone(balance('Unknown',self.floor)['quantity'])
        self.assertEqual({row['disposition'] for row in rows['Red']['balances']},{'saleable','hold','damaged','trade','transit','display'})
        self.assertEqual(before,self.snapshot(before.keys()))

    def test_reference_photo_uses_exact_product_and_prefers_general_image(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO hidden_images(product_id,image_type,filename) VALUES (?,'small','old-package.webp')", (self.products['Red'],))
            con.execute("INSERT INTO hidden_images(product_id,image_type,filename) VALUES (?,'general','red-design.webp')", (self.products['Red'],))
            con.execute("INSERT INTO hidden_images(product_id,image_type,filename) VALUES (?,'general','blue-design.webp')", (self.products['Blue'],))
            con.commit()
        rows = {row['id']:row for row in self.get().get_json()['series'][0]['products']}
        self.assertEqual(rows[self.products['Red']]['image_filename'], 'red-design.webp')
        self.assertEqual(rows[self.products['Blue']]['image_filename'], 'blue-design.webp')
        self.assertIsNone(rows[self.products['Set']]['image_filename'])

    def test_legacy_quantities_unknown_and_design_search_keeps_roster(self):
        with closing(self.connect()) as con:
            con.execute("UPDATE inventory_mode SET mode='legacy'")
            con.commit()
        response = self.get('store_code=DT&q=blue')
        self.assertEqual(response.status_code,200)
        series = response.get_json()['series']
        self.assertEqual(len(series),1)
        self.assertEqual(len(series[0]['products']),5)
        self.assertTrue(all(b['quantity'] is None and b['version'] is None for p in series[0]['products'] for b in p['balances']))
        self.assertEqual(self.get('store_code=DT&q=missing').get_json()['series'],[])
        self.assertEqual(self.get(f'store_code=DT&series_id={self.series_id}').get_json()['series'][0]['id'],self.series_id)

    def test_scope_excludes_ungranted_and_inactive_stores(self):
        response = self.get('store_code=ALL')
        self.assertEqual(response.status_code,200,response.get_json())
        data = response.get_json()
        self.assertEqual({row['store_id'] for row in data['locations']},{self.store_id})
        self.assertEqual(self.get('store_code=MK').status_code,403)
        self.assertEqual(self.get(role='admin').status_code,403)
        self.assertEqual(self.get('store_code=ALL',role='manager').status_code,403)
        for query in ('','store_code=BOGUS','store_code=DT&series_id=no'):
            self.assertEqual(self.get(query).status_code,400)
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access VALUES ('auth0|staff',?)",(self.other_store,))
            con.execute('UPDATE stores SET is_active=0 WHERE id=?',(self.other_store,))
            con.execute('UPDATE inventory_locations SET is_active=0 WHERE id=?',(self.back,))
            con.commit()
        data = self.get('store_code=ALL').get_json()
        self.assertEqual([row['id'] for row in data['locations']],[self.floor])

    def test_history_reads_only_authorized_movements(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access VALUES ('auth0|outside',?)",(self.other_store,))
            con.execute('UPDATE inventory_scope_state SET opening_verified=1 WHERE location_id=?',(self.other_floor,))
            con.commit()
            for location, actor, version in [(self.floor,'staff',4),(self.other_floor,'outside',7)]:
                post_inventory(con,{'kind':'consume','business_date':'2026-09-01','reason':'Fixture sale','source_type':'fixture','source_id':actor,'lines':[{'product_id':self.products['Red'],'quantity':1,'unit':'piece','from_location_id':location,'from_disposition':'saleable','expected_versions':{'from':version}}]},actor={'sub':'auth0|'+actor,'https://popcore/role':'staff'},request_key='history-'+actor)
        response = self.client.get(f'/api/inventory/series/{self.series_id}/history?store_code=ALL',headers=self.headers())
        self.assertEqual(response.status_code,200,response.get_json())
        items = response.get_json()['items']
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['location_id'],self.floor)
        self.assertEqual(items[0]['product_name'],'Garden · Red')
        self.assertEqual(items[0]['quantity'],-1)
        self.assertEqual(items[0]['source_type'],'fixture')
        self.assertEqual(items[0]['reason'],'Fixture sale')
        self.assertEqual(self.client.get(f'/api/inventory/series/{self.series_id}/history?store_code=MK',headers=self.headers()).status_code,403)
        self.assertEqual(self.client.get('/api/inventory/series/99999/history?store_code=DT',headers=self.headers()).status_code,404)

    def test_setup_creates_exact_named_roster_idempotently_without_stock(self):
        before = self.snapshot(['stock','inventory_balances','inventory_documents','inventory_mode','inventory_scope_state'])
        body = {'name':'New Series','design_names':['Red','Blue','Green','Gold','Silver','Black']}
        response = self.setup_roster(body)
        self.assertEqual(response.status_code,201,response.get_json())
        data = response.get_json()
        self.assertEqual(len(data['product_ids']),6)
        self.assertEqual(self.setup_roster(body).get_json(),data)
        self.assertEqual(self.setup_roster({'name':'Other','design_names':['Red']}).status_code,409)
        with closing(self.connect()) as con:
            rows = con.execute('SELECT * FROM products WHERE series_id=? ORDER BY id',(data['id'],)).fetchall()
            self.assertEqual([row['design_name'] for row in rows],body['design_names'])
            self.assertTrue(all(row['stock_form']=='confirmed_design' and row['stock_unit']=='piece' and row['identity_status']=='verified' for row in rows))
            self.assertEqual(rows[0]['jizhanming'],'New Series · Red')
            self.assertEqual(rows[0]['name_cn_en'],'New Series · Red')
            self.assertIn('new series',rows[0]['search_blob'])
            self.assertEqual(len({row['sku'] for row in rows}),6)
            self.assertIsNone(con.execute('SELECT series_id FROM products WHERE id=?',(self.product_id,)).fetchone()[0])
        self.assertEqual(before,self.snapshot(before.keys()))

    def test_setup_existing_series_rejects_normalized_duplicates_atomically(self):
        before = self.snapshot(['products','product_series','operation_requests'])
        response = self.setup_roster({'series_id':self.series_id,'design_names':['New','  RED  ']})
        self.assertEqual(response.status_code,409)
        self.assertEqual(before,self.snapshot(before.keys()))
        for names in (['Ａ','a'],['é','e\u0301'],['Red Hat',' red   HAT ']):
            self.assertEqual(self.setup_roster({'name':'Duplicate','design_names':names}).status_code,400)
        response = self.setup_roster({'series_id':self.series_id,'design_names':[' Extra ']},key='extra')
        self.assertEqual(response.status_code,201,response.get_json())
        self.assertEqual(response.get_json()['id'],self.series_id)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT design_name FROM products WHERE id=?',(response.get_json()['product_ids'][0],)).fetchone()[0],'Extra')

    def test_setup_validation_and_role_boundary(self):
        body = {'name':'New','design_names':['A']}
        self.assertEqual(self.setup_roster(body,role='staff').status_code,403)
        self.assertEqual(self.setup_roster(body,key=None).status_code,400)
        for invalid in ({},{'name':'X','design_names':[]},{'name':'X','design_names':[' ']},{'name':'X','design_names':[1]},{'name':'X','series_id':self.series_id,'design_names':['A']},{'series_id':True,'design_names':['A']},{'name':'X','design_names':['A'],'quantity':9}):
            self.assertEqual(self.setup_roster(invalid).status_code,400,invalid)
        self.assertEqual(self.setup_roster({'name':'Garden','design_names':['A']}).status_code,409)

    def test_setup_rolls_back_if_any_product_insert_fails(self):
        with closing(self.connect()) as con:
            con.execute("CREATE TRIGGER fail_design BEFORE INSERT ON products WHEN NEW.design_name='Bad' BEGIN SELECT RAISE(ABORT,'fixture insert failure'); END")
            con.commit()
        before = self.snapshot(['products','product_series','operation_requests'])
        response = self.setup_roster({'name':'Rollback','design_names':['Good','Bad']})
        self.assertEqual(response.status_code,409)
        self.assertEqual(before,self.snapshot(before.keys()))

    def test_opened_set_choices_are_scoped_and_hide_unreviewed_quantities(self):
        with closing(self.connect()) as con:
            document = con.execute("INSERT INTO inventory_documents(kind,request_key,payload_hash,actor_sub,business_date,status) VALUES ('open_set','fixture-opening','fixture','auth0|staff','2026-09-30','building')").lastrowid
            for location in (self.floor,self.back,self.other_floor):
                con.execute("INSERT INTO inventory_open_sets(opening_document_id,random_product_id,location_id,purpose,remaining_qty) VALUES (?,?,?,'customer_tray',3)",(document,self.products['Blind'],location))
            con.commit()
        products = self.get().get_json()['series'][0]['products']
        choices = next(p for p in products if p['id']==self.products['Blind'])['open_sets']
        self.assertEqual(len(choices),1)
        self.assertEqual(choices[0]['location_id'],self.floor)
        self.assertEqual(choices[0]['remaining_qty'],3)
        with closing(self.connect()) as con:
            con.execute("UPDATE inventory_mode SET mode='legacy'")
            con.commit()
        self.assertTrue(all(p['open_sets']==[] for p in self.get().get_json()['series'][0]['products']))
