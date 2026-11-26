from contextlib import closing

from test_checkout_blind_boxes import BOX_PRICE, BlindBoxFixture
from test_receiving import ReceivingFixture

# 库存查看 Inventory view (owner, 2026-11-01): only the workers an admin picks see a store's stock.
# Both accounts below have 门店操作权限 at DT, so only 库存查看 tells them apart.
HIDDEN = 'staff:hidden'
PICKED = 'staff:picked'
TABLES = ('stock', 'stock_transactions', 'inventory_documents', 'inventory_balances', 'goods_receipts',
          'inventory_deliveries', 'inventory_counts', 'inventory_viewers')


class InventoryVisibilityTests(ReceivingFixture):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            for subject in ('auth0|hidden', 'auth0|picked', 'auth0|manager', 'auth0|admin'):
                con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)', (subject, self.store_id))
            con.execute('INSERT INTO inventory_viewers(auth0_sub, store_id) VALUES (?, ?)', ('auth0|picked', self.store_id))
            self.mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            self.upstairs = con.execute(
                "SELECT id FROM inventory_locations WHERE store_id=? AND code='upstairs'", (self.store_id,)
            ).fetchone()[0]
            self.empty_id = con.execute(
                """INSERT INTO products(sku, name_cn_en, jizhanming, product_type, stock_form, stock_unit, identity_status)
                   VALUES ('EMPTY-1', 'Empty Product', 'Empty Product', 'ordinary', 'ordinary', 'piece', 'verified')"""
            ).lastrowid
            con.execute('INSERT INTO stock(product_id, store_id, upstairs_qty, instore_qty, claw_qty) VALUES (?, ?, 0, 0, 0)',
                        (self.empty_id, self.store_id))
            self.series_id = con.execute("INSERT INTO product_series(name) VALUES ('Hidden Series')").lastrowid
            con.commit()
        # Five on the floor, received by a picked worker: the ledger document the 库存 tab shows.
        draft = self.receipt(key='visibility-stock', quantity=5).get_json()
        posted = self.call('POST', f"/api/goods/receipts/{draft['id']}/post", PICKED,
                           {'expected_version': draft['version']}, 'visibility-stock-post')
        self.assertEqual(posted.status_code, 200, posted.get_json())
        self.document_id = posted.get_json()['inventory_document_id']

    def call(self, method, path, role, body=None, key=None):
        headers = {**self.headers(role), **({'Idempotency-Key': key} if key else {})}
        return self.client.open(path, method=method, headers=headers, json=body)

    def assertHidden(self, response):
        self.assertEqual((response.status_code, (response.get_json() or {}).get('code')), (403, 'inventory_hidden'),
                         response.get_json())

    def assertNotHidden(self, response):
        body = response.get_json()
        self.assertFalse(isinstance(body, dict) and body.get('code') == 'inventory_hidden', body)
        self.assertLess(response.status_code, 500, response.get_json())

    def receipt(self, role=PICKED, key='visibility-receipt', quantity=1):
        return self.call('POST', '/api/goods/receipts', role, {
            'store_id': self.store_id, 'destination_location_id': self.floor, 'business_date': '2026-11-01',
            'lines': [{'product_id': self.product_id, 'unit': 'piece', 'saleable_quantity': quantity}],
        }, key)

    def transfer(self):
        response = self.call('POST', '/api/goods/transfers', 'manager', {
            'source_location_id': self.floor, 'destination_location_id': self.upstairs, 'business_date': '2026-11-01',
            'lines': [{'product_id': self.product_id, 'unit': 'piece', 'requested_quantity': 1}],
        }, 'visibility-transfer')
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def inventory_tab_requests(self):
        """Every 库存-tab route, as (method, path, body); each names DT."""
        receipt = self.receipt().get_json()
        transfer = self.transfer()
        dt = 'store_code=DT'
        legacy = {'store_code': 'DT', 'product_id': self.product_id}
        return [
            ('GET', f'/api/stock/summary?{dt}', None),
            ('GET', f'/api/stock/transactions?{dt}', None),
            ('PATCH', f'/api/stock/{self.product_id}', {'store_code': 'DT', 'notes': 'hidden'}),
            ('POST', '/api/stock/ru_dian', {**legacy, 'qty': 1}),
            ('POST', '/api/stock/restock_upstairs', {**legacy, 'qty': 1}),
            ('POST', '/api/stock/adjust', {**legacy, 'new_qty': 1, 'location': 'upstairs', 'notes': 'hidden'}),
            ('POST', '/api/stock/batch_operation', {'store_code': 'DT', 'operation': 'ru_dian',
                                                    'items': [{'product_id': self.product_id, 'qty': 1}]}),
            ('GET', f'/api/inventory/balances?{dt}', None),
            ('GET', f'/api/inventory/documents/{self.document_id}', None),
            ('POST', '/api/inventory/commands', {'kind': 'move', 'business_date': '2026-11-01', 'reason': 'hidden',
                                                 'lines': [{'product_id': self.product_id, 'quantity': 1, 'unit': 'piece',
                                                            'from_location_id': self.floor, 'from_disposition': 'saleable',
                                                            'to_location_id': self.upstairs, 'to_disposition': 'saleable',
                                                            'expected_versions': {'from': 1, 'to': 0}}]}),
            ('GET', f'/api/inventory/series?{dt}', None),
            ('GET', f'/api/inventory/overview?{dt}', None),
            ('GET', f'/api/inventory/series/{self.series_id}/history?{dt}', None),
            ('GET', f'/api/counting/scope?{dt}', None),
            ('GET', f'/api/counting/search?{dt}&location=floor&q=Test', None),
            ('GET', f'/api/counting/card?{dt}&location=floor&product_id={self.product_id}', None),
            ('GET', f'/api/counting/progress?{dt}&location=floor', None),
            ('POST', '/api/counting/counts', {'store_code': 'DT', 'location': 'floor', 'product_id': self.product_id,
                                              'entries': [{'product_id': self.product_id, 'quantity': 1}]}),
            ('POST', '/api/goods/receipts', {'store_id': self.store_id, 'destination_location_id': self.floor,
                                             'business_date': '2026-11-01',
                                             'lines': [{'product_id': self.product_id, 'unit': 'piece',
                                                        'saleable_quantity': 1}]}),
            ('GET', f"/api/goods/receipts/{receipt['id']}", None),
            ('PATCH', f"/api/goods/receipts/{receipt['id']}", {'expected_version': receipt['version']}),
            ('POST', f"/api/goods/receipts/{receipt['id']}/post", {'expected_version': receipt['version']}),
            ('POST', f"/api/goods/receipts/{receipt['id']}/cancel", {'expected_version': receipt['version']}),
            ('GET', f'/api/goods/transfers?store_id={self.store_id}', None),
            ('GET', f"/api/goods/transfers/{transfer['id']}", None),
            ('POST', f"/api/goods/transfers/{transfer['id']}/dispatch", {'expected_version': transfer['version'],
                                                                       'lines': [{'line_no': 1, 'quantity': 1}]}),
            ('POST', '/api/goods/identify', {'location_id': self.floor, 'reason': 'hidden'}),
            ('GET', f'/api/reports/inventory?{dt}', None),
            ('GET', f'/api/reports/movements?{dt}', None),
            ('GET', f'/api/reports/goods-exceptions?{dt}', None),
            ('GET', f'/api/reports/count-discrepancies?{dt}', None),
        ]

    def test_nobody_is_picked_until_an_admin_ticks_them(self):
        stores = {row['code']: row['inventory_visible'] for row in self.call('GET', '/api/stores', HIDDEN).get_json()}
        self.assertTrue({'DT', 'MK'} <= set(stores))
        self.assertFalse(any(stores.values()))
        picked = {row['code']: row['inventory_visible'] for row in self.call('GET', '/api/stores', PICKED).get_json()}
        self.assertEqual({code for code, visible in picked.items() if visible}, {'DT'})
        for role in ('manager', 'admin'):
            self.assertTrue(all(row['inventory_visible'] for row in self.call('GET', '/api/stores', role).get_json()))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM inventory_viewers WHERE auth0_sub<>'auth0|picked'"
                                         " AND auth0_sub<>'auth0|staff'").fetchone()[0], 0)

    def test_only_admins_pick_viewers_per_store(self):
        grant = {'auth0_sub': 'auth0|hidden', 'store_id': self.store_id}
        for role in ('staff', 'manager'):
            self.assertEqual(self.call('POST', '/api/inventory/viewers', role, grant).status_code, 403)
            self.assertEqual(self.call('GET', '/api/inventory/viewers', role).status_code, 403)
        self.assertEqual(self.call('POST', '/api/inventory/viewers', 'admin', grant).status_code, 201)
        self.assertEqual(self.call('POST', '/api/inventory/viewers', 'admin', grant).status_code, 201)  # idempotent
        listing = self.call('GET', '/api/inventory/viewers', 'admin').get_json()
        self.assertTrue({'DT', 'MK'} <= {store['code'] for store in listing['stores']})
        self.assertIn(grant, listing['grants'])
        self.assertEqual(self.call('GET', f'/api/stock/summary?store_code=DT', HIDDEN).status_code, 200)
        self.assertHidden(self.call('GET', f'/api/stock/summary?store_code=MK', HIDDEN))
        self.assertEqual(self.call('DELETE', '/api/inventory/viewers', 'admin', grant).status_code, 200)
        self.assertNotIn(grant, self.call('GET', '/api/inventory/viewers', 'admin').get_json()['grants'])
        self.assertHidden(self.call('GET', f'/api/stock/summary?store_code=DT', HIDDEN))
        for body in ({}, {'auth0_sub': 'auth0|hidden'}, {'auth0_sub': ' ', 'store_id': 1}, {'auth0_sub': 'x', 'store_id': '1'}):
            self.assertEqual(self.call('POST', '/api/inventory/viewers', 'admin', body).status_code, 400)
        self.assertEqual(self.call('POST', '/api/inventory/viewers', 'admin',
                                   {'auth0_sub': 'auth0|hidden', 'store_id': 999}).status_code, 404)

    def test_unpicked_workers_are_refused_the_inventory_tab_and_its_tools(self):
        requests = self.inventory_tab_requests()
        before = self.snapshot(TABLES)
        for method, path, body in requests:
            with self.subTest(path=path, method=method):
                self.assertHidden(self.call(method, path, HIDDEN, body, f'hidden-{method}-{path}'))
        self.assertEqual(self.snapshot(TABLES), before)

    def test_picked_workers_and_managers_still_reach_them(self):
        for role in (PICKED, 'manager', 'admin'):
            for method, path, body in self.inventory_tab_requests():
                if method != 'GET':
                    continue
                with self.subTest(role=role, path=path):
                    self.assertNotHidden(self.call(method, path, role))

    def test_the_inventory_overview_is_for_picked_workers(self):
        self.assertHidden(self.call('GET', '/api/inventory/overview?store_code=DT', HIDDEN))
        self.assertHidden(self.call('GET', '/api/inventory/overview?store_code=ALL', HIDDEN))
        for role in (PICKED, 'manager'):
            with self.subTest(role=role):
                response = self.call('GET', '/api/inventory/overview?store_code=DT', role)
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertTrue(response.get_json()['live'])
                self.assertIn('figures', response.get_json())

    def test_a_pick_covers_only_its_store(self):
        with closing(self.connect()) as con:  # 门店操作权限 at MK too, so only 库存查看 refuses it there
            con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)', ('auth0|picked', self.mk))
            con.commit()
        self.assertEqual(self.call('GET', '/api/stock/summary?store_code=DT', PICKED).status_code, 200)
        for path in ('/api/stock/summary?store_code=MK', '/api/stock/summary?store_code=ALL',
                     '/api/stock/transactions?store_code=ALL', '/api/reports/inventory?store_code=MK'):
            with self.subTest(path=path):
                self.assertHidden(self.call('GET', path, PICKED))
        everywhere = self.call('GET', '/api/reports/inventory?store_code=ALL', HIDDEN)
        self.assertEqual(everywhere.status_code, 200, everywhere.get_json())
        self.assertEqual(everywhere.get_json()['items'], [])

    def test_catalog_and_checkout_lists_keep_products_but_only_say_out_of_stock(self):
        for path in ('/api/stock?store_code=DT', '/api/stock?store_code=DT&include_all=1',
                     '/api/stock?store_code=DT&page=1&page_size=500', '/api/stock?store_code=ALL'):
            with self.subTest(path=path):
                body = self.call('GET', path, HIDDEN).get_json()
                rows = {row['id']: row for row in (body['items'] if 'items' in body else body)}
                self.assertEqual({key: rows[self.product_id][key] for key in ('upstairs_qty', 'instore_qty', 'out_of_stock', 'stock_hidden')},
                                 {'upstairs_qty': None, 'instore_qty': None, 'out_of_stock': False, 'stock_hidden': True})
                self.assertTrue(rows[self.empty_id]['out_of_stock'])
                self.assertEqual(rows[self.product_id]['jizhanming'], 'Test Product')
        one = self.call('GET', f'/api/stock/{self.product_id}?store_code=DT', HIDDEN).get_json()
        self.assertEqual((one['upstairs_qty'], one['instore_qty'], one['out_of_stock']), (None, None, False))
        shown = {row['id']: row for row in self.call('GET', '/api/stock?store_code=DT', PICKED).get_json()}
        with closing(self.connect()) as con:
            stored = tuple(con.execute('SELECT upstairs_qty, instore_qty FROM stock WHERE product_id=? AND store_id=?',
                                       (self.product_id, self.store_id)).fetchone())
        self.assertEqual((shown[self.product_id]['upstairs_qty'], shown[self.product_id]['instore_qty']), stored)
        self.assertNotIn('stock_hidden', shown[self.product_id])

    def test_restock_search_says_only_whether_upstairs_is_out(self):
        for role, store, expected in ((HIDDEN, 'DT', None), (PICKED, 'DT', 10), (PICKED, 'ALL', None), ('manager', 'ALL', 10)):
            with self.subTest(role=role, store=store):
                rows = {row['id']: row for row in self.call(
                    'GET', f'/api/products/search?include_stock=1&store_code={store}', role).get_json()}
                self.assertEqual(rows[self.product_id]['upstairs_qty'], expected)
                if expected is None:
                    self.assertFalse(rows[self.product_id]['out_of_stock'])
                    self.assertTrue(rows[self.empty_id]['out_of_stock'])
        searched = self.call('GET', '/api/products/search?include_stock=1&store_code=DT&q=Test', HIDDEN).get_json()
        self.assertTrue(searched and all(row['upstairs_qty'] is None for row in searched))

    def test_unpicked_workers_still_run_restock_without_numbers(self):
        created = self.call('POST', '/api/restock/sessions', HIDDEN, {'store_code': 'DT'})
        self.assertEqual(created.status_code, 201, created.get_json())
        session = created.get_json()['id']
        for product_id in (self.product_id, self.empty_id):
            added = self.call('POST', '/api/restock/items', HIDDEN,
                              {'session_id': session, 'product_id': product_id, 'requested_qty': 1})
            self.assertEqual(added.status_code, 201, added.get_json())
        self.assertEqual(self.call('POST', f'/api/restock/session/{session}/submit', HIDDEN).status_code, 200)
        for path in (f'/api/restock/session/{session}', f'/api/restock/session/{session}/picking-list'):
            with self.subTest(path=path):
                items = {item['product_id']: item for item in self.call('GET', path, HIDDEN).get_json()['items']}
                for key in ('upstairs_qty', 'instore_qty', 'warehouse_stock_snapshot'):
                    self.assertIsNone(items[self.product_id][key])
                self.assertEqual((items[self.product_id]['out_of_stock'], items[self.empty_id]['out_of_stock']), (False, True))
                shown = {item['product_id']: item for item in self.call('GET', path, PICKED).get_json()['items']}
                self.assertEqual((shown[self.product_id]['upstairs_qty'], shown[self.product_id]['warehouse_stock_snapshot']), (10, 10))

    def test_restock_suggestions_hide_quantities(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_balances(product_id, location_id, disposition, quantity, version)"
                        " VALUES (?, ?, 'saleable', 3, 1)", (self.product_id, self.upstairs))
            con.commit()
        saved = self.call('PUT', '/api/goods/targets', 'manager', {
            'location_id': self.floor, 'product_id': self.product_id,
            'min_quantity': 8, 'max_quantity': 10, 'expected_version': 0}, 'visibility-target')
        self.assertEqual(saved.status_code, 200, saved.get_json())
        path = f'/api/goods/restock-suggestions?location_id={self.floor}'
        hidden = self.call('GET', path, HIDDEN).get_json()['items'][0]
        self.assertEqual({key: hidden[key] for key in ('floor_quantity', 'available_back_quantity', 'outstanding_inbound',
                                                       'suggested_quantity', 'out_of_stock', 'min_quantity')},
                         {'floor_quantity': None, 'available_back_quantity': None, 'outstanding_inbound': None,
                          'suggested_quantity': None, 'out_of_stock': False, 'min_quantity': 8})
        shown = self.call('GET', path, PICKED).get_json()['items'][0]
        self.assertEqual((shown['floor_quantity'], shown['available_back_quantity'], shown['suggested_quantity']), (5, 3, 3))

    def test_evening_count_stays_open(self):
        for path in ('/api/inventory-check/today?store_code=DT', '/api/history/inventory-check?store_code=DT',
                     '/api/bestsellers?store_code=DT', '/api/history/restock?store_code=DT'):
            with self.subTest(path=path):
                response = self.call('GET', path, HIDDEN)
                self.assertEqual(response.status_code, 200, response.get_json())

    def test_evening_count_pages_keep_what_popcore_holds_for_picked_workers(self):
        """Owner, 2026-11-26: the 晚盘 pages stay open, but what the floor holds is a stock number."""
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET is_bestseller=1 WHERE id=?', (self.product_id,))
            con.execute('INSERT INTO stock(product_id, store_id, upstairs_qty, instore_qty, claw_qty) VALUES (?, ?, 0, 7, 0)',
                        (self.product_id, self.mk))
            con.commit()
        saved = self.call('POST', '/api/inventory-check/submit', PICKED,
                          {'store_code': 'DT', 'checks': [{'product_id': self.product_id, 'sets': 0, 'loose': 3}]})
        self.assertEqual(saved.status_code, 201, saved.get_json())
        for role, held in ((HIDDEN, None), ('viewer:nobody', None), (PICKED, 5), ('manager', 5)):
            with self.subTest(role=role):
                best = self.call('GET', '/api/bestsellers?store_code=DT', role).get_json()
                self.assertEqual([(row['id'], row['instore_qty']) for row in best], [(self.product_id, held)])
                past = self.call('GET', '/api/history/inventory-check?store_code=DT', role).get_json()
                self.assertEqual([(row['actual_qty'], row['theoretical_qty'], row['discrepancy']) for row in past],
                                 [(3, held, held and -2)])
        # MK is not live: its 晚盘 page hides the same numbers (nobody here is picked at MK).
        for role, held in ((HIDDEN, None), (PICKED, None), ('manager', 7)):
            with self.subTest(role=role, store='MK'):
                today = self.call('GET', '/api/inventory-check/today?store_code=MK', role).get_json()['items']
                self.assertEqual([(row['product_id'], row['current_instore_qty'], row['theoretical_qty']) for row in today],
                                 [(self.product_id, held, held)])

    def count_body(self, location=None):
        return {'location_id': self.floor if location is None else location, 'business_date': '2026-11-01',
                'lines': [{'product_id': self.product_id, 'unit': 'piece', 'observed_quantity': 0}]}

    def test_stock_counts_are_for_picked_workers(self):
        """A count keeps what POPCORE held beside what was counted, so it shows stock (owner, 2026-11-26)."""
        before = self.snapshot(TABLES)
        self.assertHidden(self.call('POST', '/api/goods/counts', HIDDEN, self.count_body(), 'count-hidden'))
        self.assertEqual(self.snapshot(TABLES), before)
        created = self.call('POST', '/api/goods/counts', PICKED, self.count_body(), 'count-picked')
        self.assertEqual(created.status_code, 201, created.get_json())
        count = created.get_json()
        path = f"/api/goods/counts/{count['id']}"
        with closing(self.connect()) as con:  # the store computer: a viewer with 门店操作权限, not picked
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|counter', ?)", (self.store_id,))
            con.commit()
        for role in (HIDDEN, 'viewer:counter'):
            self.assertHidden(self.call('GET', path, role))
        self.assertHidden(self.call('PATCH', path, HIDDEN, {'expected_version': count['version'],
                                                            'lines': [{'line_no': 1, 'observed_quantity': 9}]}, 'count-patch'))
        self.assertHidden(self.call('POST', path + '/submit', HIDDEN, {'expected_version': count['version']}, 'count-submit'))
        shown = self.call('GET', path, PICKED)
        self.assertEqual(shown.status_code, 200, shown.get_json())
        self.assertEqual([(line['expected_quantity'], line['observed_quantity']) for line in shown.get_json()['lines']], [(5, 0)])
        self.assertEqual(self.call('POST', path + '/submit', PICKED, {'expected_version': count['version']},
                                   'count-submit-picked').status_code, 200)
        self.assertEqual(self.call('GET', path, 'manager').status_code, 200)

    def test_an_id_sent_as_text_is_checked_like_a_number(self):
        """The ledger reads "12" as 12, so the 库存查看 check has to as well; an id it cannot read is refused,
        never passed over."""
        def command(source, target):
            return {'kind': 'move', 'business_date': '2026-11-01', 'reason': 'text ids',
                    'lines': [{'product_id': self.product_id, 'quantity': 1, 'unit': 'piece',
                               'from_location_id': source, 'from_disposition': 'saleable',
                               'to_location_id': target, 'to_disposition': 'saleable',
                               'expected_versions': {'from': 1, 'to': 0}}]}
        receipt = {'destination_location_id': self.floor, 'business_date': '2026-11-01',
                   'lines': [{'product_id': self.product_id, 'unit': 'piece', 'saleable_quantity': 1}]}
        identify = {'source_product_id': self.product_id, 'design_product_id': self.empty_id, 'quantity': 1,
                    'source_version': 1, 'target_version': 0, 'business_date': '2026-11-01', 'reason': 'text ids'}
        before = self.snapshot(TABLES)
        hidden = [
            ('/api/inventory/commands', command(str(self.floor), str(self.upstairs))),
            ('/api/inventory/commands', command(str(self.floor), self.upstairs)),
            ('/api/inventory/commands', command(f' {self.floor}'.strip(), None) | {'kind': 'consume'}),
            ('/api/goods/identify', {**identify, 'location_id': str(self.floor)}),
            ('/api/goods/receipts', {**receipt, 'store_id': str(self.store_id)}),
            ('/api/goods/counts', self.count_body(str(self.floor))),
        ]
        for number, (path, body) in enumerate(hidden):
            with self.subTest(path=path, number=number):
                self.assertHidden(self.call('POST', path, HIDDEN, body, f'text-id-{number}'))
        unreadable = [
            ('/api/inventory/commands', command('floor', self.upstairs)),
            ('/api/inventory/commands', command([self.floor], self.upstairs)),
            ('/api/inventory/commands', command(True, self.upstairs)),
            ('/api/inventory/commands', command(1.5, self.upstairs)),
            ('/api/goods/identify', {**identify, 'location_id': 'floor'}),
            ('/api/goods/identify', identify),
            ('/api/goods/receipts', {**receipt, 'store_id': [self.store_id]}),
            ('/api/goods/receipts', receipt),
            ('/api/goods/counts', self.count_body('floor')),
        ]
        for role in (HIDDEN, PICKED):
            for number, (path, body) in enumerate(unreadable):
                with self.subTest(path=path, number=number, role=role):
                    response = self.call('POST', path, role, body, f'unreadable-{role}-{number}')
                    self.assertEqual((response.status_code, response.get_json()['code']), (400, 'invalid_input'), response.get_json())
        self.assertEqual(self.snapshot(TABLES), before)
        # A picked worker's text ids work as they always did.
        moved = self.call('POST', '/api/inventory/commands', PICKED, command(str(self.floor), str(self.upstairs)), 'text-id-picked')
        self.assertEqual(moved.status_code, 201, moved.get_json())
        self.assertEqual(self.call('POST', '/api/goods/receipts', PICKED, {**receipt, 'store_id': str(self.store_id)},
                                   'text-id-receipt').status_code, 201)

    def test_lines_that_are_not_objects_are_refused(self):
        bad = self.call('POST', '/api/goods/transfers', 'manager', {
            'source_location_id': self.floor, 'destination_location_id': self.upstairs, 'business_date': '2026-11-01',
            'lines': ['one']}, 'bad-transfer')
        self.assertEqual((bad.status_code, bad.get_json()['code']), (400, 'invalid_input'))
        transfer = self.transfer()
        bad = self.call('POST', f"/api/goods/transfers/{transfer['id']}/dispatch", 'manager',
                        {'expected_version': transfer['version'], 'lines': [1]}, 'bad-dispatch')
        self.assertEqual((bad.status_code, bad.get_json()['code']), (400, 'invalid_input'))
        for path in ('/api/goods/restock-suggestions', '/api/goods/restock-suggestions?location_id=floor'):
            self.assertEqual(self.call('GET', path, PICKED).status_code, 400)


