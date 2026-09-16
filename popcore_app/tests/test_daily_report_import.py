"""Synthetic coverage of the handwritten report contract; never contacts providers."""
from contextlib import closing
from unittest.mock import patch
import os

from support import IsolatedApiCase
from blueprints.sales import _parse_token


class DailyReportImportTests(IsolatedApiCase):
    def parse(self, text, **extra):
        response = self.client.post('/api/sales/parse_report', headers=self.headers(),
                                    json={'text': text, 'store_code': 'MK', **extra})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def submit(self, items, **extra):
        return self.client.post('/api/sales/submit_daily_report', headers=self.headers(),
                                json={'date': '2026-09-14', 'store_code': 'DT',
                                      'classification': 'summary_only', 'items': items, **extra})

    def test_report_metadata_and_repeated_items_do_not_add_discount_sale(self):
        parsed = self.parse('''2026.9.14 DT
卡机汇总：
Test Product*2
随手记汇总：
Test Product*1, Test Product * 1
员工折扣：
staff使用员工折扣购买Test Product*1 卡机18.07今月剩余1次
卖display：
Test Product*1 （秘密）
娃娃机：
17:48 Test Product*1
现金：
595/601.5''')
        self.assertEqual(parsed['store'], 'DT')
        self.assertEqual(parsed['detected_date'], '2026-09-14')
        self.assertEqual(len(parsed['confirmed']), 3)
        self.assertFalse(parsed['failed'] or parsed['review'])
        self.assertEqual(parsed['cash_total_reported'], 595)
        self.assertEqual(parsed['cash_expected_reported'], 601.5)
        self.assertEqual(len(parsed['employee_discounts']), 1)
        self.assertEqual(parsed['display_sales'], ['Test Product*1 (秘密)'])
        self.assertEqual(parsed['claw_prizes'], ['17:48 Test Product*1'])
        items = [{**it, 'product_id': it['product']['id']} for it in parsed['confirmed']]
        metadata = {'cash_actual': 595, 'cash_expected': 601.5,
                    'employee_discounts': parsed['employee_discounts'],
                    'display_sales': parsed['display_sales'], 'claw_prizes': parsed['claw_prizes']}
        for _ in range(2):
            self.assertEqual(self.submit(items, report_metadata=metadata).status_code, 200)
        with closing(self.connect()) as con:
            row = con.execute('SELECT * FROM daily_sales').fetchone()
            self.assertEqual((row['qty_pos'], row['qty_cash'], row['qty_employee'], row['qty_sold']),
                             (2, 2, 0, 4))
            cash = con.execute('SELECT * FROM daily_report_metadata').fetchone()
            self.assertEqual((cash['cash_actual_cents'], cash['cash_expected_cents']), (59500, 60150))
        saved = self.client.get('/api/sales/report-metadata?date=2026-09-14&store=DT',
                                headers=self.headers('manager')).get_json()
        self.assertEqual(saved['cash_difference'], -6.5)
        self.assertEqual(saved['employee_discounts'], metadata['employee_discounts'])

    def test_bad_quantities_and_pack_suffixes_require_review(self):
        from llm_parser import _validate
        for qty in (True, 1.5, float('inf'), 0, 2**63):
            item = _validate({'items': [{'section': 'pos', 'name': 'Test Product', 'qty': qty, 'box_size': qty}]})['items'][0]
            self.assertIsNone(item['qty'])
            self.assertIsNone(item['box_size'])
        for suffix in ('oops', '0', '-2', '1.5', '2oops', '2*1', str(2**63)):
            with self.subTest(suffix=suffix):
                self.assertTrue(_parse_token('Test Product*' + suffix, 'pos')['flagged'])
        self.assertEqual(_parse_token('smiski球s3*1', 'stock_in')['raw_name'], 'smiski球s3')
        pack = _parse_token('smiski球s3 24*1', 'stock_in')
        self.assertEqual((pack['raw_name'], pack['box_size'], pack['qty']), ('smiski球s3', 24, 1))
        self.assertEqual(_parse_token('萌粒皮克斯12*3', 'stock_in')['box_size'], 12)

    def test_nonpos_header_spelling_variant_preserves_repeated_sales(self):
        for header in ('随手机汇总：', '随手机汇总'):
            with self.subTest(header=header):
                parsed = self.parse('2026.9.5 DT\n卡机汇总：Test Product*2\n' + header
                                    + '\nTest Product*1, Test Product*1', engine='rules')
                self.assertFalse(parsed['unknown_sections'] or parsed['review'] or parsed['failed'])
                self.assertEqual([(r['section'], r['qty']) for r in parsed['confirmed']],
                                 [('pos', 2), ('cash', 1), ('cash', 1)])

    def test_mixed_receipts_preserve_sealed_counts_and_opened_display_notes(self):
        parsed = self.parse('2026.9.5 DT\n入店：\nEcho：7+（display）*1\n'
                            '犬夜叉：2+（display）*1\n鸟毛绒：4+（display）*1', engine='rules')
        rows = parsed['review'] + parsed['failed']
        self.assertFalse(parsed['confirmed'] or parsed['metadata_errors'])
        self.assertEqual([(r['raw_name'], r['section'], r['qty'], r['box_size']) for r in rows],
                         [('Echo', 'stock_in', 7, 1), ('犬夜叉', 'stock_in', 2, 1),
                          ('鸟毛绒', 'stock_in', 4, 1)])
        self.assertEqual(parsed['display_stock_in'],
                         ['Echo:7+(display)*1', '犬夜叉:2+(display)*1', '鸟毛绒:4+(display)*1'])
        for row, source in zip(rows, parsed['display_stock_in']):
            self.assertIn(source, row['note'])

        parsed = self.parse('2026.9.14 DT\n入店：Test Product:7 + (DISPLAY)*2', engine='rules')
        self.assertFalse(parsed['confirmed'] or parsed['failed'] or parsed['metadata_errors'])
        row, = parsed['review']
        self.assertEqual((row['qty'], row['box_size'], row['loose_qty']), (7, 1, 0))
        self.assertFalse(row['flagged'])
        result = self.submit([{**row, 'product_id': self.product_id, 'num_boxes': row['qty']}],
                             report_metadata={'display_stock_in': parsed['display_stock_in']})
        self.assertEqual(result.status_code, 200, result.get_json())
        with closing(self.connect()) as con:
            balance = con.execute('SELECT upstairs_qty,instore_qty FROM stock WHERE product_id=?',
                                  (self.product_id,)).fetchone()
            self.assertEqual(tuple(balance), (3, 9))
            self.assertEqual([tuple(r) for r in con.execute('SELECT txn_type,qty FROM stock_transactions')],
                             [('report_stock_in', 7)])
        saved = self.client.get('/api/sales/report-metadata?date=2026-09-14&store=DT',
                                headers=self.headers('manager')).get_json()
        self.assertEqual(saved['display_stock_in'], ['Test Product:7 + (DISPLAY)*2'])

    def test_malformed_mixed_receipts_cannot_disappear_into_display_metadata(self):
        for expression in ('7+(display)*', '7+(display)*0', '0+(display)*1',
                           '-2+(display)*1', '7.5+(display)*1', '7+(display)*1oops',
                           '7+(display)*1+2', '7+(display)*1*2', '7(display)*1',
                           '7+(display*1', '7+(display)*9223372036854775808'):
            with self.subTest(expression=expression):
                line = 'Test Product:' + expression
                parsed = self.parse('2026.9.14 DT\n入店：' + line, engine='rules')
                self.assertFalse(parsed['confirmed'] or parsed['display_stock_in'])
                self.assertTrue(parsed['metadata_errors'])
                rows = parsed['review'] + parsed['failed']
                self.assertTrue(rows)
                self.assertTrue(all(r['flagged'] for r in rows))
                self.assertIn(line, rows[0]['note'])
                result = self.submit([{**rows[0], 'product_id': self.product_id, 'num_boxes': 1}])
                self.assertEqual(result.status_code, 400)

    def test_pack_suffix_guard_protects_series_tokens_not_plural_product_names(self):
        for name in ('Peach riot power chords', 'Midlane Figures', 'Test Product'):
            with self.subTest(name=name):
                item = _parse_token(name + ' 12*1', 'stock_in')
                self.assertFalse(item['flagged'])
                self.assertEqual((item['raw_name'], item['box_size'], item['qty']), (name, 12, 1))
                self.assertEqual(item['box_size'] * item['qty'] + item['loose_qty'], 12)
        for name in ('smiski球s3', 'Smiski S 3', 'SA ver4', 'SA version 4', 'Smiski series1'):
            with self.subTest(name=name):
                item = _parse_token(name + '*1', 'stock_in')
                self.assertEqual((item['raw_name'], item['box_size'], item['qty']), (name, 1, 1))
                pack = _parse_token(name + ' 12*2+5', 'stock_in')
                self.assertEqual((pack['raw_name'], pack['box_size'], pack['qty'], pack['loose_qty']),
                                 (name, 12, 2, 5))

    def test_ambiguous_exact_and_secret_qualifier_are_never_auto_confirmed(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO products (sku,name_cn_en,jizhanming) VALUES ('TWIN','Test Product','Test Product')")
            con.commit()
        parsed = self.parse('2026.9.14 DT\n卡机汇总：\nTest Product*1\nTest Product*1 （秘密）')
        self.assertFalse(parsed['confirmed'])
        self.assertEqual(len(parsed['review']) + len(parsed['failed']), 2)

    def test_invalid_metadata_and_employee_line_leave_report_unchanged(self):
        item = {'product_id': self.product_id, 'section': 'pos', 'qty_pos': 1}
        self.assertEqual(self.submit([item]).status_code, 200)
        before = self.snapshot(('daily_sales', 'daily_report_metadata'))
        for actual in (-1, True, 'nan', '1.001', {}, 2**63):
            with self.subTest(actual=actual):
                result = self.submit([item], report_metadata={'cash_actual': actual})
                self.assertEqual(result.status_code, 400)
                self.assertEqual(self.snapshot(('daily_sales', 'daily_report_metadata')), before)
        for section in ('employee_discount', 'sell_display', 'claw'):
            result = self.submit([{**item, 'section': section}])
            self.assertEqual(result.status_code, 400)
        self.assertEqual(self.snapshot(('daily_sales', 'daily_report_metadata')), before)

    def test_pack_units_are_not_multiplied_by_catalog_pack_size_twice(self):
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET product_type='盲盒', boxes_per_dan=12 WHERE id=?", (self.product_id,))
            con.execute('UPDATE stock SET upstairs_qty=100 WHERE product_id=?', (self.product_id,))
            con.commit()
        result = self.submit([{'product_id': self.product_id, 'section': 'stock_in', 'box_size': 12, 'num_boxes': 1}])
        self.assertEqual(result.status_code, 200, result.get_json())
        with closing(self.connect()) as con:
            row = con.execute('SELECT upstairs_qty,instore_qty FROM stock WHERE product_id=?', (self.product_id,)).fetchone()
            self.assertEqual(tuple(row), (88, 14))
        # Existing stock history remains protected from replacement.
        self.assertEqual(self.submit([]).status_code, 409)

    def test_cash_errors_multiday_and_llm_annotations_remain_reviewable(self):
        parsed = self.parse('2026.9.14 DT\n卡机汇总：Test Product*1\n现金：595/oops\n2026.9.15 DT')
        self.assertTrue(parsed['metadata_errors'])
        self.assertTrue(parsed['multi_day'])
        with patch.dict(os.environ, {'ENABLE_LLM_PARSER': '1'}), patch('llm_parser.available', return_value=True), patch(
            'llm_parser.parse_report_llm', return_value={
                'items': [{'section': sec, 'name': 'Test Product', 'qty': 1}
                          for sec in ('pos', 'employee_discount', 'sell_display', 'claw')],
                'cash_total': 999,
            }
        ):
            parsed = self.parse('2026.9.14 DT\n卡机汇总：Test Product*1\n员工折扣：staff购买Test Product*1 卡机18.07\n卖display：Test Product*1\n娃娃机：17:48 Test Product*1\n现金：595/601.5', engine='llm')
        self.assertEqual(parsed['parser_engine'], 'llm')
        self.assertFalse(parsed['confirmed'])
        self.assertEqual(len(parsed['review']), 1)
        self.assertEqual((parsed['cash_total_reported'], parsed['cash_expected_reported']), (595, 601.5))
        self.assertEqual(len(parsed['employee_discounts']), 1)
        self.assertEqual(len(parsed['claw_prizes']), 1)
        self.assertEqual(len(parsed['display_sales']), 1)

    def test_metadata_rolls_back_with_stock_history_and_clears_with_day(self):
        item = {'product_id': self.product_id, 'section': 'pos', 'qty': 1}
        metadata = {'cash_actual': 10, 'cash_expected': 12, 'employee_discounts': []}
        self.assertEqual(self.submit([item], report_metadata=metadata).status_code, 200)
        with closing(self.connect()) as con:
            con.execute("INSERT INTO stock_transactions (product_id,txn_type,qty,location,date,store_id) VALUES (?,'display_open',-1,'instore','2026-09-14',?)", (self.product_id, self.store_id))
            con.commit()
        before = self.snapshot(('daily_sales', 'daily_report_metadata', 'stock_transactions'))
        self.assertEqual(self.submit([item], report_metadata={'cash_actual': 99}).status_code, 409)
        self.assertEqual(self.snapshot(('daily_sales', 'daily_report_metadata', 'stock_transactions')), before)
        with closing(self.connect()) as con:
            con.execute('DELETE FROM stock_transactions')
            con.commit()
        response = self.client.delete('/api/sales/clear_day?date=2026-09-14&store_code=DT', headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.snapshot(('daily_report_metadata',))['daily_report_metadata'], [])

    def test_header_below_title_and_annotation_only_report(self):
        text = '交班报告\n2026.9.14 DT\n卖展示：Test Product*1 (秘密)\n娃娃机：17:48 Test Product*1\n现金：0/0'
        parsed = self.parse(text)
        self.assertEqual((parsed['detected_date'], parsed['store']), ('2026-09-14', 'DT'))
        self.assertFalse(parsed['confirmed'] or parsed['review'])
        self.assertEqual([row['raw_name'] for row in parsed['failed']], ['交班报告'])
        self.assertEqual(parsed['display_sales'], ['Test Product*1 (秘密)'])
        metadata = {'cash_actual': 0, 'cash_expected': 0, 'display_sales': parsed['display_sales'], 'claw_prizes': parsed['claw_prizes']}
        self.assertEqual(self.submit([], report_metadata=metadata).status_code, 200)
        for store in ('DT', 'ALL'):
            result = self.client.get('/api/sales/recorded-dates?month=2026-09&store=' + store, headers=self.headers())
            self.assertEqual(result.get_json(), ['2026-09-14'])
        parsed = self.parse('2026.9.14 DT\nTest Product*1\n2026.9.14 MK\nTest Product*1')
        self.assertTrue(parsed['metadata_errors'])

    def test_unique_literal_store_overrides_fallback_across_all_headers(self):
        text = '2026.9.14\n2026.9.14 DT\n卡机汇总：Test Product*1'
        parsed = self.parse(text)
        self.assertEqual(parsed['store'], 'DT')
        self.assertFalse(parsed['metadata_errors'])
        with patch.dict(os.environ, {'ENABLE_LLM_PARSER': '1'}), patch('llm_parser.available', return_value=True), patch(
            'llm_parser.parse_report_llm', return_value={
                'store': 'MK', 'items': [{'section': 'pos', 'name': 'Test Product', 'qty': 1}],
            }
        ):
            parsed = self.parse(text, engine='llm')
        self.assertEqual(parsed['store'], 'DT')
        self.assertTrue(parsed['metadata_errors'])

    def test_cash_exchange_does_not_swallow_following_sales(self):
        parsed = self.parse('2026.9.12 DT\n随手记汇总：\nTest Product*1\n娃娃机*2\nTest Product*3，Test Product*1')
        self.assertEqual(sum(i['qty'] for i in parsed['confirmed']), 5)
        self.assertFalse(parsed['review'] or parsed['failed'] or parsed['claw_prizes'])
        self.assertEqual(parsed['cash_exchanges'], ['娃娃机*2'])

    def test_spaced_items_unicode_lines_and_notes_preserve_every_entry(self):
        parsed = self.parse('“””\u20282026.9.8 DT\u2028随手记汇总：\u2028Test Product*1 Test Product*2 (cash30+35.5card) Test Product*1\u2028“””')
        self.assertEqual([i['qty'] for i in parsed['confirmed']], [1, 2, 1])
        self.assertFalse(parsed['failed'] or parsed['review'])
        self.assertEqual(parsed['confirmed'][1]['note'], 'cash30+35.5card')
        parsed = self.parse('2026.9.8 DT\n随手记汇总：\nTest Product*2oops Test Product*1')
        self.assertTrue(any(i['flagged'] for i in parsed['review'] + parsed['failed']))

    def test_header_variants_and_inline_discount_keep_stock_directions(self):
        parsed = self.parse('''2026.9.9 DT
卡机汇总：Test Product*1
卖 display：Test Product*1
拆 display：
display: Test Product*1
Test Product：2
入店display：Test Product*1
出店display：Test Product*1
入娃娃机：Test Product：12*1
娃娃机出：Test Product*1
出店：Test Product*1
2026.9.9 staff使用员工折扣购入Test Product*1，EMT24，本月剩余3次''')
        items = parsed['confirmed'] + parsed['review'] + parsed['failed']
        self.assertFalse(parsed['unknown_sections'])
        self.assertEqual([(i['section'], i['qty']) for i in items],
                         [('pos', 1), ('break_display', 1), ('break_display', 2), ('stock_out', 1)])
        self.assertEqual(parsed['display_sales'], ['Test Product*1'])
        self.assertEqual(parsed['display_stock_in'], ['Test Product*1'])
        self.assertEqual(parsed['display_stock_out'], ['Test Product*1'])
        self.assertEqual(parsed['claw_stock_in'], ['Test Product:12*1'])
        self.assertEqual(parsed['claw_prizes'], ['Test Product*1'])
        self.assertEqual(len(parsed['employee_discounts']), 1)

    def test_receipt_pack_plus_loose_and_unit_quantity(self):
        for text, expected in [
            ('Test Product:12*3➕8', (3, 12, 8)),
            ('Test Product: 2', (2, 1, 0)),
            ('Test Product*2', (2, 1, 0)),
        ]:
            with self.subTest(text=text):
                item = _parse_token(text, 'stock_in')
                self.assertFalse(item['flagged'])
                self.assertEqual(item['raw_name'], 'Test Product')
                self.assertEqual((item['qty'], item['box_size'], item.get('loose_qty', 0)), expected)
        with closing(self.connect()) as con:
            con.execute('UPDATE stock SET upstairs_qty=100 WHERE product_id=?', (self.product_id,))
            con.commit()
        result = self.submit([{'product_id': self.product_id, 'section': 'stock_in',
                               'box_size': 12, 'num_boxes': 3, 'loose_qty': 8}])
        self.assertEqual(result.status_code, 200, result.get_json())
        with closing(self.connect()) as con:
            row = con.execute('SELECT upstairs_qty,instore_qty FROM stock WHERE product_id=?', (self.product_id,)).fetchone()
            self.assertEqual(tuple(row), (56, 46))

    def test_annotation_directions_round_trip_without_stock_or_sales(self):
        notes = {key: ['Test Product*2'] for key in
                 ('cash_exchanges', 'claw_stock_in', 'display_stock_in', 'display_stock_out')}
        before = self.snapshot(('stock', 'stock_transactions', 'daily_sales'))
        self.assertEqual(self.submit([], report_metadata=notes).status_code, 200)
        saved = self.client.get('/api/sales/report-metadata?date=2026-09-14&store=DT',
                                headers=self.headers('manager')).get_json()
        for key, value in notes.items():
            self.assertEqual(saved[key], value)
        self.assertEqual(self.snapshot(('stock', 'stock_transactions', 'daily_sales')), before)

    def test_reviewed_unambiguous_names_are_remembered_only_after_success(self):
        text = '2026.9.14 DT\n随手记汇总：\nTest Prodct*1'
        self.assertFalse(self.parse(text)['confirmed'])
        item = {'product_id': self.product_id, 'section': 'cash', 'qty': 1,
                'source_bucket': 'review', 'raw_name': 'Test Prodct'}
        self.assertEqual(self.submit([item], report_metadata={'cash_actual': -1}).status_code, 400)
        self.assertFalse(self.parse(text)['confirmed'])
        self.assertEqual(self.submit([item]).status_code, 200)
        self.assertEqual(len(self.parse(text)['confirmed']), 1)

    def test_conflicting_review_history_never_teaches_one_product_for_both(self):
        with closing(self.connect()) as con:
            other = con.execute("INSERT INTO products (sku,name_cn_en,jizhanming) VALUES ('OTHER','Other Product','Other Product')").lastrowid
            con.commit()
        item = {'product_id': self.product_id, 'section': 'cash', 'qty': 1,
                'source_bucket': 'review', 'raw_name': 'shared nickname'}
        self.assertEqual(self.submit([item]).status_code, 200)
        self.assertEqual(self.submit([{**item, 'product_id': other}]).status_code, 200)
        with closing(self.connect()) as con:
            self.assertIsNone(con.execute('SELECT * FROM product_aliases WHERE alias_norm=?', ('sharednickname',)).fetchone())
        self.assertFalse(self.parse('2026.9.14 DT\n随手记汇总：shared nickname*1')['confirmed'])

    def test_llm_cannot_omit_duplicate_source_items_or_change_quantities(self):
        for items in (
            [{'section': 'cash', 'name': 'Test Product', 'qty': 1}],
            [{'section': 'cash', 'name': 'Test Product', 'qty': 99}],
        ):
            with self.subTest(items=items), patch.dict(os.environ, {'ENABLE_LLM_PARSER': '1'}), patch('llm_parser.available', return_value=True), patch(
                'llm_parser.parse_report_llm', return_value={'items': items}
            ):
                parsed = self.parse('2026.9.14 DT\n随手记汇总：Test Product*1 Test Product*2', engine='llm')
            self.assertEqual(sum(i['qty'] for b in ('confirmed', 'review', 'failed') for i in parsed[b]), 3)
            self.assertEqual(parsed['parser_engine'], 'rules')

    def test_unknown_quantity_has_no_default_and_display_note_is_opened_stock(self):
        parsed = self.parse('2026.9.15 DT\n随手记汇总：\nTest Product\n出店：\nTest Product*1（display）\n入店：Test Product*1 (display)')
        items = parsed['confirmed'] + parsed['review'] + parsed['failed']
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]['flagged'])
        self.assertEqual(items[0]['qty'], 0)
        self.assertEqual(parsed['display_stock_out'], ['Test Product*1(display)'])
        self.assertEqual(parsed['display_stock_in'], ['Test Product*1 (display)'])

    def test_unclosed_note_cannot_hide_another_product_without_review(self):
        parsed = self.parse('2026.9.14 DT\n随手记汇总：Test Product*1 (note Test Product*2')
        self.assertFalse(parsed['confirmed'])
        rows = parsed['review'] + parsed['failed']
        self.assertTrue(rows[0]['flagged'])
        self.assertIn('Test Product*2', rows[0]['note'])

    def test_nested_notes_keep_following_items_and_malformed_display_needs_review(self):
        parsed = self.parse('2026.9.14 DT\n随手记汇总：Test Product*1 (cash (30), card35) Test Product*2')
        self.assertEqual(sum(i['qty'] for i in parsed['confirmed']), 3)
        self.assertFalse(parsed['review'] or parsed['failed'])
        parsed = self.parse('2026.9.14 DT\n出店：Test Product*1 (display Test Product*2')
        self.assertFalse(parsed['display_stock_out'])
        self.assertTrue(any(i['flagged'] for i in parsed['review'] + parsed['failed']))

    def test_mixed_discount_annotation_preserves_sales_on_both_sides(self):
        parsed = self.parse('2026.9.14 DT\n随手记汇总：Test Product*2, staff使用员工折扣购买Test Product*1, EMT24, 本月剩余3次, Test Product*3')
        self.assertEqual(sum(i['qty'] for i in parsed['confirmed']), 5)
        self.assertFalse(parsed['review'] or parsed['failed'])
        self.assertEqual(len(parsed['employee_discounts']), 1)
        self.assertIn('EMT24', parsed['employee_discounts'][0])

    def test_existing_manual_alias_conflict_blocks_future_automatic_match(self):
        with closing(self.connect()) as con:
            other = con.execute("INSERT INTO products (sku,name_cn_en,jizhanming) VALUES ('OTHER','Other Product','Other Product')").lastrowid
            con.execute('INSERT INTO product_aliases (product_id,alias,alias_norm) VALUES (?,?,?)',
                        (self.product_id, 'shared nickname', 'sharednickname'))
            con.commit()
        item = {'product_id': other, 'section': 'cash', 'qty': 1,
                'source_bucket': 'review', 'raw_name': 'shared nickname'}
        self.assertEqual(self.submit([item]).status_code, 200)
        self.assertFalse(self.parse('2026.9.14 DT\n随手记汇总：shared nickname*1')['confirmed'])

        batch = self.client.post('/api/products/match', headers=self.headers(),
                                 json={'queries': ['shared nickname']}).get_json()
        self.assertNotEqual(batch['results'][0]['status'], 'matched')
        single = self.client.get('/api/products/by_jizhanming?name=shared%20nickname',
                                 headers=self.headers()).get_json()
        self.assertEqual(single, [])

    def test_llm_cannot_change_literal_sku_suffix(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO products (sku,name_cn_en,jizhanming) VALUES ('SKU-1S','Alpha 1','Alpha 1')")
            con.execute("INSERT INTO products (sku,name_cn_en,jizhanming) VALUES ('SKU-1','Beta 1','Beta 1')")
            con.commit()
        with patch.dict(os.environ, {'ENABLE_LLM_PARSER': '1'}), patch('llm_parser.available', return_value=True), patch(
            'llm_parser.parse_report_llm', return_value={'items': [{'section': 'cash', 'name': 'SKU-1', 'qty': 1}]}
        ):
            parsed = self.parse('2026.9.14 DT\n随手记汇总：SKU-1S*1', engine='llm')
        self.assertEqual(parsed['parser_engine'], 'rules')
        self.assertEqual(parsed['confirmed'][0]['product']['sku'], 'SKU-1S')

    def test_unconsumed_item_syntax_blocks_report_even_if_quantity_is_edited(self):
        for line in ('Test Product*2oops Test Product*1', 'Test Product*1 (note Test Product*2'):
            with self.subTest(line=line):
                parsed = self.parse('2026.9.14 DT\n随手记汇总：' + line)
                self.assertTrue(parsed['metadata_errors'])
