"""Checkout day/location scope and immutable cashier regression checks."""
from contextlib import closing
import test_checkout
from test_receiving import ReceivingFixture


class CheckoutAccessTests(ReceivingFixture):
    call = test_checkout.CheckoutTests.call
    create_checkout = test_checkout.CheckoutTests.create_checkout
    action = test_checkout.CheckoutTests.action
    photo = test_checkout.CheckoutTests.photo

    def setUp(self):
        super().setUp()
        test_checkout.CheckoutTests.seed_checkout_shifts(self)
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_balances(product_id,location_id,disposition,quantity,version) VALUES (?,?,'saleable',5,1)", (self.product_id,self.floor))
            for subject in ('helper','manager'):
                con.execute('INSERT INTO inventory_access(auth0_sub,store_id) VALUES (?,?)', ('auth0|'+subject,self.store_id))
            con.commit()

    def get(self, path, role='staff'):
        return self.client.get(path, headers=self.headers(role))

    def revoke(self, subject='staff'):
        with closing(self.connect()) as con:
            con.execute('DELETE FROM shifts WHERE employee_id IN (SELECT id FROM employees WHERE auth0_id=?)', ('auth0|'+subject,))
            con.commit()

    def test_off_duty_own_history_but_no_live_create_or_replay(self):
        order, body = self.create_checkout()
        self.revoke()
        access = self.get('/api/checkouts/access').get_json()
        self.assertEqual(access['live_stores'], [])
        self.assertEqual(access['history_stores'][0]['id'], self.store_id)
        self.assertEqual(self.get(f'/api/checkouts?store_id={self.store_id}&view=live').status_code, 403)
        self.assertEqual(self.get(f'/api/checkouts?store_id={self.store_id}&view=history').get_json()['orders'][0]['id'], order['id'])
        self.assertFalse(self.get(f"/api/checkouts/{order['id']}").get_json()['can_process'])
        self.assertEqual(self.call('', body, 'create-order-1').status_code, 403)
        self.assertEqual(self.call(f"/{order['id']}/cancel", {'expected_version':1}, 'no-shift').status_code, 403)

    def test_shift_date_not_hours_or_availability(self):
        self.assertEqual(self.get('/api/checkouts/access').get_json()['live_stores'][0]['id'], self.store_id)
        with closing(self.connect()) as con:
            con.execute("UPDATE shifts SET date='2026-09-13' WHERE employee_id=(SELECT id FROM employees WHERE auth0_id='auth0|staff')")
            con.execute("INSERT INTO availability(employee_id,date,start_time,end_time) SELECT id,'2026-09-14','00:00','23:59' FROM employees WHERE auth0_id='auth0|staff'")
            con.commit()
        self.assertEqual(self.get('/api/checkouts/access').get_json()['live_stores'], [])
        with closing(self.connect()) as con:
            con.execute("UPDATE shifts SET date='2026-09-15' WHERE employee_id=(SELECT id FROM employees WHERE auth0_id='auth0|staff')")
            con.commit()
        self.assertEqual(self.get('/api/checkouts/access').get_json()['live_stores'], [])

    def test_live_view_never_changes_cashier_and_history_is_private(self):
        order, _ = self.create_checkout()
        order = self.action(order,'attempts','assigned',tender='card',amount_cents=4520,assigned_to='auth0|helper')
        visible = self.get(f"/api/checkouts/{order['id']}", 'staff:helper').get_json()
        self.assertEqual((visible['cashier_sub'], visible['cashier_name']), ('auth0|staff','Staff Cashier'))
        self.assertFalse(visible['can_process'])
        self.assertEqual(self.get(f'/api/checkouts?store_id={self.store_id}&view=history','staff:helper').get_json()['orders'], [])
        order = self.action(order,'complete','received',attempt_id=order['attempts'][0]['id'])
        self.action(order,'finalize','finish')
        self.assertEqual(self.get(f"/api/checkouts/{order['id']}",'staff:helper').status_code,403)

    def test_manager_uses_today_scope_admin_reads_without_inventory(self):
        order, _ = self.create_checkout()
        self.assertEqual(self.get(f"/api/checkouts/{order['id']}",'manager').status_code,200)
        self.revoke('manager')
        self.assertEqual(self.get(f"/api/checkouts/{order['id']}",'manager').status_code,403)
        access=self.get('/api/checkouts/access','admin').get_json()
        self.assertIn(self.store_id,[s['id'] for s in access['history_stores']])
        detail=self.get(f"/api/checkouts/{order['id']}",'admin')
        self.assertEqual(detail.status_code,200,detail.get_json())
        self.assertFalse(detail.get_json()['can_process'])

    def completed_with_photo(self):
        order, _ = self.create_checkout()
        order = self.action(order,'attempts','photo-attempt',tender='e_transfer',amount_cents=4520,assigned_to='auth0|helper')
        photo = self.photo(order,order['attempts'][0]['id'],'photo').get_json()
        order = self.get(f"/api/checkouts/{order['id']}").get_json()
        order = self.action(order,'complete','received',attempt_id=order['attempts'][0]['id'])
        order = self.action(order,'finalize','finish')
        return order, photo

    def test_linked_sale_and_photos_do_not_bypass_history(self):
        order, photo = self.completed_with_photo()
        evidence = order['attempts'][0]['photos'][0]['evidence_id']
        paths = [f"/api/checkouts/{order['id']}", f"/api/checkouts/{order['id']}/evidence/{photo['id']}",
                 f"/api/sale-documents/{order['sale_id']}", f'/api/payment-evidence/{evidence}/content']
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.get(path,'staff:helper').status_code,403)
                with self.get(path,'admin') as response:
                    self.assertEqual(response.status_code,200,response.get_json() if response.is_json else None)
        self.revoke()
        with closing(self.connect()) as con:
            con.execute("DELETE FROM inventory_access WHERE auth0_sub='auth0|staff'")
            con.commit()
        for path in paths:
            with self.get(path) as response:
                self.assertEqual(response.status_code,200,path)
        self.assertEqual(self.photo(order,order['attempts'][0]['id'],'late-denied',role='staff').status_code,403)

    def test_manager_report_closing_today_and_payment_write_revocation(self):
        order, photo = self.completed_with_photo()
        payment = order['attempts'][0]['payment_id']
        evidence = order['attempts'][0]['photos'][0]['evidence_id']
        sale = self.get(f"/api/sale-documents/{order['sale_id']}",'manager').get_json()
        body = {'expected_version':sale['version'],'reason':'Compared receipt'}
        headers = {**self.headers('manager'),'Idempotency-Key':'verified'}
        response = self.client.post(f'/api/payments/{payment}/verify',json=body,headers=headers)
        self.assertEqual(response.status_code,200,response.get_json())
        closing_response = self.client.post('/api/closing',json={'store_id':self.store_id,'business_date':'2026-09-08'},headers={**self.headers('manager'),'Idempotency-Key':'closing'})
        self.assertEqual(closing_response.status_code,201,closing_response.get_json())
        self.revoke('manager')
        self.assertEqual(self.client.post(f'/api/payments/{payment}/verify',json=body,headers=headers).status_code,403)
        self.assertEqual(self.client.post(f'/api/payment-evidence/{evidence}/review',json={'decision':'accepted','reason':'Compared proof'},headers={**self.headers('manager'),'Idempotency-Key':'evidence-review'}).status_code,403)
        for path in [f"/api/sale-documents/{order['sale_id']}",f'/api/payment-evidence/{evidence}/content',
                     '/api/reports/tenders?store_code=DT', '/api/reports/sales?store_code=DT',
                     '/api/reports/evidence-exceptions?store_code=DT', f"/api/closing/{closing_response.get_json()['closing_id']}"]:
            self.assertEqual(self.get(path,'manager').status_code,403,path)
        today = self.get('/api/today?business_date=2026-09-08','manager').get_json()
        self.assertEqual(today['sections']['financial']['rows'],[])
        helper_today = self.get('/api/today','staff:helper').get_json()
        self.assertEqual(helper_today['sections']['my_work']['rows'],[])

    def test_inactive_employee_and_shift_revocation_block_replay(self):
        order, _ = self.create_checkout()
        body = {'expected_version':order['version'],'tender':'cash','amount_cents':4520}
        self.assertEqual(self.call(f"/{order['id']}/attempts",body,'attempt').status_code,200)
        self.revoke()
        self.assertEqual(self.call(f"/{order['id']}/attempts",body,'attempt').status_code,403)
        with closing(self.connect()) as con:
            con.execute("UPDATE employees SET is_active=0 WHERE auth0_id='auth0|staff'")
            con.commit()
        self.assertEqual(self.get('/api/checkouts/access').status_code,403)
        self.assertEqual(self.get(f"/api/checkouts/{order['id']}").status_code,403)

    def test_history_date_filter_and_cursor_are_scoped(self):
        order, _ = self.create_checkout()
        with closing(self.connect()) as con:
            con.executemany("INSERT INTO checkout_orders(store_id,business_date,reference,payload,created_by) SELECT store_id,'2026-09-09',?,payload,created_by FROM checkout_orders WHERE id=?", [(f'page-{i}',order['id']) for i in range(101)])
            con.commit()
        base = f'/api/checkouts?store_id={self.store_id}&view=history&business_date=2026-09-09'
        page = self.get(base).get_json()
        self.assertEqual(len(page['orders']),100)
        older = self.get(base+f"&before_id={page['next_before_id']}").get_json()
        self.assertEqual(len(older['orders']),1)
        self.assertIsNone(older['next_before_id'])
        self.assertNotIn(older['orders'][0]['id'],[r['id'] for r in page['orders']])
        self.assertEqual(self.get(base,'staff:helper').get_json()['orders'],[])
        self.assertEqual(self.get(base+'&before_id=-1').status_code,400)

    def test_manager_history_follows_today_location_not_order_date(self):
        order, _ = self.create_checkout()
        with closing(self.connect()) as con:
            second = con.execute("SELECT id FROM stores WHERE id!=? AND is_active=1 LIMIT 1", (self.store_id,)).fetchone()[0]
            con.execute("UPDATE shifts SET store_id=? WHERE employee_id=(SELECT id FROM employees WHERE auth0_id='auth0|manager')", (second,))
            con.execute("INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id) SELECT id,'2026-09-08','00:00','23:59','test',? FROM employees WHERE auth0_id='auth0|manager'", (self.store_id,))
            con.commit()
        self.assertEqual(self.get(f"/api/checkouts/{order['id']}", 'manager').status_code,403)
        self.assertEqual(self.get('/api/checkouts/access','manager').get_json()['history_stores'][0]['id'],second)
        # A date range without checkout records retains its existing report access.
        self.assertEqual(self.get('/api/reports/sales?store_code=DT&from=2026-08-01&to=2026-08-31','manager').status_code,200)

    def test_demoted_payment_recorder_cannot_write_someone_elses_history(self):
        order, _ = self.completed_with_photo()
        payment = order['attempts'][0]['payment_id']
        with closing(self.connect()) as con:
            con.execute("UPDATE sale_payments SET recorded_by='auth0|helper' WHERE id=?",(payment,))
            con.commit()
        import io
        from PIL import Image
        image=io.BytesIO(); Image.new('RGB',(8,8),'blue').save(image,format='PNG');image.seek(0)
        response=self.client.post(f'/api/payments/{payment}/evidence',headers={**self.headers('staff:helper'),'Idempotency-Key':'demoted-photo'},data={'image':(image,'proof.png')})
        self.assertEqual(response.status_code,403,response.get_json())

    def test_admin_reads_without_schedule_profile(self):
        order, _ = self.completed_with_photo()
        with closing(self.connect()) as con:
            con.execute("DELETE FROM employees WHERE auth0_id='auth0|admin'")
            con.commit()
        for path in ['/api/checkouts/access',f'/api/checkouts?store_id={self.store_id}&view=live',
                     f'/api/checkouts?store_id={self.store_id}&view=history',f"/api/checkouts/{order['id']}",
                     f"/api/sale-documents/{order['sale_id']}",
                     f"/api/payment-evidence/{order['attempts'][0]['photos'][0]['evidence_id']}/content"]:
            with self.get(path,'admin') as response:
                self.assertEqual(response.status_code,200,path)
        self.assertEqual(self.photo(order,order['attempts'][0]['id'],'admin-no-inventory',role='admin').status_code,403)

    def test_closing_shift_is_rechecked_after_waiting_for_write_lock(self):
        from unittest.mock import patch
        import closing_operations
        self.create_checkout()
        response=self.client.post('/api/closing',json={'store_id':self.store_id,'business_date':'2026-09-08'},headers={**self.headers('manager'),'Idempotency-Key':'closing-race-create'})
        self.assertEqual(response.status_code,201,response.get_json())
        closing_id=response.get_json()['closing_id']
        with closing(self.connect()) as con:
            con.execute("UPDATE closing_sessions SET status='closed' WHERE id=?",(closing_id,))
            con.commit()
        before=self.snapshot(('closing_adjustments','operation_requests'))
        original_begin=closing_operations._begin
        def revoke_then_begin(con):
            self.revoke('manager')
            return original_begin(con)
        with patch('closing_operations._begin',side_effect=revoke_then_begin):
            response=self.client.post(f'/api/closing/{closing_id}/adjustments',json={'source_type':'checkout','source_id':'1','reason':'Reviewed late fact'},headers={**self.headers('manager'),'Idempotency-Key':'closing-race-adjustment'})
        self.assertEqual(response.status_code,403,response.get_json())
        self.assertEqual(self.snapshot(('closing_adjustments','operation_requests')),before)
