"""Human-reviewed report choices: isolated database, no providers or LLM."""
from contextlib import closing

from support import IsolatedApiCase


class ReportMatchChoiceTests(IsolatedApiCase):
    def parse(self, line, header='随手记汇总：'):
        response = self.client.post('/api/sales/parse_report', headers=self.headers(),
                                    json={'text': '2026.9.14 DT\n' + header + '\n' + line,
                                          'store_code': 'DT', 'engine': 'rules'})
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def submit(self, items, **extra):
        return self.client.post('/api/sales/submit_daily_report', headers=self.headers(),
                                json={'date': '2026-09-14', 'store_code': 'DT',
                                      'classification': 'summary_only', 'items': items, **extra})

    def choose(self, parsed, product_id=None):
        # Mirror DailyReportEntry's existing payload: parsed note becomes notes.
        rows = [(bucket, row) for bucket in ('review', 'failed') for row in parsed[bucket]]
        self.assertEqual(len(rows), 1)
        bucket, row = rows[0]
        return {'raw_name': row['raw_name'], 'notes': row['note'], 'section': row['section'],
                'qty_cash' if row['section'] == 'cash' else 'qty_pos': row['qty'],
                'source_bucket': bucket, 'product_id': product_id or self.product_id}

    def add_product(self, name='Other Product'):
        with closing(self.connect()) as con:
            pid = con.execute("INSERT INTO products (sku,name_cn_en,jizhanming) VALUES ('OTHER',?,?)",
                              (name, name)).lastrowid
            con.commit()
        return pid

    def assert_chosen(self, line, pid=None, header='随手记汇总：'):
        parsed = self.parse(line, header)
        self.assertFalse(parsed['review'] or parsed['failed'])
        self.assertEqual([r['product']['id'] for r in parsed['confirmed']], [pid or self.product_id])
        return parsed['confirmed'][0]

    def test_qualified_repeat_overrides_heuristic_without_teaching_bare_name(self):
        line = 'Test Product*1 (秘密)'
        first = self.parse(line)
        self.assertFalse(first['confirmed'])
        self.assertFalse(self.parse(line)['confirmed'])  # Preview does not learn.
        self.assertEqual(self.submit([self.choose(first)]).status_code, 200)
        self.assert_chosen(line)
        self.assertEqual(self.assert_chosen('Test Product*2 (秘密)', header='卡机汇总：')['qty'], 2)
        for changed in ('Test Product*1 (公开)', 'Test Product*1 (秘密 large)', 'Test Product*1'):
            with self.subTest(changed=changed):
                self.assertFalse(self.parse(changed)['confirmed'])
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM product_aliases').fetchone()[0], 0)

    def test_notes_and_explicit_variant_conflicts_are_exact_review_keys(self):
        for line in ('Test Prodct*1 (cash30+card9)', 'Test Product plush S3 400%*1',
                     'Test Product figure S3 400%*1 (Molly)'):
            with self.subTest(line=line):
                first = self.parse(line)
                self.assertFalse(first['confirmed'])
                self.assertEqual(self.submit([self.choose(first)]).status_code, 200)
                self.assert_chosen(line)
        for changed in ('Test Prodct*1 (cash31+card9)', 'Test Product plush S4 400%*1',
                        'Test Product figure S3 100%*1 (Molly)'):
            self.assertFalse(self.parse(changed)['confirmed'])

    def test_duplicate_canonical_names_can_remember_a_specific_human_choice(self):
        other = self.add_product('Test Product')
        first = self.parse('Test Product*1')
        self.assertFalse(first['confirmed'])
        self.assertEqual(self.submit([self.choose(first, other)]).status_code, 200)
        self.assert_chosen('Test Product*1', other)

    def test_exact_key_keeps_meaningful_letters_and_normalizes_case_width_spaces(self):
        first = self.parse('RememberS*1')
        self.assertEqual(self.submit([self.choose(first)]).status_code, 200)
        self.assert_chosen('ＲＥＭＥＭＢＥＲＳ*1')
        self.assertFalse(self.parse('Remember*1')['confirmed'])
        spaced = self.choose(self.parse('Human   label*1 (Molly   large)'))
        self.assertEqual(self.submit([spaced]).status_code, 200)
        self.assert_chosen('HUMAN label*1 (MOLLY large)')

    def test_failed_save_and_late_rollback_cannot_learn(self):
        line = 'Test Product*1 (secret)'
        item = self.choose(self.parse(line))
        self.assertEqual(self.submit([item], report_metadata={'cash_actual': -1}).status_code, 400)
        self.assertFalse(self.parse(line)['confirmed'])
        with closing(self.connect()) as con:
            con.execute("""CREATE TRIGGER fail_report_metadata BEFORE INSERT ON daily_report_metadata
                           BEGIN SELECT RAISE(ABORT, 'injected late failure'); END""")
            con.commit()
        tables = ('daily_sales', 'match_corrections', 'product_aliases', 'report_match_choices')
        before = self.snapshot(tables)
        self.assertEqual(self.submit([item], report_metadata={}).status_code, 500)
        self.assertEqual(self.snapshot(tables), before)
        self.assertFalse(self.parse(line)['confirmed'])
        with closing(self.connect()) as con:
            con.execute('DROP TRIGGER fail_report_metadata')
            con.commit()
        self.assertEqual(self.submit([item]).status_code, 200)
        self.assert_chosen(line)

    def test_conflicting_same_submission_or_later_review_never_teaches_last_wins(self):
        other = self.add_product()
        line = 'Shared Choice*1 (special)'
        item = self.choose(self.parse(line))
        self.assertEqual(self.submit([item, {**item, 'product_id': other}]).status_code, 200)
        self.assertFalse(self.parse(line)['confirmed'])
        self.assertEqual(self.submit([item]).status_code, 200)
        self.assertFalse(self.parse(line)['confirmed'])
        line2 = 'Second Choice*1 (special)'
        item2 = self.choose(self.parse(line2))
        self.assertEqual(self.submit([item2]).status_code, 200)
        self.assert_chosen(line2)
        self.assertEqual(self.submit([{**item2, 'product_id': other}]).status_code, 200)
        self.assertFalse(self.parse(line2)['confirmed'])

    def test_upgrade_history_conflict_without_alias_blocks_new_bare_choice(self):
        from matcher import normalize, clean_name
        other = self.add_product()
        with closing(self.connect()) as con:
            for raw, pid in (('Legacy Label', self.product_id), ('ＬＥＧＡＣＹ  label', other)):
                con.execute('INSERT INTO match_corrections(raw_name,norm_name,product_id) VALUES (?,?,?)',
                            (raw, normalize(clean_name(raw)), pid))
            con.commit()
            self.assertEqual(con.execute('SELECT COUNT(*) FROM product_aliases').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM report_match_choices').fetchone()[0], 0)
        line = 'Legacy Label*1'
        for _ in range(2):
            self.assertEqual(self.submit([self.choose(self.parse(line))]).status_code, 200)
            self.assertFalse(self.parse(line)['confirmed'])
        # Legacy records cannot establish what a qualified note used to mean.
        qualified = 'Legacy Label*1 (secret)'
        self.assertEqual(self.submit([self.choose(self.parse(qualified))]).status_code, 200)
        self.assert_chosen(qualified)

    def test_upgrade_history_does_not_merge_literal_variants_or_known_notes(self):
        from matcher import normalize, clean_name
        other = self.add_product()
        with closing(self.connect()) as con:
            for raw, pid in (('Remember', self.product_id), ('RememberS', other)):
                con.execute('INSERT INTO match_corrections(raw_name,norm_name,product_id) VALUES (?,?,?)',
                            (raw, normalize(clean_name(raw)), pid))
            # Newer qualified corrections still store bare raw_name, but include notes in norm_name.
            con.execute('INSERT INTO match_corrections(raw_name,norm_name,product_id) VALUES (?,?,?)',
                        ('RememberS', normalize('RememberS (secret)'), self.product_id))
            con.commit()
        line = 'RememberS*1'
        self.assertEqual(self.submit([self.choose(self.parse(line), other)]).status_code, 200)
        self.assert_chosen(line, other)

    def test_remembered_choice_does_not_bypass_quantity_section_or_stock_review(self):
        line = 'Variant nickname*1 (secret)'
        self.assertEqual(self.submit([self.choose(self.parse(line))]).status_code, 200)
        self.assert_chosen(line)
        for malformed in ('Variant nickname*0 (secret)', 'Variant nickname*oops (secret)',
                          'Variant nickname (secret)'):
            parsed = self.parse(malformed)
            self.assertFalse(parsed['confirmed'])
            self.assertTrue(any(r['flagged'] for r in parsed['review'] + parsed['failed']))
        unknown = self.parse(line, '待确认：')
        self.assertFalse(unknown['confirmed'])
        self.assertTrue(unknown['failed'][0]['unknown_header'])
        stock = self.parse(line, '入店：')
        self.assertFalse(stock['confirmed'])
        self.assertEqual(stock['review'][0]['section'], 'stock_in')

    def test_automatic_rows_do_not_create_human_review_memory(self):
        line = 'Unreviewed nickname*1 (special)'
        item = self.choose(self.parse(line))
        self.assertEqual(self.submit([{**item, 'source_bucket': 'confirmed'}]).status_code, 200)
        self.assertFalse(self.parse(line)['confirmed'])

    def test_identity_changes_invalidate_choice_but_price_and_jzm_changes_do_not(self):
        line = 'Human label*1 (Molly)'
        item = self.choose(self.parse(line))
        self.assertEqual(self.submit([item]).status_code, 200)
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET price=99,jizhanming='Renamed shorthand' WHERE id=?", (self.product_id,))
            con.commit()
        self.assert_chosen(line)
        for field, value in (('name_cn_en','Replacement'), ('sku','NEW-SKU'), ('product_type','plush'),
                             ('design_name','Hidden Molly'), ('stock_form','confirmed_design'),
                             ('stock_unit','piece'), ('edition_size','400%'),
                             ('ip_series','New series'), ('brand','New brand'),
                             ('hidden','Secret'), ('style_notes','Large'),
                             ('boxes_per_dan',12), ('identity_status','verified')):
            with self.subTest(field=field):
                with closing(self.connect()) as con:
                    con.execute(f'UPDATE products SET {field}=? WHERE id=?', (value,self.product_id))
                    con.commit()
                self.assertFalse(self.parse(line)['confirmed'])
                self.assertEqual(self.submit([item]).status_code, 200)  # Explicit fresh review renews it.
                self.assert_chosen(line)

    def test_stale_choice_cannot_fall_back_to_an_exact_catalog_name(self):
        other = self.add_product('Test Product')
        item = self.choose(self.parse('Test Product*1'), other)
        self.assertEqual(self.submit([item]).status_code, 200)
        self.assert_chosen('Test Product*1', other)
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET name_cn_en='Different',jizhanming='Different' WHERE id=?",
                        (self.product_id,))
            con.execute("UPDATE products SET sku='REPLACEMENT' WHERE id=?", (other,))
            con.commit()
        # Now only the chosen product has this exact canonical name, but approval is stale.
        self.assertFalse(self.parse('Test Product*1')['confirmed'])
        self.assertEqual(self.submit([item]).status_code, 200)
        self.assert_chosen('Test Product*1', other)

    def test_qualified_choices_do_not_dispute_safe_unqualified_legacy_aliases(self):
        other = self.add_product()
        with closing(self.connect()) as con:
            con.execute("INSERT INTO product_aliases(product_id,alias,alias_norm) VALUES (?, 'Old label','oldlabel')",
                        (self.product_id,))
            con.commit()
        self.assert_chosen('Old label*1')
        for note,pid in (('secret',other),('hidden',self.product_id)):
            line = f'Old label*1 ({note})'
            self.assertEqual(self.submit([self.choose(self.parse(line),pid)]).status_code, 200)
            self.assert_chosen(line,pid)
        # The legacy alias remains usable by other matcher entry points.
        response = self.client.get('/api/products/by_jizhanming?name=Old%20label',headers=self.headers()).get_json()
        self.assertEqual([r['id'] for r in response], [self.product_id])

    def test_section_choices_learn_only_after_success_and_reuse_exact_heading(self):
        line, header = 'Test Product*1', '网络收款：'
        first = self.parse(line, header)
        self.assertTrue(first['unknown_sections'])
        item = {**self.choose(first), 'section':'cash', 'qty_pos':0, 'qty_cash':1}
        choices = [{'header':header,'section':'cash'}]
        self.assertEqual(self.submit([item], section_choices=choices, report_metadata={'cash_actual':-1}).status_code,400)
        self.assertTrue(self.parse(line,header)['unknown_sections'])
        self.assertEqual(self.submit([item],section_choices=choices).status_code,200)
        self.assertEqual(self.submit([item],section_choices=choices).status_code,200)
        self.assert_chosen(line,header=header)
        self.assertEqual(self.parse(line,header)['confirmed'][0]['section'],'cash')
        self.assertTrue(self.parse(line,'网络收款其他：')['unknown_sections'])
        self.assertEqual(self.submit([],section_choices=[{'header':'无需入账：','section':'skip'}]).status_code,200)
        skipped = self.parse(line,'无需入账：')
        self.assertFalse(skipped['confirmed'] or skipped['review'] or skipped['failed'] or skipped['unknown_sections'])

    def test_section_choice_conflicts_and_late_failure_roll_back_everything(self):
        choices = [{'header':'渠道：','section':'cash'}]
        self.assertEqual(self.submit([],section_choices=choices).status_code,200)
        item = self.choose(self.parse('Unlearned Choice*1 (secret)'))
        tables = ('section_aliases','daily_sales','daily_report_metadata','report_match_choices')
        before = self.snapshot(tables)
        for conflict in ([{'header':'渠道：','section':'pos'}],
                         [{'header':'新渠道：','section':'cash'},{'header':' 新渠道: ','section':'pos'}]):
            response = self.submit([item],section_choices=conflict,report_metadata={'cash_actual':1})
            self.assertEqual(response.status_code,409)
            self.assertEqual(self.snapshot(tables),before)
        with closing(self.connect()) as con:
            con.execute("""CREATE TRIGGER fail_sections BEFORE INSERT ON daily_report_metadata
                           BEGIN SELECT RAISE(ABORT, 'injected late failure'); END""")
            con.commit()
        self.assertEqual(self.submit([item],section_choices=[{'header':'新渠道：','section':'cash'}],report_metadata={}).status_code,500)
        self.assertEqual(self.snapshot(tables),before)

    def test_section_choice_validation_never_overrides_builtin_headers(self):
        for choices in (None, {}, [{'header':'','section':'cash'}], [{'header':':','section':'cash'}],
                        [{'header':'新渠道：','section':'claw'}], [{'header':'卡机汇总：','section':'cash'}],
                        [{'header':'入店：','section':'cash'}], [{'header':'商品*1','section':'cash'}],
                        [{'header':'新渠道：\n商品','section':'cash'}]):
            with self.subTest(choices=choices):
                self.assertEqual(self.submit([],section_choices=choices).status_code,400)
        self.assertEqual(self.submit([]).status_code,200)
