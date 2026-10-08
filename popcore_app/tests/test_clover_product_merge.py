"""Legacy products merge into the Clover master without losing references, names or stock facts."""
import csv
import sys
import tempfile
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from support import IsolatedApiCase  # noqa: E402
import import_clover_items as importer  # noqa: E402
import merge_clover_products as merger  # noqa: E402


class CloverProductMergeTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        rows = [{'clover_item_name': 'Test Product', 'price': '19.99', 'category': 'POPMART', 'clover_item_id': '', 'product_code': ''},
                {'clover_item_name': 'Sonny Angel Animal Series Ver.1 Figure', 'price': '15.99', 'category': 'SONNY ANGEL', 'clover_item_id': '', 'product_code': ''}]
        with closing(self.connect()) as con:
            importer.import_items(con, [{**r, 'price': float(r['price']), 'clover_item_id': None, 'product_code': None} for r in rows])
            self.clover_id = con.execute("SELECT id FROM products WHERE clover_item_name='Test Product'").fetchone()[0]
            self.other_id = con.execute("SELECT id FROM products WHERE clover_item_name LIKE 'Sonny%'").fetchone()[0]
            con.execute("UPDATE products SET jizhanming='测试产品', price=19.99, stock_form='ordinary', stock_unit='piece' WHERE id=?",
                        (self.product_id,))
            con.commit()

    def test_review_suggests_the_matching_clover_product(self):
        with closing(self.connect()) as con:
            rows = merger.review(con)
        self.assertEqual([r['legacy_id'] for r in rows], [self.product_id])
        row = rows[0]
        self.assertEqual((row['suggested_clover_id'], row['verdict'], row['decision'], row['references']),
                         (self.clover_id, 'confident', self.clover_id, 1))
        self.assertGreater(row['score'], row['runner_up_score'])

    def test_mixed_language_legacy_names_match_on_their_english_part(self):
        with closing(self.connect()) as con:
            importer.import_items(con, [
                {'clover_item_name': 'THE MONSTERS Lazy Yoga Series Figures', 'price': 26.99, 'category': 'POPMART', 'clover_item_id': None, 'product_code': None},
                {'clover_item_name': 'THE MONSTERS Lazy Yoga Series - Vinyl Plush Pendant', 'price': 34.99, 'category': 'POPMART', 'clover_item_id': None, 'product_code': None},
                {'clover_item_name': 'MEGA JUST DIMOO 400% Mickey Mouse', 'price': 299.0, 'category': 'POPMART', 'clover_item_id': None, 'product_code': None},
                {'clover_item_name': 'MEGA JUST DIMOO 1000% Mickey Mouse', 'price': 899.0, 'category': 'POPMART', 'clover_item_id': None, 'product_code': None},
            ])
            for sku, jzm, name, price in (('L-1', '慵懒瑜伽', 'Lazy Yoga 慵懒瑜伽 figure', 26.99),
                                          ('L-2', 'Dimoo 迪士尼 400%', 'Dimoo 迪士尼 400% Mega Just DIMOO Mickey Mouse', None),
                                          ('L-3', '毛球派对', '毛球派对', 119.0)):
                con.execute('INSERT INTO products(sku, jizhanming, name_cn_en, price) VALUES (?,?,?,?)', (sku, jzm, name, price))
            con.commit()
            rows = {r['legacy_sku']: r for r in merger.review(con)}
        self.assertEqual((rows['L-1']['verdict'], rows['L-1']['suggested_name']),
                         ('confident', 'THE MONSTERS Lazy Yoga Series Figures'))
        self.assertEqual((rows['L-2']['verdict'], rows['L-2']['suggested_name']),
                         ('confident', 'MEGA JUST DIMOO 400% Mickey Mouse'))
        self.assertEqual(rows['L-3']['verdict'], 'none')

    def test_merge_repoints_stock_adds_aliases_carries_identity_and_deletes_legacy(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO product_aliases(product_id, alias, alias_norm) VALUES (?, 'TP', 'tp')", (self.product_id,))
            con.execute("INSERT INTO product_barcodes(code, product_id, code_kind, input_unit, quantity_per_scan) VALUES ('111', ?, 'manufacturer', 'piece', 1)",
                        (self.product_id,))
            con.commit()
            results = merger.apply(con, [{'legacy_id': self.product_id, 'decision': str(self.clover_id)}])
            self.assertEqual(results[0][1], 'ok', results)
            self.assertIsNone(con.execute('SELECT 1 FROM products WHERE id=?', (self.product_id,)).fetchone())
            stock = con.execute('SELECT product_id, instore_qty FROM stock WHERE store_id=?', (self.store_id,)).fetchall()
            self.assertEqual([tuple(r) for r in stock], [(self.clover_id, 2)])
            product = con.execute('SELECT * FROM products WHERE id=?', (self.clover_id,)).fetchone()
            self.assertEqual((product['jizhanming'], product['stock_form'], product['stock_unit'], product['name_cn_en'], product['brand']),
                             ('测试产品', 'ordinary', 'piece', 'Test Product', 'POPMART'))
            self.assertIn('测试产品', product['search_blob'])
            aliases = sorted(r[0] for r in con.execute('SELECT alias_norm FROM product_aliases WHERE product_id=?', (self.clover_id,)))
            self.assertEqual(aliases, ['testproduct', 'tp', '测试产品'])
            self.assertEqual(con.execute('SELECT product_id FROM product_barcodes WHERE code=?', ('111',)).fetchone()[0], self.clover_id)
            # Applying the same sheet again reports the legacy row as gone and changes nothing.
            again = merger.apply(con, [{'legacy_id': self.product_id, 'decision': str(self.clover_id)}])
            self.assertEqual(again[0][1], 'refused')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM product_aliases').fetchone()[0], 3)

    def test_colliding_stock_is_refused_and_nothing_changes(self):
        with closing(self.connect()) as con:
            con.execute('INSERT INTO stock(product_id, store_id, upstairs_qty, instore_qty, claw_qty) VALUES (?, ?, 1, 1, 0)',
                        (self.clover_id, self.store_id))
            con.commit()
            before = self.snapshot(['products', 'stock', 'product_aliases'])
            results = merger.apply(con, [{'legacy_id': self.product_id, 'decision': str(self.clover_id)}])
            self.assertEqual(results[0][1], 'refused')
            self.assertIn('stock', results[0][2])
        self.assertEqual(self.snapshot(['products', 'stock', 'product_aliases']), before)

    def test_delete_only_removes_unreferenced_legacy_products_and_keep_is_a_noop(self):
        with closing(self.connect()) as con:
            spare = con.execute("INSERT INTO products(sku, name_cn_en) VALUES ('OLD-9', 'Spare')").lastrowid
            con.commit()
            results = merger.apply(con, [{'legacy_id': self.product_id, 'decision': 'delete'},
                                         {'legacy_id': spare, 'decision': 'delete'},
                                         {'legacy_id': self.other_id, 'decision': 'keep'},
                                         {'legacy_id': spare, 'decision': 'nonsense'}])
            self.assertEqual([r[1] for r in results], ['refused', 'ok', 'ok', 'refused'])
            self.assertIsNotNone(con.execute('SELECT 1 FROM products WHERE id=?', (self.product_id,)).fetchone())
            self.assertIsNone(con.execute('SELECT 1 FROM products WHERE id=?', (spare,)).fetchone())

    def test_review_round_trips_through_csv(self):
        with tempfile.TemporaryDirectory(dir=self.tempdir.name) as directory:
            path = Path(directory) / 'review.csv'
            with closing(self.connect()) as con:
                merger.write_review(merger.review(con), path)
                with open(path, encoding='utf-8') as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(list(rows[0]), list(merger.REVIEW_COLUMNS))
                results = merger.apply(con, rows, dry_run=True)
                self.assertEqual(results[0][1], 'ok')
                self.assertIsNotNone(con.execute('SELECT 1 FROM products WHERE id=?', (self.product_id,)).fetchone())
