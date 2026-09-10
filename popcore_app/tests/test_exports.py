import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import db


class CsvSafetyTests(unittest.TestCase):
    def test_formula_like_text_is_neutralized(self):
        for value in ('=1+1', '+cmd', '-formula', '@SUM(A1:A2)', '\t=1', '\r=1',
                      '  @SUM(A1:A2)'):
            with self.subTest(value=value):
                self.assertTrue(db.esc_csv(value).startswith("'"))

    def test_numeric_values_keep_their_numeric_representation(self):
        self.assertEqual(db.esc_csv(-2), '-2')
        self.assertEqual(db.esc_csv(12.5), '12.5')

    def test_standard_csv_quoting_is_preserved(self):
        self.assertEqual(db.esc_csv('name,"quoted"'), '"name,""quoted"""')


if __name__ == '__main__':
    unittest.main()
