from contextlib import closing
from unittest.mock import patch

from support import IsolatedApiCase, db


class InventoryAuditMigrationTests(IsolatedApiCase):
    def setUp(self):
        legacy = [(name, fn) for name, fn in db._get_migrations() if name != 'add_inventory_audit_context']
        with patch.object(db, '_get_migrations', return_value=legacy):
            super().setUp()

    def test_existing_posted_history_survives_repeated_additive_migration(self):
        with closing(self.connect()) as con:
            location = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'", (self.store_id,)).fetchone()[0]
            document = con.execute("""INSERT INTO inventory_documents(kind,request_key,payload_hash,actor_sub,business_date,status)
                                      VALUES ('receipt','legacy-audit','saved-hash','auth0|staff','2026-09-01','building')""").lastrowid
            con.execute("""INSERT INTO inventory_document_lines(document_id,line_no,product_id,native_unit,quantity,to_location_id,to_disposition,to_version)
                           VALUES (?,1,?,'piece',3,?,'saleable',0)""", (document, self.product_id, location))
            con.execute("INSERT INTO inventory_movements(document_id,line_no,product_id,location_id,disposition,quantity) VALUES (?,1,?,?,'saleable',3)", (document, self.product_id, location))
            con.execute("INSERT INTO inventory_balances VALUES (?,?,'saleable',3,1)", (self.product_id, location))
            con.execute("UPDATE inventory_documents SET status='posted',stored_result=? WHERE id=?", ('{"document_id":'+str(document)+'}', document))
            con.commit()
            documents = [dict(row) for row in con.execute('SELECT * FROM inventory_documents')]
            lines = [dict(row) for row in con.execute('SELECT * FROM inventory_document_lines')]
        before = self.snapshot(('inventory_balances', 'inventory_movements', 'stock'))
        db.migrate_db()
        db.migrate_db()
        with closing(self.connect()) as con:
            self.assertIn('reason', {row['name'] for row in con.execute('PRAGMA table_info(inventory_documents)')})
            self.assertIn('open_set_id', {row['name'] for row in con.execute('PRAGMA table_info(inventory_document_lines)')})
            self.assertEqual([dict(row) for row in con.execute('SELECT * FROM inventory_documents')], [{**row, 'reason': None} for row in documents])
            self.assertEqual([dict(row) for row in con.execute('SELECT * FROM inventory_document_lines')], [{**row, 'open_set_id': None} for row in lines])
            self.assertEqual(con.execute("SELECT count(*) FROM _migrations WHERE name='add_inventory_audit_context'").fetchone()[0], 1)
            self.assertTrue(any(row['from'] == 'open_set_id' and row['table'] == 'inventory_open_sets' and row['to'] == 'id' for row in con.execute('PRAGMA foreign_key_list(inventory_document_lines)')))
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(self.snapshot(before), before)
