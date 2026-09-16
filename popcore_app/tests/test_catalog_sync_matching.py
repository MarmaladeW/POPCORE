"""Catalog identity matching and atomic Sheet confirmation (no external requests)."""
from contextlib import closing
from unittest.mock import patch

from support import IsolatedApiCase
from matcher import batch_match_jzm, match_jzm


class CatalogSyncMatchingTests(IsolatedApiCase):
    def add_product(self, sku, name, jzm='', ref=None, product_type=''):
        with closing(self.connect()) as con:
            pid = con.execute(
                'INSERT INTO products(sku,name_cn_en,jizhanming,sheet_ref,product_type) '
                'VALUES (?,?,?,?,?)', (sku, name, jzm, ref, product_type),
            ).lastrowid
            con.commit()
        return pid

    def catalog(self):
        with closing(self.connect()) as con:
            return [dict(r) for r in con.execute('SELECT * FROM products')]

    def preview(self, *rows):
        with patch('blueprints.products._fetch_sheet_rows', return_value=('ok', [
            ['ref', 'jzm', 'name'], *rows,
        ])):
            response = self.client.post('/api/products/sync-sheet', headers=self.headers('admin'))
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def confirm(self, **payload):
        return self.client.post('/api/products/sync-sheet/confirm',
                                headers=self.headers('admin'), json=payload)

    def change(self, pid=None, **overrides):
        pid = pid or self.product_id
        product = next(p for p in self.catalog() if p['id'] == pid)
        return {'product_id': pid, 'new_jizhanming': 'Renamed', 'sheet_ref': 'row-1',
                'expected_jizhanming': product['jizhanming'],
                'expected_sheet_ref': product['sheet_ref'],
                'expected_name_cn_en': product['name_cn_en'],
                'expected_product_type': product['product_type'], **overrides}

    def test_exact_full_name_beats_fuzzy_shorthand(self):
        self.add_product('FUZZY', 'Other', 'Dream Garden toy')
        exact = self.add_product('EXACT', 'Dream Garden', '')
        self.assertEqual(match_jzm('Dream Garden', self.catalog())[0],
                         (100, next(p for p in self.catalog() if p['id'] == exact)))

    def test_exact_alias_and_sku_include_blank_jzm(self):
        pid = self.add_product('SKU-123', 'Other', '')
        self.add_product('FUZZY', 'Unrelated', 'Nickname toy')
        response = self.client.post('/api/products/aliases', headers=self.headers(),
                                    json={'product_id': pid, 'alias': 'Nickname'})
        self.assertEqual(response.status_code, 200)
        for query in ('Nickname', 'SKU-123'):
            result = self.client.post('/api/products/match', headers=self.headers(),
                                      json={'queries': [query]}).get_json()['results'][0]
            self.assertEqual(result['status'], 'matched')
            self.assertEqual(result['candidates'][0]['id'], pid)
            lookup = self.client.get('/api/products/by_jizhanming', headers=self.headers(),
                                     query_string={'name': query}).get_json()
            self.assertEqual(lookup[0]['id'], pid)

    def test_tied_exact_hits_survive_limit_and_require_review(self):
        other = self.add_product('TWIN', 'Test Product', 'Test Product')
        hits = match_jzm('Test Product', self.catalog(), limit=1)
        self.assertEqual({p['id'] for score, p in hits if score == 100},
                         {self.product_id, other})
        result = batch_match_jzm(['Test Product'], self.catalog())[0]
        self.assertEqual(result['status'], 'fuzzy')
        preview = self.preview(['row-1', 'New name', 'Test Product'])
        self.assertEqual(preview['changed'], [])
        review = preview['review'][0]
        self.assertFalse(review['prechecked'])
        self.assertIsNone(review['product_id'])
        self.assertEqual({p['id'] for p in review['candidates']}, {self.product_id, other})
        candidate = next(p for p in review['candidates'] if p['id'] == other)
        response = self.confirm(review_accepted=[self.change(
            other, new_jizhanming=review['new_jizhanming'],
            expected_jizhanming=candidate['jizhanming'],
            expected_sheet_ref=candidate['sheet_ref'],
        )])
        self.assertEqual(response.status_code, 200)

    def test_alias_name_collision_stays_ambiguous(self):
        other = self.add_product('ALIAS', 'Other')
        hits = match_jzm('Test Product', self.catalog(), {'testproduct': other})
        self.assertEqual(len([s for s, _ in hits if s == 100]), 2)

    def test_conflicting_type_on_exact_shorthand_requires_review(self):
        self.add_product('WRONG', 'Dream figure', 'Dream plush', product_type='figure')
        result = batch_match_jzm(['Dream plush'], self.catalog())[0]
        self.assertNotEqual(result['status'], 'matched')

    def test_variant_cues_cannot_be_erased_by_exact_alias(self):
        pairs = [('Dream plush', 'Dream figure'), ('Dream 毛绒', 'Dream 手办'),
                 ('Dream 搪胶', 'Dream 毛绒'), ('Dream keychain', 'Dream figure'),
                 ('Dream 挂件', 'Dream figure'), ('Dream 二代', 'Dream 三代'),
                 ('Dream 10cm', 'Dream 20cm'), ('Dream secret', 'Dream')]
        for query, name in pairs:
            with self.subTest(query=query):
                product = {'id': 99, 'sku': 'X', 'name_cn_en': name, 'jizhanming': '', 'product_type': 'ordinary'}
                from matcher import normalize
                hits = match_jzm(query, [product], {normalize(query): 99}, threshold=0)
                self.assertLess(hits[0][0], 100)

    def test_stable_ref_does_not_override_destination_identity_collision(self):
        pid = self.add_product('PLUSH', 'Dream plush', 'Dream', 'stable')
        self.add_product('FIGURE', 'Dream figure', '')
        preview = self.preview(['stable', 'Dream figure', 'Dream figure'])
        self.assertEqual(preview['changed'], [])
        self.assertEqual(preview['review'], [])
        self.assertEqual(preview['conflicts'][0]['product_id'], pid)
        self.assertEqual(preview['conflicts'][0]['rows'][0]['match_via'], 'ref')
        self.assertEqual(preview['conflicts'][0]['reason'], 'destination_collision')

    def test_ambiguous_unchanged_shorthand_still_requires_review(self):
        self.add_product('TWIN', 'Test Product', 'Test Product')
        preview = self.preview(['row-1', 'Test Product', 'Test Product'])
        self.assertEqual(len(preview['review']), 1)
        self.assertEqual(preview['ref_learns'], [])

    def test_repeated_create_is_noop(self):
        payload = {'name_cn_en': 'New Catalog Item', 'jizhanming': 'New item', 'sheet_ref': 'new-1'}
        first = self.confirm(create_products=[payload, payload])
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()['created'], 1)
        before = self.snapshot(['products'])
        second = self.confirm(create_products=[payload])
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()['created'], 0)
        self.assertEqual(self.snapshot(['products']), before)

    def test_ref_collision_rolls_back_entire_request(self):
        other = self.add_product('OWNER', 'Other', 'Other', 'taken')
        before = self.snapshot(['products', 'product_aliases', 'app_settings'])
        result = self.confirm(changes=[self.change()], ref_learns=[
            self.change(other, sheet_ref='row-1'),
        ], create_products=[{'name': 'Never created', 'ref': 'new'}])
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.snapshot(list(before)), before)

    def test_create_cannot_claim_existing_ref_with_different_identity(self):
        self.add_product('OWNER', 'Other', 'Other', 'taken')
        before = self.snapshot(['products', 'product_aliases', 'app_settings'])
        result = self.confirm(changes=[self.change()], create_products=[
            {'name': 'Different', 'ref': 'taken'},
        ])
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.snapshot(list(before)), before)

    def test_create_exact_name_collision_rejected(self):
        result = self.confirm(create_products=[{'name': 'Test Product', 'ref': 'new'}])
        self.assertEqual(result.status_code, 409)
        self.assertEqual(len(self.catalog()), 1)

    def test_changed_preview_snapshot_rejected(self):
        change = self.change()
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET jizhanming=? WHERE id=?', ('Changed elsewhere', self.product_id))
            con.commit()
        before = self.snapshot(['products', 'product_aliases', 'app_settings'])
        self.assertEqual(self.confirm(changes=[change]).status_code, 409)
        self.assertEqual(self.snapshot(list(before)), before)

    def test_missing_snapshot_and_malformed_operations_rejected(self):
        for payload in ({'changes': [None]}, {'changes': 'bad'},
                        {'changes': [{'product_id': self.product_id, 'new_jizhanming': 'New'}]},
                        {'create_products': [{'name': 123}]}):
            with self.subTest(payload=payload):
                self.assertEqual(self.confirm(**payload).status_code, 400)

    def test_alias_collision_does_not_reassign(self):
        other = self.add_product('OTHER', 'Other')
        for pid, status in ((self.product_id, 200), (self.product_id, 200), (other, 409)):
            response = self.client.post('/api/products/aliases', headers=self.headers(),
                                        json={'product_id': pid, 'alias': 'My Alias'})
            self.assertEqual(response.status_code, status)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT product_id FROM product_aliases').fetchone()[0],
                             self.product_id)

    def test_legacy_lookup_does_not_auto_confirm_single_fuzzy_result(self):
        result = self.client.get('/api/products/by_jizhanming', headers=self.headers(),
                                 query_string={'name': 'Test Products extra'}).get_json()
        self.assertEqual(result, [])

    def test_ref_already_owned_by_other_product_rolls_back_rename(self):
        self.add_product('OWNER', 'Owner', 'Owner', 'taken')
        before = self.snapshot(['products', 'product_aliases', 'app_settings'])
        response = self.confirm(changes=[self.change(sheet_ref='taken')])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.snapshot(list(before)), before)

    def test_valid_unique_preview_roundtrip_preserves_old_alias(self):
        preview = self.preview(['row-1', 'Renamed', 'Test Product'])
        self.assertEqual(len(preview['changed']), 1)
        response = self.confirm(changes=preview['changed'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['updated'], 1)
        with closing(self.connect()) as con:
            row = con.execute('SELECT product_id FROM product_aliases WHERE alias_norm=?',
                              ('testproduct',)).fetchone()
        self.assertEqual(row['product_id'], self.product_id)

    def test_ref_learn_snapshot_rejects_changed_ref(self):
        preview = self.preview(['row-1', 'Test Product', 'Test Product'])
        self.assertEqual(len(preview['ref_learns']), 1)
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET sheet_ref=? WHERE id=?', ('another', self.product_id))
            con.commit()
        self.assertEqual(self.confirm(ref_learns=preview['ref_learns']).status_code, 409)

    def test_late_invalid_create_rolls_back_earlier_rename(self):
        before = self.snapshot(['products', 'product_aliases', 'app_settings'])
        self.assertEqual(self.confirm(changes=[self.change()], create_products=[{'name': []}]).status_code, 400)
        self.assertEqual(self.snapshot(list(before)), before)

    def test_old_alias_collision_rolls_back_rename_and_ref(self):
        other = self.add_product('OTHER', 'Other')
        self.client.post('/api/products/aliases', headers=self.headers(),
                         json={'product_id': other, 'alias': 'Test Product'})
        before = self.snapshot(['products', 'product_aliases', 'app_settings'])
        self.assertEqual(self.confirm(changes=[self.change()]).status_code, 409)
        self.assertEqual(self.snapshot(list(before)), before)

    def test_name_and_type_edits_invalidate_preview(self):
        for field in ('name_cn_en', 'product_type'):
            with self.subTest(field=field):
                change = self.change()
                with closing(self.connect()) as con:
                    con.execute(f'UPDATE products SET {field}=? WHERE id=?',
                                ('Edited variant', self.product_id))
                    con.commit()
                before = self.snapshot(['products', 'product_aliases', 'app_settings'])
                self.assertEqual(self.confirm(changes=[change]).status_code, 409)
                self.assertEqual(self.snapshot(list(before)), before)

    def test_entire_confirm_payload_can_replay_without_duplicate_writes(self):
        other = self.add_product('LEARN', 'Learn reference', 'Learn reference')
        body = {
            'changes': [self.change()],
            'ref_learns': [self.change(other, sheet_ref='learn')],
            'create_products': [{'name': 'Created once', 'jizhanming': 'New unique', 'ref': 'created'}],
        }
        first = self.confirm(**body)
        self.assertEqual(first.status_code, 200)
        before = self.snapshot(['products', 'product_aliases'])
        second = self.confirm(**body)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json(), {'ok': True, 'updated': 0, 'created': 0, 'refs_learned': 0})
        self.assertEqual(self.snapshot(list(before)), before)

    def test_stable_ref_type_conflict_allows_deliberate_review_override(self):
        pid = self.add_product('PLUSH', 'Dream plush', 'Dream', 'stable')
        preview = self.preview(['stable', 'Dream figure', 'Dream figure'])
        self.assertEqual(preview['review'][0]['product_id'], pid)
        self.assertEqual(self.confirm(review_accepted=preview['review']).status_code, 200)

    def test_secret_and_explicit_packaging_conflicts_require_review(self):
        pairs = [('Kubo呼吸（秘密）', 'Kubo呼吸'),
                 ('Dream full set', 'Dream single box'),
                 ('Dream整盒', 'Dream单盒'), ('Dream端', 'Dream单盒'),
                 ('Dream单盒', 'Dream整盒'), ('Dream单盒', 'Dream端')]
        from matcher import normalize
        for query, name in pairs:
            with self.subTest(query=query):
                product = {'id': 99, 'sku': 'X', 'name_cn_en': name, 'jizhanming': '', 'product_type': 'ordinary'}
                hits = match_jzm(query, [product], {normalize(query): 99}, threshold=0)
                self.assertLess(hits[0][0], 100)
        self.add_product('KUBO', 'Kubo呼吸', 'Kubo呼吸', 'kubo')
        preview = self.preview(['kubo', 'Kubo呼吸（秘密）', 'Kubo呼吸（秘密）'])
        self.assertEqual(preview['changed'], [])
        self.assertEqual(preview['review'][0]['reason'], 'type_conflict')

    def test_sku_exact_matching_preserves_trailing_s(self):
        for sku, wrong in (('PART-S', 'PART'), ('PART', 'PART-S'), ('PARTS', 'PART-S')):
            product = {'id': 99, 'sku': sku, 'name_cn_en': 'Unrelated plush', 'jizhanming': ''}
            with self.subTest(sku=sku):
                for nonliteral in {wrong, 'PARTS', 'PART S'} - {sku}:
                    self.assertFalse(any(score == 100 for score, _ in match_jzm(nonliteral, [product])))
                for literal in (sku, '  ' + sku.lower() + '  ', sku.translate(str.maketrans('PARTS-', 'ＰＡＲＴＳ－'))):
                    self.assertEqual(match_jzm(literal, [product])[0][0], 100)

    def test_destination_identity_collision_blocks_preview_and_atomic_confirmation(self):
        from matcher import normalize
        safe = self.add_product('SAFE', 'Safe unrelated product', 'Safe old')
        for field in ('alias', 'name_cn_en', 'jizhanming', 'sku'):
            with self.subTest(field=field):
                value = 'Taken ' + field
                owner = self.add_product('OWNER-' + field, 'Other ' + field)
                with closing(self.connect()) as con:
                    if field == 'alias':
                        con.execute('INSERT INTO product_aliases(product_id,alias,alias_norm) VALUES (?,?,?)',
                                    (owner, value, normalize(value)))
                    else:
                        con.execute(f'UPDATE products SET {field}=? WHERE id=?', (value, owner))
                    con.commit()
                preview = self.preview(['row-1', value, 'Test Product'])
                self.assertEqual(preview['changed'], [])
                self.assertEqual(preview['review'], [])
                self.assertEqual(preview['conflicts'][0]['reason'], 'destination_collision')
                before = self.snapshot(['products', 'product_aliases', 'app_settings'])
                response = self.confirm(
                    changes=[self.change(safe, new_jizhanming='Safe renamed', sheet_ref='safe-ref')],
                    review_accepted=[self.change(new_jizhanming=value)],
                )
                self.assertEqual(response.status_code, 409)
                self.assertEqual(self.snapshot(list(before)), before)

    def test_destination_collision_added_after_preview_is_rejected(self):
        change = self.preview(['row-1', 'New shorthand', 'Test Product'])['changed'][0]
        owner = self.add_product('OWNER', 'Other')
        self.client.post('/api/products/aliases', headers=self.headers(),
                         json={'product_id': owner, 'alias': 'New shorthand'})
        before = self.snapshot(['products', 'product_aliases', 'app_settings'])
        self.assertEqual(self.confirm(changes=[change]).status_code, 409)
        self.assertEqual(self.snapshot(list(before)), before)

    def test_rename_to_own_alias_preserves_old_alias_and_replays(self):
        self.client.post('/api/products/aliases', headers=self.headers(),
                         json={'product_id': self.product_id, 'alias': 'My nickname'})
        change = self.preview(['row-1', 'My nickname', 'Test Product'])['changed'][0]
        self.assertEqual(self.confirm(changes=[change]).status_code, 200)
        before = self.snapshot(['products', 'product_aliases'])
        self.assertEqual(self.confirm(changes=[change]).status_code, 200)
        self.assertEqual(self.snapshot(list(before)), before)
        with closing(self.connect()) as con:
            self.assertEqual({r['alias_norm'] for r in con.execute('SELECT alias_norm FROM product_aliases')},
                             {'mynickname', 'testproduct'})
