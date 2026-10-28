"""盘点 Count (owner, 2026-10-27): find a product, count it with its sealed sets and designs on
one card, and take the store live from those counts. Before go-live a count only records what
was found; once the store is live a count corrects its stock at once."""
import io
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
        self.assertEqual(response.get_json(), {'live': False, 'results': [
            {'product_id': self.box, 'quantity': 7, 'previous_count': None, 'popcore_quantity': 5},
            {'product_id': self.set, 'quantity': 1, 'previous_count': None, 'popcore_quantity': 0},
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
        unchecked = self.save([{'product_id': self.rider, 'quantity': 2}])
        self.assertEqual((unchecked.status_code, unchecked.get_json()['code']), (400, 'designs_unchecked'))
        checked = self.save([{'product_id': self.rider, 'quantity': 2}], checked=True, key='k2')
        self.assertEqual(checked.status_code, 200, checked.get_json())
        later = self.save([{'product_id': self.lantern, 'quantity': 0}], key='k3')
        self.assertEqual(later.status_code, 200, later.get_json())
        card = self.get('/api/counting/card', location='floor', product_id=self.box).get_json()
        self.assertEqual(card['designs_checked']['by'], 'someone')
        self.assertEqual({i['product_id']: i.get('count', {}).get('quantity') for i in card['items']},
                         {self.box: None, self.set: None, self.rider: 2, self.lantern: 0})

    def test_counts_must_belong_to_the_card_and_be_whole_numbers(self):
        cases = [
            ([{'product_id': self.card_item, 'quantity': 1}], 'invalid_input'),
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
        self.assertEqual(self.save(entries, role='staff:newcomer', key='n1').get_json()['code'], 'inventory_forbidden')
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
        self.assertEqual((stale.status_code, stale.get_json()['code']), (409, 'count_stale'))
        same = self.save([{'product_id': self.box, 'quantity': 6}], key='live-3')
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
        # A second phone saving the same box's first whole sets a moment later counts against that set.
        again = self.save([], product=self.echo, new_set={'quantity': 1, 'boxes_per_set': 12}, key='k2')
        self.assertEqual(again.status_code, 200, again.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM products WHERE sku LIKE 'ECHO-1-SET%'").fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT quantity FROM opening_counts WHERE product_id=?',
                                         (whole['id'],)).fetchone()[0], 1)
        changed = self.save([], product=self.echo, new_set={'quantity': 1, 'boxes_per_set': 6}, key='k3')
        self.assertEqual((changed.status_code, changed.get_json()['code']), (409, 'whole_set_changed'))

    def test_whole_sets_are_only_added_to_blind_boxes(self):
        for number, (product, new_set, code) in enumerate([
            (self.card_item, {'quantity': 1, 'boxes_per_set': 6}, 'whole_set_unavailable'),
            (self.echo, {'quantity': 0, 'boxes_per_set': 12}, 'invalid_input'),
            (self.echo, {'quantity': 1, 'boxes_per_set': 0}, 'invalid_input'),
            (self.echo, {'quantity': 1}, 'invalid_input'),
        ]):
            response = self.save([], product=product, new_set=new_set, key=f'set-{number}')
            self.assertEqual(response.get_json()['code'], code, new_set)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM products WHERE stock_form='sealed_set'").fetchone()[0], 1)
        only_sets = self.save([], product=self.echo, new_set={'quantity': 3, 'boxes_per_set': 6}, key='set-ok')
        self.assertEqual(only_sets.status_code, 200, only_sets.get_json())

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
