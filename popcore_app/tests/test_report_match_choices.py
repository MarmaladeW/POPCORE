"""Human-reviewed report choices (名称记忆 Name picks): isolated database, no providers or LLM.

Owner rules (2026-11-03): a pick is remembered for every section, stock lines included; it confirms a
line by itself only for the same name and note, store and section, with the product unchanged, and asks
again with the pick chosen anywhere else; a different product replaces the old pick."""
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from support import IsolatedApiCase, db


class ReportMatchChoiceTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        # Daily report writes need access to the store (store_access.py): the test manager gets DT.
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)",
                        (self.store_id,))
            con.commit()

    def parse(self, line, header='随手记汇总：', store='DT'):
        response = self.client.post('/api/sales/parse_report', headers=self.headers('manager'),
                                    json={'text': f'2026.9.14 {store}\n' + header + '\n' + line,
                                          'store_code': store, 'engine': 'rules'})
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def submit(self, items, store='DT', **extra):
        return self.client.post('/api/sales/submit_daily_report', headers=self.headers('manager'),
                                json={'date': '2026-09-14', 'store_code': store,
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
        # 卡机 is another section: asked again there, with the remembered product already picked.
        again = self.parse('Test Product*2 (秘密)', '卡机汇总：')
        self.assertFalse(again['confirmed'])
        self.assertEqual((again['review'][0]['product']['id'], again['review'][0]['qty'], again['review'][0]['pick']['state']),
                         (self.product_id, 2, 'confirm'))
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

    def test_one_report_picking_two_products_is_disputed_and_a_later_pick_replaces_it(self):
        other = self.add_product()
        line = 'Shared Choice*1 (special)'
        item = self.choose(self.parse(line))
        self.assertEqual(self.submit([item, {**item, 'product_id': other}]).status_code, 200)
        disputed = self.parse(line)
        self.assertFalse(disputed['confirmed'])
        self.assertEqual(disputed['review'][0]['pick']['state'], 'disputed')
        self.assertEqual({row['id'] for row in disputed['review'][0]['candidates'][:2]}, {self.product_id, other})
        # A later report picks one; then the manager changes it again. Each change replaces the pick.
        self.assertEqual(self.submit([item]).status_code, 200)
        self.assert_chosen(line)
        self.assertEqual(self.submit([{**item, 'product_id': other}]).status_code, 200)
        self.assert_chosen(line, other)
        with closing(self.connect()) as con:
            self.assertEqual([tuple(row) for row in con.execute('SELECT product_id FROM report_match_choices')], [(other,)])

    def test_upgrade_history_conflict_vetoes_only_a_choice_made_before_name_picks(self):
        from matcher import normalize, clean_name, report_identity_signature
        other = self.add_product()
        with closing(self.connect()) as con:
            for raw, pid in (('Legacy Label', self.product_id), ('ＬＥＧＡＣＹ  label', other)):
                con.execute('INSERT INTO match_corrections(raw_name,norm_name,product_id) VALUES (?,?,?)',
                            (raw, normalize(clean_name(raw)), pid))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM product_aliases').fetchone()[0], 0)
            # A choice saved before name picks (no time), confirmed for DT 随手记 by the upgrade.
            product = dict(con.execute('SELECT * FROM products WHERE id=?', (self.product_id,)).fetchone())
            con.execute("""INSERT INTO report_match_choices(name_key,note_key,product_id,identity_signature)
                           VALUES ('legacy label','',?,?)""", (self.product_id, report_identity_signature(product)))
            con.execute("""INSERT INTO report_match_situations(name_key,note_key,product_id,store_id,section)
                           VALUES ('legacy label','',?,?,'cash')""", (self.product_id, self.store_id))
            con.commit()
        line = 'Legacy Label*1'
        vetoed = self.parse(line)
        self.assertFalse(vetoed['confirmed'])
        self.assertEqual(vetoed['review'][0]['pick']['state'], 'disputed')
        # The manager's answer since name picks is the pick: the history no longer outvotes it.
        self.assertEqual(self.submit([self.choose(vetoed)]).status_code, 200)
        self.assert_chosen(line)
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

    # ── 名称记忆 Name picks: situations, stock lines and the page ───────────────

    def day(self, store='DT', date='2026-09-14', role='manager'):
        return self.client.get('/api/sales/name-picks/day', headers=self.headers(role),
                               query_string={'store_code': store, 'date': date})

    def pick(self, role='manager', **body):
        return self.client.post('/api/sales/name-picks', headers=self.headers(role), json=body)

    def test_a_pick_confirms_only_in_the_store_and_section_it_was_confirmed_in(self):
        with closing(self.connect()) as con:
            mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)", (mk,))
            con.commit()
        line = 'Nickname*1'
        self.assertEqual(self.submit([self.choose(self.parse(line))]).status_code, 200)
        self.assert_chosen(line)
        for header, store, section in (('卡机汇总：', 'DT', 'pos'), ('随手记汇总：', 'MK', 'cash')):
            with self.subTest(store=store, section=section):
                parsed = self.parse(line, header, store)
                self.assertFalse(parsed['confirmed'])
                row = parsed['review'][0]
                self.assertEqual((row['product']['id'], row['score'], row['pick']['state']), (self.product_id, 100, 'confirm'))
                self.assertIn({'store_code': 'DT', 'section': 'cash', 'display': False}, row['pick']['places'])
                # One tap there: accepted once, it confirms there too.
                self.assertEqual(self.submit([self.choose(parsed)], store).status_code, 200)
                self.assert_chosen(line, header=header) if store == 'DT' else self.assertEqual(
                    [r['product']['id'] for r in self.parse(line, header, store)['confirmed']], [self.product_id])
        self.assert_chosen(line)

    def test_a_changed_pick_replaces_the_old_one_and_where_it_was_confirmed(self):
        other = self.add_product()
        line = 'Nickname*1'
        self.assertEqual(self.submit([self.choose(self.parse(line))]).status_code, 200)
        self.assertEqual(self.submit([self.choose(self.parse(line, '卡机汇总：'))]).status_code, 200)
        self.assert_chosen(line, header='卡机汇总：')
        # In the paste the manager changes a remembered line (it then goes back through review).
        row = self.assert_chosen(line)
        changed = {'raw_name': row['raw_name'], 'notes': row['note'], 'section': 'cash', 'qty_cash': 1,
                   'source_bucket': 'review', 'product_id': other}
        self.assertEqual(self.submit([changed]).status_code, 200)
        self.assert_chosen(line, other)
        again = self.parse(line, '卡机汇总：')
        self.assertFalse(again['confirmed'])  # 卡机 was confirmed for the old product
        self.assertEqual((again['review'][0]['product']['id'], again['review'][0]['pick']['state']), (other, 'confirm'))
        with closing(self.connect()) as con:
            self.assertEqual([tuple(row) for row in con.execute(
                'SELECT product_id, section FROM report_match_situations')], [(other, 'cash')])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM product_aliases').fetchone()[0], 0)

    def test_stock_line_names_are_remembered_by_their_own_section(self):
        line = 'Nickname*1'
        parsed = self.parse(line, '出店：')
        row = parsed['failed'][0]
        self.assertEqual(row['section'], 'stock_out')
        item = {'raw_name': row['raw_name'], 'notes': row['note'], 'section': 'stock_out', 'qty': 1,
                'source_bucket': 'failed', 'product_id': self.product_id}
        self.assertEqual(self.submit([item]).status_code, 200)
        remembered = self.parse(line, '出店：')
        self.assertEqual([(r['section'], r['product']['id'], r['pick']['state']) for r in remembered['confirmed']],
                         [('stock_out', self.product_id, 'remembered')])
        # The same name sold is another section: asked again, already picked.
        sold = self.parse(line)
        self.assertEqual((sold['review'][0]['product']['id'], sold['review'][0]['pick']['state']), (self.product_id, 'confirm'))

    def test_a_product_changed_since_the_pick_is_asked_again_with_it_picked(self):
        line = 'Human label*1 (Molly)'
        self.assertEqual(self.submit([self.choose(self.parse(line))]).status_code, 200)
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET name_cn_en='Replacement' WHERE id=?", (self.product_id,))
            con.commit()
        parsed = self.parse(line)
        self.assertFalse(parsed['confirmed'])
        row = parsed['review'][0]
        self.assertEqual((row['product']['id'], row['score'], row['pick']['state']), (self.product_id, 99, 'stale'))

    def test_the_page_lists_each_pasted_day_and_answers_a_name_where_it_was_written(self):
        other = self.add_product()
        parsed = self.parse('Nickname*1\nTest Product*1')
        self.assertEqual([r['raw_name'] for r in parsed['confirmed']], ['Test Product'])
        days = self.client.get('/api/sales/name-picks', headers=self.headers('manager')).get_json()['days']
        self.assertEqual([(d['store_code'], d['date'], d['counts']['open'], d['saved']) for d in days],
                         [('DT', '2026-09-14', 1, False)])
        names = self.day().get_json()['names']
        # An exact catalog name is not a name to pick; the paste's guess comes with each name.
        self.assertEqual([(n['raw_name'], n['section'], n['status'], n['pick']) for n in names],
                         [('Nickname', 'cash', 'open', None)])
        self.assertFalse(self.parse('Nickname*1')['confirmed'])  # a preview learns nothing
        key = {'name_key': names[0]['name_key'], 'note_key': names[0]['note_key']}
        place = {'store_code': 'DT', 'section': 'cash', 'display': False}
        self.assertEqual(self.pick(raw_name='Nickname', note='', product_id=self.product_id, replaces=[], **place).status_code, 200)
        self.assertEqual(self.day().get_json()['names'][0]['status'], 'remembered')
        self.assert_chosen('Nickname*1')
        # A pick changed meanwhile is never overwritten unseen.
        refused = self.pick(raw_name='Nickname', note='', product_id=other, replaces=[], **place)
        self.assertEqual((refused.status_code, refused.get_json()['code']), (409, 'pick_changed'))
        # 记住的 All picks: a change there replaces the pick and forgets where it was confirmed.
        self.assertEqual(self.pick(**key, raw_name='Nickname', note='', product_id=other, replaces=[self.product_id]).status_code, 200)
        picks = self.client.get('/api/sales/name-picks', headers=self.headers('manager')).get_json()['picks']
        self.assertEqual([(p['raw_name'], [x['id'] for x in p['products']], p['places'], p['state']) for p in picks],
                         [('Nickname', [other], [], 'ok')])
        self.assertEqual(self.day().get_json()['names'][0]['status'], 'confirm')
        # Forget: the next paste matches the name as if it had never been answered.
        forget = self.client.delete('/api/sales/name-picks', headers=self.headers('manager'), json={**key, 'replaces': [other]})
        self.assertEqual(forget.status_code, 200)
        self.assertNotIn('pick', self.parse('Nickname*1')['failed'][0])
        # Ignore: the page sets it aside; a paste still asks.
        ignore = self.client.post('/api/sales/name-picks/ignore', headers=self.headers('manager'), json={**key, 'ignored': True})
        self.assertEqual(ignore.status_code, 200)
        self.assertEqual(self.day().get_json()['names'][0]['status'], 'ignored')
        self.assertTrue(self.parse('Nickname*1')['failed'])
        # The day's latest paste replaces its names.
        self.parse('Test Product*1')
        self.assertEqual(self.day().get_json()['names'], [])

    def test_name_picks_are_for_managers_who_may_save_the_store_reports(self):
        self.parse('Nickname*1')
        self.assertEqual(self.client.get('/api/sales/name-picks', headers=self.headers('staff')).status_code, 403)
        outsider = self.client.get('/api/sales/name-picks', headers=self.headers('manager:other')).get_json()
        self.assertEqual(outsider['days'], [])
        self.assertEqual(self.day(role='manager:other').status_code, 403)
        place = {'store_code': 'DT', 'section': 'cash', 'display': False}
        self.assertEqual(self.pick('manager:other', raw_name='Nickname', product_id=self.product_id, replaces=[], **place).status_code, 403)
        self.assertEqual(self.pick('staff', raw_name='Nickname', product_id=self.product_id, replaces=[]).status_code, 403)
        for bad in ({'raw_name': ' ', 'product_id': self.product_id}, {'raw_name': 'x', 'product_id': 'one'},
                    {'raw_name': 'x', 'product_id': self.product_id, 'replaces': 'none'},
                    {'raw_name': 'x', 'product_id': self.product_id, **place, 'section': 'refund'}):
            with self.subTest(bad=bad):
                self.assertEqual(self.pick(**{'replaces': [], **bad}).status_code, 400)
        self.assertEqual(self.pick(raw_name='x', product_id=999, replaces=[]).status_code, 404)
        # An admin may save every store's reports, with or without an inventory grant.
        self.assertEqual([d['store_code'] for d in self.client.get('/api/sales/name-picks', headers=self.headers('admin')).get_json()['days']], ['DT'])
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM report_match_choices').fetchone()[0], 0)

    def test_the_upgrade_finds_where_each_earlier_pick_was_saved(self):
        from matcher import report_identity_signature
        db.DB_PATH = str(Path(self.tempdir.name) / 'upgrade.db')
        before = db._get_migrations
        with patch.object(db, '_get_migrations', side_effect=lambda: [m for m in before() if m[0] != 'report_name_picks']):
            db.migrate_db()
        with closing(self.connect()) as con:
            pid = con.execute("INSERT INTO products(sku,name_cn_en,jizhanming) VALUES ('TEST-1','Test Product','Test Product')").lastrowid
            other = con.execute("INSERT INTO products(sku,name_cn_en,jizhanming) VALUES ('OTHER','Other','Other')").lastrowid
            dt = con.execute("SELECT id FROM stores WHERE code='DT'").fetchone()[0]
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)", (dt,))
            signature = report_identity_signature(dict(con.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone()))
            con.executemany('INSERT INTO report_match_choices(name_key,note_key,product_id,identity_signature) VALUES (?,?,?,?)',
                            [('nickname', 'secret', pid, signature), ('shared', '', pid, signature), ('shared', '', other, signature)])
            con.executemany("""INSERT INTO daily_sales(product_id,date,store,qty_pos,qty_cash,qty_sold,raw_name,notes)
                               VALUES (?,?, 'DT',?,?,1,?,?)""",
                            [(pid, '2026-09-01', 0, 1, 'NickName', 'Secret'), (pid, '2026-09-02', 0, 1, 'shared', '')])
            con.commit()
        db.migrate_db()
        with closing(self.connect()) as con:
            self.assertEqual([tuple(row) for row in con.execute(
                'SELECT name_key, note_key, product_id, store_id, section, display FROM report_match_situations')],
                [('nickname', 'secret', pid, dt, 'cash', 0)])
            self.assertEqual(con.execute("SELECT raw_name, note FROM report_match_choices WHERE name_key='nickname'").fetchone()[:],
                             ('NickName', 'Secret'))
        self.assertEqual([r['product']['id'] for r in self.parse('Nickname*1 (secret)')['confirmed']], [pid])
        self.assertEqual(self.parse('Nickname*1 (secret)', '卡机汇总：')['review'][0]['pick']['state'], 'confirm')
        self.assertEqual(self.parse('shared*1')['review'][0]['pick']['state'], 'disputed')
