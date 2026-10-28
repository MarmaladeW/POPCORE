"""Add product with a blank SKU: the next POPCORE ID for the product's brand (owner, 2026-10-24)."""
from contextlib import closing

from support import IsolatedApiCase


class CatalogCase(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            con.executemany('INSERT INTO products (sku, name_cn_en, brand, ip_series) VALUES (?, ?, ?, ?)', [
                ('PM0649', 'Labubu box', 'POPMART', 'Labubu'),
                ('PM0650', 'Twinkle pillow', 'Pop Mart', ''),
                ('PM0650-SET', 'Twinkle pillow set', 'Pop Mart', ''),
                ('PM0012-03', 'A design', 'POPMART', 'Labubu'),
                ('DR0112', 'Smiski box', 'SMISKI', 'Smiski & SA'),
                # Left over from the old numbering: never counted, never breaks numbering.
                ('SP00001', 'Ring box', '', 'POPMART'),
                ('SP00001-13', 'Ring box design', '', 'POPMART'),
            ])
            con.commit()


class BlankSkuTests(CatalogCase):
    def create(self, **fields):
        return self.client.post('/api/products', headers=self.headers('manager'),
                                json={'name_cn_en': 'New product', **fields})

    def test_series_names_the_brand_and_numbering_follows_every_id(self):
        response = self.create(ip_series='POPMART', product_type='盲盒')
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()['sku'], 'PM0651')
        self.assertEqual(self.create(brand='Sonny Angel').get_json()['sku'], 'DR0113')
        self.assertEqual(self.create(ip_series='Smiski & SA').get_json()['sku'], 'DR0114')
        self.assertEqual(self.create(brand='Hitoy').get_json()['sku'], 'HT0001')
        with closing(self.connect()) as con:
            blob = con.execute("SELECT search_blob FROM products WHERE sku='PM0651'").fetchone()[0]
        self.assertTrue(blob.startswith('pm0651 '), blob)

    def test_an_ip_series_takes_the_code_its_products_carry(self):
        self.assertEqual(self.create(ip_series='labubu').get_json()['sku'], 'PM0651')

    def test_no_brand_or_unknown_series_asks_for_one(self):
        for fields in ({}, {'ip_series': 'Brand new IP'}):
            response = self.create(**fields)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()['code'], 'brand_code_unknown')
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM products WHERE name_cn_en='New product'").fetchone()[0], 0)

    def test_a_typed_sku_is_kept(self):
        response = self.create(sku='pm9000', ip_series='POPMART')
        self.assertEqual(response.get_json()['sku'], 'PM9000')


class CreateOnceTests(CatalogCase):
    """A weak connection: Create pressed again after its response was lost."""

    def create_with_key(self, key, **fields):
        return self.client.post('/api/products', headers={**self.headers('manager'), 'Idempotency-Key': key},
                                json={'name_cn_en': 'Twice pressed', 'ip_series': 'POPMART', **fields})

    def products_named(self, name):
        with closing(self.connect()) as con:
            return [tuple(row) for row in con.execute('SELECT id, sku FROM products WHERE name_cn_en=?', (name,))]

    def test_the_same_request_twice_makes_one_product(self):
        first = self.create_with_key('form-1')
        again = self.create_with_key('form-1')
        self.assertEqual((first.status_code, again.status_code), (201, 201), again.get_json())
        self.assertEqual(again.get_json(), first.get_json())
        self.assertEqual(self.products_named('Twice pressed'), [(first.get_json()['id'], 'PM0651')])

    def test_a_changed_form_under_the_same_key_is_refused(self):
        self.create_with_key('form-2')
        changed = self.create_with_key('form-2', price=9.99)
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.get_json()['code'], 'idempotency_conflict')
        self.assertEqual(len(self.products_named('Twice pressed')), 1)

    def test_a_refused_create_leaves_its_key_free(self):
        refused = self.create_with_key('form-3', ip_series='')
        self.assertEqual(refused.get_json()['code'], 'brand_code_unknown')
        retried = self.create_with_key('form-3')
        self.assertEqual(retried.status_code, 201, retried.get_json())
        self.assertEqual(retried.get_json()['sku'], 'PM0651')
