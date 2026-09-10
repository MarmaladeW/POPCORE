import sys
import tempfile
from pathlib import Path

from support import APP_DIR, IsolatedApiCase

ROOT = APP_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.rehearse_inventory_migration import rehearse  # noqa: E402


class InventoryMigrationRehearsalTests(IsolatedApiCase):
    def test_representative_fixture_activates_and_refuses_reuse(self):
        temp_root = ROOT / '.local' / 'tmp'
        with tempfile.TemporaryDirectory(dir=temp_root) as folder:
            output = Path(folder) / 'representative'
            report = rehearse('representative', output)
            self.assertTrue(report['activated'])
            self.assertEqual(report['integrity'], 'ok')
            self.assertEqual(report['foreign_key_errors'], [])
            self.assertTrue(report['ledger_reconciled'])
            self.assertTrue(report['retry_identical'])
            with self.assertRaises(SystemExit):
                rehearse('representative', output)

    def test_ambiguous_fixture_reports_unresolved_and_stays_legacy(self):
        temp_root = ROOT / '.local' / 'tmp'
        with tempfile.TemporaryDirectory(dir=temp_root) as folder:
            output = Path(folder) / 'ambiguous'
            report = rehearse('ambiguous', output)
            self.assertFalse(report['activated'])
            self.assertEqual(len(report['unresolved']), 1)


if __name__ == '__main__':
    import unittest
    unittest.main()
