"""Historical name corrections preserve saved facts and never post inventory."""
import csv
import io
import json
import sqlite3
from contextlib import closing
from support import IsolatedApiCase
import db


class SalesHistoryMatchingTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access(auth0_sub,store_id) VALUES ('auth0|manager',?)", (self.store_id,))
            con.execute("UPDATE products SET price=20,stock_unit='piece',stock_form='ordinary',identity_status='verified' WHERE id=?", (self.product_id,))
            self.target = con.execute("INSERT INTO products(sku,name_cn_en,jizhanming,price,stock_unit,stock_form,identity_status) VALUES ('TARGET','Correct design','Correct design',90,'piece','ordinary','verified')").lastrowid
            self.record = con.execute("""INSERT INTO daily_sales(product_id,date,store,qty_pos,qty_cash,qty_claw,qty_display,qty_employee,qty_sold,raw_name,notes,unit_price)
                VALUES (?,'2026-09-01','DT',2,3,1,2,1,5,'Old label；Second label','Original note',12.5)""", (self.product_id,)).lastrowid
            con.commit()

    def preview(self, **params):
        response = self.client.get('/api/sales/history-matches',headers=self.headers('manager'),query_string={
            'store_code':'DT','date_from':'2026-09-01','date_to':'2026-09-30','target_product_id':self.target,**params})
        self.assertEqual(response.status_code,200,response.get_json())
        return response.get_json()

    def intent(self):
        row = self.preview(record_id=self.record)['items'][0]
        target = next(p for p in row['candidates'] if p['id']==self.target)
        return {'store_code':'DT','product_id':self.target,'row_token':row['row_token'],
                'target_identity_token':target['target_identity_token'],'reason':'Reviewed original receipt: all names on this row are this design'}

    def remap(self, body=None, key='history-remap', role='manager'):
        return self.client.post(f'/api/sales/record/{self.record}/remap',headers={**self.headers(role),'Idempotency-Key':key},json=body or self.intent())

    def row(self):
        with closing(self.connect()) as con:
            return dict(con.execute('SELECT * FROM daily_sales WHERE id=?',(self.record,)).fetchone())

    def test_scoped_paginated_preview_does_not_mutate_or_confirm_candidates(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO daily_sales(product_id,date,store,qty_sold,raw_name) VALUES (?,'2026-09-02','MK',8,'Outside scope')",(self.product_id,));con.commit()
        before=self.snapshot(('daily_sales','product_aliases','report_match_choices','stock','inventory_documents'))
        data=self.preview(store_code='ALL',page_size=1)
        self.assertEqual((data['total_rows'],data['page'],data['page_size']),(1,1,1))
        row=data['items'][0]
        self.assertEqual((row['id'],row['raw_name'],row['unit_price']),(self.record,'Old label；Second label',12.5))
        self.assertEqual(row['candidates'][-1]['reason'],'Selected for review')
        self.assertEqual(row['current_product']['stock_unit'],'piece')
        self.assertIsNone(row['last_review'])
        self.assertEqual(self.snapshot(before),before)
        for role in ('staff','viewer','manager:outsider'):
            response=self.client.get('/api/sales/history-matches',headers=self.headers(role),query_string={'store_code':'DT'})
            self.assertEqual(response.status_code,403)
        self.assertEqual(self.preview(record_id=self.record,q='does not exist')['total_rows'],0)

    def test_pagination_rejects_offset_beyond_sqlite_range(self):
        response = self.client.get('/api/sales/history-matches', headers=self.headers('manager'),
            query_string={'store_code': 'DT', 'page': str(2**63 - 1), 'page_size': '100'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'invalid_input')

    def test_remap_preserves_every_saved_fact_and_replays_once_with_immutable_audit(self):
        body=self.intent(); before=self.row()
        protected=self.snapshot(('stock','stock_transactions','inventory_documents','inventory_movements','product_aliases','report_match_choices','match_corrections'))
        first=self.remap(body); self.assertEqual(first.status_code,200,first.get_json())
        self.assertEqual(self.row(),{**before,'product_id':self.target})
        self.assertEqual(self.remap(body).get_json(),first.get_json())
        self.assertEqual(self.snapshot(protected),protected)
        with closing(self.connect()) as con:
            audit=dict(con.execute('SELECT * FROM daily_sales_match_audits').fetchone())
            self.assertEqual(con.execute('SELECT COUNT(*) FROM daily_sales_match_audits').fetchone()[0],1)
            self.assertEqual(json.loads(audit['before_data'])['row'],before)
            self.assertEqual(json.loads(audit['after_data'])['row'],self.row())
            self.assertEqual(audit['reason'],body['reason'])
            for sql in ('UPDATE daily_sales_match_audits SET reason=\'changed\'', 'DELETE FROM daily_sales_match_audits'):
                with self.assertRaises(sqlite3.IntegrityError): con.execute(sql)
                con.rollback()
        db.migrate_db(); db.migrate_db()
        self.assertEqual(self.row(),{**before,'product_id':self.target})
        review=self.preview(record_id=self.record)['items'][0]['last_review']
        self.assertEqual((review['from_product_id'],review['to_product_id'],review['actor_sub']),(self.product_id,self.target,'auth0|manager'))
        self.assertEqual(self.remap({**body,'reason':'different'}).status_code,409)
        with closing(self.connect()) as con:
            con.execute("DELETE FROM inventory_access WHERE auth0_sub='auth0|manager'");con.commit()
        self.assertEqual(self.remap(body).status_code,403)

    def test_stale_row_and_target_identity_do_not_apply(self):
        body=self.intent()
        with closing(self.connect()) as con:
            con.execute('UPDATE daily_sales SET qty_cash=4 WHERE id=?',(self.record,));con.commit()
        self.assertEqual(self.remap(body).status_code,409)
        body=self.intent()
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET name_cn_en='Replaced identity' WHERE id=?",(self.target,));con.commit()
        self.assertEqual(self.remap(body).status_code,409)
        self.assertEqual(self.row()['product_id'],self.product_id)

    def test_target_collision_known_unit_mismatch_and_stock_history_block(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO daily_sales(product_id,date,store,qty_sold) VALUES (?,'2026-09-01','DT',1)",(self.target,));con.commit()
        self.assertEqual(self.remap().get_json()['code'],'target_row_exists')
        with closing(self.connect()) as con:
            con.execute('DELETE FROM daily_sales WHERE product_id=?',(self.target,))
            con.execute("UPDATE products SET stock_unit='set',stock_form='sealed_set' WHERE id=?",(self.target,));con.commit()
        self.assertEqual(self.remap().get_json()['code'],'unit_mismatch')
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET stock_unit='piece',stock_form='ordinary' WHERE id=?",(self.target,))
            con.execute("INSERT INTO stock_transactions(product_id,store_id,txn_type,qty,location,date) VALUES (?,?,'report_stock_in',2,'upstairs','2026-09-01')",(self.product_id,self.store_id));con.commit()
        self.assertEqual(self.preview()['items'][0]['blocked_code'],'reconciliation_required')
        self.assertEqual(self.remap().get_json()['code'],'reconciliation_required')
        self.assertEqual(self.row()['product_id'],self.product_id)

    def test_late_audit_failure_rolls_back_mapping_and_request(self):
        body=self.intent(); before=self.snapshot(('daily_sales','operation_requests','daily_sales_match_audits'))
        with closing(self.connect()) as con:
            con.execute("CREATE TRIGGER fail_history_audit BEFORE INSERT ON daily_sales_match_audits BEGIN SELECT RAISE(ABORT,'injected audit failure'); END");con.commit()
        with self.assertRaises(sqlite3.IntegrityError): self.remap(body)
        self.assertEqual(self.snapshot(before),before)

    def test_null_price_stays_unknown_in_read_export_and_after_remap(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE daily_sales SET unit_price=NULL WHERE id=?',(self.record,));con.commit()
        path='/api/sales?store_code=DT&date=2026-09-01'
        self.assertIsNone(self.client.get(path,headers=self.headers()).get_json()[0]['price'])
        self.assertEqual(self.remap().status_code,200)
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET price=999 WHERE id=?',(self.target,));con.commit()
        row=self.client.get(path,headers=self.headers()).get_json()[0]
        self.assertIsNone(row['price']); self.assertIsNone(row['unit_price'])
        export=self.client.get('/api/sales/export?store_code=DT&from=2026-09-01&to=2026-09-01',headers=self.headers('manager'))
        self.assertEqual(list(csv.reader(io.StringIO(export.get_data(as_text=True))))[1][5],'')

    def test_explicit_type_variant_and_packaging_notes_require_review(self):
        for note in ('plush S3','sealed set','single box','100%','隐藏','Molly secret'):
            response=self.client.post('/api/sales/parse_report',headers=self.headers(),json={'store_code':'DT','engine':'rules','text':f'卡机汇总:\nTest Product*1 ({note})'})
            self.assertFalse(response.get_json()['confirmed'],note)
        paid=self.client.post('/api/sales/parse_report',headers=self.headers(),json={'store_code':'DT','engine':'rules','text':'卡机汇总:\nTest Product*1 (cash30+card9)'})
        self.assertEqual(len(paid.get_json()['confirmed']),1)

    def test_explicit_confirmed_design_notes_do_not_auto_confirm_bare_alias(self):
        from matcher import clean_name, normalize
        with closing(self.connect()) as con:
            con.execute('INSERT INTO product_aliases(product_id,alias,alias_norm) VALUES (?,?,?)',
                (self.product_id,'Garden',normalize(clean_name('Garden'))))
            con.commit()
        for note,should_confirm in (('confirmed Moon',False),('指定款 Moon',False),('明盒 Moon',False),
                                    ('',True),('cash30+card9',True)):
            with self.subTest(note=note):
                suffix=f' ({note})' if note else ''
                response=self.client.post('/api/sales/parse_report',headers=self.headers(),json={
                    'store_code':'DT','engine':'rules','text':f'卡机汇总:\nGarden*1{suffix}'})
                self.assertEqual(response.status_code,200,response.get_json())
                parsed=response.get_json()
                self.assertEqual(bool(parsed['confirmed']),should_confirm)
                if should_confirm:
                    self.assertEqual([row['product']['id'] for row in parsed['confirmed']],[self.product_id])
                else:
                    self.assertEqual(len(parsed['review'])+len(parsed['failed']),1)
