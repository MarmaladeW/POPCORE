"""The Clover item import creates the product master without touching stock, identity or aliases."""
import csv
import sys
import tempfile
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from support import IsolatedApiCase  # noqa: E402
import import_clover_items as importer  # noqa: E402


def write_csv(path, rows, headers=('clover_item_name', 'price', 'category', 'clover_item_id', 'product_code')):
    with open(path, 'w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


class CloverItemImportTests(IsolatedApiCase):
    def rows(self):
        return [
            {'clover_item_name': 'Zsiga x Care Bears Series Figures', 'price': '26.99', 'category': 'POPMART'},
            {'clover_item_name': 'Sonny Angel Animal Series Ver.1 Figure', 'price': '15.99', 'category': 'SONNY ANGEL',
             'product_code': '4542202655511'},
            {'clover_item_name': 'Custom Gift Card', 'price': 'Variable', 'category': 'Gift Cards'},
        ]

    def test_import_creates_products_and_barcodes_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(dir=self.tempdir.name) as directory:
            path = Path(directory) / 'items.csv'
            write_csv(path, self.rows())
            rows = importer.read_rows(path)
            with closing(self.connect()) as con:
                before_stock = con.execute('SELECT COUNT(*) FROM stock').fetchone()[0]
                self.assertEqual(importer.import_items(con, rows),
                                 {'created': 3, 'updated': 0, 'unchanged': 0, 'barcodes': 1})
                self.assertEqual(importer.import_items(con, rows),
                                 {'created': 0, 'updated': 0, 'unchanged': 3, 'barcodes': 0})
                product = con.execute("SELECT * FROM products WHERE clover_item_name=?",
                                      ('Sonny Angel Animal Series Ver.1 Figure',)).fetchone()
                self.assertEqual((product['sku'], product['name_cn_en'], product['brand'], product['price'],
                                  product['jizhanming'], product['identity_status'] or 'unverified'),
                                 ('CL00002', 'Sonny Angel Animal Series Ver.1 Figure', 'SONNY ANGEL', 15.99,
                                  '', 'unverified'))
                self.assertIn('sonny angel', product['search_blob'])
                self.assertEqual([tuple(r) for r in con.execute('SELECT code, code_kind, input_unit FROM product_barcodes WHERE product_id=?',
                                                                (product['id'],))], [('4542202655511', 'manufacturer', 'piece')])
                gift = con.execute("SELECT price FROM products WHERE clover_item_name='Custom Gift Card'").fetchone()
                self.assertIsNone(gift['price'])
                self.assertEqual(con.execute('SELECT COUNT(*) FROM stock').fetchone()[0], before_stock)

    def test_reimport_updates_price_and_id_but_keeps_jizhanming_aliases_and_identity(self):
        with tempfile.TemporaryDirectory(dir=self.tempdir.name) as directory:
            path = Path(directory) / 'items.csv'
            write_csv(path, self.rows())
            with closing(self.connect()) as con:
                importer.import_items(con, importer.read_rows(path))
                product_id = con.execute("SELECT id FROM products WHERE clover_item_name=?",
                                         ('Zsiga x Care Bears Series Figures',)).fetchone()[0]
                con.execute("UPDATE products SET jizhanming='Zsiga 爱心熊', stock_form='ordinary', stock_unit='piece',"
                            " identity_status='verified' WHERE id=?", (product_id,))
                con.execute("INSERT INTO product_aliases(product_id, alias, alias_norm) VALUES (?, 'zsiga care bears', 'zsiga care bears')",
                            (product_id,))
                con.commit()
            changed = self.rows()
            changed[0].update(price='28.99', clover_item_id='A' * 13)
            write_csv(path, changed)
            with closing(self.connect()) as con:
                self.assertEqual(importer.import_items(con, importer.read_rows(path))['updated'], 1)
                product = con.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
                self.assertEqual((product['price'], product['clover_item_id'], product['jizhanming'],
                                  product['identity_status'], product['stock_form']),
                                 (28.99, 'A' * 13, 'Zsiga 爱心熊', 'verified', 'ordinary'))
                self.assertIn('zsiga 爱心熊', product['search_blob'])
                self.assertEqual(con.execute('SELECT COUNT(*) FROM product_aliases WHERE product_id=?',
                                             (product_id,)).fetchone()[0], 1)
                # A later rename on Clover is recognised by the item ID, not the name.
                renamed = self.rows()
                renamed[0].update(clover_item_name='Zsiga × Care Bears Series', clover_item_id='A' * 13)
                write_csv(path, renamed)
                importer.import_items(con, importer.read_rows(path))
                self.assertEqual(con.execute('SELECT COUNT(*) FROM products').fetchone()[0], 4)  # fixture + 3
                self.assertEqual(con.execute('SELECT name_cn_en FROM products WHERE id=?', (product_id,)).fetchone()[0],
                                 'Zsiga × Care Bears Series')

    def test_ligature_names_are_cleaned_and_earlier_imports_renamed_in_place(self):
        with tempfile.TemporaryDirectory(dir=self.tempdir.name) as directory:
            path = Path(directory) / 'items.csv'
            with closing(self.connect()) as con:
                con.execute("INSERT INTO products(sku, name_cn_en, clover_item_name, brand) VALUES ('CL00001', 'CRYBABY X Powerpu\ufb00 Girls Series Figures', 'CRYBABY X Powerpu\ufb00 Girls Series Figures', 'POPMART')")
                con.commit()
                write_csv(path, [{'clover_item_name': 'CRYBABY X Powerpu\ufb00 Girls Series Figures', 'price': '26.99', 'category': 'POPMART'}])
                rows = importer.read_rows(path)
                self.assertEqual(rows[0]['clover_item_name'], 'CRYBABY X Powerpuff Girls Series Figures')
                self.assertEqual(importer.import_items(con, rows)['updated'], 1)
                names = [r[0] for r in con.execute("SELECT clover_item_name FROM products WHERE clover_item_name IS NOT NULL")]
                self.assertEqual(names, ['CRYBABY X Powerpuff Girls Series Figures'])
                self.assertEqual(con.execute("SELECT name_cn_en FROM products WHERE sku='CL00001'").fetchone()[0],
                                 'CRYBABY X Powerpuff Girls Series Figures')

    def test_invalid_rows_are_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory(dir=self.tempdir.name) as directory:
            path = Path(directory) / 'items.csv'
            for bad in ([{'clover_item_name': '', 'price': '1', 'category': 'X'}],
                        [{'clover_item_name': 'A', 'price': 'abc', 'category': 'X'}],
                        [{'clover_item_name': 'A', 'price': '-1', 'category': 'X'}],
                        [{'clover_item_name': 'A', 'price': '1', 'category': 'X', 'clover_item_id': 'short'}],
                        [{'clover_item_name': 'A', 'price': '1', 'category': 'X'},
                         {'clover_item_name': 'A', 'price': '2', 'category': 'Y'}]):
                write_csv(path, bad)
                with self.assertRaises(ValueError):
                    importer.read_rows(path)
            write_csv(path, [{'name': 'A'}], headers=('name',))
            with self.assertRaises(ValueError):
                importer.read_rows(path)
            with closing(self.connect()) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM products').fetchone()[0], 1)
