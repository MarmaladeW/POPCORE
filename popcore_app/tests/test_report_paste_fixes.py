"""What the Nov 6–8 daily reports needed by hand before they could be pasted (2026-11-09), now read as staff
write it: a sale's place written after it (`-display`, `（diaplay）`, `出库-入店`, `- 出库入店`), the
`出库入display` heading, two items glued together, 卖display of a design whose sale names the series, a
卖display section that only repeats the sales' (display) marks, and a design written as a note
(`海贼王船*1（Polar Tang）`), which staff mean as the dash form `海贼王船 - Polar Tang`.

The raw Nov 7–8 texts are in fixtures/daily_reports, as staff sent them."""
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from support import IsolatedApiCase

from blueprints import sales
from blueprints.sales import _detect_section_type, _split_report_items

REPORTS = Path(__file__).resolve().parent / 'fixtures' / 'daily_reports'


def report_text(name):
    return (REPORTS / f'{name}.txt').read_text(encoding='utf-8')


class ReportPasteFixTests(IsolatedApiCase):
    def parse(self, text, store='DT', date=None, live=True):
        # Both stores are live: their stock sections are stock lines.
        with patch.object(sales, '_is_authoritative', return_value=live):
            response = self.client.post('/api/sales/parse_report', headers=self.headers('manager'),
                                        json={'text': text, 'store_code': store, 'date': date})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    @staticmethod
    def rows(parsed, line=None):
        rows = parsed['confirmed'] + parsed['review'] + parsed['failed']
        return sorted(((row['line'], row['section'], row['raw_name'], row['qty']) for row in rows
                       if line is None or row['line'] == line), key=lambda row: (row[0], row[1]))

    @staticmethod
    def checks(parsed):
        return [(check['level'], check['lines']) for check in parsed['format_checks']]

    def add_series(self, head, designs):
        """A blind-box series: its box (the head) and its confirmed designs, by name."""
        with closing(self.connect()) as con:
            series = con.execute('INSERT INTO product_series(name) VALUES (?)', (head,)).lastrowid
            head_id = con.execute(
                """INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, series_id)
                   VALUES (?, ?, ?, 'random_box', 'box', ?)""", (f'S{series}', head, head, series)).lastrowid
            con.execute('UPDATE product_series SET product_id=? WHERE id=?', (head_id, series))
            for number, design in enumerate(designs, 1):
                con.execute(
                    """INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, series_id, design_name)
                       VALUES (?, ?, ?, 'confirmed_design', 'piece', ?, ?)""",
                    (f'S{series}-{number:02d}', f'{head} {design}', f'{head} {design}', series, design))
            con.commit()

    # ── 1. a sale's place written after it ───────────────────────────────────

    def test_a_display_mark_after_a_sale_is_a_sale_from_display(self):
        parsed = self.parse('2026.11.7 DT\n卡机汇总：\nSA Easter Series 2018-Easter Bunny*1-display\n'
                            '小王子-The King*1（diaplay）\n小王子-snake-display', date='2026-11-07')
        self.assertEqual(self.rows(parsed), [
            (3, 'display_sold', 'SA Easter Series 2018-Easter Bunny', 1), (3, 'pos', 'SA Easter Series 2018-Easter Bunny', 1),
            (4, 'display_sold', '小王子-The King', 1), (4, 'pos', '小王子-The King', 1),
            (5, 'display_sold', '小王子-snake', 1), (5, 'pos', '小王子-snake', 1)])
        # No *N at all: read as one, and the format check says so.
        self.assertEqual(self.checks(parsed), [('check', [5])])
        self.assertIn('按 1 个读', parsed['format_checks'][0]['problem'])
        self.assertFalse(parsed['metadata_errors'])

    def test_a_出库入店_mark_after_a_sale_also_brings_it_down_from_upstairs(self):
        parsed = self.parse('2026.11.7 DT\n随手记汇总：\n小野哈利波特-sorting hat*1出库-入店\n'
                            '小野哈利波特figure - Harry Potter and Hedwig *1 -出库入店\n小王子-Rose- 出库入店',
                            date='2026-11-07')
        self.assertEqual(self.rows(parsed), [
            (3, 'cash', '小野哈利波特-sorting hat', 1), (3, 'refill', '小野哈利波特-sorting hat', 1),
            (4, 'cash', '小野哈利波特figure - Harry Potter and Hedwig', 1),
            (4, 'refill', '小野哈利波特figure - Harry Potter and Hedwig', 1),
            (5, 'cash', '小王子-Rose', 1), (5, 'refill', '小王子-Rose', 1)])
        refills = [row for row in parsed['failed'] if row['section'] == 'refill']
        # One piece down, never a set: a written number in the name is not a set size here.
        self.assertEqual({(row['box_size'], row['loose_qty']) for row in refills}, {(1, 0)})
        self.assertEqual(self.checks(parsed), [('check', [5])])

    def test_a_mark_alone_or_after_the_comma_is_the_place_of_the_sale_before_it(self):
        parsed = self.parse('2026.11.7 DT\n随手记汇总：\n小野哈利波特- 大隐藏special edition*1\n-display\n'
                            '小野哈利波特figure - Albus Dumbledore *1,-出库入店小王子-The King*1（diaplay）',
                            date='2026-11-07')
        self.assertEqual(self.rows(parsed), [
            (3, 'cash', '小野哈利波特- 大隐藏special edition', 1), (3, 'display_sold', '小野哈利波特- 大隐藏special edition', 1),
            (5, 'cash', '小野哈利波特figure - Albus Dumbledore', 1), (5, 'cash', '小王子-The King', 1),
            (5, 'display_sold', '小王子-The King', 1), (5, 'refill', '小野哈利波特figure - Albus Dumbledore', 1)])
        # Both are a reading of where the mark belongs, so both are pointed out.
        self.assertEqual(self.checks(parsed), [('check', [3, 4]), ('check', [5])])
        self.assertIn('卖display', parsed['format_checks'][0]['problem'])
        self.assertIn('出库入店', parsed['format_checks'][1]['problem'])

    def test_names_that_only_look_like_marks_are_left_alone(self):
        parsed = self.parse('2026.11.7 MK\n随手记汇总：\n展示盒100%木色*1\n宝可梦自然-bulbasaur*1\n'
                            '小野公路（display）*1', store='MK', date='2026-11-07')
        self.assertEqual(self.rows(parsed), [
            (3, 'cash', '展示盒100%木色', 1), (4, 'cash', '宝可梦自然-bulbasaur', 1),
            (5, 'cash', '小野公路', 1), (5, 'display_sold', '小野公路', 1)])
        self.assertEqual(parsed['format_checks'], [])
        # A mark with no sale before it is not guessed at: it stays a line to fix.
        alone = self.parse('2026.11.7 DT\n随手记汇总：\n-display', date='2026-11-07')
        self.assertEqual(self.checks(alone), [('fix', [3])])

    def test_a_legacy_store_keeps_the_display_mark_as_a_note(self):
        parsed = self.parse('2026.11.7 DT\n卡机汇总：\nsa dream*1-display', date='2026-11-07', live=False)
        self.assertEqual([(row['section'], row['raw_name'], row['qty'], row['note']) for row in parsed['failed']],
                         [('pos', 'sa dream', 1, 'display')])

    # ── 2. 出库入display ─────────────────────────────────────────────────────

    def test_出库入display_is_入display(self):
        self.assertEqual(_detect_section_type('出库入display：', {}), ('display_stock_in', ''))
        parsed = self.parse('2026.11.7 DT\n出库入display：\nsp image*1（the imagination）\n小野Mime-Patience*1\n'
                            '入display：\nSA animal ver3 - Zebra*1', date='2026-11-07')
        self.assertEqual(parsed['unknown_sections'], [])
        self.assertEqual({row['section'] for row in parsed['failed']}, {'display_stock_in'})
        self.assertEqual(len(parsed['failed']), 3)
        # Two headings staff wrote apart are not "the same heading twice".
        self.assertEqual(parsed['format_checks'], [])

    # ── 3. glued items ────────────────────────────────────────────────────────

    def test_two_items_glued_together_are_two_items(self):
        self.assertEqual(_split_report_items('哭娃星光毛绒*1豹猫搪胶*1'), ['哭娃星光毛绒*1', '豹猫搪胶*1'])
        # The paste is normalized first, so a note's brackets arrive as ( ).
        self.assertEqual(_split_report_items('海贼王船*1(Polar Tang)豹猫搪胶*2'), ['海贼王船*1(Polar Tang)', '豹猫搪胶*2'])
        # Not a second item: a mark, a unit, or letters that may be a mistyped quantity.
        self.assertEqual(_split_report_items('小野哈利波特-sorting hat*1出库-入店'), ['小野哈利波特-sorting hat*1出库-入店'])
        self.assertEqual(_split_report_items('sa dream*2个'), ['sa dream*2个'])
        self.assertEqual(_split_report_items('Test Product*2oops Test Product*1'), ['Test Product*2oops Test Product*1'])
        parsed = self.parse('2026.11.7 DT\n随手记汇总：\n哭娃星光毛绒*1豹猫搪胶*1', date='2026-11-07')
        self.assertEqual(self.rows(parsed), [(3, 'cash', '哭娃星光毛绒', 1), (3, 'cash', '豹猫搪胶', 1)])
        self.assertEqual(parsed['metadata_errors'], [])

    # ── 4. 卖display against same-series sales ───────────────────────────────

    def test_a_design_sold_from_display_counts_its_series_among_the_sales(self):
        self.add_series('海贼王船', ['Polar Tang', 'Thousand Sunny'])
        # 卖display names the design, the sales line names the series with the design as a note (DT, Nov 8).
        design_shown = self.parse('2026.11.8 DT\n随手记汇总：\n海贼王船*1（Polar Tang）\n卖display：\n海贼王船 - Polar Tang*1',
                                  date='2026-11-08')
        self.assertEqual([row['product']['sku'] for row in design_shown['review'] if row['section'] == 'display_sold'],
                         ['S1-01'])
        self.assertEqual(design_shown['format_checks'], [])
        # And the other way round: the series box on display, the design among the sales.
        box_shown = self.parse('2026.11.8 DT\n随手记汇总：\n海贼王船 - Thousand Sunny*1\n卖display：\n海贼王船*1',
                               date='2026-11-08')
        self.assertEqual(box_shown['format_checks'], [])
        # More sold from display than the series sold is still pointed out.
        short = self.parse('2026.11.8 DT\n随手记汇总：\n海贼王船*1\n卖display：\n海贼王船 - Polar Tang*1\n'
                           '海贼王船 - Thousand Sunny*1', date='2026-11-08')
        self.assertEqual(self.checks(short), [('check', [3, 5, 6])])
        self.assertIn('销售里只有 1 个', short['format_checks'][0]['problem'])

    # ── 5. a 卖display section that repeats the sales' (display) marks ───────

    def test_a_卖display_section_that_only_repeats_the_display_marks_is_left_out(self):
        parsed = self.parse('2026.11.8 MK\n卡机汇总：\nshelter*1（Circus）（display）\n萌粒度假*1（display）\n'
                            '卖display：\nshelter*1（Circus）\n萌粒度假*1', store='MK', date='2026-11-08')
        self.assertEqual(self.rows(parsed), [
            (3, 'display_sold', 'shelter', 1), (3, 'pos', 'shelter', 1),
            (4, 'display_sold', '萌粒度假', 1), (4, 'pos', '萌粒度假', 1)])
        self.assertEqual(self.checks(parsed), [('check', [5, 6, 7])])
        self.assertIn('已不算', parsed['format_checks'][0]['problem'])

    def test_a_卖display_section_is_kept_where_it_may_be_another_sale(self):
        # Two Circus sold, one marked (display): the section's Circus may be the other one.
        covered = self.parse('2026.11.8 MK\n卡机汇总：\nshelter*1（Circus）（display）\nshelter*1（Circus）\n'
                             '卖display：\nshelter*1（Circus）', store='MK', date='2026-11-08')
        self.assertEqual([row[0] for row in self.rows(covered) if row[1] == 'display_sold'], [3, 6])
        # Kept as written, with the check that it takes display twice.
        self.assertEqual(self.checks(covered), [('check', [3, 6])])
        # One line in the section repeats nothing: the whole section stays, as written.
        mixed = self.parse('2026.11.8 MK\n卡机汇总：\nshelter*1（Circus）（display）\nsa dream*1\n'
                           '卖display：\nshelter*1（Circus）\nsa dream*1', store='MK', date='2026-11-08')
        self.assertEqual([row[0] for row in self.rows(mixed) if row[1] == 'display_sold'], [3, 6, 7])

    # ── the raw Nov 7–8 texts ─────────────────────────────────────────────────

    def test_the_raw_nov_7_and_8_reports_read_their_marks_headings_and_glued_items(self):
        marks = ('display', 'diaplay', '出库入店', '出库-入店')
        for name in ('DT-2026-11-07', 'DT-2026-11-08', 'MK-2026-11-07', 'MK-2026-11-08'):
            with self.subTest(report=name):
                parsed = self.parse(report_text(name), store=name[:2], date=name[3:])
                self.assertEqual(parsed['metadata_errors'], [])
                self.assertNotIn('出库入display:', parsed['unknown_sections'])
                rows = parsed['confirmed'] + parsed['review'] + parsed['failed']
                # No mark is left in a name, and no line with a mark reads as 0.
                self.assertEqual([row['raw_name'] for row in rows if row['section'] in ('pos', 'cash')
                                  and any(mark in row['raw_name'].lower() for mark in marks)], [])
                marked = [line for line, text in enumerate(report_text(name).splitlines(), 1)
                          if any(mark in text.lower() for mark in marks) and not text.rstrip().endswith(('：', ':'))]
                self.assertEqual([row['line'] for row in rows if row['line'] in marked and not row['qty']], [])
        dt7 = self.parse(report_text('DT-2026-11-07'), date='2026-11-07')
        self.assertEqual(self.rows(dt7, 26), [(26, 'cash', '哭娃星光毛绒', 1), (26, 'cash', '豹猫搪胶', 1)])
        self.assertEqual(self.rows(dt7, 108), [(108, 'pos', '小野哈利波特figure - Harry Potter and Hedwig', 1),
                                               (108, 'refill', '小野哈利波特figure - Harry Potter and Hedwig', 1)])
        self.assertEqual({row[1] for line in range(170, 176) for row in self.rows(dt7, line)}, {'display_stock_in'})

    def test_dt_nov_8_no_longer_says_a_display_sale_has_no_sales(self):
        self.add_series('海贼王船', ['Polar Tang'])
        parsed = self.parse(report_text('DT-2026-11-08'), date='2026-11-08')
        self.assertFalse([check for check in parsed['format_checks'] if '销售里只有 0 个' in check['problem']])
        # Its 卖display section repeats the (display) marks among the sales, so it is left out.
        self.assertIn(('check', list(range(125, 135))), self.checks(parsed))
        self.assertFalse([row for row in parsed['confirmed'] + parsed['review'] + parsed['failed']
                          if 125 <= row['line'] <= 134])
        # Even with the section kept, "海贼王船 - Polar Tang" on 卖display is counted with the series' sales
        # (lines 88 and 114), so it no longer says "sales 0".
        with patch.object(sales, '_repeated_display_blocks', return_value=[]):
            kept = self.parse(report_text('DT-2026-11-08'), date='2026-11-08')
        self.assertFalse([check for check in kept['format_checks'] if 129 in check['lines']
                          and check['section'] == '卖display' and '销售里只有' in check['problem']])

    # ── 6. a design written as a note ─────────────────────────────────────────

    def test_a_design_written_as_a_note_is_suggested_as_the_dash_form_is(self):
        self.add_series('海贼王船', ['Polar Tang', 'Thousand Sunny'])
        self.add_series('小野哈利波特figure', ['Lord Voldemort', 'Draco Malfoy'])
        self.add_series('小野雾中人', ['The Soul Corroder', 'The Backlit Messenger'])
        self.add_series('SP Image', ['The Imagination', 'The Dreamer'])
        self.add_series('漫威英雄', ['Spider-Man', 'Iron Man'])
        parsed = self.parse('2026.11.8 DT\n随手记汇总：\n海贼王船*1（Polar Tang）\n小野哈利波特figure*1（Lord Voldemort）\n'
                            '卡机汇总：\n小野雾中人*1（The Soul Corroder）\n漫威英雄*1（Spider-Man）\n'
                            '入display：\nsp image*1（the imagination）', date='2026-11-08')
        self.assertEqual(parsed['confirmed'], [])
        self.assertEqual([(row['line'], row['product']['sku'], row['score']) for row in parsed['review']],
                         [(3, 'S1-01', 99), (4, 'S2-01', 99), (6, 'S3-01', 99), (7, 'S5-01', 99), (9, 'S4-01', 99)])
        # The series box is still offered after it.
        self.assertIn('S1', [candidate['sku'] for candidate in parsed['review'][0]['candidates']])
        # The same design as the dash form gets.
        dash = self.parse('2026.11.8 DT\n随手记汇总：\n海贼王船 - Polar Tang*1', date='2026-11-08')
        self.assertEqual([row['product']['sku'] for row in dash['review']], ['S1-01'])

    def test_a_note_that_names_no_design_leaves_the_series_box(self):
        self.add_series('海贼王船', ['Polar Tang'])
        parsed = self.parse('2026.11.8 DT\n随手记汇总：\n海贼王船*1（Going Merry）', date='2026-11-08')
        self.assertEqual([row['product']['sku'] for row in parsed['confirmed']], ['S1'])

    def test_cue_and_tender_notes_never_name_a_design(self):
        # Designs named like the notes, so a note read as a design name would find one.
        self.add_series('海贼王船', ['Polar Tang', '隐藏', 'Secret', 'Figure', '15cm', 'Card', 'Cash', '微信'])
        for note in ('隐藏', 'secret', 'figure', '15cm'):
            with self.subTest(note=note):
                text = f'2026.11.8 DT\n随手记汇总：\n海贼王船*1（{note}）'
                with patch.object(sales, '_note_design', return_value=None):
                    before = self.parse(text, date='2026-11-08')
                self.assertEqual(self.parse(text, date='2026-11-08'), before)
        # How it was paid: still the series box, and still confirmed.
        for note in ('cash30+35.5card', 'cash30+card9', 'card', 'Cash', '30+35.5', '微信'):
            with self.subTest(note=note):
                parsed = self.parse(f'2026.11.8 DT\n随手记汇总：\n海贼王船*1（{note}）', date='2026-11-08')
                self.assertEqual([(row['product']['sku'], row['note']) for row in parsed['confirmed']], [('S1', note)])

    def test_the_raw_nov_7_and_8_note_designs_are_suggested(self):
        self.add_series('海贼王船', ['Polar Tang'])
        self.add_series('小野哈利波特figure', ['Draco Malfoy', 'Lord Voldemort'])
        self.add_series('小野雾中人', ['The Soul Corroder'])
        self.add_series('SP Image', ['The Imagination'])
        self.add_series('Shelter', ['Mantel Clock', 'Circus', 'Sunny Doll'])
        expected = {'DT-2026-11-07': {171: {'S4-01'}},
                    'DT-2026-11-08': {114: {'S1-01'}},
                    'MK-2026-11-07': {50: {'S2-01', 'S2-02'}, 120: {'S2-02'}, 121: {'S2-01'}},
                    # "Sonny Doll" is Shelter's Sunny Doll, written with a typo.
                    'MK-2026-11-08': {7: {'S5-01'}, 8: {'S5-02'}, 9: {'S5-03'}, 63: {'S3-01'}, 110: {'S3-01'}}}
        for name, lines in expected.items():
            with self.subTest(report=name):
                parsed = self.parse(report_text(name), store=name[:2], date=name[3:])
                self.assertFalse([row for row in parsed['confirmed'] if row['line'] in lines])
                self.assertEqual({line: {row['product']['sku'] for row in parsed['review'] if row['line'] == line}
                                  for line in lines}, lines)
