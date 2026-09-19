import io
from contextlib import closing

from PIL import Image
from test_receiving import ReceivingFixture


class CheckoutTests(ReceivingFixture):
    def setUp(self):
        super().setUp()
        self.seed_checkout_shifts()
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_balances(product_id,location_id,disposition,quantity,version) VALUES (?,?,'saleable',5,1)", (self.product_id,self.floor))
            con.execute("INSERT INTO inventory_access(auth0_sub,store_id) VALUES ('auth0|helper',?)", (self.store_id,))
            con.execute("INSERT INTO inventory_access(auth0_sub,store_id) VALUES ('auth0|manager',?)", (self.store_id,))
            con.commit()

    def seed_checkout_shifts(self):
        from unittest.mock import patch
        clock = patch('checkout_access.business_date', return_value='2026-09-14')
        clock.start(); self.addCleanup(clock.stop)
        with closing(self.connect()) as con:
            for subject, name in [('staff','Staff Cashier'),('helper','Photo Helper'),('manager','Manager'),('admin','Administrator')]:
                employee = con.execute('INSERT INTO employees(auth0_id,name) VALUES (?,?)', ('auth0|'+subject,name)).lastrowid
                if subject != 'admin':
                    con.execute("INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id) VALUES (?,'2026-09-14','00:01','00:02','test',?)", (employee,self.store_id))
            con.commit()

    def call(self, path, body, key, role='staff'):
        return self.client.post('/api/checkouts' + path, json=body, headers={**self.headers(role), 'Idempotency-Key': key})

    def create_checkout(self, reference='order-1'):
        body = dict(store_id=self.store_id, business_date='2026-09-08', reference=reference,
                    subtotal_cents=4000, source_tax_cents=520, gross_cents=4520,
                    reduction_cents=0, rounding_cents=0, collected_cents=4520,
                    lines=[dict(product_id=self.product_id,unit='piece',quantity=2,unit_price_cents=2000,source_tax_cents=520)])
        response = self.call('', body, 'create-' + reference)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json(), body

    def action(self, order, action, key, **values):
        response = self.call(f"/{order['id']}/{action}", {'expected_version': order['version'], **values}, key)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def photo(self, order, attempt, key, role='staff:helper'):
        file = io.BytesIO(); Image.new('RGB', (8,8), 'red').save(file, format='PNG'); file.seek(0)
        return self.client.post(f"/api/checkouts/{order['id']}/attempts/{attempt}/evidence", headers={**self.headers(role),'Idempotency-Key':key}, data={'image': (file, 'proof.png')})

    def test_split_payment_photo_and_finalization_are_exactly_once(self):
        order, body = self.create_checkout()
        order = self.action(order, 'attempts', 'first', tender='e_transfer', amount_cents=2000, assigned_to='auth0|helper')
        first = order['attempts'][-1]['id']
        photo = self.photo(order, first, 'photo')
        self.assertEqual(photo.status_code, 201, photo.get_json())
        self.assertEqual(self.photo(order, first, 'photo').get_json(), photo.get_json())
        order = self.client.get(f"/api/checkouts/{order['id']}", headers=self.headers()).get_json()
        order = self.action(order, 'complete', 'paid-first', attempt_id=first)
        blocked = self.call(f"/{order['id']}/cancel", {'expected_version':order['version']}, 'cancel-paid')
        self.assertEqual(blocked.status_code,409)
        order = self.action(order, 'attempts', 'second', tender='cash', amount_cents=2520)
        order = self.action(order, 'complete', 'paid-second', attempt_id=order['attempts'][-1]['id'])
        final_version = order['version']
        order = self.action(order, 'finalize', 'finish')
        replay = self.call(f"/{order['id']}/finalize", {'expected_version':final_version}, 'finish')
        self.assertEqual(replay.get_json()['sale_id'],order['sale_id'])
        self.assertEqual(self.call('',body,'create-order-1').get_json()['id'], order['id'])
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM sale_documents').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT sum(amount_cents) FROM sale_payments').fetchone()[0],4520)
            self.assertEqual(con.execute("SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=? AND disposition='saleable'",(self.product_id,self.floor)).fetchone()[0],3)
            evidence = con.execute('SELECT uploader_sub,status FROM payment_evidence').fetchone()
            self.assertEqual(tuple(evidence),('auth0|helper','pending'))

    def test_replaced_attempt_cannot_complete_or_carry_photos(self):
        order, _ = self.create_checkout()
        order = self.action(order,'attempts','old',tender='wechat',amount_cents=4520,assigned_to='auth0|helper')
        old = order['attempts'][-1]['id']
        self.assertEqual(self.photo(order,old,'old-photo').status_code,201)
        order = self.client.get(f"/api/checkouts/{order['id']}",headers=self.headers()).get_json()
        order = self.action(order,'attempts','replace',tender='cash',amount_cents=4520)
        response = self.call(f"/{order['id']}/complete", {'expected_version':order['version'],'attempt_id':old}, 'late')
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.photo(order,old,'late-photo').status_code,409)
        order = self.action(order,'complete','new-paid',attempt_id=order['attempts'][-1]['id'])
        self.action(order,'finalize','finish-new')
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM payment_evidence').fetchone()[0],0)

    def test_scope_and_cashier_authority(self):
        order, _ = self.create_checkout()
        self.assertEqual(self.client.get(f"/api/checkouts/{order['id']}",headers=self.headers('staff:helper')).status_code,200)
        order = self.action(order,'attempts','assigned',tender='card',amount_cents=4520,assigned_to='auth0|helper')
        self.assertEqual(self.client.get(f"/api/checkouts/{order['id']}",headers=self.headers('staff:helper')).status_code,200)
        today = self.client.get('/api/today',headers=self.headers('staff:helper')).get_json()
        self.assertTrue(any(row['type']=='checkout' and row['source_id']==order['id'] for row in today['sections']['my_work']['rows']))
        denied = self.call(f"/{order['id']}/complete", {'expected_version':order['version'],'attempt_id':order['attempts'][-1]['id']},'helper-paid','staff:helper')
        self.assertEqual(denied.status_code,403)
        denied = self.call(f"/{order['id']}/attempts", {'expected_version':order['version'],'tender':'cash','amount_cents':4521},'overpay')
        self.assertEqual(denied.status_code,400)

    def test_unresolved_checkout_blocks_closing_and_cancellation_updates_token(self):
        order, _ = self.create_checkout()
        response = self.client.post('/api/closing',json={'store_id':self.store_id,'business_date':'2026-09-08'},headers={**self.headers('manager'),'Idempotency-Key':'closing'})
        self.assertEqual(response.status_code,201,response.get_json())
        session = response.get_json()
        self.assertIn(f"checkout_unresolved:{order['id']}",session['hard_blockers'])
        self.action(order,'cancel','cancel-unpaid')
        current = self.client.get(f"/api/closing/{session['closing_id']}",headers=self.headers('manager')).get_json()
        self.assertNotEqual(session['source_token'],current['source_token'])
        self.assertNotIn(f"checkout_unresolved:{order['id']}",current['hard_blockers'])

    def test_failed_finalization_keeps_payment_and_retry_posts_once(self):
        from unittest.mock import patch
        order, _ = self.create_checkout()
        order = self.action(order,'attempts','pay',tender='cash',amount_cents=4520)
        order = self.action(order,'complete','received',attempt_id=order['attempts'][-1]['id'])
        with patch('checkout_operations._post_sale_in_transaction',side_effect=RuntimeError('posting interrupted')):
            with self.assertRaises(RuntimeError):
                self.call(f"/{order['id']}/finalize",{'expected_version':order['version']},'finish-retry')
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM sale_documents').fetchone()[0],0)
            self.assertEqual(con.execute("SELECT count(*) FROM checkout_attempts WHERE status='completed'").fetchone()[0],1)
        self.action(order,'finalize','finish-retry')

    def test_staged_photo_backup_and_late_photo_link(self):
        from pathlib import Path
        from backup_package import create_package, restore_package
        order, _ = self.create_checkout()
        order = self.action(order,'attempts','photo-attempt',tender='card',amount_cents=4520,assigned_to='auth0|helper')
        attempt = order['attempts'][-1]['id']
        self.assertEqual(self.photo(order,attempt,'before-payment').status_code,201)
        package = Path(self.tempdir.name)/'backup'
        roots = {'product':self.upload_dir,'payment':self.upload_dir/'payment_evidence','condition':self.upload_dir/'condition_evidence'}
        import db
        manifest = create_package(db.DB_PATH,roots,package)
        self.assertEqual(len(manifest['attachments']),1)
        restore_package(package,Path(self.tempdir.name)/'restore')
        order = self.client.get(f"/api/checkouts/{order['id']}",headers=self.headers()).get_json()
        order = self.action(order,'complete','photo-paid',attempt_id=attempt)
        order = self.action(order,'finalize','photo-finish')
        self.assertEqual(self.photo(order,attempt,'after-payment',role='staff').status_code,201)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM payment_evidence').fetchone()[0],2)
        manifest = create_package(db.DB_PATH,roots,Path(self.tempdir.name)/'backup-complete')
        self.assertEqual(len(manifest['attachments']),2)
        denied = self.client.get(f"/api/checkouts/{order['id']}/evidence/1",headers=self.headers('staff:other'))
        self.assertEqual(denied.status_code,403)
        self.assertEqual(self.client.get('/api/checkouts',query_string={'store_id':self.store_id},headers=self.headers('staff:helper')).status_code,200)

    def test_invalid_tender_and_path_ids_are_client_errors_without_writes(self):
        order, _ = self.create_checkout()
        before = self.snapshot(('checkout_orders','checkout_attempts','operation_requests'))
        for tender in ([], {}, True, 1, None):
            with self.subTest(tender=tender):
                response = self.call(f"/{order['id']}/attempts", {
                    'expected_version':order['version'],'tender':tender,'amount_cents':100,
                }, 'invalid-tender')
                self.assertEqual(response.status_code,400,response.get_json())
        for path in (f'/{2**63}', f"/{order['id']}/evidence/{2**63}"):
            with self.subTest(path=path):
                response = self.client.get('/api/checkouts'+path,headers=self.headers())
                self.assertEqual(response.status_code,400,response.get_json())
        self.assertEqual(self.snapshot(('checkout_orders','checkout_attempts','operation_requests')),before)

    def test_order_reference_must_be_text(self):
        _, body = self.create_checkout()
        before = self.snapshot(('checkout_orders','operation_requests'))
        for reference in ({'receipt':1}, ['receipt'], 123, True):
            with self.subTest(reference=reference):
                response = self.call('',{**body,'reference':reference},'invalid-reference')
                self.assertEqual(response.status_code,400,response.get_json())
        self.assertEqual(self.snapshot(('checkout_orders','operation_requests')),before)

    def test_parallel_receipt_retry_and_competing_finalizers(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        order, _ = self.create_checkout()
        order = self.action(order,'attempts','race-attempt',tender='cash',amount_cents=4520)
        barrier = Barrier(2)
        def request(action, key, payload):
            with self.app.test_client() as client:
                barrier.wait(timeout=5)
                response = client.post(f"/api/checkouts/{order['id']}/{action}",
                    headers={**self.headers(),'Idempotency-Key':key},json=payload)
                return response.status_code,response.get_json()
        with ThreadPoolExecutor(max_workers=2) as pool:
            payload={'expected_version':order['version'],'attempt_id':order['attempts'][-1]['id']}
            replies=[future.result(timeout=10) for future in [pool.submit(request,'complete','receipt-retry',payload) for _ in range(2)]]
            self.assertEqual([status for status,_ in replies],[200,200],replies)
            self.assertEqual(replies[0][1]['version'],order['version']+1)
            order=replies[0][1]
            payload={'expected_version':order['version']}
            replies=[future.result(timeout=10) for future in [pool.submit(request,'finalize',f'finalizer-{i}',payload) for i in range(2)]]
            self.assertEqual(sorted(status for status,_ in replies),[200,409],replies)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM sale_payments').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT count(*) FROM sale_documents').fetchone()[0],1)
            self.assertEqual(con.execute("SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=? AND disposition='saleable'",(self.product_id,self.floor)).fetchone()[0],3)
            self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_photo_failure_rolls_back_sale_payment_and_stock_together(self):
        from unittest.mock import patch
        order,_=self.create_checkout()
        order=self.action(order,'attempts','atomic-attempt',tender='card',amount_cents=4520,assigned_to='auth0|helper')
        self.assertEqual(self.photo(order,order['attempts'][-1]['id'],'atomic-photo').status_code,201)
        order=self.client.get(f"/api/checkouts/{order['id']}",headers=self.headers()).get_json()
        order=self.action(order,'complete','atomic-received',attempt_id=order['attempts'][-1]['id'])
        tables=('checkout_orders','checkout_attempts','checkout_evidence','sale_documents','sale_payments','payment_evidence','inventory_documents','inventory_balances','operation_requests')
        before=self.snapshot(tables)
        with patch('checkout_operations.attach_photo',side_effect=OSError('evidence linking interrupted')):
            with self.assertRaises(OSError):
                self.call(f"/{order['id']}/finalize",{'expected_version':order['version']},'atomic-finalize')
        self.assertEqual(self.snapshot(tables),before)
        self.action(order,'finalize','atomic-finalize')

    def test_lost_stock_retains_financial_fact_for_manager_allocation(self):
        order,_=self.create_checkout()
        order=self.action(order,'attempts','shortage-attempt',tender='card',amount_cents=4520)
        order=self.action(order,'complete','shortage-received',attempt_id=order['attempts'][-1]['id'])
        with closing(self.connect()) as con:
            con.execute("UPDATE inventory_balances SET quantity=1,version=version+1 WHERE product_id=? AND location_id=?",(self.product_id,self.floor))
            con.commit()
        order=self.action(order,'finalize','shortage-finalize')
        sale=self.client.get(f"/api/sale-documents/{order['sale_id']}",headers=self.headers()).get_json()
        self.assertEqual((sale['financial_status'],sale['allocation_status'],sale['payment_total_cents']),('recorded','pending',4520))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM inventory_documents WHERE source_type='sale_document'").fetchone()[0],0)
            self.assertEqual(con.execute('SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?',(self.product_id,self.floor)).fetchone()[0],1)
        session=self.client.post('/api/closing',json={'store_id':self.store_id,'business_date':'2026-09-08'},headers={**self.headers('manager'),'Idempotency-Key':'shortage-close'}).get_json()
        self.assertIn(f"pending_allocation:{order['sale_id']}",session['hard_blockers'])

    def test_revoked_scope_blocks_existing_assignments_and_replays(self):
        order,_=self.create_checkout()
        order=self.action(order,'attempts','revoked-attempt',tender='card',amount_cents=4520,assigned_to='auth0|helper')
        attempt=order['attempts'][-1]['id']
        photo=self.photo(order,attempt,'revoked-photo').get_json()
        with closing(self.connect()) as con:
            con.execute("DELETE FROM inventory_access WHERE auth0_sub='auth0|helper'")
            con.execute("DELETE FROM shifts WHERE employee_id=(SELECT id FROM employees WHERE auth0_id='auth0|helper')")
            con.commit()
        for path in (f"/api/checkouts/{order['id']}", f"/api/checkouts/{order['id']}/evidence/{photo['id']}"):
            self.assertEqual(self.client.get(path,headers=self.headers('staff:helper')).status_code,403)
        self.assertEqual(self.photo(order,attempt,'revoked-photo').status_code,403)
        self.assertEqual(self.client.get(f"/api/checkouts/{order['id']}").status_code,401)
        self.assertEqual(self.client.get(f"/api/checkouts/{order['id']}",headers=self.headers('viewer')).status_code,403)

    def test_invalid_opened_set_is_rejected_before_payment(self):
        _,body=self.create_checkout()
        for form,unit in (('ordinary','piece'),('random_box','box')):
            with closing(self.connect()) as con:
                con.execute('UPDATE products SET stock_form=?,stock_unit=? WHERE id=?',(form,unit,self.product_id))
                con.commit()
            bad={**body,'reference':'bad-set-'+form,'lines':[{**body['lines'][0],'unit':unit,'open_set_id':999}]}
            response=self.call('',bad,'bad-set-'+form)
            self.assertEqual(response.status_code,400,response.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM checkout_orders').fetchone()[0],1)

    def test_public_sale_apis_cannot_claim_checkout_source_identities(self):
        order,body=self.create_checkout()
        native={'system':'popcore_checkout','account':str(self.store_id),'reference':order['reference']}
        def post(path,payload,key,role='staff'):
            return self.client.post(path,json=payload,headers={**self.headers(role),'Idempotency-Key':key})
        forged=post('/api/sale-documents',{**body,'entry_mode':'planned_entry','source':native},'forged-sale')
        self.assertEqual(forged.status_code,400,forged.get_json())
        manual=post('/api/sale-documents',{**body,'entry_mode':'planned_entry','source':{**native,'system':'manual','reference':'manual-review'}},'manual-review').get_json()
        modified=self.client.patch(f"/api/sale-documents/{manual['sale_id']}",json={**body,'entry_mode':'planned_entry','source':native,'expected_version':manual['version']},headers={**self.headers(),'Idempotency-Key':'forged-update'})
        self.assertEqual(modified.status_code,400,modified.get_json())
        source={'source_system':'popcore_checkout','source_account':str(self.store_id),'source_reference':order['reference']}
        linked=post(f"/api/sale-documents/{manual['sale_id']}/source-links",{**source,'reason':'link','expected_version':manual['version']},'forged-link','manager')
        self.assertEqual(linked.status_code,400,linked.get_json())
        manual=post(f"/api/sale-documents/{manual['sale_id']}/post",{'expected_version':manual['version']},'manual-post').get_json()
        order=self.action(order,'attempts','reserved-attempt',tender='cash',amount_cents=4520)
        payment=post(f"/api/sale-documents/{manual['sale_id']}/payments",{'expected_version':manual['version'],'payments':[{**source,'source_reference':str(order['attempts'][-1]['id']),'tender':'cash','amount_cents':4520}]},'forged-payment')
        self.assertEqual(payment.status_code,400,payment.get_json())
        order=self.action(order,'complete','reserved-received',attempt_id=order['attempts'][-1]['id'])
        self.action(order,'finalize','reserved-finalize')

    def test_checkout_locks_catalog_identity_even_without_stock_history(self):
        with closing(self.connect()) as con:
            con.execute('DELETE FROM inventory_balances WHERE product_id=?',(self.product_id,))
            con.execute('UPDATE stock SET upstairs_qty=0,instore_qty=0,claw_qty=0 WHERE product_id=?',(self.product_id,))
            con.commit()
        order,_=self.create_checkout()
        response=self.client.patch(f'/api/products/{self.product_id}/inventory-identity',json={'stock_form':'random_box','stock_unit':'box'},headers=self.headers('admin'))
        self.assertEqual(response.status_code,409,response.get_json())
        self.assertEqual(response.get_json()['code'],'identity_locked')
        order=self.action(order,'attempts','identity-attempt',tender='card',amount_cents=4520)
        order=self.action(order,'complete','identity-received',attempt_id=order['attempts'][-1]['id'])
        self.action(order,'finalize','identity-finalize')

    def test_changed_catalog_snapshot_preserves_paid_sale_for_reconciliation(self):
        order,_=self.create_checkout()
        order=self.action(order,'attempts','drift-attempt',tender='card',amount_cents=4520)
        order=self.action(order,'complete','drift-received',attempt_id=order['attempts'][-1]['id'])
        # Reproduce a legacy/imported identity edit that bypassed the catalog API lock.
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET stock_form='random_box',stock_unit='box' WHERE id=?",(self.product_id,))
            con.commit()
        order=self.action(order,'finalize','drift-finalize')
        sale=self.client.get(f"/api/sale-documents/{order['sale_id']}",headers=self.headers()).get_json()
        self.assertEqual((sale['financial_status'],sale['allocation_status']),('recorded','pending'))
        self.assertEqual(sale['unresolved_reasons'],['product_mapping_required'])
        self.assertEqual(sale['payment_total_cents'],4520)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?',(self.product_id,self.floor)).fetchone()[0],5)

    def test_backup_restore_preserves_payment_and_finalization_retry_identity(self):
        from pathlib import Path
        from unittest.mock import patch
        from backup_package import create_package,restore_package
        import db
        order,_=self.create_checkout()
        order=self.action(order,'attempts','restore-attempt',tender='cash',amount_cents=4520)
        order=self.action(order,'complete','restore-received',attempt_id=order['attempts'][-1]['id'])
        version=order['version']
        completed=self.action(order,'finalize','restore-finalize')
        package=Path(self.tempdir.name)/'restart-backup'; restored=Path(self.tempdir.name)/'restart-restored'
        roots={'product':self.upload_dir,'payment':self.upload_dir/'payment_evidence','condition':self.upload_dir/'condition_evidence'}
        create_package(db.DB_PATH,roots,package);restore_package(package,restored)
        with patch.object(db,'DB_PATH',str(restored/'database.db')):
            replay=self.call(f"/{order['id']}/finalize",{'expected_version':version},'restore-finalize')
            self.assertEqual(replay.status_code,200,replay.get_json())
            self.assertEqual(replay.get_json()['sale_id'],completed['sale_id'])
            with closing(self.connect()) as con:
                self.assertEqual(con.execute('SELECT count(*) FROM sale_payments').fetchone()[0],1)
                self.assertEqual(con.execute('SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?',(self.product_id,self.floor)).fetchone()[0],3)

    def test_completed_checkout_refund_does_not_return_physical_stock(self):
        order,_=self.create_checkout()
        order=self.action(order,'attempts','refund-attempt',tender='card',amount_cents=4520)
        order=self.action(order,'complete','refund-received',attempt_id=order['attempts'][-1]['id'])
        order=self.action(order,'finalize','refund-finalize')
        sale=self.client.get(f"/api/sale-documents/{order['sale_id']}",headers=self.headers('manager')).get_json()
        payment_id=sale['payments'][0]['id']
        response=self.client.post(f'/api/payments/{payment_id}/events',json={'expected_version':sale['version'],'event_type':'refund','amount_cents':1000,'reason':'Refund confirmed outside POPCORE'},headers={**self.headers('manager'),'Idempotency-Key':'checkout-refund'})
        self.assertEqual(response.status_code,200,response.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?',(self.product_id,self.floor)).fetchone()[0],3)
        response=self.client.post(f"/api/sale-documents/{sale['sale_id']}/returns",json={'expected_version':response.get_json()['version'],'line_no':1,'quantity':1,'disposition':'saleable','reason':'One item physically received and checked'},headers={**self.headers('manager'),'Idempotency-Key':'checkout-physical-return'})
        self.assertEqual(response.status_code,200,response.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?',(self.product_id,self.floor)).fetchone()[0],4)
            self.assertEqual(con.execute("SELECT count(*) FROM payment_events WHERE event_type='refund'").fetchone()[0],1)

    def test_fresh_set_identity_cannot_be_unverified_and_retyped(self):
        with closing(self.connect()) as con:
            random_id=con.execute("INSERT INTO products(sku,name_cn_en,stock_form,stock_unit,identity_status) VALUES ('AUDIT-BOX','Audit Box','random_box','box','verified')").lastrowid
            set_id=con.execute("INSERT INTO products(sku,name_cn_en,stock_form,stock_unit,identity_status) VALUES ('AUDIT-SET','Audit Set','sealed_set','set','verified')").lastrowid
            conversion=con.execute('INSERT INTO product_conversions(source_product_id,target_product_id,output_per_input,version) VALUES (?,?,12,1)',(set_id,random_id)).lastrowid
            con.execute("INSERT INTO inventory_balances(product_id,location_id,disposition,quantity,version) VALUES (?,?,'saleable',1,1)",(set_id,self.floor))
            con.commit()
        body=dict(store_id=self.store_id,business_date='2026-09-08',reference='fresh-set-drift',subtotal_cents=700,source_tax_cents=0,gross_cents=700,reduction_cents=0,rounding_cents=0,collected_cents=700,
                  lines=[dict(product_id=random_id,unit='box',quantity=7,unit_price_cents=100,fresh_set=dict(product_id=set_id,conversion_id=conversion,conversion_factor=12))])
        response=self.call('',body,'fresh-set-create')
        self.assertEqual(response.status_code,201,response.get_json())
        order=self.action(response.get_json(),'attempts','fresh-set-attempt',tender='card',amount_cents=700)
        order=self.action(order,'complete','fresh-set-received',attempt_id=order['attempts'][-1]['id'])
        response=self.client.patch(f'/api/products/{set_id}/inventory-identity',json={'identity_status':'unverified'},headers=self.headers('manager'))
        self.assertEqual(response.status_code,200,response.get_json())
        response=self.client.patch(f'/api/products/{set_id}/inventory-identity',json={'stock_form':'ordinary','stock_unit':'piece','identity_status':'verified'},headers=self.headers('manager'))
        self.assertEqual(response.status_code,409,response.get_json())
        # Old imports may already contain this drift: paid money must still be retained.
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET stock_form='ordinary',stock_unit='piece',identity_status='verified' WHERE id=?",(set_id,))
            con.commit()
        order=self.action(order,'finalize','fresh-set-finalize')
        sale=self.client.get(f"/api/sale-documents/{order['sale_id']}",headers=self.headers()).get_json()
        self.assertEqual((sale['allocation_status'],sale['payment_total_cents']),('pending',700))
        self.assertEqual(sale['unresolved_reasons'],['product_mapping_required'])
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?',(set_id,self.floor)).fetchone()[0],1)

    def manager_action(self, order, action, key, **values):
        response=self.call(f"/{order['id']}/{action}",{'expected_version':order['version'],**values},key,'manager')
        self.assertEqual(response.status_code,200,response.get_json())
        return response.get_json()

    def abandoned_checkout(self):
        order,_=self.create_checkout()
        order=self.action(order,'attempts','deposit-attempt',tender='cash',amount_cents=2000)
        order=self.action(order,'complete','deposit-received',attempt_id=order['attempts'][-1]['id'])
        return self.manager_action(order,'abandon','abandon-deposit',reason='Customer abandoned purchase')

    def refund_body(self, order, amount=2000, reference='refund-1', date='2026-09-09'):
        return dict(attempt_id=order['attempts'][0]['id'],amount_cents=amount,reference=reference,
                    business_date=date,reason='Confirmed cash returned to customer',confirmed_received_and_refunded=True)

    def test_abandoned_deposit_refunds_cancel_without_a_sale_or_stock_movement(self):
        order=self.abandoned_checkout()
        original=order['attempts'][0]
        for action,values in [('attempts',dict(tender='cash',amount_cents=2520)),('complete',dict(attempt_id=original['id'])),('finalize',{})]:
            self.assertEqual(self.call(f"/{order['id']}/{action}",{'expected_version':order['version'],**values},'frozen-'+action).status_code,409)
        partial=self.manager_action(order,'refund','partial-refund',**self.refund_body(order,500))
        self.assertEqual((partial['status'],partial['received_cents'],partial['refunded_cents'],partial['refund_due_cents']),('open',2000,500,1500))
        final=self.manager_action(partial,'refund','final-refund',**self.refund_body(order,1500,'refund-2'))
        self.assertEqual((final['status'],final['refund_due_cents'],final['sale_id']),('cancelled',0,None))
        retry=self.call(f"/{order['id']}/refund",{'expected_version':partial['version'],**self.refund_body(order,1500,'refund-2')},'final-refund','manager')
        self.assertEqual(retry.get_json(),final)
        self.assertEqual(final['attempts'][0]['amount_cents'],original['amount_cents'])
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM sale_documents').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT count(*) FROM inventory_documents').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?',(self.product_id,self.floor)).fetchone()[0],5)
            self.assertEqual(con.execute('SELECT sum(amount_cents) FROM checkout_refunds').fetchone()[0],2000)

    def test_checkout_refund_requires_manager_confirmation_and_bounded_actual_facts(self):
        order=self.abandoned_checkout()
        body=self.refund_body(order)
        denied=self.call(f"/{order['id']}/refund",{'expected_version':order['version'],**body},'staff-refund')
        self.assertEqual(denied.status_code,403)
        for i,changed in enumerate([{'amount_cents':0},{'amount_cents':-1},{'amount_cents':True},{'amount_cents':2001},
                                   {'business_date':'2026-09-07'},{'business_date':'2099-01-01'},
                                   {'reason':''},{'reference':''},{'reference':{}},{'confirmed_received_and_refunded':False}]):
            response=self.call(f"/{order['id']}/refund",{'expected_version':order['version'],**body,**changed},f'invalid-refund-{i}','manager')
            self.assertIn(response.status_code,(400,409),response.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM checkout_refunds').fetchone()[0],0)
        self.manager_action(order,'refund','checked-refund',**body)

    def refund_closing(self, date):
        response=self.client.post('/api/closing',json={'store_id':self.store_id,'business_date':date},
            headers={**self.headers('manager'),'Idempotency-Key':'refund-closing-'+date})
        self.assertEqual(response.status_code,201,response.get_json())
        return response.get_json()

    def closing_action(self, session, action, key, **values):
        if 'denomination_counts' in values:
            from closing_operations import DENOMINATIONS
            values['denomination_counts'] = {**dict.fromkeys(DENOMINATIONS,0),**values['denomination_counts']}
        response=self.client.post(f"/api/closing/{session['closing_id']}/{action}",json={
            'expected_version':session['version'],'source_token':session['source_token'],**values},
            headers={**self.headers('manager'),'Idempotency-Key':key})
        self.assertEqual(response.status_code,200,response.get_json())
        return response.get_json()

    def test_refund_dates_flow_to_cash_reports_stale_counts_and_closing_snapshot(self):
        import json
        from closing_operations import cash_summary
        order=self.abandoned_checkout()
        receipt_day=self.refund_closing('2026-09-08')
        refund_day=self.refund_closing('2026-09-09')
        self.assertIn(f"checkout_unresolved:{order['id']}",receipt_day['hard_blockers'])
        self.assertEqual(receipt_day['cash']['unknown_checkout_cash_attempt_ids'],[order['attempts'][0]['id']])
        counted=self.closing_action(receipt_day,'cash-counts','deposit-count',opening_coin_cents=0,
            retained_coin_cents=0,denomination_counts={'5000':13,'2000':1})
        partial=self.manager_action(order,'refund','date-partial',**self.refund_body(order,500))
        current=self.client.get(f"/api/closing/{refund_day['closing_id']}",headers=self.headers('manager')).get_json()
        self.assertIn(f"checkout_unresolved:{order['id']}",current['hard_blockers'])
        final=self.manager_action(partial,'refund','date-rest',**self.refund_body(order,1500,'date-rest'))
        receipt_day=self.client.get(f"/api/closing/{receipt_day['closing_id']}",headers=self.headers('manager')).get_json()
        refund_day=self.client.get(f"/api/closing/{refund_day['closing_id']}",headers=self.headers('manager')).get_json()
        self.assertNotEqual(counted['source_token'],receipt_day['source_token'])
        self.assertNotIn(f"checkout_unresolved:{order['id']}",receipt_day['hard_blockers'])
        self.assertNotIn(f"checkout_unresolved:{order['id']}",refund_day['hard_blockers'])
        self.assertEqual(receipt_day['tender_totals_cents']['cash'],2000)
        self.assertEqual(refund_day['tender_totals_cents']['cash'],-2000)
        with closing(self.connect()) as con:
            self.assertEqual(cash_summary(con,self.store_id,'2026-09-08',0)['expected_drawer_cents'],67000)
            self.assertEqual(cash_summary(con,self.store_id,'2026-09-09',0)['expected_drawer_cents'],63000)
        for day,recorded,refunded in [('2026-09-08',2000,0),('2026-09-09',0,2000)]:
            response=self.client.get(f'/api/reports/tenders?store_code=DT&from={day}&to={day}',headers=self.headers('manager'))
            self.assertEqual(response.status_code,200,response.get_json())
            self.assertEqual(response.get_json()['items'],[dict(tender='cash',recorded_cents=recorded,
                verified_cents=recorded,unknown_count=0,pending_count=0,refund_cents=refunded)])
        patched=self.client.patch(f"/api/closing/{receipt_day['closing_id']}",json={
            'expected_version':receipt_day['version'],'intake_complete':True},headers={
            **self.headers('manager'),'Idempotency-Key':'refund-intake'}).get_json()
        stale=self.client.post(f"/api/closing/{patched['closing_id']}/submit",json={
            'expected_version':patched['version'],'source_token':patched['source_token']},headers={
            **self.headers('manager'),'Idempotency-Key':'refund-stale-submit'})
        self.assertEqual(stale.status_code,409,stale.get_json())
        self.assertEqual(stale.get_json()['code'],'closing_stale')
        counted=self.closing_action(patched,'cash-counts','deposit-recount',opening_coin_cents=0,
            retained_coin_cents=0,denomination_counts={'5000':13,'2000':1})
        submitted=self.closing_action(counted,'submit','deposit-submit')
        signed=self.closing_action(submitted,'close','deposit-close',accepted_exceptions=[])
        self.assertEqual(signed['status'],'closed')
        with closing(self.connect()) as con:
            snapshot=con.execute('SELECT snapshot_json FROM closing_snapshots WHERE closing_session_id=?',
                (signed['closing_id'],)).fetchone()[0]
            self.assertEqual(json.loads(snapshot)['tender_totals_cents']['cash'],2000)
        # Replenish the cash returned on the next day so the required float can be retained.
        refund_day=self.closing_action(refund_day,'cash-events','refund-day-float',event_type='paid_in',
            amount_cents=2000,reason='Manager replenished refunded cash')
        refund_day=self.closing_action(refund_day,'cash-counts','refund-day-count',opening_coin_cents=0,
            retained_coin_cents=0,denomination_counts={'5000':13})
        refund_day=self.client.patch(f"/api/closing/{refund_day['closing_id']}",json={
            'expected_version':refund_day['version'],'intake_complete':True},headers={
            **self.headers('manager'),'Idempotency-Key':'refund-day-intake'}).get_json()
        refund_day=self.closing_action(refund_day,'submit','refund-day-submit')
        refund_day=self.closing_action(refund_day,'close','refund-day-close',accepted_exceptions=[])
        with closing(self.connect()) as con:
            refund_snapshot=con.execute('SELECT snapshot_json FROM closing_snapshots WHERE closing_session_id=?',
                (refund_day['closing_id'],)).fetchone()[0]
            self.assertEqual(json.loads(refund_snapshot)['tender_totals_cents']['cash'],-2000)
        # A later-recorded checkout/refund creates an adjustment without changing the signed snapshot.
        late,_=self.create_checkout('late-abandonment')
        late=self.action(late,'attempts','late-attempt',tender='cash',amount_cents=100)
        late=self.action(late,'complete','late-received',attempt_id=late['attempts'][0]['id'])
        late=self.manager_action(late,'abandon','late-abandon',reason='Late recorded abandonment')
        self.manager_action(late,'refund','late-refund',**self.refund_body(late,100,'late-refund'))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT snapshot_json FROM closing_snapshots WHERE closing_session_id=?',
                (signed['closing_id'],)).fetchone()[0],snapshot)
            self.assertEqual(con.execute("SELECT count(*) FROM closing_adjustments WHERE closing_session_id=? AND source_type='checkout_refund'",
                (signed['closing_id'],)).fetchone()[0],1)
            self.assertEqual(con.execute('SELECT snapshot_json FROM closing_snapshots WHERE closing_session_id=?',
                (refund_day['closing_id'],)).fetchone()[0],refund_snapshot)
            self.assertEqual(con.execute("SELECT count(*) FROM closing_adjustments WHERE closing_session_id=? AND source_type='checkout_refund'",
                (refund_day['closing_id'],)).fetchone()[0],1)

    def test_split_tender_refunds_cancel_pending_attempt_and_keep_cash_separate(self):
        from closing_operations import cash_summary
        order,_=self.create_checkout()
        for i,(tender,amount) in enumerate([('cash',1000),('e_transfer',1500)]):
            order=self.action(order,'attempts',f'split-start-{i}',tender=tender,amount_cents=amount)
            order=self.action(order,'complete',f'split-receive-{i}',attempt_id=order['attempts'][-1]['id'])
        order=self.action(order,'attempts','split-unpaid',tender='card',amount_cents=2020)
        order=self.manager_action(order,'abandon','split-abandon',reason='Customer changed mind')
        self.assertEqual(order['attempts'][-1]['status'],'cancelled')
        order=self.manager_action(order,'refund','split-cash',**self.refund_body(order,1000,'split-cash','2026-09-08'))
        self.assertEqual(order['refund_due_cents'],1500)
        invalid={**self.refund_body(order,1500,'split-electronic','2026-09-08'),
            'attempt_id':order['attempts'][1]['id'],'tender':'cash'}
        self.assertEqual(self.call(f"/{order['id']}/refund",{'expected_version':order['version'],**invalid},'wrong-refund-tender','manager').status_code,400)
        invalid.pop('tender')
        order=self.manager_action(order,'refund','split-electronic',**invalid)
        self.assertEqual(order['status'],'cancelled')
        with closing(self.connect()) as con:
            cash=cash_summary(con,self.store_id,'2026-09-08',0)
            self.assertEqual((cash['verified_cash_receipts_cents'],cash['event_totals_cents']['refund'],cash['expected_drawer_cents']),(1000,1000,65000))
        session=self.refund_closing('2026-09-08')
        self.assertEqual(session['tender_totals_cents']['e_transfer'],0)
        self.assertEqual(session['tender_totals_cents']['cash'],0)

    def test_duplicate_reference_competing_refunds_and_revoked_manager_replay(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        order=self.abandoned_checkout()
        order=self.manager_action(order,'refund','unique-first',**self.refund_body(order,500,'same-external-refund'))
        duplicate=self.call(f"/{order['id']}/refund",{'expected_version':order['version'],
            **self.refund_body(order,500,'same-external-refund')},'unique-other-key','manager')
        self.assertEqual(duplicate.status_code,409,duplicate.get_json())
        barrier=Barrier(2)
        body={'expected_version':order['version'],**self.refund_body(order,1500,'race-refund')}
        def refund(key):
            with self.app.test_client() as client:
                barrier.wait(timeout=5)
                response=client.post(f"/api/checkouts/{order['id']}/refund",json=body,
                    headers={**self.headers('manager'),'Idempotency-Key':key})
                return response.status_code,key
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=[f.result(timeout=10) for f in [pool.submit(refund,f'race-{i}') for i in range(2)]]
        self.assertEqual(sorted(status for status,_ in results),[200,409])
        winning_key=next(key for status,key in results if status==200)
        replay=self.call(f"/{order['id']}/refund",body,winning_key,'manager')
        self.assertEqual(replay.status_code,200,replay.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT sum(amount_cents) FROM checkout_refunds').fetchone()[0],2000)
            con.execute("DELETE FROM inventory_access WHERE auth0_sub='auth0|manager'")
            con.commit()
        self.assertEqual(self.call(f"/{order['id']}/refund",body,winning_key,'manager').status_code,403)

    def test_refund_backup_restore_preserves_evidence_and_replay(self):
        from pathlib import Path
        from unittest.mock import patch
        import db
        from backup_package import create_package,restore_package
        import payment_evidence
        order=self.abandoned_checkout()
        response=self.photo(order,order['attempts'][0]['id'],'refund-proof',role='manager')
        self.assertEqual(response.status_code,201,response.get_json())
        photo=response.get_json()
        order=self.client.get(f"/api/checkouts/{order['id']}",headers=self.headers('manager')).get_json()
        body={'expected_version':order['version'],**self.refund_body(order,500,'restored-refund')}
        self.manager_action(order,'refund','restore-refund',**self.refund_body(order,500,'restored-refund'))
        package=Path(self.tempdir.name)/'refund-backup';restored=Path(self.tempdir.name)/'refund-restored'
        roots={'product':self.upload_dir,'payment':self.upload_dir/'payment_evidence','condition':self.upload_dir/'condition_evidence'}
        create_package(db.DB_PATH,roots,package);restore_package(package,restored)
        with patch.object(db,'DB_PATH',str(restored/'database.db')), patch.object(payment_evidence,'EVIDENCE_DIR',restored/'attachments'/'payment'):
            db.migrate_db();db.migrate_db()
            replay=self.call(f"/{order['id']}/refund",body,'restore-refund','manager')
            self.assertEqual(replay.status_code,200,replay.get_json())
            order=replay.get_json()
            self.assertEqual(order['refunded_cents'],500)
            order=self.manager_action(order,'refund','restore-refund-rest',**self.refund_body(order,1500,'restored-rest'))
            self.assertEqual(order['status'],'cancelled')
            with self.client.get(f"/api/checkouts/{order['id']}/evidence/{photo['id']}",headers=self.headers('manager')) as image:
                self.assertEqual(image.status_code,200)
            with closing(self.connect()) as con:
                self.assertEqual(con.execute('SELECT count(*) FROM checkout_refunds').fetchone()[0],2)
                self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_refund_failure_rolls_back_money_status_and_retry_key_together(self):
        from unittest.mock import patch
        order=self.abandoned_checkout()
        tables=('checkout_orders','checkout_attempts','checkout_refunds','operation_requests','closing_adjustments')
        before=self.snapshot(tables)
        body={'expected_version':order['version'],**self.refund_body(order)}
        with patch('checkout_operations.record_late_adjustment',side_effect=OSError('interrupted after refund insert')):
            with self.assertRaises(OSError):
                self.call(f"/{order['id']}/refund",body,'atomic-refund','manager')
        self.assertEqual(self.snapshot(tables),before)
        self.assertEqual(self.call(f"/{order['id']}/refund",body,'atomic-refund','manager').get_json()['status'],'cancelled')

    def test_refunds_require_abandonment_and_exact_attempt_and_manager_role_on_replay(self):
        order,_=self.create_checkout()
        order=self.action(order,'attempts','scope-attempt',tender='card',amount_cents=2000)
        order=self.action(order,'complete','scope-received',attempt_id=order['attempts'][0]['id'])
        body={'expected_version':order['version'],**self.refund_body(order)}
        self.assertEqual(self.call(f"/{order['id']}/refund",body,'not-abandoned','manager').status_code,409)
        self.assertEqual(self.call(f"/{order['id']}/abandon",{'expected_version':order['version'],'reason':'Abandoned'},'staff-abandon').status_code,403)
        order=self.manager_action(order,'abandon','scope-abandon',reason='Abandoned')
        other,_=self.create_checkout('other-order')
        other=self.action(other,'attempts','other-attempt',tender='card',amount_cents=2000)
        other=self.action(other,'complete','other-received',attempt_id=other['attempts'][0]['id'])
        for attempt in (other['attempts'][0]['id'],True,2**80):
            response=self.call(f"/{order['id']}/refund",{'expected_version':order['version'],
                **self.refund_body(order),'attempt_id':attempt},'bad-refund-attempt-'+str(attempt),'manager')
            self.assertEqual(response.status_code,400,response.get_json())
        body={'expected_version':order['version'],**self.refund_body(order)}
        self.assertEqual(self.call(f"/{order['id']}/refund",body,'role-refund','manager').status_code,200)
        self.assertEqual(self.call(f"/{order['id']}/refund",body,'role-refund','staff:manager').status_code,403)
        self.assertEqual(self.call(f"/{order['id']}/refund",{**body,'amount_cents':1000},'role-refund','manager').status_code,409)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM checkout_refunds').fetchone()[0],1)

    def test_upgrade_existing_checkout_preserves_attempt_and_accepts_refund(self):
        import db
        # Recreate the pre-refund schema in this disposable database, retaining a received checkout.
        order,_=self.create_checkout()
        order=self.action(order,'attempts','upgrade-attempt',tender='cash',amount_cents=2000)
        order=self.action(order,'complete','upgrade-received',attempt_id=order['attempts'][0]['id'])
        with closing(self.connect()) as con:
            con.execute('DROP TABLE checkout_refunds')
            for column in ('abandoned_reason','abandoned_by','abandoned_at'):
                con.execute(f'ALTER TABLE checkout_orders DROP COLUMN {column}')
            con.execute("DELETE FROM _migrations WHERE name='checkout_refunds'")
            con.commit()
        db.migrate_db();db.migrate_db()
        order=self.manager_action(order,'abandon','upgraded-abandon',reason='Abandoned')
        order=self.manager_action(order,'refund','upgraded-refund',**self.refund_body(order))
        self.assertEqual((order['status'],order['received_cents'],order['refunded_cents']),('cancelled',2000,2000))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])
