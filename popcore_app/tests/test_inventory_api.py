from datetime import date
from contextlib import closing

from tests.support import IsolatedApiCase


class InventoryApiTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)",
                ('auth0|staff', self.store_id),
            )
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)",
                ('auth0|manager', self.store_id),
            )
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)",
                ('auth0|admin', self.store_id),
            )
            con.execute(
                """UPDATE products SET stock_form='random_box', stock_unit='box',
                          identity_status='verified' WHERE id=?""",
                (self.product_id,),
            )
            self.floor_id = con.execute(
                "SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'",
                (self.store_id,),
            ).fetchone()[0]
            con.commit()

    def _opening(self, quantity=5):
        from inventory_commands import post_inventory
        with closing(self.connect()) as con:
            return post_inventory(con, {
                'kind': 'opening',
                'business_date': str(date.today()),
                'reason': 'reviewed opening',
                'lines': [{
                    'product_id': self.product_id,
                    'quantity': quantity,
                    'unit': 'box',
                    'to_location_id': self.floor_id,
                    'to_disposition': 'saleable',
                    'expected_versions': {'to': 0},
                }],
            }, actor={'sub': 'auth0|admin', 'https://popcore/role': 'admin'},
               request_key='api-fixture-opening')

    def test_identity_endpoint_returns_review_data(self):
        response = self.client.get(
            f'/api/products/{self.product_id}/inventory-identity',
            headers=self.headers('staff'),
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['stock_form'], 'random_box')
        self.assertEqual(body['stock_unit'], 'box')
        self.assertEqual(body['identity_status'], 'verified')
        self.assertEqual(body['barcodes'], [])
        self.assertEqual(body['conversions'], [])

    def test_public_command_rejects_opening_and_requires_request_key(self):
        payload = {'kind': 'opening', 'business_date': str(date.today()), 'lines': []}
        no_key = self.client.post(
            '/api/inventory/commands', json=payload, headers=self.headers('staff')
        )
        self.assertEqual(no_key.status_code, 400)
        self.assertEqual(no_key.get_json()['code'], 'idempotency_key_required')

        blocked = self.client.post(
            '/api/inventory/commands', json=payload,
            headers={**self.headers('staff'), 'Idempotency-Key': 'blocked-opening'},
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.get_json()['code'], 'opening_not_public')

    def test_post_balance_and_document_are_scoped(self):
        self._opening()
        command = {
            'kind': 'consume',
            'business_date': str(date.today()),
            'reason': 'customer sale',
            'lines': [{
                'product_id': self.product_id,
                'quantity': 2,
                'unit': 'box',
                'from_location_id': self.floor_id,
                'from_disposition': 'saleable',
                'expected_versions': {'from': 1},
            }],
        }
        headers = {**self.headers('staff'), 'Idempotency-Key': 'api-consume-1'}
        posted = self.client.post('/api/inventory/commands', json=command, headers=headers)
        self.assertEqual(posted.status_code, 201)
        result = posted.get_json()
        replay = self.client.post('/api/inventory/commands', json=command, headers=headers)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.get_json(), result)

        balances = self.client.get(
            '/api/inventory/balances?store_code=DT', headers=self.headers('staff')
        )
        self.assertEqual(balances.status_code, 200)
        row = balances.get_json()['items'][0]
        self.assertEqual(row['quantity'], 3)
        self.assertEqual(row['unit'], 'box')
        self.assertTrue(row['opening_verified'])

        document = self.client.get(
            f"/api/inventory/documents/{result['document_id']}",
            headers=self.headers('staff'),
        )
        self.assertEqual(document.status_code, 200)
        self.assertEqual(document.get_json()['kind'], 'consume')
        self.assertEqual(len(document.get_json()['movements']), 1)

        denied = self.client.get(
            '/api/inventory/balances?store_code=DT', headers=self.headers('viewer')
        )
        self.assertEqual(denied.status_code, 403)


if __name__ == '__main__':
    import unittest
    unittest.main()
