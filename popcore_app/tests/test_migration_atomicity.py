"""A migration commits together with its _migrations row or not at all, and one-time data fixes run once."""
import gc
import sqlite3
import tempfile
import threading
import unittest
import warnings
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from support import APP_DIR

import db


class MigrationCase(unittest.TestCase):
    def setUp(self):
        root = APP_DIR.parent / '.local' / 'tmp'
        root.mkdir(parents=True, exist_ok=True)
        self.tempdir = tempfile.TemporaryDirectory(dir=root)
        self.original_db_path = db.DB_PATH
        db.DB_PATH = str(Path(self.tempdir.name) / 'migrations.db')
        db.migrate_db()

    def tearDown(self):
        db.DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def connect(self):
        return closing(sqlite3.connect(db.DB_PATH))


class MigrationAtomicityTests(MigrationCase):
    def setUp(self):
        super().setUp()
        # Back to before the last two migrations, as a deploy that brings them finds the database.
        with self.connect() as con:
            con.execute('DROP INDEX idx_product_series_product')
            con.execute('ALTER TABLE product_series DROP COLUMN product_id')
            con.execute('DROP TABLE clover_sends')
            con.execute("DELETE FROM _migrations WHERE name IN ('series_owned_by_product', 'clover_sends')")
            con.commit()

    def state(self):
        """(product_series.product_id exists, series_owned_by_product recorded, clover_sends recorded)"""
        with self.connect() as con:
            columns = {row[1] for row in con.execute('PRAGMA table_info(product_series)')}
            applied = {row[0] for row in con.execute('SELECT name FROM _migrations')}
        return 'product_id' in columns, 'series_owned_by_product' in applied, 'clover_sends' in applied

    def boot_replacing(self, name, replace):
        """Run migrate_db once with migration `name` swapped for replace(migration); return its error."""
        original = db._get_migrations
        error = None
        with patch.object(db, '_get_migrations', side_effect=lambda: [
                (n, replace(fn) if n == name else fn) for n, fn in original()]):
            try:
                db.migrate_db()
            except sqlite3.OperationalError as exc:
                error = str(exc)
        # A failed migrate_db leaves its connection to the garbage collector; close it now (Windows
        # cannot delete an open database file).
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', ResourceWarning)
            gc.collect()
        return error

    def test_a_failed_migration_keeps_the_one_before_it_applied_and_recorded(self):
        def locked(migration):
            def fail(con, cur):
                raise sqlite3.OperationalError('database is locked')
            return fail

        self.assertEqual(self.boot_replacing('clover_sends', locked), 'database is locked')
        self.assertEqual(self.state(), (True, True, False))
        db.migrate_db()  # the next boots finish the rest instead of failing on 'duplicate column name'
        db.migrate_db()
        self.assertEqual(self.state(), (True, True, True))

    def test_a_migration_failing_part_way_rolls_back_its_schema_change(self):
        def interrupted(migration):
            def fail(con, cur):
                migration(con, cur)
                raise sqlite3.OperationalError('disk I/O error')
            return fail

        self.assertEqual(self.boot_replacing('series_owned_by_product', interrupted), 'disk I/O error')
        self.assertEqual(self.state(), (False, False, False))
        db.migrate_db()
        self.assertEqual(self.state(), (True, True, True))

    def test_a_second_process_waits_for_the_first_and_skips_what_it_applied(self):
        # Another process holds the write lock part-way through applying both migrations.
        other = sqlite3.connect(db.DB_PATH, isolation_level=None, check_same_thread=False)
        commit = threading.Timer(1.0, other.execute, ('COMMIT',))
        try:
            other.execute('BEGIN IMMEDIATE')
            for name, migration in db._get_migrations():
                if name in ('series_owned_by_product', 'clover_sends'):
                    migration(other, other.cursor())
            commit.start()
            db.migrate_db()  # waits for that commit, then finds both migrations recorded
        finally:
            commit.cancel()
            if commit.is_alive():
                commit.join()
            other.close()
        self.assertEqual(self.state(), (True, True, True))


class OneTimeProductTypeMergeTests(MigrationCase):
    def test_legacy_blind_box_types_merge_once_and_later_choices_survive_restarts(self):
        with self.connect() as con:
            # A database from before the merge was recorded still holds a retired type.
            con.execute("DELETE FROM _migrations WHERE name='merge_blind_box_product_types'")
            con.execute("INSERT INTO products (sku, product_type) VALUES ('OLD-1', '盲盒Figure')")
            con.commit()
        db.migrate_db()
        with self.connect() as con:  # a manager later picks the type on purpose
            con.execute("INSERT INTO products (sku, product_type) VALUES ('NEW-1', '盲盒毛绒')")
            con.commit()
        db.migrate_db()
        db.migrate_db()
        with self.connect() as con:
            types = dict(con.execute('SELECT sku, product_type FROM products'))
        self.assertEqual(types, {'OLD-1': '盲盒', 'NEW-1': '盲盒毛绒'})


if __name__ == '__main__':
    unittest.main()
