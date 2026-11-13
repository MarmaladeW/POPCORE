"""图鉴 Dex step 2: the one-time load of the website's book (owner, 2026-11-12: "The Dex itself lives on POPCORE";
dropped IPs are deleted for good; an outside name is a partner, never an IP under a toy maker), and the list of
POPCORE items not in the book yet. A small made-up export and IP list stand in for the real ones."""
import csv
import importlib.util
import io
import json
import sqlite3
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path

from support import IsolatedApiCase

import dex_load
from blueprints import dex

ROOT = Path(__file__).resolve().parents[2]
COLUMNS = ('Checked company', 'IP (and English name if different)', 'What it is', 'Was listed under', 'Changed',
           'How sure', 'Source link', 'Owner decision', 'Note')
IP_LIST = [
    ('POP MART', 'THE MONSTERS = THE MONSTERS (Labubu)', 'own IP', 'POP MART', '', ''),
    ('POP MART', 'Hirono', 'own IP', 'POP MART', '', ''),
    ('POP MART', 'Yoki', '', 'POP MART', 'delete (new, 12 Nov)', ''),
    ('POP MART', 'POP MART = POP MART (catch-all)', 'not an IP', 'POP MART', '', ''),
    ('(no toy maker: collaboration partner)', 'Disney Princess', 'collaboration partner', 'POP MART',
     'collaboration; Dex IP renamed Disney 12 Nov', ''),
    ('(no toy maker: collaboration partner)', 'Disney', 'collaboration partner', 'POP MART', 'collaboration', ''),
    ('(no toy maker: collaboration partner)', 'Sanrio', 'collaboration partner', 'POP MART', 'collaboration', ''),
    ('Dreams Inc.', 'Sonny Angel', 'own IP', 'Dreams Inc.', '', ''),
    ('Heyone', '33R3NA', '', 'Heyone', 'delete', ''),
    ('TOP TOY', 'Nommi', 'own IP', 'Surefun', '', ''),
]


def entry(kind, website_id, name, parent=None, **more):
    row = {'ref': None, 'website_id': website_id, 'kind': kind, 'parent_ref': None, 'partner_ref': None,
           'character_ref': None, 'name': name, 'sort_order': 0, 'picture_id': None, 'blind_box': None, 'rarity': None,
           'popcore_id': None, 'extras': {}, 'version': None, 'deleted': False, 'slug': name.lower().replace(' ', '-'),
           'picture_url': None, 'thumb_url': None, 'parent_website_id': parent, 'listing_ids': [],
           'parent_kind': {'character': 'ip', 'series': 'ip', 'product': 'series', 'design': 'product'}.get(kind)}
    if kind == 'product':
        row['item'] = False
    row.update(more)
    return row


