"""Every product search finds a product by its shop name (记账名) and returns its product name."""
import csv
import io
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from support import IsolatedApiCase  # noqa: E402
import clover_match_review as review  # noqa: E402
from catalog_identity import product_search_text_sql  # noqa: E402


class ProductSearchShopNameTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.series_id = con.execute("INSERT INTO product_series(name) VALUES ('Hirono Shelter')").lastrowid
            # A stale search_blob (blank) must not hide the live shop name or product name.
            self.plush = con.execute(
                """INSERT INTO products(sku, jizhanming, name_cn_en, search_blob, series_id,
                                        stock_form, stock_unit, identity_status, clover_item_name)
                   VALUES ('CL-1', '小野暗场毛绒', 'Hirono Shelter Plush', '', ?, 'random_box', 'box',
                           'verified', 'Hirono Shelter Plush')""", (self.series_id,)).lastrowid
            # A merged Clover product keeps a second legacy shop name only as an alias.
            self.pixar = con.execute(
                """INSERT INTO products(sku, jizhanming, name_cn_en, search_blob, clover_item_name)
                   VALUES ('CL-2', '萌粒皮克斯', 'POP BEAN Pixar Series', 'cl-2 萌粒皮克斯 pop bean pixar series',
                           'POP BEAN Pixar Series')""").lastrowid
            con.execute("INSERT INTO product_aliases(product_id, alias, alias_norm) VALUES (?, '萌粒dimoo皮克斯', 'mlz')",
                        (self.pixar,))
            con.execute('INSERT INTO stock(product_id, store_id, upstairs_qty, instore_qty) VALUES (?, ?, 1, 2)',
                        (self.plush, self.store_id))
            con.execute('INSERT INTO stock(product_id, store_id, upstairs_qty, instore_qty) VALUES (?, ?, 0, 3)',
                        (self.pixar, self.store_id))
            con.execute("INSERT INTO inventory_access VALUES ('auth0|manager', ?)", (self.store_id,))
            con.commit()

    def search(self, q, role='staff'):
        response = self.client.get('/api/products/search', query_string={'q': q, 'limit': 8},
                                   headers=self.headers(role))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_shop_name_finds_product_and_returns_product_name_even_with_stale_blob(self):
        first, *_ = self.search('暗场毛绒')
        self.assertEqual((first['id'], first['name_cn_en'], first['jizhanming']),
                         (self.plush, 'Hirono Shelter Plush', '小野暗场毛绒'))
        self.assertNotIn('alias_text', first)
        self.assertEqual(self.search('shelter plush')[0]['id'], self.plush)

    def test_alias_from_merged_shop_name_finds_the_clover_product_first(self):
        results = self.search('萌粒dimoo皮克斯')
        self.assertEqual(results[0]['id'], self.pixar)
        self.assertEqual(results[0]['name_cn_en'], 'POP BEAN Pixar Series')
        self.assertTrue(all('alias_text' not in row for row in results))
        self.assertIn('is_bestseller', results[0])

    def test_stock_list_and_exports_use_the_same_search_text(self):
        for q, product, sku, other in (('暗场', self.plush, 'CL-1', 'CL-2'),
                                       ('dimoo皮克斯', self.pixar, 'CL-2', 'CL-1')):
            with self.subTest(q=q):
                rows = self.client.get('/api/stock', query_string={'store_code': 'DT', 'q': q},
                                       headers=self.headers()).get_json()
                self.assertEqual([row['id'] for row in rows], [product])
                for url, params in (('/api/stock/export', {'store_code': 'DT', 'q': q}),
                                    ('/api/products/export', {'q': q})):
                    exported = self.client.get(url, query_string=params, headers=self.headers('manager'))
                    self.assertEqual(exported.status_code, 200, url)
                    text = exported.get_data(as_text=True)
                    self.assertIn(sku, text, url)
                    self.assertNotIn(other, text, url)

    def test_series_view_matches_product_name_and_aliases_when_shop_name_is_set(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET series_id=? WHERE id=?', (self.series_id, self.pixar))
            con.commit()
        for query, expected in (('Shelter Plush', [self.series_id]), ('小野暗场', [self.series_id]),
                                ('dimoo皮克斯', [self.series_id]), ('pop bean pixar', [self.series_id]),
                                ('no such product', [])):
            with self.subTest(query=query):
                response = self.client.get('/api/inventory/series', query_string={'store_code': 'DT', 'q': query},
                                           headers=self.headers('manager'))
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertEqual([s['id'] for s in response.get_json()['series']], expected)

    def test_search_text_sql_reads_live_names_and_aliases(self):
        with closing(self.connect()) as con:
            text = dict(con.execute(f'SELECT id, {product_search_text_sql("p")} FROM products p').fetchall())
        self.assertIn('小野暗场毛绒', text[self.plush])
        self.assertIn('hirono shelter plush', text[self.plush])
        self.assertIn('萌粒dimoo皮克斯', text[self.pixar])

    def test_restock_search_reads_the_selected_stores_warehouse_stock(self):
        with closing(self.connect()) as con:
            mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            con.execute('INSERT INTO stock(product_id, store_id, upstairs_qty, instore_qty) VALUES (?, ?, 0, 5)',
                        (self.plush, mk))
            con.commit()
        for store_code, upstairs in (('DT', 1), ('mk', 0)):
            for q in ('', '暗场毛绒'):  # the plain list and the scored search read stock separately
                with self.subTest(store_code=store_code, q=q):
                    response = self.client.get('/api/products/search', headers=self.headers(), query_string={
                        'q': q, 'include_stock': 1, 'limit': 20, 'store_code': store_code})
                    self.assertEqual(response.status_code, 200, response.get_json())
                    rows = {row['id']: row for row in response.get_json()}
                    self.assertEqual(rows[self.plush]['upstairs_qty'], upstairs)
        unknown = self.client.get('/api/products/search', headers=self.headers(),
                                  query_string={'q': '暗场', 'include_stock': 1, 'store_code': 'XX'})
        self.assertEqual(unknown.status_code, 400)

    def test_clover_target_options_carry_shop_names_for_search(self):
        with closing(self.connect()) as con:
            options = {row['id']: row for row in review.list_reviews(con)['clover_products']}
        self.assertIn('小野暗场毛绒', options[self.plush]['search_text'])
        self.assertIn('萌粒dimoo皮克斯', options[self.pixar]['search_text'])
