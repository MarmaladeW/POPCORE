"""Bulk identity sheet: manager export, preview and atomic confirm through update_product_identity."""
import csv
import io
from contextlib import closing

from support import IsolatedApiCase

HEADER = 'product_id,SKU,series_id,series,stock_form,stock_unit,design_name'


class IdentitySheetTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            stores = {r['code']: r['id'] for r in con.execute('SELECT id, code FROM stores')}
            self.existing_series = con.execute(
                "INSERT INTO product_series(name) VALUES ('Existing Series')").lastrowid
            def product(sku, **extra):
                columns = {'sku': sku, 'name_cn_en': sku.title(), 'jizhanming': sku.lower(), **extra}
                return con.execute('INSERT INTO products (' + ', '.join(columns) + ') VALUES ('
                                   + ', '.join('?' for _ in columns) + ')', list(columns.values())).lastrowid
            self.box_id = product('BOX-1', ip_series='Legacy Boxes')
            self.plain_id = product('PLAIN-1')
            self.locked_id = product('LOCKED-1', stock_form='random_box', stock_unit='box')
            self.verified_id = product('DONE-1', stock_form='ordinary', stock_unit='piece',
                                       identity_status='verified')
            self.set_id = product('SET-1')
            product('BLANK-1')
            con.execute('INSERT INTO stock (product_id, store_id, upstairs_qty, instore_qty) VALUES (?,?,0,4)',
                        (self.box_id, stores['MK']))
            con.execute('INSERT INTO stock (product_id, store_id, upstairs_qty, instore_qty) VALUES (?,?,3,0)',
                        (self.locked_id, stores['DT']))
            con.execute('INSERT INTO stock (product_id, store_id, upstairs_qty, instore_qty) VALUES (?,?,0,0)',
                        (self.plain_id, stores['DT']))
            con.commit()

    # helpers -------------------------------------------------------------
    def export(self, query='', role='manager'):
        return self.client.get('/api/products/identity-sheet' + query, headers=self.headers(role))

    def export_rows(self, query=''):
        response = self.export(query)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return list(csv.DictReader(io.StringIO(response.get_data(as_text=True).lstrip('﻿'))))

    def preview(self, text, role='manager'):
        return self.client.post('/api/products/identity-import/preview',
                                headers=self.headers(role), json={'text': text})

    def preview_ok(self, text):
        response = self.preview(text)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def confirm(self, rows, key='identity-1', role='manager'):
        headers = self.headers(role)
        if key:
            headers['Idempotency-Key'] = key
        return self.client.post('/api/products/identity-import/confirm', headers=headers, json={'rows': rows})

    def all_tables(self, exclude=()):
        with closing(self.connect()) as con:
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
                      if not r[0].startswith('sqlite_') and r[0] not in exclude]
        return self.snapshot(tables)

    def identity(self, product_id):
        with closing(self.connect()) as con:
            row = con.execute('''SELECT ps.name AS series, stock_form, stock_unit, design_name, identity_status
                                 FROM products p LEFT JOIN product_series ps ON ps.id=p.series_id
                                 WHERE p.id=?''', (product_id,)).fetchone()
            return tuple(row)

    def by_line(self, result):
        return {row['line']: row for row in result['rows']}

    # export --------------------------------------------------------------
    def test_export_is_manager_only_and_filters_by_store_stock_and_status(self):
        self.assertEqual(self.export(role='staff').status_code, 403)
        response = self.export('?store_code=dt&unverified=1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('identity_sheet_DT_unverified_', response.headers['Content-Disposition'])
        text = response.get_data(as_text=True)
        self.assertTrue(text.startswith('﻿product_id,SKU,name,记账名,legacy_series,series_id,series,'
                                        'stock_form,stock_unit,design_name,identity_status'))
        rows = list(csv.DictReader(io.StringIO(text.lstrip('﻿'))))
        # TEST-1 (2 floor, 10 upstairs) and LOCKED-1 (3 upstairs) have DT stock; PLAIN-1's row is all zero.
        self.assertEqual({r['SKU'] for r in rows}, {'TEST-1', 'LOCKED-1'})
        self.assertEqual({r['SKU'] for r in self.export_rows('?store_code=MK')}, {'BOX-1'})
        everything = {r['SKU']: r for r in self.export_rows()}
        self.assertEqual(len(everything), 7)
        self.assertEqual(everything['DONE-1']['identity_status'], 'verified')
        self.assertEqual(everything['BOX-1']['legacy_series'], 'Legacy Boxes')
        self.assertNotIn('DONE-1', {r['SKU'] for r in self.export_rows('?unverified=1')})
        self.assertEqual(self.export('?store_code=XX').status_code, 400)

    # preview -------------------------------------------------------------
    def test_preview_reports_each_row_and_writes_nothing(self):
        before = self.all_tables()
        result = self.preview_ok('\n'.join([
            HEADER,
            f'{self.product_id},TEST-1,,Existing Series,ordinary,,',             # 2 update via exact name
            f',box-1,{self.existing_series},,random_box,box,',                     # 3 update via id + SKU
            f',PLAIN-1,,New Series,confirmed_design,piece,Alpha',                  # 4 new series
            ',SET-1,,,sealed_set,,',                                               # 5 needs a conversion
            f'{self.verified_id},DONE-1,,,ordinary,piece,',                        # 6 unchanged
            f'{self.locked_id},LOCKED-1,,,ordinary,,',                             # 7 locked by stock
            ',NOPE-9,,,ordinary,,',                                                # 8 unknown SKU
            f'{self.box_id},TEST-1,,,ordinary,,',                                  # 9 id/SKU mismatch
            ',BLANK-1,,,,,',                                                       # 10 nothing entered
            f'{self.product_id},,,,ordinary,,',                                    # 11 duplicate product
        ]))
        rows = self.by_line(result)
        self.assertEqual([rows[n]['status'] for n in range(2, 12)],
                         ['update', 'update', 'update', 'rejected', 'unchanged', 'rejected',
                          'rejected', 'rejected', 'skipped', 'rejected'])
        self.assertEqual((result['update'], result['unchanged'], result['skipped'], result['rejected']),
                         (3, 1, 1, 5))
        self.assertEqual(result['new_series'], ['New Series'])
        self.assertEqual(rows[2]['values']['series_id'], self.existing_series)
        self.assertEqual(rows[2]['values']['stock_unit'], 'piece')  # derived from the form
        self.assertEqual(rows[2]['before']['identity_status'], 'unverified')
        self.assertEqual(rows[3]['product_id'], self.box_id)
        self.assertTrue(rows[4]['values']['new_series'])
        self.assertIn('reviewed conversion', rows[5]['reason'])
        self.assertEqual(rows[7]['code'], 'identity_locked')
        self.assertIn('exact SKU', rows[8]['reason'])
        self.assertIn('has SKU BOX-1', rows[9]['reason'])
        self.assertIn('more than once', rows[11]['reason'])
        self.assertEqual(self.all_tables(), before)

    def test_preview_rejects_unclear_series_and_design_conflicts_between_rows(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET stock_form=NULL, stock_unit=NULL WHERE id=?', (self.locked_id,))
            con.commit()
        result = self.preview_ok('\n'.join([
            'SKU,series,series_id,stock_form,design_name',
            'TEST-1,existing  series,,confirmed_design,A',             # 2 near-duplicate of an existing series
            f'BOX-1,Wrong Name,{self.existing_series},random_box,',   # 3 id and name disagree
            'PLAIN-1,Fresh,,confirmed_design,Alpha',                  # 4 new series
            'LOCKED-1,fresh,,confirmed_design,Beta',                  # 5 spelling of the new series
            'SET-1,Fresh,,confirmed_design, alpha ',                  # 6 same design as row 4
            'DONE-1,,,confirmed_design,Gamma',                        # 7 design without series
        ]))
        rows = self.by_line(result)
        self.assertEqual(rows[2]['code'], 'series_conflict')
        self.assertIn('not "Wrong Name"', rows[3]['reason'])
        self.assertEqual(rows[4]['status'], 'update')
        self.assertEqual(rows[5]['code'], 'series_conflict')
        self.assertEqual(rows[6]['code'], 'design_conflict')
        self.assertIn('requires a series', rows[7]['reason'])
        self.assertEqual(result['new_series'], ['Fresh'])

    def test_preview_rejects_unsupported_columns_and_staff(self):
        self.assertEqual(self.preview(HEADER + f'\n{self.product_id},,,,ordinary,,', role='staff').status_code, 403)
        for text in ('SKU,stock_form,instore_qty\nTEST-1,ordinary,4',
                     'SKU,name\nTEST-1,Only names',
                     'stock_form\nordinary',
                     'SKU,SKU,stock_form\nTEST-1,TEST-1,ordinary',
                     ''):
            with self.subTest(text=text):
                response = self.preview(text)
                self.assertEqual((response.status_code, response.get_json()['code']), (400, 'invalid_input'))

    def test_exported_sheet_round_trips_as_skipped_or_unchanged(self):
        response = self.export()
        result = self.preview_ok(response.get_data(as_text=True))
        statuses = {row['sku']: row['status'] for row in result['rows']}
        self.assertEqual(statuses['DONE-1'], 'unchanged')
        self.assertEqual(statuses['TEST-1'], 'skipped')
        # LOCKED-1 already has a form, so the exported row proposes verifying it as it stands.
        self.assertEqual(statuses['LOCKED-1'], 'update')

    # confirm -------------------------------------------------------------
    def test_confirm_applies_atomically_without_touching_stock_and_replays(self):
        result = self.preview_ok('\n'.join([
            HEADER,
            f'{self.product_id},,,Existing Series,ordinary,,',
            f'{self.box_id},,,New Series,random_box,box,',
            f'{self.plain_id},,,New Series,confirmed_design,,Alpha',
            ',SET-1,,,sealed_set,,',
        ]))
        accepted = [row for row in result['rows'] if row['status'] == 'update']
        self.assertEqual(len(accepted), 3)
        stock_before = self.all_tables(exclude=('products', 'product_series', 'operation_requests'))
        response = self.confirm(accepted)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json(), {'ok': True, 'updated': 3, 'unchanged': 0,
                                               'series_created': ['New Series']})
        self.assertEqual(self.identity(self.product_id), ('Existing Series', 'ordinary', 'piece', None, 'verified'))
        self.assertEqual(self.identity(self.box_id), ('New Series', 'random_box', 'box', None, 'verified'))
        self.assertEqual(self.identity(self.plain_id), ('New Series', 'confirmed_design', 'piece', 'Alpha', 'verified'))
        self.assertEqual(self.identity(self.set_id)[-1], 'unverified')
        self.assertEqual(self.all_tables(exclude=('products', 'product_series', 'operation_requests')),
                         stock_before)
        after = self.all_tables()
        replay = self.confirm(accepted)
        self.assertEqual((replay.status_code, replay.get_json()), (200, response.get_json()))
        self.assertEqual(self.all_tables(), after)
        changed = self.confirm(accepted[:1])
        self.assertEqual((changed.status_code, changed.get_json()['code']), (409, 'idempotency_conflict'))
        # A fresh key finds the rows already applied: no writes, nothing reported as updated.
        again = self.confirm(accepted[:1], key='identity-2')
        self.assertEqual(again.get_json(), {'ok': True, 'updated': 0, 'unchanged': 1, 'series_created': []})
        # A second manager confirming the same preview, including the rows that created
        # "New Series", finds every row already at its target.
        second = self.confirm(accepted, key='identity-3')
        self.assertEqual((second.status_code, second.get_json()),
                         (200, {'ok': True, 'updated': 0, 'unchanged': 3, 'series_created': []}))
        self.assertEqual(self.all_tables(exclude=('operation_requests',)),
                         {k: v for k, v in after.items() if k != 'operation_requests'})

    def test_stale_row_rolls_back_every_row(self):
        result = self.preview_ok('\n'.join([
            HEADER,
            f'{self.product_id},,,Brand New,ordinary,,',
            f'{self.box_id},,,,random_box,,',
        ]))
        accepted = [row for row in result['rows'] if row['status'] == 'update']
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET stock_form='ordinary', stock_unit='piece' WHERE id=?", (self.box_id,))
            con.commit()
        before = self.all_tables()
        response = self.confirm(accepted)
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'stale_preview'))
        self.assertIn('BOX-1', response.get_json()['error'])
        self.assertEqual(self.all_tables(), before)

    def test_confirm_reapplies_identity_rules(self):
        result = self.preview_ok(HEADER + f'\n{self.locked_id},,,,random_box,,')
        accepted = [row for row in result['rows'] if row['status'] == 'update']
        self.assertEqual(len(accepted), 1)
        # Tampered rows still go through update_product_identity and its lock rules.
        tampered = [{**accepted[0], 'before': {**accepted[0]['before']},
                     'values': {**accepted[0]['values'], 'stock_form': 'ordinary', 'stock_unit': 'piece'}}]
        before = self.all_tables()
        response = self.confirm(tampered)
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'identity_locked'))
        self.assertEqual(self.all_tables(), before)

    def test_confirm_requires_key_manager_and_accepted_rows(self):
        result = self.preview_ok(HEADER + f'\n{self.product_id},,,,ordinary,,\n{self.plain_id},,,,,,')
        accepted = [row for row in result['rows'] if row['status'] == 'update']
        skipped = [row for row in result['rows'] if row['status'] == 'skipped']
        self.assertEqual(self.confirm(accepted, role='staff').status_code, 403)
        missing = self.confirm(accepted, key=None)
        self.assertEqual((missing.status_code, missing.get_json()['code']), (400, 'invalid_input'))
        for rows in ([], skipped, accepted + accepted, [{**accepted[0], 'values': None}]):
            with self.subTest(rows=rows):
                response = self.confirm(rows, key='bad-rows')
                self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(self.identity(self.product_id)[-1], 'unverified')
