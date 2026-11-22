"""Four gaps the DT Nov 19–21 reports showed when the owner pasted them (2026-11-22), each fixed by hand that day
and now read the way the owner wants:

1. 入display is new pieces put onto display (usually that day's delivery), the same as 入店 X（display）. Only
   出库入display is upstairs → display. A day saved when 入display still meant upstairs keeps that reading.
2. 入店 (入库, 入店（display）, 入display) of a product never counted counts it: "if it wasn't counted it was simply
   not counted, but now there is 入店 so it should be added". Lines taking stock out keep the notice.
3. A 卖display line is judged on its own against the day's (display) sales of the same product: a slightly
   different spelling is the same product, and one line selling more that day no longer keeps the whole section.
4. (The pick box showing the design and SKU first is a frontend change, tested in src/lib/productLabel.test.ts.)

The raw Nov 20–21 texts and the versions fixed by hand are in fixtures/daily_reports."""
import io
import json
import sys
from collections import Counter
from contextlib import closing, redirect_stdout
from pathlib import Path

from support import APP_DIR, IsolatedApiCase, db

ROOT = APP_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.inventory_opening import main as _opening_cli  # noqa: E402

REPORTS = Path(__file__).resolve().parent / 'fixtures' / 'daily_reports'
DAY = '2026-11-21'
STOCK = ('refill', 'stock_in', 'stock_out', 'display_sold', 'break_display', 'display_stock_in', 'claw_prize',
         'back_in', 'transfer_out', 'floor_to_back', 'display_to_floor')
PACKED = ('refill', 'stock_in', 'back_in', 'transfer_out', 'floor_to_back', 'display_to_floor')


def report_text(name):
    return (REPORTS / f'{name}.txt').read_text(encoding='utf-8')


class ReportPasteGapTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.dt = con.execute("SELECT id FROM stores WHERE code='DT'").fetchone()[0]
            self.at = {row[0]: row[1] for row in con.execute(
                'SELECT code, id FROM inventory_locations WHERE store_id=? AND is_active=1', (self.dt,))}
            item = lambda sku, name: self._product(con, sku, name, 'ordinary', 'piece')
            self.chords = item('PR0001', 'peach riot power chords')
            self.mcflurry = item('PM0100', 'Dimoo McFlurry')
            self.ver4 = self._product(con, 'SA0004', 'SA animal ver4', 'random_box', 'box')
            self.coffee = self._product(con, 'PM0200', '萌粒 咖啡', 'random_box', 'box')
            self.dream = self._product(con, 'SA0010', 'sa dream', 'random_box', 'box')
            # The Monsters Fruits: a blind box and its design Grape (DT Nov 20 wrote "Fruits" and "Fruit").
            series = con.execute("INSERT INTO product_series(name) VALUES ('The Monsters Fruits')").lastrowid
            self.fruits = self._product(con, 'PM0300', 'The Monsters Fruits', 'random_box', 'box', series=series)
            con.execute('UPDATE product_series SET product_id=? WHERE id=?', (self.fruits, series))
            self.grape = self._product(con, 'PM0300-01', 'The Monsters Fruits Grape', 'confirmed_design', 'piece',
                                       series=series, design='Grape')
            # Never counted anywhere: a 盲盒 by its type (无畏契约 PM0564 on Nov 21), another blind box, and one with
            # no type at all.
            self.valorant = self._unverified(con, 'PM0564', '无畏契约', '盲盒')
            self.uncounted = self._unverified(con, 'PM0565', '没盘过', '盲盒')
            self.untyped = self._unverified(con, 'PM0566', '没类型', None)
            for subject in ('auth0|manager', 'auth0|admin'):
                con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)', (subject, self.dt))
            counts = [('floor', self.chords, 'ordinary', 0), ('upstairs', self.mcflurry, 'ordinary', 2),
                      ('display', self.mcflurry, 'ordinary', 1), ('display', self.ver4, 'random_box', 1),
                      ('floor', self.ver4, 'random_box', 5), ('floor', self.coffee, 'random_box', 10),
                      ('display', self.coffee, 'random_box', 1), ('floor', self.dream, 'random_box', 4),
                      ('display', self.dream, 'random_box', 1), ('display', self.grape, 'confirmed_design', 1)]
            for code, product, form, quantity in counts:
                con.execute("""INSERT INTO opening_counts(location_id, product_id, stock_form, quantity, counted_by)
                               VALUES (?, ?, ?, ?, 'auth0|admin')""", (self.at[code], product, form, quantity))
            con.commit()
        empty = [arg for code in sorted(set(self.at) - {'floor', 'upstairs', 'display'})
                 for arg in ('--empty-location', f'DT-{code}')]
        with redirect_stdout(io.StringIO()):
            _opening_cli(['intake', '--db', db.DB_PATH, '--from-app', '--store', 'DT', *empty,
                          '--business-date', '2026-11-14', '--actor-sub', 'auth0|admin',
                          '--apply', '--backup', str(Path(self.tempdir.name) / 'before-dt.db')])

    @staticmethod
    def _product(con, sku, name, form, unit, series=None, design=None):
        return con.execute(
            """INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, identity_status, series_id,
                                    design_name)
               VALUES (?, ?, ?, ?, ?, 'verified', ?, ?)""", (sku, name, name, form, unit, series, design)).lastrowid

    @staticmethod
    def _unverified(con, sku, name, product_type):
        return con.execute("INSERT INTO products(sku, name_cn_en, jizhanming, product_type) VALUES (?, ?, ?, ?)",
                           (sku, name, name, product_type)).lastrowid

    # ── the manager's paste ──────────────────────────────────────────────────

    def parse(self, text, store='DT'):
        response = self.client.post('/api/sales/parse_report', json={'text': text, 'store_code': store},
                                    headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    @staticmethod
    def rows(parsed):
        return parsed['confirmed'] + parsed['review'] + parsed['failed']

    def body(self, parsed):
        """What the paste screen sends once every row is accepted as the paste picked it."""
        items = []
        for row in parsed['confirmed'] + parsed['review']:
            item = {'product_id': row['product']['id'], 'section': row['section'], 'raw_name': row['raw_name'],
                    'notes': row.get('note') or ''}
            if row['section'] in PACKED:
                item.update(box_size=row['box_size'], num_boxes=row['qty'], loose_qty=row.get('loose_qty') or 0,
                            to_display=bool(row.get('to_display')))
            elif row['section'] in STOCK:
                item.update(qty=row['qty'], from_display=bool(row.get('from_display')))
            else:
                item['qty_pos'] = row['qty']
            items.append(item)
        return self.lines_body(items)

    @staticmethod
    def lines_body(items, day=DAY):
        return {'date': day, 'store_code': 'DT', 'mode': 'replace', 'classification': 'summary_only',
                'stock_mode': 'live', 'items': items}

    @staticmethod
    def line(product, section, **amount):
        item = {'product_id': product, 'section': section, 'raw_name': str(product)}
        if section in PACKED:
            item.update(box_size=amount.get('size', 1), num_boxes=amount['n'], loose_qty=0,
                        to_display=amount.get('to_display', False))
        else:
            item.update(qty=amount['n'], from_display=False)
        return item

    def save(self, body):
        preview = self.client.post('/api/sales/submit_daily_report', json={**body, 'dry_run': True},
                                   headers=self.headers('manager'))
        self.assertEqual(preview.status_code, 200, preview.get_json())
        response = self.client.post('/api/sales/submit_daily_report',
                                    json={**body, 'stock_digest': preview.get_json()['stock_digest']},
                                    headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['stock']

    def balance(self, product, code):
        with closing(self.connect()) as con:
            row = con.execute("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                                 AND disposition='saleable'""", (product, self.at[code])).fetchone()
            return row[0] if row else 0

    def identity(self, product):
        with closing(self.connect()) as con:
            return tuple(con.execute('SELECT identity_status, stock_form, stock_unit FROM products WHERE id=?',
                                     (product,)).fetchone())

    def notices(self):
        response = self.client.get('/api/sales/report-stock', headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        return {(row['product_id'], row['reason']) for row in response.get_json()['notices']}

    # ── 1. 入display is new goods onto display ───────────────────────────────

    def test_入display_puts_new_pieces_on_display_and_出库入display_brings_them_down(self):
        parsed = self.parse('2026.11.21 DT\n入display：\npeach riot power chords*1\n'
                            '出库入display：\nDimoo McFlurry*1')
        self.assertEqual(sorted((row['line'], row['section'], bool(row.get('to_display'))) for row in self.rows(parsed)),
                         [(3, 'stock_in', True), (5, 'display_stock_in', False)])
        self.assertEqual(parsed['format_checks'], [])
        stock = self.save(self.body(parsed))
        moved = {line['section']: (line.get('from', {}).get('location'), line['to']['location'], line['moving'])
                 for line in stock['lines']}
        self.assertEqual(moved, {'stock_in': (None, 'display', 1), 'display_stock_in': ('upstairs', 'display', 1)})
        self.assertEqual((self.balance(self.chords, 'display'), self.balance(self.chords, 'floor'),
                          self.balance(self.chords, 'upstairs')), (1, 0, 0))
        self.assertEqual((self.balance(self.mcflurry, 'upstairs'), self.balance(self.mcflurry, 'display')), (1, 2))
        # Pasted again unchanged, the day moves nothing more.
        again = self.save(self.body(self.parse('2026.11.21 DT\n入display：\npeach riot power chords*1\n'
                                               '出库入display：\nDimoo McFlurry*1')))
        self.assertEqual([line['moving'] for line in again['lines']], [0, 0])
        self.assertEqual((self.balance(self.chords, 'display'), self.balance(self.mcflurry, 'display')), (1, 2))

    def test_入display_is_read_like_入店_display(self):
        # The hand fix of Nov 21 rewrote each 入display line as "入店 X*1（display）": both now read the same.
        written = self.parse('2026.11.21 DT\n入display：\npeach riot power chords*1\n星星人美味时刻毛绒6*2')
        fixed = self.parse('2026.11.21 DT\n入店：\npeach riot power chords*1（display）\n星星人美味时刻毛绒6*2（display）')
        shape = lambda parsed: sorted((row['section'], row['raw_name'], row.get('note') or '', row['qty'], row['box_size'],
                                       bool(row.get('to_display'))) for row in self.rows(parsed))
        self.assertEqual(shape(written), shape(fixed))
        self.assertEqual(shape(written)[0], ('stock_in', 'peach riot power chords', '', 1, 1, True))

    def test_a_day_saved_when_入display_meant_upstairs_keeps_that_reading(self):
        with closing(self.connect()) as con:
            con.execute("UPDATE app_settings SET value=? WHERE key='display_in_old_days'", (json.dumps({'DT': [DAY]}),))
            con.commit()
        parsed = self.parse('2026.11.21 DT\n入display：\nDimoo McFlurry*1')
        self.assertEqual([(row['section'], bool(row.get('to_display'))) for row in self.rows(parsed)],
                         [('display_stock_in', False)])
        (check,) = parsed['format_checks']
        self.assertEqual((check['level'], check['lines']), ('check', [2]))
        self.assertIn('按 楼上 → display 读的', check['problem'])
        # The next day is not on the list: it reads the new way.
        later = self.parse('2026.11.22 DT\n入display：\nDimoo McFlurry*1')
        self.assertEqual([(row['section'], bool(row.get('to_display'))) for row in self.rows(later)], [('stock_in', True)])

    def test_the_migration_lists_the_days_saved_with_an_upstairs_to_display_line(self):
        with closing(self.connect()) as con:
            floor, upstairs, display = self.at['floor'], self.at['upstairs'], self.at['display']
            rows = [('2026-11-15', 'a', 'display_stock_in', 'move', upstairs, display, 1, 1),
                    ('2026-11-16', 'b', 'display_stock_in', 'move', upstairs, display, 1, 0),  # still short
                    ('2026-11-17', 'c', 'display_stock_in', 'move', upstairs, display, 0, 0),  # taken off the report
                    ('2026-11-18', 'd', 'stock_in', 'receipt', None, floor, 3, 3)]
            for day, key, section, kind, source, target, wanted, moved in rows:
                con.execute("""INSERT INTO report_stock_moves(store_id, business_date, line_key, section, kind,
                                   from_product_id, from_location_id, to_product_id, to_location_id, wanted, moved)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (self.dt, day, key, section, kind, self.mcflurry if source else None, source,
                             self.mcflurry, target, wanted, moved))
            con.execute("DELETE FROM app_settings WHERE key='display_in_old_days'")
            con.execute("DELETE FROM _migrations WHERE name='display_in_new_goods'")
            cur = con.cursor()
            db._migration_display_in_new_goods(con, cur)
            con.commit()
            value = con.execute("SELECT value FROM app_settings WHERE key='display_in_old_days'").fetchone()[0]
        self.assertEqual(json.loads(value), {'DT': ['2026-11-15', '2026-11-16']})

    def test_a_legacy_store_keeps_入display_as_a_note(self):
        parsed = self.parse('2026.11.21 MK\n入display：\npeach riot power chords*1', store='MK')
        self.assertEqual((self.rows(parsed), parsed['display_stock_in']), ([], ['peach riot power chords*1']))

    # ── 2. 入店 of a never-counted product counts it ─────────────────────────

    def test_a_never_counted_products_入店_counts_it_and_its_other_lines_then_move(self):
        # DT Nov 21: 入店 无畏契约12*3, 拆display 无畏契约*1, 出店 无畏契约*1. Nothing moved until it was counted as 0.
        stock = self.save(self.lines_body([self.line(self.valorant, 'stock_in', size=12, n=3),
                                           self.line(self.valorant, 'break_display', n=1),
                                           self.line(self.valorant, 'stock_out', n=1)]))
        self.assertEqual(self.identity(self.valorant), ('verified', 'random_box', 'box'))
        by_section = {line['section']: line for line in stock['lines']}
        self.assertEqual({section: (line['moving'], line['short']) for section, line in by_section.items()},
                         {'stock_in': (36, 0), 'break_display': (1, 0), 'stock_out': (1, 0)})
        # The preview and the result say the 入店 line is its first count.
        self.assertEqual(by_section['stock_in'].get('first_count'), 'random_box')
        self.assertNotIn('first_count', by_section['stock_out'])
        self.assertEqual((self.balance(self.valorant, 'floor'), self.balance(self.valorant, 'display')), (34, 1))
        self.assertEqual(self.notices(), set())

    def test_入库_and_入店_display_of_a_never_counted_product_add_too(self):
        stock = self.save(self.lines_body([self.line(self.valorant, 'back_in', n=2),
                                           self.line(self.uncounted, 'stock_in', n=1, to_display=True)]))
        self.assertTrue(all(line['moving'] and not line['short'] for line in stock['lines']), stock['lines'])
        self.assertEqual((self.balance(self.valorant, 'upstairs'), self.balance(self.uncounted, 'display')), (2, 1))
        self.assertEqual((self.identity(self.valorant)[0], self.identity(self.uncounted)[0]), ('verified', 'verified'))

    def test_lines_taking_a_never_counted_product_out_keep_the_notice(self):
        stock = self.save(self.lines_body([self.line(self.uncounted, 'stock_out', n=1)]))
        (line,) = stock['lines']
        self.assertEqual((line['moving'], line['short'], line['problem']), (0, 1, 'not_counted'))
        self.assertEqual(self.identity(self.uncounted)[0], 'unverified')
        self.assertIn((self.uncounted, 'move_short'), self.notices())

    def test_a_never_counted_product_with_no_type_is_not_guessed(self):
        stock = self.save(self.lines_body([self.line(self.untyped, 'stock_in', n=2)]))
        (line,) = stock['lines']
        self.assertEqual((line['moving'], line['short'], line['problem']), (0, 2, 'not_counted'))
        self.assertEqual(self.identity(self.untyped), ('unverified', None, None))

    # ── 3. each 卖display line on its own ────────────────────────────────────

    def test_a_卖display_line_spelled_differently_still_repeats_the_display_sale(self):
        # DT Nov 20: "The Monsters Fruits - Grape*1（display）" sold, "卖display: The Monsters Fruit - Grape*1".
        parsed = self.parse('2026.11.20 DT\n卡机汇总：\nThe Monsters Fruits - Grape*1（display）\n'
                            '卖display:\nThe Monsters Fruit - Grape*1')
        self.assertEqual(sorted((row['line'], row['section']) for row in self.rows(parsed)),
                         [(3, 'display_sold'), (3, 'pos')])
        (check,) = parsed['format_checks']
        self.assertEqual(check['lines'], [3, 4, 5])
        self.assertIn('已不算', check['problem'])
        # Display holds 1: the sale takes it once, and no notice is left.
        stock = self.save(self.body(parsed))
        self.assertEqual((self.balance(self.grape, 'display'), stock['moves_short']), (0, []))

    def test_one_line_selling_more_that_day_no_longer_keeps_the_whole_section(self):
        # DT Nov 21: 萌粒 咖啡 sold 4, one marked (display); every line of the 卖display section repeats a mark.
        parsed = self.parse('2026.11.21 DT\n卡机汇总：\nSA animal ver4 *1（display）\nDimoo McFlurry*1（display）\n'
                            '萌粒 咖啡*1\n随手记汇总：\n萌粒 咖啡*1（display）\n萌粒 咖啡*1，萌粒 咖啡*1\n'
                            '卖display：\nSA animal ver4 *1\nDimoo McFlurry*1\n萌粒 咖啡*1')
        self.assertFalse([row for row in self.rows(parsed) if row['line'] >= 10])
        (check,) = parsed['format_checks']
        self.assertEqual(check['lines'], [3, 4, 7, 9, 10, 11, 12])
        stock = self.save(self.body(parsed))
        # Display takes each piece once; 3 of the 4 coffees come off the floor.
        self.assertEqual((self.balance(self.ver4, 'display'), self.balance(self.mcflurry, 'display'),
                          self.balance(self.coffee, 'display'), self.balance(self.coffee, 'floor')), (0, 0, 0, 10 - 3))
        self.assertEqual(stock['moves_short'], [])

    def test_only_the_lines_that_repeat_are_left_out(self):
        parsed = self.parse('2026.11.21 DT\n卡机汇总：\n萌粒 咖啡*1（display）\nsa dream*2\nSA animal ver4 *1（display）\n'
                            '卖display：\n萌粒 咖啡*1\n萌粒 咖啡*1\nsa dream*1\nSA animal ver4 *2')
        kept = sorted((row['line'], row['raw_name'], row['qty']) for row in self.rows(parsed)
                      if row['section'] == 'display_sold' and row['line'] >= 7)
        # One coffee mark covers one 卖display coffee only; sa dream has no mark; ver4*2 is more than its one mark.
        self.assertEqual(kept, [(8, '萌粒 咖啡', 1), (9, 'sa dream', 1), (10, 'SA animal ver4', 2)])
        repeat = [check for check in parsed['format_checks'] if '已不算' in check['problem']]
        self.assertEqual([check['lines'] for check in repeat], [[3, 6, 7]])

    # ── the raw Nov 20 and Nov 21 reports ────────────────────────────────────

    def test_the_raw_nov_20_and_21_reports_now_read_as_they_were_fixed_by_hand(self):
        def shape(parsed):
            return Counter((row['section'], row['raw_name'], row.get('note') or '', row['qty'], row.get('box_size'),
                            row.get('loose_qty') or 0, bool(row.get('to_display')), bool(row.get('from_display')))
                           for row in self.rows(parsed))

        for day in ('2026-11-20', '2026-11-21'):
            with self.subTest(day=day):
                raw = self.parse(report_text(f'DT-{day}'))
                fixed = self.parse(report_text(f'DT-{day}-as-pasted-v2'))
                self.assertEqual(raw['metadata_errors'], fixed['metadata_errors'])
                self.assertEqual(shape(raw), shape(fixed))
                left_out = [check for check in raw['format_checks'] if '已不算' in check['problem']]
                self.assertEqual(len(left_out), 1)
        nov21 = self.parse(report_text('DT-2026-11-21'))
        # The six 卖display lines (189–194) repeat the six (display) sales and are all left out.
        self.assertFalse([row for row in self.rows(nov21) if 188 <= row['line'] <= 194])
        # The six 入display lines (233–238) are new pieces onto display.
        self.assertEqual({(row['section'], bool(row.get('to_display'))) for row in self.rows(nov21)
                          if 233 <= row['line'] <= 238}, {('stock_in', True)})
