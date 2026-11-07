"""A pasted daily report points out by line anything written so that stock may move wrongly (owner,
2026-11-06): "flag anything that might be error, and be explicit about where the error is". The checks
never stop a save; the rows they point at are fixed or removed on the paste screen as before."""
from support import IsolatedApiCase

from blueprints.sales import _detect_section_type


class ReportFormatCheckTests(IsolatedApiCase):
    def parse(self, text, store='DT', date=None):
        response = self.client.post('/api/sales/parse_report', headers=self.headers('manager'),
                                    json={'text': text, 'store_code': store, 'date': date})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    @staticmethod
    def found(parsed):
        return [(check['level'], check['lines'], check['section']) for check in parsed['format_checks']]

    def check_at(self, parsed, lines):
        matches = [check for check in parsed['format_checks'] if check['lines'] == lines]
        self.assertEqual(len(matches), 1, parsed['format_checks'])
        return matches[0]

    def test_a_line_with_no_written_quantity_is_named_by_its_line(self):
        parsed = self.parse('2026.11.4 DT\n卡机汇总：\nsa dream x2\nsa animal ver2*1\n\n出店：\ngift for you')
        self.assertEqual(self.found(parsed), [('fix', [3], '卡机'), ('fix', [7], '出店')])
        self.assertIn('不是 x N', self.check_at(parsed, [3])['problem'])
        self.assertIn('没有数量', self.check_at(parsed, [7])['problem'])
        # The line is quoted as written, so it can be found in the paste.
        self.assertEqual(self.check_at(parsed, [3])['text'], ['sa dream x2'])

    def test_a_bare_入库_is_a_heading_so_its_lines_never_join_the_section_above(self):
        # MK, 2026-11-05: "入库" with no colon under 入店 would have put upstairs goods on the floor.
        parsed = self.parse('2026.11.5 DT\n卡机汇总：\nsa dream*1\n入店：\n哭娃星光毛绒：6*4\n入库\n哭娃星光毛绒：6*3')
        self.assertEqual(parsed['unknown_sections'], ['入库:'])
        under = [item for item in parsed['failed'] if item.get('unknown_header') == '入库:']
        self.assertEqual([item['line'] for item in under], [7])
        stocked = [item for item in parsed['confirmed'] + parsed['review'] + parsed['failed']
                   if item['section'] == 'stock_in' and not item.get('unknown_header')]
        self.assertEqual([(item['qty'], item['box_size']) for item in stocked], [(4, 6)])
        check = self.check_at(parsed, [6, 7])
        self.assertEqual(check['level'], 'fix')
        self.assertIn('入库', check['problem'])
        # The heading is answered in the form a section choice (Skip) is accepted under.
        self.assertEqual(_detect_section_type('入库:', {}), ('unknown', ''))

    def test_a_report_dated_another_day_or_written_without_its_year_is_flagged(self):
        other_day = self.parse('2026.11.3 DT\n卡机汇总：\nsa dream*1', date='2026-11-04')
        self.assertEqual(self.found(other_day), [('fix', [1], None)])
        self.assertIn('2026-11-03', other_day['format_checks'][0]['problem'])
        no_year = self.parse('11.5 太谷\n卡机汇总：\nsa dream*1', store='MK', date='2026-11-05')
        # One check for the date line, not a second one for it as a product with no quantity.
        self.assertEqual(self.found(no_year), [('fix', [1], None)])
        self.assertIn('没有年份', no_year['format_checks'][0]['problem'])
        undated = self.parse('卡机汇总：\nsa dream*1', date='2026-11-05')
        self.assertEqual(self.found(undated), [('check', [], None)])
        same_day = self.parse('2026.11.5 DT\n卡机汇总：\nsa dream*1', date='2026-11-05')
        self.assertEqual(same_day['format_checks'], [])

    def test_a_report_for_the_other_store_is_flagged(self):
        parsed = self.parse('2026.11.5 DT\n卡机汇总：\nsa dream*1', store='MK', date='2026-11-05')
        self.assertEqual(self.found(parsed), [('fix', [1], None)])
        self.assertIn('DT', parsed['format_checks'][0]['problem'])

    def test_repeats_big_sales_and_lines_before_any_heading_are_asked_about(self):
        parsed = self.parse('2026.11.3 DT\nsa dream*1\n卡机汇总：\nmerodi点心*12\n'
                            '出库入店：\nsa dream 12*1\n出库入店：\nsa dream 12*1', date='2026-11-03')
        self.assertEqual(self.found(parsed), [
            ('check', [2], '随手记'), ('check', [4], '卡机'), ('check', [5, 7], '出库入店'), ('check', [6, 8], '入店')])
        self.assertIn('12', self.check_at(parsed, [4])['problem'])

    def test_a_number_read_as_part_of_a_name_on_出店_is_explained(self):
        parsed = self.parse('2026.11.3 DT\n出店：\nAlpha 12*2', date='2026-11-03')
        check = self.check_at(parsed, [3])
        self.assertEqual(check['level'], 'check')
        self.assertIn('*24', check['problem'])

    def test_errors_that_stop_the_save_name_their_line(self):
        parsed = self.parse('2026.11.3 DT\n卡机汇总：\nsa dream*1*2', date='2026-11-03')
        self.assertTrue(parsed['metadata_errors'][0].startswith('第3行'), parsed['metadata_errors'])
