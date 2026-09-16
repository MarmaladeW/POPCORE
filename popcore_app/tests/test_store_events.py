"""Store activity stays attributed, scoped, and separate from stock and sales."""
from contextlib import closing
from datetime import date, timedelta
import json
from unittest.mock import patch

from test_receiving import ReceivingFixture
from checkout_access import business_date


class StoreEventsTests(ReceivingFixture):
    def setUp(self):
        super().setUp()
        self.today = business_date()
        with closing(self.connect()) as con:
            for name in ('staff', 'helper', 'manager'):
                con.execute('INSERT INTO employees(auth0_id,name) VALUES (?,?)', (f'auth0|{name}', name.title()))
                con.execute("INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id) VALUES ((SELECT id FROM employees WHERE auth0_id=?),?,'09:00','17:00','test',?)", (f'auth0|{name}', self.today, self.store_id))
            con.commit()

    def post(self, body, key='event-1', role='staff'):
        return self.client.post('/api/store-events', json=body,
            headers={**self.headers(role), 'Idempotency-Key': key})

    def get(self, role='staff', date=None):
        return self.client.get(f'/api/store-events?store_id={self.store_id}&business_date={date or self.today}', headers=self.headers(role))

    def test_records_operational_events_without_stock_or_sale_changes(self):
        before = self.snapshot(('inventory_documents','sale_documents','stock'))
        for kind in ('claw_prize','claw_refill','display_in'):
            body = dict(store_id=self.store_id,kind=kind,product_id=self.product_id,quantity=2,note='Counted')
            first = self.post(body, kind)
            self.assertEqual(first.status_code, 201, first.get_json())
            self.assertEqual(self.post(body,kind).get_json(), first.get_json())
            self.assertEqual(first.get_json()['product_name'],'Test Product')
        self.assertEqual(self.snapshot(('inventory_documents','sale_documents','stock')), before)
        self.assertEqual(len(self.get().get_json()['events']),3)

    def test_product_snapshot_uses_catalog_fallbacks(self):
        body = dict(store_id=self.store_id,kind='display_in',product_id=self.product_id,quantity=1)
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET name_cn_en=NULL,jizhanming='Display Name' WHERE id=?",(self.product_id,))
            con.commit()
        named = self.post(body,'named').get_json()
        self.assertEqual(named['product_name'],'Display Name')
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET jizhanming=NULL WHERE id=?",(self.product_id,))
            con.commit()
        sku = self.post(body,'sku').get_json()
        self.assertEqual(sku['product_name'],'TEST-1')
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET sku='' WHERE id=?",(self.product_id,))
            con.commit()
        fallback = self.post(body,'id').get_json()
        self.assertEqual(fallback['product_name'],str(self.product_id))
        self.assertEqual(self.get().get_json()['events'][0]['product_name'],'Display Name')

    def test_cash_exchange_pays_out_once_and_rechecks_permission(self):
        body = dict(store_id=self.store_id,kind='cash_exchange',tender='card',amount_cents=2000,note='Digital receipt confirmed')
        first = self.post(body)
        self.assertEqual(first.status_code,201,first.get_json())
        self.assertEqual(self.post(body).get_json(),first.get_json())
        with closing(self.connect()) as con:
            payout = con.execute('SELECT * FROM cash_events').fetchall()
            self.assertEqual(len(payout),1)
            self.assertEqual((payout[0]['event_type'],payout[0]['amount_cents']),('payout',2000))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM sale_payments').fetchone()[0],0)
            con.execute("DELETE FROM inventory_access WHERE auth0_sub='auth0|staff'")
            con.commit()
        self.assertEqual(self.post(body).status_code,403)

    def test_exact_retry_across_toronto_date_keeps_original_event(self):
        body = dict(store_id=self.store_id,kind='claw_refill',product_id=self.product_id,quantity=1)
        first = self.post(body).get_json()
        tomorrow = (date.fromisoformat(self.today) + timedelta(days=1)).isoformat()
        with closing(self.connect()) as con:
            con.execute("UPDATE shifts SET date=? WHERE employee_id=(SELECT id FROM employees WHERE auth0_id='auth0|staff')",(tomorrow,))
            con.commit()
        with patch('blueprints.store_events.business_date',return_value=tomorrow), patch('checkout_access.business_date',return_value=tomorrow):
            replay = self.post(body)
        self.assertEqual(replay.status_code,201,replay.get_json())
        self.assertEqual(replay.get_json(),first)

    def test_validation_closed_day_and_privacy(self):
        valid = dict(store_id=self.store_id,kind='claw_prize',product_id=self.product_id,quantity=1,note='Prize')
        for invalid in ({**valid,'actor_sub':'auth0|helper'}, {**valid,'business_date':self.today},
                        {**valid,'quantity':0}, {**valid,'product_id':999999},
                        {**valid,'tender':'cash'}, {**valid,'kind':'bad'}, {**valid,'kind':[]}):
            self.assertEqual(self.post(invalid,'invalid-'+str(invalid)).status_code,400)
        self.assertEqual(self.post(valid,'staff-event').status_code,201)
        self.assertEqual(self.post(valid,'helper-event','staff:helper').status_code,201)
        personal = self.get().get_json()
        self.assertEqual(personal['scope'],'personal')
        self.assertEqual(len(personal['events']),1)
        self.assertEqual(len(self.get('manager').get_json()['events']),2)
        with closing(self.connect()) as con:
            con.execute("INSERT INTO closing_sessions(store_id,business_date,status,created_by) VALUES (?,?,'closed','test')",(self.store_id,self.today))
            con.commit()
        self.assertEqual(self.post(valid,'after-close').status_code,409)
        self.assertEqual(self.post(valid,'staff-event').status_code,201)
        with closing(self.connect()) as con:
            con.execute("DELETE FROM shifts WHERE employee_id=(SELECT id FROM employees WHERE auth0_id='auth0|staff')")
            con.commit()
        self.assertEqual(self.post(valid,'staff-event').status_code,403)
        self.assertEqual(self.get().status_code,200)
        self.assertEqual(len(self.get().get_json()['events']),1)

    def test_completed_checkout_groups_once_and_goods_require_inventory_access(self):
        with closing(self.connect()) as con:
            for reference, owner, tenders in (
                ('POS-1','staff',[('card',1000)]),
                ('NONPOS-1','staff',[('e_transfer',1500)]),
                ('SPLIT-1','staff',[('cash',800),('card',700)]),
                ('PRIVATE-1','helper',[('card',500)]),
            ):
                order_id = con.execute('''INSERT INTO checkout_orders
                    (store_id,business_date,reference,payload,created_by,status)
                    VALUES (?,?,?,'{}',?,'completed')''',
                    (self.store_id,self.today,reference,'auth0|'+owner)).lastrowid
                for tender, amount in tenders:
                    con.execute('''INSERT INTO checkout_attempts
                        (checkout_id,tender,amount_cents,assigned_to,status)
                        VALUES (?,?,?,?,'completed')''',
                        (order_id,tender,amount,'auth0|'+owner))
            con.execute('''INSERT INTO goods_receipts
                (store_id,destination_location_id,business_date,status,created_by)
                VALUES (?,?,?,'posted','auth0|staff')''',(self.store_id,self.floor,self.today))
            con.execute('''INSERT INTO goods_receipts
                (store_id,destination_location_id,business_date,status,created_by)
                VALUES (?,?,?,'posted','auth0|helper')''',(self.store_id,self.floor,self.today))
            other = con.execute('SELECT id FROM inventory_locations WHERE store_id!=? LIMIT 1',(self.store_id,)).fetchone()['id']
            incoming = con.execute("INSERT INTO inventory_deliveries(kind,source_location_id,destination_location_id,business_date,created_by) VALUES ('transfer',?,?,?,'auth0|helper')",(other,self.floor,self.today)).lastrowid
            con.execute("INSERT INTO inventory_deliveries(kind,source_location_id,destination_location_id,business_date,created_by) VALUES ('transfer',?,?,?,'auth0|helper')",(self.floor,other,self.today))
            con.execute("INSERT INTO inventory_deliveries(kind,source_location_id,destination_location_id,business_date,created_by) VALUES ('restock',?,?,?,'auth0|helper')",(other,self.floor,self.today))
            con.commit()
        personal = self.get().get_json()
        self.assertEqual([len(personal['checkouts'][group]) for group in ('pos','non_pos','split')],[1,1,1])
        self.assertEqual(sum(len(group) for group in personal['checkouts'].values()),3)
        self.assertEqual(len(personal['receipts']),2)
        self.assertEqual([item['id'] for item in personal['transfers']],[incoming])
        self.assertNotIn('PRIVATE-1',personal['summary_text'])
        self.assertEqual(len(self.get('manager').get_json()['checkouts']['pos']),2)
        self.assertEqual(self.get('manager').get_json()['receipts'],[])
        with closing(self.connect()) as con:
            con.execute("DELETE FROM inventory_access WHERE auth0_sub='auth0|staff'")
            con.commit()
        self.assertEqual(self.get().get_json()['receipts'],[])

    def test_summary_text_uses_snapshots_actor_local_time_and_pending_count(self):
        payload = {'lines':[{'product_name_snapshot':'Captured Plush','quantity':2}], 'collected_cents':1200}
        with closing(self.connect()) as con:
            order_id = con.execute('''INSERT INTO checkout_orders
                (store_id,business_date,reference,payload,created_by,status)
                VALUES (?,?,?,?,?,'completed')''',
                (self.store_id,self.today,'SOLD-1',json.dumps(payload),'auth0|staff')).lastrowid
            con.execute("INSERT INTO checkout_attempts(checkout_id,tender,amount_cents,assigned_to,status) VALUES (?,'card',1200,'auth0|staff','completed')",(order_id,))
            con.execute("INSERT INTO checkout_orders(store_id,business_date,reference,payload,created_by) VALUES (?,?,?,'{}','auth0|staff')",(self.store_id,self.today,'PENDING-1'))
            con.commit()
        self.assertEqual(self.post(dict(store_id=self.store_id,kind='claw_prize',product_id=self.product_id,quantity=1,note='Won')).status_code,201)
        text = self.get().get_json()['summary_text']
        for expected in ('DT',self.today,'个人','卡/现金订单','Captured Plush × 2','Staff','娃娃机出奖: 1','入娃娃机: 0','换现金: 0','入display: 0','待完成订单: 1'):
            self.assertIn(expected,text)
        self.assertNotIn('PENDING-1:',text)
