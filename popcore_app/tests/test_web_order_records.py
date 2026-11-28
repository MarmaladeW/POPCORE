"""网站订单记录 Web order records (owner, 2026-11-28, popcore-work#157: "网站订单 also need to be recorded, their order
details"; 汇总 is "instore orders and website orders"). Every paid web order stays on record with its number, paid
time, items as the customer bought them, amount, pickup or delivery, stores and status, for the admin only: on
销售 → 网站订单 and as their own line on 每日汇总, never in the store's total and with nothing personal."""
from contextlib import closing
from datetime import datetime, timezone

from test_website_board import ADMIN, CONTACT, PRIVATE, BoardCase
from test_website_sync import _ago

from checkout_access import business_date

import web_order_records
from website_sync import TORONTO


def _month(gmt):
    return datetime.fromisoformat(gmt).replace(tzinfo=timezone.utc).astimezone(TORONTO).strftime('%Y-%m')


class WebOrderRecordsTests(BoardCase):
    def records(self, headers=ADMIN, **params):
        response = self.client.get('/api/website/records', query_string=params, headers=headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def find(self, number, **params):
        return next(order for order in self.records(q=number, **params)['orders'] if order['number'] == number)

    def test_every_order_keeps_its_details_and_where_it_stands(self):
        pickup = self.find('701')
        self.assertEqual({key: pickup[key] for key in ('kind', 'stores', 'taken_from', 'status', 'waiting', 'counted',
                                                       'total_cents', 'tax_cents', 'payment', 'pieces')},
                         {'kind': 'pickup', 'stores': ['DT'], 'taken_from': ['DT'], 'status': 'open', 'waiting': False,
                          'counted': True, 'total_cents': 4520, 'tax_cents': 520,
                          'payment': 'Credit Card (Stripe)', 'pieces': 1})
        # The item as the customer bought it (the website's name), and the name staff know it by.
        self.assertEqual(pickup['lines'], [{'name': 'PM0200', 'quantity': 1, 'total_cents': 2000,
                                            'popcore_id': 'PM0200', 'popcore_name': 'Bear 记账名'}])
        self.assertEqual(pickup['day'], web_order_records.toronto_day(self.order_row(701)['paid_at']))
        self.assertNotIn('packed_at', pickup)
        self.assertEqual({number: self.find(number)['status'] for number in ('702', '703', '705', '706', '708')},
                         {'702': 'ready', '703': 'open', '705': 'out', '706': 'open', '708': 'done'})
        self.assertEqual(self.find('708')['closed_at'], self.order_row(708)['modified_at'])
        self.assertIsNone(self.find('701')['closed_at'])
        # 707 is short of stock: it waits for an admin on the 网站 page, and still counts as a paid order.
        self.assertEqual((self.find('707')['waiting'], self.find('707')['counted'], self.find('707')['pieces']),
                         (True, True, 3))
        # Packed on the board: when, and who.
        self.assertEqual(self.step(703, 'packed').get_json()['result'], 'done')
        packed = self.find('703')
        self.assertEqual((packed['kind'], packed['status'], packed['packed_by'], packed['stores']),
                         ('delivery', 'packed', 'Amy', ['DT']))
        self.assertTrue(packed['packed_at'])

    def test_a_cancelled_or_refunded_order_stays_on_record_but_counts_in_no_total(self):
        before = self.records(q='Bear')['totals']
        self.woo.orders[701].update(status='cancelled', date_modified_gmt=_ago(0))
        self.woo.orders[704].update(status='refunded', date_modified_gmt=_ago(0))
        self.run_sync()
        cancelled, refunded = self.find('701'), self.find('704')
        self.assertEqual((cancelled['status'], cancelled['counted'], refunded['status'], refunded['counted']),
                         ('cancelled', False, 'refunded', False))
        self.assertEqual(cancelled['closed_at'], self.order_row(701)['modified_at'])
        after = self.records(q='Bear')['totals']
        self.assertEqual((after['orders'], after['cents'], after['not_counted']),
                         (before['orders'] - 2, before['cents'] - 2 * 4520, before['not_counted'] + 2))

    def test_a_month_lists_its_orders_newest_first_by_store_with_nothing_personal(self):
        month = _month(self.order_row(708)['paid_at'])
        response = self.client.get(f'/api/website/records?month={month}', headers=ADMIN)
        data = response.get_json()
        self.assertEqual(data['month'], month)
        orders = data['orders']
        self.assertTrue(orders)
        self.assertTrue(all(order['day'].startswith(month) for order in orders))
        self.assertEqual([order['paid_at'] for order in orders], sorted((order['paid_at'] for order in orders), reverse=True))
        self.assertEqual(data['totals']['cents'], sum(order['total_cents'] for order in orders if order['counted']))
        self.assertEqual(data['totals']['pickup'] + data['totals']['delivery'], data['totals']['orders'])
        text = response.get_data(as_text=True)
        for value in (*CONTACT, *PRIVATE, 'Zoë', 'Lima', 'Sammy', 'K7R4MX', 'ACD346', 'HJK777', 'MKP999', 'pickup_code'):
            self.assertNotIn(value, text)
        # A store's own: MK has its pickup only; DT has everything else.
        mk = self.records(month=_month(self.order_row(709)['paid_at']), store='MK')['orders']
        self.assertEqual([order['number'] for order in mk], ['709'])
        self.assertNotIn('709', [order['number'] for order in self.records(month=month, store='DT')['orders']])
        self.assertEqual(len(self.records(month=month, store='ALL')['orders']), len(orders))
        # A month with none, and a month that is no month.
        self.assertEqual(self.records(month='2020-01')['orders'], [])
        for bad in ('2026-13', '202611', 'november'):
            self.assertEqual(self.client.get(f'/api/website/records?month={bad}', headers=ADMIN).status_code, 400)

    def test_search_finds_an_order_by_number_or_item_in_any_month(self):
        self.assertEqual([order['number'] for order in self.records(q='702')['orders']], ['702'])
        self.assertEqual([order['number'] for order in self.records(q='#702')['orders']], ['702'])
        self.assertIsNone(self.records(q='702')['month'])
        # Words of an item: the website's name, the 记账名 or the POPCORE ID, in any case.
        bear = {order['number'] for order in self.records(q='bear 记账名')['orders']}
        self.assertEqual(bear, {'701', '704', '706'})
        self.assertEqual({order['number'] for order in self.records(q='pm0200')['orders']}, bear)
        self.assertEqual(self.records(q='nothing like it')['orders'], [])

    def test_only_an_admin_sees_the_records(self):
        for role in ('viewer', 'staff', 'manager', 'norole'):
            response = self.client.get('/api/website/records', headers=self.headers(role))
            self.assertEqual(response.status_code, 403, role)

    def test_the_day_summary_shows_the_stores_web_orders_as_their_own_line(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO employees(auth0_id, name) VALUES ('auth0|boss', 'Boss')")
            con.commit()
        day = web_order_records.toronto_day(self.order_row(708)['paid_at'])
        response = self.client.get(f'/api/store-events?store_id={self.dt}&business_date={day}',
                                   headers=self.headers('admin:boss'))
        data = response.get_json()
        self.assertEqual(response.status_code, 200, data)
        self.assertEqual(data['scope'], 'store')
        numbers = [order['number'] for order in data['web_orders']['orders']]
        self.assertIn('708', numbers)
        self.assertNotIn('709', numbers)  # MK's pickup is on MK's summary
        self.assertTrue(all(order['day'] == day and 'DT' in order['stores'] for order in data['web_orders']['orders']))
        # Its own line: the store's checkouts and their text are untouched.
        self.assertEqual(data['checkouts'], {'pos': [], 'non_pos': [], 'split': []})
        totals = data['web_orders']['totals']
        self.assertIn(f"网站订单（不计入门店）: {totals['orders']} 单，${totals['cents'] / 100:.2f}", data['summary_text'])
        self.assertIn('#708 自取', data['summary_text'])
        self.assertIn('现金: 0 笔，$0.00', data['summary_text'])
        for value in ('K7R4MX', 'ACD346', 'Private', 'Zoë'):
            self.assertNotIn(value, response.get_data(as_text=True))

    def test_a_managers_own_summary_has_no_web_orders(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO employees(auth0_id, name) VALUES ('auth0|lead', 'Lead')")
            con.execute("""INSERT INTO shifts(employee_id, date, start_time, end_time, assigned_by, store_id)
                           VALUES ((SELECT id FROM employees WHERE auth0_id='auth0|lead'), ?, '09:00', '17:00', 'test', ?)""",
                        (business_date(), self.dt))
            con.commit()
        response = self.client.get(f'/api/store-events?store_id={self.dt}', headers=self.headers('manager:lead'))
        data = response.get_json()
        self.assertEqual((response.status_code, data['scope']), (200, 'personal'), data)
        self.assertNotIn('web_orders', data)
        self.assertNotIn('网站订单', data['summary_text'])
