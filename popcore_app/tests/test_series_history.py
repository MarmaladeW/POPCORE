"""Scoped audit history over posted inventory, without changing stock."""
from contextlib import closing
from urllib.parse import urlencode

from support import IsolatedApiCase
from inventory_commands import post_inventory


class SeriesHistoryTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.series_id = con.execute("INSERT INTO product_series(name) VALUES ('Audit Garden')").lastrowid
            self.floor = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'", (self.store_id,)).fetchone()[0]
            self.other_floor = con.execute("SELECT l.id FROM inventory_locations l JOIN stores s ON s.id=l.store_id WHERE s.code='MK' AND l.code='floor'").fetchone()[0]
            con.execute("INSERT INTO inventory_access VALUES ('auth0|staff',?)", (self.store_id,))
            con.execute("INSERT INTO inventory_access SELECT 'auth0|outside',id FROM stores WHERE code='MK'")
            con.execute("UPDATE inventory_mode SET mode='authoritative'")
            con.execute("UPDATE inventory_scope_state SET opening_verified=1")
            self.products = []
            for name in ('Moon', 'Sun'):
                self.products.append(con.execute("""INSERT INTO products(sku,jizhanming,series_id,stock_form,stock_unit,design_name,identity_status)
                    VALUES (?,?,?,'confirmed_design','piece',?,'verified')""", (name,name,self.series_id,name)).lastrowid)
            con.commit()
        self.documents = []
        for i in range(5):
            self.documents.append(self.receive(self.products[i % 2], self.floor, 'staff', f'2026-09-{i+1:02}', i))
        self.outside = self.receive(self.products[0], self.other_floor, 'outside', '2026-09-06', 99)

    def receive(self, product, location, actor, date, key):
        with closing(self.connect()) as con:
            row = con.execute("SELECT version FROM inventory_balances WHERE product_id=? AND location_id=? AND disposition='saleable'", (product,location)).fetchone()
            result = post_inventory(con, {'kind':'receipt','business_date':date,'reason':f'Checked delivery {key}','source_type':'fixture','source_id':str(key),
                'lines':[{'product_id':product,'quantity':1,'unit':'piece','to_location_id':location,'to_disposition':'saleable','expected_versions':{'to':row['version'] if row else 0}}]},
                actor={'sub':'auth0|'+actor,'https://popcore/role':'staff'},request_key='audit-'+str(key))
            return result['document_id']

    def get(self, **params):
        return self.client.get(f'/api/inventory/series/{self.series_id}/history?'+urlencode({'store_code':'DT',**params}),headers=self.headers())

    def test_cursor_pages_are_scoped_and_stable_when_new_movements_arrive(self):
        first = self.get(limit=2).get_json()
        self.assertEqual(len(first['items']),2)
        self.assertTrue(first['has_more'])
        self.assertEqual(first['next_before_id'],first['items'][-1]['id'])
        self.receive(self.products[0],self.floor,'staff','2026-09-07',100)
        second = self.get(limit=2,before_id=first['next_before_id']).get_json()
        last = self.get(limit=2,before_id=second['next_before_id']).get_json()
        items = first['items']+second['items']+last['items']
        self.assertEqual(len(items),5)
        self.assertEqual(len({item['id'] for item in items}),5)
        self.assertEqual([item['document_id'] for item in items],list(reversed(self.documents)))
        self.assertFalse(last['has_more'])
        self.assertIsNone(last['next_before_id'])
        self.assertTrue(all(item['actor_sub']=='auth0|staff' for item in items))
        self.assertEqual(items[0]['reason'],'Checked delivery 4')
        self.assertEqual(items[0]['store_code'],'DT')
        self.assertEqual(items[0]['native_unit'],'piece')

    def test_exact_product_and_inclusive_business_dates(self):
        response = self.get(product_id=self.products[0],date_from='2026-09-02',date_to='2026-09-05')
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual([item['business_date'] for item in response.get_json()['items']],['2026-09-05','2026-09-03'])
        self.assertEqual(self.get(date_from='2026-10-01').get_json()['items'],[])
        for product in (self.product_id,999999):
            self.assertEqual(self.get(product_id=product).status_code,404)

    def test_invalid_filters_and_limit_boundary(self):
        for params in ({'date_from':'2026-02-30'},{'date_to':'invalid'},{'date_from':'2026-09-05','date_to':'2026-09-01'},
                       {'date_from':'20260901'},{'date_from':''},{'before_id':0},{'before_id':-1},{'before_id':'oops'},
                       {'before_id':2**63},{'limit':0},{'limit':'two'},{'product_id':-1}):
            with self.subTest(params=params):
                response = self.get(**params)
                self.assertEqual(response.status_code,400,response.get_json())
        for i in range(101):
            self.receive(self.products[0],self.floor,'staff','2026-09-08',200+i)
        data = self.get(limit=999).get_json()
        self.assertEqual(len(data['items']),100)
        self.assertTrue(data['has_more'])

    def test_partial_store_history_does_not_authorize_whole_document(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access SELECT 'auth0|staff',id FROM stores WHERE code='MK'")
            con.commit()
            lines = []
            for location in (self.floor,self.other_floor):
                version = con.execute("SELECT version FROM inventory_balances WHERE product_id=? AND location_id=? AND disposition='saleable'", (self.products[0],location)).fetchone()[0]
                lines.append({'product_id':self.products[0],'quantity':1,'unit':'piece','to_location_id':location,'to_disposition':'saleable','expected_versions':{'to':version}})
            result = post_inventory(con,{'kind':'receipt','business_date':'2026-09-09','reason':'Shared receipt','lines':lines},
                actor={'sub':'auth0|staff','https://popcore/role':'staff'},request_key='shared-receipt')
            con.execute("DELETE FROM inventory_access WHERE auth0_sub='auth0|staff' AND store_id<>?", (self.store_id,))
            con.commit()
        items = self.get().get_json()['items']
        self.assertEqual(sum(item['document_id']==result['document_id'] for item in items),1)
        response = self.client.get(f"/api/inventory/documents/{result['document_id']}",headers=self.headers())
        self.assertEqual(response.status_code,403)
        self.assertNotIn('lines',response.get_json())

    def test_no_unauthorized_or_inactive_location_leak_and_no_writes(self):
        before = self.snapshot(['inventory_documents','inventory_movements','inventory_balances'])
        data = self.get(store_code='ALL').get_json()
        self.assertEqual(len(data['items']),5)
        self.assertTrue(all(item['location_id']==self.floor for item in data['items']))
        self.assertEqual(self.get(store_code='MK',product_id=self.products[0]).status_code,403)
        self.assertEqual(self.client.get(f'/api/inventory/documents/{self.outside}',headers=self.headers()).status_code,403)
        self.assertEqual(before,self.snapshot(before.keys()))
        with closing(self.connect()) as con:
            con.execute('UPDATE inventory_locations SET is_active=0 WHERE id=?',(self.floor,))
            con.commit()
        self.assertEqual(self.get().get_json()['items'],[])
