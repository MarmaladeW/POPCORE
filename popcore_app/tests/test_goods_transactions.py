import unittest
import sqlite3
from contextlib import closing

from support import IsolatedApiCase

from inventory_commands import (
    InventoryConflict,
    _post_inventory_in_transaction,
    post_inventory,
)


class GoodsTransactionTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.actor = {
            'sub': 'auth0|staff',
            'https://popcore/role': 'staff',
        }
        with closing(self.connect()) as con:
            con.execute(
                """UPDATE products SET stock_form='ordinary', stock_unit='piece',
                          identity_status='verified' WHERE id=?""",
                (self.product_id,),
            )
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)",
                (self.actor['sub'], self.store_id),
            )
            locations = con.execute(
                "SELECT id, code FROM inventory_locations WHERE store_id=?",
                (self.store_id,),
            ).fetchall()
            self.locations = {row['code']: row['id'] for row in locations}
            con.execute("UPDATE inventory_mode SET mode='authoritative' WHERE id=1")
            con.execute(
                "UPDATE inventory_scope_state SET opening_verified=1 WHERE store_id=?",
                (self.store_id,),
            )
            con.execute(
                """INSERT INTO inventory_balances
                   (product_id, location_id, disposition, quantity, version)
                   VALUES (?, ?, 'saleable', 5, 1)""",
                (self.product_id, self.locations['upstairs']),
            )
            con.commit()

    def move_payload(self):
        return {
            'kind': 'move',
            'business_date': '2026-09-08',
            'reason': 'Goods transaction fixture',
            'lines': [{
                'product_id': self.product_id,
                'quantity': 2,
                'unit': 'piece',
                'from_location_id': self.locations['upstairs'],
                'from_disposition': 'saleable',
                'to_location_id': self.locations['upstairs'],
                'to_disposition': 'transit',
                'expected_versions': {'from': 1, 'to': 0},
            }],
        }

    def test_nested_post_rolls_back_with_workflow_failure(self):
        with self.assertRaises(sqlite3.OperationalError):
            with closing(self.connect()) as con:
                before = [tuple(row) for row in con.execute(
                    'SELECT * FROM inventory_balances ORDER BY 1,2,3'
                )]
                con.execute('BEGIN IMMEDIATE')
                _post_inventory_in_transaction(
                    con, self.move_payload(), actor=self.actor,
                    request_key='goods-nested-1', allow_transit=True,
                )
                con.execute('INSERT INTO missing_workflow_table VALUES (1)')

        with closing(self.connect()) as con:
            after = [tuple(row) for row in con.execute(
                'SELECT * FROM inventory_balances ORDER BY 1,2,3'
            )]
            documents = con.execute(
                "SELECT COUNT(*) FROM inventory_documents WHERE request_key='goods-nested-1'"
            ).fetchone()[0]
        self.assertEqual(after, before)
        self.assertEqual(documents, 0)

    def test_standalone_command_cannot_create_unowned_transit(self):
        with closing(self.connect()) as con:
            with self.assertRaises(InventoryConflict) as raised:
                post_inventory(
                    con, self.move_payload(), actor=self.actor,
                    request_key='unowned-transit-1',
                )
        self.assertEqual(raised.exception.code, 'delivery_context_required')


if __name__ == '__main__':
    unittest.main()
