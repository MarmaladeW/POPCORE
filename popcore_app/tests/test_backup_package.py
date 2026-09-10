import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from support import APP_DIR
from backup_package import create_package, restore_package


class BackupPackageTests(unittest.TestCase):
    def test_restore_rejects_manifest_missing_database_attachment(self):
        temp_root = APP_DIR.parent / '.local' / 'tmp'
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp:
            root = Path(temp)
            database = root / 'source.db'
            with sqlite3.connect(database) as con:
                con.execute('CREATE TABLE hidden_images(id INTEGER PRIMARY KEY, filename TEXT)')
                con.execute('CREATE TABLE payment_evidence(id INTEGER PRIMARY KEY, object_id TEXT)')
                con.execute('CREATE TABLE condition_evidence(id INTEGER PRIMARY KEY, object_id TEXT)')
                con.execute("INSERT INTO hidden_images(filename) VALUES ('image.bin')")
            roots = {kind: root / kind for kind in ('product', 'payment', 'condition')}
            for path in roots.values():
                path.mkdir()
            (roots['product'] / 'image.bin').write_bytes(b'image')
            package = root / 'package'
            create_package(database, roots, package)
            manifest_path = package / 'manifest.json'
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['attachments'] = []
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'does not match database references'):
                restore_package(package, root / 'restored')


if __name__ == '__main__':
    unittest.main()
