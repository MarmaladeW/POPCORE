"""记一笔 Quick sale, in place of 随手记 (owner, 2026-11-21): a whole sale saved in one tap at the app's prices, paid by
one tender, a photo asked for e-transfer, WeChat Pay and Alipay (saved without one, the sale says so), and the day
written as the 日报's 卡机汇总 and 随手记汇总. Its stock comes off only when the day's 日报 is reviewed and saved
("the stock change only at the end of the day at once after the report has been reviewed"); 撤销 takes back a sale
recorded by mistake."""
import io
from contextlib import closing
from unittest.mock import patch

from PIL import Image

from test_receiving import ReceivingFixture
import test_report_stock  # noqa: E402
import exchange_rates  # noqa: E402 - the fixture puts the app on the path
from inventory_commands import InventoryConflict  # noqa: E402

TODAY = '2026-11-21'


class QuickSaleFixture(ReceivingFixture):
    def setUp(self):
        super().setUp()
        clock = patch('checkout_access.business_date', return_value=TODAY)
        clock.start()
        self.addCleanup(clock.stop)
        with closing(self.connect()) as con:
            for subject, name in (('staff', 'Xiao Chen'), ('manager', 'Manager'), ('helper', 'Amy')):
                employee = con.execute('INSERT INTO employees(auth0_id,name) VALUES (?,?)',
                                       ('auth0|' + subject, name)).lastrowid
                if subject != 'helper':
                    con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                        VALUES (?,?,'10:00','18:00','test',?)""", (employee, TODAY, self.store_id))
            con.execute("INSERT INTO inventory_access(auth0_sub,store_id) VALUES ('auth0|manager',?)", (self.store_id,))
            con.execute("INSERT INTO inventory_access(auth0_sub,store_id) VALUES ('auth0|helper',?)", (self.store_id,))
            con.execute("UPDATE products SET jizhanming='蒙奇奇', price=20.0 WHERE id=?", (self.product_id,))
            # A series whose designs have no price of their own: they sell at the series' price.
            series = con.execute("INSERT INTO product_series(name) VALUES ('SA Santa')").lastrowid
            self.head = con.execute("""INSERT INTO products(sku,name_cn_en,jizhanming,price,stock_form,stock_unit,
                identity_status,series_id) VALUES ('DR0200','Sonny Angel Santa','SA Santa',14.99,'random_box','box',
                'verified',?)""", (series,)).lastrowid
            con.execute('UPDATE product_series SET product_id=? WHERE id=?', (self.head, series))
            self.design = con.execute("""INSERT INTO products(sku,name_cn_en,jizhanming,stock_form,stock_unit,
                identity_status,series_id,design_name) VALUES ('DR0200-01','Sonny Angel Santa · Reindeer',
                'SA Santa Reindeer','confirmed_design','piece','verified',?,'Reindeer')""", (series,)).lastrowid
            self.no_price = con.execute("""INSERT INTO products(sku,name_cn_en,jizhanming,stock_form,stock_unit,
                identity_status) VALUES ('PM0900','No Price Yet','没价钱','ordinary','piece','verified')""").lastrowid
            self.set_id = con.execute("""INSERT INTO products(sku,name_cn_en,jizhanming,price,stock_form,stock_unit,
                identity_status) VALUES ('DR0200-SET','SA Santa Whole Set','SA Santa 整端',179.88,'sealed_set','set',
                'verified')""").lastrowid
            con.execute("""INSERT INTO product_conversions(source_product_id,target_product_id,output_per_input,version)
                VALUES (?,?,12,1)""", (self.set_id, self.head))
            self.display = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='display'",
                                       (self.store_id,)).fetchone()[0]
            for product, location, quantity in ((self.product_id, self.floor, 5), (self.head, self.floor, 10),
                                                (self.set_id, self.floor, 2), (self.design, self.floor, 3),
                                                (self.product_id, self.display, 1)):
                con.execute("""INSERT INTO inventory_balances(product_id,location_id,disposition,quantity,version)
                    VALUES (?,?,'saleable',?,1)""", (product, location, quantity))
            con.commit()

    def one(self, sql, *args):
        with closing(self.connect()) as con:
            return con.execute(sql, args).fetchone()

    def balance(self, product_id, location=None):
        row = self.one("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                          AND disposition='saleable'""", product_id, location or self.floor)
        return row[0] if row else 0

    def quote(self, lines, role='staff', expect=200, **extra):
        response = self.client.post('/api/quick-sales/quote', json={'store_id': self.store_id, 'lines': lines, **extra},
                                    headers=self.headers(role))
        self.assertEqual(response.status_code, expect, response.get_json())
        return response.get_json()

    def save(self, lines, tender, key, role='staff', expect=201, amount=None, **extra):
        if amount is None:
            priced = self.quote(lines, role=role, **{k: v for k, v in extra.items() if k == 'discount_cents'})
            amount = priced['tenders'][tender]
            if tender in ('wechat', 'alipay') and priced['cny']:
                extra.setdefault('cny_fen', priced['cny']['fen'])
        response = self.client.post('/api/quick-sales', json={
            'store_id': self.store_id, 'lines': lines, 'tender': tender, 'expected_amount_cents': amount, **extra},
            headers={**self.headers(role), 'Idempotency-Key': key})
        self.assertEqual(response.status_code, expect, response.get_json())
        return response.get_json()

    def day(self, role='staff', expect=200, date=None):
        response = self.client.get('/api/quick-sales', query_string={'store_id': self.store_id,
                                                                      **({'date': date} if date else {})},
                                   headers=self.headers(role))
        self.assertEqual(response.status_code, expect, response.get_json())
        return response.get_json()