def export():
    return {'made_at': '2026-11-13T00:00:00Z', 'entries': [
        entry('ip', 1, 'THE MONSTERS', picture_id=10, picture_url='https://popcore.ca/u/tm.webp',
              thumb_url='https://popcore.ca/u/tm-300x300.webp', extras={'aliases': ['Labubu'], 'is_original': False}),
        entry('ip', 2, 'Hirono'),
        entry('ip', 3, 'Yoki'),
        entry('ip', 4, 'POP MART'),
        entry('ip', 5, 'Disney Princess', picture_id=50, picture_url='https://popcore.ca/u/dp.webp'),
        entry('ip', 6, 'Sanrio'),
        entry('ip', 7, 'Sonny Angel'),
        entry('ip', 8, 'Mystery'),
        entry('ip', 9, '33R3NA'),
        entry('character', 11, 'Labubu', 1),
        entry('character', 12, 'Yoki', 3),
        entry('character', 13, 'Disney Princess', 5),
        entry('series', 101, 'Have a Seat', 1, manufacturer='pop_mart', listing_ids=['PM0100', 'PM0100-01'],
              extras={'release_date': '2024-01-01', 'tags': ['plush']}),
        entry('series', 102, 'Yoki Dream', 3, manufacturer='pop_mart'),
        entry('series', 103, 'Dear Birds', 4, manufacturer='pop_mart'),
        entry('series', 104, 'Fun Park', 5, manufacturer='pop_mart', character_website_id=11),
        entry('series', 105, 'Sanrio Dream House', 6, manufacturer='pop_mart'),
        entry('series', 106, 'Angel Garden', 7, manufacturer=''),
        entry('series', 107, 'Mystery Time', 8, manufacturer='pop_mart'),
        entry('series', 108, 'Glow', 9, manufacturer='heyone'),
        entry('product', 201, 'Vinyl Figure Blind Box', 101, blind_box=True, extras={'retail_price': 22.0}),
        entry('product', 202, 'Vinyl Figure Blind Box', 102, blind_box=True),
        entry('product', 203, 'Vinyl Figure Blind Box', 103, blind_box=True),
        entry('product', 204, 'Vinyl Figure Blind Box', 104, blind_box=True),
        entry('product', 205, 'Vinyl Figure Blind Box', 105, blind_box=True),
        entry('product', 208, 'Vinyl Figure Blind Box', 108, blind_box=True),
        # An item figure (no series) with the same website number as a product line (contract 1.2).
        entry('product', 201, 'Labubu Keychain', 1, item=True, parent_kind='ip', blind_box=False,
              character_website_id=11, listing_ids=['PM0600']),
        entry('design', 301, 'Sun', 201, rarity='regular', listing_ids=['PM0100-01'], picture_id=31,
              picture_url='https://popcore.ca/u/sun.webp', extras={'weight': '92-95', 'shake_note': None}),
        entry('design', 302, 'Moon', 201, rarity='secret'),
        entry('design', 303, 'Star', 201, rarity='normal'),
        entry('design', 304, 'Dream', 202, rarity='normal'),
        entry('design', 305, 'Bird', 203, rarity='normal'),
        entry('design', 308, 'Glow One', 208, rarity='normal'),
    ]}


class DexLoadTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(dex.bp)
        self.dir = Path(self.tempdir.name)
        self.ip_list_path = self.dir / 'ip-list.csv'
        with self.ip_list_path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            for company, ip, what, was, decision, note in IP_LIST:
                writer.writerow((company, ip, what, was, '', '', '', decision, note))
        self.export_path = self.dir / 'book-export.json'
        self.write_export(export())
        with closing(self.connect()) as con:
            con.execute("INSERT INTO product_series(name) VALUES ('Have a Seat box')")
            family = con.execute('SELECT id FROM product_series').fetchone()[0]
            for sku, form, design, series in (
                    ('PM0100', 'random_box', None, family), ('PM0100-01', 'confirmed_design', 'Sun', family),
                    ('PM0100-02', 'confirmed_design', 'Moon', family), ('PM0100-03', 'confirmed_design', 'Stars', family),
                    ('PM0100-SET', 'sealed_set', None, family), ('PM0200', 'ordinary', None, None),
                    ('PM0300', None, None, None), ('PM0400', 'confirmed_design', 'Lone', None),
                    ('PM0600', 'ordinary', None, None)):
                con.execute("""INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, design_name, series_id, ip_series)
                               VALUES (?, ?, ?, ?, ?, ?, 'Labubu')""",
                            (sku, f'Have a Seat {sku}', f'坐坐 {sku}', form, design, series))
            con.execute("UPDATE product_series SET product_id=(SELECT id FROM products WHERE sku='PM0100')")
            con.execute("""INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, ip_series)
                           VALUES ('PM0500', 'THE MONSTERS Fun Park Vinyl Figure', '乐园', 'random_box', 'Labubu')""")
            self.items = {row[0]: row[1] for row in con.execute('SELECT sku, id FROM products')}
            con.commit()

    def write_export(self, document):
        self.export_path.write_text(json.dumps(document), encoding='utf-8')

    def plan(self, document=None):
        with closing(self.connect()) as con:
            return dex_load.plan(con, document or export(), dex_load.read_ip_list(self.ip_list_path))

    def load(self, document=None):
        with closing(self.connect()) as con:
            plan = dex_load.plan(con, document or export(), dex_load.read_ip_list(self.ip_list_path))
            dex_load.apply(con, plan)
            return plan

    def rows(self):
        with closing(self.connect()) as con:
            return {('item' if row['website_item'] else row['kind'],
                     row['website_id'] if row['website_id'] is not None else row['name']): dict(row)
                    for row in con.execute('SELECT * FROM dex_entries')}

    def one(self, kind, website_id):
        return self.rows()[(kind, website_id)]

    def name_of(self, entry_id):
        with closing(self.connect()) as con:
            return con.execute('SELECT name FROM dex_entries WHERE id=?', (entry_id,)).fetchone()[0]

    def test_companies_and_partners_come_from_the_ip_list(self):
        self.load()
        rows = self.rows()
        live = {key[1] for key, row in rows.items() if row['kind'] == 'company' and row['deleted_at'] is None}
        self.assertEqual(live, {'POP MART', 'Dreams Inc.', 'TOP TOY'})
        # Heyone keeps no IP: a deleted row holds its dropped IP.
        self.assertIsNotNone(rows[('company', 'Heyone')]['deleted_at'])
        partners = {row['name']: row for row in rows.values() if row['kind'] == 'partner'}
        self.assertEqual(set(partners), {'Disney', 'Sanrio'})  # Disney Princess was renamed Disney: one partner
        self.assertEqual(partners['Disney']['website_id'], 5)  # it keeps the website IP row's number and picture
        self.assertEqual(partners['Disney']['picture_url'], 'https://popcore.ca/u/dp.webp')
        ip = self.one('ip', 1)
        self.assertEqual((self.name_of(ip['parent_id']), ip['updated_by'], ip['thumb_url']),
                         ('POP MART', 'load', 'https://popcore.ca/u/tm-300x300.webp'))
        self.assertEqual(json.loads(ip['extras']), {'aliases': ['Labubu'], 'is_original': False})
        self.assertNotIn(('partner', 6), {(k[0], k[1]) for k in rows if k[0] == 'ip'})
        self.assertNotIn(('ip', 5), rows)  # a partner IP row is not loaded as an IP

    def test_a_dropped_ip_and_everything_under_it_are_stored_as_deleted_and_never_shown(self):
        plan = self.load()
        for kind, website_id in (('ip', 3), ('character', 12), ('series', 102), ('product', 202), ('design', 304)):
            self.assertIsNotNone(self.one(kind, website_id)['deleted_at'], (kind, website_id))
        self.assertEqual(self.get('/api/dex/entries?q=Yoki'), [])
        pop_mart = self.one('ip', 1)['parent_id']
        self.assertNotIn('Yoki', [row['name'] for row in self.get(f'/api/dex/entries?parent={pop_mart}')])
        dropped = {row['name']: row for row in plan.dropped}
        self.assertEqual(set(dropped), {'Yoki', 'POP MART', '33R3NA'})
        with closing(self.connect()) as con:
            tabs = {title: rows for title, _, rows in dex_load.sheet_tabs(con, plan, dex_load.read_ip_list(self.ip_list_path))}
        yoki = next(row for row in tabs['Deleted on the website'] if row[0] == 'Yoki')
        self.assertEqual(yoki[3:7], (1, 1, 1, 1))  # one character, series, product and design go with it

    def test_a_not_an_ip_rows_series_go_under_the_company(self):
        self.load()
        series = self.one('series', 103)
        self.assertIsNone(series['deleted_at'])
        self.assertEqual(self.name_of(series['parent_id']), 'POP MART')
        self.assertIsNotNone(self.one('ip', 4)['deleted_at'])
        self.assertIsNone(self.one('design', 305)['deleted_at'])

    def test_a_partner_series_is_rehomed_when_its_home_ip_is_plain_and_flagged_when_not(self):
        plan = self.load()
        fun = self.one('series', 104)  # its character Labubu is THE MONSTERS'
        self.assertEqual(self.name_of(fun['parent_id']), 'THE MONSTERS')
        self.assertEqual(self.name_of(fun['partner_id']), 'Disney')
        sanrio = self.one('series', 105)
        self.assertEqual((self.name_of(sanrio['parent_id']), self.name_of(sanrio['partner_id'])), ('POP MART', 'Sanrio'))
        flagged = {flag['name']: flag for flag in plan.flags}
        self.assertIn('no clear home IP', flagged['Sanrio Dream House']['why'])
        self.assertNotIn('Fun Park', flagged)
        princess = self.one('character', 13)  # a partner's character sits under the partner and keeps its number
        self.assertEqual((self.name_of(princess['parent_id']), princess['deleted_at']), ('Disney', None))
        self.assertNotIn('Disney Princess', flagged)
        self.assertEqual(self.one('series', 106)['parent_id'], self.one('ip', 7)['id'])  # blank maker, own IP
        self.assertIn('Not in the IP list', flagged['Mystery']['why'])
        unmatched = {row['name'] for row in plan.unmatched}
        self.assertEqual(unmatched, {'Nommi', 'Disney', 'Mystery'})

    def test_a_product_line_and_an_item_with_the_same_number_both_load(self):
        self.load()
        line, item = self.one('product', 201), self.one('item', 201)
        self.assertNotEqual(line['id'], item['id'])
        self.assertEqual((line['website_item'], item['website_item']), (0, 1))
        self.assertEqual((self.name_of(line['parent_id']), self.name_of(item['parent_id'])), ('Have a Seat', 'THE MONSTERS'))
        self.assertEqual(self.name_of(item['character_id']), 'Labubu')
        self.assertEqual(item['product_id'], self.items['PM0600'])
        # Step 1's code never sets website_item and still writes on the migrated table.
        with closing(self.connect()) as con:
            con.execute("INSERT INTO dex_entries(kind, name, website_id) VALUES ('design', 'Old code', 999)")
            con.commit()
            self.assertEqual(con.execute("SELECT website_item FROM dex_entries WHERE name='Old code'").fetchone()[0], 0)
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("INSERT INTO dex_entries(kind, name, website_id, website_item) VALUES ('product', 'Twin', 201, 1)")

    def test_kept_own_ips_the_website_lacks_are_made_new_to_the_dex(self):
        plan = self.load()
        nommi = self.rows()[('ip', 'Nommi')]
        self.assertEqual((self.name_of(nommi['parent_id']), nommi['website_id'], nommi['updated_by']), ('TOP TOY', None, 'load'))
        self.assertEqual(dex_load.summary(plan)['new_to_dex'], 1)
        with closing(self.connect()) as con:
            loaded = next(rows for title, _, rows in dex_load.sheet_tabs(con, plan, dex_load.read_ip_list(self.ip_list_path))
                          if title == 'Loaded')
        self.assertIn('New to the Dex', next(row for row in loaded if row[2] == 'Nommi')[4])
        # No company is left empty, and a second run neither adds it again nor adopts it.
        again = self.plan()
        self.assertEqual(again.rows[('ip', 'new:nommi')]['action'], 'same')
        self.load()
        self.assertEqual(sum(1 for row in self.rows().values() if row['name'] == 'Nommi'), 1)

    def test_pairing_by_id_then_by_name(self):
        plan = self.load()
        sun, moon, star = self.one('design', 301), self.one('design', 302), self.one('design', 303)
        box = self.one('product', 201)
        self.assertEqual(sun['product_id'], self.items['PM0100-01'])
        self.assertEqual(box['product_id'], self.items['PM0100'])
        self.assertEqual(moon['product_id'], self.items['PM0100-02'])
        self.assertIsNone(star['product_id'])  # "Star" and "Stars" are close, not equal
        self.assertEqual((sun['rarity'], moon['rarity'], box['blind_box']), ('normal', 'secret', 1))
        self.assertEqual(json.loads(sun['extras']), {'weight': '92-95'})
        how = {pair['sku']: pair['how'] for pair in plan.paired}
        self.assertEqual(how, {'PM0100-01': 'By ID (website listing)', 'PM0100': "By ID (its designs' listings)",
                               'PM0100-02': 'By name in its box', 'PM0600': 'By ID (website listing)'})
        star_flag = next(flag for flag in plan.flags if flag['name'] == 'Star')
        self.assertEqual(star_flag['choices'], 'PM0100-03')
        not_paired = {item['sku'] for item in plan.not_paired}
        self.assertEqual(not_paired, {'PM0100-03', 'PM0200', 'PM0300', 'PM0400', 'PM0500', 'TEST-1'})
        # Close names are only suggested, never linked: Fun Park's box, and "Stars" for the design "Star".
        self.assertIsNone(self.one('product', 204)['product_id'])
        suggested = {sku: plan.suggestions[self.items[sku]] for sku in ('PM0500', 'PM0100-03', 'PM0200', 'PM0400')}
        self.assertEqual((suggested['PM0500']['path'], suggested['PM0500']['score']),
                         ('POP MART › THE MONSTERS › Fun Park › Vinyl Figure Blind Box', 100))
        self.assertEqual(suggested['PM0100-03']['path'], 'POP MART › THE MONSTERS › Have a Seat › Vinyl Figure Blind Box › Star')
        self.assertGreaterEqual(suggested['PM0100-03']['score'], 85)
        self.assertIsNone(suggested['PM0200'])
        self.assertIsNone(suggested['PM0400'])
        with closing(self.connect()) as con:
            rows = next(rows for title, _, rows in dex_load.sheet_tabs(con, plan, dex_load.read_ip_list(self.ip_list_path))
                        if title == 'Not in the book')
        self.assertEqual([row[0] for row in rows], ['PM0200', 'PM0300', 'PM0500', 'TEST-1', 'PM0100-03', 'PM0400'])
        self.assertEqual(rows[2][8:], ('POP MART › THE MONSTERS › Fun Park › Vinyl Figure Blind Box', 100))

    def test_a_second_run_changes_nothing(self):
        self.load()
        before = self.rows()
        plan = self.plan()
        self.assertEqual({row['action'] for row in plan.rows.values()}, {'same'})
        self.load()
        self.assertEqual(self.rows(), before)

    def test_a_row_a_person_changed_is_left_alone_and_reported(self):
        self.load()
        series = self.one('series', 101)
        response = self.client.patch(f'/api/dex/entries/{series["id"]}', json={'name': 'Have a Seat!'},
                                     headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        changed = export()
        next(e for e in changed['entries'] if e['website_id'] == 101 and e['kind'] == 'series')['name'] = 'Have A Seat'
        next(e for e in changed['entries'] if e['website_id'] == 2 and e['kind'] == 'ip')['name'] = 'HIRONO'
        plan = self.load(changed)
        self.assertEqual(self.one('series', 101)['name'], 'Have a Seat!')
        self.assertEqual(self.one('series', 101)['updated_by'], 'auth0|manager')
        hirono = self.one('ip', 2)
        self.assertEqual((hirono['name'], hirono['version']), ('HIRONO', 2))  # the load's own row follows the website
        self.assertEqual(dex_load.summary(plan)['left_alone_people'], 1)

    def test_a_hand_made_entry_is_adopted(self):
        company = self.client.post('/api/dex/entries', json={'kind': 'company', 'name': 'pop mart'},
                                   headers=self.headers('manager')).get_json()
        ip = self.client.post('/api/dex/entries', json={'kind': 'ip', 'name': 'The Monsters', 'parent_id': company['id'],
                                                       'extras': {'description': 'Kasing Lung'}},
                              headers=self.headers('manager')).get_json()
        plan = self.load()
        self.assertEqual(plan.rows[('ip', 1)]['action'], 'adopt')
        adopted = self.one('ip', 1)
        self.assertEqual((adopted['id'], adopted['parent_id'], adopted['name']), (ip['id'], company['id'], 'The Monsters'))
        self.assertEqual(json.loads(adopted['extras']),
                         {'aliases': ['Labubu'], 'is_original': False, 'description': 'Kasing Lung'})
        self.assertEqual(sum(1 for row in self.rows().values() if row['kind'] == 'company' and row['name'].casefold() == 'pop mart'), 1)
        # Adopted, it stays the person's row: later runs leave it alone.
        self.assertEqual(adopted['updated_by'], 'auth0|manager')
        again = self.load()
        self.assertEqual(again.rows[('ip', 1)]['action'], 'person')
        self.assertEqual(self.one('ip', 1)['name'], 'The Monsters')

    def test_a_link_a_person_made_is_never_replaced(self):
        self.load()
        sun, star = self.one('design', 301), self.one('design', 303)
        for entry_id, product_id in ((sun['id'], None), (star['id'], self.items['PM0100-01'])):
            response = self.client.patch(f'/api/dex/entries/{entry_id}', json={'product_id': product_id},
                                         headers=self.headers('manager'))
            self.assertEqual(response.status_code, 200, response.get_json())
        self.load()
        self.assertIsNone(self.one('design', 301)['product_id'])
        self.assertEqual(self.one('design', 303)['product_id'], self.items['PM0100-01'])

    def script(self):
        spec = importlib.util.spec_from_file_location('load_dex_book', ROOT / 'scripts' / 'load_dex_book.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_script(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            code = self.script().main(['--export', str(self.export_path), '--ip-list', str(self.ip_list_path),
                                       '--out', str(self.dir / 'review'), *extra])
        return code, out.getvalue()

    def test_a_dry_run_writes_nothing(self):
        code, out = self.run_script()
        self.assertEqual(code, 0)
        self.assertEqual(self.rows(), {})
        result = json.loads(out)
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['plan']['entries_by_kind']['design'], 4)
        sheet = Path(result['review_sheet'])
        self.assertTrue(sheet.is_file())
        from openpyxl import load_workbook
        self.assertEqual(load_workbook(sheet).sheetnames, ['Summary', 'Loaded', 'Paired', 'Your pick', 'Not in the book',
                                                           'Deleted on the website', 'Names not matched'])

    def test_apply_refuses_without_a_fresh_backup_path(self):
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()), self._quiet():
                self.run_script('--apply')
        taken = self.dir / 'taken.db'
        taken.write_bytes(b'')
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()), self._quiet():
                self.run_script('--apply', '--backup', str(taken))
        self.assertEqual(self.rows(), {})
        backup = self.dir / 'before.db'
        code, out = self.run_script('--apply', '--backup', str(backup))
        self.assertEqual(code, 0, out)
        with closing(sqlite3.connect(backup)) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM dex_entries').fetchone()[0], 0)  # taken before the load
        self.assertTrue(self.rows())
        self.assertEqual(json.loads(out)['written']['add'], len(self.rows()))

    @staticmethod
    def _quiet():
        import contextlib
        return contextlib.redirect_stderr(io.StringIO())

    def get(self, url, role='staff'):
        response = self.client.get(url, headers=self.headers(role))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()


    # GET /api/dex/unlinked: POPCORE items with no live book entry; never sealed sets.

    def test_unlinked_lists_items_with_no_live_book_entry_and_never_sealed_sets(self):
        page = self.get('/api/dex/unlinked', role='viewer')
        skus = [item['sku'] for item in page['items']]
        self.assertEqual(skus, ['PM0100', 'PM0100-01', 'PM0100-02', 'PM0100-03', 'PM0200', 'PM0300', 'PM0400', 'PM0500',
                                'PM0600', 'TEST-1'])
        self.assertEqual(page['total'], 10)
        self.assertEqual({item['suggestion'] for item in page['items']}, {None})  # an empty book suggests nothing
        self.load()
        page = self.get('/api/dex/unlinked')
        self.assertEqual([item['sku'] for item in page['items']],
                         ['PM0100-03', 'PM0200', 'PM0300', 'PM0400', 'PM0500', 'TEST-1'])
        by_sku = {item['sku']: item for item in page['items']}
        self.assertEqual(by_sku['PM0500']['suggestion'],
                         {'id': self.one('product', 204)['id'],
                          'path': 'POP MART › THE MONSTERS › Fun Park › Vinyl Figure Blind Box', 'score': 100})
        self.assertEqual(by_sku['PM0100-03']['suggestion']['id'], self.one('design', 303)['id'])
        self.assertIsNone(by_sku['PM0400']['suggestion'])
        design = page['items'][0]
        self.assertEqual(design['box']['sku'], 'PM0100')
        self.assertEqual(design['box_entry'], {'id': self.one('product', 201)['id'], 'name': 'Vinyl Figure Blind Box'})
        self.assertIsNone(page['items'][3]['box'])  # a design with no box family

    def test_unlinked_search_paging_and_total(self):
        page = self.get('/api/dex/unlinked?q=PM01&per_page=2&page=2')
        self.assertEqual((page['total'], page['page'], page['per_page']), (4, 2, 2))
        self.assertEqual([item['sku'] for item in page['items']], ['PM0100-02', 'PM0100-03'])
        self.assertEqual(self.get('/api/dex/unlinked?q=%E5%9D%90%E5%9D%90%20PM0200')['total'], 1)  # by 记账名
        self.assertEqual(self.get('/api/dex/unlinked?per_page=1000')['per_page'], 100)
        bad = self.client.get('/api/dex/unlinked?page=x', headers=self.headers('staff'))
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.get('/api/dex/unlinked').status_code, 401)

    def test_unlinked_a_deleted_entry_does_not_count_as_in_the_book(self):
        with closing(self.connect()) as con:
            con.execute("""INSERT INTO dex_entries(kind, name, product_id, website_id, deleted_at)
                           VALUES ('product', 'Gone', ?, 9, datetime('now'))""", (self.items['PM0200'],))
            con.commit()
        self.assertIn('PM0200', [item['sku'] for item in self.get('/api/dex/unlinked')['items']])


