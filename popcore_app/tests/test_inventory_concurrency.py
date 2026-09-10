from contextlib import closing
from datetime import date
from threading import Barrier, Thread
from unittest.mock import patch

import inventory_commands
from inventory_commands import InventoryBusy, InventoryConflict, post_inventory
from support import IsolatedApiCase


class InventoryConcurrencyTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            for role in ('staff', 'admin'):
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
            con.commit()
        with closing(self.connect()) as con:
            post_inventory(con, {
                'kind': 'opening', 'business_date': str(date.today()),
                'reason': 'reviewed opening', 'lines': [{
                    'product_id': self.product_id, 'quantity': 5, 'unit': 'box',
                    'to_location_id': self.floor, 'to_disposition': 'saleable',
                    'expected_versions': {'to': 0},
                }],
            }, actor=self.actor('admin'), request_key='concurrency-opening')

    @staticmethod
    def actor(role='staff'):
        return {'sub': f'auth0|{role}', 'https://popcore/role': role}

    def consume(self):
        return {'kind': 'consume', 'business_date': str(date.today()),
                'reason': 'test consume', 'lines': [{
                    'product_id': self.product_id, 'quantity': 4, 'unit': 'box',
                    'from_location_id': self.floor,
                    'from_disposition': 'saleable',
                    'expected_versions': {'from': 1},
                }]}

    def run_pair(self, keys):
        barrier = Barrier(2)
        results = []
        def worker(key):
            with closing(self.connect()) as con:
                barrier.wait()
                try:
                    results.append(('ok', post_inventory(
                        con, self.consume(), actor=self.actor(), request_key=key)))
                except InventoryConflict as exc:
                    results.append((exc.code, None))
        threads = [Thread(target=worker, args=(key,)) for key in keys]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        return results

    def test_two_consumers_cannot_spend_the_same_version(self):
        results = self.run_pair(('consume-a', 'consume-b'))
        self.assertEqual(sum(status == 'ok' for status, _ in results), 1)
        self.assertIn(next(status for status, _ in results if status != 'ok'),
                      {'stale_version', 'insufficient_stock'})
        with closing(self.connect()) as con:
            quantity = con.execute(
                """SELECT quantity FROM inventory_balances
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (self.product_id, self.floor),
            ).fetchone()[0]
            self.assertEqual(quantity, 1)

    def test_same_key_concurrent_requests_have_one_effect(self):
        results = self.run_pair(('same-key', 'same-key'))
        self.assertEqual([status for status, _ in results], ['ok', 'ok'])
        self.assertEqual(results[0][1], results[1][1])
        with closing(self.connect()) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM inventory_documents WHERE request_key='same-key'"
            ).fetchone()[0], 1)

    def test_busy_then_same_key_retry_and_lost_response_recovery(self):
        held = self.connect()
        held.execute('BEGIN IMMEDIATE')
        try:
            with patch.object(inventory_commands, 'INVENTORY_BUSY_TIMEOUT_MS', 100):
                with closing(self.connect()) as blocked:
                    with self.assertRaises(InventoryBusy):
                        post_inventory(blocked, self.consume(), actor=self.actor(),
                                       request_key='busy-retry')
        finally:
            held.rollback()
            held.close()
        with closing(self.connect()) as con:
            first = post_inventory(con, self.consume(), actor=self.actor(),
                                   request_key='busy-retry')
        with closing(self.connect()) as restarted:
            replay = post_inventory(restarted, self.consume(), actor=self.actor(),
                                    request_key='busy-retry')
        self.assertEqual(replay, first)

    def test_compatibility_failure_rolls_back_document_and_balance(self):
        with closing(self.connect()) as con:
            con.execute("""CREATE TRIGGER fail_compat BEFORE INSERT ON stock_transactions
                           BEGIN SELECT RAISE(ABORT, 'compat failed'); END""")
            con.commit()
            with self.assertRaises(Exception):
                post_inventory(con, self.consume(), actor=self.actor(),
                               request_key='compat-failure')
        with closing(self.connect()) as con:
            self.assertIsNone(con.execute(
                "SELECT 1 FROM inventory_documents WHERE request_key='compat-failure'"
            ).fetchone())
            self.assertEqual(con.execute(
                """SELECT quantity FROM inventory_balances
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (self.product_id, self.floor),
            ).fetchone()[0], 5)


if __name__ == '__main__':
    import unittest
    unittest.main()
