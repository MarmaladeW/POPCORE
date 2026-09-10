from contextlib import closing
from support import IsolatedApiCase

class OperationalReportTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            con.execute("INSERT OR IGNORE INTO inventory_access(auth0_sub,store_id) VALUES ('auth0|staff',?)",(self.store_id,))
            con.execute("INSERT OR IGNORE INTO inventory_access(auth0_sub,store_id) VALUES ('auth0|manager',?)",(self.store_id,))
            sale=con.execute("""INSERT INTO sale_documents(store_id,business_date,status,entry_mode,gross_cents,collected_cents,financial_status,allocation_status,created_by)
                VALUES (?,'2026-09-08','posted','already_paid',3000,3000,'recorded','allocated','auth0|staff')""",(self.store_id,)).lastrowid
            series=con.execute("INSERT INTO product_series(name) VALUES ('Pilot Series')").lastrowid
            con.execute("UPDATE products SET series_id=?,design_name='Pilot Design',identity_status='verified',stock_unit='box' WHERE id=?",(series,self.product_id))
            con.execute("INSERT INTO sale_lines(sale_id,line_no,product_id,product_name_snapshot,native_unit,quantity) VALUES (?,1,?,'Pilot Design','box',2)",(sale,self.product_id))
            cash_payment=con.execute("INSERT INTO sale_payments(sale_id,tender,amount_cents,state,recorded_by) VALUES (?,'cash',1000,'verified','auth0|staff')",(sale,)).lastrowid
            con.execute("INSERT INTO payment_events(payment_id,event_type,direction,amount_cents,reason,actor_sub) VALUES (?,'refund','decrease',200,'Customer refund','auth0|manager')",(cash_payment,))
            con.execute("INSERT INTO sale_payments(sale_id,tender,amount_cents,state,recorded_by) VALUES (?,'card',2000,'recorded','auth0|staff')",(sale,))
            con.commit()
    def test_financial_reports_require_manager_and_scope(self):
        self.assertEqual(self.client.get('/api/reports/tenders?store_code=DT',headers=self.headers('staff')).status_code,403)
        data=self.client.get('/api/reports/tenders?store_code=DT',headers=self.headers('manager')).get_json()
        self.assertEqual({r['tender']:r['recorded_cents'] for r in data['items']},{'card':2000,'cash':1000})
        self.assertEqual({r['tender']:r['refund_cents'] for r in data['items']},{'card':0,'cash':200})
        self.assertEqual(self.client.get('/api/reports/tenders?store_code=MK',headers=self.headers('manager')).status_code,403)
    def test_inventory_report_keeps_native_units_separate(self):
        data=self.client.get('/api/reports/inventory?store_code=DT',headers=self.headers('staff')).get_json()
        self.assertIn('items',data);self.assertNotIn('combined_quantity',data)
    def test_movements_report_uses_ledger_columns(self):
        with closing(self.connect()) as con:
            location=con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'",(self.store_id,)).fetchone()[0]
            set_product=con.execute("INSERT INTO products(sku,name_cn_en,jizhanming,product_type,boxes_per_dan,stock_unit) VALUES ('SET-1','Set','Set','ordinary',1,'set')").lastrowid
            box_product=con.execute("INSERT INTO products(sku,name_cn_en,jizhanming,product_type,boxes_per_dan,stock_unit) VALUES ('BOX-1','Box','Box','ordinary',1,'box')").lastrowid
            document=con.execute("INSERT INTO inventory_documents(kind,request_key,payload_hash,actor_sub,business_date,status) VALUES ('opening','report-movement','hash','auth0|staff','2026-09-08','building')").lastrowid
            con.execute("INSERT INTO inventory_document_lines(document_id,line_no,product_id,native_unit,quantity,to_location_id,to_disposition,to_version) VALUES (?,1,?,'set',1,?,'saleable',0)",(document,set_product,location))
            con.execute("INSERT INTO inventory_movements(document_id,line_no,product_id,location_id,disposition,quantity) VALUES (?,1,?,?,'saleable',-1)",(document,set_product,location))
            con.execute("INSERT INTO inventory_movements(document_id,line_no,product_id,location_id,disposition,quantity) VALUES (?,1,?,?,'saleable',12)",(document,box_product,location));con.commit()
        response=self.client.get('/api/reports/movements?store_code=DT&from=2026-09-08&to=2026-09-08',headers=self.headers('staff'))
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual({row['product_id']:row['native_unit'] for row in response.get_json()['items']},{set_product:'set',box_product:'box'})
        self.assertTrue(all(row['location_id']==location for row in response.get_json()['items']))
    def test_sales_report_groups_verified_product_quantities(self):
        data=self.client.get('/api/reports/sales?store_code=DT&from=2026-09-08&to=2026-09-08',headers=self.headers('manager')).get_json()
        self.assertEqual(data['items'],[{'store_id':self.store_id,'series':'Pilot Series','design':'Pilot Design','unverified_product':None,'product_id':self.product_id,'native_unit':'box','quantity':2,'line_count':1,'identity_complete':1}])
        self.assertEqual(data['known_gross_cents'],3000)
    def test_unknown_report_and_unbounded_page_are_rejected(self):
        self.assertEqual(self.client.get('/api/reports/nope?store_code=DT',headers=self.headers('manager')).status_code,404)
        self.assertEqual(self.client.get('/api/reports/sales?store_code=DT&page_size=1001',headers=self.headers('manager')).status_code,400)
        self.assertEqual(self.client.get('/api/reports/sales?store_code=DT&from=bad',headers=self.headers('manager')).status_code,400)
        self.assertEqual(self.client.get('/api/reports/sales?store_code=DT&from=2026-09-09&to=2026-09-08',headers=self.headers('manager')).status_code,400)
