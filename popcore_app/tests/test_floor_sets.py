"""scripts/open_floor_sets.py: sealed sets sitting on a floor become loose boxes there (a floor holds blind
boxes as boxes only). The dry run writes nothing; --apply backs up first and changes only those sets."""
import io
import sqlite3
import sys
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from support import APP_DIR, db
from test_receiving import ReceivingFixture

ROOT = APP_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from floor_sets import FloorSetError, apply_floor_sets  # noqa: E402
from scripts.open_floor_sets import main as _cli  # noqa: E402


def cli(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        report = _cli(argv)
    return report, out.getvalue()


class FloorSetTests(ReceivingFixture):
    def setUp(self):
        super().setUp()
        self.folder = Path(self.tempdir.name)
        with closing(self.connect()) as con:
            product = lambda sku, name, form, unit: con.execute(  # noqa: E731
                """INSERT INTO products(sku, name_cn_en, jizhanming, product_type, stock_form, stock_unit,
                                        identity_status)
                   VALUES (?, ?, ?, '盲盒', ?, ?, 'verified')""", (sku, name, name, form, unit)).lastrowid
            self.box = product('PM0182', '小野雾中人', 'random_box', 'box')
            self.set = product('PM0182-SET', '小野雾中人 整端', 'sealed_set', 'set')
            con.execute("""INSERT INTO product_conversions(source_product_id, target_product_id, output_per_input, version)
                           VALUES (?, ?, 12, 1)""", (self.set, self.box))
            # A whole set nobody recorded a size for: never opened by a guess.
            self.unsized = product('OT0099-SET', '没写数量 整端', 'sealed_set', 'set')
            self.back = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='upstairs'",
                                    (self.store_id,)).fetchone()[0]
            # Markham is not live here: its floor is left alone.
            self.mk_floor = con.execute(
                """SELECT l.id FROM inventory_locations l JOIN stores s ON s.id=l.store_id
                   WHERE s.code='MK' AND l.code='floor'""").fetchone()[0]
            for item, location, disposition, quantity in (
                    (self.set, self.floor, 'saleable', 2), (self.box, self.floor, 'saleable', 5),
                    (self.unsized, self.floor, 'saleable', 1), (self.set, self.floor, 'hold', 1),
                    (self.set, self.back, 'saleable', 3), (self.set, self.mk_floor, 'saleable', 4)):
                con.execute("""INSERT INTO inventory_balances (product_id, location_id, disposition, quantity, version)
                               VALUES (?, ?, ?, ?, 1)""", (item, location, disposition, quantity))
                con.execute("""INSERT INTO inventory_documents (kind, request_key, payload_hash, actor_sub, business_date, status)
                               VALUES ('receipt', ?, 'fixture', 'fixture', '2026-11-05', 'building')""",
                            (f'fixture-{item}-{location}-{disposition}',))
                document = con.execute('SELECT last_insert_rowid()').fetchone()[0]
                con.execute("""INSERT INTO inventory_document_lines (document_id, line_no, product_id, native_unit, quantity,
                                                                     to_location_id, to_disposition, to_version)
                               VALUES (?, 1, ?, (SELECT stock_unit FROM products WHERE id=?), ?, ?, ?, 0)""",
                            (document, item, item, quantity, location, disposition))
                con.execute("""INSERT INTO inventory_movements (document_id, line_no, product_id, location_id, disposition, quantity)
                               VALUES (?, 1, ?, ?, ?, ?)""", (document, item, location, disposition, quantity))
            con.commit()

    def everything(self):
        with closing(self.connect()) as con:
            tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return self.snapshot(tables)

    def balances(self, path=None):
        with closing(sqlite3.connect(path or db.DB_PATH)) as con:
            return {tuple(row[:3]): row[3] for row in con.execute(
                'SELECT product_id, location_id, disposition, quantity FROM inventory_balances WHERE quantity > 0')}

    def test_the_dry_run_lists_the_sets_and_changes_nothing(self):
        before = self.everything()
        report, text = cli(['--db', db.DB_PATH])
        self.assertEqual(self.everything(), before)
        self.assertTrue(report['dry_run'])
        self.assertEqual([(item['store'], item['set_sku'], item['sets'], item['boxes_per_set'], item['boxes'],
                           item['box_sku'], item['floor_boxes_before'], item['floor_boxes_after'])
                          for item in report['items']], [('DT', 'PM0182-SET', 2, 12, 24, 'PM0182', 5, 29)])
        self.assertEqual([(item['set_sku'], item['sets'], item['code']) for item in report['blocked']],
                         [('OT0099-SET', 1, 'set_size_unknown')])
        self.assertEqual(report['totals'], {'set_products': 1, 'sets': 2, 'boxes': 24, 'stores': ['DT']})
        self.assertIn('DRY RUN: nothing was changed', text)
        self.assertIn('PM0182-SET', text)
        self.assertIn('OT0099-SET', text)
        # Another store's code lists nothing here, and a code no store has is refused.
        self.assertEqual(cli(['--db', db.DB_PATH, '--store', 'MK'])[0]['items'], [])
        with self.assertRaises(SystemExit):
            cli(['--db', db.DB_PATH, '--store', 'XX'])
        self.assertEqual(self.everything(), before)

    def test_apply_backs_up_first_and_opens_only_the_floor_sets(self):
        backup = self.folder / 'before-floor-sets.db'
        before = self.balances()
        report, text = cli(['--db', db.DB_PATH, '--apply', '--backup', str(backup), '--actor-sub', 'auth0|staff',
                            '--business-date', '2026-11-15'])
        self.assertTrue(report['applied'])
        self.assertIn('APPLIED', text)
        after = self.balances()
        # The two sets are 24 more boxes on the same floor. Storage, the held set, the unsized set and the
        # store that is not live are as they were.
        self.assertEqual(after, {**{key: value for key, value in before.items() if key != (self.set, self.floor, 'saleable')},
                                 (self.box, self.floor, 'saleable'): 29})
        self.assertEqual(self.balances(str(backup)), before)
        with closing(self.connect()) as con:
            document = con.execute('SELECT * FROM inventory_documents WHERE id=?', (report['documents']['DT'],)).fetchone()
            self.assertEqual((document['kind'], document['source_type'], document['actor_sub'], document['business_date'],
                              document['status']), ('correction', 'floor_sets_opened', 'auth0|staff', '2026-11-15', 'posted'))
            moved = {(row['product_id'], row['location_id']): row['quantity'] for row in con.execute(
                'SELECT * FROM inventory_movements WHERE document_id=?', (document['id'],))}
            self.assertEqual(moved, {(self.set, self.floor): -2, (self.box, self.floor): 24})
            # The boxes are loose (no opened set is kept), and the legacy stock row shows the same floor.
            self.assertEqual(con.execute('SELECT COUNT(*) FROM inventory_open_sets').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT instore_qty FROM stock WHERE product_id=? AND store_id=?',
                                         (self.box, self.store_id)).fetchone()[0], 29)
        # A second run finds only the set it had to leave, and writes nothing.
        settled = self.everything()
        again = apply_floor_sets(db.DB_PATH, backup_path=str(self.folder / 'second.db'), actor_sub='auth0|staff')
        self.assertEqual((again['applied'], again['items'], len(again['blocked'])), (False, [], 1))
        self.assertFalse((self.folder / 'second.db').exists())
        self.assertEqual(self.everything(), settled)

    def test_apply_refuses_without_a_new_backup_or_an_actor_with_access(self):
        before = self.everything()
        taken = self.folder / 'taken.db'
        taken.write_bytes(b'')
        for argv in (['--apply'], ['--apply', '--backup', str(taken), '--actor-sub', 'auth0|staff'],
                     ['--apply', '--backup', str(self.folder / 'new.db')],
                     ['--apply', '--backup', str(self.folder / 'new.db'), '--actor-sub', 'auth0|stranger']):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                cli(['--db', db.DB_PATH, *argv])
        self.assertFalse((self.folder / 'new.db').exists())
        self.assertEqual(self.everything(), before)

    def test_a_set_whose_box_is_not_verified_is_left_and_nothing_is_written(self):
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET identity_status='unverified' WHERE id=?", (self.box,))
            con.commit()
        before = self.everything()
        result = apply_floor_sets(db.DB_PATH, backup_path=str(self.folder / 'unverified.db'), actor_sub='auth0|staff')
        self.assertEqual((result['applied'], result['items']), (False, []))
        self.assertEqual({item['set_sku'] for item in result['blocked']}, {'PM0182-SET', 'OT0099-SET'})
        self.assertFalse((self.folder / 'unverified.db').exists())
        self.assertEqual(self.everything(), before)

    def test_a_failed_check_after_the_change_rolls_everything_back(self):
        before = self.everything()
        backup = self.folder / 'rolled-back.db'
        # The ledger check finds a balance that no longer matches its movements once the sets are opened.
        with patch('floor_sets._drift', side_effect=[0, 1]), self.assertRaises(FloorSetError) as refused:
            apply_floor_sets(db.DB_PATH, backup_path=str(backup), actor_sub='auth0|staff')
        self.assertEqual(refused.exception.code, 'ledger_mismatch')
        self.assertEqual(self.everything(), before)
        # The backup taken first stays as the snapshot from before.
        self.assertEqual(self.balances(str(backup)), self.balances())
        with self.assertRaises(FloorSetError):
            apply_floor_sets(db.DB_PATH, backup_path=str(self.folder / 'none.db'), actor_sub='')
