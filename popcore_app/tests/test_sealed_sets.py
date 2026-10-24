import io
import sqlite3
import sys
from contextlib import closing, redirect_stdout
from pathlib import Path

from support import APP_DIR, IsolatedApiCase, db

ROOT = APP_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import identity_sheet  # noqa: E402
from inventory_opening import count_sheet_rows  # noqa: E402
from sealed_sets import SealedSetError, apply_sealed_sets, plan_sealed_sets  # noqa: E402
from scripts.create_sealed_sets import main as _sealed_cli  # noqa: E402


def sealed_cli(argv):
    with redirect_stdout(io.StringIO()):
        return _sealed_cli(argv)


class SealedSetTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.folder = Path(self.tempdir.name)
        with closing(self.connect()) as con:
            self.dt = con.execute("SELECT id FROM stores WHERE code='DT'").fetchone()[0]
            self.box = self._product(con, 'BOX-1', '哭娃度假', 'Crybaby Vacation', '盲盒', 12, 17.7,
                                     'random_box', 'box', 'verified')
            self.waiting = self._product(con, 'BOX-2', '未核对盲盒', 'Waiting Box', '盲盒', 9, 20,
                                         None, None, 'unverified')
            self.blister = self._product(con, 'CARD-1', '吊卡', 'Blister', '吊卡', None, 15,
                                         None, None, 'unverified')
            self.loose = self._product(con, 'BOX-3', '散盒', 'Loose Box', '盲盒', None, 18,
                                       'random_box', 'box', 'verified')
            con.execute('INSERT INTO stock(product_id, store_id, instore_qty, upstairs_qty) VALUES (?,?,3,24)',
                        (self.box, self.dt))
            con.commit()

    @staticmethod
    def _product(con, sku, jizhanming, name, product_type, boxes, price, form, unit, status):
        return con.execute(
            """INSERT INTO products (sku, jizhanming, name_cn_en, product_type, boxes_per_dan, price,
                                     stock_form, stock_unit, identity_status)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (sku, jizhanming, name, product_type, boxes, price, form, unit, status)).lastrowid

    def test_plan_lists_verified_blind_boxes_and_what_waits(self):
        with closing(self.connect()) as con:
            report = plan_sealed_sets(con)
        self.assertTrue(report['ready_to_apply'])
        mine = [item for item in report['items'] if item['box_sku'].startswith('BOX-')]
        self.assertEqual([item['box_product_id'] for item in mine], [self.box])
        item = mine[0]
        self.assertEqual((item['set_sku'], item['boxes_per_set'], item['set_price'], item['set_jizhanming']),
                         ('BOX-1-SET', 12, 212.4, '哭娃度假 整端'))
        self.assertTrue(item['new_series'])
        waiting = {row['product_id'] for row in report['waiting']}
        self.assertIn(self.waiting, waiting)
        self.assertNotIn(self.loose, waiting)  # no boxes-per-set count, so nothing to create

    def test_apply_creates_a_linked_verified_sealed_set_behind_a_backup(self):
        backup = self.folder / 'before-sealed-sets.db'
        report = apply_sealed_sets(db.DB_PATH, backup_path=str(backup))
        self.assertTrue(report['applied'])
        self.assertTrue(backup.exists())
        with closing(sqlite3.connect(backup)) as old:
            self.assertEqual(old.execute("SELECT COUNT(*) FROM products WHERE sku='BOX-1-SET'").fetchone()[0], 0)
        set_id = next(item['set_product_id'] for item in report['items'] if item['box_sku'] == 'BOX-1')
        with closing(self.connect()) as con:
            made = con.execute('SELECT * FROM products WHERE id=?', (set_id,)).fetchone()
            self.assertEqual((made['sku'], made['stock_form'], made['stock_unit'], made['identity_status']),
                             ('BOX-1-SET', 'sealed_set', 'set', 'verified'))
            box = con.execute('SELECT series_id FROM products WHERE id=?', (self.box,)).fetchone()
            self.assertEqual(made['series_id'], box['series_id'])
            self.assertEqual(con.execute('SELECT name FROM product_series WHERE id=?',
                                         (made['series_id'],)).fetchone()[0], '哭娃度假')
            conversion = con.execute(
                'SELECT source_product_id, target_product_id, output_per_input FROM product_conversions'
            ).fetchall()
            self.assertIn((set_id, self.box, 12), [tuple(row) for row in conversion])
            # The sealed set is listed right after its blind box on every location's count sheet.
            for location in ('floor', 'upstairs', 'display'):
                skus = [row['sku'] for row in count_sheet_rows(con, 'DT', location) if row['sku'].startswith('BOX-')]
                self.assertEqual(skus, ['BOX-1', 'BOX-1-SET'], location)
            self.assertEqual(plan_sealed_sets(con)['items'], [])

    def test_taken_sku_blocks_apply_and_writes_nothing(self):
        with closing(self.connect()) as con:
            self._product(con, 'BOX-1-SET', 'x', 'x', '盲盒', None, None, None, None, 'unverified')
            con.commit()
        before = self.snapshot(('products', 'product_series', 'product_conversions'))
        report = apply_sealed_sets(db.DB_PATH, backup_path=str(self.folder / 'unused.db'))
        self.assertFalse(report['applied'])
        self.assertEqual([error['code'] for error in report['errors']], ['sku_taken'])
        self.assertEqual(self.snapshot(('products', 'product_series', 'product_conversions')), before)
        self.assertFalse((self.folder / 'unused.db').exists())
        with self.assertRaises(SealedSetError):
            apply_sealed_sets(db.DB_PATH, backup_path=None)

    def test_cli_defaults_to_a_read_only_dry_run(self):
        before = self.snapshot(('products', 'product_series', 'product_conversions'))
        report = sealed_cli(['--db', db.DB_PATH])
        self.assertTrue(report['dry_run'])
        self.assertEqual(self.snapshot(('products', 'product_series', 'product_conversions')), before)
        with self.assertRaises(SystemExit):
            sealed_cli(['--db', db.DB_PATH, '--apply'])
        applied = sealed_cli(['--db', db.DB_PATH, '--apply', '--backup', str(self.folder / 'cli.db')])
        self.assertTrue(applied['applied'])

    def test_identity_sheet_prefills_blank_rows_from_product_type(self):
        with closing(self.connect()) as con:
            text, _ = identity_sheet.export_csv(con, suggest=True)
            plain, _ = identity_sheet.export_csv(con)
        rows = {line.split(',')[1]: line.split(',') for line in text.splitlines()[1:]}
        self.assertEqual(rows['BOX-2'][7:9], ['random_box', 'box'])
        self.assertEqual(rows['CARD-1'][7:9], ['ordinary', 'piece'])
        plain_rows = {line.split(',')[1]: line.split(',') for line in plain.splitlines()[1:]}
        self.assertEqual(plain_rows['CARD-1'][7:9], ['', ''])
        # Rows that already carry an identity keep it.
        self.assertEqual(rows['BOX-1'][7:9], ['random_box', 'box'])