class CheckoutBoxCountTests(BlindBoxFixture):
    """Checkout asks where a blind box over half a set came from; without 库存查看 its answers say only
    whether there is a sealed set and loose boxes, never how many (owner, 2026-11-01)."""

    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:  # this cashier is not picked for DT
            con.execute("DELETE FROM inventory_viewers WHERE auth0_sub='auth0|staff'")
            con.commit()

    def test_unpicked_cashiers_see_no_box_counts_but_still_sell(self):
        order = self.manual()
        hidden = {'set_size': 12, 'open_sets': [], 'loose_units': None, 'loose_out_of_stock': False, 'stock_hidden': True,
                  'fresh_set': {'product_id': self.set_id, 'conversion_id': self.conversion_id, 'conversion_factor': 12,
                                'name': 'Series Set', 'sets_on_hand': None, 'out_of_stock': False, 'stock_hidden': True}}
        self.assertEqual(order['box_choices'][0]['options'], hidden)
        path = f'/api/sale-documents/box-options?store_id={self.store_id}&product_id={self.box_id}'
        self.assertEqual(self.client.get(path, headers=self.headers('staff')).get_json()['options'], hidden)
        shown = self.client.get(path, headers=self.headers('manager')).get_json()['options']
        self.assertEqual((shown['loose_units'], shown['fresh_set']['sets_on_hand']), (10, 2))
        self.assertNotIn('stock_hidden', shown)

        opened = self.choose(order, 'fresh', 'fresh_set')
        self.assertEqual(opened['box_choices'][0]['choice'], 'fresh_set')
        self.assertIsNone(opened['box_choices'][0]['options']['loose_units'])
        paid = self.receive(opened, 'pay', 'card', 7 * BOX_PRICE, card_reference='4242')
        self.assertEqual(paid['status'], 'completed', paid)
        with closing(self.connect()) as con:
            remaining = con.execute('SELECT remaining_qty FROM inventory_open_sets').fetchone()[0]
        self.assertEqual(remaining, 5)  # a set of 12 opened, 7 sold
        later = self.manual(key='create-2')
        self.assertEqual(later['box_choices'][0]['options']['open_sets'][0]['remaining_qty'], None)
