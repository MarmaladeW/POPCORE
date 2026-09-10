from contextlib import closing

from support import IsolatedApiCase


class TodayPermissionTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.mk_id = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            con.execute("INSERT OR IGNORE INTO inventory_access(auth0_sub,store_id) VALUES (?,?)", ('auth0|staff', self.store_id))
            con.execute("INSERT OR IGNORE INTO inventory_access(auth0_sub,store_id) VALUES (?,?)", ('auth0|manager', self.store_id))
            con.execute("INSERT OR IGNORE INTO inventory_access(auth0_sub,store_id) VALUES (?,?)", ('auth0|admin', self.store_id))
            con.execute("INSERT INTO sale_documents(store_id,business_date,entry_mode,created_by) VALUES (?,'2026-09-08','planned_entry',?)", (self.store_id,'auth0|staff'))
            con.execute("INSERT INTO sale_documents(store_id,business_date,entry_mode,created_by) VALUES (?,'2026-09-08','planned_entry',?)", (self.mk_id,'auth0|other'))
            con.commit()

    def get_today(self, token='staff', store='ALL'):
        return self.client.get(f'/api/today?store_code={store}&business_date=2026-09-08', headers=self.headers(token))

    def test_four_roles_receive_separate_safe_projections(self):
        viewer = self.get_today('viewer').get_json()
        staff = self.get_today('staff').get_json()
        manager = self.get_today('manager').get_json()
        admin = self.get_today('admin').get_json()
        self.assertEqual(viewer['store_ids'], [])
        self.assertEqual(staff['store_ids'], [self.store_id])
        self.assertEqual(manager['store_ids'], [self.store_id])
        self.assertEqual(admin['store_ids'], [self.store_id])
        self.assertEqual(staff['role'], 'staff')
        self.assertTrue(any(r['source_id'] == 1 for r in staff['sections']['my_work']['rows']))
        for payload in (viewer, staff):
            raw = str(payload)
            self.assertNotIn('tender_totals_cents', raw)
            self.assertNotIn('cash_variance_cents', raw)
            self.assertNotIn('evidence_id', raw)
        self.assertIn('financial', manager['sections'])
        self.assertIn('catalog', admin['sections'])

    def test_specific_store_and_all_never_expand_scope(self):
        self.assertEqual(self.get_today('staff', 'DT').status_code, 200)
        self.assertEqual(self.get_today('staff', 'MK').status_code, 403)
        all_payload = self.get_today('staff').get_json()
        self.assertEqual(all_payload['store_ids'], [self.store_id])
        self.assertNotIn(self.mk_id, [r.get('store_id') for section in all_payload['sections'].values() for r in section['rows']])

    def test_revoked_and_empty_access_return_no_operations(self):
        with closing(self.connect()) as con:
            con.execute("DELETE FROM inventory_access WHERE auth0_sub='auth0|staff'")
            con.commit()
        payload = self.get_today('staff').get_json()
        self.assertEqual(payload['store_ids'], [])
        self.assertEqual(payload['sections']['my_work']['rows'], [])

    def test_role_is_taken_from_verified_token_and_auth_is_required(self):
        forged = self.client.get('/api/today?store_code=ALL&role=admin', headers=self.headers('staff')).get_json()
        self.assertEqual(forged['role'], 'staff')
        self.assertNotIn('financial', forged['sections'])
        self.assertEqual(self.client.get('/api/today').status_code, 401)
