"""补货 brings whole sets down as boxes (owner, 2026-10-29: the floor keeps blind boxes as boxes only;
2026-11-06: a 整端 is not tracked as a set once it is downstairs). A sealed set received on a floor lands
as the boxes its conversion records, loose and ready to sell. The picker may record more than the sheet
asked for, and the floor counts what really came down, below or above the pick (owner, 2026-11-15)."""
from contextlib import closing

from test_receiving import ReceivingFixture


class SetsFixture(ReceivingFixture):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            product = lambda sku, name, form, unit: con.execute(  # noqa: E731
                """INSERT INTO products(sku, name_cn_en, jizhanming, product_type, stock_form, stock_unit,
                                        identity_status)
                   VALUES (?, ?, ?, '盲盒', ?, ?, 'verified')""", (sku, name, name, form, unit)).lastrowid
            self.box = product('PM0182', '小野雾中人', 'random_box', 'box')
            self.set = product('PM0182-SET', '小野雾中人 整端', 'sealed_set', 'set')
            self.conversion = con.execute(
                """INSERT INTO product_conversions(source_product_id, target_product_id, output_per_input, version)
                   VALUES (?, ?, 12, 1)""", (self.set, self.box)).lastrowid
            self.back = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='upstairs'",
                                    (self.store_id,)).fetchone()[0]
            for item, quantity in ((self.set, 3), (self.box, 5), (self.product_id, 10)):
                con.execute("""INSERT INTO inventory_balances (product_id, location_id, disposition, quantity, version)
                               VALUES (?, ?, 'saleable', ?, 1)""", (item, self.back, quantity))
            self.session_id = con.execute(
                "INSERT INTO restock_sessions(date, status, store_id) VALUES ('2026-11-14', 'picking', ?)",
                (self.store_id,)).lastrowid
            # Line 1 one whole set, line 2 two loose boxes, line 3 four of five pieces.
            self.items = [con.execute(
                """INSERT INTO restock_items (session_id, product_id, requested_qty, found_qty, pick_status)
                   VALUES (?, ?, ?, ?, 'found')""", (self.session_id, item, asked, found)).lastrowid
                for item, asked, found in ((self.set, 1, 1), (self.box, 2, 2), (self.product_id, 5, 4))]
            con.commit()

    def act(self, action, key, version, lines, **extra):
        return self.client.post(f'/api/restock/session/{self.session_id}/{action}',
                                headers={**self.headers(), 'Idempotency-Key': key},
                                json={'expected_version': version, 'lines': lines, **extra})

    def pick(self, lines=None):
        picked = self.act('pick', 'pick', 1, lines or [
            {'line_no': 1, 'quantity': 1}, {'line_no': 2, 'quantity': 2}, {'line_no': 3, 'quantity': 4}])
        self.assertEqual(picked.status_code, 200, picked.get_json())

    def balances(self):
        with closing(self.connect()) as con:
            return {(row['product_id'], row['location_id'], row['disposition']): row['quantity']
                    for row in con.execute('SELECT * FROM inventory_balances') if row['quantity']}

    def sheet(self):
        return self.client.get(f'/api/restock/session/{self.session_id}', headers=self.headers()).get_json()

    def value(self, sql, *args):
        with closing(self.connect()) as con:
            return con.execute(sql, args).fetchone()[0]


