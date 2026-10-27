"""Blind boxes over half a set: where they came from is asked before payment (owner, 2026-10-27).

开新端 a new sealed set, 已开的端 a set already opened on the floor, or 散盒 loose boxes from the floor.
A sale that still waits (card on Clover, or not enough loose boxes) is finished on its sale page."""
import os
from contextlib import closing
from unittest.mock import patch

from test_clover_checkout import ITEM, MERCHANT, FakeConnector, card, clover_order
from test_receiving import ReceivingFixture
import clover_checkout

BOX_PRICE = 1999


class BlindBoxFixture(ReceivingFixture):
    def setUp(self):
        super().setUp()
        clock = patch('checkout_access.business_date', return_value='2026-10-20')
        clock.start()
        self.addCleanup(clock.stop)
        with closing(self.connect()) as con:
            for subject, name in (('staff', 'Staff Cashier'), ('manager', 'Manager')):
                employee = con.execute('INSERT INTO employees(auth0_id,name) VALUES (?,?)', ('auth0|' + subject, name)).lastrowid
                con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                    VALUES (?,'2026-10-20','00:01','00:02','test',?)""", (employee, self.store_id))
            con.execute("INSERT INTO inventory_access(auth0_sub,store_id) VALUES ('auth0|manager',?)", (self.store_id,))
            self.set_id = con.execute("""INSERT INTO products(sku,name_cn_en,jizhanming,stock_form,stock_unit,identity_status)
                VALUES ('BOX-12-SET','Series Set','Series Set','sealed_set','set','verified')""").lastrowid
            self.box_id = con.execute("""INSERT INTO products(sku,name_cn_en,jizhanming,stock_form,stock_unit,identity_status,
                clover_item_id) VALUES ('BOX-12','Series Box','Series Box','random_box','box','verified',?)""", (ITEM,)).lastrowid
            self.conversion_id = con.execute("""INSERT INTO product_conversions(source_product_id,target_product_id,
                output_per_input,version) VALUES (?,?,12,1)""", (self.set_id, self.box_id)).lastrowid
            con.commit()
        self.stock(boxes=10, sets=2)
        environment = patch.dict(os.environ, {
            'CLOVER_CONNECTOR_URL': 'https://connector.test/clover-sandbox', 'CLOVER_CONNECTOR_PASSWORD': 'p' * 32,
            'CLOVER_CONNECTOR_WRITER_KEY': 'k' * 32, 'CLOVER_MERCHANT_ID': MERCHANT,
            'CLOVER_STORE_ID': str(self.store_id), 'CLOVER_IMPORT_SINCE': '2026-10-01', 'CLOVER_INSTANCE': 'pc'})
        environment.start()
        self.addCleanup(environment.stop)
        self.clover = FakeConnector()
        connector = patch('clover_checkout.requests.request', side_effect=self.clover)
        connector.start()
        self.addCleanup(connector.stop)

    def stock(self, *, boxes, sets):
        with closing(self.connect()) as con:
            for product_id, quantity in ((self.box_id, boxes), (self.set_id, sets)):
                con.execute("""INSERT INTO inventory_balances(product_id,location_id,disposition,quantity,version)
                    VALUES (?,?,'saleable',?,1)""", (product_id, self.floor, quantity))
            con.commit()

    def one(self, sql, *args):
        with closing(self.connect()) as con:
            return con.execute(sql, args).fetchone()

    def floor_stock(self, product_id):
        return self.one("SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=? AND disposition='saleable'",
                        product_id, self.floor)[0]

    def open_sets(self):
        with closing(self.connect()) as con:
            return [tuple(row) for row in con.execute('SELECT id,remaining_qty FROM inventory_open_sets ORDER BY id')]

    def post(self, path, body, key, role='staff', expect=200):
        response = self.client.post(path, json=body, headers={**self.headers(role), 'Idempotency-Key': key})
        self.assertEqual(response.status_code, expect, response.get_json())
        return response.get_json()

    def manual(self, quantity=7, key='create-1', **line):
        total = BOX_PRICE * quantity
        return self.post('/api/checkouts', dict(
            store_id=self.store_id, business_date='2026-10-20', subtotal_cents=total, source_tax_cents=0,
            gross_cents=total, reduction_cents=0, rounding_cents=0, collected_cents=total,
            lines=[dict(product_id=self.box_id, unit='box', quantity=quantity, unit_price_cents=BOX_PRICE, **line)]),
            key, expect=201)

    def choose(self, order, key, choice, role='staff', expect=200, **extra):
        return self.post(f"/api/checkouts/{order['id']}/boxes", {'expected_version': order['version'],
                         'product_id': self.box_id, 'choice': choice, **extra}, key, role=role, expect=expect)

    def receive(self, order, key, tender, amount, expect=200, **extra):
        return self.post(f"/api/checkouts/{order['id']}/receive", {'tender': tender, 'amount_cents': amount, 'plan': 'pay',
                         'expected_version': order['version'], **extra}, key, expect=expect)

    def sale(self, sale_id):
        return dict(self.one('SELECT * FROM sale_documents WHERE id=?', sale_id))

    def allocate(self, sale, mappings, key, expect=200):
        return self.post(f"/api/sale-documents/{sale['id']}/allocate", {
            'expected_version': sale['version'], 'reason': 'Checked the shelf', 'mappings': mappings}, key,
            role='manager', expect=expect)


class ManualCheckoutBlindBoxTests(BlindBoxFixture):
    def test_the_cashier_says_where_the_boxes_came_from_before_payment(self):
        order = self.manual()
        (asked,) = order['box_choices']
        self.assertEqual((order['box_choice_required'], order['pricing']['can_receive']), (True, False))
        self.assertEqual((asked['quantity'], asked['set_size'], asked['choice'], asked['from_display']), (7, 12, None, False))
        self.assertEqual(asked['options'], {
            'set_size': 12, 'open_sets': [], 'loose_units': 10,
            'fresh_set': {'product_id': self.set_id, 'conversion_id': self.conversion_id, 'conversion_factor': 12,
                          'name': 'Series Set', 'sets_on_hand': 2}})
        refused = self.receive(order, 'too-early', 'card', 7 * BOX_PRICE, expect=409)
        self.assertEqual(refused['code'], 'box_choice_required')
        self.choose(order, 'nowhere', 'somewhere', expect=400)
        self.choose(order, 'display-set', 'fresh_set', from_display=True, expect=400)
        loose = self.choose(order, 'loose', 'loose')
        self.assertEqual((loose['box_choice_required'], loose['pricing']['can_receive'], loose['box_choices'][0]['choice']),
                         (False, True, 'loose'))
        self.assertEqual(self.choose(order, 'loose', 'loose')['version'], loose['version'])  # a retry changes nothing
        done = self.receive(loose, 'card', 'card', 7 * BOX_PRICE, expected_due_cents=7 * BOX_PRICE)
        sale = self.sale(done['sale_id'])
        self.assertEqual((done['status'], sale['allocation_status']), ('completed', 'allocated'))
        self.assertEqual(self.one('SELECT loose_boxes FROM sale_lines WHERE sale_id=?', sale['id'])[0], 1)
        self.assertEqual((self.floor_stock(self.box_id), self.floor_stock(self.set_id), self.open_sets()), (3, 2, []))

    def test_a_new_sealed_set_is_opened_even_after_other_sales_of_the_box(self):
        order = self.choose(self.manual(), 'fresh', 'fresh_set')
        self.assertEqual(order['box_choices'][0]['choice'], 'fresh_set')
        # Another sale of the same box between the answer and the payment.
        other = self.manual(quantity=1, key='create-2')
        self.receive(other, 'card-other', 'card', BOX_PRICE)
        done = self.receive(order, 'card', 'card', 7 * BOX_PRICE)
        self.assertEqual(self.sale(done['sale_id'])['allocation_status'], 'allocated')
        (opened,) = self.open_sets()
        self.assertEqual((self.floor_stock(self.set_id), opened[1], self.floor_stock(self.box_id)), (1, 5, 10 - 1 + 12 - 7))

    def test_an_opened_set_or_loose_boxes_and_a_short_sale_is_finished_on_its_sale_page(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE inventory_balances SET quantity=3 WHERE product_id=?', (self.box_id,))
            con.commit()
        # A set opened earlier for one box keeps 11: three loose boxes are left beside it.
        first = self.manual(quantity=1, key='create-1', fresh_set={
            'product_id': self.set_id, 'conversion_id': self.conversion_id, 'conversion_factor': 12})
        self.receive(first, 'card-1', 'card', BOX_PRICE)
        (opened,) = self.open_sets()
        order = self.manual(key='create-2')
        options = order['box_choices'][0]['options']
        self.assertEqual((options['open_sets'], options['loose_units']), ([{'id': opened[0], 'remaining_qty': 11}], 3))
        self.choose(order, 'no-set', 'open_set', expect=400)
        # Loose boxes never take what an opened set keeps: the paid sale waits, short of stock.
        loose = self.choose(order, 'loose', 'loose')
        done = self.receive(loose, 'card-2', 'card', 7 * BOX_PRICE)
        sale = self.sale(done['sale_id'])
        self.assertEqual((done['status'], sale['allocation_status'], sale['allocation_reason']),
                         ('completed', 'pending', 'insufficient_stock'))
        self.assertEqual(self.open_sets(), [(opened[0], 11)])
        # The manager finishes it from the opened set.
        allocated = self.allocate(sale, [{'line_no': 1, 'product_id': self.box_id, 'open_set_id': opened[0],
                                          'fresh_set': None, 'loose_boxes': False}], 'allocate-1')
        self.assertEqual(allocated['allocation_status'], 'allocated')
        self.assertEqual(self.open_sets(), [(opened[0], 4)])
        self.assertEqual(self.one('SELECT loose_boxes,open_set_id FROM sale_lines WHERE sale_id=?', sale['id'])[:],
                         (0, opened[0]))


class CloverBlindBoxTests(BlindBoxFixture):
    def queue(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE clover_feed SET next_poll=0')
            con.commit()
        response = self.client.get(f'/api/checkouts?store_id={self.store_id}&view=live', headers=self.headers())
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def detail(self, checkout_id):
        with closing(self.connect()) as con:
            con.execute('UPDATE clover_orders SET fetch_next_at=0')
            con.commit()
        return self.client.get(f'/api/checkouts/{checkout_id}', headers=self.headers()).get_json()

    def test_a_clover_order_asks_before_money_and_keeps_the_answer_when_clover_changes(self):
        self.clover.put(clover_order(prices=(BOX_PRICE,) * 7, total=7 * BOX_PRICE, mapped='all'))
        order = self.detail(self.queue()['orders'][0]['id'])
        self.assertEqual((order['box_choice_required'], order['clover']['can_receive']), (True, False))
        cash = order['clover']['collect']['pay']['cash_rest_cents']
        refused = self.receive(order, 'cash-early', 'cash', cash, expect=409)
        self.assertEqual(refused['code'], 'box_choice_required')
        answered = self.choose(order, 'loose', 'loose')
        self.assertEqual((answered['clover']['register'], answered['cashier_name']), (False, 'Staff Cashier'))
        # An eighth box scanned on Clover: the answer stays with the product.
        self.clover.put(clover_order(prices=(BOX_PRICE,) * 8, total=8 * BOX_PRICE, mapped='all'))
        self.queue()
        changed = self.detail(order['id'])
        self.assertEqual((changed['box_choices'][0]['quantity'], changed['box_choices'][0]['choice'],
                          changed['clover']['can_receive']), (8, 'loose', True))
        cash = changed['clover']['collect']['pay']['cash_rest_cents']
        done = self.receive(changed, 'cash', 'cash', cash, expected_due_cents=changed['clover']['collect']['pay']['due_cents'])
        self.assertEqual((done['status'], self.sale(done['sale_id'])['allocation_status']), ('completed', 'allocated'))
        self.assertEqual(self.floor_stock(self.box_id), 2)

    def test_card_on_clover_without_an_answer_waits_and_the_sale_page_finishes_it(self):
        self.clover.put(clover_order(prices=(BOX_PRICE,) * 7, total=7 * BOX_PRICE, mapped='all',
                                     payments=[card('CARDPAYMENT01', 7 * BOX_PRICE)], state='PAID'))
        with closing(self.connect()) as con:
            clover_checkout.ensure_feed(con, clover_checkout.config())
        clover_checkout.background_sync()
        checkout = self.one('SELECT * FROM checkout_orders')
        sale = self.sale(checkout['sale_id'])
        self.assertEqual((checkout['status'], sale['allocation_status'], sale['allocation_reason']),
                         ('completed', 'pending', 'fresh_set_selection_required'))
        options = self.client.get(f'/api/sale-documents/box-options?store_id={self.store_id}&product_id={self.box_id}',
                                  headers=self.headers('manager')).get_json()['options']
        self.assertEqual((options['set_size'], options['loose_units'], options['fresh_set']['sets_on_hand']), (12, 10, 2))
        other = self.client.get(f'/api/sale-documents/box-options?store_id={self.store_id}&product_id={self.product_id}',
                                headers=self.headers('manager')).get_json()
        self.assertEqual(other, {'options': None})  # not a blind box with a sealed set
        self.allocate(sale, [{'line_no': 1, 'product_id': self.box_id, 'fresh_set': {
            'product_id': self.set_id, 'conversion_id': self.conversion_id, 'conversion_factor': 12}}], 'allocate-1')
        self.assertEqual(self.sale(sale['id'])['allocation_status'], 'allocated')
        self.assertEqual((self.floor_stock(self.set_id), self.open_sets()[0][1]), (1, 5))


class RecordSaleBlindBoxTests(BlindBoxFixture):
    def sale_body(self, reference, entry_mode, **line):
        total = 7 * BOX_PRICE
        return {'store_id': self.store_id, 'business_date': '2026-10-20', 'entry_mode': entry_mode,
                'source': {'system': 'manual', 'account': 'DT', 'reference': reference},
                'subtotal_cents': total, 'source_tax_cents': 0, 'gross_cents': total, 'reduction_cents': 0,
                'rounding_cents': 0, 'collected_cents': total,
                'lines': [{'product_id': self.box_id, 'unit': 'box', 'quantity': 7, 'unit_price_cents': BOX_PRICE, **line}]}

    def test_record_sale_takes_loose_boxes_and_a_pending_sale_takes_an_answer(self):
        # 补录 Record sale: the draft keeps the answer until it is recorded.
        draft = self.post('/api/sale-documents', self.sale_body('LOOSE-1', 'planned_entry', loose_boxes=True), 'loose-create',
                           expect=201)
        posted = self.post(f"/api/sale-documents/{draft['sale_id']}/post", {'expected_version': draft['version']}, 'loose-post')
        self.assertEqual(posted['allocation_status'], 'allocated')
        self.assertEqual(self.floor_stock(self.box_id), 3)
        self.post('/api/sale-documents', self.sale_body('BOTH', 'planned_entry', loose_boxes=True, open_set_id=1),
                  'both-create', expect=400)
        # A paid sale with no answer waits; the page that could not finish it before now can.
        self.stock_up(7)
        draft = self.post('/api/sale-documents', self.sale_body('PAID-1', 'already_paid'), 'paid-create', expect=201)
        pending = self.post(f"/api/sale-documents/{draft['sale_id']}/post", {'expected_version': draft['version']}, 'paid-post')
        self.assertEqual(pending['unresolved_reasons'], ['fresh_set_selection_required'])
        sale = self.sale(draft['sale_id'])
        self.allocate(sale, [{'line_no': 1, 'product_id': self.box_id}], 'no-answer', expect=409)
        done = self.allocate(sale, [{'line_no': 1, 'product_id': self.box_id, 'loose_boxes': True}], 'loose-answer')
        self.assertEqual(done['allocation_status'], 'allocated')
        self.assertEqual(self.floor_stock(self.box_id), 3)

    def stock_up(self, boxes):
        with closing(self.connect()) as con:
            con.execute('UPDATE inventory_balances SET quantity=quantity+?,version=version+1 WHERE product_id=?',
                        (boxes, self.box_id))
            con.commit()
