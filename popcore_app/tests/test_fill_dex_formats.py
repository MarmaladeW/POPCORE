"""图鉴 Dex: scripts/fill_dex_formats.py gives every book product its format by the contract's rule (contract 1.9 section
4: the owner's pick, a person's own choice, the POPCORE type word, the product's name; nothing is sent with a guess)."""
import importlib.util
import io
import json
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path

from support import APP_DIR, IsolatedApiCase

import dex_book

ROOT = APP_DIR.parent
PERSON = {'sub': 'auth0|manager'}


def script():
    spec = importlib.util.spec_from_file_location('fill_dex_formats', ROOT / 'scripts' / 'fill_dex_formats.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FillDexFormatsTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.fill = script()
        self.dir = Path(self.tempdir.name)
        with closing(self.connect()) as con:
            for sku, name, kind in (('PM0001', 'HIRONO Echo Series Blister', '吊卡'), ('PM0002', 'POP BEAN Island Series', '盲盒'),
                                    ('PM0003', 'Labubu Lucky Series Figures', '盲盒'), ('PM0004', 'SKULLPANDA Cards Series', '盲盒（杂项）')):
                con.execute('INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, product_type) VALUES (?, ?, ?, ?, ?)',
                            (sku, name, name, 'random_box', kind))
            items = {row[0]: row[1] for row in con.execute('SELECT sku, id FROM products')}
            company = dex_book.create(con, {'kind': 'company', 'name': 'POP MART'}, PERSON)['id']
            ip = dex_book.create(con, {'kind': 'ip', 'name': 'HIRONO', 'parent_id': company}, PERSON)['id']
            series = dex_book.create(con, {'kind': 'series', 'name': 'Echo Series', 'parent_id': ip}, PERSON)['id']

            def product(name, item=None, fmt=None, by='load'):
                entry = dex_book.create(con, {'kind': 'product', 'name': name, 'parent_id': series,
                                              'product_id': items.get(item), 'extras': {'format': fmt} if fmt else {}},
                                        PERSON)['id']
                con.execute('UPDATE dex_entries SET updated_by=? WHERE id=?', (by, entry))
                con.commit()
                return entry
            self.ids = {
                'blister': product('Figures', 'PM0001', 'Figures'),  # type word 吊卡 beats the name and the website
                'pendant': product('Vinyl Plush Pendant', fmt='Figures'),  # the website's silent default, replaced
                'bean': product('Bean Figures', 'PM0002'),  # the name says figure, the item says POP BEAN: owner
                'plain': product('Lucky Figures', 'PM0003'),  # both say figures
                'mixed': product('Art Cards', 'PM0004'),  # 盲盒（杂项）: by the name, goods
                'ornament': product('2023 Ornament', fmt='Figures'),  # nothing settles it: owner, website value cleared
                'person': product('Snow Globe', fmt='Merchandise', by='auth0|manager'),  # a person's choice stays
                'both': product('Figures Surprise Bag'),  # goods and figure words: owner
            }

    def formats(self):
        with closing(self.connect()) as con:
            return {key: json.loads(con.execute('SELECT extras FROM dex_entries WHERE id=?', (ref,)).fetchone()[0]).get('format')
                    for key, ref in self.ids.items()}

    def run_fill(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = self.fill.main(['--out', str(self.dir / 'report'), *extra])
        return code, json.loads(out.getvalue()) if code == 0 else out.getvalue()

    def test_the_name_rule(self):
        name = lambda text: self.fill.name_format(text)[0]  # noqa: E731
        self.assertEqual(name('POP BEAN Bubble Tea Set'), 'POP BEAN')
        self.assertEqual(name('MEGA SPACE MOLLY 400% Garfield'), 'MEGA')
        self.assertEqual(name('Vinyl Plush Hanging Card'), 'Blister Card')
        self.assertEqual(name('Scented Candle'), 'Fragrance Blind Box')
        self.assertEqual(name('Sachet Pendent Blind Box'), 'Plush Pendants')
        self.assertEqual(name('Earphone Case (Hit Snooze)'), 'Merchandise')
        self.assertEqual(name('Limited Mini Figure — 2017 Edition'), 'Figures')
        self.assertEqual(name('Plush Doll'), 'Figures')
        self.assertEqual(name('HIPPERS'), 'Figures')
        self.assertEqual(name('Block Figures'), None)
        self.assertEqual(name('Vehicles'), None)
        self.assertEqual(name('Spinning Top'), None)  # "pin" only as a whole word

    def test_a_dry_run_reports_and_writes_nothing(self):
        before = self.formats()
        code, result = self.run_fill()
        self.assertEqual(code, 0, result)
        self.assertFalse(result['applied'])
        self.assertEqual(self.formats(), before)
        report = {row['id']: row for row in json.loads(Path(result['report']).read_text(encoding='utf-8'))['products']}
        self.assertEqual(report[self.ids['blister']]['why'], 'type word 吊卡')
        self.assertEqual(report[self.ids['bean']]['suggestion'], 'POP BEAN')
        self.assertEqual(report[self.ids['ornament']]['suggestion'], 'Figures')  # the website's value, as a hint
        self.assertEqual(report[self.ids['ornament']]['place'], ['POP MART', 'HIRONO', 'Echo Series'])
        owner = Path(result['owner_list']).read_text(encoding='utf-8')
        self.assertEqual(owner.count('\n'), 2 + 3)  # header, rule, and the three products left open
        self.assertIn('Figures Surprise Bag', owner)

    def test_apply_sets_the_rule_leaves_the_rest_to_the_owner_and_runs_twice(self):
        with self.assertRaises(SystemExit):
            self.run_fill('--apply')
        backup = self.dir / 'before.db'
        code, result = self.run_fill('--apply', '--backup', str(backup))
        self.assertEqual(code, 0, result)
        self.assertTrue(backup.is_file())
        self.assertEqual(self.formats(), {
            'blister': 'Blister Card', 'pendant': 'Plush Pendants', 'bean': None, 'plain': 'Figures',
            'mixed': 'Merchandise', 'ornament': None, 'person': 'Merchandise', 'both': None})
        self.assertEqual(result['for_owner'], 3)
        with closing(self.connect()) as con:
            row = con.execute('SELECT updated_by, version FROM dex_entries WHERE id=?', (self.ids['pendant'],)).fetchone()
        self.assertEqual(row[0], 'format-rule')
        again_backup = self.dir / 'again.db'
        code, again = self.run_fill('--apply', '--backup', str(again_backup))
        self.assertEqual((code, again['changed']), (0, 0))
        # The owner's picks, then.
        picks = self.dir / 'picks.json'
        picks.write_text(json.dumps({str(self.ids['bean']): 'POP BEAN', str(self.ids['ornament']): 'Figures'}), encoding='utf-8')
        code, done = self.run_fill('--picks', str(picks), '--apply', '--backup', str(self.dir / 'picks.db'))
        self.assertEqual((code, done['changed'], done['for_owner']), (0, 2, 1))
        self.assertEqual((self.formats()['bean'], self.formats()['ornament']), ('POP BEAN', 'Figures'))
        bad = self.dir / 'bad.json'
        bad.write_text(json.dumps({str(self.ids['both']): 'Standalone'}), encoding='utf-8')
        with self.assertRaises(SystemExit):
            self.run_fill('--picks', str(bad))