class RestockSetsAsBoxesTests(SetsFixture):
    def test_a_restocked_set_arrives_on_the_floor_as_boxes(self):
        self.pick()
        in_transit = self.sheet()['delivery']['lines'][0]
        self.assertEqual((in_transit['boxes_per_set'], in_transit['landed_boxes']), (12, 0))
        received = self.act('receive', 'receive', 2, [
            {'line_no': 1, 'quantity': 1}, {'line_no': 2, 'quantity': 2}, {'line_no': 3, 'quantity': 4}])
        self.assertEqual(received.status_code, 200, received.get_json())
        self.assertEqual(received.get_json()['landed_as_boxes'], [
            {'line_no': 1, 'product_id': self.set, 'sets': 1, 'box_product_id': self.box, 'boxes': 12}])
        # One set became 12 boxes beside the 2 loose ones; no sealed set is on the floor or left in transit.
        self.assertEqual(self.balances(), {
            (self.set, self.back, 'saleable'): 2, (self.box, self.back, 'saleable'): 3,
            (self.product_id, self.back, 'saleable'): 6,
            (self.box, self.floor, 'saleable'): 14, (self.product_id, self.floor, 'saleable'): 4})
        sheet = self.sheet()
        self.assertEqual(sheet['status'], 'picking')  # one piece asked for was not found: closed with a reason
        lines = sheet['delivery']['lines']
        self.assertEqual([(line['received_quantity'], line['outstanding_transit']) for line in lines],
                         [(1, 0), (2, 0), (4, 0)])
        self.assertEqual([(line['boxes_per_set'], line['landed_boxes']) for line in lines],
                         [(12, 12), (None, 0), (None, 0)])
        with closing(self.connect()) as con:
            # The screens that still read the legacy stock row see the same floor.
            stock = {row['product_id']: (row['upstairs_qty'], row['instore_qty']) for row in con.execute(
                'SELECT * FROM stock WHERE store_id=?', (self.store_id,))}
            self.assertEqual((stock[self.set], stock[self.box]), ((2, 0), (3, 14)))
            # The boxes are loose: nothing keeps them as an opened set, so a sale takes them without asking.
            self.assertEqual(con.execute('SELECT COUNT(*) FROM inventory_open_sets').fetchone()[0], 0)
            # One document did it, and its set line records the conversion it used.
            line = con.execute(
                """SELECT l.* FROM inventory_document_lines l WHERE l.document_id=? AND l.product_id=?""",
                (received.get_json()['inventory_document_id'], self.set)).fetchone()
            self.assertEqual((line['native_unit'], line['quantity'], line['conversion_id'], line['conversion_factor']),
                             ('set', 1, self.conversion, 12))
            moved = {(row['product_id'], row['location_id'], row['disposition']): row['quantity']
                     for row in con.execute('SELECT * FROM inventory_movements WHERE document_id=? AND line_no=?',
                                            (line['document_id'], line['line_no']))}
            self.assertEqual(moved, {(self.set, self.back, 'transit'): -1, (self.box, self.floor, 'saleable'): 12})

    def test_a_resent_receive_does_not_move_twice(self):
        self.pick()
        lines = [{'line_no': 1, 'quantity': 1}]
        first = self.act('receive', 'receive-once', 2, lines)
        self.assertEqual(first.status_code, 200, first.get_json())
        after = self.balances()
        self.assertEqual(after[(self.box, self.floor, 'saleable')], 12)
        again = self.act('receive', 'receive-once', 2, lines)
        self.assertEqual((again.status_code, again.get_json()), (200, first.get_json()))
        # The same press with a new key meets the delivery's new version and moves nothing either.
        stale = self.act('receive', 'receive-twice', 2, lines)
        self.assertEqual((stale.status_code, stale.get_json()['code']), (409, 'delivery_state_conflict'))
        self.assertEqual(self.balances(), after)
        self.assertEqual(self.value("""SELECT COUNT(*) FROM inventory_delivery_events e JOIN inventory_deliveries d
                                       ON d.id=e.delivery_id WHERE d.restock_session_id=? AND e.action='receive'""",
                                    self.session_id), 1)

    def test_the_picker_may_record_more_than_the_sheet_asked(self):
        with closing(self.connect()) as con:
            con.execute("UPDATE restock_items SET pick_status='pending', found_qty=NULL WHERE id=?", (self.items[0],))
            con.commit()
        too_many = self.client.patch(f'/api/restock/items/{self.items[0]}/pick', headers=self.headers(),
                                     json={'pick_status': 'found', 'found_qty': 1000})
        self.assertEqual(too_many.status_code, 400, too_many.get_json())
        saved = self.client.patch(f'/api/restock/items/{self.items[0]}/pick', headers=self.headers(),
                                  json={'pick_status': 'found', 'found_qty': 2})
        self.assertEqual((saved.status_code, saved.get_json()['found_qty']), (200, 2))
        self.pick([{'line_no': 1, 'quantity': 2}, {'line_no': 2, 'quantity': 2}, {'line_no': 3, 'quantity': 4}])
        sheet = self.sheet()
        # The sheet still says 1 was asked for; the delivery carries the 2 that came down.
        self.assertEqual((sheet['items'][0]['requested_qty'], sheet['items'][0]['found_qty']), (1, 2))
        line = sheet['delivery']['lines'][0]
        self.assertEqual((line['requested_quantity'], line['dispatched_quantity'], line['outstanding_transit']), (2, 2, 2))
        received = self.act('receive', 'receive-two', 2, [{'line_no': 1, 'quantity': 2}])
        self.assertEqual(received.status_code, 200, received.get_json())
        balances = self.balances()
        self.assertEqual((balances[(self.set, self.back, 'saleable')], balances[(self.box, self.floor, 'saleable')]),
                         (1, 24))

    def test_picking_more_than_upstairs_holds_is_refused_when_the_pick_is_confirmed(self):
        self.client.patch(f'/api/restock/items/{self.items[0]}/pick', headers=self.headers(),
                          json={'pick_status': 'found', 'found_qty': 4})
        before = self.balances()
        refused = self.act('pick', 'pick-four', 1, [{'line_no': 1, 'quantity': 4}])
        self.assertEqual((refused.status_code, refused.get_json()['code']), (409, 'insufficient_stock'))
        self.assertEqual(self.balances(), before)
        self.assertEqual(self.value('SELECT COUNT(*) FROM inventory_deliveries'), 0)

    def test_the_floor_counts_more_than_the_pick_recorded(self):
        self.pick()
        # The pick said 1 set and 2 boxes; 2 sets and 3 boxes are standing on the floor.
        received = self.act('receive', 'count-more', 2, [
            {'line_no': 1, 'quantity': 2}, {'line_no': 2, 'quantity': 3}, {'line_no': 3, 'quantity': 4}])
        self.assertEqual(received.status_code, 200, received.get_json())
        self.assertEqual(self.balances(), {
            (self.set, self.back, 'saleable'): 1, (self.box, self.back, 'saleable'): 2,
            (self.product_id, self.back, 'saleable'): 6,
            (self.box, self.floor, 'saleable'): 27, (self.product_id, self.floor, 'saleable'): 4})
        sheet = self.sheet()
        self.assertEqual([(item['requested_qty'], item['found_qty']) for item in sheet['items']], [(1, 2), (2, 3), (5, 4)])
        self.assertEqual([(line['requested_quantity'], line['dispatched_quantity'], line['received_quantity'],
                           line['outstanding_transit']) for line in sheet['delivery']['lines']],
                         [(2, 2, 2, 0), (3, 3, 3, 0), (5, 4, 4, 0)])
        with closing(self.connect()) as con:
            events = [(row['action'], row['reason'], row['quantity']) for row in con.execute(
                """SELECT e.action, e.reason, SUM(l.quantity) AS quantity FROM inventory_delivery_events e
                   JOIN inventory_delivery_event_lines l ON l.event_id=e.id GROUP BY e.id ORDER BY e.id""")]
        self.assertEqual(events, [('dispatch', None, 7),
                                  ('dispatch', 'Counted on the floor above the recorded pick', 2),
                                  ('receive', None, 9)])
        # Counting the same again is the same request: nothing more comes off upstairs.
        again = self.act('receive', 'count-more', 2, [
            {'line_no': 1, 'quantity': 2}, {'line_no': 2, 'quantity': 3}, {'line_no': 3, 'quantity': 4}])
        self.assertEqual(again.get_json(), received.get_json())
        self.assertEqual(self.balances()[(self.box, self.floor, 'saleable')], 27)

    def test_a_line_the_picker_marked_out_of_stock_can_still_be_counted_on_the_floor(self):
        with closing(self.connect()) as con:
            con.execute("UPDATE restock_items SET pick_status='not_found', found_qty=0 WHERE id=?", (self.items[0],))
            con.commit()
        self.pick([{'line_no': 2, 'quantity': 2}, {'line_no': 3, 'quantity': 4}])
        received = self.act('receive', 'count-unpicked', 2, [{'line_no': 1, 'quantity': 1}])
        self.assertEqual(received.status_code, 200, received.get_json())
        item = self.sheet()['items'][0]
        self.assertEqual((item['pick_status'], item['found_qty']), ('found', 1))
        self.assertEqual(self.balances()[(self.box, self.floor, 'saleable')], 12)

    def test_counting_more_than_upstairs_holds_moves_nothing(self):
        self.pick()
        before = self.balances()
        refused = self.act('receive', 'count-too-many', 2, [{'line_no': 1, 'quantity': 4}])
        self.assertEqual((refused.status_code, refused.get_json()['code']), (409, 'insufficient_stock'))
        self.assertEqual(self.balances(), before)
        self.assertEqual(self.sheet()['delivery']['lines'][0]['dispatched_quantity'], 1)

    def test_fewer_than_picked_stay_in_transit_until_they_go_back_upstairs(self):
        self.pick()
        received = self.act('receive', 'count-fewer', 2, [{'line_no': 2, 'quantity': 1}])
        self.assertEqual(received.status_code, 200, received.get_json())
        self.assertEqual(self.sheet()['delivery']['lines'][1]['outstanding_transit'], 1)
        returned = self.act('return', 'back-up', 3, [{'line_no': 2, 'quantity': 1}])
        self.assertEqual(returned.status_code, 200, returned.get_json())
        balances = self.balances()
        self.assertEqual((balances[(self.box, self.back, 'saleable')], balances[(self.box, self.floor, 'saleable')]),
                         (4, 1))
        self.assertNotIn((self.box, self.back, 'transit'), balances)

    def test_a_set_whose_size_was_never_recorded_is_not_received(self):
        self.pick()
        with closing(self.connect()) as con:
            con.execute('DELETE FROM product_conversions WHERE id=?', (self.conversion,))
            con.commit()
        before = self.balances()
        self.assertIsNone(self.sheet()['delivery']['lines'][0]['boxes_per_set'])
        refused = self.act('receive', 'no-size', 2, [{'line_no': 1, 'quantity': 1}])
        self.assertEqual((refused.status_code, refused.get_json()['code']), (409, 'set_size_unknown'))
        self.assertEqual(self.balances(), before)
        # It can still go back upstairs, and the other lines are received as usual.
        returned = self.act('return', 'no-size-back', 2, [{'line_no': 1, 'quantity': 1}])
        self.assertEqual(returned.status_code, 200, returned.get_json())
        self.assertEqual(self.balances()[(self.set, self.back, 'saleable')], 3)

    def test_a_set_received_as_held_stays_a_set(self):
        self.pick()
        held = self.act('receive', 'held', 2, [{'line_no': 1, 'quantity': 1, 'disposition': 'hold'}])
        self.assertEqual(held.status_code, 200, held.get_json())
        self.assertNotIn('landed_as_boxes', held.get_json())
        balances = self.balances()
        self.assertEqual(balances[(self.set, self.floor, 'hold')], 1)
        self.assertNotIn((self.box, self.floor, 'saleable'), balances)

    def test_completing_a_sheet_that_never_started_a_delivery_lands_sets_as_boxes(self):
        completed = self.client.post(f'/api/restock/session/{self.session_id}/complete', headers=self.headers())
        self.assertEqual(completed.status_code, 200, completed.get_json())
        expected = {
            (self.set, self.back, 'saleable'): 2, (self.box, self.back, 'saleable'): 3,
            (self.product_id, self.back, 'saleable'): 6,
            (self.box, self.floor, 'saleable'): 14, (self.product_id, self.floor, 'saleable'): 4}
        self.assertEqual(self.balances(), expected)
        self.assertEqual(self.sheet()['status'], 'completed')
        again = self.client.post(f'/api/restock/session/{self.session_id}/complete', headers=self.headers())
        self.assertEqual((again.status_code, again.get_json()), (200, completed.get_json()))
        self.assertEqual(self.balances(), expected)