class QuickSaleTests(QuickSaleFixture):
    def test_quote_prices_the_cart_with_tax_and_the_cash_price(self):
        priced = self.quote([{'product_id': self.product_id, 'quantity': 2}, {'product_id': self.design, 'quantity': 1}])
        self.assertEqual([line['unit_price_cents'] for line in priced['lines']], [2000, 1499])
        self.assertEqual([line['price_from'] for line in priced['lines']], ['product', 'series'])
        self.assertEqual((priced['subtotal_cents'], priced['tax_cents'], priced['total_cents']), (5499, 715, 6214))
        # Card pays the total; every other tender the cash price ($62.14 → $60), cash rounded to 5¢.
        self.assertEqual(priced['tenders'], {'card': 6214, 'cash': 6000, 'e_transfer': 6000, 'wechat': 6000,
                                             'alipay': 6000})
        self.assertIsNone(priced['cny'])  # no rate set and no reference rate in tests

    def test_cash_is_rounded_to_five_cents_and_a_discount_floors_to_the_dollar(self):
        # $14.99 + HST = $16.94; the cash price is $15.
        self.assertEqual(self.quote([{'product_id': self.head, 'quantity': 1}])['tenders']['cash'], 1500)
        # An agreed discount: other tenders pay the whole dollar under it; cash the same, card the exact total.
        priced = self.quote([{'product_id': self.head, 'quantity': 1}], discount_cents=150)
        self.assertEqual(priced['total_cents'], 1544)
        self.assertEqual((priced['tenders']['card'], priced['tenders']['cash'], priced['tenders']['e_transfer']),
                         (1544, 1500, 1500))
        # A typed price replaces the list price.
        typed = self.quote([{'product_id': self.product_id, 'quantity': 1, 'unit_price_cents': 1800}])
        self.assertEqual((typed['lines'][0]['price_from'], typed['subtotal_cents']), ('typed', 1800))

    def test_a_cash_sale_is_saved_and_paid_in_one_tap_and_moves_no_stock(self):
        lines = [{'product_id': self.product_id, 'quantity': 2}, {'product_id': self.design, 'quantity': 1}]
        sale = self.save(lines, 'cash', 'qs-1')
        self.assertEqual((sale['status'], sale['reference'], sale['total_cents']), ('completed', 'DT-20261121-001', 6000))  # what was collected
        self.assertEqual([(p['tender'], p['amount_cents']) for p in sale['payments']], [('cash', 6000)])
        self.assertFalse(sale['needs_photo'])
        # The stock waits for the day's reviewed 日报.
        self.assertEqual((self.balance(self.product_id), self.balance(self.design)), (5, 3))
        document = self.one("""SELECT status, collected_cents, reduction_cents, allocation_status, allocation_reason,
                                      inventory_document_id FROM sale_documents WHERE id=?""", sale['sale_id'])
        self.assertEqual(tuple(document), ('posted', 6000, 214, 'allocated', 'daily_report', None))
        self.assertEqual(self.one("SELECT COUNT(*) FROM inventory_documents WHERE source_type='sale_document'")[0], 0)
        # The same tap again (a retry) is the same sale.
        again = self.save(lines, 'cash', 'qs-1', expect=201)
        self.assertEqual((again['id'], again['sale_id']), (sale['id'], sale['sale_id']))
        self.assertEqual(self.one('SELECT COUNT(*) FROM checkout_orders')[0], 1)
        line = self.one("""SELECT actor_name, kind, summary, detail, link, audience FROM activity_log
                           WHERE endpoint='checkout.quick_sale_save'""")
        self.assertEqual(tuple(line)[:2], ('Xiao Chen', 'sale'))
        self.assertIn('DT-20261121-001', line['summary'])
        self.assertEqual(line['detail'], '现金 Cash $60.00 · 蒙奇奇*2，SA Santa Reindeer*1')
        self.assertEqual((line['link'], line['audience']), ('/sales/quick', 'store'))
        self.assertEqual(self.one("SELECT COUNT(*) FROM activity_log WHERE endpoint='checkout.quick_sale_quote'")[0], 0)

    def test_a_changed_amount_or_a_missing_price_saves_nothing(self):
        stale = self.save([{'product_id': self.product_id, 'quantity': 1}], 'cash', 'qs-stale', amount=2000, expect=409)
        self.assertEqual(stale['code'], 'price_changed')
        priced = self.quote([{'product_id': self.no_price, 'quantity': 1}])
        self.assertEqual((priced['missing_prices'], priced['tenders']), (['没价钱'], None))
        missing = self.save([{'product_id': self.no_price, 'quantity': 1}], 'cash', 'qs-missing', amount=1, expect=409)
        self.assertEqual(missing['code'], 'price_missing')
        self.assertEqual(self.one('SELECT COUNT(*) FROM checkout_orders')[0], 0)
        # With a price typed in, it sells.
        sale = self.save([{'product_id': self.no_price, 'quantity': 1, 'unit_price_cents': 1000}], 'card', 'qs-typed')
        self.assertEqual(sale['payments'][0]['amount_cents'], 1130)

    def test_e_transfer_without_a_photo_is_flagged_until_one_is_added(self):
        sale = self.save([{'product_id': self.product_id, 'quantity': 1}], 'e_transfer', 'qs-emt')
        self.assertTrue(sale['needs_photo'])
        self.assertIn('没有照片 No photo', self.one("SELECT detail FROM activity_log WHERE endpoint='checkout.quick_sale_save'")[0])
        self.assertEqual(self.day()['needs_photo'], 1)
        file = io.BytesIO(); Image.new('RGB', (8, 8), 'red').save(file, format='PNG'); file.seek(0)
        attempt = sale['payments'][0]['id']
        response = self.client.post(f"/api/checkouts/{sale['id']}/attempts/{attempt}/evidence",
                                    headers={**self.headers(), 'Idempotency-Key': 'photo-1'},
                                    data={'image': (file, 'proof.png')})
        self.assertEqual(response.status_code, 201, response.get_json())
        today = self.day()
        self.assertEqual((today['needs_photo'], today['sales'][0]['needs_photo']), (0, False))
        # The photo reached the sale's payment, and 动态 says who added it at this store.
        self.assertEqual(self.one('SELECT COUNT(*) FROM payment_evidence')[0], 1)
        line = self.one("SELECT store_id, kind, actor_name FROM activity_log WHERE endpoint='checkout.upload'")
        self.assertEqual(tuple(line), (self.store_id, 'sale', 'Xiao Chen'))

    def test_wechat_is_charged_in_yuan_at_the_checkout_rate(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO app_settings(key,value) VALUES ('checkout_cny_per_cad','5.0000')")
            con.commit()
        lines = [{'product_id': self.product_id, 'quantity': 1}]
        priced = self.quote(lines)
        self.assertEqual(priced['cny'], {'rate': '5.0000', 'source': 'Settings', 'fen': 10500})  # $22.60 → cash price $21
        wrong = self.save(lines, 'wechat', 'qs-wx-wrong', cny_fen=9999, expect=409)
        self.assertEqual(wrong['code'], 'price_changed')
        sale = self.save(lines, 'wechat', 'qs-wx')
        self.assertEqual((sale['payments'][0]['tender'], sale['payments'][0]['cny_fen']), ('wechat', 10500))
        self.assertTrue(sale['needs_photo'])

    def test_the_reference_rate_is_used_when_settings_has_none(self):
        with patch('exchange_rates.cny_per_cad', return_value={'rate': '4.8000', 'date': TODAY, 'source': 'ECB'}):
            priced = self.quote([{'product_id': self.product_id, 'quantity': 1}])
        self.assertEqual(priced['cny'], {'rate': '4.8000', 'source': f'ECB {TODAY}',
                                         'fen': exchange_rates.cad_to_fen(2100, '4.8000')})

    def test_blind_boxes_over_half_a_set_and_more_than_on_record_save_without_questions(self):
        # Where the boxes came from, and a floor that shows too few, are the 日报's business now.
        lines = [{'product_id': self.head, 'quantity': 11}, {'product_id': self.design, 'quantity': 9}]
        priced = self.quote(lines)
        self.assertNotIn('box_needed', priced)
        self.assertTrue(all('box' not in line and 'short' not in line for line in priced['lines']))
        sale = self.save(lines, 'cash', 'qs-box')
        self.assertEqual(self.one('SELECT allocation_status FROM sale_documents WHERE id=?', sale['sale_id'])[0], 'allocated')
        self.assertEqual((self.balance(self.head), self.balance(self.set_id), self.balance(self.design)), (10, 2, 3))
        self.assertEqual(self.one('SELECT COUNT(*) FROM inventory_open_sets')[0], 0)

    def test_the_day_reads_like_the_daily_report(self):
        self.save([{'product_id': self.product_id, 'quantity': 1, 'from_display': True},
                   {'product_id': self.head, 'quantity': 2}], 'card', 'qs-card-1')
        self.save([{'product_id': self.head, 'quantity': 1}], 'card', 'qs-card-2')
        self.save([{'product_id': self.product_id, 'quantity': 1}, {'product_id': self.design, 'quantity': 1}],
                  'cash', 'qs-cash')
        self.save([{'product_id': self.head, 'quantity': 1}], 'e_transfer', 'qs-emt')
        self.assertEqual((self.balance(self.product_id, self.display), self.balance(self.product_id)), (1, 5))
        today = self.day()
        self.assertEqual([sale['cashier'] for sale in today['sales']], ['Xiao Chen'] * 4)
        self.assertEqual(today['summary_text'],
                         '2026.11.21 DT\n\n卡机汇总：\n蒙奇奇*1（display）\nSA Santa*3\n\n'
                         '随手记汇总：\n蒙奇奇*1，SA Santa Reindeer*1\nSA Santa*1\n')

    def test_only_someone_on_shift_with_store_access_records_a_sale(self):
        lines = [{'product_id': self.product_id, 'quantity': 1}]
        no_shift = self.quote(lines, role='staff:helper', expect=403)
        self.assertEqual(no_shift['code'], 'quick_sale_no_shift')
        self.day(role='staff:helper', expect=403)
        with closing(self.connect()) as con:
            con.execute("DELETE FROM inventory_access WHERE auth0_sub='auth0|staff'")
            con.commit()
        self.assertEqual(self.quote(lines, expect=403)['code'], 'quick_sale_no_access')

    def test_staff_wait_for_clover_where_the_owner_keeps_checkout_closed(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO app_settings(key,value) VALUES ('staff_checkout','clover')")
            con.commit()
        lines = [{'product_id': self.product_id, 'quantity': 1}]
        self.assertEqual(self.quote(lines, expect=403)['code'], 'checkout_needs_clover')
        self.save(lines, 'cash', 'qs-manager', role='manager')  # managers always may

    def test_staff_read_today_only_and_managers_any_day(self):
        self.save([{'product_id': self.product_id, 'quantity': 1}], 'cash', 'qs-1')
        self.day(date='2026-11-20', expect=403)
        self.assertEqual(self.day(role='manager', date=TODAY)['total_cents'], 2100)
        self.assertEqual(self.day(role='manager', date='2026-11-20')['sales'], [])

    def test_a_payment_that_fails_leaves_no_unpaid_order_behind(self):
        lines = [{'product_id': self.product_id, 'quantity': 1}]
        with patch('quick_sale.receive_manual', side_effect=InventoryConflict('Checkout changed.', 'checkout_state_conflict')):
            failed = self.save(lines, 'cash', 'qs-fail', expect=409)
        self.assertEqual(failed['code'], 'checkout_state_conflict')
        self.assertEqual(self.one('SELECT status FROM checkout_orders')[0], 'cancelled')
        self.assertEqual(self.balance(self.product_id), 5)
        # A new tap (new key) records it.
        self.assertEqual(self.save(lines, 'cash', 'qs-next')['status'], 'completed')


class UndoTests(QuickSaleFixture):
    """撤销: a sale recorded by mistake is taken back by its cashier that day, or by a manager; nothing is deleted."""

    def undo(self, sale, key, role='staff', expect=200, **body):
        response = self.client.post(f"/api/quick-sales/{sale['id']}/undo", json=body,
                                    headers={**self.headers(role), 'Idempotency-Key': key})
        self.assertEqual(response.status_code, expect, response.get_json())
        return response.get_json()

    def test_the_cashier_takes_back_their_own_sale_and_it_leaves_the_day(self):
        wrong = self.save([{'product_id': self.product_id, 'quantity': 1}], 'cash', 'qs-wrong')
        right = self.save([{'product_id': self.head, 'quantity': 1}], 'card', 'qs-right')
        self.assertTrue(wrong['can_undo'])
        undone = self.undo(wrong, 'undo-1', reason='应该是 SA Santa')
        self.assertEqual(undone['undone']['reason'], '记错了 Recorded by mistake · 应该是 SA Santa')
        self.assertEqual((undone['undone']['by'], undone['can_undo'], undone['can_add_photo']), ('Xiao Chen', False, False))
        # The money is corrected to zero with the reason; the sale and its payment stay in the history.
        event = self.one("SELECT event_type, direction, amount_cents, reason FROM payment_events")
        self.assertEqual(tuple(event)[:3], ('correction', 'decrease', 2100))
        self.assertEqual(self.one('SELECT COUNT(*) FROM sale_documents')[0], 2)
        today = self.day()
        self.assertEqual((today['count'], today['total_cents']), (1, right['total_cents']))
        self.assertEqual(today['summary_text'], '2026.11.21 DT\n\n卡机汇总：\nSA Santa*1\n\n随手记汇总：\n')
        self.assertEqual([bool(sale['undone']) for sale in today['sales']], [True, False])
        # The same tap again is the same undo; a second undo is refused.
        self.assertEqual(self.undo(wrong, 'undo-1', reason='应该是 SA Santa')['undone']['reason'], undone['undone']['reason'])
        self.assertEqual(self.undo(wrong, 'undo-2', expect=409)['code'], 'quick_sale_undone')
        line = self.one("SELECT kind, actor_name, detail FROM activity_log WHERE endpoint='checkout.quick_sale_undo'")
        self.assertEqual(tuple(line)[:2], ('sale', 'Xiao Chen'))
        self.assertIn('应该是 SA Santa', line['detail'])
        # The tenders and sales reports leave it out.
        tenders = self.client.get('/api/reports/tenders?store_code=DT&from=2026-11-21&to=2026-11-21', headers=self.headers('manager'))
        cash = [row for row in tenders.get_json()['items'] if row['tender'] == 'cash']
        self.assertEqual(cash[0]['recorded_cents'] if cash else 0, 0)
        sales = self.client.get('/api/reports/sales?store_code=DT&from=2026-11-21&to=2026-11-21', headers=self.headers('manager')).get_json()
        self.assertEqual([row['product_id'] for row in sales['items']], [self.head])

    def test_only_the_cashier_on_the_day_or_a_manager_takes_a_sale_back(self):
        sale = self.save([{'product_id': self.product_id, 'quantity': 1}], 'cash', 'qs-1')
        with closing(self.connect()) as con:
            employee = con.execute("INSERT INTO employees(auth0_id,name) VALUES ('auth0|other','Bo')").lastrowid
            con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                VALUES (?,?,'10:00','18:00','test',?)""", (employee, TODAY, self.store_id))
            con.execute("INSERT INTO inventory_access(auth0_sub,store_id) VALUES ('auth0|other',?)", (self.store_id,))
            con.commit()
        self.undo(sale, 'undo-other', role='staff:other', expect=403)
        with patch('checkout_access.business_date', return_value='2026-11-22'):
            self.undo(sale, 'undo-late', expect=403)
        self.assertIsNotNone(self.undo(sale, 'undo-manager', role='manager')['undone'])


    def test_a_sale_ticked_staff_discount_counts_one_use_per_product_until_it_is_undone(self):
        """员工折扣 in the app (owner, 2026-11-24): the sale names who bought; it is a sale like any other."""
        with closing(self.connect()) as con:
            amy = con.execute("UPDATE employees SET rank='regular' WHERE name='Amy' RETURNING id").fetchone()[0]
            con.commit()
        lines = [{'product_id': self.product_id, 'quantity': 3}, {'product_id': self.head, 'quantity': 1}]
        sale = self.save(lines, 'e_transfer', 'qs-staff', discount_cents=1000, staff_discount_employee_id=amy)
        self.assertEqual(sale['staff_discount'], 'Amy')
        self.assertIsNone(self.save([{'product_id': self.head, 'quantity': 1}], 'cash', 'qs-plain')['staff_discount'])
        self.save(lines, 'cash', 'qs-nobody', expect=400, staff_discount_employee_id=99999)

        def page(role='manager'):
            response = self.client.get('/api/staff-discounts?month=2026-11', headers=self.headers(role))
            self.assertEqual(response.status_code, 200, response.get_json())
            return response.get_json()
        month = page()
        use = month['uses'][0]
        # Two different products are two uses ("purchasing ten smiski hipper is still one employee discount").
        self.assertEqual((len(month['uses']), use['name'], use['uses'], use['payment'], use['amount_cents'], use['store']),
                         (1, 'Amy', 2, 'emt', sale['total_cents'], 'DT'))
        self.assertEqual([item['qty'] for item in use['items']], [3, 1])
        person = next(row for row in month['people'] if row['name'] == 'Amy')
        self.assertEqual((person['used'], person['left']), (2, 2))
        # Read again (by anyone), it is still one record; staff do not see what was paid.
        self.assertNotIn('amount_cents', page('staff')['uses'][0])
        self.assertEqual(len(page()['uses']), 1)
        # Undone, it stops counting and stays on the page with why.
        self.undo(sale, 'undo-staff')
        month = page()
        self.assertEqual((month['uses'], month['ignored'][0]['ignored_reason']), ([], '这一笔已撤销 Sale undone'))
        self.assertEqual(next(row for row in month['people'] if row['name'] == 'Amy')['left'], 4)


class NotCountedTests(QuickSaleFixture):
    """不计库存 (owner, 2026-11-21: "mark these one as reviewed, we are not going to count their stock. and yes we do sell
    them"): a manager reviews an accessory as an ordinary piece that is never counted; it sells in 记一笔 with no stock
    move and no manager follow-up."""

    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.outfit = con.execute("""INSERT INTO products(sku,name_cn_en,jizhanming,price,brand,identity_status)
                VALUES ('AC0013','Outfit $20','娃衣 $20',20.0,'Accessories','unverified')""").lastrowid
            con.commit()

    def mark(self, product, body, expect=200):
        response = self.client.patch(f'/api/products/{product}', json=body, headers=self.headers('manager'))
        self.assertEqual(response.status_code, expect, response.get_json())
        return response.get_json()

    def test_a_manager_reviews_it_as_not_counted_and_it_sells_without_moving_stock(self):
        self.mark(self.outfit, {'identity_status': 'verified', 'stock_form': 'ordinary', 'stock_unit': 'piece',
                                'stock_counted': False})
        product = self.one('SELECT identity_status, stock_form, stock_unit, stock_counted FROM products WHERE id=?', self.outfit)
        self.assertEqual(tuple(product), ('verified', 'ordinary', 'piece', 0))
        lines = [{'product_id': self.outfit, 'quantity': 2}, {'product_id': self.product_id, 'quantity': 1}]
        self.save(lines, 'cash', 'qs-outfit')
        self.assertEqual((self.balance(self.outfit), self.balance(self.product_id)), (0, 5))
        self.assertEqual(self.one("SELECT COUNT(*) FROM inventory_balances WHERE product_id=?", self.outfit)[0], 0)
        # Alone in a sale it needs no stock document at all, and nothing waits for a manager.
        alone = self.save([{'product_id': self.outfit, 'quantity': 1}], 'card', 'qs-outfit-alone')
        document = self.one('SELECT allocation_status, inventory_document_id FROM sale_documents WHERE id=?', alone['sale_id'])
        self.assertEqual(tuple(document), ('allocated', None))
        self.assertEqual(self.day()['summary_text'],
                         '2026.11.21 DT\n\n卡机汇总：\n娃衣 $20*1\n\n随手记汇总：\n娃衣 $20*2，蒙奇奇*1\n')

    def test_only_an_ordinary_product_without_stock_on_record_goes_uncounted(self):
        self.assertEqual(self.mark(self.product_id, {'stock_counted': False}, expect=409)['code'], 'stock_on_record')
        self.assertEqual(self.mark(self.head, {'stock_counted': False}, expect=400)['code'], 'invalid_input')
        self.mark(self.outfit, {'identity_status': 'verified', 'stock_form': 'ordinary', 'stock_unit': 'piece',
                                'stock_counted': False})
        self.mark(self.outfit, {'stock_counted': True})
        self.assertEqual(self.one('SELECT stock_counted FROM products WHERE id=?', self.outfit)[0], 1)
        self.assertEqual(self.mark(self.outfit, {'stock_counted': 'no'}, expect=400)['code'], 'invalid_input')


class ReportTakesQuickSalesTests(test_report_stock.ReportStockTests):
    """记一笔 sales come off the floor once, when the day's 日报 is reviewed and saved; a 买单 checkout that took its own
    stock is not taken again."""
    DAY = test_report_stock.LIVE_DATE

    def setUp(self):
        super().setUp()
        clock = patch('checkout_access.business_date', return_value=self.DAY)
        clock.start()
        self.addCleanup(clock.stop)
        with closing(self.connect()) as con:
            employee = con.execute("INSERT INTO employees(auth0_id,name) VALUES ('auth0|staff','Xiao Chen')").lastrowid
            con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                VALUES (?,?,'10:00','18:00','test',?)""", (employee, self.DAY, self.dt))
            con.execute('UPDATE products SET price=14.99 WHERE id=?', (self.box,))
            con.execute('UPDATE products SET price=20 WHERE id=?', (self.item,))
            series = con.execute("INSERT INTO product_series(name, product_id) VALUES ('Echo', ?)", (self.box,)).lastrowid
            con.execute('UPDATE products SET series_id=? WHERE id=?', (series, self.box))
            self.design = con.execute("""INSERT INTO products(sku,name_cn_en,jizhanming,stock_form,stock_unit,identity_status,
                series_id,design_name) VALUES ('ECHO-01','Echo · Star','Echo Star','confirmed_design','piece','verified',?,
                'Star')""", (series,)).lastrowid
            for product, code, quantity in ((self.design, 'floor', 2), (self.item, 'display', 1)):
                con.execute("""INSERT INTO inventory_balances(product_id,location_id,disposition,quantity,version)
                    VALUES (?,?,'saleable',?,1)""", (product, self.locations[code], quantity))
            con.commit()

    def sell(self, lines, key, tender='cash'):
        priced = self.client.post('/api/quick-sales/quote', json={'store_id': self.dt, 'lines': lines},
                                  headers=self.headers()).get_json()
        response = self.client.post('/api/quick-sales', json={
            'store_id': self.dt, 'lines': lines, 'tender': tender, 'expected_amount_cents': priced['tenders'][tender]},
            headers={**self.headers(), 'Idempotency-Key': key})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def paste(self, items):
        def submit(**extra):
            return self.client.post('/api/sales/submit_daily_report', json={
                'date': self.DAY, 'store_code': 'DT', 'mode': 'replace', 'classification': 'summary_only',
                'stock_mode': 'live', 'items': items, **extra}, headers=self.headers('manager'))
        preview = submit(dry_run=True)
        self.assertEqual(preview.status_code, 200, preview.get_json())
        saved = submit(stock_digest=preview.get_json()['stock_digest'])
        self.assertEqual(saved.status_code, 200, saved.get_json())
        return preview.get_json()['stock'], saved.get_json()['stock']

    @staticmethod
    def sale(product, quantity, section='pos'):
        return {'product_id': product, 'section': section, 'qty_pos': quantity, 'qty_cash': 0, 'raw_name': str(product)}

    def at(self, product, code):
        with closing(self.connect()) as con:
            row = con.execute("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                                 AND disposition='saleable'""", (product, self.locations[code])).fetchone()
            return row[0] if row else 0

    def checkout(self, product, quantity, key, unit='box', price=1499):
        """A 买单 manual order paid by card: it takes its stock when it is paid."""
        subtotal = price * quantity
        tax = (subtotal * 13 + 50) // 100
        created = self.client.post('/api/checkouts', json={
            'store_id': self.dt, 'business_date': self.DAY, 'subtotal_cents': subtotal, 'source_tax_cents': tax,
            'gross_cents': subtotal + tax, 'reduction_cents': 0, 'rounding_cents': 0, 'collected_cents': subtotal + tax,
            'lines': [{'product_id': product, 'unit': unit, 'quantity': quantity, 'unit_price_cents': price}]},
            headers={**self.headers(), 'Idempotency-Key': key})
        self.assertEqual(created.status_code, 201, created.get_json())
        paid = self.client.post(f"/api/checkouts/{created.get_json()['id']}/receive", json={
            'tender': 'card', 'plan': 'pay', 'amount_cents': subtotal + tax, 'expected_version': created.get_json()['version']},
            headers={**self.headers(), 'Idempotency-Key': key + '-pay'})
        self.assertEqual(paid.status_code, 200, paid.get_json())

    def test_quick_sales_come_off_once_when_the_report_is_saved(self):
        self.sell([{'product_id': self.box, 'quantity': 2}, {'product_id': self.item, 'quantity': 1}], 'qs-1')
        self.assertEqual((self.floor(self.box), self.floor(self.item)), (30, 3))
        preview, saved = self.paste([self.sale(self.box, 2), self.sale(self.item, 1)])
        self.assertEqual((preview['in_app'], preview['taken']), ([], 3))
        self.assertEqual((saved['taken'], self.floor(self.box), self.floor(self.item)), (3, 28, 2))
        # Saved again unchanged, nothing more moves.
        _, again = self.paste([self.sale(self.box, 2), self.sale(self.item, 1)])
        self.assertEqual((again['taken'], self.floor(self.box)), (0, 28))

    def test_a_display_piece_comes_off_display_with_the_report(self):
        self.sell([{'product_id': self.item, 'quantity': 1, 'from_display': True}], 'qs-display')
        self.assertEqual(self.at(self.item, 'display'), 1)
        self.paste([self.sale(self.item, 1), {'product_id': self.item, 'section': 'display_sold', 'qty': 1, 'raw_name': 'Bear'}])
        self.assertEqual((self.at(self.item, 'display'), self.floor(self.item)), (0, 3))

    def test_a_checkout_that_took_its_own_stock_is_not_taken_again(self):
        self.checkout(self.box, 2, 'co-1')
        self.assertEqual(self.floor(self.box), 28)
        preview, saved = self.paste([self.sale(self.box, 2)])
        self.assertEqual([(row['product_id'], row['quantity']) for row in preview['in_app']], [(self.box, 2)])
        self.assertEqual((saved['taken'], self.floor(self.box)), (0, 28))
        # A design sold at checkout covers the same sale written under its series' box.
        self.checkout(self.design, 1, 'co-2', unit='piece')
        _, saved = self.paste([self.sale(self.box, 3)])
        self.assertEqual((self.floor(self.box), self.floor(self.design)), (28, 1))

    def test_the_evening_count_leaves_out_todays_quick_sales_until_the_report_is_saved(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET is_bestseller=1 WHERE id=?', (self.box,))
            con.commit()
        clock = patch('blueprints.inventory.business_date', return_value=self.DAY)
        clock.start()
        self.addCleanup(clock.stop)

        def expected():
            response = self.client.get('/api/inventory-check/today?store_code=DT', headers=self.headers('manager'))
            self.assertEqual(response.status_code, 200, response.get_json())
            item = {row['product_id']: row for row in response.get_json()['items']}[self.box]
            return item['expected_sets'], item['expected_loose']

        self.sell([{'product_id': self.box, 'quantity': 3}], 'qs-1')
        self.assertEqual((self.floor(self.box), expected()), (30, (0, 27)))
        counted = self.client.post('/api/inventory-check/submit', json={'store_code': 'DT', 'checks': [
            {'product_id': self.box, 'sets': 0, 'loose': 27}]}, headers=self.headers())
        self.assertEqual(counted.get_json()['differ'], 0)
        # Once the 日报 is saved, its sales are off the floor and nothing is taken off twice.
        self.paste([self.sale(self.box, 3)])
        self.assertEqual((self.floor(self.box), expected()), (27, (0, 27)))

    def test_a_product_not_counted_comes_off_nothing_and_leaves_no_notice(self):
        with closing(self.connect()) as con:
            bag = con.execute("""INSERT INTO products(sku,name_cn_en,jizhanming,stock_form,stock_unit,identity_status,
                stock_counted) VALUES ('AC0019','Shopping bag','购物袋','ordinary','piece','verified',0)""").lastrowid
            con.commit()
        _, saved = self.paste([self.sale(bag, 3), self.sale(self.box, 1)])
        self.assertEqual((saved['taken'], saved['short']), (1, []))
        self.assertNotIn(bag, {product for product, _ in self.notices()})

class ReviewDayTests(ReportTakesQuickSalesTests):
    """审核 (owner, 2026-11-24: "once the 记一笔 is fully built, we dont need the 日报 pasting anymore"): a manager
    reviews the day's quick sales in the app; that saves them as the day's sales by product id and takes them off
    stock once. A day is reviewed or pasted, never both."""

    def review(self, day=None, role='manager', expect=200, **extra):
        response = self.client.post('/api/sales/submit_daily_report', json={
            'source': 'quick_sale', 'date': day or self.DAY, 'store_code': 'DT',
            # Whatever else the screen sends is not used.
            'items': [{'product_id': self.item, 'section': 'pos', 'qty_pos': 99}], **extra}, headers=self.headers(role))
        if response.status_code == 409 and response.get_json().get('code') == 'stock_confirmation_required' and expect == 200:
            # A display piece (a stock line) is confirmed with the preview's digest, as the screen does.
            return self.review(day, role, expect, **{**extra, 'stock_digest': response.get_json()['stock_digest']})
        self.assertEqual(response.status_code, expect, response.get_json())
        return response.get_json()

    def state(self, day=None, role='manager'):
        response = self.client.get('/api/quick-sales', query_string={'store_id': self.dt, 'date': day or self.DAY},
                                   headers=self.headers(role))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def sold(self, product):
        with closing(self.connect()) as con:
            row = con.execute('SELECT SUM(qty_pos), SUM(qty_cash), SUM(qty_sold) FROM daily_sales WHERE date=? AND product_id=?',
                              (self.DAY, product)).fetchone()
            return tuple(row)

    def test_a_review_saves_the_days_sales_and_takes_them_off_once(self):
        self.sell([{'product_id': self.box, 'quantity': 2}, {'product_id': self.item, 'quantity': 1}], 'qs-1')
        self.sell([{'product_id': self.box, 'quantity': 1}], 'qs-2', tender='card')
        self.assertEqual(self.state()['review']['state'], 'none')
        preview = self.review(dry_run=True)
        self.assertEqual((preview['dry_run'], preview['stock']['taken']), (True, 4))
        self.assertEqual((self.floor(self.box), self.floor(self.item), self.state()['review']['state']), (30, 3, 'none'))
        saved = self.review()
        self.assertEqual((saved['stock']['taken'], self.floor(self.box), self.floor(self.item)), (4, 27, 2))
        # The day's sales history comes from the sales themselves: card as 卡机, the rest as 随手记.
        self.assertEqual((self.sold(self.box), self.sold(self.item)), ((1, 2, 3), (0, 1, 1)))
        day = self.state()
        self.assertEqual((day['review']['state'], day['review']['by'], day['unreviewed_days']), ('reviewed', '没有员工资料 No staff profile', []))
        # Reviewed again unchanged, nothing moves.
        self.assertEqual((self.review()['stock']['taken'], self.floor(self.box)), (0, 27))
        with closing(self.connect()) as con:
            line = con.execute("SELECT kind, summary, detail FROM activity_log WHERE endpoint='sales.submit_daily_report' ORDER BY id").fetchone()
        self.assertEqual((line['kind'], line['summary'], line['detail']), ('sale', f'审核 Reviewed the day · DT {self.DAY}', '4 件 sold · 扣 4 off floor'))

    def test_the_review_keeps_the_cash_the_manager_counted(self):
        """现金 in the app (owner, 2026-11-24): the day shows its cash sales; the review may record the cash counted."""
        cash = self.sell([{'product_id': self.box, 'quantity': 2}], 'qs-1')
        self.sell([{'product_id': self.item, 'quantity': 1}], 'qs-2', tender='card')
        day = self.state()
        self.assertEqual((day['cash_cents'], day['review']['cash_counted_cents']), (cash['total_cents'], None))
        self.review(cash_counted_cents=93505)
        self.assertEqual(self.state()['review']['cash_counted_cents'], 93505)
        # It is kept where a pasted 日报's cash line was, so the 汇总 reads it the same way.
        kept = self.client.get(f'/api/sales/report-metadata?date={self.DAY}&store=DT', headers=self.headers('manager')).get_json()
        self.assertEqual((kept['cash_actual'], kept['cash_expected']), (935.05, None))
        self.review(cash_counted_cents=-1, expect=400)
        self.review(cash_counted_cents='lots', expect=400)
        self.assertEqual(self.state()['review']['cash_counted_cents'], 93505)

    def test_only_a_manager_reviews_and_an_empty_day_has_nothing_to_review(self):
        self.assertEqual(self.review(expect=409)['code'], 'nothing_to_review')
        self.sell([{'product_id': self.box, 'quantity': 1}], 'qs-1')
        self.review(role='staff', expect=403)
        self.assertEqual(self.floor(self.box), 30)

    def test_a_display_piece_leaves_display_and_a_whole_set_leaves_as_boxes(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET price=179.88 WHERE id=?', (self.set,))
            con.commit()
        self.sell([{'product_id': self.item, 'quantity': 1, 'from_display': True}, {'product_id': self.set, 'quantity': 1}], 'qs-1')
        self.review()
        self.assertEqual((self.at(self.item, 'display'), self.floor(self.item), self.floor(self.box)), (0, 3, 18))

    def test_sales_after_the_review_and_an_undo_move_only_the_difference(self):
        first = self.sell([{'product_id': self.box, 'quantity': 2}], 'qs-1')
        self.review()
        self.sell([{'product_id': self.box, 'quantity': 3}], 'qs-late')
        self.assertEqual((self.floor(self.box), self.state()['review']['state']), (28, 'changed'))
        self.assertEqual((self.review()['stock']['taken'], self.floor(self.box), self.state()['review']['state']), (3, 25, 'reviewed'))
        undone = self.client.post(f"/api/quick-sales/{first['id']}/undo", json={}, headers={**self.headers(), 'Idempotency-Key': 'undo-1'})
        self.assertEqual((undone.status_code, undone.get_json()['report_saved']), (200, True))
        self.assertEqual(self.state()['review']['state'], 'changed')
        again = self.review()
        self.assertEqual((again['stock']['returned'], self.floor(self.box), self.sold(self.box)), (2, 27, (0, 3, 3)))

    def test_a_day_is_reviewed_or_pasted_never_both(self):
        self.sell([{'product_id': self.box, 'quantity': 2}], 'qs-1')
        self.review()
        pasted = self.client.post('/api/sales/submit_daily_report', json={
            'date': self.DAY, 'store_code': 'DT', 'mode': 'replace', 'classification': 'summary_only', 'stock_mode': 'live',
            'items': [self.sale(self.box, 2)], 'dry_run': True}, headers=self.headers('manager'))
        self.assertEqual((pasted.status_code, pasted.get_json()['code']), (409, 'day_reviewed_in_app'))
        self.assertEqual(self.floor(self.box), 28)

    def test_a_pasted_day_cannot_be_reviewed_and_shows_where_the_two_differ(self):
        # Both kept side by side (随手记 and its pasted 日报, with 记一笔 alongside): the paste moves the stock.
        self.sell([{'product_id': self.box, 'quantity': 2}, {'product_id': self.item, 'quantity': 1}], 'qs-1')
        self.paste([self.sale(self.box, 3)])
        self.assertEqual(self.floor(self.box), 27)
        self.assertEqual(self.review(expect=409)['code'], 'day_pasted')
        day = self.state()
        self.assertEqual(day['review']['state'], 'pasted')
        self.assertEqual([(row['product_id'], row['quick'], row['report']) for row in day['review']['differs']],
                         sorted([(self.box, 2, 3), (self.item, 1, 0)]))
        self.assertEqual((self.floor(self.box), self.floor(self.item)), (27, 3))

    def test_the_evening_count_still_leaves_out_an_earlier_day_nobody_reviewed(self):
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET is_bestseller=1 WHERE id=?', (self.box,))
            con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id)
                SELECT id,'2026-11-02','10:00','18:00','test',? FROM employees WHERE auth0_id='auth0|staff'""", (self.dt,))
            con.commit()
        self.sell([{'product_id': self.box, 'quantity': 3}], 'qs-day-1')
        for target in ('checkout_access.business_date', 'blueprints.inventory.business_date'):
            clock = patch(target, return_value='2026-11-02')
            clock.start()
            self.addCleanup(clock.stop)
        self.sell([{'product_id': self.box, 'quantity': 1}], 'qs-day-2')

        def expected():
            items = self.client.get('/api/inventory-check/today?store_code=DT', headers=self.headers('manager')).get_json()['items']
            return {row['product_id']: row for row in items}[self.box]['expected_loose']

        self.assertEqual((self.floor(self.box), expected()), (30, 26))
        self.assertEqual(self.state('2026-11-02')['unreviewed_days'], [self.DAY])
        self.review(self.DAY)
        self.assertEqual((self.floor(self.box), expected()), (27, 26))
        self.review('2026-11-02')
        self.assertEqual((self.floor(self.box), expected()), (26, 26))

    def test_a_retry_is_the_same_sale_even_after_a_price_change(self):
        lines = [{'product_id': self.box, 'quantity': 1}]
        sale = self.sell(lines, 'qs-1')
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET price=99 WHERE id=?', (self.box,))
            con.commit()
        again = self.client.post('/api/quick-sales', json={'store_id': self.dt, 'lines': lines, 'tender': 'cash',
                                                           'expected_amount_cents': 1500},
                                 headers={**self.headers(), 'Idempotency-Key': 'qs-1'})
        self.assertEqual((again.status_code, again.get_json()['id']), (201, sale['id']))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM checkout_orders').fetchone()[0], 1)

    def test_an_undone_sale_leaves_the_summary_page_and_photos_are_no_home_tasks(self):
        wrong = self.sell([{'product_id': self.box, 'quantity': 1}], 'qs-1', tender='e_transfer')
        self.sell([{'product_id': self.item, 'quantity': 1}], 'qs-2')
        file = io.BytesIO(); Image.new('RGB', (8, 8), 'red').save(file, format='PNG'); file.seek(0)
        photo = self.client.post(f"/api/checkouts/{wrong['id']}/attempts/{wrong['payments'][0]['id']}/evidence",
                                 headers={**self.headers(), 'Idempotency-Key': 'photo-1'}, data={'image': (file, 'proof.png')})
        self.assertEqual(photo.status_code, 201, photo.get_json())
        tasks = self.client.get(f'/api/today?store_code=DT&business_date={self.DAY}', headers=self.headers()).get_json()
        self.assertEqual([row['type'] for row in tasks['sections']['my_work']['rows']], [])
        manager = self.client.get(f'/api/today?store_code=DT&business_date={self.DAY}', headers=self.headers('manager')).get_json()
        self.assertNotIn('payment_exception', {row['type'] for row in manager['sections']['financial']['rows']})
        self.client.post(f"/api/quick-sales/{wrong['id']}/undo", json={}, headers={**self.headers(), 'Idempotency-Key': 'undo-1'})
        summary = self.client.get(f'/api/store-events?store_id={self.dt}&business_date={self.DAY}', headers=self.headers())
        self.assertEqual(summary.status_code, 200, summary.get_json())
        self.assertNotIn(wrong['reference'], summary.get_data(as_text=True))


for _name in [name for name in vars(test_report_stock.ReportStockTests) if name.startswith('test_')]:
    setattr(ReportTakesQuickSalesTests, _name, None)
for _name in [name for name in vars(ReportTakesQuickSalesTests) if name.startswith('test_')]:
    setattr(ReviewDayTests, _name, None)


if __name__ == '__main__':
    import unittest
    unittest.main()
