"""图鉴 Dex: applying the owner's review decisions to the book (owner, 13 Nov 2026: the review is done with the
owner, who decides; what is approved is applied). A small book stands in for the loaded one: each operation, the
three ways of referring to an entry, re-run safety, all or nothing, and the backup rule."""
import importlib.util
import io
import json
import sqlite3
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path

from support import IsolatedApiCase

import dex_decisions

ROOT = Path(__file__).resolve().parents[2]


def why(card='T-1', reason='a test decision'):
    return {'card': card, 'reason': reason}


def web(kind, number, item=False):
    return {'kind': kind, 'website_id': number, 'item': item}


POP_MART = {'kind': 'company', 'name': 'pop mart', 'parent': None}  # names match in any case


class DexDecisionsTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.dir = Path(self.tempdir.name)
        with closing(self.connect()) as con:
            for sku, form in (('PM0100', 'random_box'), ('PM0100-01', 'confirmed_design'), ('PM0100-02', 'confirmed_design'),
                              ('PM0200', 'random_box'), ('PM0200-01', 'confirmed_design'), ('PM0300', 'ordinary')):
                con.execute("INSERT INTO products(sku, name_cn_en, jizhanming, stock_form) VALUES (?, ?, ?, ?)",
                            (sku, f'Item {sku}', f'Item {sku}', form))
            self.items = {row[0]: row[1] for row in con.execute('SELECT sku, id FROM products')}

            def add(kind, name, parent=None, website_id=None, item=0, **more):
                cur = con.execute(
                    """INSERT INTO dex_entries(kind, parent_id, name, website_id, website_item, blind_box, rarity, product_id,
                                               updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'load')""",
                    (kind, parent, name, website_id, item, more.get('blind_box'), more.get('rarity'),
                     self.items.get(more.get('sku'))))
                return cur.lastrowid
            self.ids = ids = {}
            ids['pop'] = add('company', 'POP MART')
            ids['disney'] = add('partner', 'Disney', website_id=90)
            ids['hirono'] = add('ip', 'Hirono', ids['pop'], 1)
            ids['monsters'] = add('ip', 'THE MONSTERS', ids['pop'], 2)
            ids['echo'] = add('series', 'Echo Series', ids['hirono'], 10)
            ids['line'] = add('product', 'Vinyl Figure Blind Box', ids['echo'], 20, blind_box=1, sku='PM0100')
            ids['sun'] = add('design', 'Sun', ids['line'], 30, rarity='normal', sku='PM0100-01')
            ids['moon'] = add('design', 'MOON', ids['line'], 31, rarity='normal')
            ids['mixed'] = add('series', 'Dear Birds Series', ids['pop'], 11)
            ids['mixed_line'] = add('product', 'Vinyl Figure Blind Box', ids['mixed'], 21, blind_box=1)
            ids['bird'] = add('design', 'Hirono-Bird', ids['mixed_line'], 32, rarity='normal')
            ids['old'] = add('series', 'Fearless Journey Series', ids['pop'], 12)
            ids['old_line'] = add('product', 'Vinyl Figure Blind Box', ids['old'], 22, blind_box=1)
            add('design', '孙悟空', ids['old_line'], 33, rarity='normal')
            ids['item'] = add('product', 'Hirono Coffee Figure', ids['hirono'], 20, item=1, blind_box=0)
            con.commit()

    def run_ops(self, operations, apply=True):
        with closing(self.connect()) as con:
            return dex_decisions.run(con, operations, apply=apply)

    def row(self, entry_id):
        with closing(self.connect()) as con:
            row = con.execute('SELECT * FROM dex_entries WHERE id=?', (entry_id,)).fetchone()
            return dict(row) if row else None

    def snapshot(self):
        with closing(self.connect()) as con:
            return [tuple(row) for row in con.execute('SELECT * FROM dex_entries ORDER BY id')]

    def statuses(self, result):
        return [line['status'] for line in result['report']]

    # Each operation ---------------------------------------------------------------------------------------

    def test_create_with_a_key_that_later_operations_use(self):
        result = self.run_ops([
            {'op': 'create', 'key': 'new-series', 'kind': 'series', 'parent': web('ip', 2), 'name': 'Big Into  Energy Series',
             'partner': web('partner', 90), 'why': why('A-001', 'a new series the book lacks')},
            {'op': 'create', 'key': 'new-line', 'kind': 'product', 'parent': {'key': 'new-series'}, 'name': 'Figures',
             'blind_box': True, 'extras': {'retail_price': 22.0}, 'why': why('A-001', 'its product')},
            {'op': 'link', 'popcore_id': 'pm0200', 'entry': {'key': 'new-line'}, 'why': why('A-001', 'the box is this line')},
            {'op': 'create', 'kind': 'design', 'parent': {'key': 'new-line'}, 'name': 'Spark', 'rarity': 'secret',
             'why': why('B-001', 'a design of the new box')},
        ])
        self.assertTrue(result['applied'], result['refused'])
        self.assertEqual(self.statuses(result), ['done'] * 4)
        series = self.row(result['report'][0]['entry_id'])
        line = self.row(result['report'][1]['entry_id'])
        design = self.row(result['report'][3]['entry_id'])
        self.assertEqual((series['name'], series['parent_id'], series['partner_id'], series['updated_by']),
                         ('Big Into Energy Series', self.ids['monsters'], self.ids['disney'], 'owner-review'))
        self.assertEqual((line['parent_id'], line['blind_box'], line['product_id'], json.loads(line['extras'])),
                         (series['id'], 1, self.items['PM0200'], {'retail_price': 22.0}))
        self.assertEqual((design['parent_id'], design['rarity'], design['website_id']), (line['id'], 'secret', None))

    def test_a_create_keeps_the_websites_number(self):
        operations = [
            {'op': 'create', 'key': 'pendant', 'kind': 'product', 'parent': web('series', 10), 'name': 'Plush Pendant',
             'website_id': 287, 'item': False, 'blind_box': True, 'why': why('D-040', 'the line the load left out')},
            {'op': 'create', 'kind': 'design', 'parent': {'key': 'pendant'}, 'name': 'Cat Lover', 'website_id': 2823,
             'why': why('D-041', 'its design')},
            {'op': 'create', 'kind': 'product', 'parent': POP_MART, 'name': 'POP BEAN Bubble Tea', 'website_id': 25,
             'item': True, 'why': why('D-029', 'an item figure the load left out')},
        ]
        result = self.run_ops(operations)
        self.assertTrue(result['applied'], result['refused'])
        line, design, item = (self.row(result['report'][n]['entry_id']) for n in range(3))
        self.assertEqual((line['website_id'], line['website_item'], design['website_id'], item['website_id'], item['website_item']),
                         (287, 0, 2823, 25, 1))
        self.assertEqual(self.statuses(self.run_ops(operations)), ['already done'] * 3)
        # A number another entry of the kind holds is refused, and nothing is written.
        before = self.snapshot()
        taken = self.run_ops([{'op': 'create', 'kind': 'design', 'parent': web('product', 20), 'name': 'Star',
                               'website_id': 30, 'why': why()},
                              {'op': 'create', 'kind': 'design', 'parent': web('product', 20), 'name': 'Comet',
                               'website_id': 2823, 'why': why()}])
        self.assertEqual([line.get('code') for line in taken['refused']], ['website_number_taken', 'website_number_taken'])
        self.assertEqual(self.snapshot(), before)

    def test_set_changes_the_fields_given_and_raises_the_version(self):
        result = self.run_ops([
            {'op': 'set', 'entry': web('design', 31), 'fields': {'name': 'Moon', 'rarity': 'secret'}, 'why': why()},
            {'op': 'set', 'entry': web('series', 10), 'fields': {'partner': web('partner', 90)}, 'why': why()},
            {'op': 'set', 'entry': web('design', 32), 'fields': {'ip': web('ip', 1)}, 'why': why('F-001', 'mixed set')},
            {'op': 'set', 'entry': web('product', 20, item=True), 'fields': {'name': 'HIRONO Coffee Figure'}, 'why': why()},
        ])
        self.assertTrue(result['applied'], result['refused'])
        moon = self.row(self.ids['moon'])
        self.assertEqual((moon['name'], moon['rarity'], moon['version'], moon['updated_by']), ('Moon', 'secret', 2, 'owner-review'))
        self.assertEqual(self.row(self.ids['echo'])['partner_id'], self.ids['disney'])
        self.assertEqual(self.row(self.ids['bird'])['ip_id'], self.ids['hirono'])
        self.assertEqual(self.row(self.ids['item'])['name'], 'HIRONO Coffee Figure')  # the item, not the line #20
        self.assertEqual(self.row(self.ids['line'])['name'], 'Vinyl Figure Blind Box')

    def test_move_puts_the_entry_under_its_new_parent(self):
        result = self.run_ops([{'op': 'move', 'entry': web('series', 11), 'parent': web('ip', 1), 'why': why()}])
        self.assertEqual(result['refused'], [])
        self.assertEqual(self.row(self.ids['mixed'])['parent_id'], self.ids['hirono'])

    def test_delete_takes_everything_under_it_and_keeps_website_rows_as_deleted(self):
        result = self.run_ops([{'op': 'delete', 'entry': web('series', 12), 'why': why('D-028', 'drop it')}])
        self.assertTrue(result['applied'], result['refused'])
        self.assertIn('1 design', result['report'][0]['result'])
        self.assertIsNotNone(self.row(self.ids['old'])['deleted_at'])
        self.assertIsNotNone(self.row(self.ids['old_line'])['deleted_at'])
        again = self.run_ops([{'op': 'delete', 'entry': web('series', 12), 'why': why('D-028', 'drop it')}])
        self.assertEqual(self.statuses(again), ['already done'])

    def test_link_and_unlink(self):
        result = self.run_ops([
            {'op': 'unlink', 'popcore_id': 'PM0100-01', 'entry': web('design', 30), 'why': why('E-001', 'wrong box')},
            {'op': 'link', 'popcore_id': 'PM0100-01', 'entry': web('design', 31), 'why': why('E-001', 'right design')},
            {'op': 'link', 'popcore_id': 'PM0100-02', 'entry': web('design', 30), 'why': why('E-001', 'the other one')},
        ])
        self.assertTrue(result['applied'], result['refused'])
        self.assertEqual(self.row(self.ids['moon'])['product_id'], self.items['PM0100-01'])
        self.assertEqual(self.row(self.ids['sun'])['product_id'], self.items['PM0100-02'])

    def test_link_never_replaces_another_link(self):
        result = self.run_ops([{'op': 'link', 'popcore_id': 'PM0100-02', 'entry': web('design', 30), 'why': why()}], apply=False)
        self.assertEqual(self.statuses(result), ['refused'])
        self.assertIn('unlink it first', result['refused'][0]['result'])

    def test_a_rule_of_the_book_refuses_and_says_why(self):
        result = self.run_ops([
            {'op': 'delete', 'entry': web('series', 10), 'why': why()},  # its line links PM0100
            {'op': 'link', 'popcore_id': 'PM0100-SET', 'entry': web('design', 31), 'why': why()},  # no such item
            {'op': 'set', 'entry': web('design', 30), 'fields': {'ip': web('ip', 2)}, 'why': why()},  # an IP is above
        ], apply=False)
        self.assertEqual([line.get('code') for line in result['refused']], ['dex_linked', 'refused', 'ip_above'])

    # Referring to entries ---------------------------------------------------------------------------------

    def test_three_ways_of_referring_to_an_entry(self):
        result = self.run_ops([
            {'op': 'create', 'key': 'lilios', 'kind': 'ip', 'parent': POP_MART, 'name': 'LiLiOS', 'why': why('GQ-B3', 'bring back')},
            {'op': 'set', 'entry': web('design', 32), 'fields': {'ip': {'key': 'lilios'}}, 'why': why()},
            {'op': 'set', 'entry': {'kind': 'ip', 'name': 'lilios', 'parent': POP_MART},
             'fields': {'extras': {'description': 'back again'}}, 'why': why()},
            {'op': 'set', 'entry': {'kind': 'partner', 'name': 'DISNEY'}, 'fields': {'name': 'Disney'}, 'why': why()},
            {'op': 'set', 'entry': {'kind': 'ip', 'name': 'Nobody', 'parent': POP_MART}, 'fields': {'name': 'X'}, 'why': why()},
            {'op': 'set', 'entry': web('design', 999), 'fields': {'name': 'X'}, 'why': why()},
            {'op': 'set', 'entry': {'key': 'later'}, 'fields': {'name': 'X'}, 'why': why()},
        ], apply=False)
        self.assertEqual(self.statuses(result), ['would do', 'would do', 'would do', 'already done', 'refused', 'refused', 'refused'])
        self.assertEqual(self.row(self.ids['bird'])['ip_id'], None)  # a dry run writes nothing

    # Re-runs, all or nothing, backups ---------------------------------------------------------------------

    def operations(self):
        return [
            {'op': 'create', 'key': 'lilios', 'kind': 'ip', 'parent': POP_MART, 'name': 'LiLiOS', 'why': why()},
            {'op': 'set', 'entry': web('design', 32), 'fields': {'ip': {'key': 'lilios'}}, 'why': why()},
            {'op': 'unlink', 'popcore_id': 'PM0100-01', 'entry': web('design', 30), 'why': why()},
            {'op': 'link', 'popcore_id': 'PM0100-01', 'entry': web('design', 31), 'why': why()},
            {'op': 'move', 'entry': web('series', 10), 'parent': web('ip', 2), 'why': why()},
            {'op': 'delete', 'entry': web('series', 12), 'why': why()},
            {'op': 'set', 'entry': web('design', 31), 'fields': {'name': 'Moon'}, 'why': why()},
        ]

    def test_a_second_run_changes_nothing(self):
        first = self.run_ops(self.operations())
        self.assertTrue(first['applied'], first['refused'])
        after = self.snapshot()
        second = self.run_ops(self.operations())
        self.assertEqual(self.statuses(second), ['already done'] * len(self.operations()))
        self.assertEqual(self.snapshot(), after)

    def test_a_create_does_not_take_over_an_entry_someone_else_made(self):
        result = self.run_ops([{'op': 'create', 'kind': 'series', 'parent': web('ip', 1), 'name': 'ECHO SERIES', 'why': why()}],
                              apply=False)
        self.assertEqual(result['refused'][0]['n'], 1)
        self.assertIn('refer to it instead', result['refused'][0]['result'])

    def test_nothing_is_half_applied(self):
        before = self.snapshot()
        operations = self.operations()
        operations.insert(2, {'op': 'link', 'popcore_id': 'PM9999', 'entry': web('design', 31), 'why': why()})
        operations.append({'op': 'set', 'entry': web('design', 31), 'fields': {'colour': 'red'}, 'why': why()})
        operations.append({'op': 'set', 'entry': web('design', 31), 'fields': {'name': 'Moon'}})  # no why
        result = self.run_ops(operations)
        self.assertFalse(result['applied'])
        self.assertEqual([line['n'] for line in result['refused']], [3, 9, 10])
        self.assertEqual(self.snapshot(), before)
        dry = self.run_ops(operations, apply=False)
        self.assertEqual([line['n'] for line in dry['refused']], [3, 9, 10])
        self.assertEqual(self.snapshot(), before)

    def script(self):
        spec = importlib.util.spec_from_file_location('apply_dex_decisions', ROOT / 'scripts' / 'apply_dex_decisions.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_script(self, *extra):
        path = self.dir / 'decisions.json'
        path.write_text(json.dumps(self.operations()), encoding='utf-8')
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = self.script().main(['--decisions', str(path), '--out', str(self.dir / 'report'), *extra])
        return code, out.getvalue()

    def test_the_script_dry_runs_by_default_and_writes_its_report(self):
        before = self.snapshot()
        code, out = self.run_script()
        self.assertEqual(code, 0, out)
        result = json.loads(out)
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['by_status'], {'would do': 7})
        self.assertEqual(self.snapshot(), before)
        report = json.loads(Path(result['report']).read_text(encoding='utf-8'))
        self.assertEqual(report['report'][0]['card'], 'T-1')
        from openpyxl import load_workbook
        self.assertEqual(load_workbook(result['sheet']).sheetnames, ['Summary', 'Operations', 'Refused'])

    def test_apply_needs_a_fresh_backup_path_and_takes_the_backup_first(self):
        with self.assertRaises(SystemExit):
            self.run_script('--apply')
        taken = self.dir / 'taken.db'
        taken.write_bytes(b'')
        with self.assertRaises(SystemExit):
            self.run_script('--apply', '--backup', str(taken))
        before = self.snapshot()
        backup = self.dir / 'before-decisions.db'
        code, out = self.run_script('--apply', '--backup', str(backup))
        self.assertEqual(code, 0, out)
        self.assertTrue(json.loads(out)['applied'])
        self.assertNotEqual(self.snapshot(), before)
        with closing(sqlite3.connect(backup)) as con:
            self.assertEqual([tuple(row) for row in con.execute('SELECT * FROM dex_entries ORDER BY id')], before)


if __name__ == '__main__':
    unittest.main()
