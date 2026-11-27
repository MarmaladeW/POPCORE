"""Rules from the 2026-11-26 access audit that have no other home: the old stock summary keeps the value of stock
for the admin, and input a route cannot read is answered 400, never a server error."""
from contextlib import closing

from support import IsolatedApiCase


class StockSummaryValueTests(IsolatedApiCase):
    """DT is not live here, so the summary is read from the old stock table: 10 upstairs and 2 on the floor."""

    def setUp(self):
        super().setUp()
        self.grant_inventory_view('auth0|staff')
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET price=10 WHERE id=?', (self.product_id,))
            con.commit()

    def test_the_value_of_stock_is_the_admins_alone(self):
        for store in ('DT', 'ALL'):
            for role, value in (('staff', None), ('manager', None), ('admin', 120)):
                with self.subTest(store=store, role=role):
                    response = self.client.get(f'/api/stock/summary?store_code={store}', headers=self.headers(role))
                    self.assertEqual(response.status_code, 200, response.get_json())
                    body = response.get_json()
                    self.assertEqual((body['total_stock_value'], body['total_upstairs_qty'], body['total_instore_qty']),
                                     (value, 10, 2))


class UnreadableInputTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.grant_inventory_view('auth0|staff')
        with closing(self.connect()) as con:  # the staff account works at DT
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|staff', ?)", (self.store_id,))
            con.commit()

    def test_product_search_limit(self):
        for limit, status in (('abc', 400), ('-1', 400), ('1.5', 400), ('1', 200), ('0', 200), ('', 200)):
            with self.subTest(limit=limit):
                response = self.client.get(f'/api/products/search?limit={limit}', headers=self.headers())
                self.assertEqual(response.status_code, status, response.get_json())
        self.assertEqual(len(self.client.get('/api/products/search?limit=1', headers=self.headers()).get_json()), 1)

    def test_evening_count_of_a_store_that_is_not_live(self):
        def submit(body):
            return self.client.post('/api/inventory-check/submit', json=body, headers=self.headers())
        good = {'product_id': self.product_id, 'actual_qty': 1}
        for checks in ([{'product_id': 'x', 'actual_qty': 1}], [{'product_id': self.product_id, 'actual_qty': 'many'}],
                       [{'product_id': self.product_id, 'actual_qty': -1}], [{'product_id': self.product_id, 'actual_qty': 1.5}],
                       [good, 'two'], [good, {'product_id': self.product_id + 1, 'actual_qty': -3}], 'nothing', {'a': 1}):
            with self.subTest(checks=checks):
                response = submit({'store_code': 'DT', 'checks': checks})
                self.assertEqual((response.status_code, response.get_json().get('code')), (400, 'invalid_input'), response.get_json())
        self.assertEqual(submit([good]).status_code, 400)
        with closing(self.connect()) as con:  # a batch with one bad count saves none of it
            self.assertEqual(con.execute('SELECT COUNT(*) FROM inventory_checks').fetchone()[0], 0)
        saved = submit({'store_code': 'DT', 'checks': [good]})
        self.assertEqual(saved.status_code, 201, saved.get_json())
        # The floor held 2: the difference shows, since this account may see DT's stock.
        self.assertEqual(saved.get_json()['results'], [{'product_id': self.product_id, 'discrepancy': -1}])

    def test_the_difference_is_kept_from_whoever_may_not_see_stock(self):
        with closing(self.connect()) as con:
            con.execute("DELETE FROM inventory_viewers WHERE auth0_sub='auth0|staff'")
            con.execute('UPDATE products SET is_bestseller=1 WHERE id=?', (self.product_id,))
            con.commit()
        saved = self.client.post('/api/inventory-check/submit', headers=self.headers(),
                                 json={'store_code': 'DT', 'checks': [{'product_id': self.product_id, 'actual_qty': 1}]})
        self.assertEqual(saved.status_code, 201, saved.get_json())
        self.assertEqual(saved.get_json()['results'], [{'product_id': self.product_id, 'discrepancy': None}])
        today = self.client.get('/api/inventory-check/today?store_code=DT', headers=self.headers()).get_json()['items'][0]
        self.assertEqual((today['actual_qty'], today['current_instore_qty'], today['theoretical_qty'], today['discrepancy']),
                         (1, None, None, None))
        seen = self.client.get('/api/inventory-check/today?store_code=DT', headers=self.headers('manager')).get_json()['items'][0]
        self.assertEqual((seen['actual_qty'], seen['current_instore_qty'], seen['discrepancy']), (1, 2, -1))

    def test_a_list_where_an_object_is_expected(self):
        for method, path in (('post', '/api/restock/items'), ('patch', '/api/restock/items/1/pick'),
                             ('patch', f'/api/stock/{self.product_id}'), ('patch', f'/api/products/{self.product_id}/bestseller')):
            with self.subTest(path=path):
                response = getattr(self.client, method)(path, json=['not', 'an', 'object'], headers=self.headers())
                self.assertEqual((response.status_code, response.get_json().get('code')), (400, 'invalid_input'), response.get_json())

    def test_stock_history_limit_stays_between_1_and_500(self):
        with closing(self.connect()) as con:
            con.executemany("""INSERT INTO stock_transactions(product_id, txn_type, qty, location, date, notes, store_id)
                               VALUES (?, 'adjust', 1, 'upstairs', '2026-11-01', ?, ?)""",
                            [(self.product_id, f'line {number}', self.store_id) for number in range(505)])
            con.commit()
        for limit, rows in (('-1', 1), ('0', 1), ('3', 3), ('100000', 500)):
            with self.subTest(limit=limit):
                response = self.client.get(f'/api/stock/transactions?store_code=DT&limit={limit}', headers=self.headers())
                self.assertEqual((response.status_code, len(response.get_json())), (200, rows))
        self.assertEqual(self.client.get('/api/stock/transactions?store_code=DT&limit=abc', headers=self.headers()).status_code, 400)