class SuggestionNumbersTest(unittest.TestCase):
    """Close words are not enough: a year, a size or a series number must agree (400% is not 1000%)."""

    def nodes(self):
        rows = [
            (1, 'company', 'POP MART', None), (2, 'ip', 'MOLLY', 1), (3, 'series', 'Space Molly', 2),
            (4, 'product', 'Mega Space Molly 1000%', 3), (5, 'product', 'Mega Space Molly 400% Series 2', 3),
        ]
        return {i: {'id': i, 'kind': kind, 'name': name, 'parent': parent, 'product_id': None}
                for i, kind, name, parent in rows}

    def test_numbers_must_agree(self):
        found = dex_load.candidates(self.nodes())
        item = {'stock_form': 'random_box', 'ip_series': 'Molly', 'name_cn_en': 'Mega Space Molly 400%', 'jizhanming': ''}
        self.assertIsNone(dex_load.suggest(item, found))  # 1000% and "400% Series 2" both differ in their numbers
        item['name_cn_en'] = 'Mega Space Molly 1000%'
        self.assertEqual(dex_load.suggest(item, found)['id'], 4)

    def test_a_number_alone_or_another_ip_is_no_match(self):
        nodes = self.nodes()
        nodes[6] = {'id': 6, 'kind': 'ip', 'name': 'Animal Bakery', 'parent': 1, 'product_id': None}
        nodes[7] = {'id': 7, 'kind': 'product', 'name': 'Series 2', 'parent': 3, 'product_id': None}
        found = dex_load.candidates(nodes)
        # Its IP (Animal Bakery) has no products: nothing from another IP is offered, however alike "Series 2" looks.
        item = {'stock_form': 'random_box', 'ip_series': '', 'name_cn_en': 'Animal Bakery Mini Figure Series 2', 'jizhanming': ''}
        self.assertIsNone(dex_load.suggest(item, found))
        # No IP at all, and only a number left once the filler words are gone: no match either.
        item = {'stock_form': 'ordinary', 'ip_series': '', 'name_cn_en': 'Series 2', 'jizhanming': ''}
        self.assertIsNone(dex_load.suggest(item, found))
