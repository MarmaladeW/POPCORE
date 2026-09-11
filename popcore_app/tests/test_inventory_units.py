import unittest
from contextlib import closing

from support import IsolatedApiCase

from catalog_identity import (
    add_conversion,
    assign_barcode,
    qualifies_for_fresh_set,
    receipt_units,
    resolve_barcode,
)


class ReceiptUnitTests(unittest.TestCase):
    def test_pack_math_uses_boxes_once(self):
        self.assertEqual(receipt_units(6, 2), 12)
        self.assertEqual(receipt_units(9, 1), 9)
        self.assertEqual(receipt_units(12, 1), 12)
        self.assertNotEqual(receipt_units(12, 1), 144)

    def test_pack_math_rejects_invalid_values_and_overflow(self):
        for value in (0, -1, 1.0, True, '', None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    receipt_units(value, 1)
        with self.assertRaises(ValueError):
            receipt_units(2**62, 4)


class FreshSetEligibilityTests(unittest.TestCase):
    def test_more_than_half_is_required(self):
        self.assertTrue(qualifies_for_fresh_set(7, 12))
        self.assertFalse(qualifies_for_fresh_set(6, 12))
        self.assertTrue(qualifies_for_fresh_set(5, 9))

class CatalogUnitTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.series_id = con.execute(
                "INSERT INTO product_series(name) VALUES ('Series A')"
            ).lastrowid
            self.other_series_id = con.execute(
                "INSERT INTO product_series(name) VALUES ('Series B')"
            ).lastrowid
            self.random_id = con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, series_id, stock_form, stock_unit)
                   VALUES ('RANDOM-A', 'Random A', ?, 'random_box', 'box')""",
                (self.series_id,),
            ).lastrowid
            self.design_id = con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, series_id, stock_form, stock_unit,
                    design_name)
                   VALUES ('DESIGN-A', 'Design A', ?, 'confirmed_design',
                           'piece', 'Design A')""",
                (self.series_id,),
            ).lastrowid
            self.other_random_id = con.execute(
                """INSERT INTO products
                   (sku, name_cn_en, series_id, stock_form, stock_unit)
                   VALUES ('RANDOM-B', 'Random B', ?, 'random_box', 'box')""",
                (self.other_series_id,),
            ).lastrowid
            self.set_ids = []
            for factor in (6, 9, 12):
                set_id = con.execute(
                    """INSERT INTO products
                       (sku, name_cn_en, series_id, stock_form, stock_unit)
                       VALUES (?, ?, ?, 'sealed_set', 'set')""",
                    (f'SET-{factor}', f'Set {factor}', self.series_id),
                ).lastrowid
                self.set_ids.append((set_id, factor))
            con.commit()

    def test_conversions_accept_reviewed_integer_sizes(self):
        with closing(self.connect()) as con:
            for set_id, factor in self.set_ids:
                result = add_conversion(con, set_id, self.random_id, factor)
                self.assertEqual(result['output_per_input'], factor)
            con.commit()
            self.assertEqual(
                con.execute('SELECT COUNT(*) FROM product_conversions').fetchone()[0],
                3,
            )

    def test_conversions_reject_invalid_relationships(self):
        with closing(self.connect()) as con:
            source_id = self.set_ids[0][0]
            invalid = (
                (source_id, self.other_random_id, 6),
                (source_id, source_id, 6),
                (self.random_id, source_id, 6),
                (source_id, self.design_id, 6),
                (source_id, self.random_id, 0),
                (source_id, self.random_id, 1.5),
                (source_id, self.random_id, 2**63),
            )
            for args in invalid:
                with self.subTest(args=args):
                    with self.assertRaises(ValueError):
                        add_conversion(con, *args)

    def test_barcode_resolution_preserves_leading_zeroes(self):
        with closing(self.connect()) as con:
            assign_barcode(
                con, self.random_id, '001234567890', 'manufacturer', 'box', 1
            )
            assign_barcode(
                con, self.design_id, '001234567890', 'manufacturer', 'piece', 1
            )
            assign_barcode(
                con, self.design_id, 'PC-0001', 'internal', 'piece', 1
            )
            con.commit()

            generic = resolve_barcode(con, '001234567890\r\n', 'confirmed')
            exact = resolve_barcode(con, 'PC-0001\n', 'confirmed')
            unknown = resolve_barcode(con, '1234567890', 'move')

        self.assertEqual(generic['status'], 'ambiguous')
        self.assertEqual(
            [c['product_id'] for c in generic['candidates']],
            [self.random_id, self.design_id],
        )
        self.assertEqual(exact['status'], 'exact')
        self.assertEqual(exact['candidates'][0]['product_id'], self.design_id)
        self.assertEqual(unknown, {'status': 'unknown', 'candidates': []})


if __name__ == '__main__':
    unittest.main()