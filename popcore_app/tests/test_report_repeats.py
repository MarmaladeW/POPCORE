"""A stock section that repeats itself word for word is caught before anything saves (owner, 2026-11-15).

DT's Nov 14 report repeated a five-line 入库 block by a copy/paste slip; pasted raw, those goods would have been
added twice without a word. The parse now points such a repeat out by its lines and marks the repeat's rows, so
the paste screen leaves them out ("去掉重复 Drop the repeat") unless the manager chooses "两次都算 Count both".
In 卡机/随手记 one line is one sale, so the same lines twice there are normal and never flagged; a single line
written twice in a stock section may be meant (two sets) and is only asked about. A whole report pasted twice
into the box is read once."""
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from support import IsolatedApiCase

from blueprints import sales
from report_checks import repeated_blocks, same_text

REPORTS = Path(__file__).resolve().parent / 'fixtures' / 'daily_reports'


class ReportRepeatTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            # These reports are read the way a live store's are from the day the paste moved 入库 by itself.
            con.execute("UPDATE app_settings SET value='2026-11-01' WHERE key='report_lines_from'")
            con.commit()

    def parse(self, text, date='2026-11-14', live=True):
        with patch.object(sales, '_is_authoritative', return_value=live), \
                patch.object(sales, 'other_store', return_value={'code': 'MK'}):
            response = self.client.post('/api/sales/parse_report', headers=self.headers('manager'),
                                        json={'text': text, 'store_code': 'DT', 'date': date})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    @staticmethod
    def rows(parsed):
        return parsed['confirmed'] + parsed['review'] + parsed['failed']

    @staticmethod
    def repeats(parsed):
        return [(check['repeat_id'], check['repeat_lines'], check['lines']) for check in parsed['format_checks']
                if check.get('repeat_id')]

    def test_the_dt_nov_14_入库_block_written_twice(self):
        text = (REPORTS / 'DT-2026-11-14.txt').read_text(encoding='utf-8')
        parsed = self.parse(text)
        self.assertEqual(self.repeats(parsed), [('block:244-248', [244, 245, 246, 247, 248], list(range(239, 249)))])
        check = next(check for check in parsed['format_checks'] if check.get('repeat_id'))
        self.assertEqual(check['section'], '入库')
        self.assertIn('第 244–248 行', check['problem'])
        self.assertIn('两次都算', check['problem'])
        self.assertEqual(check['text'][-1], 'dimoo mcflurry*2')
        marked = sorted({row['line'] for row in self.rows(parsed) if row.get('repeat_id')})
        self.assertEqual(marked, [244, 245, 246, 247, 248])
        # The first copy stays a normal line, and its rows are all 入库.
        first = [row for row in self.rows(parsed) if 239 <= row['line'] <= 243]
        self.assertTrue(first and all(row['section'] == 'back_in' and not row.get('repeat_id') for row in first))
        # Repeated sales lines (随手记 92–93 "Smiski bed*1", 94–96 "比奇堡二代*1") are one line per sale.
        within_sales = [check for check in parsed['format_checks'] if check['lines'] and all(91 <= line < 186 for line in check['lines'])]
        self.assertEqual(within_sales, [])
        # The repeat's own rows get no second check of their own (line 247 is the copy of 242's "小王子*2…").
        self.assertFalse(any(247 in check['lines'] for check in parsed['format_checks'] if not check.get('repeat_id')))

    def test_a_heading_written_again_with_the_same_lines(self):
        parsed = self.parse('2026.11.14 DT\n卡机汇总：\nsa dream*1\n入库：\nsa shu 6*2\nsa dream 12*1\n'
                            '出店：\nsa dream*1\n入库：\nsa shu 6*2\nsa dream 12*1')
        self.assertEqual(self.repeats(parsed), [('section:9', [9, 10, 11], [4, 5, 6, 9, 10, 11])])
        self.assertEqual(sorted(row['line'] for row in self.rows(parsed) if row.get('repeat_id')), [10, 11])
        # Not also "heading written twice": the repeat says it.
        self.assertFalse([check for check in parsed['format_checks'] if '标题出现' in check['problem']])

    def test_spacing_case_and_full_width_punctuation_do_not_hide_a_repeat(self):
        parsed = self.parse('2026.11.14 DT\n出店：\nSA Dream*1（Gift）\nsa shu*2\nsa  dream *1(gift)\nSA SHU＊2')
        self.assertEqual(self.repeats(parsed), [('block:5-6', [5, 6], [3, 4, 5, 6])])
        self.assertEqual(same_text('小野雾中人*5（the wingless follower)'), same_text('小野雾中人 *5 (The Wingless Follower）'))

    def test_one_line_written_twice_is_only_asked_about(self):
        # "Angry Molly 8*1" twice may mean two sets: it is never dropped, only checked.
        parsed = self.parse('2026.11.14 DT\n出库入店：\nAngry Molly 8*1\nAngry Molly 8*1')
        self.assertEqual(self.repeats(parsed), [])
        self.assertFalse(any(row.get('repeat_id') for row in self.rows(parsed)))
        checks = [check for check in parsed['format_checks'] if check['lines'] == [3, 4]]
        self.assertEqual([check['level'] for check in checks], ['check'])
        self.assertIn('写了 2 次', checks[0]['problem'])

    def test_sales_lines_repeated_are_normal(self):
        parsed = self.parse('2026.11.14 DT\n卡机汇总：\nsmiski bed*1\nsa dream*1\nsmiski bed*1\nsa dream*1\n'
                            '随手记汇总：\nsmiski bed*1\nsa dream*1\nsmiski bed*1\nsa dream*1')
        self.assertEqual(self.repeats(parsed), [])
        self.assertFalse(any(row.get('repeat_id') for row in self.rows(parsed)))

    def test_a_legacy_store_is_checked_too(self):
        parsed = self.parse('2026.11.14 DT\n入店：\nsa shu 6*2\nsa dream 12*1\nsa shu 6*2\nsa dream 12*1', live=False)
        self.assertEqual(self.repeats(parsed), [('block:5-6', [5, 6], [3, 4, 5, 6])])

    def test_the_whole_report_pasted_twice_is_read_once(self):
        text = (REPORTS / 'DT-2026-11-14.txt').read_text(encoding='utf-8')
        once, twice = self.parse(text), self.parse(text + '\n\n' + text)
        self.assertEqual(len(self.rows(twice)), len(self.rows(once)))
        self.assertEqual(twice['metadata_errors'], once['metadata_errors'])
        fix = [check for check in twice['format_checks'] if '贴了两次' in check['problem']]
        self.assertEqual([(check['level'], check['lines']) for check in fix], [('fix', [1, 252])])
        # A different second day is still two reports, which the paste refuses as before.
        other = self.parse(text + '\n' + text.replace('2026.11.14', '2026.11.15', 1))
        self.assertTrue(other['multi_day'])

    def test_repeated_blocks_by_itself(self):
        parts = [(1, 'back_in', [(2, 'a*1'), (3, 'b*1'), (4, 'c*1'), (5, 'a*1'), (6, 'b*1'), (7, 'c*1'),
                                 (8, 'a*1'), (9, 'b*1')])]
        self.assertEqual([(r['lines'], r['of']) for r in repeated_blocks(parts)],
                         [([5, 6, 7], [2, 3, 4]), ([8, 9], [2, 3])])
