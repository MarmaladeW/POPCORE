"""Brands: one spelling everywhere (owner, 2026-11-20: "change all of Pop Mart into POPMART"), the one-off clean-up of
the stored brands (scripts/normalize_brands.py) and the 系列 IP fill for products the Dex gives no IP
(scripts/fill_ip_series.py)."""
import csv
import importlib.util
import io
import json
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path

from support import APP_DIR, IsolatedApiCase

import dex_book
from blueprints.products import BRAND_CODES, _brand_code
from brands import BRANDS, canonical_brand
from matcher import load_report_picks, report_identity_signature

ROOT = APP_DIR.parent
PERSON = {'sub': 'auth0|manager'}


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CanonicalBrandTests(IsolatedApiCase):
    def test_every_spelling_of_a_known_brand_becomes_its_one_name(self):
        for name, brand in (('Pop Mart', 'POPMART'), (' pop   mart ', 'POPMART'), ('POP-MART', 'POPMART'),
                            ('popmart', 'POPMART'), ('Top Toy', 'TOP TOY'), ('TopToy', 'TOP TOY'),
                            ('Toyzero+', 'TOYZERO+'), ('ToyZero Plus', 'TOYZERO+'), ('Sonny Angel', 'SONNY ANGEL'),
                            ('SonnyAngel', 'SONNY ANGEL'), ('Smiski', 'SMISKI'), ('Dreams', 'DREAMS'),
                            ('Dreams Inc.', 'DREAMS'), ('Hitoy', 'HITOY'), ('Baby Three', 'BABY THREE'),
                            ('Finding Unicorn', 'FINDING UNICORN'), ('Vipo', 'VIPO'), ('Pokémon', 'POKEMON'),
                            ('Pokemon', 'POKEMON'), ('Moetch', 'MOETCH'), ("Let's Van", 'LETSVAN'),
                            ('Accessories', 'ACCESSORIES'), ('Gift Card', 'GIFT CARDS'), ('Gift Cards', 'GIFT CARDS'),
                            ('Other', 'OTHER')):
            self.assertEqual(canonical_brand(name), brand, name)
        for brand in BRANDS:
            self.assertEqual(canonical_brand(brand), brand)

    def test_an_unknown_brand_is_kept_in_capitals_and_blank_stays_blank(self):
        self.assertEqual(canonical_brand('  Kemelife   toys '), 'KEMELIFE TOYS')
        self.assertEqual(canonical_brand('52Toys'), '52TOYS')
        for blank in (None, '', '   '):
            self.assertEqual(canonical_brand(blank), '')

    def test_the_brand_code_is_unchanged(self):
        """PM/DR/TT… numbering reads the brand in lower case: every spelling keeps its code."""
        with closing(self.connect()) as con:
            con.execute("INSERT INTO products (sku, name_cn_en, brand) VALUES ('OT0005', 'Moetch cup', 'Moetch')")
            con.commit()
            names = [name for names in BRAND_CODES.values() for name in names]
            names += ['Pop Mart', 'POP MART', 'Top Toy', 'Toyzero+', 'Dreams', 'Sonny Angel', 'SMISKI', 'Other',
                      'Pokemon', 'Accessories', 'Gift Cards', 'HITOY', 'Baby Three', 'Finding Unicorn', 'VIPO',
                      'Letsvan', 'Moetch']
            for name in names:
                before = _brand_code(con, name, '')
                self.assertIsNotNone(before, name)
                self.assertEqual(_brand_code(con, canonical_brand(name), ''), before, name)
            for brand in BRANDS:
                self.assertIsNotNone(_brand_code(con, brand, ''), brand)


