"""Cohesive API/database store-day proof used by the Build 6 pilot gate."""
import io,json
from contextlib import closing
import test_closing


class StoreDayIntegrationTests(test_closing.ClosingTests):
    PNG=bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c020000000b4944415478da6364f80f00010501012718e3660000000049454e44ae426082')

    def test_review_return_close_and_late_adjustments_reconcile(self):
        ready=self.ready_session()
        with closing(self.connect()) as con:
            sale_id=con.execute("SELECT id FROM sale_documents WHERE business_date='2026-09-08' ORDER BY id DESC").fetchone()[0]
            card=con.execute("SELECT id FROM sale_payments WHERE sale_id=? AND tender='card'",(sale_id,)).fetchone()[0]
        upload=self.client.post(f'/api/payments/{card}/evidence',headers={**self.headers(),'Idempotency-Key':'integration-evidence'},data={'image':(io.BytesIO(self.PNG),'pilot.png')},content_type='multipart/form-data')
        self.assertEqual(upload.status_code,201,upload.get_json());evidence_id=upload.get_json()['evidence_id']
        review=self.client.post(f'/api/payment-evidence/{evidence_id}/review',headers={**self.headers('manager'),'Idempotency-Key':'integration-evidence-review'},json={'decision':'accepted','reason':'Readable receipt'})
        self.assertEqual(review.status_code,200,review.get_json())
        sale=self.client.get(f'/api/sale-documents/{sale_id}',headers=self.headers('manager')).get_json()
        verified=self.client.post(f'/api/payments/{card}/verify',headers={**self.headers('manager'),'Idempotency-Key':'integration-card-verify'},json={'expected_version':sale['version'],'reason':'Matched terminal'})
        self.assertEqual(verified.status_code,200,verified.get_json())

        stale=self.client.post(f"/api/closing/{ready['closing_id']}/submit",headers={**self.headers(),'Idempotency-Key':'integration-stale-submit'},json={'expected_version':ready['version'],'source_token':ready['source_token']})
        self.assertEqual(stale.status_code,409,stale.get_json())
        refreshed=self.client.get(f"/api/closing/{ready['closing_id']}",headers=self.headers()).get_json()
        recounted=self.client.post(f"/api/closing/{ready['closing_id']}/cash-counts",headers={**self.headers(),'Idempotency-Key':'integration-recount'},json={'expected_version':refreshed['version'],'source_token':refreshed['source_token'],'opening_coin_cents':2000,'retained_coin_cents':2000,'denomination_counts':self.COUNTS}).get_json()
        submitted=self.client.post(f"/api/closing/{ready['closing_id']}/submit",headers={**self.headers(),'Idempotency-Key':'integration-submit'},json={'expected_version':recounted['version'],'source_token':recounted['source_token']}).get_json()
        returned=self.client.post(f"/api/closing/{ready['closing_id']}/return",headers={**self.headers('manager'),'Idempotency-Key':'integration-return'},json={'expected_version':submitted['version'],'reason':'Confirm each non-cash tender'}).get_json()
        resubmitted=self.client.post(f"/api/closing/{ready['closing_id']}/submit",headers={**self.headers(),'Idempotency-Key':'integration-resubmit'},json={'expected_version':returned['version'],'source_token':returned['source_token']}).get_json()
        manager_detail=self.client.get(f"/api/closing/{ready['closing_id']}",headers=self.headers('manager')).get_json()
        accepted=[{'code':code,'reason':f'Reviewed {code}'} for code in manager_detail['review_exceptions']]
        closed=self.client.post(f"/api/closing/{ready['closing_id']}/close",headers={**self.headers('manager'),'Idempotency-Key':'integration-close'},json={'expected_version':resubmitted['version'],'source_token':resubmitted['source_token'],'accepted_exceptions':accepted})
        self.assertEqual(closed.status_code,200,closed.get_json())
        before=self.client.get(f"/api/closing/{ready['closing_id']}",headers=self.headers('manager')).get_json()['snapshot']

        sale=self.client.get(f'/api/sale-documents/{sale_id}',headers=self.headers('manager')).get_json()
        cash=next(item for item in sale['payments'] if item['tender']=='cash')
        refund_body={'expected_version':sale['version'],'event_type':'refund','amount_cents':500,'reason':'Customer refund'}
        first=self.client.post(f"/api/payments/{cash['id']}/events",headers={**self.headers('manager'),'Idempotency-Key':'integration-refund'},json=refund_body)
        replay=self.client.post(f"/api/payments/{cash['id']}/events",headers={**self.headers('manager'),'Idempotency-Key':'integration-refund'},json=refund_body)
        self.assertEqual(first.get_json(),replay.get_json())
        sale=self.client.get(f'/api/sale-documents/{sale_id}',headers=self.headers('manager')).get_json()
        physical=self.client.post(f'/api/sale-documents/{sale_id}/returns',headers={**self.headers('manager'),'Idempotency-Key':'integration-physical-return'},json={'expected_version':sale['version'],'line_no':1,'quantity':1,'disposition':'saleable','reason':'Unopened return'})
        self.assertEqual(physical.status_code,200,physical.get_json())
        after=self.client.get(f"/api/closing/{ready['closing_id']}",headers=self.headers('manager')).get_json()
        self.assertEqual(before,after['snapshot']);self.assertGreaterEqual(len(after['late_adjustments']),2)

        tenders=self.client.get('/api/reports/tenders?store_code=DT&from=2026-09-08&to=2026-09-08',headers=self.headers('manager')).get_json()
        values={row['tender']:row['recorded_cents'] for row in tenders['items']}
        self.assertEqual(values,{'alipay':5000,'card':60000,'cash':30000,'e_transfer':10000,'wechat':5000})
        self.assertEqual(self.client.get('/api/reports/tenders?store_code=MK',headers=self.headers('manager')).status_code,403)
        staff_today=self.client.get('/api/today?store_code=DT&business_date=2026-09-08',headers=self.headers()).get_json()
        self.assertNotIn('financial',staff_today['sections'])
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM payment_events WHERE event_type='refund'").fetchone()[0],1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM sale_returns').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM closing_snapshots').fetchone()[0],1)


if __name__=='__main__':
    import unittest;unittest.main()
