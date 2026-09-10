import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from flask import Flask


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

os.environ.setdefault('AUTH0_DOMAIN', 'test.invalid')
os.environ.setdefault('DISABLE_SCHEDULER', '1')

import auth  # noqa: E402
import db  # noqa: E402
import payment_evidence  # noqa: E402
import condition_evidence  # noqa: E402
from blueprints import closing as closing_blueprint, goods, inventory, payments, products, reports, restock, sale_documents, sales, schedule, stock, stores, trades, today  # noqa: E402


class IsolatedApiCase(unittest.TestCase):
    def setUp(self):
        temp_root = APP_DIR.parent / '.local' / 'tmp'
        temp_root.mkdir(parents=True, exist_ok=True)
        self.original_tempdir = tempfile.tempdir
        tempfile.tempdir = str(temp_root)
        self.tempdir = tempfile.TemporaryDirectory(dir=temp_root)
        self.original_db_path = db.DB_PATH
        test_db = (Path(self.tempdir.name) / 'test.db').resolve()
        if temp_root.resolve() not in test_db.parents:
            raise RuntimeError('Refusing to use a test database outside .local/tmp')
        db.DB_PATH = str(test_db)
        self.original_upload_dir = products.HIDDEN_IMG_DIR
        self.original_evidence_dir = payment_evidence.EVIDENCE_DIR
        self.original_condition_evidence_dir = condition_evidence.EVIDENCE_DIR
        self.upload_dir = Path(self.tempdir.name) / 'uploads'
        products.HIDDEN_IMG_DIR = str(self.upload_dir)
        payment_evidence.EVIDENCE_DIR = self.upload_dir / 'payment_evidence'
        condition_evidence.EVIDENCE_DIR = self.upload_dir / 'condition_evidence'
        db.migrate_db()

        with closing(self.connect()) as con:
            con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, jizhanming, product_type, boxes_per_dan)
                   VALUES ('TEST-1', 'Test Product', 'Test Product', 'ordinary', 1)"""
            )
            self.product_id = con.execute(
                "SELECT id FROM products WHERE sku='TEST-1'"
            ).fetchone()[0]
            self.store_id = con.execute(
                "SELECT id FROM stores WHERE code='DT'"
            ).fetchone()[0]
            con.execute(
                """INSERT OR IGNORE INTO stock
                   (product_id, store_id, upstairs_qty, instore_qty, claw_qty)
                   VALUES (?, ?, 10, 2, 0)""",
                (self.product_id, self.store_id),
            )
            con.commit()

        app = Flask(__name__)
        app.config.update(TESTING=True)
        for blueprint in (
            sales.bp, stock.bp, restock.bp, inventory.bp, products.bp, stores.bp,
            goods.bp,
            sale_documents.bp,
            payments.bp,
            closing_blueprint.bp,
            trades.bp,
            today.bp,
            schedule.bp,
            reports.bp,
        ):
            app.register_blueprint(blueprint)
        app.teardown_appcontext(db.close_db)
        self.app = app
        self.client = app.test_client()
        self.auth_patch = patch.object(
            auth, '_decode_token', side_effect=self._decode_test_token
        )
        self.auth_patch.start()
        self.network_patch = patch(
            'requests.sessions.Session.request',
            side_effect=AssertionError('Unexpected external request in isolated test'),
        )
        self.network_patch.start()

    def tearDown(self):
        self.network_patch.stop()
        self.auth_patch.stop()
        products.HIDDEN_IMG_DIR = self.original_upload_dir
        payment_evidence.EVIDENCE_DIR = self.original_evidence_dir
        condition_evidence.EVIDENCE_DIR = self.original_condition_evidence_dir
        db.DB_PATH = self.original_db_path
        self.tempdir.cleanup()
        tempfile.tempdir = self.original_tempdir

    @staticmethod
    def _decode_test_token(token):
        role, separator, subject = token.partition(':')
        role = role if role in auth.ROLE_HIERARCHY else 'viewer'
        return {
            'sub': f'auth0|{subject if separator else role}',
            auth.ROLE_CLAIM: role,
        }

    @staticmethod
    def headers(role='staff'):
        return {'Authorization': f'Bearer {role}'}

    def connect(self):
        con = sqlite3.connect(db.DB_PATH)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys = ON')
        return con

    def snapshot(self, tables):
        result = {}
        with closing(self.connect()) as con:
            for table in tables:
                rows = con.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
                result[table] = [tuple(row) for row in rows]
        return result
