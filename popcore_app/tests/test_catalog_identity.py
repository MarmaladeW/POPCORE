import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from support import APP_DIR, IsolatedApiCase

import db


class CatalogMigrationTests(unittest.TestCase):
    def setUp(self):
        root = APP_DIR.parent / '.local' / 'tmp'
        root.mkdir(parents=True, exist_ok=True)
        self.tempdir = tempfile.TemporaryDirectory(dir=root)
        self.original_db_path = db.DB_PATH
        db.DB_PATH = str(Path(self.tempdir.name) / 'catalog.db')

    def tearDown(self):
        db.DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def connect(self):
        con = sqlite3.connect(db.DB_PATH)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys = ON')
        return con

    def test_empty_database_defaults_to_legacy_without_inventing_identity(self):
        db.migrate_db()
        with closing(self.connect()) as con:
            mode = con.execute(
                "SELECT mode FROM inventory_mode WHERE id=1"
            ).fetchone()[0]
            products = con.execute('SELECT COUNT(*) FROM products').fetchone()[0]
            columns = {
                row['name'] for row in con.execute('PRAGMA table_info(products)')
            }
        self.assertEqual(mode, 'legacy')
        self.assertEqual(products, 0)
        self.assertTrue({
            'series_id', 'stock_form', 'stock_unit', 'design_name',
            'identity_status',
        }.issubset(columns))
    def test_additive_migration_preserves_legacy_identity_and_references(self):
        original_migrations = db._get_migrations
        with patch.object(
            db,
            '_get_migrations',
            side_effect=lambda: [m for m in original_migrations()
                                 if m[0] != 'create_catalog_identity'],
        ):
            db.migrate_db()

        with closing(self.connect()) as con:
            con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, jizhanming, boxes_per_dan, sheet_ref)
                   VALUES ('LEGACY-1', 'Legacy Product', 'legacy', 12, '001')"""
            )
            old_id = con.execute(
                "SELECT id FROM products WHERE sku='LEGACY-1'"
            ).fetchone()['id']
            con.execute(
                """INSERT INTO product_aliases
                   (product_id, alias, alias_norm, created_by)
                   VALUES (?, 'legacy alias', 'legacyalias', 'fixture')""",
                (old_id,),
            )
            con.execute(
                """INSERT INTO daily_sales
                   (product_id, date, qty_sold, qty_pos, qty_cash, store)
                   VALUES (?, '2026-09-08', 1, 1, 0, 'DT')""",
                (old_id,),
            )
            con.commit()

        db.migrate_db()
        with closing(self.connect()) as con:
            row = con.execute(
                "SELECT id, sheet_ref, boxes_per_dan, identity_status "
                "FROM products WHERE sku='LEGACY-1'"
            ).fetchone()
            self.assertEqual(row['id'], old_id)
            self.assertEqual(row['sheet_ref'], '001')
            self.assertEqual(row['boxes_per_dan'], 12)
            self.assertEqual(row['identity_status'], 'unverified')
            self.assertEqual(
                con.execute('SELECT product_id FROM product_aliases').fetchone()[0],
                old_id,
            )
            self.assertEqual(
                con.execute('SELECT product_id FROM daily_sales').fetchone()[0],
                old_id,
            )
            tables = {
                row['name'] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertTrue({
                'product_series', 'product_conversions', 'product_barcodes'
            }.issubset(tables))
            before = {
                table: [tuple(r) for r in con.execute(
                    f'SELECT * FROM {table} ORDER BY 1'
                )]
                for table in (
                    'products', 'product_aliases', 'daily_sales',
                    'product_series', 'product_conversions', 'product_barcodes',
                )
            }

        db.migrate_db()
        with closing(self.connect()) as con:
            after = {
                table: [tuple(r) for r in con.execute(
                    f'SELECT * FROM {table} ORDER BY 1'
                )]
                for table in before
            }
            count = con.execute(
                "SELECT COUNT(*) FROM _migrations "
                "WHERE name='create_catalog_identity'"
            ).fetchone()[0]
        self.assertEqual(after, before)
        self.assertEqual(count, 1)


class CatalogIdentityApiTests(IsolatedApiCase):
    def test_referenced_verified_product_identity_cannot_change(self):
        with closing(self.connect()) as con:
            series_id = con.execute(
                "INSERT INTO product_series(name) VALUES ('Test Series')"
            ).lastrowid
            con.execute(
                """UPDATE products
                   SET series_id=?, stock_form='random_box', stock_unit='box',
                       identity_status='verified'
                   WHERE id=?""",
                (series_id, self.product_id),
            )
            con.commit()

        response = self.client.patch(
            f'/api/products/{self.product_id}',
            headers=self.headers('manager'),
            json={'stock_unit': 'piece'},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'identity_locked')

    def test_verified_identity_requires_valid_form_and_conversion(self):
        with closing(self.connect()) as con:
            series_id = con.execute(
                "INSERT INTO product_series(name) VALUES ('Review Series')"
            ).lastrowid
            random_id = con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, series_id, stock_form, stock_unit)
                   VALUES ('RANDOM-REVIEW', 'Random', ?, 'random_box', 'box')""",
                (series_id,),
            ).lastrowid
            con.commit()

        mismatch = self.client.patch(
            f'/api/products/{self.product_id}',
            headers=self.headers('manager'),
            json={'stock_form': 'sealed_set', 'stock_unit': 'box'},
        )
        self.assertEqual(mismatch.status_code, 400)

        prepared = self.client.patch(
            f'/api/products/{self.product_id}',
            headers=self.headers('manager'),
            json={
                'series_id': series_id, 'stock_form': 'sealed_set',
                'stock_unit': 'set',
            },
        )
        self.assertEqual(prepared.status_code, 200)
        without_conversion = self.client.patch(
            f'/api/products/{self.product_id}',
            headers=self.headers('manager'),
            json={'identity_status': 'verified'},
        )
        self.assertEqual(without_conversion.status_code, 400)

        conversion = self.client.post(
            f'/api/products/{self.product_id}/conversions',
            headers=self.headers('manager'),
            json={'target_product_id': random_id, 'output_per_input': 12},
        )
        verified = self.client.patch(
            f'/api/products/{self.product_id}',
            headers=self.headers('manager'),
            json={'identity_status': 'verified'},
        )
        self.assertEqual(conversion.status_code, 201)
        self.assertEqual(verified.status_code, 200)

        with closing(self.connect()) as con:
            design_id = con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, series_id, stock_form, stock_unit)
                   VALUES ('DESIGN-NAMELESS', 'Nameless', ?,
                           'confirmed_design', 'piece')""",
                (series_id,),
            ).lastrowid
            con.commit()
        nameless = self.client.patch(
            f'/api/products/{design_id}', headers=self.headers('manager'),
            json={'identity_status': 'verified'},
        )
        self.assertEqual(nameless.status_code, 400)

    def test_sheet_confirm_preserves_verified_identity_fields(self):
        with closing(self.connect()) as con:
            series_id = con.execute(
                "INSERT INTO product_series(name) VALUES ('Sheet Series')"
            ).lastrowid
            con.execute(
                """UPDATE products
                   SET series_id=?, stock_form='ordinary', stock_unit='piece',
                       design_name=NULL, identity_status='verified'
                   WHERE id=?""",
                (series_id, self.product_id),
            )
            con.commit()

        response = self.client.post(
            '/api/products/sync-sheet/confirm',
            headers=self.headers('admin'),
            json={'changes': [{
                'product_id': self.product_id,
                'new_jizhanming': 'renamed',
                'sheet_ref': 'sheet-001',
            }]},
        )
        self.assertEqual(response.status_code, 200)
        with closing(self.connect()) as con:
            row = con.execute(
                """SELECT series_id, stock_form, stock_unit, design_name,
                          identity_status, sheet_ref
                   FROM products WHERE id=?""",
                (self.product_id,),
            ).fetchone()
        self.assertEqual(tuple(row), (
            series_id, 'ordinary', 'piece', None, 'verified', 'sheet-001'
        ))
    def test_conflicting_internal_barcode_returns_409(self):
        with closing(self.connect()) as con:
            con.execute(
                """UPDATE products SET stock_form='ordinary', stock_unit='piece'
                   WHERE id=?""",
                (self.product_id,),
            )
            other_id = con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, stock_form, stock_unit)
                   VALUES ('TEST-2', 'Other', 'ordinary', 'piece')"""
            ).lastrowid
            con.commit()

        first = self.client.post(
            f'/api/products/{self.product_id}/barcodes',
            headers=self.headers('manager'),
            json={
                'code': '001234567890', 'code_kind': 'internal',
                'input_unit': 'piece', 'quantity_per_scan': 1,
            },
        )
        second = self.client.post(
            f'/api/products/{other_id}/barcodes',
            headers=self.headers('manager'),
            json={
                'code': '001234567890', 'code_kind': 'internal',
                'input_unit': 'piece', 'quantity_per_scan': 1,
            },
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()['code'], 'barcode_conflict')


if __name__ == '__main__':
    unittest.main()