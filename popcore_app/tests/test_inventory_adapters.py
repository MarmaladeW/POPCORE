from contextlib import closing
from datetime import date

from support import IsolatedApiCase
from inventory_commands import InventoryConflict, post_inventory


class InventoryAdapterTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            for role in ('staff', 'manager', 'admin'):
                con.execute(
                    'INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)',
                    (f'auth0|{role}', self.store_id),
                )
            con.execute(
                """UPDATE products SET stock_form='random_box', stock_unit='box',
                          identity_status='verified' WHERE id=?""", (self.product_id,)
            )
            self.floor = con.execute(
                "SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'",
                (self.store_id,),
            ).fetchone()[0]
            self.back = con.execute(
                "SELECT id FROM inventory_locations WHERE store_id=? AND code='upstairs'",
                (self.store_id,),
            ).fetchone()[0]
            con.commit()
        with closing(self.connect()) as con:
            post_inventory(con, {
                'kind': 'opening', 'business_date': str(date.today()),
                'reason': 'reviewed opening', 'lines': [
                    {'product_id': self.product_id, 'quantity': 2, 'unit': 'box',
                     'to_location_id': self.floor, 'to_disposition': 'saleable',
                     'expected_versions': {'to': 0}},
                    {'product_id': self.product_id, 'quantity': 10, 'unit': 'box',
                     'to_location_id': self.back, 'to_disposition': 'saleable',
                     'expected_versions': {'to': 0}},
                ],
            }, actor={'sub': 'auth0|admin', 'https://popcore/role': 'admin'},
               request_key='adapter-opening')

    def test_legacy_move_route_uses_one_idempotent_document(self):
        payload = {'product_id': self.product_id, 'qty': 3,
                   'date': str(date.today()), 'store_code': 'DT'}
        headers = {**self.headers('staff'), 'Idempotency-Key': 'adapter-move-1'}
        first = self.client.post('/api/stock/ru_dian', json=payload, headers=headers)
        second = self.client.post('/api/stock/ru_dian', json=payload, headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.get_json(), first.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM inventory_documents WHERE request_key='adapter-move-1'"
            ).fetchone()[0], 1)
            row = con.execute(
                'SELECT upstairs_qty, instore_qty FROM stock WHERE product_id=? AND store_id=?',
                (self.product_id, self.store_id),
            ).fetchone()
            self.assertEqual((row['upstairs_qty'], row['instore_qty']), (7, 5))

    def test_ambiguous_legacy_writers_are_blocked(self):
        claw = self.client.post('/api/stock/batch_operation', json={
            'operation': 'ru_dian_claw', 'date': str(date.today()),
            'store_code': 'DT',
            'items': [{'product_id': self.product_id, 'qty': 1}],
        }, headers={**self.headers('staff'), 'Idempotency-Key': 'claw-1'})
        self.assertEqual(claw.status_code, 409)
        self.assertEqual(claw.get_json()['code'], 'migration_required')

        report = self.client.post('/api/sales/submit_daily_report', json={
            'date': str(date.today()), 'store_code': 'DT',
            'items': [{'product_id': self.product_id, 'section': 'stock_out',
                       'qty': 1}],
        }, headers=self.headers('staff'))
        self.assertEqual(report.status_code, 409)
        self.assertEqual(report.get_json()['code'], 'migration_required')

    def test_batch_adapter_reuses_versions_for_lost_response_replay(self):
        payload = {'operation': 'restock_upstairs', 'date': str(date.today()),
                   'store_code': 'DT',
                   'items': [{'product_id': self.product_id, 'qty': 2}]}
        headers = {**self.headers('staff'), 'Idempotency-Key': 'batch-replay-1'}
        first = self.client.post('/api/stock/batch_operation', json=payload,
                                 headers=headers)
        replay = self.client.post('/api/stock/batch_operation', json=payload,
                                  headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.get_json(), first.get_json())

    def test_staff_cannot_use_authoritative_absolute_adjustment(self):
        response = self.client.post('/api/stock/adjust', json={
            'product_id': self.product_id, 'new_qty': 4, 'location': 'instore',
            'notes': 'count correction', 'date': str(date.today()),
            'store_code': 'DT',
        }, headers={**self.headers('staff'), 'Idempotency-Key': 'adjust-1'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'inventory_forbidden')

    def test_authoritative_history_prevents_stock_row_deletion(self):
        response = self.client.delete('/api/stock/rows', json={
            'store_code': 'DT', 'product_ids': [self.product_id],
        }, headers=self.headers('manager'))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'stock_row_retained')

    def test_authoritative_summary_keeps_native_units_separate(self):
        response = self.client.get(
            '/api/stock/summary?store_code=DT', headers=self.headers('staff')
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['mode'], 'authoritative')
        self.assertIsNone(body['total_upstairs_qty'])
        self.assertEqual(body['unit_totals'], [
            {'unit': 'box', 'floor_qty': 2, 'back_qty': 10, 'total_qty': 12}
        ])

    def test_restock_completion_and_session_transition_are_one_effect(self):
        with closing(self.connect()) as con:
            sid = con.execute(
                """INSERT INTO restock_sessions(date, status, store_id)
                   VALUES (?, 'picking', ?)""", (str(date.today()), self.store_id)
            ).lastrowid
            con.execute(
                """INSERT INTO restock_items
                   (session_id, product_id, requested_qty, found_qty, pick_status)
                   VALUES (?, ?, 3, 3, 'found')""", (sid, self.product_id)
            )
            con.commit()
        first = self.client.post(
            f'/api/restock/session/{sid}/complete', headers=self.headers('staff')
        )
        replay = self.client.post(
            f'/api/restock/session/{sid}/complete', headers=self.headers('staff')
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.get_json(), first.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute(
                'SELECT status FROM restock_sessions WHERE id=?', (sid,)
            ).fetchone()[0], 'completed')
            self.assertEqual(con.execute(
                """SELECT COUNT(*) FROM inventory_documents
                   WHERE source_type='restock_session' AND source_id=?""", (str(sid),)
            ).fetchone()[0], 1)

    def test_exact_full_reversal_can_be_posted_only_once(self):
        with closing(self.connect()) as con:
            moved = post_inventory(con, {
                'kind': 'move', 'business_date': str(date.today()),
                'reason': 'test move', 'lines': [{
                    'product_id': self.product_id, 'quantity': 3, 'unit': 'box',
                    'from_location_id': self.back, 'from_disposition': 'saleable',
                    'to_location_id': self.floor, 'to_disposition': 'saleable',
                    'expected_versions': {'from': 1, 'to': 1},
                }],
            }, actor={'sub': 'auth0|staff', 'https://popcore/role': 'staff'},
               request_key='reversal-move')
            inverse = {
                'kind': 'correction', 'business_date': str(date.today()),
                'reason': 'reverse reviewed move',
                'correction_of': moved['document_id'], 'lines': [{
                    'product_id': self.product_id, 'quantity': 3, 'unit': 'box',
                    'from_location_id': self.floor, 'from_disposition': 'saleable',
                    'to_location_id': self.back, 'to_disposition': 'saleable',
                    'expected_versions': {'from': 2, 'to': 2},
                }],
            }
            post_inventory(
                con, inverse,
                actor={'sub': 'auth0|manager', 'https://popcore/role': 'manager'},
                request_key='reversal-one',
            )
            with self.assertRaises(InventoryConflict) as raised:
                post_inventory(
                    con, inverse,
                    actor={'sub': 'auth0|manager', 'https://popcore/role': 'manager'},
                    request_key='reversal-two',
                )
            self.assertEqual(raised.exception.code, 'already_reversed')


if __name__ == '__main__':
    import unittest
    unittest.main()
