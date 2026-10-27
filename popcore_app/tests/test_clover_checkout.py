"""Clover orders as native checkouts: import, pricing, settlement, write-back, closing."""
import json
import os
import time
from contextlib import closing
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import requests

from test_receiving import ReceivingFixture
import clover_checkout
import db
from blueprints import settings
from checkout_access import REGISTER_SUB

MERCHANT = 'M' * 13
CREATED = int(datetime(2026, 10, 20, 12, 0, tzinfo=ZoneInfo('America/Toronto')).timestamp() * 1000)
ITEM = 'ITEMAAAAAAAAA'


def response(status, payload, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    result.headers.update(headers or {})
    return result


def clover_order(order_id='ORDERAAAAAAAA', *, prices=(1770, 1999), total=4259, payments=(), source='cloud',
                 state='OPEN', discounts=(), refunds=(), created=CREATED, mapped=True):
    items = [{'id': f'LINE{n:09d}', 'name': f'Item {n}', 'price': price, 'itemId': ITEM if mapped == 'all' or (mapped and n == 0) else None}
             for n, price in enumerate(prices)]
    return {'id': order_id, 'currency': 'CAD', 'source': source, 'total': total, 'createdTime': created,
            'paymentState': state if source == 'cloud' else 'OPEN', 'items': items, 'discounts': list(discounts),
            'payments': [] if source == 'device' else list(payments), 'refunds': list(refunds), 'credits': [],
            'seen_at': time.time(), 'employeeName': 'Mason'}


def card(payment_id, amount):
    return {'id': payment_id, 'amount': amount, 'result': 'SUCCESS', 'tenderLabel': 'Credit Card',
            'tenderLabelKey': 'com.clover.tender.credit_card', 'taxAmount': 0}


class FakeConnector:
    """The POPCORE side of the connector contract, with Clover's state kept in memory."""

    def __init__(self):
        self.orders, self.device, self.calls, self.verified = {}, {}, [], True
        self.fail = {}  # route -> (status, payload)
        self.deleted = set()
        self.sent = {}  # POPCORE send key -> Clover order ID
        self.lose_reply = False  # create the order on Clover, then fail the reply

    def put(self, *orders):
        """Cloud orders are Clover's record; a device snapshot only overlays the feed."""
        for order in orders:
            order['seen_at'] = time.time()
            if order['source'] == 'device':
                self.device[order['id']] = order
            else:
                self.device.pop(order['id'], None)
                self.orders[order['id']] = order

    def feed(self):
        rows = {**self.orders, **self.device}
        return [row for order_id, row in rows.items() if order_id not in self.deleted]

    def __call__(self, method, url, **kwargs):
        path = url.split('/clover-sandbox', 1)[1]
        self.calls.append((method, path, kwargs.get('json'), kwargs.get('headers') or {}))
        route = method + ' ' + path.split('?')[0].rsplit('/', 1)[-1] if path.count('/') > 2 else method + ' ' + path
        for key in (route, method + ' ' + path):
            if key in self.fail:
                status, payload = self.fail[key]
                return response(status, payload)
        base = {'environment': 'sandbox', 'merchant_id': MERCHANT}
        if method == 'POST' and path == '/orders/create':
            body = kwargs['json']
            order_id = self.sent.get(body['key'])
            if order_id is None:
                order_id = self.sent[body['key']] = f'SENT{len(self.sent):09d}'
                units = [line for line in body['lines'] for _ in range(line['quantity'])]
                items = [{'id': f'LN{order_id[-6:]}{n:05d}', 'name': line['name'], 'price': line['price'], 'itemId': None}
                         for n, line in enumerate(units)]
                self.orders[order_id] = {
                    'id': order_id, 'currency': 'CAD', 'source': 'cloud', 'total': body['expected_total'],
                    'createdTime': int(time.time() * 1000), 'paymentState': 'OPEN', 'items': items,
                    'discounts': [{'id': 'AGREED0000001', 'name': body['discount']['name'],
                                   'amount': -body['discount']['amount']}] if body.get('discount') else [],
                    'payments': [], 'refunds': [], 'credits': [], 'seen_at': time.time(),
                    'externalReferenceId': body['reference'], 'title': body['title'], 'employeeName': None}
            if self.lose_reply:
                return response(502, {'error': 'Clover could not be read. Retry or reconnect.', 'code': 'clover_unavailable'})
            return response(200, {**base, 'created': True, 'order': {**self.orders[order_id], 'seen_at': time.time()},
                                  'pricing': {'verified': True, 'tax_cents': 0}})
        if method == 'GET' and path == '/orders':
            return response(200, {**base, 'fetched_at': time.time(), 'cloud_unavailable': False, 'orders': self.feed()})
        if method == 'GET' and path == '/orders/scan':
            return response(200, {**base, 'fetched_at': time.time(), 'more': False,
                                  'orders': [o for o in self.orders.values() if o['id'] not in self.deleted]})
        order_id = path.split('/')[2] if path.startswith('/orders/') else None
        if method == 'GET' and order_id:
            if order_id in self.deleted or order_id not in self.orders:
                return response(404, {'error': 'Clover has no such order.', 'code': 'not_found'})
            row = {**self.orders[order_id], 'source': 'cloud', 'seen_at': time.time()}
            return response(200, {**base, 'order': row, 'pricing': {'verified': self.verified}})
        if method == 'DELETE' and order_id:
            if any(p['result'] == 'SUCCESS' for p in self.orders.get(order_id, {}).get('payments', [])):
                return response(409, {'error': 'Clover keeps orders that have payments.', 'code': 'has_payments'})
            self.deleted.add(order_id)
            return response(200, {'deleted': True, 'missing': False})
        if method == 'POST' and path.endswith('/settle'):
            body = kwargs['json']
            order = self.orders[order_id]
            order['paymentState'] = 'PAID'
            written = [{'key': p['key'], 'id': f'PAY{abs(hash(p["key"])) % 10**10:010d}', 'amount': p['amount']}
                       for p in body['payments']]
            for payment in written:
                if not any(p.get('externalPaymentId') == payment['key'] for p in order['payments']):
                    order['payments'].append({'id': payment['id'], 'amount': payment['amount'], 'result': 'SUCCESS',
                                              'tenderLabel': 'Cash', 'externalPaymentId': payment['key']})
            if body['discount_name']:
                order['discounts'] = [d for d in order['discounts'] if d.get('name') != body['discount_name']] + [
                    {'id': 'DISCOUNT00001', 'name': body['discount_name'], 'amount': body['target_total'] - order['total']}]
            order['total'] = body['target_total']
            return response(200, {'total': body['target_total'], 'paymentState': 'PAID', 'paid': True,
                                  'discount': {'id': 'DISCOUNT00001', 'amount': -1} if body['discount_name'] else None,
                                  'payments': written, 'difference_cents': 0})
        if method == 'POST' and path.endswith('/label'):
            order = self.orders[order_id]
            order['title'] = kwargs['json']['label']
            return response(200, {'title': order['title'], 'written': ['title'], 'paymentState': order['paymentState'],
                                  'total': order['total']})
        raise AssertionError(f'Unexpected connector call {method} {path}')

    def settles(self):
        return [call for call in self.calls if call[0] == 'POST' and call[1].endswith('/settle')]

    def labels(self):
        return [call for call in self.calls if call[0] == 'POST' and call[1].endswith('/label')]

    def writes(self):
        return [call for call in self.calls if call[0] in ('POST', 'DELETE')]


class CloverCheckoutTests(ReceivingFixture):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(settings.bp)
        clock = patch('checkout_access.business_date', return_value='2026-10-20')
        clock.start()
        self.addCleanup(clock.stop)
        with closing(self.connect()) as con:
            for subject, name in (('staff', 'Staff Cashier'), ('other', 'Other Cashier'), ('manager', 'Manager')):
                employee = con.execute('INSERT INTO employees(auth0_id,name) VALUES (?,?)', ('auth0|' + subject, name)).lastrowid
                con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                    VALUES (?,'2026-10-20','00:01','00:02','test',?)""", (employee, self.store_id))
            for subject in ('other', 'manager'):
                con.execute('INSERT INTO inventory_access(auth0_sub,store_id) VALUES (?,?)', ('auth0|' + subject, self.store_id))
            con.execute("INSERT INTO inventory_balances(product_id,location_id,disposition,quantity,version) VALUES (?,?,'saleable',5,1)",
                        (self.product_id, self.floor))
            con.execute('UPDATE products SET clover_item_id=? WHERE id=?', (ITEM, self.product_id))
            con.commit()
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

    # helpers
    def queue(self, role='staff'):
        with closing(self.connect()) as con:
            con.execute('UPDATE clover_feed SET next_poll=0')
            con.commit()
        result = self.client.get(f'/api/checkouts?store_id={self.store_id}&view=live', headers=self.headers(role))
        self.assertEqual(result.status_code, 200, result.get_json())
        return result.get_json()

    def detail(self, checkout_id, role='staff'):
        with closing(self.connect()) as con:
            con.execute('UPDATE clover_orders SET fetch_next_at=0')
            con.commit()
        result = self.client.get(f'/api/checkouts/{checkout_id}', headers=self.headers(role))
        self.assertEqual(result.status_code, 200, result.get_json())
        return result.get_json()

    def act(self, checkout_id, action, key, role='staff', expect=200, **body):
        result = self.client.post(f'/api/checkouts/{checkout_id}/{action}', json=body,
                                  headers={**self.headers(role), 'Idempotency-Key': key})
        self.assertEqual(result.status_code, expect, result.get_json())
        return result.get_json()

    def receive(self, order, key, tender, amount, *, plan='pay', expect=200, role='staff', **extra):
        return self.act(order['id'], 'receive', key, role=role, expect=expect, tender=tender, amount_cents=amount,
                        plan=plan, expected_version=order['version'], **extra)

    def one(self, sql, *args):
        with closing(self.connect()) as con:
            return con.execute(sql, args).fetchone()

    def checkout_for(self, order_id='ORDERAAAAAAAA'):
        row = self.one('SELECT checkout_id FROM clover_orders WHERE order_id=?', order_id)
        return row['checkout_id'] if row else None

    # tests
    def test_import_once_unclaimed_with_original_prices_and_exact_cash_price(self):
        self.clover.put(clover_order(), clover_order('OLDORDERAAAAA', created=CREATED - 40 * 86400000),
                        clover_order('EMPTYAAAAAAAA', prices=(), total=0),
                        clover_order('DEVICEAAAAAAA', source='device', total=None))
        listed = self.queue()
        self.assertEqual(listed['clover']['connected'], True)
        orders = [o for o in listed['orders'] if o['clover']]
        self.assertEqual([o['reference'] for o in orders], ['ORDERAAAAAAAA'])
        order = orders[0]
        self.assertEqual((order['cashier_name'], order['clover']['register'], order['business_date']),
                         ('Clover register', True, '2026-10-20'))
        self.assertEqual([(l['unit_price_cents'], l['mapped']) for l in order['clover']['lines']],
                         [(1770, True), (1999, False)])
        version = order['version']
        self.assertEqual(self.queue()['orders'][0]['version'], version)
        detail = self.detail(order['id'])
        self.assertEqual(detail['clover']['prices'], {
            'card_cents': 4259, 'card_blocked': False, 'cash_cents': 4100, 'cash_mode': 'cash_discount',
            'cash_label': 'Cash discount', 'split_cents': 4200, 'split_mode': 'split_card', 'split_label': 'Rounding'})
        self.assertTrue(detail['clover']['can_claim'])
        self.assertEqual(self.one('SELECT COUNT(*) n FROM checkout_orders')['n'], 1)

    def test_device_snapshots_never_touch_payments(self):
        self.clover.put(clover_order(payments=[card('CARDPAYMENT01', 2000)], state='PARTIALLY_PAID'))
        checkout_id = self.queue()['orders'][0]['id']
        self.clover.put(clover_order(prices=(1770, 1999, 500), total=4824, source='device'))
        listed = self.queue()['orders'][0]
        self.assertEqual((listed['clover']['total_cents'], listed['clover']['state']), (4824, 'PARTIALLY_PAID'))
        with closing(self.connect()) as con:
            attempts = con.execute('SELECT origin,status,amount_cents FROM checkout_attempts WHERE checkout_id=?',
                                   (checkout_id,)).fetchall()
        self.assertEqual([tuple(a) for a in attempts], [('clover', 'completed', 2000)])
        # Clover's own record still has the card; reading it keeps the payment.
        attempts = self.detail(checkout_id)['attempts']
        self.assertEqual([(a['origin'], a['status'], a['amount_cents']) for a in attempts], [('clover', 'completed', 2000)])

    def paid_label(self, checkout_id):
        with closing(self.connect()) as con:
            return clover_checkout.paid_label(con, checkout_id)

    def test_cash_sale_is_recorded_and_the_clover_order_labelled_paid_but_left_open(self):
        self.clover.put(clover_order(mapped='all'))
        order = self.detail(self.queue()['orders'][0]['id'])
        done = self.receive(order, 'cash-1', 'cash', 4100, expected_due_cents=4100)
        self.assertEqual((done['status'], done['cashier_name'], done['clover']['writeback']['state']),
                         ('completed', 'Staff Cashier', 'done'))
        again = self.receive(order, 'cash-1', 'cash', 4100, expected_due_cents=4100)
        self.assertEqual(again['sale_id'], done['sale_id'])
        label = self.paid_label(done['id'])
        self.assertRegex(label, r'^Paid \d{4}-\d{2}-\d{2} \d{2}:\d{2}$')
        self.assertEqual(done['clover']['writeback'], {'state': 'done', 'error': None, 'mode': 'label', 'label': label})
        (_, path, body, headers), = self.clover.writes()
        self.assertEqual((path, body, headers['X-Popcore-Writer']), ('/orders/ORDERAAAAAAAA/label', {'label': label},
                                                                     'k' * 32))
        self.assertEqual((self.clover.orders['ORDERAAAAAAAA']['paymentState'], done['clover']['state']), ('OPEN', 'OPEN'))
        sale = self.one('SELECT * FROM sale_documents WHERE id=?', done['sale_id'])
        self.assertEqual((sale['created_by'], sale['gross_cents'], sale['reduction_cents'], sale['collected_cents'],
                          sale['allocation_status']), ('auth0|staff', 4259, 159, 4100, 'allocated'))
        payment = self.one('SELECT * FROM sale_payments WHERE sale_id=?', done['sale_id'])
        self.assertEqual((payment['tender'], payment['amount_cents'], payment['state'], payment['source_system']),
                         ('cash', 4100, 'recorded', 'popcore_checkout'))
        # Later reads of the still-open Clover order change nothing, and stock moves once.
        self.queue()
        clover_checkout.background_sync()
        self.assertEqual(len(self.detail(done['id'])['attempts']), 1)
        self.assertIsNone(self.detail(done['id'])['clover']['review'])
        self.assertEqual(self.one('SELECT COUNT(*) n FROM sale_documents')['n'], 1)
        self.assertEqual(self.one('SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?',
                                  self.product_id, self.floor)['quantity'], 3)

    def test_wechat_on_a_clover_order_keeps_the_yuan_charged(self):
        self.clover.put(clover_order(mapped='all'))
        order = self.detail(self.queue()['orders'][0]['id'])
        self.receive(order, 'wechat-no-rate', 'wechat', 4100, expected_due_cents=4100, expect=400)
        done = self.receive(order, 'wechat-1', 'wechat', 4100, expected_due_cents=4100, cny_per_cad='5.2500', cny_fen=21525)
        self.assertEqual(done['status'], 'completed')
        paid = done['attempts'][-1]
        self.assertEqual((paid['tender'], paid['amount_cents'], paid['cny_fen'], paid['cny_per_cad']),
                         ('wechat', 4100, 21525, '5.2500'))

    def test_pay_mode_still_pays_the_clover_order_with_popcore_tenders(self):
        with patch.dict(os.environ, {'CLOVER_WRITEBACK': 'pay'}):
            self.clover.put(clover_order(mapped='all'))
            order = self.detail(self.queue()['orders'][0]['id'])
            done = self.receive(order, 'cash-1', 'cash', 4100, expected_due_cents=4100)
            label = self.paid_label(done['id'])
            self.assertEqual(done['clover']['writeback'], {'state': 'done', 'error': None, 'mode': 'pay', 'label': label})
            (_, path, body, _), = self.clover.writes()
            attempt = done['attempts'][0]
            self.assertEqual(path, '/orders/ORDERAAAAAAAA/settle')
            self.assertEqual((body['target_total'], body['discount_name'], body['reference']),
                             (4100, 'Cash discount', {'title': label}))
            self.assertEqual(body['payments'], [{'key': f"popcore-pc-{done['id']}-{attempt['id']}", 'tender': 'cash',
                                                 'amount': 4100}])
            self.assertIsNotNone(self.one('SELECT provider_payment_id FROM checkout_attempts WHERE id=?',
                                          attempt['id'])['provider_payment_id'])
            # The mirrored cash tender coming back from Clover is never counted again.
            self.queue()
            self.assertEqual(len(self.detail(done['id'])['attempts']), 1)
            self.assertIsNone(self.detail(done['id'])['clover']['review'])
        with patch.dict(os.environ, {'CLOVER_WRITEBACK': 'other'}):
            self.assertRaises(ValueError, clover_checkout.config)

    def test_split_card_part_waits_for_clover_then_completes(self):
        self.clover.put(clover_order(mapped=False))
        order = self.detail(self.queue()['orders'][0]['id'])
        part = self.receive(order, 'split-cash', 'cash', 2000, plan='split_card', expected_due_cents=4200)
        self.assertEqual((part['status'], part['clover']['remaining_cents']), ('open', 2200))
        self.assertEqual(self.clover.writes(), [])
        self.clover.put(clover_order(mapped=False, payments=[card('CARDPAYMENT01', 2200)], state='PARTIALLY_PAID'))
        self.queue()
        done = self.detail(order['id'])
        self.assertEqual((done['status'], done['clover']['writeback']['state']), ('completed', 'done'))
        (_, path, body, _), = self.clover.writes()
        self.assertEqual((path, body), ('/orders/ORDERAAAAAAAA/label', {'label': self.paid_label(done['id'])}))
        self.assertEqual(done['clover']['state'], 'PARTIALLY_PAID')
        payments = {row['tender']: row for row in (self.one(
            'SELECT tender,state FROM sale_payments WHERE sale_id=? AND tender=?', done['sale_id'], t) for t in ('cash', 'card'))}
        self.assertEqual((payments['card']['state'], payments['cash']['state']), ('verified', 'recorded'))
        self.assertIsNotNone(self.one("SELECT 1 FROM payment_events WHERE event_type='verify' AND reason LIKE ?",
                                      '%CARDPAYMENT01%'))

    def test_card_taken_first_sets_the_rest_and_a_mismatch_is_flagged(self):
        self.clover.put(clover_order(mapped=False, payments=[card('CARDPAYMENT01', 2300)], state='PARTIALLY_PAID'))
        order = self.detail(self.queue()['orders'][0]['id'])
        self.assertEqual(order['clover']['clover_cents'], 2300)
        self.receive(order, 'too-much', 'cash', 2000, plan='split_card', expect=400)
        done = self.receive(order, 'rest', 'cash', 1900, plan='split_card')
        self.assertEqual((done['status'], done['received_cents']), ('completed', 4200))
        # Overpaid: the card lands after more cash than the rest was recorded.
        self.clover.put(clover_order('ORDERBBBBBBBB', mapped=False))
        self.queue()
        second = self.detail(self.checkout_for('ORDERBBBBBBBB'))
        self.receive(second, 'cash-b', 'cash', 2000, plan='split_card')
        self.clover.put(clover_order('ORDERBBBBBBBB', mapped=False, payments=[card('CARDPAYMENT02', 2500)],
                                     state='PARTIALLY_PAID'))
        self.queue()
        flagged = self.detail(second['id'])
        self.assertEqual((flagged['status'], flagged['clover']['review']['reason']), ('open', 'overpaid'))
        self.assertFalse(flagged['clover']['can_receive'])
        # The cashier hands the cash back, takes it back in POPCORE and records what was really paid.
        cash = flagged['clover']['undo_attempt_ids'][0]
        self.act(second['id'], 'undo', 'undo-1', role='staff:other', expect=403, attempt_id=cash, reason='Gave cash back')
        self.act(second['id'], 'undo', 'undo-2', expect=400, attempt_id=cash, reason=' ')
        undone = self.act(second['id'], 'undo', 'undo-3', attempt_id=cash, reason='Gave cash back')
        self.assertIsNone(undone['clover']['review'])
        self.assertEqual(undone['clover']['collect']['pay']['rest_cents'], 1700)
        fixed = self.receive(undone, 'cash-c', 'cash', 1700, plan='split_card', expected_due_cents=4200)
        self.assertEqual((fixed['status'], fixed['received_cents']), ('completed', 4200))
        self.assertEqual(self.one('SELECT cancel_reason FROM checkout_attempts WHERE id=?', cash)['cancel_reason'],
                         'Gave cash back')
        self.act(second['id'], 'undo', 'undo-4', expect=409, attempt_id=fixed['attempts'][-1]['id'], reason='Late')

    def test_amounts_to_collect_for_every_way_of_paying(self):
        self.clover.put(clover_order(prices=(2000, 2259), total=4813, mapped=False))
        order = self.detail(self.queue()['orders'][0]['id'])
        collect = order['clover']['collect']
        self.assertEqual(collect['pay'], {'due_cents': 4600, 'mode': 'cash_discount', 'label': 'Cash discount',
                                          'rest_cents': 4600, 'cash_rest_cents': 4600})
        self.assertEqual(collect['split_card'], {'due_cents': 4800, 'mode': 'split_card', 'label': 'Rounding',
                                                 'rest_cents': 4800, 'cash_rest_cents': 4800})
        self.assertEqual(collect['card'], {'due_cents': 4813, 'rest_cents': 4813})
        part = self.receive(order, 'part', 'cash', 2000, plan='split_card', expected_due_cents=4800)
        collect = part['clover']['collect']
        self.assertEqual((collect['pay']['rest_cents'], collect['split_card']['rest_cents'], collect['card']['rest_cents']),
                         (2600, 2800, 2800))
        self.clover.put(clover_order('ORDERBBBBBBBB', prices=(2000, 2259), total=4813, mapped=False,
                                     payments=[card('CARDPAYMENT01', 2300)], state='PARTIALLY_PAID'))
        self.queue()
        collect = self.detail(self.checkout_for('ORDERBBBBBBBB'))['clover']['collect']
        self.assertEqual((collect['pay']['rest_cents'], collect['split_card']['rest_cents'], collect['card']['rest_cents']),
                         (2500, 2500, 2513))

    def test_cash_is_rounded_to_five_cents_on_a_clover_order(self):
        # Owner, 2026-10-27: card $20.37 on Clover; the rest of the $42.00 split is $21.63, collected as $21.65.
        self.clover.put(clover_order(prices=(3770,), total=4260, mapped=False, payments=[card('CARDPAYMENT01', 2037)],
                                     state='PARTIALLY_PAID'))
        order = self.detail(self.queue()['orders'][0]['id'])
        rest = order['clover']['collect']['split_card']
        self.assertEqual((rest['due_cents'], rest['rest_cents'], rest['cash_rest_cents']), (4200, 2163, 2165))
        self.receive(order, 'odd-cash', 'cash', 2163, plan='split_card', expect=400)
        done = self.receive(order, 'cash-rest', 'cash', 2165, plan='split_card', expected_due_cents=4200)
        self.assertEqual((done['status'], done['received_cents'], done['clover']['due_cents']), ('completed', 4202, 4202))
        self.assertIsNone(done['clover']['review'])
        sale = self.one('SELECT * FROM sale_documents WHERE id=?', done['sale_id'])
        self.assertEqual((sale['gross_cents'], sale['reduction_cents'], sale['collected_cents']), (4260, 58, 4202))

    def test_pay_mode_never_writes_more_than_the_clover_total(self):
        # Cash rounded up past an exact Clover total ($19.13 collected as $19.15): Clover is paid its total.
        with patch.dict(os.environ, {'CLOVER_WRITEBACK': 'pay'}):
            legacy = [{'id': 'D00000000002', 'name': 'CA$0.89 Off', 'amount': -89, 'percentage': None}]
            self.clover.put(clover_order(prices=(1770,), total=1913, mapped=False, discounts=legacy))
            order = self.detail(self.queue()['orders'][0]['id'])
            self.assertEqual(order['clover']['collect']['pay']['cash_rest_cents'], 1915)
            done = self.receive(order, 'cash-1', 'cash', 1915, expected_due_cents=1913)
            self.assertEqual((done['status'], done['received_cents'], done['clover']['writeback']['state']),
                             ('completed', 1915, 'done'))
            (_, path, body, _), = self.clover.writes()
            self.assertEqual((path, body['target_total'], body['discount_name'], [p['amount'] for p in body['payments']]),
                             ('/orders/ORDERAAAAAAAA/settle', 1913, None, [1913]))
            self.assertEqual(self.one('SELECT amount_cents FROM sale_payments WHERE sale_id=?', done['sale_id'])[0], 1915)

    def test_a_manager_can_take_payment_on_another_cashiers_order(self):
        self.clover.put(clover_order(mapped=False))
        order = self.act(self.detail(self.queue()['orders'][0]['id'])['id'], 'claim', 'claim-1')
        done = self.receive(order, 'manager-cash', 'cash', 4100, role='manager', expected_due_cents=4100)
        self.assertEqual((done['status'], done['cashier_name']), ('completed', 'Staff Cashier'))
        self.assertEqual(done['attempts'][0]['recorded_by'], 'auth0|manager')

    def test_card_only_sale_is_recorded_by_the_register_without_a_screen(self):
        self.clover.put(clover_order(payments=[card('CARDPAYMENT01', 4259)], state='PAID'))
        with closing(self.connect()) as con:
            clover_checkout.ensure_feed(con, clover_checkout.config())
        clover_checkout.background_sync()
        checkout_id = self.checkout_for()
        row = self.one('SELECT * FROM checkout_orders WHERE id=?', checkout_id)
        self.assertEqual((row['status'], row['created_by']), ('completed', REGISTER_SUB))
        sale = self.one('SELECT * FROM sale_documents WHERE id=?', row['sale_id'])
        self.assertEqual((sale['created_by'], sale['collected_cents'], sale['reduction_cents']), (REGISTER_SUB, 4259, 0))
        self.assertEqual(self.one('SELECT state FROM sale_payments WHERE sale_id=?', row['sale_id'])['state'], 'verified')
        # Clover already shows it paid: nothing is written there.
        self.assertEqual(self.clover.writes(), [])
        self.assertEqual(self.one('SELECT writeback_state FROM clover_orders WHERE checkout_id=?',
                                  checkout_id)['writeback_state'], 'none')
        # Staff on shift can still open the day's closing with register-owned sales in it.
        created = self.client.post('/api/closing', json={'store_id': self.store_id, 'business_date': '2026-10-20'},
                                   headers={**self.headers(), 'Idempotency-Key': 'closing-1'})
        self.assertEqual(created.status_code, 201, created.get_json())

    def test_write_back_failure_never_blocks_the_sale_and_is_retried(self):
        self.clover.put(clover_order(mapped=False))
        order = self.detail(self.queue()['orders'][0]['id'])
        self.clover.fail['POST label'] = (502, {'error': 'Clover is limiting requests.', 'code': 'clover_unavailable'})
        done = self.receive(order, 'cash-1', 'cash', 4100)
        self.assertEqual((done['status'], done['clover']['writeback']['state']), ('completed', 'failed'))
        self.assertTrue(done['clover']['can_retry'])
        closing_id = self.client.post('/api/closing', json={'store_id': self.store_id, 'business_date': '2026-10-20'},
                                      headers={**self.headers('manager'), 'Idempotency-Key': 'c-1'}).get_json()['closing_id']
        blockers = self.client.get(f'/api/closing/{closing_id}', headers=self.headers('manager')).get_json()['hard_blockers']
        self.assertIn(f"clover_writeback_pending:{done['id']}", blockers)
        del self.clover.fail['POST label']
        retried = self.act(done['id'], 'clover-retry', 'retry-1')
        self.assertEqual(retried['clover']['writeback']['state'], 'done')
        blockers = self.client.get(f'/api/closing/{closing_id}', headers=self.headers('manager')).get_json()['hard_blockers']
        self.assertNotIn(f"clover_writeback_pending:{done['id']}", blockers)
        self.assertNotIn('clover_sync_stale', blockers)

    def test_changes_on_clover_before_and_after_the_sale(self):
        self.clover.put(clover_order(mapped=False))
        order = self.detail(self.queue()['orders'][0]['id'])
        self.clover.put(clover_order(prices=(1770, 1999, 500), total=4824, mapped=False))
        self.receive(order, 'stale', 'cash', 4100, expect=409)
        fresh = self.detail(order['id'])
        self.assertEqual(fresh['clover']['total_cents'], 4824)
        done = self.receive(fresh, 'cash-2', 'cash', fresh['clover']['prices']['cash_cents'],
                            expected_due_cents=fresh['clover']['prices']['cash_cents'])
        self.assertEqual(done['status'], 'completed')
        paid = self.clover.orders['ORDERAAAAAAAA']
        self.clover.put({**paid, 'refunds': [{'id': 'REFUND0000001', 'amount': 500}]})
        self.queue()
        flagged = self.detail(done['id'])
        self.assertEqual(flagged['clover']['review']['reason'], 'clover_refunded')
        closing_id = self.client.post('/api/closing', json={'store_id': self.store_id, 'business_date': '2026-10-20'},
                                      headers={**self.headers('manager'), 'Idempotency-Key': 'c-2'}).get_json()['closing_id']
        exceptions = self.client.get(f'/api/closing/{closing_id}', headers=self.headers('manager')).get_json()['review_exceptions']
        self.assertIn(f"clover_review:{done['id']}", exceptions)

    def test_cancel_deletes_on_clover_and_a_clover_delete_cancels(self):
        self.clover.put(clover_order(), clover_order('ORDERBBBBBBBB'))
        self.queue()
        first, second = self.checkout_for(), self.checkout_for('ORDERBBBBBBBB')
        cancelled = self.act(first, 'cancel', 'cancel-1')
        self.assertEqual((cancelled['status'], cancelled['clover']['state']), ('cancelled', 'DELETED'))
        self.assertIn('ORDERAAAAAAAA', self.clover.deleted)
        # A Clover read taken just before the delete arrives late: the order stays deleted.
        self.clover.deleted.discard('ORDERAAAAAAAA')
        self.queue()
        self.assertEqual(self.detail(first)['clover']['state'], 'DELETED')
        self.clover.deleted.add('ORDERAAAAAAAA')
        self.clover.deleted.add('ORDERBBBBBBBB')
        gone = self.detail(second)
        self.assertEqual((gone['status'], gone['clover']['state']), ('cancelled', 'DELETED'))
        self.clover.put(clover_order('ORDERCCCCCCCC', payments=[card('CARDPAYMENT01', 1000)], state='PARTIALLY_PAID'))
        self.queue()
        third = self.checkout_for('ORDERCCCCCCCC')
        self.act(third, 'cancel', 'cancel-3', expect=409)

    def test_claim_release_and_another_cashiers_order(self):
        self.clover.put(clover_order())
        order = self.detail(self.queue()['orders'][0]['id'])
        claimed = self.act(order['id'], 'claim', 'claim-1')
        self.assertEqual((claimed['cashier_name'], claimed['clover']['register']), ('Staff Cashier', False))
        self.act(order['id'], 'claim', 'claim-2', role='staff:other', expect=409)
        self.receive(claimed, 'other-cash', 'cash', 4100, role='staff:other', expect=403)
        released = self.act(order['id'], 'release', 'release-1')
        self.assertTrue(released['clover']['register'])
        self.assertEqual(self.act(order['id'], 'claim', 'claim-3', role='staff:other')['cashier_name'], 'Other Cashier')

    def test_price_changes_and_card_are_refused(self):
        self.clover.put(clover_order())
        order = self.detail(self.queue()['orders'][0]['id'])
        self.receive(order, 'card', 'card', 4259, expect=400)
        self.receive(order, 'price', 'cash', 4100, expected_due_cents=4200, expect=409)
        self.clover.verified = False
        # Label mode writes no total to Clover, so Clover's own total is enough to price from.
        self.assertEqual(self.detail(order['id'])['clover']['prices']['cash_cents'], 4100)
        with patch.dict(os.environ, {'CLOVER_WRITEBACK': 'pay'}):
            unverified = self.detail(order['id'])
        self.assertEqual(unverified['clover']['prices']['cash_cents'], 4259)
        self.assertEqual(unverified['clover']['prices']['cash_mode'], 'exact')

    def test_closing_needs_a_clover_day_scan(self):
        self.clover.put(clover_order(payments=[card('CARDPAYMENT01', 4259)], state='PAID'))
        self.queue()
        self.clover.fail['GET /orders/scan'] = (502, {'error': 'down', 'code': 'clover_unavailable'})
        closing_id = self.client.post('/api/closing', json={'store_id': self.store_id, 'business_date': '2026-10-20'},
                                      headers={**self.headers('manager'), 'Idempotency-Key': 'c-3'}).get_json()['closing_id']
        detail = self.client.get(f'/api/closing/{closing_id}', headers=self.headers('manager')).get_json()
        self.assertIn('clover_sync_stale', detail['hard_blockers'])
        del self.clover.fail['GET /orders/scan']
        detail = self.client.get(f'/api/closing/{closing_id}', headers=self.headers('manager')).get_json()
        self.assertNotIn('clover_sync_stale', detail['hard_blockers'])

    def closing_detail(self, key='closing-1'):
        closing_id = self.client.post('/api/closing', json={'store_id': self.store_id, 'business_date': '2026-10-20'},
                                      headers={**self.headers('manager'), 'Idempotency-Key': key}).get_json()['closing_id']
        detail = self.client.get(f'/api/closing/{closing_id}', headers=self.headers('manager'))
        self.assertEqual(detail.status_code, 200, detail.get_json())
        return detail.get_json()

    def test_a_clover_payment_without_items_waits_for_a_manager_and_counts_at_closing(self):
        # A custom amount or quick sale on Clover: paid by card, no items (owner, 2026-10-27).
        self.clover.put(clover_order('QUICKSALE0001', prices=(), total=5000, payments=[card('QUICKPAYMENT1', 5000)],
                                     state='PAID'), clover_order('EMPTYAAAAAAAA', prices=(), total=0))
        self.queue()
        self.assertIsNone(self.checkout_for('EMPTYAAAAAAAA'))  # an empty order is still not an order
        checkout = self.one('SELECT * FROM checkout_orders WHERE id=?', self.checkout_for('QUICKSALE0001'))
        sale = self.one('SELECT * FROM sale_documents WHERE id=?', checkout['sale_id'])
        self.assertEqual((checkout['status'], sale['collected_cents'], sale['allocation_status'], sale['allocation_reason']),
                         ('completed', 5000, 'pending', 'product_mapping_required'))
        self.assertEqual(self.one('SELECT raw_product_text FROM sale_lines WHERE sale_id=?', sale['id'])[0],
                         'Clover payment with no items')
        detail = self.closing_detail()
        self.assertIn(f"pending_allocation:{sale['id']}", detail['hard_blockers'])
        self.assertEqual(detail['tender_totals_cents']['card'], 5000)
        # The manager records it as a charge that moves no stock.
        allocated =self.client.post(f"/api/sale-documents/{sale['id']}/allocate", json={
            'expected_version': sale['version'], 'reason': 'Deposit for a special order',
            'mappings': [{'line_no': 1, 'non_stock': True}]}, headers={**self.headers('manager'), 'Idempotency-Key': 'alloc'})
        self.assertEqual(allocated.status_code, 200, allocated.get_json())
        self.assertEqual((allocated.get_json()['allocation_status'], allocated.get_json()['inventory_document_id']),
                         ('allocated', None))
        self.assertEqual(self.one('SELECT reason FROM sale_allocations WHERE sale_id=?', sale['id'])[0],
                         'non_stock_charge: Deposit for a special order')
        self.assertNotIn(f"pending_allocation:{sale['id']}", self.closing_detail('closing-2')['hard_blockers'])

    def test_a_clover_order_that_cannot_be_imported_holds_closing_until_it_imports_or_is_resolved(self):
        with closing(self.connect()) as con:  # an admin removed the register's grant
            clover_checkout.ensure_feed(con, clover_checkout.config())
            con.execute('DELETE FROM inventory_access WHERE auth0_sub=?', (REGISTER_SUB,))
            con.commit()
        self.clover.put(clover_order('CARDSALE00001', mapped='all', payments=[card('CARDPAYMENT01', 4259)], state='PAID'))
        detail = self.closing_detail()
        self.assertIn('clover_import_failed:CARDSALE00001', detail['hard_blockers'])
        self.assertNotIn('clover_sync_stale', detail['hard_blockers'])
        self.assertEqual(detail['issue_targets']['clover_import_failed:CARDSALE00001'],
                         {'href': '/checkout', 'label': 'Clover order CARDSALE00001'})
        failures = self.queue('manager')['clover']['failures']
        self.assertEqual([(f['order_id'], f['business_date']) for f in failures], [('CARDSALE00001', '2026-10-20')])
        # Once the grant is back the next scan imports it, and closing counts the card.
        with closing(self.connect()) as con:
            con.execute('INSERT INTO inventory_access(auth0_sub,store_id) VALUES (?,?)', (REGISTER_SUB, self.store_id))
            con.commit()
        detail = self.closing_detail()
        self.assertNotIn('clover_import_failed:CARDSALE00001', detail['hard_blockers'])
        self.assertEqual(detail['tender_totals_cents']['card'], 4259)
        self.assertEqual(self.queue('manager')['clover']['failures'], [])
        # Another one a manager resolves instead; it is then never imported, so it is never counted twice.
        with closing(self.connect()) as con:
            con.execute('DELETE FROM inventory_access WHERE auth0_sub=?', (REGISTER_SUB,))
            con.commit()
        self.clover.put(clover_order('CARDSALE00002', mapped='all', payments=[card('CARDPAYMENT02', 4259)], state='PAID'))
        self.assertIn('clover_import_failed:CARDSALE00002', self.closing_detail()['hard_blockers'])
        path, body = '/api/checkouts/clover/CARDSALE00002/resolve', {'store_id': self.store_id, 'reason': 'Recorded by hand'}
        self.assertEqual(self.client.post(path, json=body, headers={**self.headers(), 'Idempotency-Key': 'r-0'}).status_code, 403)
        self.assertEqual(self.client.post(path, json={**body, 'reason': ' '},
                                          headers={**self.headers('manager'), 'Idempotency-Key': 'r-1'}).status_code, 400)
        resolved = self.client.post(path, json=body, headers={**self.headers('manager'), 'Idempotency-Key': 'r-2'})
        self.assertEqual((resolved.status_code, resolved.get_json()), (200, {'failures': []}))
        self.assertNotIn('clover_import_failed:CARDSALE00002', self.closing_detail()['hard_blockers'])
        with closing(self.connect()) as con:
            con.execute('INSERT INTO inventory_access(auth0_sub,store_id) VALUES (?,?)', (REGISTER_SUB, self.store_id))
            con.commit()
        self.closing_detail()
        self.assertIsNone(self.checkout_for('CARDSALE00002'))

    def test_clover_orders_from_before_the_store_went_live_are_never_imported(self):
        # Owner, 2026-10-27: the opening count already holds what was sold before it.
        with closing(self.connect()) as con:
            opened = datetime.fromtimestamp(CREATED / 1000, ZoneInfo('UTC')).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            document = con.execute("""INSERT INTO inventory_documents(kind,request_key,payload_hash,actor_sub,business_date,
                posted_at,status) VALUES ('opening','opening-dt','x','auth0|admin','2026-10-20',?,'posted')""",
                (opened,)).lastrowid
            con.execute('UPDATE inventory_scope_state SET opening_document_id=? WHERE store_id=?', (document, self.store_id))
            con.commit()
        self.clover.put(clover_order('BEFOREOPENING', mapped='all', payments=[card('CARDPAYMENT01', 4259)], state='PAID',
                                     created=CREATED - 3600_000),
                        clover_order('AFTEROPENING1', mapped='all', created=CREATED + 3600_000))
        self.queue()
        detail = self.closing_detail()
        self.assertEqual((self.checkout_for('BEFOREOPENING'), bool(self.checkout_for('AFTEROPENING1'))), (None, True))
        self.assertEqual(self.one('SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?',
                                  self.product_id, self.floor)['quantity'], 5)
        self.assertNotIn('clover_sync_stale', detail['hard_blockers'])
        # A store that has not gone live takes no Clover orders, and says so.
        with closing(self.connect()) as con:
            con.execute('UPDATE inventory_scope_state SET opening_verified=0 WHERE store_id=?', (self.store_id,))
            con.commit()
        self.clover.put(clover_order('NOTLIVEORDER1', mapped='all', created=CREATED + 7200_000))
        with closing(self.connect()) as con:
            clover_checkout.sync(con, clover_checkout.config(), force=True)
            message = con.execute('SELECT error FROM clover_feed').fetchone()[0]
        self.assertIsNone(self.checkout_for('NOTLIVEORDER1'))
        self.assertEqual(message, 'Clover orders are not imported until this store goes live with its opening count.')

    def test_find_by_clover_id_and_cny_rate(self):
        self.clover.put(clover_order())
        checkout_id = self.queue()['orders'][0]['id']
        found = self.client.get(f'/api/checkouts/clover/orderaaaaaaaa?store_id={self.store_id}', headers=self.headers())
        self.assertEqual(found.get_json()['id'], checkout_id)
        self.assertEqual(self.client.get(f'/api/checkouts/clover/NOSUCHORDER01?store_id={self.store_id}',
                                         headers=self.headers()).status_code, 404)
        access = self.client.get('/api/checkouts/access', headers=self.headers()).get_json()
        self.assertEqual((access['cny_per_cad'], access['clover_store_id']), ('', self.store_id))
        for rate in ('0', '5.12345', 'bad'):
            self.assertEqual(self.client.put('/api/settings', json={'checkout_cny_per_cad': rate},
                                             headers=self.headers('admin')).status_code, 400, rate)
        self.assertEqual(self.client.put('/api/settings', json={'checkout_cny_per_cad': '5.2500'},
                                         headers=self.headers('admin')).status_code, 200)
        self.assertEqual(self.client.get('/api/checkouts/access', headers=self.headers()).get_json()['cny_per_cad'],
                         '5.2500')

    def history(self, role='manager', **filters):
        result = self.client.get('/api/checkouts', query_string={'store_id': self.store_id, 'view': 'history', **filters},
                                 headers=self.headers(role))
        self.assertEqual(result.status_code, 200, result.get_json())
        return result.get_json()

    def test_history_filters_by_payment_method_and_keyword(self):
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET jizhanming='泡泡玛特 Labubu' WHERE id=?", (self.product_id,))
            con.commit()
        self.clover.put(clover_order(mapped='all'), clover_order('ORDERBBBBBBBB', prices=(999,), total=1129, mapped=False,
                                                                 payments=[card('CARDPAYMENT01', 1129)], state='PAID'))
        self.queue()
        cash = self.detail(self.checkout_for())
        self.receive(cash, 'cash-1', 'cash', 4100, expected_due_cents=4100)
        card_order = self.checkout_for('ORDERBBBBBBBB')
        listed = self.history()
        self.assertEqual(listed['clover']['enabled'], True)  # history shows the live Clover status too
        self.assertEqual(len(listed['orders']), 2)
        refs = lambda data: sorted(o['reference'] for o in data['orders'])
        self.assertEqual(refs(self.history(tender='cash')), ['ORDERAAAAAAAA'])
        self.assertEqual(refs(self.history(tender='card')), ['ORDERBBBBBBBB'])
        self.assertEqual(refs(self.history(tender='alipay')), [])
        self.assertEqual(refs(self.history(q='Item 0')), ['ORDERAAAAAAAA', 'ORDERBBBBBBBB'])  # Clover item names
        self.assertEqual(refs(self.history(q='泡泡玛特')), ['ORDERAAAAAAAA'])  # POPCORE product name, in Chinese
        self.assertEqual(refs(self.history(q='orderbbbb')), ['ORDERBBBBBBBB'])  # the Clover order ID
        self.assertEqual(refs(self.history(q='item 0', tender='card')), ['ORDERBBBBBBBB'])
        self.assertEqual(refs(self.history(q='%')), [])  # wildcards are plain text
        self.assertEqual(self.client.get('/api/checkouts', query_string={'store_id': self.store_id, 'view': 'history',
                                                                         'tender': 'cheque'},
                                         headers=self.headers('manager')).status_code, 400)
        self.assertEqual(self.checkout_for('ORDERBBBBBBBB'), card_order)

    def test_migration_can_run_again(self):
        db.migrate_db()
        db.migrate_db()
        self.assertIsNotNone(self.one("SELECT 1 FROM _migrations WHERE name='clover_checkouts'"))



class SendToCloverTests(CloverCheckoutTests):
    """A sale started in POPCORE (exact designs, agreed prices) and paid by card on the Clover device."""

    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            series = con.execute("INSERT INTO product_series(name) VALUES ('小王子')").lastrowid
            self.design = con.execute("""INSERT INTO products(sku,jizhanming,name_cn_en,series_id,stock_form,stock_unit,
                design_name,identity_status) VALUES ('PM0209-01','小王子 商人','The Little Prince Businessman',?,
                'confirmed_design','piece','The Businessman','verified')""", (series,)).lastrowid
            con.execute("INSERT INTO inventory_balances(product_id,location_id,disposition,quantity,version) VALUES (?,?,'saleable',3,1)",
                        (self.design, self.floor))
            con.execute("UPDATE products SET sku='TT0001' WHERE id=?", (self.product_id,))
            con.commit()

    def manual(self, *, tax=720, reduction=0, key='create-1'):
        lines = [dict(product_id=self.design, unit='piece', quantity=2, unit_price_cents=1770),
                 dict(product_id=self.product_id, unit='piece', quantity=1, unit_price_cents=1999)]
        result = self.client.post('/api/checkouts', headers={**self.headers(), 'Idempotency-Key': key}, json=dict(
            store_id=self.store_id, business_date='2026-10-20', lines=lines, subtotal_cents=5539, source_tax_cents=tax,
            gross_cents=5539 + tax, reduction_cents=reduction, rounding_cents=0, collected_cents=5539 + tax - reduction))
        self.assertEqual(result.status_code, 201, result.get_json())
        return result.get_json()

    def creates(self):
        return [call for call in self.clover.calls if call[1] == '/orders/create']

    def stock(self, product_id):
        return self.one('SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?',
                        product_id, self.floor)['quantity']

    def test_card_on_clover_records_the_exact_designs_on_the_popcore_checkout(self):
        order = self.manual()
        self.assertEqual(order['clover_send'], {'pending': False, 'error': None, 'can_send': True, 'can_stop': False})
        sent = self.act(order['id'], 'clover-send', 'send-1')
        (_, _, body, headers), = self.creates()
        self.assertEqual(headers['X-Popcore-Writer'], 'k' * 32)
        self.assertEqual(body, {
            'key': f"popcore-pc-{order['id']}", 'reference': 'DT261020001', 'title': 'DT-20261020-001',
            'tax_rate': 1300000, 'expected_total': 6259, 'discount': None,
            'lines': [{'name': 'PM0209-01 小王子 The Businessman', 'price': 1770, 'quantity': 2},
                      {'name': 'TT0001 Test Product', 'price': 1999, 'quantity': 1}]})
        order_id = sent['clover']['order_id']
        self.assertEqual((sent['id'], sent['reference'], sent['cashier_name'], sent['clover']['register']),
                         (order['id'], 'DT-20261020-001', 'Staff Cashier', False))
        self.assertEqual((sent['clover']['started_in_popcore'], sent['clover']['can_release']), (True, False))
        self.assertEqual([(l['product_id'], l['mapped']) for l in sent['clover']['lines']],
                         [(self.design, True)] * 2 + [(self.product_id, True)])
        self.assertEqual(sent['clover']['prices']['card_cents'], 6259)
        # Sending again, or Clover's feed showing the order, never makes a second order on either side.
        self.act(order['id'], 'clover-send', 'send-2')
        self.assertEqual(len(self.creates()), 1)
        self.assertEqual([o['id'] for o in self.queue()['orders']], [order['id']])
        # The cashier takes card on the device; Clover's record finalizes the POPCORE sale by itself.
        self.clover.put({**self.clover.orders[order_id], 'paymentState': 'PAID', 'payments': [card('CARDPAYMENT01', 6259)]})
        clover_checkout.background_sync()
        done = self.detail(order['id'])
        self.assertEqual((done['status'], done['clover']['writeback']['state']), ('completed', 'none'))
        sale = self.one('SELECT * FROM sale_documents WHERE id=?', done['sale_id'])
        self.assertEqual((sale['business_date'], sale['created_by'], sale['collected_cents']), ('2026-10-20', 'auth0|staff', 6259))
        self.assertEqual(self.one('SELECT source_reference FROM sale_sources WHERE sale_id=?', done['sale_id'])[0],
                         'DT-20261020-001')
        with closing(self.connect()) as con:  # Windows cannot delete a database that is still open
            lines = {row['product_id']: row['quantity'] for row in con.execute(
                'SELECT product_id,quantity FROM sale_lines WHERE sale_id=?', (done['sale_id'],))}
        self.assertEqual(lines, {self.design: 2, self.product_id: 1})
        self.assertEqual((self.stock(self.design), self.stock(self.product_id)), (1, 4))
        payment = self.one('SELECT tender,amount_cents,state FROM sale_payments WHERE sale_id=?', done['sale_id'])
        self.assertEqual(tuple(payment), ('card', 6259, 'verified'))
        self.assertEqual([w for w in self.clover.writes() if w[1] != '/orders/create'], [])
        self.assertEqual(self.one('SELECT COUNT(*) n FROM checkout_orders')['n'], 1)

    def test_cash_on_a_sent_order_is_recorded_in_popcore_and_only_labels_clover(self):
        order = self.act(self.manual()['id'], 'clover-send', 'send-1')
        cash = order['clover']['prices']['cash_cents']
        self.assertEqual((cash, order['clover']['prices']['cash_mode']), (6100, 'cash_discount'))
        done = self.receive(order, 'cash-1', 'cash', cash, expected_due_cents=cash)
        self.assertEqual((done['status'], done['clover']['writeback']['state']), ('completed', 'done'))
        clover_order = self.clover.orders[done['clover']['order_id']]
        self.assertEqual((clover_order['paymentState'], clover_order['payments']), ('OPEN', []))
        self.assertRegex(clover_order['title'], r'^Paid \d{4}-\d{2}-\d{2} \d{2}:\d{2}$')
        self.assertEqual([w[1].rsplit('/', 1)[-1] for w in self.clover.writes()], ['create', 'label'])
        self.assertEqual((self.stock(self.design), self.stock(self.product_id)), (1, 4))

    def test_refusals_leave_a_popcore_order_and_a_lost_reply_still_joins_it(self):
        # Hand-typed tax that Clover would not charge: nothing exists on Clover and POPCORE pays it as before.
        order = self.manual(tax=700)
        self.clover.fail['POST /orders/create'] = (409, {'error': 'Clover would charge $62.59, not $62.39.', 'code': 'amounts_differ',
                                                 'clover_total': 6259})
        refused = self.act(order['id'], 'clover-send', 'send-1', expect=409)
        self.assertEqual(refused['code'], 'amounts_differ')
        self.assertIn('Clover would charge $62.59', refused['error'])
        again = self.detail(order['id'])
        self.assertEqual((again['clover'], again['clover_send']['pending'], again['pricing']['can_receive']),
                         (None, False, True))
        del self.clover.fail['POST /orders/create']
        # Clover unreachable: the order waits for Retry or Stop, and POPCORE takes no payment meanwhile.
        self.clover.fail['POST /orders/create'] = (502, {'error': 'Clover connection unavailable.', 'code': 'clover_unavailable'})
        second = self.manual(key='create-2')
        self.act(second['id'], 'clover-send', 'send-2', expect=409)
        waiting = self.detail(second['id'])
        self.assertEqual((waiting['clover_send']['pending'], waiting['clover_send']['can_stop'], waiting['pricing']['can_receive']),
                         (True, True, False))
        self.receive(waiting, 'cash-x', 'cash', 6100, plan='pay', expect=409)
        stopped = self.act(second['id'], 'clover-unsend', 'stop-1')
        self.assertEqual((stopped['clover_send']['pending'], stopped['pricing']['can_receive']), (False, True))
        del self.clover.fail['POST /orders/create']
        # Clover made the order but the reply was lost: the feed joins it to the POPCORE checkout by its reference.
        third = self.manual(key='create-3')
        self.clover.lose_reply = True
        self.act(third['id'], 'clover-send', 'send-3', expect=409)
        listed = [o['id'] for o in self.queue()['orders']]
        self.assertEqual(sorted(listed), sorted([order['id'], second['id'], third['id']]))
        joined = self.detail(third['id'])
        self.assertEqual((joined['clover']['started_in_popcore'], joined['clover']['register']), (True, False))
        self.assertEqual(self.one('SELECT COUNT(*) n FROM clover_orders')['n'], 1)

    def test_an_agreed_discount_goes_to_clover_and_a_paid_order_cannot_be_sent(self):
        order = self.manual(tax=694, reduction=200)
        sent = self.act(order['id'], 'clover-send', 'send-1')
        self.assertEqual(self.creates()[0][2]['discount'], {'name': 'Agreed discount', 'amount': 200})
        self.assertEqual(sent['clover']['prices']['card_cents'], 6033)
        paid = self.manual(key='create-2')
        self.receive(paid, 'card-1', 'card', 1000, plan='split_card')
        self.assertFalse(self.detail(paid['id'])['clover_send']['can_send'])
        self.act(paid['id'], 'clover-send', 'send-2', expect=409)