class TransferSetsAsBoxesTests(SetsFixture):
    """A transfer into a floor follows the same rule; one into storage keeps whole sets whole."""

    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.mk_id = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            self.warehouse = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='warehouse'",
                                         (self.mk_id,)).fetchone()[0]
            con.executemany("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)",
                            [(self.store_id,), (self.mk_id,)])
            con.execute('UPDATE inventory_scope_state SET opening_verified=1')
            con.commit()

    def transfer(self, destination, key):
        manager = self.headers('manager')
        created = self.client.post('/api/goods/transfers', headers={**manager, 'Idempotency-Key': f'{key}-create'}, json={
            'source_location_id': self.back, 'destination_location_id': destination, 'business_date': '2026-11-14',
            'lines': [{'product_id': self.set, 'unit': 'set', 'requested_quantity': 1}]})
        self.assertEqual(created.status_code, 201, created.get_json())
        delivery = created.get_json()['id']
        dispatched = self.client.post(f'/api/goods/transfers/{delivery}/dispatch',
                                      headers={**manager, 'Idempotency-Key': f'{key}-dispatch'},
                                      json={'expected_version': 1, 'lines': [{'line_no': 1, 'quantity': 1}]})
        self.assertEqual(dispatched.status_code, 200, dispatched.get_json())
        return delivery

    def receive(self, delivery, key, quantity=1):
        return self.client.post(f'/api/goods/transfers/{delivery}/receive',
                                headers={**self.headers('manager'), 'Idempotency-Key': key},
                                json={'expected_version': 2, 'lines': [{'line_no': 1, 'quantity': quantity}]})

    def test_a_transfer_into_a_floor_lands_its_set_as_boxes(self):
        delivery = self.transfer(self.floor, 'to-floor')
        received = self.receive(delivery, 'to-floor-receive')
        self.assertEqual(received.status_code, 200, received.get_json())
        balances = self.balances()
        self.assertEqual(balances[(self.box, self.floor, 'saleable')], 12)
        self.assertNotIn((self.set, self.floor, 'saleable'), balances)

    def test_a_transfer_into_storage_keeps_the_set_whole(self):
        delivery = self.transfer(self.warehouse, 'to-warehouse')
        received = self.receive(delivery, 'to-warehouse-receive')
        self.assertEqual(received.status_code, 200, received.get_json())
        self.assertNotIn('landed_as_boxes', received.get_json())
        self.assertEqual(self.balances()[(self.set, self.warehouse, 'saleable')], 1)

    def test_a_transfer_is_never_received_above_what_was_sent(self):
        delivery = self.transfer(self.floor, 'above-sent')
        before = self.balances()
        refused = self.receive(delivery, 'above-sent-receive', quantity=2)
        self.assertEqual((refused.status_code, refused.get_json()['code']), (409, 'delivery_quantity_conflict'))
        self.assertEqual(self.balances(), before)
