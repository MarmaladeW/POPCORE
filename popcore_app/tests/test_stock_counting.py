"""盘点 Count (owner, 2026-10-27): find a product, count it with its sealed sets and designs on
one card, and take the store live from those counts. Before go-live a count only records what
was found; once the store is live a count corrects its stock at once."""
import io
import json
import sys
from contextlib import closing, redirect_stdout
from pathlib import Path

from support import APP_DIR, IsolatedApiCase, db

ROOT = APP_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from checkout_access import business_date  # noqa: E402
from scripts.inventory_opening import main as _opening_cli  # noqa: E402

STAFF = 'auth0|staff'
BARCODE = '6941234567890'


def opening_cli(argv):
    with redirect_stdout(io.StringIO()):
        return _opening_cli(argv)


class StockCountingTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.dt = con.execute("SELECT id FROM stores WHERE code='DT'").fetchone()[0]
            self.box = self._product(con, 'WARM-BOX', 'Warmth 盲盒', '盲盒', 'random_box', 'box')
            self.set = self._product(con, 'WARM-SET', 'Warmth 整端', '盲盒', 'sealed_set', 'set')
            con.execute("""INSERT INTO product_conversions(source_product_id, target_product_id, output_per_input, version)
                           VALUES (?, ?, 12, 1)""", (self.set, self.box))
            self.series = con.execute("INSERT INTO product_series(name, product_id) VALUES ('Warmth', ?)",
                                      (self.box,)).lastrowid
            con.execute('UPDATE products SET series_id=? WHERE id=?', (self.series, self.box))
            self.rider = self._product(con, 'WARM-BOX-01', 'Warmth Rider', '盲盒', 'confirmed_design', 'piece',
                                       series=self.series, design='Rider')
            self.lantern = self._product(con, 'WARM-BOX-02', 'Warmth Lantern', '盲盒', 'confirmed_design',
                                         'piece', series=self.series, design='Lantern')
            self.card_item = self._product(con, 'HANG-1', 'Hanging Card Bear', '吊卡', None, None)
            # A blind box like most in production: typed 盲盒, unverified, 12 boxes per set, no whole set yet.
            self.echo = self._product(con, 'ECHO-1', 'Echo', '盲盒', None, None)
            con.execute('UPDATE products SET boxes_per_dan=12 WHERE id=?', (self.echo,))
            con.execute("""INSERT INTO product_barcodes(code, product_id, code_kind, input_unit, quantity_per_scan)
                           VALUES (?, ?, 'manufacturer', 'box', 1)""", (BARCODE, self.box))
            con.execute('INSERT INTO stock(product_id, store_id, upstairs_qty, instore_qty, claw_qty) VALUES (?, ?, 2, 5, 0)',
                        (self.box, self.dt))
            con.execute('INSERT INTO stock(product_id, store_id, upstairs_qty, instore_qty, claw_qty) VALUES (?, ?, 0, 3, 0)',
                        (self.card_item, self.dt))
            for subject in (STAFF, 'auth0|admin'):
                con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)', (subject, self.dt))
            con.commit()

    @staticmethod
    def _product(con, sku, name, product_type, form, unit, *, series=None, design=None):
        return con.execute(
            """INSERT INTO products(sku, name_cn_en, jizhanming, product_type, stock_form, stock_unit,
                                    identity_status, series_id, design_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sku, name, name, product_type, form, unit, 'verified' if form else 'unverified', series, design),
        ).lastrowid

    def get(self, path, role='staff', **params):
        return self.client.get(path, query_string={'store_code': 'DT', **params}, headers=self.headers(role))

    def save(self, entries, *, location='floor', product=None, checked=None, key='k1', role='staff', new_set=None):
        body = {'store_code': 'DT', 'location': location, 'product_id': product or self.box, 'entries': entries}
        if checked is not None:
            body['designs_checked'] = checked
        if new_set is not None:
            body['new_sealed_set'] = new_set
        headers = {**self.headers(role), 'Idempotency-Key': key} if key else self.headers(role)
        return self.client.post('/api/counting/counts', json=body, headers=headers)

    def search(self, query):
        response = self.get('/api/counting/search', location='floor', q=query)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['results']

    def test_search_finds_one_card_by_design_name_barcode_or_set_sku(self):
        for query in ('rider', BARCODE, 'warm-set', 'Warmth'):
            results = self.search(query)
            self.assertEqual([r['product_id'] for r in results], [self.box], query)
            self.assertEqual((results[0]['kind'], results[0]['sets'], results[0]['designs']), ('box', 1, 2))
        bear = self.search('hanging card')
        self.assertEqual([(r['product_id'], r['kind']) for r in bear], [(self.card_item, 'item')])
        self.assertEqual(self.search('   '), [])

    def test_the_card_holds_the_box_its_sealed_set_and_its_designs(self):
        card = self.get('/api/counting/card', location='floor', product_id=self.lantern).get_json()
        self.assertEqual(card['product_id'], self.box)
        self.assertEqual([(i['role'], i['product_id']) for i in card['items']],
                         [('box', self.box), ('set', self.set), ('design', self.lantern), ('design', self.rider)])
        self.assertEqual(card['items'][1]['boxes_per_set'], 12)
        self.assertIsNone(card['designs_checked'])
        self.assertFalse(card['live'])
        self.assertTrue(all('count' not in item and 'balance_version' not in item for item in card['items']))
        item = self.get('/api/counting/card', location='floor', product_id=self.card_item).get_json()
        self.assertEqual([(i['role'], i['suggested_form']) for i in item['items']], [('item', 'ordinary')])

    def test_before_go_live_a_count_is_recorded_and_stock_is_untouched(self):
        before = self.snapshot(['stock', 'inventory_balances', 'stock_transactions'])
        response = self.save([{'product_id': self.box, 'quantity': 7}, {'product_id': self.set, 'quantity': 1}])
        self.assertEqual(response.status_code, 200, response.get_json())
        # POPCORE kept whole sets as boxes, so only the box total is compared: 7 + 1 x 12 against 5.
        self.assertEqual(response.get_json(), {'live': False, 'boxes': {'popcore': 5, 'counted': 19}, 'results': [
            {'product_id': self.box, 'quantity': 7, 'previous_count': None, 'popcore_quantity': None},
            {'product_id': self.set, 'quantity': 1, 'previous_count': None, 'popcore_quantity': None},
        ]})
        recount = self.save([{'product_id': self.box, 'quantity': 8}], key='k2').get_json()
        self.assertEqual(recount['results'][0]['previous_count'], 7)
        self.assertEqual(self.snapshot(['stock', 'inventory_balances', 'stock_transactions']), before)
        with closing(self.connect()) as con:
            self.assertEqual([tuple(r) for r in con.execute(
                'SELECT product_id, quantity, stock_form, counted_by FROM opening_counts ORDER BY product_id')],
                [(self.box, 8, 'random_box', STAFF), (self.set, 1, 'sealed_set', STAFF)])
            self.assertEqual([tuple(r) for r in con.execute(
                'SELECT product_id, quantity, previous_quantity FROM opening_count_events ORDER BY id')],
                [(self.box, 7, None), (self.set, 1, None), (self.box, 8, 7)])
        card = self.get('/api/counting/card', location='floor', product_id=self.box).get_json()
        self.assertEqual(card['items'][0]['count']['quantity'], 8)

    def test_designs_are_counted_only_once_they_are_checked(self):
        entries = [{'product_id': self.box, 'quantity': 0}, {'product_id': self.rider, 'quantity': 2}]
        unchecked = self.save(entries)
        self.assertEqual((unchecked.status_code, unchecked.get_json()['code']), (400, 'designs_unchecked'))
        checked = self.save(entries, checked=True, key='k2')
        self.assertEqual(checked.status_code, 200, checked.get_json())
        later = self.save([{'product_id': self.lantern, 'quantity': 0}], key='k3')
        self.assertEqual(later.status_code, 200, later.get_json())
        card = self.get('/api/counting/card', location='floor', product_id=self.box).get_json()
        self.assertEqual(card['designs_checked']['by'], 'someone')
        self.assertEqual({i['product_id']: i.get('count', {}).get('quantity') for i in card['items']},
                         {self.box: 0, self.set: None, self.rider: 2, self.lantern: 0})

    def test_whole_sets_or_designs_need_the_single_boxes_counted_too(self):
        # A blank 散盒 would leave POPCORE's boxes here uncounted, so it has to be a number, 0 included.
        alone = self.save([{'product_id': self.set, 'quantity': 2}])
        self.assertEqual((alone.status_code, alone.get_json()['code']), (400, 'head_uncounted'))
        new_sets = self.save([], product=self.echo, new_set={'quantity': 1, 'boxes_per_set': 12}, key='k2')
        self.assertEqual(new_sets.get_json()['code'], 'head_uncounted')
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM products WHERE sku='ECHO-1-SET'").fetchone()[0], 0)
        self.assertEqual(self.save([{'product_id': self.box, 'quantity': 0}], key='k3').status_code, 200)
        later = self.save([{'product_id': self.set, 'quantity': 2}], key='k4')
        self.assertEqual(later.status_code, 200, later.get_json())
        self.assertEqual(later.get_json()['boxes'], {'popcore': 5, 'counted': 24})
        # Elsewhere in the store the boxes are counted again.
        upstairs = self.save([{'product_id': self.set, 'quantity': 1}], location='upstairs', key='k5')
        self.assertEqual(upstairs.get_json()['code'], 'head_uncounted')

    def test_counts_must_belong_to_the_card_and_be_whole_numbers(self):
        cases = [
            ([{'product_id': self.card_item, 'quantity': 1}], 'card_changed'),
            ([{'product_id': self.box, 'quantity': -1}], 'invalid_input'),
            ([{'product_id': self.box, 'quantity': 1.5}], 'invalid_input'),
            ([{'product_id': self.box, 'quantity': 1}, {'product_id': self.box, 'quantity': 2}], 'invalid_input'),
            ([], 'invalid_input'),
            ([{'product_id': self.box, 'quantity': 1, 'stock_form': 'ordinary'}], 'identity_changed'),
        ]
        for number, (entries, code) in enumerate(cases):
            response = self.save(entries, key=f'bad-{number}')
            self.assertEqual(response.get_json().get('code'), code, entries)
            self.assertIn(response.status_code, (400, 409))
        self.assertEqual(self.save([{'product_id': self.box, 'quantity': 1}], key=None).status_code, 400)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM opening_counts').fetchone()[0], 0)

    def test_a_retried_save_is_not_counted_twice(self):
        first = self.save([{'product_id': self.box, 'quantity': 4}])
        again = self.save([{'product_id': self.box, 'quantity': 4}])
        self.assertEqual(again.get_json(), first.get_json())
        changed = self.save([{'product_id': self.box, 'quantity': 5}])
        self.assertEqual((changed.status_code, changed.get_json()['code']), (409, 'idempotency_conflict'))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM opening_count_events').fetchone()[0], 1)

    def test_an_unverified_item_is_counted_as_the_form_the_counter_confirms(self):
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET product_type='' WHERE id=?", (self.card_item,))
            con.commit()
        plain = [{'product_id': self.card_item, 'quantity': 3}]
        missing = self.save(plain, product=self.card_item)
        self.assertEqual((missing.status_code, missing.get_json()['code']), (400, 'identity_unverified'))
        not_countable = self.save([{**plain[0], 'stock_form': 'sealed_set'}], product=self.card_item, key='k2')
        self.assertEqual(not_countable.get_json()['code'], 'identity_unverified')
        saved = self.save([{**plain[0], 'stock_form': 'ordinary'}], product=self.card_item, key='k3')
        self.assertEqual(saved.status_code, 200, saved.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT identity_status FROM products WHERE id=?',
                                         (self.card_item,)).fetchone()[0], 'unverified')

    def test_who_may_count(self):
        entries = [{'product_id': self.box, 'quantity': 1}]
        self.assertEqual(self.save(entries, role='viewer').status_code, 403)
        refused = self.save(entries, role='staff:newcomer', key='n1').get_json()
        self.assertEqual(refused['code'], 'inventory_forbidden')
        self.assertIn('a shift there today', refused['error'])
        with closing(self.connect()) as con:
            employee = con.execute("INSERT INTO employees(auth0_id, name) VALUES ('auth0|newcomer', 'Newcomer')").lastrowid
            con.execute("""INSERT INTO shifts(employee_id, store_id, date, start_time, end_time, assigned_by)
                           VALUES (?, ?, ?, '11:00', '19:00', 'auth0|manager')""", (employee, self.dt, business_date()))
            con.commit()
        on_shift = self.save(entries, role='staff:newcomer', key='n2')
        self.assertEqual(on_shift.status_code, 200, on_shift.get_json())
        self.assertEqual(self.get('/api/counting/scope', role='staff:newcomer').status_code, 200)
        self.assertEqual(self.save(entries, role='admin', key='a1').status_code, 200)
        self.assertEqual(self.get('/api/counting/search', location='attic', q='warm').get_json()['code'],
                         'invalid_input')

    def test_progress_lists_what_has_pos_stock_here_but_no_count(self):
        progress = self.get('/api/counting/progress', location='floor').get_json()
        missing = {item['product_id']: item['blocks_opening'] for item in progress['missing']}
        self.assertEqual(missing, {self.product_id: False, self.box: True, self.card_item: False})
        self.save([{'product_id': self.box, 'quantity': 5}])
        progress = self.get('/api/counting/progress', location='floor').get_json()
        self.assertEqual(progress['counted'], 1)
        self.assertNotIn(self.box, {item['product_id'] for item in progress['missing']})
        scope = self.get('/api/counting/scope').get_json()
        self.assertEqual({location['code']: location['counted'] for location in scope['locations']},
                         {'floor': 1, 'upstairs': 0, 'claw': 0, 'display': 0})

    def _count_the_store(self):
        saves = [
            ('floor', self.box, [{'product_id': self.box, 'quantity': 7}, {'product_id': self.set, 'quantity': 1},
                                 {'product_id': self.rider, 'quantity': 2}, {'product_id': self.lantern, 'quantity': 0}],
             True),
            ('floor', self.card_item, [{'product_id': self.card_item, 'quantity': 4}], None),
            ('upstairs', self.box, [{'product_id': self.box, 'quantity': 2}], None),
        ]
        for number, (location, product, entries, checked) in enumerate(saves):
            response = self.save(entries, location=location, product=product, checked=checked, key=f'count-{number}')
            self.assertEqual(response.status_code, 200, response.get_json())

    def _go_live(self, *extra):
        return opening_cli(['intake', '--db', db.DB_PATH, '--from-app', '--store', 'DT',
                            '--empty-location', 'DT-claw', '--empty-location', 'DT-display',
                            '--business-date', '2026-11-01', '--actor-sub', 'auth0|admin', *extra])

    def _review(self):
        """The go-live dry run's report, also when it finds errors."""
        report = Path(self.tempdir.name) / 'review.json'
        try:
            self._go_live('--report', str(report))
        except SystemExit:
            pass
        return json.loads(report.read_text())

    def test_going_live_applies_the_counts_saved_in_the_app(self):
        self._count_the_store()
        review = self._go_live()
        self.assertTrue(review['ready_to_apply'], review['errors'])
        self.assertEqual((review['app_counts'], review['dry_run']), (6, True))
        self.assertEqual({item['product_id'] for item in review['verified_by_count']}, {self.card_item})
        applied = self._go_live('--apply', '--backup', str(Path(self.tempdir.name) / 'before-dt.db'))
        self.assertTrue(applied['applied'], applied.get('errors'))
        with closing(self.connect()) as con:
            balances = {(r['product_id'], r['code']): r['quantity'] for r in con.execute(
                """SELECT b.product_id, l.code, b.quantity FROM inventory_balances b
                   JOIN inventory_locations l ON l.id=b.location_id
                   WHERE l.store_id=? AND b.disposition='saleable'""", (self.dt,))}
            self.assertEqual({key: value for key, value in balances.items() if value},
                             {(self.box, 'floor'): 7, (self.set, 'floor'): 1, (self.rider, 'floor'): 2,
                              (self.card_item, 'floor'): 4, (self.box, 'upstairs'): 2})
            self.assertEqual(con.execute('SELECT stock_form, identity_status FROM products WHERE id=?',
                                         (self.card_item,)).fetchone()[:], ('ordinary', 'verified'))
        again = self._go_live('--apply', '--backup', str(Path(self.tempdir.name) / 'again.db'))
        self.assertTrue(again['already_applied'])

    def test_once_live_a_count_corrects_stock_at_once(self):
        self._count_the_store()
        self._go_live('--apply', '--backup', str(Path(self.tempdir.name) / 'before-dt.db'))
        card = self.get('/api/counting/card', location='floor', product_id=self.box).get_json()
        self.assertTrue(card['live'])
        version = card['items'][0]['balance_version']
        self.assertTrue(all('count' not in item for item in card['items']))
        result = self.save([{'product_id': self.box, 'quantity': 6, 'balance_version': version}], key='live-1')
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()['results'], [
            {'product_id': self.box, 'quantity': 6, 'previous_count': None, 'popcore_quantity': 7}])
        stale = self.save([{'product_id': self.box, 'quantity': 5, 'balance_version': version}], key='live-2')
        self.assertEqual((stale.status_code, stale.get_json()['code'], stale.get_json()['product_ids']),
                         (409, 'count_stale', [self.box]))
        fresh = self.get('/api/counting/card', location='floor', product_id=self.box).get_json()['items'][0]
        same = self.save([{'product_id': self.box, 'quantity': 6, 'balance_version': fresh['balance_version']}],
                         key='live-3')
        self.assertEqual(same.status_code, 200, same.get_json())
        with closing(self.connect()) as con:
            location = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'",
                                   (self.dt,)).fetchone()[0]
            self.assertEqual(con.execute("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                                            AND disposition='saleable'""", (self.box, location)).fetchone()[0], 6)
            counts = [tuple(r) for r in con.execute(
                """SELECT c.status, c.review_reason, c.inventory_document_id IS NOT NULL, line.expected_quantity,
                          line.observed_quantity FROM inventory_counts c
                   JOIN inventory_count_lines line ON line.count_id=c.id ORDER BY c.id""")]
            self.assertEqual(counts, [('approved', '盘点 Count', 1, 7, 6), ('approved', '盘点 Count', 0, 6, 6)])
            self.assertEqual(con.execute("SELECT instore_qty FROM stock WHERE product_id=? AND store_id=?",
                                         (self.box, self.dt)).fetchone()[0], 6)
        progress = self.get('/api/counting/progress', location='floor').get_json()
        self.assertEqual((progress['live'], progress['counted']), (True, 1))

    def test_once_live_a_product_missed_on_count_day_is_verified_by_its_recount(self):
        self._count_the_store()
        self._go_live('--apply', '--backup', str(Path(self.tempdir.name) / 'before-dt.db'))
        card = self.get('/api/counting/card', location='upstairs', product_id=self.product_id).get_json()
        self.assertEqual([(i['identity_status'], i['suggested_form']) for i in card['items']], [('unverified', None)])
        entry = {'product_id': self.product_id, 'quantity': 10, 'balance_version': card['items'][0]['balance_version']}
        missing_form = self.save([entry], location='upstairs', product=self.product_id, key='late-1')
        self.assertEqual(missing_form.get_json()['code'], 'identity_unverified')
        saved = self.save([{**entry, 'stock_form': 'ordinary'}], location='upstairs', product=self.product_id, key='late-2')
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(saved.get_json()['results'][0]['popcore_quantity'], 0)
        with closing(self.connect()) as con:
            self.assertEqual(tuple(con.execute('SELECT stock_form, stock_unit, identity_status FROM products WHERE id=?',
                                               (self.product_id,)).fetchone()), ('ordinary', 'piece', 'verified'))
            self.assertEqual(con.execute(
                """SELECT b.quantity FROM inventory_balances b JOIN inventory_locations l ON l.id=b.location_id
                   WHERE b.product_id=? AND l.code='upstairs' AND l.store_id=? AND b.disposition='saleable'""",
                (self.product_id, self.dt)).fetchone()[0], 10)

    def test_the_product_type_decides_what_is_counted(self):
        def card(product):
            return self.get('/api/counting/card', location='floor', product_id=product).get_json()
        echo = card(self.echo)
        self.assertEqual([(i['role'], i['suggested_form']) for i in echo['items']], [('box', 'random_box')])
        self.assertEqual(echo['new_sealed_set'], {'boxes_per_set': 12})
        hanging = card(self.card_item)
        self.assertEqual(([(i['role'], i['suggested_form']) for i in hanging['items']], hanging['new_sealed_set']),
                         ([('item', 'ordinary')], None))
        self.assertIsNone(card(self.box)['new_sealed_set'])  # it already has its whole set
        untyped = card(self.product_id)  # no known type: the counter is asked, and may say blind box
        self.assertEqual(([i['suggested_form'] for i in untyped['items']], untyped['new_sealed_set']),
                         ([None], {'boxes_per_set': None}))

    def test_counting_the_first_whole_sets_gives_the_blind_box_its_whole_set(self):
        saved = self.save([{'product_id': self.echo, 'quantity': 5}], product=self.echo,
                          new_set={'quantity': 2, 'boxes_per_set': 12})
        self.assertEqual(saved.status_code, 200, saved.get_json())
        with closing(self.connect()) as con:
            whole = con.execute("SELECT * FROM products WHERE sku='ECHO-1-SET'").fetchone()
            self.assertEqual((whole['stock_form'], whole['stock_unit'], whole['identity_status']),
                             ('sealed_set', 'set', 'verified'))
            self.assertEqual(con.execute('SELECT output_per_input FROM product_conversions WHERE source_product_id=? '
                                         'AND target_product_id=?', (whole['id'], self.echo)).fetchone()[0], 12)
            self.assertEqual(tuple(con.execute('SELECT stock_form, identity_status, boxes_per_dan FROM products WHERE id=?',
                                               (self.echo,)).fetchone()), ('random_box', 'verified', 12))
            self.assertEqual(sorted(tuple(r) for r in con.execute(
                'SELECT product_id, quantity, stock_form FROM opening_counts ORDER BY product_id')),
                [(self.echo, 5, 'random_box'), (whole['id'], 2, 'sealed_set')])
        self.assertEqual(sorted((r['product_id'], r['quantity']) for r in saved.get_json()['results']),
                         [(self.echo, 5), (whole['id'], 2)])
        card = self.get('/api/counting/card', location='floor', product_id=self.echo).get_json()
        self.assertEqual(([(i['role'], i.get('count', {}).get('quantity')) for i in card['items']], card['new_sealed_set']),
                         ([('box', 5), ('set', 2)], None))
        # A second phone whose card showed no whole set: here the first phone's count stands...
        same_shelf = self.save([], product=self.echo, new_set={'quantity': 1, 'boxes_per_set': 12}, key='k2')
        self.assertEqual((same_shelf.status_code, same_shelf.get_json()['code'], same_shelf.get_json()['product_ids']),
                         (409, 'count_stale', [whole['id']]))
        # ...and upstairs it counts against the set the first phone added.
        upstairs = self.save([{'product_id': self.echo, 'quantity': 0}], location='upstairs', product=self.echo,
                             new_set={'quantity': 1, 'boxes_per_set': 12}, key='k3')
        self.assertEqual(upstairs.status_code, 200, upstairs.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM products WHERE sku LIKE 'ECHO-1-SET%'").fetchone()[0], 1)
            self.assertEqual([r[0] for r in con.execute('SELECT quantity FROM opening_counts WHERE product_id=? '
                                                        'ORDER BY location_id', (whole['id'],))], [2, 1])
        changed = self.save([], product=self.echo, new_set={'quantity': 1, 'boxes_per_set': 6}, key='k4')
        self.assertEqual((changed.status_code, changed.get_json()['code']), (409, 'whole_set_changed'))

    def test_boxes_per_set_typed_with_no_whole_sets_is_kept_on_the_product(self):
        def card():
            return self.get('/api/counting/card', location='floor', product_id=self.echo).get_json()
        def boxes_per_dan():
            with closing(self.connect()) as con:
                return con.execute('SELECT boxes_per_dan FROM products WHERE id=?', (self.echo,)).fetchone()[0]
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET boxes_per_dan=NULL WHERE id=?', (self.echo,))
            con.commit()
        self.assertEqual(card()['new_sealed_set'], {'boxes_per_set': None})
        # Owner report 2026-10-30: 每端几盒 typed with 0 whole sets was dropped, so it was empty on reopening.
        saved = self.save([{'product_id': self.echo, 'quantity': 5}], product=self.echo,
                          new_set={'quantity': 0, 'boxes_per_set': 10})
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(saved.get_json(), {'live': False, 'results': [
            {'product_id': self.echo, 'quantity': 5, 'previous_count': None, 'popcore_quantity': 0}]})
        self.assertEqual(boxes_per_dan(), 10)
        with closing(self.connect()) as con:
            # No whole set yet, so no set product: it is added when its first whole sets are counted.
            self.assertEqual(con.execute("SELECT COUNT(*) FROM products WHERE sku LIKE 'ECHO-1-SET%'").fetchone()[0], 0)
            self.assertIsNone(con.execute('SELECT 1 FROM product_conversions WHERE target_product_id=?',
                                          (self.echo,)).fetchone())
            self.assertEqual([tuple(r) for r in con.execute('SELECT product_id, quantity FROM opening_counts')],
                             [(self.echo, 5)])
        reopened = card()
        self.assertEqual(reopened['new_sealed_set'], {'boxes_per_set': 10})
        self.assertEqual([(i['role'], i['count']['quantity']) for i in reopened['items']], [('box', 5)])
        # A retry is not saved twice; the same key with another size is refused.
        self.assertEqual(self.save([{'product_id': self.echo, 'quantity': 5}], product=self.echo,
                                   new_set={'quantity': 0, 'boxes_per_set': 10}).get_json(), saved.get_json())
        other = self.save([{'product_id': self.echo, 'quantity': 5}], product=self.echo,
                          new_set={'quantity': 0, 'boxes_per_set': 8})
        self.assertEqual((other.status_code, other.get_json()['code']), (409, 'idempotency_conflict'))
        # The size alone saves on a counted box; where the box is not counted yet, nothing is saved.
        alone = self.save([], product=self.echo, new_set={'quantity': 0, 'boxes_per_set': 12}, key='k2')
        self.assertEqual((alone.status_code, alone.get_json()), (200, {'live': False, 'results': []}))
        self.assertEqual(boxes_per_dan(), 12)
        upstairs = self.save([], location='upstairs', product=self.echo,
                             new_set={'quantity': 0, 'boxes_per_set': 6}, key='k3')
        self.assertEqual(upstairs.get_json()['code'], 'head_uncounted')
        self.assertEqual(boxes_per_dan(), 12)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM opening_count_events').fetchone()[0], 1)
        # Counting the first whole sets still adds the set, with the saved size; after that it cannot change.
        first_sets = self.save([], product=self.echo, new_set={'quantity': 2, 'boxes_per_set': 12}, key='k4')
        self.assertEqual(first_sets.status_code, 200, first_sets.get_json())
        with closing(self.connect()) as con:
            whole = con.execute("SELECT id FROM products WHERE sku='ECHO-1-SET'").fetchone()[0]
            self.assertEqual(con.execute('SELECT output_per_input FROM product_conversions WHERE source_product_id=?',
                                         (whole,)).fetchone()[0], 12)
        self.assertEqual(([(i['role'], i['count']['quantity'], i.get('boxes_per_set')) for i in card()['items']],
                          card()['new_sealed_set']), ([('box', 5, None), ('set', 2, 12)], None))
        changed = self.save([], product=self.echo, new_set={'quantity': 0, 'boxes_per_set': 6}, key='k5')
        self.assertEqual((changed.status_code, changed.get_json()['code']), (409, 'whole_set_changed'))
        self.assertEqual(boxes_per_dan(), 12)

    def test_once_live_boxes_per_set_alone_moves_no_stock(self):
        self._count_the_store()
        self._go_live('--apply', '--backup', str(Path(self.tempdir.name) / 'before-dt.db'))
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET boxes_per_dan=NULL WHERE id=?', (self.echo,))
            con.commit()
        card = self.get('/api/counting/card', location='floor', product_id=self.echo).get_json()
        self.assertEqual((card['live'], card['new_sealed_set']), (True, {'boxes_per_set': None}))
        ledger = ['inventory_balances', 'inventory_counts', 'inventory_documents', 'inventory_movements']
        before = self.snapshot(ledger)
        # A box count against a balance that moved is refused, and the size typed with it is not saved.
        stale = self.save([{'product_id': self.echo, 'quantity': 2, 'balance_version': 99}], product=self.echo,
                          new_set={'quantity': 0, 'boxes_per_set': 10}, key='stale')
        self.assertEqual((stale.status_code, stale.get_json()['code']), (409, 'count_stale'))
        with closing(self.connect()) as con:
            self.assertIsNone(con.execute('SELECT boxes_per_dan FROM products WHERE id=?', (self.echo,)).fetchone()[0])
        saved = self.save([], product=self.echo, new_set={'quantity': 0, 'boxes_per_set': 10}, key='size')
        self.assertEqual((saved.status_code, saved.get_json()), (200, {'live': True, 'results': []}))
        self.assertEqual(self.snapshot(ledger), before)
        with closing(self.connect()) as con:
            self.assertEqual(tuple(con.execute('SELECT boxes_per_dan, stock_form FROM products WHERE id=?',
                                               (self.echo,)).fetchone()), (10, None))
            self.assertEqual(con.execute("SELECT COUNT(*) FROM products WHERE sku='ECHO-1-SET'").fetchone()[0], 0)
        self.assertEqual(self.get('/api/counting/card', location='floor', product_id=self.echo).get_json()['new_sealed_set'],
                         {'boxes_per_set': 10})

    def test_whole_sets_are_only_added_to_blind_boxes(self):
        for number, (product, new_set, code) in enumerate([
            (self.card_item, {'quantity': 1, 'boxes_per_set': 6}, 'whole_set_unavailable'),
            (self.echo, {'quantity': -1, 'boxes_per_set': 12}, 'invalid_input'),
            (self.echo, {'quantity': 1, 'boxes_per_set': 0}, 'invalid_input'),
            (self.echo, {'quantity': 1, 'boxes_per_set': 1}, 'invalid_input'),
            (self.echo, {'quantity': 1}, 'invalid_input'),
        ]):
            response = self.save([], product=product, new_set=new_set, key=f'set-{number}')
            self.assertEqual(response.get_json()['code'], code, new_set)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM products WHERE stock_form='sealed_set'").fetchone()[0], 1)
        only_sets = self.save([{'product_id': self.echo, 'quantity': 0}], product=self.echo,
                              new_set={'quantity': 3, 'boxes_per_set': 6}, key='set-ok')
        self.assertEqual(only_sets.status_code, 200, only_sets.get_json())

    def test_whole_sets_are_not_offered_without_a_free_set_sku(self):
        def offer():
            return self.get('/api/counting/card', location='floor', product_id=self.echo).get_json()['new_sealed_set']
        with closing(self.connect()) as con:
            taken = self._product(con, 'ECHO-1-SET', 'Someone else', '周边', 'ordinary', 'piece')
            con.commit()
        self.assertIsNone(offer())
        refused = self.save([{'product_id': self.echo, 'quantity': 0}], product=self.echo,
                            new_set={'quantity': 1, 'boxes_per_set': 12})
        self.assertEqual((refused.status_code, refused.get_json()['code']), (409, 'whole_set_unavailable'))
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET sku='OTHER-1' WHERE id=?", (taken,))
            con.execute("UPDATE products SET sku='' WHERE id=?", (self.echo,))
            con.commit()
        self.assertIsNone(offer())

    def test_a_card_opened_before_someone_else_saved_does_not_overwrite_their_count(self):
        def shown(product):
            card = self.get('/api/counting/card', location='floor', product_id=self.box).get_json()
            return next(item.get('count', {}).get('version') for item in card['items'] if item['product_id'] == product)
        self.assertIsNone(shown(self.box))
        first = self.save([{'product_id': self.box, 'quantity': 7, 'count_version': None}])
        self.assertEqual(first.status_code, 200, first.get_json())
        # Another phone's card still shows no count of the box.
        late = self.save([{'product_id': self.box, 'quantity': 5, 'count_version': None},
                          {'product_id': self.set, 'quantity': 1, 'count_version': None}], key='k2')
        self.assertEqual((late.status_code, late.get_json()['code'], late.get_json()['product_ids']),
                         (409, 'count_stale', [self.box]))
        with closing(self.connect()) as con:
            self.assertEqual([tuple(r) for r in con.execute('SELECT product_id, quantity FROM opening_counts')],
                             [(self.box, 7)])
        # Counted again on the reloaded card, it saves.
        again = self.save([{'product_id': self.box, 'quantity': 5, 'count_version': shown(self.box)},
                           {'product_id': self.set, 'quantity': 1, 'count_version': None}], key='k3')
        self.assertEqual(again.status_code, 200, again.get_json())
        self.assertEqual(again.get_json()['results'][0]['previous_count'], 7)

    def test_a_card_opened_before_go_live_cannot_change_live_stock(self):
        self._count_the_store()
        self._go_live('--apply', '--backup', str(Path(self.tempdir.name) / 'before-dt.db'))
        before = self.snapshot(['inventory_balances', 'inventory_counts'])
        # A card loaded before the store went live has saved counts and no balance versions.
        blind = self.save([{'product_id': self.box, 'quantity': 3, 'count_version': 9},
                           {'product_id': self.set, 'quantity': 0}], key='late')
        self.assertEqual((blind.status_code, blind.get_json()['code'], blind.get_json()['product_ids']),
                         (409, 'count_stale', sorted([self.box, self.set])))
        self.assertIn('went live', blind.get_json()['error'])
        self.assertEqual(self.snapshot(['inventory_balances', 'inventory_counts']), before)

    def test_a_live_card_names_only_the_products_whose_stock_moved(self):
        self._count_the_store()
        self._go_live('--apply', '--backup', str(Path(self.tempdir.name) / 'before-dt.db'))
        card = self.get('/api/counting/card', location='floor', product_id=self.box).get_json()
        versions = {item['product_id']: item['balance_version'] for item in card['items']}
        moved = self.save([{'product_id': self.rider, 'quantity': 1, 'balance_version': versions[self.rider]}],
                          key='sale')
        self.assertEqual(moved.status_code, 200, moved.get_json())
        stale = self.save([{'product_id': product, 'quantity': 1, 'balance_version': versions[product]}
                           for product in (self.box, self.set, self.rider)], key='mine')
        self.assertEqual((stale.status_code, stale.get_json()['product_ids']), (409, [self.rider]))
        self.assertIn('Warmth Rider', stale.get_json()['error'])

    def test_a_live_whole_set_added_by_another_phone_is_counted_again(self):
        self._count_the_store()
        self._go_live('--apply', '--backup', str(Path(self.tempdir.name) / 'before-dt.db'))
        card = self.get('/api/counting/card', location='floor', product_id=self.echo).get_json()
        box = {'product_id': self.echo, 'quantity': 2, 'balance_version': card['items'][0]['balance_version']}
        first = self.save([box], product=self.echo, new_set={'quantity': 1, 'boxes_per_set': 12}, key='a')
        self.assertEqual(first.status_code, 200, first.get_json())
        late = self.save([], product=self.echo, new_set={'quantity': 3, 'boxes_per_set': 12}, key='b')
        whole = next(r['product_id'] for r in first.get_json()['results'] if r['product_id'] != self.echo)
        self.assertEqual((late.status_code, late.get_json()['product_ids']), (409, [whole]))
        self.assertIn('Someone just added the whole set', late.get_json()['error'])
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("""SELECT SUM(quantity) FROM inventory_balances
                                            WHERE product_id=? AND disposition='saleable'""", (whole,)).fetchone()[0], 1)

    def test_a_live_card_shows_units_not_counted_as_sellable(self):
        self._count_the_store()
        self._go_live('--apply', '--backup', str(Path(self.tempdir.name) / 'before-dt.db'))
        with closing(self.connect()) as con:
            floor = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'",
                                (self.dt,)).fetchone()[0]
            con.execute("""INSERT INTO inventory_balances(product_id, location_id, disposition, quantity, version)
                           VALUES (?, ?, 'hold', 2, 1)""", (self.box, floor))
            con.commit()
        items = self.get('/api/counting/card', location='floor', product_id=self.box).get_json()['items']
        self.assertEqual([(i['product_id'], i.get('other_stock')) for i in items if i.get('other_stock')],
                         [(self.box, [{'disposition': 'hold', 'quantity': 2}])])

    def test_whats_left_marks_what_going_live_needs(self):
        def left(location):
            progress = self.get('/api/counting/progress', location=location).get_json()
            return {item['product_id']: (item['blocks_opening'], item.get('recount', False))
                    for item in progress['missing']}
        self.assertEqual(left('floor')[self.product_id], (False, False))
        # Counted upstairs, going live verifies it, so its POPCORE stock on the floor must be counted too.
        upstairs = self.save([{'product_id': self.product_id, 'quantity': 10, 'stock_form': 'ordinary'}],
                             location='upstairs', product=self.product_id)
        self.assertEqual(upstairs.status_code, 200, upstairs.get_json())
        self.assertEqual(left('floor')[self.product_id], (True, False))
        # Set up as another form in Products after it was counted: count it again.
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET stock_form='random_box', stock_unit='box', identity_status='verified' "
                        'WHERE id=?', (self.product_id,))
            con.commit()
        self.assertEqual(left('upstairs')[self.product_id], (True, True))
        self.assertIn('identity_changed', {error['code'] for error in self._review()['errors']})

    def test_an_untyped_product_keeps_the_form_it_was_first_counted_as(self):
        floor = self.save([{'product_id': self.product_id, 'quantity': 2, 'stock_form': 'random_box'}],
                          product=self.product_id)
        self.assertEqual(floor.status_code, 200, floor.get_json())
        card = self.get('/api/counting/card', location='upstairs', product_id=self.product_id).get_json()
        self.assertEqual(card['items'][0]['counted_form'], 'random_box')
        other = self.save([{'product_id': self.product_id, 'quantity': 10, 'stock_form': 'ordinary'}],
                          location='upstairs', product=self.product_id, key='k2')
        self.assertEqual((other.status_code, other.get_json()['code']), (409, 'identity_conflict'))
        self.assertIn('DT', other.get_json()['error'])
        same = self.save([{'product_id': self.product_id, 'quantity': 10, 'stock_form': 'random_box'}],
                         location='upstairs', product=self.product_id, key='k3')
        self.assertEqual(same.status_code, 200, same.get_json())
        # Counted as an item at one place, it gets no whole set (which would make it a blind box) at another.
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET product_type='' WHERE id=?", (self.card_item,))
            con.commit()
        item = self.save([{'product_id': self.card_item, 'quantity': 1, 'stock_form': 'ordinary'}],
                         location='upstairs', product=self.card_item, key='k4')
        self.assertEqual(item.status_code, 200, item.get_json())
        boxed = self.save([{'product_id': self.card_item, 'quantity': 0, 'stock_form': 'random_box'}],
                          product=self.card_item, new_set={'quantity': 1, 'boxes_per_set': 6}, key='k5')
        self.assertEqual((boxed.status_code, boxed.get_json()['code']), (409, 'identity_conflict'))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM products WHERE sku='HANG-1-SET'").fetchone()[0], 0)
        self.assertNotIn('identity_conflict', {error['code'] for error in self._review()['errors']})

    def test_a_type_set_in_products_wins_over_an_earlier_count(self):
        # Untyped, counted as an item at the claw; then a manager types it 盲盒 in Products.
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET product_type='' WHERE id=?", (self.echo,))
            con.commit()
        claw = self.save([{'product_id': self.echo, 'quantity': 0, 'stock_form': 'ordinary'}],
                         location='claw', product=self.echo)
        self.assertEqual(claw.status_code, 200, claw.get_json())
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET product_type='盲盒' WHERE id=?", (self.echo,))
            con.commit()
        card = self.get('/api/counting/card', location='floor', product_id=self.echo).get_json()
        self.assertEqual(card['items'][0]['suggested_form'], 'random_box')
        floor = self.save([{'product_id': self.echo, 'quantity': 3, 'stock_form': 'random_box'}],
                          product=self.echo, key='k2')
        self.assertEqual(floor.status_code, 200, floor.get_json())
        # The claw count, in the form the type no longer has, is counted again.
        left = self.get('/api/counting/progress', location='claw').get_json()['missing']
        self.assertIn((self.echo, True), [(row['product_id'], row.get('recount', False)) for row in left])
        again = self.save([{'product_id': self.echo, 'quantity': 0, 'stock_form': 'random_box'}],
                          location='claw', product=self.echo, key='k3')
        self.assertEqual(again.status_code, 200, again.get_json())
        left = self.get('/api/counting/progress', location='claw').get_json()['missing']
        self.assertNotIn(self.echo, [row['product_id'] for row in left])
        with closing(self.connect()) as con:
            self.assertEqual({row[0] for row in con.execute(
                'SELECT stock_form FROM opening_counts WHERE product_id=?', (self.echo,))}, {'random_box'})

    def test_every_product_is_on_its_own_card(self):
        with closing(self.connect()) as con:
            # Not a design, yet inside the Warmth series; and a design whose series no product owns.
            con.execute('UPDATE products SET series_id=? WHERE id=?', (self.series, self.card_item))
            loose = con.execute("INSERT INTO product_series(name) VALUES ('Loose')").lastrowid
            orphan = self._product(con, 'LOOSE-01', 'Loose One', '盲盒', 'confirmed_design', 'piece',
                                   series=loose, design='One')
            con.commit()
            products = [row[0] for row in con.execute('SELECT id FROM products')]
        for product in products:
            card = self.get('/api/counting/card', location='floor', product_id=product).get_json()
            self.assertIn(product, [item['product_id'] for item in card['items']], card)
        self.assertEqual([(r['product_id'], r['kind']) for r in self.search('hanging card')],
                         [(self.card_item, 'item')])
        self.assertEqual([r['kind'] for r in self.search('loose one')], ['series'])
        self.assertEqual(self.get('/api/counting/card', location='floor', product_id=orphan).get_json()['title'],
                         'Loose')

    def test_a_mistyped_design_can_be_removed_with_its_count(self):
        self.save([{'product_id': self.rider, 'quantity': 1}], checked=True)
        removed = self.client.post(f'/api/products/{self.rider}/design/remove', headers=self.headers('manager'))
        self.assertEqual(removed.status_code, 200, removed.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM opening_counts WHERE product_id=?',
                                         (self.rider,)).fetchone()[0], 0)
            con.execute('INSERT INTO stock(product_id, store_id, upstairs_qty, instore_qty, claw_qty) VALUES (?, ?, 0, 1, 0)',
                        (self.lantern, self.dt))
            con.commit()
        in_use = self.client.post(f'/api/products/{self.lantern}/design/remove', headers=self.headers('manager'))
        self.assertEqual((in_use.status_code, in_use.get_json()['code']), (409, 'design_in_use'))
        self.assertEqual(self.client.post(f'/api/products/{self.box}/design/remove',
                                          headers=self.headers('manager')).status_code, 404)
        self.assertEqual(self.client.post(f'/api/products/{self.lantern}/design/remove',
                                          headers=self.headers('staff')).status_code, 403)