class SavedBrandTests(IsolatedApiCase):
    def brand(self, pid):
        with closing(self.connect()) as con:
            return tuple(con.execute('SELECT brand, search_blob FROM products WHERE id=?', (pid,)).fetchone())

    def post(self, path, body, key=None):
        headers = {**self.headers('manager'), **({'Idempotency-Key': key} if key else {})}
        return self.client.post(path, json=body, headers=headers)

    def test_add_product_saves_the_one_spelling_and_numbers_by_it(self):
        response = self.post('/api/products', {'name_cn_en': 'Twinkle pillow', 'brand': ' pop  mart '})
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()['sku'], 'PM0001')
        brand, blob = self.brand(response.get_json()['id'])
        self.assertEqual(brand, 'POPMART')
        self.assertIn(' popmart ', f' {blob} ')

    def test_edit_saves_the_one_spelling(self):
        response = self.client.patch(f'/api/products/{self.product_id}', json={'brand': 'Top Toy'},
                                     headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        brand, blob = self.brand(self.product_id)
        self.assertEqual(brand, 'TOP TOY')
        self.assertIn('top toy', blob)

    def test_catalog_import_saves_the_one_spelling(self):
        preview = self.post('/api/products/import/preview', {'text': 'SKU,品牌\nTEST-1,toyzero+\nPM0700,Pop Mart\n'})
        self.assertEqual(preview.status_code, 200, preview.get_json())
        rows = preview.get_json()['rows']
        self.assertEqual([row['values']['brand'] for row in rows], ['TOYZERO+', 'POPMART'])
        confirm = self.post('/api/products/import/confirm', {'rows': rows})
        self.assertEqual(confirm.status_code, 200, confirm.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(dict(con.execute("SELECT sku, brand FROM products WHERE sku IN ('TEST-1', 'PM0700')")),
                             {'TEST-1': 'TOYZERO+', 'PM0700': 'POPMART'})

    def test_designs_and_sealed_sets_copy_the_one_spelling(self):
        self.grant_inventory_view('auth0|manager')
        with closing(self.connect()) as con:
            box = con.execute("""INSERT INTO products (sku, jizhanming, name_cn_en, price, brand, product_type)
                                 VALUES ('PM0123', '坐坐', 'Have a Seat Blind Box', 26.99, 'Pop Mart', '盲盒')""").lastrowid
            con.commit()
        designs = self.post(f'/api/products/{box}/designs', {'design_names': ['Sisi']}, 'd-1')
        self.assertEqual(designs.status_code, 201, designs.get_json())
        sealed = self.post(f'/api/products/{box}/sealed-set', {'boxes_per_set': 6}, 's-1')
        self.assertEqual(sealed.status_code, 201, sealed.get_json())
        with closing(self.connect()) as con:
            rows = dict(con.execute("SELECT sku, brand FROM products WHERE sku IN ('PM0123-01', 'PM0123-SET')"))
            blobs = [row[0] for row in con.execute("SELECT search_blob FROM products WHERE sku LIKE 'PM0123-%'")]
        self.assertEqual(rows, {'PM0123-01': 'POPMART', 'PM0123-SET': 'POPMART'})
        self.assertTrue(all('popmart' in blob and 'pop mart' not in blob for blob in blobs), blobs)


class ScriptCase(IsolatedApiCase):
    """A fixture catalog for both one-off scripts."""

    def add(self, sku, name, brand='', ip_series='', **extra):
        with closing(self.connect()) as con:
            columns = {'sku': sku, 'name_cn_en': name, 'jizhanming': name, 'brand': brand, 'ip_series': ip_series,
                       **extra}
            pid = con.execute(f"INSERT INTO products ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                              list(columns.values())).lastrowid
            blob = ' '.join(str(columns.get(field) or '').lower() for field in
                            ('sku', 'jizhanming', 'name_cn_en', 'brand', 'product_type', 'ip_series'))
            con.execute('UPDATE products SET search_blob=? WHERE id=?', (blob, pid))
            con.commit()
        self.ids[sku] = pid
        return pid

    def dex(self, kind, name, parent=None, product=None):
        with closing(self.connect()) as con:
            data = {'kind': kind, 'name': name, **({'parent_id': parent} if parent else {}),
                    **({'product_id': self.ids[product]} if product else {})}
            entry = dex_book.create(con, data, PERSON)['id']
            con.commit()
        return entry

    def pick(self, sku, good=True):
        """A remembered 日报 name pick on the product: good (its identity is unchanged since) or stale."""
        with closing(self.connect()) as con:
            row = dict(con.execute('SELECT * FROM products WHERE id=?', (self.ids[sku],)).fetchone())
            signature = report_identity_signature(row) if good else 'stale'
            con.execute('INSERT INTO report_match_choices (name_key, note_key, product_id, identity_signature) '
                        'VALUES (?, ?, ?, ?)', (sku.lower(), '', self.ids[sku], signature))
            con.commit()

    def state(self):
        with closing(self.connect()) as con:
            products = {row['sku']: dict(row) for row in con.execute('SELECT * FROM products')}
            picks = {key[0]: pick['state'] for key, pick in load_report_picks(con).items()}
        return products, picks

    def run_script(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = self.module.main(['--out', str(self.dir / 'report'), *extra])
        self.assertEqual(code, 0)
        result = json.loads(out.getvalue())
        with open(result['csv'], encoding='utf-8-sig', newline='') as handle:
            return result, list(csv.DictReader(handle))

    def check_dry_run_then_apply(self, field):
        """The dry run changes nothing; --apply makes only the CSV's changes (the field and the search text), keeps a
        good name pick good, and a second run finds nothing left to change."""
        before, picks = self.state()
        result, rows = self.run_script()
        self.assertEqual(self.state(), (before, picks))
        self.assertFalse(result['applied'])
        backup = self.dir / 'before.db'
        applied, applied_rows = self.run_script('--apply', '--backup', str(backup))
        self.assertTrue(applied['applied'] and backup.is_file())
        self.assertEqual(applied_rows, rows)
        after, picks_after = self.state()
        changes = {row['sku']: row['after'] for row in rows if row['action'] == 'change'}
        for sku, product in before.items():
            expected = dict(product)
            if sku in changes:  # the field, and the search text rebuilt as the product save builds it
                expected[field] = changes[sku]
                expected['search_blob'] = ' '.join(str(expected[name] or '').lower() for name in
                                                   ('sku', 'jizhanming', 'name_cn_en', 'brand', 'product_type',
                                                    'ip_series'))
            self.assertEqual(after[sku], expected, sku)
        self.assertEqual(picks_after, picks)
        again, _ = self.run_script()
        self.assertEqual(again['changed'], 0)
        return {row['sku']: row for row in rows}


class NormalizeBrandsTests(ScriptCase):
    def setUp(self):
        super().setUp()
        self.module = script('normalize_brands')
        self.dir = Path(self.tempdir.name)
        self.ids = {}
        self.add('PM0001', 'Labubu box', 'Pop Mart')
        self.add('PM0002', 'Labubu earphone case', 'The Monsters')
        self.add('TT0013', 'MEGA SPACE MOLLY 400% Sanrio', 'Space Molly 400%')
        self.add('PM0003', 'Dimoo blister', '吊卡')
        self.add('PM0004', 'Romantic Ring Box', '')
        self.add('PM0005', 'Volleyball tumbler', 'Other')
        self.add('DR0001', 'Smiski Keychain')
        self.add('DR0002', 'Cat Christmas Blind Box')
        self.add('DR0003', 'Animal Bakery Series 1')
        self.add('DR0004', 'SA dog time')
        self.add('LV0001', 'wkk一代', 'LET… (truncated in print; WAKUKU items)')
        self.add('TT0002', 'Hello Kitty Plush', 'Top Toy')
        self.add('HT0001', 'Nommi box', 'HITOY')
        self.add('DR0005', 'Smiski Series 2', 'Dreams')
        self.add('DR0006', 'Sonny Angel Flower Series', 'Dreams')
        self.add('DR0007', 'Chiikawa Hippers', 'Dreams')
        self.add('SP00001', 'Old ring box', '')
        company = self.dex('company', 'Dreams Inc.')
        sonny = self.dex('ip', 'Sonny Angel', company)
        series = self.dex('series', 'Cat Christmas Series', sonny)
        self.dex('product', 'Blind Box', series, product='DR0002')
        self.pick('PM0001')
        self.pick('PM0003')
        self.pick('PM0002', good=False)

    def test_the_plan(self):
        _, rows = self.run_script()
        rows = {row['sku']: row for row in rows}
        self.assertEqual({sku: (row['action'], row['after']) for sku, row in rows.items()}, {
            'PM0001': ('change', 'POPMART'), 'PM0002': ('change', 'POPMART'),
            'TT0013': ('change', 'POPMART'), 'PM0003': ('change', 'POPMART'), 'PM0004': ('change', 'POPMART'),
            'PM0005': ('change', 'OTHER'), 'DR0001': ('change', 'SMISKI'), 'DR0002': ('change', 'SONNY ANGEL'),
            'DR0003': ('change', 'DREAMS'), 'DR0004': ('change', 'SONNY ANGEL'), 'LV0001': ('change', 'LETSVAN'),
            'TT0002': ('change', 'TOP TOY'), 'SP00001': ('list', ''), 'TEST-1': ('list', ''),
            'DR0005': ('change', 'SMISKI'), 'DR0006': ('change', 'SONNY ANGEL'), 'DR0007': ('change', 'DREAMS'),
        })
        self.assertEqual(rows['DR0002']['why'], 'blank → code DR, Dex IP Sonny Angel')
        self.assertEqual(rows['PM0005']['check'], 'brand is OT, ID is PM')
        self.assertEqual(rows['TT0013']['check'], 'ID code is TT')
        self.assertEqual(rows['DR0005']['why'], 'Dreams → code DR, name says SMISKI')
        self.assertEqual(rows['DR0003']['check'], 'check: DREAMS')
        self.assertEqual(rows['LV0001']['check'], 'cut-off text')

    def test_dry_run_changes_nothing_and_apply_makes_only_the_listed_changes(self):
        self.check_dry_run_then_apply('brand')
        products, picks = self.state()
        self.assertEqual(picks, {'pm0001': 'ok', 'pm0003': 'ok', 'pm0002': 'stale'})
        self.assertNotIn('pop mart', products['PM0001']['search_blob'])

    def test_apply_needs_a_new_backup_file(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.module.main(['--out', str(self.dir), '--apply'])


class FillIpSeriesTests(ScriptCase):
    def setUp(self):
        super().setUp()
        self.module = script('fill_ip_series')
        self.dir = Path(self.tempdir.name)
        self.ids = {}
        self.add('PM0010', 'LABUBU Rhythmic Gymnastics Figure', 'POPMART')
        self.add('PM0011', 'Labubu Have a Seat', 'POPMART', 'Labubu')
        self.add('PM0012', 'Minions Lazy Every Day Series Figures', 'POPMART', '其他系列')
        self.add('PM0013', 'Hirono x Minions Figure', 'POPMART', 'POPMART')
        self.add('PM0014', 'Romantic Ring Box Series 4', 'POPMART')
        self.add('PM0015', 'Labubu and Hirono Pair', 'POPMART')
        self.add('PM0016', 'Hirono Echo Series', 'POPMART')
        self.add('FU0001', 'ZZOTON Delicious Bunny', 'FINDING UNICORN')
        self.add('DR0010', 'Sonny Angel Hair Clip', 'DREAMS')
        self.add('DR0011', 'Smiski Keychain', 'DREAMS', '')
        self.add('AC0001', 'Shoes', 'ACCESSORIES')
        self.add('PM0010-01', 'LABUBU Rhythmic Gymnastics Figure · Ribbon', 'POPMART',
                 stock_form='confirmed_design', stock_unit='piece', design_name='Ribbon')
        with closing(self.connect()) as con:
            series = con.execute("INSERT INTO product_series (name, product_id) VALUES ('Rhythmic', ?)",
                                 (self.ids['PM0010'],)).lastrowid
            con.execute('UPDATE products SET series_id=? WHERE id IN (?, ?)',
                        (series, self.ids['PM0010'], self.ids['PM0010-01']))
            con.commit()
        pop_mart = self.dex('company', 'POP MART')
        self.dex('ip', 'THE MONSTERS', pop_mart)
        hirono = self.dex('ip', 'HIRONO', pop_mart)
        self.dex('ip', 'BUNNY', pop_mart)
        self.dex('partner', 'Minions')
        dreams = self.dex('company', 'Dreams Inc.')
        self.dex('ip', 'Sonny Angel', dreams)
        self.dex('ip', 'SMISKI', dreams)
        echo = self.dex('series', 'Echo Series', hirono)
        self.dex('product', 'Figures', echo, product='PM0016')
        self.pick('PM0012')

    def test_the_plan(self):
        _, rows = self.run_script()
        rows = {row['sku']: row for row in rows}
        self.assertEqual({sku: (row['action'], row['after'], row['dex']) for sku, row in rows.items()}, {
            'PM0010': ('change', 'Labubu', 'THE MONSTERS'), 'PM0010-01': ('change', 'Labubu', 'THE MONSTERS'),
            'PM0012': ('change', 'Minions', 'Minions'), 'PM0013': ('change', 'Hirono', 'HIRONO'),
            'PM0014': ('list', '', ''), 'PM0015': ('list', '', ''), 'FU0001': ('list', '', ''),
            'DR0010': ('change', 'Smiski & SA', 'Sonny Angel'), 'DR0011': ('change', 'Smiski & SA', 'SMISKI'),
            'AC0001': ('list', '', ''), 'TEST-1': ('list', '', ''),
        })
        self.assertEqual(rows['PM0015']['why'], 'name names several ips')
        self.assertEqual(rows['FU0001']['matched'], 'BUNNY')
        self.assertEqual(rows['AC0001']['why'], 'accessory: no IP expected')
        self.assertEqual(rows['PM0010-01']['card'], 'PM0010')

    def test_dry_run_changes_nothing_and_apply_makes_only_the_listed_changes(self):
        self.check_dry_run_then_apply('ip_series')
        self.assertEqual(self.state()[1], {'pm0012': 'ok'})
