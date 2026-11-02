"""Each product records when it was added, so the catalog can list the newest first (owner, 2026-10-31)."""
import re
from contextlib import closing

from support import IsolatedApiCase, db

UTC_TIME = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')


class ProductCreatedAtTests(IsolatedApiCase):
    def added(self, product_id):
        with closing(self.connect()) as con:
            return con.execute('SELECT created_at FROM products WHERE id=?', (product_id,)).fetchone()[0]

    def test_every_way_of_adding_a_product_records_the_time(self):
        response = self.client.post('/api/products', headers=self.headers('manager'),
                                    json={'name_cn_en': 'New product', 'sku': 'NEW-1'})
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertRegex(self.added(response.get_json()['id']), UTC_TIME)
        # Inserted directly, as the sheet sync, imports and scripts do.
        self.assertRegex(self.added(self.product_id), UTC_TIME)

    def test_a_time_given_on_insert_is_kept(self):
        with closing(self.connect()) as con:
            product = con.execute("INSERT INTO products(sku, created_at) VALUES ('OLD-1', '2026-01-02 03:04:05')").lastrowid
            con.commit()
        self.assertEqual(self.added(product), '2026-01-02 03:04:05')

    def test_products_from_before_have_no_time_and_new_ones_get_one(self):
        with closing(self.connect()) as con:
            con.execute('DROP TRIGGER products_created_at')
            con.execute('ALTER TABLE products DROP COLUMN created_at')
            con.execute("DELETE FROM _migrations WHERE name='product_created_at'")
            con.commit()
        db.migrate_db()
        db.migrate_db()  # safe to re-run
        with closing(self.connect()) as con:
            later = con.execute("INSERT INTO products(sku) VALUES ('NEW-2')").lastrowid
            con.commit()
        self.assertIsNone(self.added(self.product_id))
        self.assertRegex(self.added(later), UTC_TIME)

    def test_catalog_search_returns_when_each_product_was_added(self):
        for params in ({'top_level': '1'}, {'top_level': '1', 'q': 'test product'}):
            with self.subTest(params=params):
                response = self.client.get('/api/products/search', query_string=params, headers=self.headers())
                self.assertEqual(response.status_code, 200, response.get_json())
                row = next(row for row in response.get_json() if row['id'] == self.product_id)
                self.assertRegex(row['created_at'], UTC_TIME)

    def test_inventory_list_returns_when_each_product_was_added(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access VALUES ('auth0|staff',?)", (self.store_id,))
            con.commit()
        response = self.client.get('/api/inventory/series?store_code=DT', headers=self.headers())
        self.assertEqual(response.status_code, 200, response.get_json())
        row = next(row for row in response.get_json()['products'] if row['id'] == self.product_id)
        self.assertRegex(row['created_at'], UTC_TIME)
