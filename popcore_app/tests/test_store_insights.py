from contextlib import closing
import json
from unittest.mock import patch

from support import IsolatedApiCase
from blueprints import insights as insight_routes


class StoreOverviewTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        clock=patch('checkout_access.business_date',return_value='2026-10-01')
        clock.start();self.addCleanup(clock.stop)
        with closing(self.connect()) as con:
            employee=con.execute("INSERT INTO employees(auth0_id,name) VALUES ('auth0|manager','Manager')").lastrowid
            con.execute("INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id) VALUES (?,'2026-10-01','09:00','17:00','fixture',?)",(employee,self.store_id))
            con.execute("INSERT INTO inventory_access VALUES ('auth0|manager',?)",(self.store_id,))
            self.floor=con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'",(self.store_id,)).fetchone()[0]
            self.back=con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='upstairs'",(self.store_id,)).fetchone()[0]
            self.other=con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            series=con.execute("INSERT INTO product_series(name) VALUES ('Garden')").lastrowid
            con.execute("UPDATE products SET identity_status='verified',stock_form='confirmed_design',stock_unit='piece',series_id=?,design_name='New label',price=999 WHERE id=?",(series,self.product_id))
            con.execute("UPDATE inventory_mode SET mode='authoritative'")
            con.execute("UPDATE inventory_scope_state SET opening_verified=1 WHERE store_id=?",(self.store_id,))
            con.execute("INSERT INTO inventory_balances VALUES (?,?,'saleable',4,1)",(self.product_id,self.back))
            con.execute("INSERT INTO inventory_balances VALUES (?,?,'hold',1,1)",(self.product_id,self.floor))
            self.sale=con.execute("""INSERT INTO sale_documents(store_id,business_date,status,entry_mode,gross_cents,created_by)
                VALUES (?,'2026-09-01','posted','already_paid',3000,'auth0|manager')""",(self.store_id,)).lastrowid
            con.execute("INSERT INTO sale_lines(sale_id,line_no,product_id,product_name_snapshot,native_unit,quantity) VALUES (?,1,?,'Original name','piece',2)",(self.sale,self.product_id))
            con.execute("""INSERT INTO sale_documents(store_id,business_date,status,entry_mode,gross_cents,created_by)
                VALUES (?,'2026-09-01','posted','already_paid',NULL,'auth0|manager')""",(self.store_id,))
            con.execute("""INSERT INTO sale_documents(store_id,business_date,status,entry_mode,gross_cents,created_by)
                VALUES (?,'2026-09-01','posted','already_paid',99999,'auth0|manager')""",(self.other,))
            for status,sale_id,total in [('completed',self.sale,3000),('open',None,1200),('open',None,None),('cancelled',None,500)]:
                con.execute('INSERT INTO checkout_orders(store_id,business_date,reference,payload,created_by,status,sale_id) VALUES (?,?,?,?,?,?,?)',
                    (self.store_id,'2026-09-01',f'fixture-{status}-{total}',json.dumps({'gross_cents':total}),'auth0|manager',status,sale_id))
            con.execute("INSERT INTO daily_sales(product_id,date,store,qty_sold,raw_name,unit_price) VALUES (?,'2026-09-01','DT',9,'Original raw name',99)",(self.product_id,))
            con.execute("INSERT INTO daily_report_metadata(date,store) VALUES ('2026-09-02','DT')")
            con.commit()

    def get(self,query='store_code=DT&from=2026-09-01&to=2026-09-03',role='manager'):
        return self.client.get('/api/reports/overview?'+query,headers=self.headers(role))

    def test_sources_money_names_and_calendar_coverage_stay_separate(self):
        response=self.get()
        self.assertEqual(response.status_code,200,response.get_json())
        data=response.get_json()
        self.assertEqual(data['posted_sales']['known_gross_cents'],3000)
        self.assertEqual(data['posted_sales']['unknown_gross_count'],1)
        self.assertFalse(data['posted_sales']['gross_complete'])
        self.assertEqual(data['posted_sales']['top_products'][0]['product_name'],'Original name')
        self.assertEqual(data['posted_sales']['top_products'][0]['quantity'],2)
        self.assertEqual([row['date'] for row in data['posted_sales']['daily']],['2026-09-01','2026-09-02','2026-09-03'])
        self.assertIsNone(data['posted_sales']['daily'][1]['known_gross_cents'])
        historical=data['historical_reports']
        self.assertEqual(historical['top_products'][0]['reported_quantity'],9)
        self.assertEqual(historical['top_products'][0]['product_name'],'Garden · New label')
        self.assertEqual(historical['top_products'][0]['raw_names'],['Original raw name'])
        self.assertEqual([row['coverage'] for row in historical['daily']],['report_rows','metadata_only','no_report'])
        self.assertIsNone(historical['daily'][1]['reported_quantity'])
        self.assertNotIn('revenue',historical)
        checkout=data['checkout_activity']
        self.assertEqual((checkout['order_count'],checkout['open_count'],checkout['completed_in_posted_sales']), (4,2,1))
        self.assertEqual((checkout['open_snapshot_gross_cents'],checkout['open_unknown_gross_count']),(1200,1))
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET price=1,jizhanming=\'Changed again\' WHERE id=?',(self.product_id,));con.commit()
        after=self.get().get_json()
        self.assertEqual(after['posted_sales'],data['posted_sales'])

    def test_overview_is_read_only_and_keeps_mixed_native_units_separate(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO sale_lines(sale_id,line_no,product_id,product_name_snapshot,native_unit,quantity) VALUES (?,2,?,'Earlier box identity','box',1)",(self.sale,self.product_id));con.commit()
        before=self.snapshot(('sale_documents','sale_lines','daily_sales','checkout_orders','inventory_balances','insights'))
        response=self.get()
        self.assertEqual(response.status_code,200,response.get_json())
        rows=response.get_json()['posted_sales']['top_products']
        self.assertEqual({row['native_unit']:row['quantity'] for row in rows},{'box':1,'piece':2})
        self.assertEqual(before,self.snapshot(before.keys()))

    def test_inventory_exceptions_are_current_reviewed_and_native(self):
        current=self.get().get_json()['current_inventory']
        self.assertEqual(current['exception_counts'],{'out_of_stock':0,'replenish':1,'condition':1,'unverified':0})
        self.assertEqual(current['items'][0]['native_unit'],'piece')
        self.assertEqual(current['items'][0]['product_name'],'Garden · New label')
        with closing(self.connect()) as con:
            con.execute('UPDATE inventory_scope_state SET opening_verified=0 WHERE location_id=?',(self.back,));con.commit()
        current=self.get().get_json()['current_inventory']
        self.assertEqual(current['unreviewed_location_count'],1)
        self.assertEqual(current['unknown_product_count'],1)
        self.assertEqual(current['exception_counts']['replenish'],0)
        self.assertEqual(current['exception_counts']['out_of_stock'],0)
        with closing(self.connect()) as con:
            con.execute("UPDATE inventory_mode SET mode='legacy'");con.commit()
        self.assertEqual(self.get().get_json()['current_inventory']['exception_counts']['condition'],0)

    def test_confirmed_design_without_series_is_unknown(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET series_id=NULL WHERE id=?',(self.product_id,));con.commit()
        current=self.get().get_json()['current_inventory']
        self.assertEqual(current['exception_counts'],{'out_of_stock':0,'replenish':0,'condition':0,'unverified':1})
        report=self.client.get('/api/reports/inventory?store_code=DT',headers=self.headers('manager')).get_json()
        self.assertTrue(report['items'])
        self.assertTrue(all(row['quantity'] is None and not row['inventory_verified'] for row in report['items']))

    def test_product_rankings_keep_top_twenty_per_native_unit(self):
        with closing(self.connect()) as con:
            for index in range(21):
                con.execute("INSERT INTO sale_lines(sale_id,line_no,raw_product_text,native_unit,quantity) VALUES (?,?,?,'box',?)",
                    (self.sale,index+2,f'Box {index:02}',index+1))
            con.execute("INSERT INTO sale_lines(sale_id,line_no,raw_product_text,native_unit,quantity) VALUES (?,23,'Sealed set','set',1)",(self.sale,));con.commit()
        rows=self.get().get_json()['posted_sales']['top_products']
        self.assertEqual(len(rows),22)
        self.assertEqual(sum(row['native_unit']=='box' for row in rows),20)
        self.assertEqual({row['native_unit'] for row in rows},{'box','piece','set'})
        self.assertNotIn('Box 00',[row['product_name'] for row in rows])
        self.assertIn('Box 20',[row['product_name'] for row in rows])

    def test_manager_and_explicit_grant_scope_before_aggregation(self):
        self.assertEqual(self.get(role='staff').status_code,403)
        self.assertEqual(self.get(role='manager:ungranted').status_code,403)
        self.assertEqual(self.get('store_code=MK&from=2026-09-01&to=2026-09-03').status_code,403)
        data=self.get('store_code=ALL&from=2026-09-01&to=2026-09-03').get_json()
        self.assertEqual(data['stores'],[{'id':self.store_id,'code':'DT'}])
        self.assertEqual(data['posted_sales']['known_gross_cents'],3000)
        for query in ('store_code=DT','store_code=DT&from=2026-09-03&to=2026-09-01','store_code=DT&from=2026-02-30&to=2026-09-03','store_code=DT&from=20260901&to=2026-09-03','store_code=DT&from=2024-01-01&to=2026-09-03'):
            self.assertEqual(self.get(query).status_code,400,query)

    def test_inventory_report_labels_unknown_stock_without_fabricating_zero(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE inventory_scope_state SET opening_verified=0 WHERE location_id=?',(self.back,));con.commit()
        data=self.client.get('/api/reports/inventory?store_code=DT',headers=self.headers('manager')).get_json()
        row=next(item for item in data['items'] if item['location_id']==self.back)
        self.assertEqual(row['product_name'],'Garden · New label')
        self.assertIsNone(row['quantity'])
        self.assertFalse(row['inventory_verified'])

    def test_movement_labels_and_open_set_units_survive_catalog_changes(self):
        from inventory_commands import post_inventory
        with closing(self.connect()) as con:
            series=con.execute('SELECT series_id FROM products WHERE id=?',(self.product_id,)).fetchone()[0]
            source=con.execute("INSERT INTO products(sku,jizhanming,series_id,stock_form,stock_unit,identity_status) VALUES ('SET','Garden set',?,'sealed_set','set','verified')",(series,)).lastrowid
            target=con.execute("INSERT INTO products(sku,jizhanming,series_id,stock_form,stock_unit,identity_status) VALUES ('BOX','Garden box',?,'random_box','box','verified')",(series,)).lastrowid
            conversion=con.execute('INSERT INTO product_conversions(source_product_id,target_product_id,output_per_input,version) VALUES (?,?,9,1)',(source,target)).lastrowid
            con.execute("INSERT INTO inventory_balances VALUES (?,?,'saleable',1,1)",(source,self.floor));con.commit()
            posted=post_inventory(con,{'kind':'open_set','business_date':'2026-09-01','reason':'Review fixture','lines':[
                {'product_id':source,'unit':'set','quantity':1,'from_location_id':self.floor,'to_location_id':self.floor,
                 'from_disposition':'saleable','to_disposition':'saleable','expected_versions':{'from':1,'to':0},
                 'conversion_id':conversion,'conversion_factor':9,'purpose':'customer_tray'}]},
                actor={'sub':'auth0|manager','https://popcore/role':'manager'},request_key='overview-open-set')
            con.execute("UPDATE products SET stock_unit='piece' WHERE id IN (?,?)",(source,target));con.commit()
        report=self.client.get('/api/reports/movements?store_code=DT&from=2026-09-01&to=2026-09-01',headers=self.headers('manager')).get_json()
        movements=[row for row in report['items'] if row['document_id']==posted['document_id']]
        self.assertEqual({row['product_id']:row['native_unit'] for row in movements},{source:'set',target:'box'})
        self.assertEqual({row['location'] for row in movements},{'Floor'})
        self.assertEqual({row['product_name'] for row in movements},{'Garden set','Garden box'})
        history=self.client.get(f'/api/inventory/series/{series}/history?store_code=DT',headers=self.headers('manager')).get_json()
        self.assertEqual({row['product_id']:row['native_unit'] for row in history['items']},{source:'set',target:'box'})

    def test_overview_cannot_bypass_checkout_history_access(self):
        with closing(self.connect()) as con:
            con.execute('DELETE FROM shifts');con.commit()
        self.assertEqual(self.get().status_code,403)


class LegacyInsightAccessTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(insight_routes.bp)
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access VALUES ('auth0|manager',?)",(self.store_id,))
            self.ids={}
            for code in ('DT','MK'):
                self.ids[code]=con.execute("INSERT INTO insights(store,check_type,severity,title,body,generated_at) VALUES (?,'REVENUE_GAP','alert','Fixture revenue','Fixture body',datetime('now'))",(code,)).lastrowid
            con.commit()

    def test_list_count_and_dismiss_share_manager_store_scope(self):
        for path in ('/api/insights','/api/insights/count'):
            self.assertEqual(self.client.get(path,headers=self.headers()).status_code,403)
            self.assertEqual(self.client.get(path+'?store=MK',headers=self.headers('manager')).status_code,403)
        items=self.client.get('/api/insights?store=ALL',headers=self.headers('manager')).get_json()
        self.assertEqual([item['store'] for item in items],['DT'])
        self.assertEqual(self.client.get('/api/insights/count',headers=self.headers('manager')).get_json(),{'count':1})
        self.assertEqual(self.client.post(f"/api/insights/{self.ids['MK']}/dismiss",headers=self.headers('manager')).status_code,403)
        self.assertEqual(self.client.post(f"/api/insights/{self.ids['DT']}/dismiss",headers=self.headers()).status_code,403)
        self.assertEqual(self.client.post(f"/api/insights/{self.ids['DT']}/dismiss",headers=self.headers('manager')).status_code,200)
        with closing(self.connect()) as con:
            self.assertIsNone(con.execute('SELECT dismissed_at FROM insights WHERE id=?',(self.ids['MK'],)).fetchone()[0])
