"""价格走势: POPCORE keeps every item's price changes, not only confirmed designs' (owner, 2026-11-14: "Yes,
every item"). The database records them itself, so every place that saves products.price is covered. Confirmed
designs keep design_prices as their one history."""
import sys
import time
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from support import IsolatedApiCase  # noqa: E402

import db  # noqa: E402
import merge_clover_products as merger  # noqa: E402


class ProductPriceHistoryTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.box = con.execute("""INSERT INTO products(sku, name_cn_en, jizhanming, price, stock_form)
                                      VALUES ('PM0100', 'Echo box', 'Echo box', 25, 'random_box')""").lastrowid
            self.design = con.execute("""INSERT INTO products(sku, name_cn_en, jizhanming, price, stock_form)
                                         VALUES ('PM0100-01', 'Echo · Sun', 'Echo Sun', 25, 'confirmed_design')""").lastrowid
            con.commit()

    def history(self, product_id):
        with closing(self.connect()) as con:
            return [tuple(row) for row in con.execute(
                'SELECT old_cents, new_cents, source FROM product_price_history WHERE product_id=? ORDER BY id',
                (product_id,))]

    def rerun_migration(self):
        with closing(self.connect()) as con:
            con.execute('DROP TRIGGER products_price_history_update')
            con.execute('DROP TRIGGER products_price_history_insert')
            con.execute('DROP TRIGGER products_price_history_delete')
            con.execute('DROP TABLE product_price_history')
            con.execute("DELETE FROM _migrations WHERE name='product_price_history'")
            con.commit()
        db.migrate_db()

    def test_migration_runs_twice_and_gives_existing_items_one_starting_point(self):
        with closing(self.connect()) as con:
            unpriced = con.execute("INSERT INTO products(sku, stock_form) VALUES ('PM0300', 'ordinary')").lastrowid
            con.commit()
        self.rerun_migration()
        with closing(self.connect()) as con:  # run again with the table there: nothing added twice
            con.execute("DELETE FROM _migrations WHERE name='product_price_history'")
            con.commit()
        db.migrate_db()
        db.migrate_db()
        self.assertEqual(self.history(self.box), [(None, 2500, 'start')])
        self.assertEqual(self.history(self.design), [])
        self.assertEqual(self.history(unpriced), [])
        self.assertEqual(self.history(self.product_id), [])  # the shared fixture's item has no price

    def test_a_price_change_through_the_products_edit_adds_one_row(self):
        response = self.client.patch(f'/api/products/{self.box}', json={'price': 27.99}, headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.history(self.box), [(None, 2500, 'insert'), (2500, 2799, 'update')])
        response = self.client.patch(f'/api/products/{self.box}', json={'price': 27.99, 'notes': 'same price'},
                                     headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(len(self.history(self.box)), 2)

    def test_direct_updates_record_only_real_changes(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET price=30 WHERE id=?', (self.box,))
            con.execute('UPDATE products SET price=30.0, notes=? WHERE id=?', ('same', self.box))
            con.execute("UPDATE products SET notes='no price' WHERE id=?", (self.box,))
            con.execute('UPDATE products SET price=19.99 WHERE id=?', (self.box,))
            con.execute('UPDATE products SET price=NULL WHERE id=?', (self.box,))
            con.commit()
            changed_at = con.execute('SELECT changed_at FROM product_price_history WHERE product_id=? ORDER BY id DESC',
                                     (self.box,)).fetchone()[0]
        self.assertEqual(self.history(self.box), [(None, 2500, 'insert'), (2500, 3000, 'update'),
                                                  (3000, 1999, 'update'), (1999, None, 'update')])
        self.assertRegex(changed_at, r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')

    def test_a_new_item_gets_its_first_point(self):
        response = self.client.post('/api/products', headers=self.headers('manager'),
                                    json={'name_cn_en': 'New item', 'sku': 'NEW-1', 'price': 12.5})
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(self.history(response.get_json()['id']), [(None, 1250, 'insert')])
        with closing(self.connect()) as con:
            unpriced = con.execute("INSERT INTO products(sku) VALUES ('NEW-2')").lastrowid
            con.commit()
        self.assertEqual(self.history(unpriced), [])
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET price=8 WHERE id=?', (unpriced,))
            con.commit()
        self.assertEqual(self.history(unpriced), [(None, 800, 'update')])

    def test_a_confirmed_designs_price_change_is_left_to_design_prices(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET price=40 WHERE id=?', (self.design,))
            con.commit()
        self.assertEqual(self.history(self.design), [])

    def test_deleting_an_item_takes_its_history_with_it(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET price=26 WHERE id=?', (self.box,))
            con.commit()
        response = self.client.post('/api/products/bulk_delete', json=[self.box], headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.history(self.box), [])
        with closing(self.connect()) as con:
            self.assertIsNone(con.execute('SELECT 1 FROM products WHERE id=?', (self.box,)).fetchone())

    def test_a_direct_delete_takes_its_history_and_the_table_has_no_foreign_key(self):
        # No foreign key on purpose: the generic "who points at products" counts never see this table.
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("PRAGMA foreign_key_list('product_price_history')").fetchall(), [])
            con.execute('UPDATE products SET price=26 WHERE id=?', (self.box,))
            con.execute('DELETE FROM products WHERE id=?', (self.box,))
            con.commit()
        self.assertEqual(self.history(self.box), [])

    def test_a_merge_keeps_the_survivors_history_and_none_of_the_removed_items(self):
        with closing(self.connect()) as con:
            legacy = con.execute("INSERT INTO products(sku, name_cn_en, price) VALUES ('OLD-7', 'Old Echo', 20)").lastrowid
            con.execute('UPDATE products SET price=21 WHERE id=?', (legacy,))
            con.execute("UPDATE products SET clover_item_name='Echo box Clover' WHERE id=?", (self.box,))
            con.execute('UPDATE products SET price=26 WHERE id=?', (self.box,))
            con.commit()
            results = merger.apply(con, [{'legacy_id': legacy, 'decision': str(self.box)}])
            self.assertEqual(results[0][1], 'ok', results)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM product_price_history WHERE product_id=?',
                                         (legacy,)).fetchone()[0], 0)
        self.assertEqual(self.history(self.box), [(None, 2500, 'insert'), (2500, 2600, 'update')])

    def test_removing_a_confirmed_design_is_not_blocked_by_its_earlier_history(self):
        # An item recorded before it was confirmed as a design keeps those rows; they never hold its removal back.
        with closing(self.connect()) as con:
            series = con.execute("INSERT INTO product_series(name, product_id) VALUES ('Echo', ?)", (self.box,)).lastrowid
            con.execute('UPDATE products SET series_id=? WHERE id=?', (series, self.box))
            moon = con.execute("""INSERT INTO products(sku, name_cn_en, jizhanming, price, stock_form, series_id)
                                  VALUES ('PM0100-02', 'Echo · Moon', 'Echo Moon', 25, NULL, ?)""", (series,)).lastrowid
            con.execute("UPDATE products SET stock_form='confirmed_design', design_name='Moon' WHERE id=?", (moon,))
            con.execute('UPDATE products SET series_id=? WHERE id=?', (series, self.design))
            con.commit()
        self.assertEqual(self.history(moon), [(None, 2500, 'insert')])
        for design in (moon, self.design):
            response = self.client.post(f'/api/products/{design}/design/remove', headers=self.headers('manager'))
            self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.history(moon), [])

    def test_older_code_keeps_working_on_the_migrated_database(self):
        # The triggers only insert into their own table: plain inserts, updates and deletes still succeed.
        with closing(self.connect()) as con:
            item = con.execute("INSERT INTO products(sku, price) VALUES ('OLD-1', 5)").lastrowid
            con.execute('UPDATE products SET price=6, name_cn_en=? WHERE id=?', ('Old', item))
            con.execute('DELETE FROM products WHERE id=?', (item,))
            con.commit()
            self.assertEqual(con.execute('SELECT COUNT(*) FROM product_price_history WHERE product_id=?',
                                         (item,)).fetchone()[0], 0)

    def test_the_migration_is_quick_on_a_catalogue_of_3000_items(self):
        with closing(self.connect()) as con:
            con.executemany("INSERT INTO products(sku, price, stock_form) VALUES (?, ?, ?)",
                            [(f'BULK{n:04d}', 10 + n % 50, 'confirmed_design' if n % 5 == 0 else 'random_box')
                             for n in range(3000)])
            con.commit()
            con.execute('DROP TRIGGER products_price_history_update')
            con.execute('DROP TRIGGER products_price_history_insert')
            con.execute('DROP TRIGGER products_price_history_delete')
            con.execute('DROP TABLE product_price_history')
            con.execute("DELETE FROM _migrations WHERE name='product_price_history'")
            con.commit()
            started = time.perf_counter()
            con.execute('BEGIN IMMEDIATE')
            db._migration_product_price_history(con, con.cursor())
            con.commit()
            seconds = time.perf_counter() - started
            starts = con.execute("SELECT COUNT(*) FROM product_price_history WHERE source='start'").fetchone()[0]
        self.assertEqual(starts, 2400 + 1)  # 3000 bulk items less 600 designs, plus PM0100
        print(f'\nproduct_price_history migration on 3,000 items: {seconds * 1000:.1f} ms')
        self.assertLess(seconds, 10)


if __name__ == '__main__':
    unittest.main()
