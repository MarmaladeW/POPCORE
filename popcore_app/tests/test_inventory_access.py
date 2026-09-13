from contextlib import closing

from support import IsolatedApiCase


class InventoryAccessTests(IsolatedApiCase):
    def test_only_admin_can_read_grant_or_revoke(self):
        for method in ('GET', 'POST', 'DELETE'):
            for role in ('viewer', 'staff', 'manager'):
                response = self.client.open('/api/inventory/access', method=method,
                    headers=self.headers(role), json={'auth0_sub': 'auth0|staff', 'store_id': self.store_id})
                self.assertEqual(response.status_code, 403, (method, role))
            self.assertEqual(self.client.open('/api/inventory/access', method=method).status_code, 401)

    def test_admin_can_grant_self_and_revoke_one_store_without_affecting_others(self):
        headers = self.headers('admin')
        body = self.client.get('/api/inventory/access', headers=headers).get_json()
        self.assertEqual({store['code'] for store in body['stores']}, {'DT', 'MK'})
        self.assertEqual(body['grants'], [])
        mk_id = next(store['id'] for store in body['stores'] if store['code'] == 'MK')
        dt = {'auth0_sub': 'auth0|admin', 'store_id': self.store_id}
        for store_id in (self.store_id, mk_id):
            for _ in range(2):
                self.assertEqual(self.client.post('/api/inventory/access', headers=headers,
                    json={**dt, 'store_id': store_id}).status_code, 201)
        self.assertEqual(self.client.get('/api/today?store_code=DT', headers=headers).status_code, 200)
        for _ in range(2):
            self.assertEqual(self.client.delete('/api/inventory/access', headers=headers, json=dt).status_code, 200)
        grants = self.client.get('/api/inventory/access', headers=headers).get_json()['grants']
        self.assertEqual(grants, [{'auth0_sub': 'auth0|admin', 'store_id': mk_id}])
        self.assertEqual(self.client.get('/api/today?store_code=DT', headers=headers).status_code, 403)
        self.assertEqual(self.client.get('/api/today?store_code=MK', headers=headers).status_code, 200)

    def test_invalid_requests_and_inactive_stores_cannot_grant_access(self):
        headers = self.headers('admin')
        for method in ('POST', 'DELETE'):
            for body in ([], {}, {'auth0_sub': ' ', 'store_id': 1},
                         {'auth0_sub': 'auth0|staff', 'store_id': True},
                         {'auth0_sub': 'auth0|staff', 'store_id': '1'}):
                self.assertEqual(self.client.open('/api/inventory/access', method=method,
                    headers=headers, json=body).status_code, 400)
        with closing(self.connect()) as con:
            con.execute('UPDATE stores SET is_active=0 WHERE id=?', (self.store_id,))
            con.execute('INSERT INTO inventory_access(auth0_sub,store_id) VALUES (?,?)', ('auth0|staff', self.store_id))
            con.commit()
        body = {'auth0_sub': 'auth0|staff', 'store_id': self.store_id}
        self.assertEqual(self.client.post('/api/inventory/access', headers=headers, json=body).status_code, 404)
        self.assertEqual(self.client.delete('/api/inventory/access', headers=headers, json=body).status_code, 200)
