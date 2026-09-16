"""Catalog paste import must never write stock or infer product identity."""
from contextlib import closing
from copy import deepcopy
from unittest.mock import patch

from support import IsolatedApiCase


class CatalogImportTests(IsolatedApiCase):
    def preview(self, text, role='manager'):
        return self.client.post('/api/products/import/preview',
                                headers=self.headers(role), json={'text': text})

    def confirm(self, rows, role='manager'):
        return self.client.post('/api/products/import/confirm',
                                headers=self.headers(role), json={'rows': rows})

    def preview_rows(self, text):
        response = self.preview(text)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['rows']

    def database_snapshot(self, exclude=()):
        with closing(self.connect()) as con:
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
                      if not r[0].startswith('sqlite_') and r[0] not in exclude]
        return self.snapshot(tables)

    def product(self, sku='TEST-1'):
        with closing(self.connect()) as con:
            row = con.execute('SELECT * FROM products WHERE sku=?', (sku,)).fetchone()
            return dict(row) if row else None

    def test_csv_export_headers_and_quoted_full_names_preview_without_writes(self):
        before = self.database_snapshot()
        response = self.preview(
            '\ufeffSKU,记账名,产品名称,系列,类型,品牌,单价,发售时间,版本/限量,渠道,备注\n'
            'TEST-1,Full short name,"Full name, with comma",Core,Plush,Brand,12.50,2026-09-16,Limited,Retail,"two\nlines"\n'
            'Part-S,New short name,New full name,Series,Figure,Maker,0,,,,\n'
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()
        self.assertEqual((result['created'], result['updated']), (1, 1))
        update, create = result['rows']
        self.assertEqual((update['sku'], update['action'], update['product_id']),
                         ('TEST-1', 'update', self.product_id))
        self.assertEqual(update['before']['name_cn_en'], 'Test Product')
        self.assertEqual(update['values']['name_cn_en'], 'Full name, with comma')
        self.assertEqual(update['values']['notes'], 'two\nlines')
        self.assertEqual(update['values']['price'], 12.5)
        self.assertEqual((create['sku'], create['action'], create['product_id'], create['before']),
                         ('Part-S', 'create', None, None))
        self.assertEqual(self.database_snapshot(), before)

    def test_confirm_changes_catalog_only_and_replay_is_noop(self):
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET stock_form='ordinary',stock_unit='piece',identity_status='verified',"
                        "sheet_ref='stable',brand='Keep brand' WHERE id=?", (self.product_id,))
            con.commit()
        before_other_tables = self.database_snapshot(exclude=('products',))
        rows = self.preview_rows('sku\tname_cn_en\tjizhanming\tprice\tboxes_per_dan\n'
                                 'test-1\tLong full product name\tFull bookkeeping name\t9.5\t6\n'
                                 'New SKU-S\tNew full product name\tNew shorthand\t0\t1')
        response = self.confirm(rows)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json(), {'ok': True, 'created': 1, 'updated': 1})
        product = self.product()
        self.assertEqual(product['name_cn_en'], 'Long full product name')
        self.assertEqual(product['boxes_per_dan'], 6)
        self.assertEqual(product['brand'], 'Keep brand')
        self.assertEqual(tuple(product[k] for k in ('stock_form', 'stock_unit', 'identity_status', 'sheet_ref')),
                         ('ordinary', 'piece', 'verified', 'stable'))
        self.assertIn('long full product name', product['search_blob'])
        self.assertIsNotNone(self.product('New SKU-S'))
        self.assertEqual(self.database_snapshot(exclude=('products',)), before_other_tables)
        after = self.database_snapshot()
        replay = self.confirm(rows)
        self.assertEqual(replay.status_code, 200, replay.get_json())
        self.assertEqual(replay.get_json(), {'ok': True, 'created': 0, 'updated': 0})
        self.assertEqual(self.database_snapshot(), after)

    def test_present_blanks_clear_and_absent_columns_are_preserved(self):
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET price=12,boxes_per_dan=6,notes='old note',brand='keep' WHERE id=?",
                        (self.product_id,))
            con.commit()
        rows = self.preview_rows('SKU,notes,price,boxes_per_dan\nTEST-1,,,')
        self.assertEqual(rows[0]['values'], {'notes': '', 'price': None, 'boxes_per_dan': None})
        self.assertEqual(self.confirm(rows).status_code, 200)
        product = self.product()
        self.assertEqual((product['notes'], product['price'], product['boxes_per_dan'], product['brand']),
                         ('', None, None, 'keep'))
        self.assertEqual(product['name_cn_en'], 'Test Product')

    def test_literal_skus_preserve_punctuation_and_never_match_fuzzy_names(self):
        rows = self.preview_rows('sku,name_cn_en\nPART-S,Test Product\nPARTS,Test Product\nPART S,Test Product')
        self.assertTrue(all(row['action'] == 'create' for row in rows))
        self.assertEqual(self.confirm(rows).get_json()['created'], 3)
        for sku in ('PART-S', 'PARTS', 'PART S'):
            self.assertEqual(self.product(sku)['sku'], sku)
        self.assertEqual(self.product()['name_cn_en'], 'Test Product')

    def test_duplicate_input_sku_and_invalid_csv_rejected_without_writes(self):
        before = self.database_snapshot()
        for text in ('', '\ufeff', 'sku,name_cn_en', 'name_cn_en\nMissing SKU', 'sku,name_cn_en\n,Name',
                     'sku,notes\nABC,one\nａｂｃ,two', 'sku,SKU\na,b',
                     'sku,notes\nA', 'sku,notes\nA,b,extra', 'sku,notes\nA,"unclosed',
                     'sku,notes\n\nA,ok'):
            with self.subTest(text=text):
                self.assertEqual(self.preview(text).status_code, 400)
        self.assertEqual(self.database_snapshot(), before)

    def test_unknown_stock_and_identity_headers_are_rejected(self):
        for header in ('qty', 'upstairs_qty', 'instore_qty', 'claw_qty', 'quantity', '库存',
                       'stock_form', 'stock_unit', 'series_id', 'design_name', 'identity_status',
                       'sheet_ref', 'unknown'):
            with self.subTest(header=header):
                self.assertEqual(self.preview(f'sku,{header}\nNEW,1').status_code, 400)

    def test_normalized_catalog_sku_conflict_blocks_preview_and_confirm(self):
        rows = self.preview_rows('sku,notes\nTEST-1,Changed')
        with closing(self.connect()) as con:
            con.execute("INSERT INTO products(sku,name_cn_en) VALUES ('ｔｅｓｔ-1','Duplicate identity')")
            con.commit()
        before = self.database_snapshot()
        self.assertEqual(self.preview('sku,notes\nTEST-1,Changed').status_code, 409)
        self.assertEqual(self.confirm(rows).status_code, 409)
        self.assertEqual(self.database_snapshot(), before)

    def test_numeric_validation_in_both_preview_and_confirm(self):
        row = self.preview_rows('sku,notes\nTEST-1,Changed')[0]
        for field, invalid_values in (
            ('price', ['nan', 'inf', '-1', 'abc', True, [], {}]),
            ('boxes_per_dan', ['0', '-1', '1.5', 'abc', '9223372036854775808', True, 1.5, [], {}]),
        ):
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    if isinstance(value, str):
                        self.assertEqual(self.preview(f'sku,{field}\nNEW,{value}').status_code, 400)
                    modified = deepcopy(row)
                    modified['values'][field] = value
                    self.assertEqual(self.confirm([modified]).status_code, 400)

    def test_stale_snapshot_rolls_back_earlier_create(self):
        rows = self.preview_rows('sku,name_cn_en\nNEW,New product\nTEST-1,Changed name')
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET notes='Concurrent edit' WHERE id=?", (self.product_id,))
            con.commit()
        before = self.database_snapshot()
        self.assertEqual(self.confirm(rows).status_code, 409)
        self.assertEqual(self.database_snapshot(), before)

    def test_create_replay_does_not_overwrite_different_existing_product(self):
        rows = self.preview_rows('sku,name_cn_en\nNEW,New product')
        with closing(self.connect()) as con:
            con.execute("INSERT INTO products(sku,name_cn_en) VALUES ('new','Different identity')")
            con.commit()
        before = self.database_snapshot()
        self.assertEqual(self.confirm(rows).status_code, 409)
        self.assertEqual(self.database_snapshot(), before)

    def test_confirm_rejects_bad_rows_and_forbidden_fields_atomically(self):
        rows = self.preview_rows('sku,notes\nNEW,Create\nTEST-1,Change')
        before = self.database_snapshot()
        for patch_values in ({'stock_form': 'ordinary'}, {'qty': 1}, {'sku': 'Another'},
                             {'name_cn_en': []}, {'search_blob': 'injected'}):
            modified = deepcopy(rows)
            modified[1]['values'].update(patch_values)
            self.assertEqual(self.confirm(modified).status_code, 400)
        for invalid_rows in ([], None, {}, [None], [rows[0], rows[0]]):
            self.assertEqual(self.confirm(invalid_rows).status_code, 400)
        for field, value, status in (('before', {}, 400), ('product_id', 9999, 409),
                                     ('action', 'delete', 400), ('sku', '', 400)):
            modified = deepcopy(rows)
            modified[1][field] = value
            self.assertEqual(self.confirm(modified).status_code, status)
        self.assertEqual(self.database_snapshot(), before)

    def test_catalog_import_and_sheet_sync_permissions(self):
        for role in ('staff', 'viewer'):
            self.assertEqual(self.preview('sku\nNEW', role).status_code, 403)
            self.assertEqual(self.confirm([], role).status_code, 403)
            for path in ('/api/products/sync-sheet', '/api/products/sync-sheet/confirm'):
                self.assertEqual(self.client.post(path, headers=self.headers(role), json={}).status_code, 403)
            self.assertEqual(self.client.get('/api/products/sync-sheet/last-sync', headers=self.headers(role)).status_code, 403)
        for role in ('manager', 'admin'):
            self.assertEqual(self.preview('sku\nNEW', role).status_code, 200)
            with patch('blueprints.products._fetch_sheet_rows', return_value=('ok', [])):
                self.assertEqual(self.client.post('/api/products/sync-sheet', headers=self.headers(role)).status_code, 200)
            self.assertEqual(self.client.post('/api/products/sync-sheet/confirm', headers=self.headers(role), json={}).status_code, 200)
            self.assertEqual(self.client.get('/api/products/sync-sheet/last-sync', headers=self.headers(role)).status_code, 200)

    def test_non_object_requests_and_non_text_input_return_400(self):
        for body in (None, [], {'text': None}, {'text': 42}):
            with self.subTest(body=body):
                response = self.client.post('/api/products/import/preview',
                                            headers=self.headers('manager'), json=body)
                self.assertEqual(response.status_code, 400)
        for path in ('/api/products/import/preview', '/api/products/import/confirm'):
            self.assertEqual(self.client.post(path, json={}).status_code, 401)

    def test_english_metadata_headers_preserve_all_supported_columns(self):
        fields = ('jizhanming', 'name_cn_en', 'ip_series', 'product_type', 'brand',
                  'release_date', 'edition_size', 'channel', 'notes')
        values = ['Full ' + field + ' value' for field in fields]
        rows = self.preview_rows('sku,' + ','.join(fields) + ',price,boxes_per_dan\n'
                                 + 'Complete SKU-S,' + ','.join(values) + ',17.25,12')
        self.assertEqual(self.confirm(rows).status_code, 200)
        product = self.product('Complete SKU-S')
        self.assertEqual({field: product[field] for field in fields}, dict(zip(fields, values)))
        self.assertEqual((product['price'], product['boxes_per_dan']), (17.25, 12))
