from contextlib import closing
from test_receiving import ReceivingFixture


class DesignSearchTests(ReceivingFixture):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.series_id = con.execute("INSERT INTO product_series(name) VALUES ('Moon Garden')").lastrowid
            con.execute("UPDATE products SET series_id=?, stock_form='confirmed_design', design_name='Star Keeper', jizhanming='Old generic name', search_blob='old generic name' WHERE id=?", (self.series_id, self.product_id))
            con.commit()

    def test_search_finds_design_and_reviewed_series_without_rewriting_legacy_names(self):
        for query in ('Star Keeper', 'Moon Garden'):
            response = self.client.get('/api/products/search', query_string={'q':query}, headers=self.headers())
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertTrue(response.get_json(), query)
            match = response.get_json()[0]
            self.assertEqual(match['id'], self.product_id)
            self.assertEqual(match['series_name'], 'Moon Garden')
            self.assertEqual(match['design_name'], 'Star Keeper')
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT jizhanming FROM products WHERE id=?', (self.product_id,)).fetchone()[0], 'Old generic name')

    def test_stock_lookup_preserves_design_identity(self):
        response=self.client.get('/api/stock',query_string={'store_code':'DT','q':'Star Keeper'},headers=self.headers())
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual([row['id'] for row in response.get_json()],[self.product_id])
        self.assertEqual(response.get_json()[0]['design_name'],'Star Keeper')

    def test_sale_snapshot_names_the_exact_design(self):
        from sales_operations import _sale_input
        with closing(self.connect()) as con:
            intent = _sale_input(con, {
                'store_id':self.store_id, 'business_date':'2026-09-30', 'entry_mode':'planned_entry',
                'source':{'system':'manual','account':'DT','reference':'design-test'},
                'lines':[{'product_id':self.product_id,'unit':'piece','quantity':1}],
            }, {'sub':'auth0|staff','https://popcore/role':'staff'})
        self.assertEqual(intent['lines'][0]['product_name_snapshot'], 'Moon Garden · Star Keeper')

    def test_reviewed_design_requires_series_and_cannot_duplicate_a_named_design(self):
        with closing(self.connect()) as con:
            other = con.execute("INSERT INTO products(sku,name_cn_en) VALUES ('OTHER-DESIGN','Other')").lastrowid
            con.commit()
        payload = {'stock_form':'confirmed_design','stock_unit':'piece','design_name':'Star Keeper','identity_status':'verified'}
        missing = self.client.patch(f'/api/products/{other}/inventory-identity',json=payload,headers=self.headers('manager'))
        self.assertEqual(missing.status_code, 400, missing.get_json())
        duplicate = self.client.patch(f'/api/products/{other}',json={**payload,'series_id':self.series_id,'design_name':'  STAR   KEEPER  '},headers=self.headers('manager'))
        self.assertEqual(duplicate.status_code, 409, duplicate.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT identity_status FROM products WHERE id=?',(other,)).fetchone()[0], 'unverified')
