"""The one-time catalogue cutover applies the master list in one transaction and refuses stale plans."""
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from support import IsolatedApiCase  # noqa: E402
import apply_master_catalogue as cutover  # noqa: E402
import import_clover_items as importer  # noqa: E402


class ApplyMasterCatalogueTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            importer.import_items(con, [
                {'clover_item_name': name, 'price': 19.99, 'category': 'POPMART', 'clover_item_id': None, 'product_code': None}
                for name in ('Test Product', 'Duplicate Item', 'Charm - Red', 'Charm - Blue')])
            self.ids = {r['clover_item_name']: r['id'] for r in con.execute(
                'SELECT id, clover_item_name FROM products WHERE clover_item_name IS NOT NULL')}
            # sheet_ref is unique and carried by the merge
            con.execute("UPDATE products SET jizhanming='测试产品', sheet_ref='SP00001' WHERE id=?", (self.product_id,))
            self.kept_id = con.execute("INSERT INTO products(sku, jizhanming, name_cn_en) VALUES ('SP00900', '老名', 'Old kept')").lastrowid
            self.fold_id = con.execute("INSERT INTO products(sku, jizhanming, name_cn_en) VALUES ('SP00901', '放学后', 'Old Merodi')").lastrowid
            self.undecided_id = con.execute("INSERT INTO products(sku, jizhanming, name_cn_en) VALUES ('SP00902', '未定', 'Undecided')").lastrowid
            con.execute("INSERT INTO clover_match_reviews(legacy_product_id, legacy_sku, legacy_jizhanming, legacy_name, decision, target_product_id) "
                        "SELECT id, sku, jizhanming, name_cn_en, 'merge', ? FROM products WHERE id=?", (self.ids['Test Product'], self.product_id))
            con.execute("INSERT INTO product_aliases(product_id, alias, alias_norm) VALUES (?, '新名', '新名')", (self.undecided_id,))
            for pid, qty in ((self.product_id, 1), (self.ids['Test Product'], 2)):
                con.execute("INSERT INTO daily_sales(product_id, date, store, qty_sold, qty_pos, qty_cash) VALUES (?, '2026-05-17', 'DT', ?, 0, ?)",
                            (pid, qty, qty))
            self.total = con.execute('SELECT COUNT(*) FROM products').fetchone()[0]
            self.legacy_sku = con.execute('SELECT sku FROM products WHERE id=?', (self.product_id,)).fetchone()[0]
            con.commit()

    def plan(self, **changes):
        tp, dup = self.ids['Test Product'], self.ids['Duplicate Item']
        plan = {
            'snapshot_products': self.total,
            'reviews': [[self.product_id, 'merge', tp]],
            'products': [
                {'ref': f'id:{tp}', 'name': 'Test Product', 'sku': 'PM0001', 'jizhanming': '新名', 'aliases': ['别名'],
                 'price': 19.99, 'category': 'Pop Mart', 'parent': None, 'design_name': None},
                {'ref': f'id:{self.kept_id}', 'name': 'Old kept', 'sku': 'PM0002', 'jizhanming': '老名', 'aliases': [],
                 'price': None, 'category': 'Pop Mart', 'parent': None, 'design_name': None},
                {'ref': 'dt:Merodi Figures', 'name': 'Merodi Figures', 'sku': 'PM0003', 'jizhanming': '放学后', 'aliases': [],
                 'price': None, 'category': 'Pop Mart', 'parent': None, 'design_name': None},
                {'ref': 'new:Charm', 'name': 'Charm', 'sku': 'PM0004', 'jizhanming': '挂件', 'aliases': [],
                 'price': None, 'category': 'Pop Mart', 'parent': None, 'design_name': None},
                {'ref': f"id:{self.ids['Charm - Red']}", 'name': 'Charm - Red', 'sku': 'PM0004-01', 'jizhanming': '挂件 Red',
                 'aliases': [], 'price': 19.99, 'category': 'Pop Mart', 'parent': 'new:Charm', 'design_name': 'Red'},
            ],
            'folds': [{'from': f'id:{self.fold_id}', 'to': 'dt:Merodi Figures'}],
            'removed': [{'ref': f'id:{dup}', 'name': 'Duplicate Item', 'refs': 0}],
        }
        plan.update(changes)
        return plan

    def run_plan(self, plan):
        with closing(self.connect()) as con:
            con.isolation_level = None
            con.execute('BEGIN IMMEDIATE')
            try:
                counts = cutover.apply_plan(con, plan)
            except Exception:
                con.execute('ROLLBACK')
                raise
            con.execute('COMMIT')
        return counts

    def test_applies_merges_creates_renames_folds_and_attaches_designs(self):
        counts = self.run_plan(self.plan())
        self.assertEqual((counts['reviews merged'], counts['created (Downtown items)'], counts['folded'], counts['removed'],
                          counts['designs attached']), (1, 1, 1, 1, 1))
        with closing(self.connect()) as con:
            tp = con.execute('SELECT sku, jizhanming FROM products WHERE id=?', (self.ids['Test Product'],)).fetchone()
            self.assertEqual(tuple(tp), ('PM0001', '新名'))
            aliases = {r[0] for r in con.execute('SELECT alias FROM product_aliases WHERE product_id=?', (self.ids['Test Product'],))}
            self.assertTrue({'别名', '测试产品', 'CL00001'} <= aliases, aliases)
            # the old sku and 记账名 of a renamed product stay searchable
            self.assertIn('SP00900', {r[0] for r in con.execute('SELECT alias FROM product_aliases WHERE product_id=?', (self.kept_id,))})
            # an alias elsewhere that equals a new 记账名 is dropped; the undecided product is otherwise untouched
            self.assertIsNone(con.execute("SELECT 1 FROM product_aliases WHERE product_id=?", (self.undecided_id,)).fetchone())
            self.assertEqual(con.execute('SELECT sku FROM products WHERE id=?', (self.undecided_id,)).fetchone()[0], 'SP00902')
            merodi = con.execute("SELECT id, sku, clover_item_name FROM products WHERE name_cn_en='Merodi Figures'").fetchone()
            self.assertEqual((merodi['sku'], merodi['clover_item_name']), ('PM0003', 'Merodi Figures'))
            self.assertIsNone(con.execute('SELECT 1 FROM products WHERE id IN (?, ?)', (self.fold_id, self.ids['Duplicate Item'])).fetchone())
            red = con.execute('SELECT series_id, stock_form, design_name FROM products WHERE sku=?', ('PM0004-01',)).fetchone()
            owner = con.execute('SELECT p.sku FROM product_series s JOIN products p ON p.id=s.product_id WHERE s.id=?', (red['series_id'],)).fetchone()
            self.assertEqual((red['stock_form'], red['design_name'], owner['sku']), ('confirmed_design', 'Red', 'PM0004'))
            # same-day sales of the merged products add up
            sold = con.execute("SELECT qty_sold, qty_cash FROM daily_sales WHERE date='2026-05-17'").fetchall()
            self.assertEqual([tuple(r) for r in sold], [(3, 3)])
            review = con.execute('SELECT applied_by FROM clover_match_reviews WHERE legacy_product_id=?', (self.product_id,)).fetchone()
            self.assertEqual(review['applied_by'], 'catalogue-cutover')

    def test_a_stale_plan_changes_nothing(self):
        with self.assertRaises(cutover.Refused):
            self.run_plan(self.plan(reviews=[[self.product_id, 'merge', self.ids['Duplicate Item']]]))
        with self.assertRaises(cutover.Refused):
            self.run_plan(self.plan(snapshot_products=self.total + 1))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM products').fetchone()[0], self.total)
            self.assertEqual(con.execute('SELECT sku FROM products WHERE id=?', (self.product_id,)).fetchone()[0], self.legacy_sku)
