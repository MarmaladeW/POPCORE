"""网站订单 Web orders board (owner, 2026-11-19; the popcore.ca website session's brief of the same day): staff with
store operations access see their stores' paid web orders in five columns, the orders that need a manager and those
done today; Ready and Handed over move a pickup order on the website and leave a note; the code box finds a pickup
order by its code and reads the customer's first name live. No pickup code reaches the board's data; the only
personal thing in it is the name each delivery goes to (owner, 2026-11-25), read live and never stored."""
import json
from contextlib import closing
from datetime import datetime, timedelta, timezone

from test_website_sync import WebsiteCase, _ago

import website_board as board_module
import website_sync

STAFF = {'Authorization': 'Bearer staff'}            # auth0|staff, DT store operations access, named Amy
OTHER = {'Authorization': 'Bearer staff:other'}      # auth0|other, no store operations access
PRIVATE = ('Private', 'Person', '416-555', 'Secret St', 'someone@example.com')
# What never reaches the board, names aside: the customer's phone, email and addresses.
CONTACT = ('416-555', 'Secret St', 'someone@example.com', '647-555', 'Hidden Ave')


def _days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S')


class BoardCase(WebsiteCase):
    """DT live with nine web orders in every state, and a staff member with DT access (test_web_order_notifications.py
    builds on it)."""

    def setUp(self):
        super().setUp()
        self.turn_on()
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|staff', ?)", (self.dt,))
            con.execute("INSERT INTO employees(auth0_id, name) VALUES ('auth0|staff', 'Amy')")
            con.execute("UPDATE products SET jizhanming='Bear 记账名' WHERE id=?", (self.item,))
            # Orders paid in the last week count, so a pickup can have waited days.
            website_sync.set_state(con, 'since', _days_ago(7))
            website_sync.set_state(con, 'orders_after', _days_ago(7))
            con.commit()
        woo = self.woo
        woo.order(701, [(20, 1)], pickup='dt', paid=_ago(50), meta=[('_popcore_pickup_code', 'K7R4MX')])
        woo.order(702, [(11, 2)], pickup='dt', status='ready-pickup', paid=_days_ago(4),
                  meta=[('_popcore_pickup_code', 'ACD346'), ('_pcc_collector_name', 'Sammy')])
        woo.order(703, [(11, 1)], paid=_ago(40), meta=[('_pcc_delivery_window', 'Fri Nov 21, 2–6 pm'),
                                                       ('_pcc_delivery_service', 'normal'),
                                                       ('_pcc_delivery_zone', 'Downtown')],
                  shipping={'first_name': 'Zoë', 'last_name': 'Ng'})
        woo.order(704, [(20, 1)], status='dispatched', paid=_ago(300), shipping={'first_name': 'Ana', 'last_name': 'Lima'},
                  meta=[
            ('_pcc_delivery_window', 'Thu Nov 20, 2–6 pm'), ('_pcc_delivery_service', 'door'),
            ('fleetnow_order_no', 'DO202611190000000001'), ('fleetnow_pickup_window', '13:00–14:00'),
            ('fleetnow_delivery_window', '14:00–18:00'), ('fleetnow_status', 'CONFIRMED')])
        woo.order(705, [(11, 1)], status='out-for-delivery', paid=_ago(400))
        woo.order(706, [(20, 1)], pickup='dt', status='awaiting-stock', paid=_ago(30))   # a special order
        woo.order(707, [(12, 3)], pickup='dt', paid=_ago(20), meta=[('_popcore_pickup_code', 'HJK777')])  # only 2 sets
        woo.order(708, [(11, 1)], pickup='dt', status='completed', paid=_ago(10))
        woo.order(709, [(11, 1)], pickup='mk', paid=_ago(5), meta=[('_popcore_pickup_code', 'MKP999')])
        self.run_sync()

    def board(self, headers=STAFF, store=None):
        response = self.client.get('/api/website/board' + (f'?store={store}' if store else ''), headers=headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def numbers(self, cards):
        return [card['number'] for card in cards]

    def code(self, code, headers=STAFF, store='DT'):
        return self.client.post('/api/website/board/code', json={'code': code, 'store': store}, headers=headers)

    def step(self, order_id, action, code=None, headers=STAFF):
        return self.client.post(f'/api/website/board/orders/{order_id}/{action}',
                                json={'code': code} if code else {}, headers=headers)


class WebOrdersBoardTests(BoardCase):
    def test_orders_sit_in_their_columns_oldest_first_with_nothing_personal_but_delivery_names(self):
        response = self.client.get('/api/website/board', headers=STAFF)
        board = response.get_json()
        self.assertEqual(board['stores'], ['DT'])
        self.assertEqual({name: self.numbers(cards) for name, cards in board['columns'].items()},
                         {'pick': ['701'], 'ready': ['702'], 'pack': ['703'], 'booked': ['704'], 'out': ['705']})
        # The MK pickup is on MK's board only, though DT's stock filled it (MK is not live here).
        self.assertEqual([(card['number'], card['reason']) for card in board['needs_manager']],
                         [('706', 'status'), ('707', 'not_enough_stock')])
        self.assertEqual(self.numbers(board['done_today']), ['708'])
        pick = board['columns']['pick'][0]
        self.assertEqual((pick['kind'], pick['store'], pick['status'], pick['taken_elsewhere']),
                         ('pickup', 'DT', 'processing', []))
        # Lines by 记账名, with where they were taken from (the floor first, owner 2026-11-19).
        self.assertEqual(pick['lines'], [{'name': 'Bear 记账名', 'quantity': 1, 'popcore_id': 'PM0200',
                                          'picks': [{'store': 'DT', 'place': 'floor', 'quantity': 1}]}])
        self.assertEqual(board['columns']['pack'][0]['delivery'],
                         {'window': 'Fri Nov 21, 2–6 pm', 'service': 'normal', 'zone': 'Downtown'})
        self.assertEqual(board['columns']['booked'][0]['delivery'], {
            'window': 'Thu Nov 20, 2–6 pm', 'service': 'door', 'fleetnow_no': 'DO202611190000000001',
            'fleetnow_pickup': '13:00–14:00', 'fleetnow_delivery': '14:00–18:00', 'fleetnow_status': 'CONFIRMED'})
        self.assertNotIn('delivery', pick)
        # A pickup carries no name; its name stays behind the code.
        self.assertFalse([card for cards in board['columns'].values() for card in cards
                          if card['kind'] == 'pickup' and 'name' in card])
        # No phone, email, address or pickup code on the board; nothing personal in the managers' order list.
        manager_orders = self.client.get('/api/website/orders', headers=self.headers('manager')).get_data(as_text=True)
        for value in (*CONTACT, 'K7R4MX', 'ACD346', 'HJK777', 'MKP999', 'pickup_code'):
            self.assertNotIn(value, response.get_data(as_text=True))
        for value in (*PRIVATE, 'Zoë', 'Lima', 'K7R4MX', 'ACD346', 'HJK777', 'MKP999', 'pickup_code'):
            self.assertNotIn(value, manager_orders)
        # One store only, and none without store operations access; an account with no role cannot open it.
        self.assertEqual(self.board(store='MK')['stores'], [])
        self.assertEqual(self.board(OTHER)['columns']['pick'], [])
        self.assertEqual(self.client.get('/api/website/board', headers=self.headers('norole')).status_code, 403)

    def test_delivery_cards_carry_the_name_read_live_in_one_request(self):
        """Owner, 2026-11-25: "we should be able to see the name for the delivery orders, as well as searching up the
        name." The shipping name, else the billing name, read live for all of the board's deliveries at once."""
        self.woo.order(712, [(11, 1)], status='awaiting-stock', paid=_ago(15), modified=_ago(0),
                       shipping={'first_name': ' Émile ', 'last_name': 'Roy'})
        self.woo.order(713, [(11, 1)], status='completed', paid=_ago(12), modified=_ago(0),
                       shipping={'first_name': 'Done', 'last_name': 'Today'})
        self.run_sync()
        self.woo.reads.clear()
        response = self.client.get('/api/website/board', headers=STAFF)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        board = response.get_json()
        self.assertEqual((board['deliveries'], board['names_read']), (True, True))
        named = {card['number']: card.get('name') for cards in (*board['columns'].values(), board['needs_manager'],
                                                                 board['done_today']) for card in cards}
        self.assertEqual({number: named[number] for number in ('703', '704', '705', '712', '713')},
                         {'703': 'Zoë Ng', '704': 'Ana Lima', '705': 'Private Person', '712': 'Émile Roy',
                          '713': 'Done Today'})
        self.assertEqual(named['701'], None)  # a pickup: no name
        # One read, for the deliveries only, asking for the four name fields only.
        self.assertEqual(self.woo.reads, [('/orders', {'include': '703,704,705,712,713', 'status': 'any',
                                                       '_fields': board_module.NAME_FIELDS, 'per_page': 100})])
        for value in CONTACT:
            self.assertNotIn(value, response.get_data(as_text=True))
        # Never kept: no name is in the database.
        with closing(self.connect()) as con:
            dump = '\n'.join(con.iterdump())
        for value in ('Zoë', 'Lima', 'Émile', 'Private'):
            self.assertNotIn(value, dump)
        # The website does not answer: the board still shows, without names, and says so.
        self.woo.refuse_order_list = True
        board = self.board()
        self.assertEqual((board['names_read'], board['columns']['pack'][0]['name']), (False, None))
        # A board without the delivery store has no deliveries to name, and reads nothing.
        self.woo.refuse_order_list = False
        self.woo.reads.clear()
        self.assertEqual((self.board(store='MK')['deliveries'], self.woo.reads), (False, []))

    def test_a_pickup_taken_from_another_store_says_so(self):
        with closing(self.connect()) as con:
            mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|staff', ?)", (mk,))
            con.commit()
        board = self.board(store='MK')
        self.assertEqual([(card['number'], card['taken_elsewhere']) for card in board['columns']['pick']],
                         [('709', ['DT'])])
        self.assertEqual(self.numbers(self.board()['columns']['pick']), ['701', '709'])

    def test_a_pickup_its_own_store_filled_says_nothing_extra(self):
        """Owner, 2026-11-22: an MK pickup comes off MK's floor, so the card says nothing about bringing it over;
        only what MK lacked and DT filled says "Taken from DT"."""
        self.open_mk(3)
        with closing(self.connect()) as con:
            mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|staff', ?)", (mk,))
            con.commit()
        self.woo.order(710, [(11, 2)], pickup='mk', paid=_ago(4))
        self.woo.order(711, [(11, 3)], pickup='mk', paid=_ago(3))  # MK has 1 left: 2 come from DT
        self.run_sync()
        cards = {card['number']: card for card in self.board(store='MK')['columns']['pick']}
        self.assertEqual({number: cards[number]['taken_elsewhere'] for number in ('709', '710', '711')},
                         {'709': ['DT'], '710': [], '711': ['DT']})
        self.assertEqual(cards['710']['lines'][0]['picks'], [{'store': 'MK', 'place': 'floor', 'quantity': 2}])
        self.assertEqual(cards['711']['lines'][0]['picks'], [{'store': 'MK', 'place': 'floor', 'quantity': 1},
                                                             {'store': 'DT', 'place': 'floor', 'quantity': 2}])

    def test_ready_moves_a_pickup_on_the_website_and_says_who(self):
        response = self.step(701, 'ready')
        self.assertEqual(response.get_json(), {'result': 'done', 'noted': True})
        self.assertEqual(self.woo.orders[701]['status'], 'ready-pickup')
        self.assertEqual(self.woo.notes, [(701, {'note': 'Ready in popcore.store by Amy', 'customer_note': False})])
        self.assertEqual(self.numbers(self.board()['columns']['ready']), ['702', '701'])
        # A second press is safe: the website is already there.
        self.assertEqual(self.step(701, 'ready').get_json(), {'result': 'already', 'noted': None})
        self.assertEqual(self.woo.status_writes, [(701, 'ready-pickup')])
        # Deliveries are FleetNow's; other stores and other people's stores are not this board's.
        self.assertEqual(self.step(703, 'ready').get_json()['code'], 'not_pickup')
        self.assertEqual(self.step(709, 'ready').status_code, 403)
        self.assertEqual(self.step(701, 'ready', headers=OTHER).status_code, 403)
        self.assertEqual(self.woo.status_writes, [(701, 'ready-pickup')])

    def test_ready_reads_the_website_first_and_waits_for_its_answer(self):
        # Cancelled on the website a minute ago: never put back to ready.
        self.woo.orders[701]['status'] = 'cancelled'
        response = self.step(701, 'ready')
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'status_changed'))
        self.assertEqual(self.woo.status_writes, [])
        self.assertNotIn('701', self.numbers(self.board()['columns']['pick']))
        # The website refuses: nothing shows as done.
        self.woo.orders[707]['status'] = 'processing'
        with closing(self.connect()) as con:  # a manager settled 707, so it is back in its column
            con.execute("UPDATE website_orders SET state='dismissed' WHERE wc_order_id=707")
            con.commit()
        self.woo.refuse_orders = True
        response = self.step(707, 'ready')
        self.assertEqual(response.status_code, 502)
        self.assertIn('Something went wrong.', response.get_json()['error'])
        self.assertEqual(self.numbers(self.board()['columns']['pick']), ['707'])
        # The status went but the note did not: done, and the page says the note is missing.
        self.woo.refuse_orders, self.woo.refuse_notes = False, True
        self.assertEqual(self.step(707, 'ready').get_json(), {'result': 'done', 'noted': False})
        self.assertEqual(self.woo.orders[707]['status'], 'ready-pickup')

    def test_the_code_box_finds_the_order_and_reads_the_name_live(self):
        with closing(self.connect()) as con:  # an order closed long ago that happens to have the same code
            con.execute("""INSERT INTO website_orders(wc_order_id, number, wc_status, paid_at, lines, state, fulfilment,
                               pickup_store, pickup_code)
                           VALUES (600, '600', 'completed', ?, '[]', 'recorded', 'pickup', 'DT', 'K7R4MX')""",
                        (_days_ago(400),))
            con.commit()
        response = self.code(' k7r-4mx ')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        found = response.get_json()
        self.assertEqual((found['found'], found['first_name'], found['collector'], found['needs_manager']),
                         (True, 'Private', None, False))
        self.assertEqual((found['order']['number'], found['order']['store'], found['order']['lines'][0]['name']),
                         ('701', 'DT', 'Bear 记账名'))
        self.assertNotIn('K7R4MX', response.get_data(as_text=True))
        for value in PRIVATE[1:]:
            self.assertNotIn(value, response.get_data(as_text=True))
        self.assertEqual(self.code('ACD346').get_json()['collector'], 'Sammy')
        # Never kept: the name is nowhere in the database.
        with closing(self.connect()) as con:
            dump = '\n'.join(con.iterdump())
        for value in (*PRIVATE, 'Sammy'):
            self.assertNotIn(value, dump)
        # Unknown, another store's, or closed on the website: said plainly, with nothing about the order.
        self.assertEqual(self.code('ZZZZZZ').get_json(), {'found': False, 'why': 'not_found'})
        self.assertEqual(self.code('MKP999').get_json(), {'found': False, 'why': 'other_store', 'store': 'MK'})
        self.assertEqual(self.code('HJK777').get_json()['found'], True)  # 707 waits for a manager but is handed over
        self.assertEqual(self.code('HJK777').get_json()['needs_manager'], True)
        self.woo.orders[701]['status'] = 'cancelled'  # a minute ago on the website; the sync has not read it yet
        self.assertEqual(self.code('K7R4MX').get_json(), {'found': False, 'why': 'cancelled'})
        self.assertNotIn('701', self.numbers(self.board()['columns']['pick']))
        self.woo.orders[706]['meta_data'].append({'key': '_popcore_pickup_code', 'value': 'AWT333'})
        self.woo.orders[706]['date_modified_gmt'] = _ago(0)
        self.run_sync()
        self.assertEqual(self.code('AWT333').get_json(), {'found': False, 'why': 'status', 'status': 'awaiting-stock'})
        self.assertEqual(self.code('', ).status_code, 400)

    def test_handed_over_needs_the_code(self):
        self.assertEqual(self.step(702, 'handed-over').get_json()['code'], 'code_needed')
        self.assertEqual(self.step(702, 'handed-over', code='ACD347').status_code, 403)
        self.assertEqual(self.woo.status_writes, [])
        self.assertEqual(self.step(702, 'handed-over', code='acd-346').get_json(), {'result': 'done', 'noted': True})
        self.assertEqual(self.woo.orders[702]['status'], 'completed')
        self.assertEqual(self.woo.notes[-1], (702, {'note': 'Handed over in popcore.store by Amy', 'customer_note': False}))
        board = self.board()
        self.assertEqual(self.numbers(board['columns']['ready']), [])
        self.assertEqual(sorted(self.numbers(board['done_today'])), ['702', '708'])  # within 30 s at most
        # Staff skipped Ready: a processing order is handed over too.
        self.assertEqual(self.step(701, 'handed-over', code='K7R4MX').get_json()['result'], 'done')
        self.assertEqual(self.step(701, 'handed-over', code='K7R4MX').get_json(), {'result': 'already', 'noted': None})
        self.assertEqual(self.woo.status_writes, [(702, 'completed'), (701, 'completed')])
        # The sync reads both changes back; nothing moves in stock.
        self.run_sync()
        self.assertEqual(self.numbers(self.board()['columns']['pick']), [])
        self.assertIsNone(self.state('last_error'))

    def test_orders_recorded_before_the_board_read_their_facts_once(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE website_orders SET pickup_code=NULL, delivery=NULL')
            con.commit()
        self.woo.reads.clear()
        self.run_sync()
        with closing(self.connect()) as con:
            rows = {row[0]: (row[1], row[2]) for row in con.execute(
                'SELECT wc_order_id, pickup_code, delivery FROM website_orders')}
        self.assertEqual(rows[701], ('K7R4MX', '{}'))
        self.assertEqual(json.loads(rows[703][1])['zone'], 'Downtown')
        self.assertEqual(rows[708], (None, None))  # done: never read again
        reads = [params['include'] for path, params in self.woo.reads if path == '/orders' and 'include' in params]
        self.assertEqual(reads, ['701,702,703,704,705,706,707,709'])
        self.woo.reads.clear()
        self.run_sync()
        self.assertFalse([params for path, params in self.woo.reads if 'include' in params and path == '/orders'])


class CounterAccountTests(BoardCase):
    """Owner, 2026-11-19: the store computer's account looks up products, stock and Dex prices but edits nothing, and
    uses the board's two buttons. It is a 查看者 Viewer with DT store operations access and 库存查看."""
    COUNTER = {'Authorization': 'Bearer viewer:counter'}  # auth0|counter

    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|counter', ?)", (self.dt,))
            con.execute("INSERT INTO inventory_viewers(auth0_sub, store_id) VALUES ('auth0|counter', ?)", (self.dt,))
            con.commit()

    def test_a_viewer_counter_account_works_the_board_and_reads_stock_but_changes_nothing(self):
        board = self.board(self.COUNTER)
        self.assertEqual((board['stores'], self.numbers(board['columns']['pick'])), (['DT'], ['701']))
        # Its buttons ask who is at the computer (owner, 2026-11-20): a name + PIN, and the note names that person.
        self.assertEqual(self.step(701, 'ready', headers=self.COUNTER).get_json()['code'], 'person_needed')
        self.assertEqual(self.client.post('/api/me/pin', json={'pin': '2580'}, headers=STAFF).status_code, 200)
        with closing(self.connect()) as con:
            amy = con.execute("SELECT id FROM employees WHERE auth0_id='auth0|staff'").fetchone()[0]
        person = {**self.COUNTER, 'X-Popcore-Person': f'{amy}:2580'}
        self.assertEqual(self.step(701, 'ready', headers=person).get_json(), {'result': 'done', 'noted': True})
        self.assertEqual(self.woo.notes[-1][1]['note'], 'Ready in popcore.store by Amy')
        self.assertEqual(self.code('K7R4MX', headers=self.COUNTER).get_json()['first_name'], 'Private')
        self.assertEqual(self.step(701, 'handed-over', code='K7R4MX', headers=person).get_json()['result'], 'done')
        # The menus learn where it has store operations access.
        stores = {row['code']: row for row in self.client.get('/api/stores', headers=self.COUNTER).get_json()}
        self.assertEqual((stores['DT']['operations'], stores['DT']['inventory_visible'], stores['MK']['operations']),
                         (True, True, False))
        # It reads DT's stock ...
        self.assertEqual(self.client.get('/api/inventory/series?store_code=DT', headers=self.COUNTER).status_code, 200)
        self.assertEqual(self.client.get('/api/inventory/overview?store_code=DT', headers=self.COUNTER).status_code, 200)
        # ... and changes none of it, whatever its store access; a name + PIN says who, never adds a permission.
        with closing(self.connect()) as con:
            version = con.execute("""SELECT version FROM inventory_balances WHERE product_id=? AND location_id=?
                                     AND disposition='saleable'""", (self.item, self.locations['upstairs'])).fetchone()[0]
        move = self.client.post('/api/inventory/commands', headers={**person, 'Idempotency-Key': 'counter-move'}, json={
            'kind': 'move', 'business_date': '2026-11-01', 'lines': [{
                'product_id': self.item, 'unit': 'piece', 'quantity': 1, 'from_location_id': self.locations['upstairs'],
                'from_disposition': 'saleable', 'to_location_id': self.locations['floor'], 'to_disposition': 'saleable',
                'expected_versions': {'from': version, 'to': 0}}]})
        self.assertEqual(move.status_code, 403, move.get_json())
        self.assertEqual(self.balance(self.item, 'upstairs'), 1)
        # Website settings stay with managers.
        self.assertEqual(self.client.get('/api/website/orders', headers=self.COUNTER).status_code, 403)

    def test_a_viewer_without_store_operations_access_gets_nothing_from_the_board(self):
        plain = {'Authorization': 'Bearer viewer'}
        self.assertEqual(self.board(plain)['stores'], [])
        self.assertEqual(self.step(701, 'ready', headers=plain).status_code, 403)
        self.assertEqual(self.code('K7R4MX', headers=plain).get_json(), {'found': False, 'why': 'other_store', 'store': 'DT'})
        self.assertEqual(self.woo.status_writes, [])
