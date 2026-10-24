import csv
import io
import sys
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from support import APP_DIR, IsolatedApiCase, db

ROOT = APP_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inventory_opening import (  # noqa: E402
    OpeningCountError, apply_opening_counts, count_sheet_rows, read_count_file,
    review_opening_counts,
)
from scripts.inventory_opening import main as _opening_cli  # noqa: E402


def opening_cli(argv):
    with redirect_stdout(io.StringIO()):
        return _opening_cli(argv)


ADMIN_SUB = 'auth0|admin'
LEDGER_TABLES = (
    'inventory_documents', 'inventory_document_lines', 'inventory_movements',
    'inventory_balances', 'inventory_scope_state', 'inventory_mode', 'stock',
    'stock_transactions',
)


class OpeningCountTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.folder = Path(self.tempdir.name)
        with closing(self.connect()) as con:
            self.stores = {r['code']: r['id'] for r in con.execute(
                "SELECT id, code FROM stores WHERE code IN ('DT','MK')")}
            self.box = self._product(con, 'OPEN-BOX', 'Opening Box', 'random_box', 'box')
            self.set = self._product(con, 'OPEN-SET', 'Opening Set', 'sealed_set', 'set')
            self.design = self._product(con, 'OPEN-DESIGN', 'Opening Design',
                                        'confirmed_design', 'piece')
            con.execute(
                """INSERT INTO product_barcodes
                   (code, product_id, code_kind, input_unit, quantity_per_scan)
                   VALUES ('00012345', ?, 'manufacturer', 'box', 1)""", (self.box,))
            self._stock(con, self.box, 'DT', instore=4, upstairs=6, claw=3)
            self._stock(con, self.set, 'DT', instore=0, upstairs=2, claw=2)
            self._stock(con, self.design, 'MK', instore=3, upstairs=0, claw=1)
            for role in ('admin', 'manager'):
                for store_id in self.stores.values():
                    con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)',
                                (f'auth0|{role}', store_id))
            con.commit()

    @staticmethod
    def _product(con, sku, name, form, unit):
        return con.execute(
            """INSERT INTO products
               (sku, name_cn_en, jizhanming, product_type, search_blob,
                stock_form, stock_unit, identity_status)
               VALUES (?, ?, ?, '盲盒', ?, ?, ?, 'verified')""",
            (sku, name, name, name.lower(), form, unit),
        ).lastrowid

    def _stock(self, con, product_id, store, *, instore, upstairs, claw=0):
        con.execute(
            """INSERT INTO stock(product_id, store_id, upstairs_qty, instore_qty, claw_qty)
               VALUES (?, ?, ?, ?, ?)""",
            (product_id, self.stores[store], upstairs, instore, claw))

    def _sheet(self, store, location, counts, scope='legacy'):
        """Export the real count sheet for a location, then fill in counts by SKU."""
        with closing(self.connect()) as con:
            rows = count_sheet_rows(con, store, location, scope)
        path = self.folder / f'{store}-{location}.csv'
        with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else
                                    ['store', 'location', 'product_id', 'sku', 'unit', 'count'])
            writer.writeheader()
            for row in rows:
                if row['sku'] in counts:
                    row['count'] = counts[row['sku']]
                writer.writerow(row)
            if not rows:
                for sku, count in counts.items():
                    writer.writerow({'store': store, 'location': location, 'sku': sku,
                                     'unit': 'box', 'count': count})
        return path

    def _complete_rows(self):
        """Counts for every location; TEST-1 has DT legacy stock but no verified identity."""
        paths = [
            self._sheet('DT', 'floor', {'OPEN-BOX': '5', 'OPEN-SET': '0'}),
            self._sheet('DT', 'upstairs', {'OPEN-BOX': '6', 'OPEN-SET': '0'}),
            self._sheet('DT', 'claw', {'OPEN-BOX': '1'}),
            self._sheet('MK', 'floor', {'OPEN-DESIGN': '2'}),
        ]
        rows = []
        for path in paths:
            rows.extend(read_count_file(path))
        return rows

    def _review(self, rows, **kwargs):
        with closing(self.connect()) as con:
            return review_opening_counts(con, rows, business_date='2026-10-20', **kwargs)

    def _codes(self, report):
        return sorted({error['code'] for error in report['errors']})

    def _shop_name(self, product_id):
        with closing(self.connect()) as con:
            return con.execute('SELECT jizhanming FROM products WHERE id=?', (product_id,)).fetchone()[0]

    def _row(self, **values):
        base = {'store': 'DT', 'location': 'floor', 'unit': 'box', 'count': '1',
                '_file': 'manual.csv', '_row': 2}
        return {**base, **values}

    # ── count sheet ──────────────────────────────────────────────────────────

    def test_count_sheet_route_is_blind_scoped_and_manager_only(self):
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET name_cn_en='Open Box Series Figures' WHERE id=?", (self.box,))
            con.commit()
        response = self.client.get(
            '/api/products/count-sheet?store_code=DT&location=floor',
            headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('count_sheet_DT_floor_legacy_', response.headers['Content-Disposition'])
        rows = list(csv.DictReader(response.get_data(as_text=True).lstrip('﻿').splitlines()))
        self.assertEqual({row['sku'] for row in rows}, {'TEST-1', 'OPEN-BOX', 'OPEN-SET'})
        box = next(row for row in rows if row['sku'] == 'OPEN-BOX')
        self.assertEqual((box['product_id'], box['barcode'], box['stock_form'], box['unit'],
                          box['identity'], box['count']),
                         (str(self.box), '00012345', 'random_box', 'box', 'verified', ''))
        self.assertEqual((box['记账名'], box['name']),
                         (self._shop_name(self.box), 'Open Box Series Figures'))
        self.assertLess(list(rows[0]).index('记账名'), list(rows[0]).index('name'))
        self.assertNotIn('instore_qty', rows[0])
        self.assertEqual(next(r for r in rows if r['sku'] == 'TEST-1')['identity'], 'unverified')

        catalog = self.client.get(
            '/api/products/count-sheet?store_code=MK&location=warehouse&scope=catalog',
            headers=self.headers('manager'))
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(len(catalog.get_data(as_text=True).strip().splitlines()), 1 + 4)

        self.assertEqual(self.client.get(
            '/api/products/count-sheet?store_code=DT&location=floor',
            headers=self.headers('staff')).status_code, 403)
        self.assertEqual(self.client.get(
            '/api/products/count-sheet?store_code=DT&location=floor',
            headers=self.headers('manager:other')).status_code, 403)
        bad = self.client.get('/api/products/count-sheet?store_code=DT&location=warehouse',
                              headers=self.headers('manager'))
        self.assertEqual((bad.status_code, bad.get_json()['code']), (400, 'location_unknown'))
        scope = self.client.get(
            '/api/products/count-sheet?store_code=DT&location=floor&scope=all',
            headers=self.headers('manager'))
        self.assertEqual((scope.status_code, scope.get_json()['code']), (400, 'invalid_scope'))

    def test_cli_count_sheet_reads_only_and_refuses_existing_output(self):
        before = self.snapshot(LEDGER_TABLES)
        output = self.folder / 'sheets' / 'mk-floor.csv'
        rows = opening_cli(['count-sheet', '--db', db.DB_PATH, '--store', 'mk',
                            '--location', 'FLOOR', '--output', str(output)])
        self.assertEqual([row['sku'] for row in rows], ['OPEN-DESIGN'])
        self.assertTrue(output.read_text(encoding='utf-8').startswith('﻿store,location,'))
        self.assertEqual(self.snapshot(LEDGER_TABLES), before)
        with self.assertRaises(SystemExit):
            opening_cli(['count-sheet', '--db', db.DB_PATH, '--store', 'MK',
                         '--location', 'floor', '--output', str(output)])

    # ── review ───────────────────────────────────────────────────────────────

    def test_dry_run_compares_with_legacy_and_writes_nothing(self):
        before = self.snapshot(LEDGER_TABLES)
        report = self._review(self._complete_rows(), empty_locations=['MK-warehouse'],
                              actor_sub=ADMIN_SUB)
        self.assertEqual(report['errors'], [])
        self.assertTrue(report['ready_to_apply'])
        counts = {(c['sku'], c['location']): c for c in report['counts']}
        self.assertEqual(counts['OPEN-BOX', 'floor']['legacy_qty'], 4)
        self.assertEqual(counts['OPEN-BOX', 'floor']['difference'], 1)
        self.assertEqual(counts['OPEN-SET', 'upstairs']['difference'], -2)
        self.assertEqual(counts['OPEN-DESIGN', 'floor']['difference'], -1)
        # Legacy claw_qty overlapped the floor count, so the claw count is not compared with it.
        self.assertEqual((counts['OPEN-BOX', 'claw']['legacy_qty'],
                          counts['OPEN-BOX', 'claw']['difference']), (None, 1))
        warnings = {(w['code'], w['product_id']) for w in report['warnings']}
        self.assertIn(('unverified_legacy_stock', self.product_id), warnings)
        self.assertIn(('legacy_claw_unverified', self.design), warnings)
        self.assertIn(('legacy_claw_unverified', self.set), warnings)
        self.assertEqual(report['request_key'], self._review(
            self._complete_rows(), empty_locations=['MK-warehouse'])['request_key'])
        self.assertEqual(self.snapshot(LEDGER_TABLES), before)

    def test_rows_must_name_one_verified_product_location_and_native_unit(self):
        cases = {
            'identity_unverified': self._row(product_id=str(self.product_id)),
            'identity_missing': self._row(name='Opening Box', barcode='00012345'),
            'identity_mismatch': self._row(product_id=str(self.box), sku='OPEN-SET'),
            'sku_unknown': self._row(sku='open-box'),
            'product_unknown': self._row(product_id='999999'),
            'unit_mismatch': self._row(sku='OPEN-SET', unit='box'),
            'location_unknown': self._row(sku='OPEN-BOX', location='warehouse'),
            'count_invalid': self._row(sku='OPEN-BOX', count='2.5'),
        }
        for code, row in cases.items():
            with self.subTest(code=code):
                report = self._review([row])
                self.assertIn(code, self._codes(report))
                self.assertFalse(report['ready_to_apply'])
        negative = self._review([self._row(sku='OPEN-BOX', count='-1')])
        self.assertIn('count_invalid', self._codes(negative))

    def test_duplicates_across_files_reject_every_copy(self):
        rows = self._complete_rows() + [self._row(product_id=str(self.box), count='5')]
        report = self._review(rows, empty_locations=['MK-warehouse'])
        duplicates = [e for e in report['errors'] if e['code'] == 'duplicate_count']
        self.assertEqual(len(duplicates), 2)
        self.assertNotIn((self.box, 'floor'),
                         {(c['product_id'], c['location']) for c in report['counts']})

    def test_missing_locations_and_uncounted_legacy_stock_block_apply(self):
        rows = list(read_count_file(self._sheet('DT', 'floor', {'OPEN-BOX': '5'})))
        report = self._review(rows)
        missing = sorted(e['message'].split()[0] for e in report['errors']
                         if e['code'] == 'location_not_counted')
        self.assertEqual(missing, ['DT-claw', 'DT-upstairs', 'MK-floor', 'MK-warehouse'])
        self.assertNotIn('uncounted_legacy_stock', self._codes(report))
        floor_only = self._review(rows + [self._row(location='upstairs', sku='OPEN-BOX',
                                                    count='6')],
                                  empty_locations=['DT-claw', 'MK-floor', 'MK-warehouse'])
        uncounted = {e['product_id'] for e in floor_only['errors']
                     if e['code'] == 'uncounted_legacy_stock'}
        self.assertEqual(uncounted, {self.set, self.design})

    def test_empty_claw_needs_no_legacy_counts_and_clears_legacy_claw_qty(self):
        rows = [row for row in self._complete_rows() if row['location'] != 'claw']
        missing = self._review(rows, empty_locations=['MK-warehouse'])
        self.assertIn('DT-claw has no count rows', ' '.join(
            e['message'] for e in missing['errors'] if e['code'] == 'location_not_counted'))
        report = apply_opening_counts(
            db.DB_PATH, rows, actor_sub=ADMIN_SUB, business_date='2026-10-20',
            backup_path=self.folder / 'claw-empty.db',
            empty_locations=['DT-claw', 'MK-warehouse'])
        self.assertTrue(report['applied'], report['errors'])
        self.assertNotIn('uncounted_legacy_stock', self._codes(report))
        self.assertTrue(report['reconciliation']['ok'], report['reconciliation'])
        with closing(self.connect()) as con:
            claw = {r[0]: r[1] for r in con.execute(
                'SELECT product_id, claw_qty FROM stock WHERE store_id=?', (self.stores['DT'],))}
            self.assertEqual((claw[self.box], claw[self.set]), (0, 0))
            self.assertEqual(con.execute(
                'SELECT claw_qty FROM stock WHERE product_id=? AND store_id=?',
                (self.design, self.stores['MK'])).fetchone()[0], 1)
            self.assertEqual(con.execute(
                """SELECT COUNT(*) FROM inventory_scope_state ss
                   JOIN inventory_locations l ON l.id=ss.location_id
                   WHERE l.code='claw' AND ss.opening_verified=1""").fetchone()[0], 1)

    def test_sheet_with_only_blank_counts_is_not_an_empty_location(self):
        blank = self._row(store='MK', location='warehouse', sku='OPEN-DESIGN', count='')
        report = self._review(self._complete_rows() + [blank])
        self.assertFalse(report['ready_to_apply'])
        blocked = [e['message'] for e in report['errors'] if e['code'] == 'location_not_counted']
        self.assertEqual(len(blocked), 1)
        self.assertIn('MK-warehouse has count rows but every count is blank', blocked[0])
        declared = self._review(self._complete_rows() + [blank], empty_locations=['MK-warehouse'])
        self.assertNotIn('location_not_counted', self._codes(declared))

    def test_review_reports_actor_without_access_and_closed_opening(self):
        report = self._review(self._complete_rows(), empty_locations=['MK-warehouse'],
                              actor_sub='auth0|nobody')
        self.assertEqual(self._codes(report), ['actor_access_missing'])
        with closing(self.connect()) as con:
            con.execute("UPDATE inventory_mode SET mode='authoritative' WHERE id=1")
            con.commit()
        closed = self._review(self._complete_rows(), empty_locations=['MK-warehouse'])
        self.assertIn('opening_closed', self._codes(closed))

    # ── apply ────────────────────────────────────────────────────────────────

    def test_apply_backs_up_posts_one_opening_and_activates_every_location(self):
        backup = self.folder / 'pre-opening.db'
        rows = self._complete_rows()
        report = apply_opening_counts(
            db.DB_PATH, rows, actor_sub=ADMIN_SUB,
            business_date='2026-10-20', backup_path=backup,
            empty_locations=['MK-warehouse'])
        self.assertTrue(report['applied'], report['errors'])
        self.assertTrue(report['reconciliation']['ok'], report['reconciliation'])
        self.assertEqual(report['backup']['integrity'], 'ok')
        document_id = report['opening_document_id']
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT mode FROM inventory_mode').fetchone()[0],
                             'authoritative')
            scope = con.execute(
                'SELECT opening_verified, opening_document_id FROM inventory_scope_state'
            ).fetchall()
            self.assertEqual(len(scope), 5)
            self.assertEqual({tuple(row) for row in scope}, {(1, document_id)})
            document = con.execute(
                'SELECT kind, actor_sub, business_date, request_key FROM inventory_documents'
            ).fetchall()
            self.assertEqual([tuple(row) for row in document],
                             [('opening', ADMIN_SUB, '2026-10-20', report['request_key'])])
            balances = {(r['sku'], r['code']): r['quantity'] for r in con.execute(
                """SELECT p.sku, l.code, b.quantity FROM inventory_balances b
                   JOIN products p ON p.id=b.product_id
                   JOIN inventory_locations l ON l.id=b.location_id""")}
            self.assertEqual(balances, {('OPEN-BOX', 'floor'): 5,
                                        ('OPEN-BOX', 'upstairs'): 6,
                                        ('OPEN-BOX', 'claw'): 1,
                                        ('OPEN-DESIGN', 'floor'): 2})
            stock = {r['sku']: (r['instore_qty'], r['upstairs_qty'], r['claw_qty'])
                     for r in con.execute(
                """SELECT p.sku, s.instore_qty, s.upstairs_qty, s.claw_qty
                   FROM stock s JOIN products p ON p.id=s.product_id""")}
            self.assertEqual(stock['OPEN-SET'], (0, 0, 0))
            self.assertEqual(stock['OPEN-BOX'], (5, 6, 1))
            self.assertEqual(stock['OPEN-DESIGN'], (2, 0, 1))
            self.assertEqual(stock['TEST-1'], (2, 10, 0))
            zeroed = con.execute(
                """SELECT location, qty, inventory_document_id FROM stock_transactions
                   WHERE product_id=? AND txn_type='inventory_opening' ORDER BY id""", (self.set,)
            ).fetchall()
            self.assertEqual([tuple(row) for row in zeroed],
                             [('upstairs', 0, document_id), ('claw', 0, document_id)])
        with closing(self.connect()) as con:
            con.execute(f"ATTACH DATABASE '{backup}' AS snapshot")
            self.assertEqual(con.execute(
                'SELECT mode FROM snapshot.inventory_mode').fetchone()[0], 'legacy')
            self.assertEqual(con.execute(
                'SELECT COUNT(*) FROM snapshot.inventory_documents').fetchone()[0], 0)
            self.assertEqual(con.execute(
                'SELECT upstairs_qty FROM snapshot.stock WHERE product_id=?',
                (self.set,)).fetchone()[0], 2)

        before = self.snapshot(LEDGER_TABLES)
        replay = apply_opening_counts(
            db.DB_PATH, [dict(row) for row in rows], actor_sub=ADMIN_SUB,
            business_date='2026-10-20', backup_path=self.folder / 'second.db',
            empty_locations=['MK-warehouse'])
        self.assertTrue(replay['already_applied'])
        self.assertEqual(replay['opening_document_id'], document_id)
        self.assertFalse((self.folder / 'second.db').exists())
        self.assertEqual(self.snapshot(LEDGER_TABLES), before)

        changed = [dict(row) for row in rows]
        next(row for row in changed if row['sku'] == 'OPEN-BOX')['count'] = '7'
        refused = apply_opening_counts(
            db.DB_PATH, changed, actor_sub=ADMIN_SUB, business_date='2026-10-20',
            backup_path=self.folder / 'third.db', empty_locations=['MK-warehouse'])
        self.assertFalse(refused['applied'])
        self.assertIn('opening_closed', self._codes(refused))
        self.assertEqual(self.snapshot(LEDGER_TABLES), before)

        public = self.client.post(
            '/api/inventory/commands',
            json={'kind': 'opening', 'business_date': '2026-10-20', 'lines': []},
            headers={**self.headers('admin'), 'Idempotency-Key': 'public-opening'})
        self.assertEqual(public.get_json()['code'], 'opening_not_public')

    def test_apply_with_review_errors_changes_nothing_and_takes_no_backup(self):
        before = self.snapshot(LEDGER_TABLES)
        backup = self.folder / 'not-taken.db'
        report = apply_opening_counts(
            db.DB_PATH, self._complete_rows(), actor_sub=ADMIN_SUB,
            business_date='2026-10-20', backup_path=backup)
        self.assertFalse(report['applied'])
        self.assertIn('location_not_counted', self._codes(report))
        self.assertFalse(backup.exists())
        self.assertEqual(self.snapshot(LEDGER_TABLES), before)

    def test_apply_refuses_existing_backup_and_rolls_back_failed_posting(self):
        existing = self.folder / 'existing.db'
        existing.write_bytes(b'')
        with self.assertRaises(OpeningCountError):
            apply_opening_counts(db.DB_PATH, self._complete_rows(), actor_sub=ADMIN_SUB,
                                 business_date='2026-10-20', backup_path=existing,
                                 empty_locations=['MK-warehouse'])
        before = self.snapshot(LEDGER_TABLES)
        backup = self.folder / 'kept.db'
        with patch('inventory_opening.reconcile_inventory',
                   return_value={'ok': False}):
            with self.assertRaises(OpeningCountError) as raised:
                apply_opening_counts(
                    db.DB_PATH, self._complete_rows(), actor_sub=ADMIN_SUB,
                    business_date='2026-10-20', backup_path=backup,
                    empty_locations=['MK-warehouse'])
        self.assertEqual(raised.exception.code, 'reconciliation_failed')
        self.assertTrue(backup.exists())
        self.assertEqual(self.snapshot(LEDGER_TABLES), before)

    def test_cli_defaults_to_dry_run_and_apply_needs_explicit_inputs(self):
        paths = [self._sheet('DT', 'floor', {'OPEN-BOX': '5', 'OPEN-SET': '0'}),
                 self._sheet('DT', 'upstairs', {'OPEN-BOX': '6', 'OPEN-SET': '0'}),
                 self._sheet('DT', 'claw', {'OPEN-BOX': '1'}),
                 self._sheet('MK', 'floor', {'OPEN-DESIGN': '2'})]
        before = self.snapshot(LEDGER_TABLES)
        report_path = self.folder / 'report.json'
        report = opening_cli(['intake', '--db', db.DB_PATH, *map(str, paths),
                              '--empty-location', 'MK-warehouse',
                              '--business-date', '2026-10-20', '--report', str(report_path)])
        self.assertTrue(report['dry_run'])
        self.assertTrue(report['ready_to_apply'])
        self.assertTrue(report_path.exists())
        self.assertEqual(self.snapshot(LEDGER_TABLES), before)
        with self.assertRaises(SystemExit):
            opening_cli(['intake', '--db', db.DB_PATH, *map(str, paths),
                         '--empty-location', 'MK-warehouse', '--apply'])
        with self.assertRaises(SystemExit):
            opening_cli(['intake', '--db', db.DB_PATH, str(paths[0])])
        self.assertEqual(self.snapshot(LEDGER_TABLES), before)
        applied = opening_cli(['intake', '--db', db.DB_PATH, *map(str, paths),
                               '--empty-location', 'MK-warehouse',
                               '--business-date', '2026-10-20', '--apply',
                               '--actor-sub', ADMIN_SUB,
                               '--backup', str(self.folder / 'cli-backup.db')])
        self.assertTrue(applied['applied'])
        self.assertEqual(applied['request_key'], report['request_key'])


if __name__ == '__main__':
    import unittest
    unittest.main()
