"""POP MART 价格 POP MART prices (owner, 2026-11-25, popcore-work#91: "add this to popcore.store, to make it a weekly
regular thing"; chose a compare screen, Monday 6:00 AM, and the March scraper removed): POPCORE reads POP MART CA and
US every Monday, keeps the newest good list, and shows managers same / we are higher / we are lower / not found. A
read that fails or finds far fewer products keeps the earlier list. Nothing here changes a price."""
import json
from contextlib import closing
from datetime import datetime, timezone

from support import IsolatedApiCase
import popmart_prices
from blueprints import popmart_prices as popmart_routes

MANAGER = 'manager'


def page_html(total, products):
    data = {'props': {'pageProps': {'serverSearchRaw': {'total': total, 'list': products}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def product(pid, title, *options):
    """options: (title, cents, stock[, sale cents])."""
    return {'id': pid, 'title': title, 'skus': [
        {'id': f'{pid}-{n}', 'title': option[0], 'price': option[1], 'discountPrice': option[3] if len(option) > 3 else option[1],
         'stock': {'onlineStock': option[2], 'onlineLockStock': 0}, 'currency': 'CAD'} for n, option in enumerate(options)]}


class FakePopmart:
    """Every search word answers the whole catalogue, 100 a page, as POP MART's search pages do."""

    def __init__(self, ca, us):
        self.catalogues, self.asked, self.broken = {'ca': ca, 'us': us}, [], None

    def __call__(self, region, term, page):
        self.asked.append((region, term, page))
        if self.broken == region:
            return '<html>Access denied</html>'
        items = self.catalogues[region]
        return page_html(len(items), items[(page - 1) * 100:page * 100])


ECHO = product(9001, 'Echo Series Figures', ('Single box\t', 2699, 1), ('Whole Set', 32388, 0))
WINGS = product(9002, 'LABUBU × Pronounce - Wings Plush', ('QUANTITY', 4999, 2))
DIMOO = product(9003, 'DIMOO Typo Series Figures', ('Single box', 1899, 0))
OTHER = product(9004, 'Hirono Mime Series Figures', ('Single box', 1999, 1))
US_ECHO = product(5501, 'Echo Series Figures', ('Single box', 1999, 0))


def moment(text):
    return datetime.strptime(text, '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)


class PopmartPriceTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(popmart_routes.bp)
        with closing(self.connect()) as con:
            add = lambda sku, name, price, form: con.execute(
                """INSERT INTO products(sku, name_cn_en, jizhanming, price, stock_form, identity_status, boxes_per_dan)
                   VALUES (?, ?, ?, ?, ?, 'verified', 12)""", (sku, name, name, price, form)).lastrowid
            self.echo = add('PM0100', 'Echo Series Figure', 26.99, 'random_box')          # same price, singular name
            self.echo_set = add('PM0100-SET', 'Echo Series Figure · Whole Set', 299.99, 'sealed_set')  # lower
            self.wings = add('PM0101', 'LABUBU x Pronounce Wings Plush', 50.00, 'ordinary')  # higher by a cent
            self.typo = add('PM0102', 'Dimoo Typo Seriees Figure', 18.99, 'random_box')     # our typo: not found
            self.design = add('PM0100-01', 'Echo Series Figure · Sun', 30.00, 'confirmed_design')  # never compared
            add('SA0001', 'Sonny Angel Animal Series', 15.99, 'random_box')                 # not POP MART
            con.commit()
        self.popmart = FakePopmart([ECHO, WINGS, DIMOO, OTHER], [US_ECHO])

    def read(self, trigger='manual', **kw):
        with closing(popmart_prices.connect()) as con:
            popmart_prices.run_read(con, trigger, fetch=self.popmart, sleep=lambda seconds: None, **kw)

    def run_due(self, at):
        popmart_prices.run_due(now=moment(at), fetch=self.popmart, sleep=lambda seconds: None)

    def listing(self, role=MANAGER):
        return self.client.get('/api/popmart-prices', headers=self.headers(role))

    def rows(self):
        response = self.listing()
        self.assertEqual(response.status_code, 200, response.get_json())
        return {row['sku']: row for row in response.get_json()['rows']}

    def reads(self):
        with closing(self.connect()) as con:
            return [dict(row) for row in con.execute('SELECT region, trigger, status, products, options, error FROM popmart_reads ORDER BY id')]

    def test_a_read_sets_popcore_beside_popmart_ca_and_us(self):
        self.read()
        self.assertEqual([(r['region'], r['status'], r['products'], r['options']) for r in self.reads()],
                         [('ca', 'ok', 4, 5), ('us', 'ok', 1, 1)])
        rows = self.rows()
        self.assertEqual(set(rows), {'PM0100', 'PM0100-SET', 'PM0101', 'PM0102'})  # POP MART boxes, sets, items only
        echo = rows['PM0100']
        self.assertEqual((echo['status'], echo['our_cents'], echo['diff_cents'], echo['kind']), ('same', 2699, 0, 'box'))
        self.assertEqual((echo['ca']['option'], echo['ca']['price_cents'], echo['ca']['in_stock'], echo['ca']['currency']),
                         ('Single box', 2699, True, 'CAD'))
        self.assertEqual(echo['ca']['url'], 'https://www.popmart.com/ca/products/9001')
        self.assertEqual((echo['us']['price_cents'], echo['us']['in_stock'], echo['us']['currency']), (1999, False, 'USD'))
        whole = rows['PM0100-SET']  # a whole set is POP MART's Whole Set option of its box
        self.assertEqual((whole['status'], whole['ca']['option'], whole['ca']['price_cents'], whole['diff_cents'], whole['ca']['in_stock']),
                         ('lower', 'Whole Set', 32388, 29999 - 32388, False))
        self.assertIsNone(whole['us']['price_cents'])  # US sells no whole set of it
        wings = rows['PM0101']  # × and x, a dash and plural s do not stop a match
        self.assertEqual((wings['status'], wings['diff_cents'], wings['ca']['option']), ('higher', 1, 'QUANTITY'))
        self.assertIsNone(wings['us'])
        typo = rows['PM0102']
        self.assertEqual((typo['status'], typo['ca'], typo['suggestions']), ('not_found', None, ['DIMOO Typo Series Figures']))
        self.assertEqual(self.listing().get_json()['counts'],
                         {'same': 1, 'higher': 1, 'lower': 1, 'not_found': 1, 'no_option': 0, 'no_price': 0})

    def test_a_manager_links_a_product_whose_name_differs_and_its_set_follows(self):
        self.read()
        response = self.client.put(f'/api/popmart-prices/links/{self.typo}', json={'title': 'DIMOO Typo Series Figures'},
                                   headers=self.headers(MANAGER))
        self.assertEqual(response.status_code, 200, response.get_json())
        typo = self.rows()['PM0102']
        self.assertEqual((typo['status'], typo['linked'], typo['ca']['price_cents'], typo['suggestions']), ('same', True, 1899, []))
        for body, product_id in (({'title': 'Echo Series Figures'}, self.echo_set), ({'title': 'Not on the list'}, self.typo)):
            response = self.client.put(f'/api/popmart-prices/links/{product_id}', json=body, headers=self.headers(MANAGER))
            self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(self.client.delete(f'/api/popmart-prices/links/{self.typo}', headers=self.headers(MANAGER)).status_code, 200)
        self.assertEqual(self.rows()['PM0102']['status'], 'not_found')

    def test_only_managers_see_or_start_it(self):
        for role in ('staff', 'viewer'):
            self.assertEqual(self.listing(role).status_code, 403)
            self.assertEqual(self.client.post('/api/popmart-prices/read', headers=self.headers(role)).status_code, 403)
            self.assertEqual(self.client.put(f'/api/popmart-prices/links/{self.typo}', json={'title': 'x'},
                                             headers=self.headers(role)).status_code, 403)

    def test_pages_are_read_to_the_end_of_each_word(self):
        self.popmart.catalogues['ca'] = [product(10000 + n, f'Series {n} Figures', ('Single box', 1599, 1)) for n in range(150)]
        self.read(regions=('ca',))
        self.assertEqual([asked for asked in self.popmart.asked if asked[1] == 'Series'], [('ca', 'Series', 1), ('ca', 'Series', 2)])
        self.assertEqual(len(self.popmart.asked), 2 * len(popmart_prices.TERMS))
        self.assertEqual(self.reads()[-1]['products'], 150)

    def test_a_failed_or_thin_read_keeps_the_earlier_list(self):
        self.read()
        self.popmart.broken = 'ca'
        self.read(regions=('ca',))
        self.assertEqual(self.reads()[-1]['status'], 'error')
        self.assertIn('no product data', self.reads()[-1]['error'])
        self.assertEqual(self.rows()['PM0100']['status'], 'same')  # the earlier list is still there
        self.popmart.broken = None
        self.popmart.catalogues['ca'] = [ECHO]  # 1 of 4 products: under 60% of the last good read
        self.read(regions=('ca',))
        self.assertEqual(self.reads()[-1]['status'], 'error')
        self.assertIn('only 1 products (the last good read had 4)', self.reads()[-1]['error'])
        self.assertEqual(self.rows()['PM0101']['status'], 'higher')
        self.popmart.catalogues['ca'] = [ECHO, WINGS, OTHER]  # 3 of 4: kept
        self.read(regions=('ca',))
        self.assertEqual(self.reads()[-1]['status'], 'ok')
        self.assertEqual(self.rows()['PM0102']['suggestions'], [])  # DIMOO Typo left POP MART's list

    def test_it_reads_every_monday_from_six_toronto_time_and_retries_an_hour_after_a_failure(self):
        # Monday 30 Nov 2026, 6:00 AM EST = 11:00 UTC. The deploy's first minute reads at once: no read this week yet.
        self.run_due('2026-11-25 23:00')
        self.assertEqual([(r['region'], r['trigger'], r['status']) for r in self.reads()], [('ca', 'weekly', 'ok'), ('us', 'weekly', 'ok')])
        self.run_due('2026-11-30 10:59')
        self.assertEqual(len(self.reads()), 2)  # still last week's read
        self.popmart.broken = 'us'
        self.run_due('2026-11-30 11:00')
        self.assertEqual([(r['region'], r['status']) for r in self.reads()[2:]], [('ca', 'ok'), ('us', 'error')])
        self.popmart.broken = None
        self.run_due('2026-11-30 11:30')
        self.assertEqual(len(self.reads()), 4)  # the failed region waits an hour
        self.run_due('2026-11-30 12:01')
        self.assertEqual([(r['region'], r['status']) for r in self.reads()[4:]], [('us', 'ok')])  # only US again
        self.run_due('2026-12-02 15:00')
        self.assertEqual(len(self.reads()), 5)  # done for the week
        self.assertEqual(popmart_prices.next_slot(moment('2026-12-02 15:00')), moment('2026-12-07 11:00'))
        self.assertEqual(popmart_prices.week_slot(moment('2026-07-13 10:00')), moment('2026-07-13 10:00'))  # 6:00 EDT
        self.assertEqual(popmart_prices.week_slot(moment('2026-07-13 09:59')), moment('2026-07-06 10:00'))
        self.assertEqual(popmart_prices.week_slot(moment('2026-11-30 04:30')), moment('2026-11-23 11:00'))  # Sunday night there

    def test_read_now_starts_within_a_minute_even_after_this_weeks_read(self):
        self.run_due('2026-11-30 11:00')
        response = self.client.post('/api/popmart-prices/read', headers=self.headers(MANAGER))
        self.assertEqual(response.status_code, 202)
        self.assertTrue(self.listing().get_json()['requested'])
        self.run_due('2026-11-30 11:01')
        self.assertEqual([(r['region'], r['trigger']) for r in self.reads()[2:]], [('ca', 'manual'), ('us', 'manual')])
        self.assertFalse(self.listing().get_json()['requested'])

    def test_a_read_cut_off_by_a_restart_is_closed_and_tried_again(self):
        with closing(self.connect()) as con:
            con.execute("INSERT INTO popmart_reads(region, trigger, started_at, status) VALUES ('ca', 'weekly', '2026-11-30 11:00:00', 'running')")
            con.commit()
        self.run_due('2026-11-30 11:10')
        self.assertEqual(len(self.reads()), 1)  # still running: nothing starts beside it
        self.run_due('2026-11-30 12:01')
        reads = self.reads()
        self.assertEqual((reads[0]['status'], reads[0]['error']), ('error', 'The read stopped before it finished (a restart?).'))
        self.assertEqual([(r['region'], r['status']) for r in reads[1:]], [('ca', 'ok'), ('us', 'ok')])

    def test_a_popmart_sale_price_is_what_a_shopper_pays(self):
        self.popmart.catalogues['ca'] = [product(9001, 'Echo Series Figures', ('Single box', 2999, 1, 2699)), WINGS, DIMOO, OTHER]
        self.read()
        echo = self.rows()['PM0100']['ca']
        self.assertEqual((echo['price_cents'], echo['regular_cents'], echo['sale_cents']), (2699, 2999, 2699))
        self.assertEqual(self.rows()['PM0100']['status'], 'same')

    def test_names_are_matched_without_punctuation_plurals_or_filler_words(self):
        norm = popmart_prices.norm
        self.assertEqual(norm('THE MONSTERS × FIFA SERIES - Football-shaped Mini Bag Blind Box'),
                         norm('THE MONSTERS x FIFA SERIES-Football-shaped Mini Bag'))
        self.assertEqual(norm("KUBO Angel’s Poem Series Figures"), norm("KUBO Angel's Poem Series Figure"))
        self.assertNotEqual(norm('MEGA SPACE MOLLY 400% Planet'), norm('MEGA SPACE MOLLY 100% Planet'))
