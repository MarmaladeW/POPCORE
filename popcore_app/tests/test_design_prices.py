"""确认款价格 Design prices (owner, 2026-11-12): a manager sets confirmed designs' prices in bulk in POPCORE, and the
website sync sends them like box prices, a jump of more than 30% waiting for a manager's OK ("Manager OK on big
jumps"). A design is created with its box's price; that copy is never sent. A design that only carried its box's
price on the website, or none, gets its first price without an OK, and the summary says so."""
import unittest
from contextlib import closing
from datetime import datetime, timezone
from unittest.mock import patch

from test_website_sync import WebsiteSyncTests, website_sync

import db
from blueprints import design_prices as design_prices_bp


def load_tests(loader, tests, pattern):
    """Only this file's tests: WebsiteSyncTests' own run from test_website_sync."""
    return unittest.TestSuite(DesignPriceTests(name) for name in loader.getTestCaseNames(DesignPriceTests)
                              if name in DesignPriceTests.__dict__)


class DesignPriceTests(WebsiteSyncTests):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(design_prices_bp.bp)
        # Echo's box has its Clover price; its designs are added as the 子项 list adds them, so each copies it.
        self.popcore_price(self.box, 20.99, 'Echo Clover', boxes=12)
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET ip_series='Echo IP', brand='POP MART' WHERE id=?", (self.box,))
            con.commit()
        response = self.client.post(f'/api/products/{self.box}/designs', json={'design_names': ['Sun', 'Moon', '隐藏 Secret Star']},
                                    headers={**self.headers('manager'), 'Idempotency-Key': 'designs-1'})
        self.assertEqual(response.status_code, 201, response.get_json())
        self.sun, self.moon, self.star = response.get_json()['product_ids']
        with closing(self.connect()) as con:  # Echo Rain (PM0100-03) belongs to the same box, at the box price too
            series = con.execute('SELECT series_id FROM products WHERE id=?', (self.box,)).fetchone()[0]
            con.execute("UPDATE products SET series_id=?, design_name='Rain', price=20.99 WHERE id=?", (series, self.design))
            self.assertEqual([tuple(row) for row in con.execute(
                "SELECT sku, price FROM products WHERE stock_form='confirmed_design' ORDER BY sku")],
                [('PM0100-03', 20.99), ('PM0100-04', 20.99), ('PM0100-05', 20.99), ('PM0100-06', 20.99)])
            con.commit()
        self.woo.set_price(11, '20.99')
        self.woo.set_price(12, '251.88')
        # popcore.ca on 2026-11-10: the published designs carry the owner's collector prices, the drafts their box's.
        self.woo.set_price(30, '95.00')                                      # Rain, published, its own price
        self.woo.product(31, 'PM0100-04', status='draft', price='20.99')     # Sun, a draft at the box price
        self.woo.product(32, 'PM0100-05', status='draft')                    # Moon, a draft with no price
        self.woo.product(33, 'PM0100-06', price='109.99', meta=[('_popcore_is_vault', '1'),
                                                                 ('_popcore_kind', 'confirmed_design')])  # the secret, published

    DESIGN_ITEMS = (30, 31, 32, 33)

    def designs(self, role='manager'):
        return self.client.get('/api/design-prices', headers=self.headers(role))

    def by_sku(self):
        return {design['sku']: design for design in self.designs().get_json()['designs']}

    def prices(self, prices, *, check=False, key=None, role='manager', stale=False):
        """Set {POPCORE ID: cents} as the screen does: each with the price the page showed."""
        shown = self.by_sku()
        changes = [{'product_id': shown[sku]['id'], 'cents': cents,
                    'expected_cents': shown[sku]['price_cents'] + 1 if stale else shown[sku]['price_cents']}
                   for sku, cents in prices.items()]
        headers = {**self.headers(role), 'Idempotency-Key': key or f'prices:{sorted(prices.items())}'}
        return self.client.post('/api/design-prices/check' if check else '/api/design-prices',
                                json={'changes': changes}, headers=headers)

    def design_writes(self, every=True):
        return {wc_id: price for wc_id, price in self.prices_written(every=every).items() if wc_id in self.DESIGN_ITEMS}

    def effects(self, result):
        return {change['sku']: [(item['wc_id'], item['state'], item['first'], item['reasons']) for item in change['website']]
                for change in result['changes']}

    def test_a_price_copied_from_the_box_is_never_sent(self):
        """Every design carries its box's price when it is added. Turning the sync on sends no design price, so
        the published collector prices ($95.00, $109.99) stay, and so do the drafts'. A box price written into
        designs some other way (a script, as on 2026-11-12, or a new box price) is never sent either, also
        after a manager once set the design's price."""
        self.turn_on()
        self.assertEqual(self.design_writes(), {})
        self.assertEqual([self.woo.item(wc_id)['regular_price'] for wc_id in self.DESIGN_ITEMS],
                         ['95.00', '20.99', '', '109.99'])
        preview = self.preview()
        self.assertEqual(preview['price_left']['design'], 4)
        self.assertFalse([row for row in preview['prices'] + preview['held_prices'] if row.get('popcore_id', '').startswith('PM0100-0')])
        # The box gets a new Clover price, and a script copies it into every design (as on 2026-11-12).
        self.popcore_price(self.box, 22.99, 'Echo Clover')
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET price=22.99 WHERE stock_form='confirmed_design'")
            con.commit()
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        self.assertEqual(self.prices_written(), {11: '22.99', 12: '275.88'})  # the box and its set only
        # A manager sets Sun's price: that one goes. Then a copy overwrites it: the website keeps the set price.
        self.assertEqual(self.prices({'PM0100-04': 4500}).status_code, 200)
        self.run_sync()
        self.assertEqual(self.design_writes(every=False), {31: '45.00'})
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET price=22.99 WHERE id=?', (self.sun,))
            con.commit()
        self.woo.writes.clear()
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        self.run_sync()
        self.assertEqual(self.design_writes(every=False), {})
        self.assertEqual(self.woo.item(31)['regular_price'], '45.00')
        self.assertEqual(self.by_sku()['PM0100-04']['state'], 'elsewhere')
        # Renaming a design in its editor sets no price; a price typed there is the design's own and goes.
        edit = self.client.post(f'/api/products/{self.moon}/design/edit', json={'design_name': 'Full Moon'},
                                headers=self.headers('manager'))
        self.assertEqual(edit.status_code, 200, edit.get_json())
        self.assertEqual(self.by_sku()['PM0100-05']['state'], 'unset')
        self.client.post(f'/api/products/{self.moon}/design/edit', json={'price': 31.5}, headers=self.headers('manager'))
        self.run_sync()
        self.assertEqual(self.design_writes(every=False), {32: '31.50'})
        with closing(self.connect()) as con:
            self.assertEqual([tuple(row) for row in con.execute(
                'SELECT product_id, cents, previous_cents, source, set_by FROM design_prices ORDER BY id')],
                [(self.sun, 4500, 2299, 'design_prices', 'auth0|manager'),
                 (self.moon, 3150, 2299, 'design_editor', 'auth0|manager')])

    def test_a_first_price_goes_and_a_big_jump_from_a_real_price_waits(self):
        """Sun's website price is its box's and Moon has none: each gets its first price, which is no jump. Rain
        and the secret are published with prices of their own: Rain's +4% goes, the secret's +36% waits for a
        manager's OK. The summary before saving says so, judged as the sync judges a write."""
        self.turn_on()
        new = {'PM0100-03': 9900, 'PM0100-04': 6000, 'PM0100-05': 3000, 'PM0100-06': 15000}
        check = self.prices(new, check=True)
        self.assertEqual(check.status_code, 200, check.get_json())
        result = check.get_json()
        self.assertEqual(self.effects(result), {
            'PM0100-03': [(30, 'send', False, [])], 'PM0100-04': [(31, 'send', True, [])],
            'PM0100-05': [(32, 'send', True, [])],
            'PM0100-06': [(33, 'held', False, [{'code': 'big_change', 'percent': 36}])]})
        self.assertEqual(result['counts'], {'changes': 4, 'first': 4, 'website_first': 2, 'send': 3, 'daily': 0,
                                            'held': 1, 'same': 0, 'not_linked': 0})
        self.assertEqual({change['sku']: (change['old_cents'], change['new_cents'], change['box_cents'])
                          for change in result['changes']}['PM0100-04'], (2099, 6000, 2099))
        self.assertEqual(self.by_sku()['PM0100-04']['price_cents'], 2099)  # the check saved nothing
        self.assertEqual(self.prices(new).get_json()['counts'], result['counts'])
        # The Website page says which design prices are first prices.
        self.assertEqual({row['popcore_id']: (row['price_state'], row['price_note']) for row in self.preview()['prices']
                          if row['popcore_id'].startswith('PM0100-0')},
                         {'PM0100-03': ('send', None), 'PM0100-04': ('send', 'first_design_price'),
                          'PM0100-05': ('send', 'first_design_price'), 'PM0100-06': ('held', None)})
        self.run_sync()
        self.assertEqual(self.design_writes(every=False), {30: '99.00', 31: '60.00', 32: '30.00'})
        preview = self.preview()
        (held,) = preview['held_prices']
        self.assertEqual([(option['popcore_id'], option['regular_cents'], option['price'], option['price_reasons'])
                          for option in held['options']], [('PM0100-06', 10999, 15000, [{'code': 'big_change', 'percent': 36}])])
        self.assertEqual(self.ok_prices(33).get_json(), {'ok': True})
        self.woo.writes.clear()
        self.run_sync()
        self.assertEqual(self.design_writes(every=False), {33: '150.00'})
        with closing(self.connect()) as con:
            self.assertEqual({tuple(row) for row in con.execute(
                "SELECT wc_id, old_cents, new_cents, approved_by FROM website_price_writes WHERE popcore_id LIKE 'PM0100-0%'")},
                {(30, 9500, 9900, None), (31, 2099, 6000, None), (32, None, 3000, None), (33, 10999, 15000, 'auth0|manager')})
        # From now on each is measured from the price POPCORE wrote, drafts too: Sun +50% waits.
        self.assertEqual(self.effects(self.prices({'PM0100-04': 9000}, check=True).get_json()),
                         {'PM0100-04': [(31, 'held', False, [{'code': 'big_change', 'percent': 50}])]})

    def test_a_drafts_price_is_not_its_own_until_popcore_writes_it_and_a_design_published_since_the_read_counts(self):
        """Customers cannot see a draft, and drafts mostly carry a box's price (often an old one), so a manager's
        first price for a draft goes without an OK, also from a hand-set draft price. A draft published since the
        daily read is judged as published when its items are read again just before the write."""
        self.woo.set_price(32, '40.00')  # Moon: a hand-set price on a draft
        self.turn_on()
        self.assertEqual(self.effects(self.prices({'PM0100-05': 6000}, check=True).get_json()),
                         {'PM0100-05': [(32, 'send', True, [])]})
        # Sun is published on the website after the read, still at the box's $20.99.
        self.prices({'PM0100-04': 6000})
        self.woo.products[31]['status'] = 'publish'
        self.run_sync()
        self.assertEqual(self.design_writes(every=False), {})
        self.assertEqual({option['wc_id']: option['price_reasons'] for group in self.preview()['held_prices']
                          for option in group['options']}, {31: [{'code': 'big_change', 'percent': 186}]})

    def test_each_design_price_write_marks_the_design_as_priced_by_popcore(self):
        """The website reads `_popcore_design_price_at` to show "Set by POPCORE" and lock the price field (website
        session, 2026-11-12). Every design price POPCORE writes sets it, in the same write, to the time of that write
        (UTC, ISO 8601), on a simple product or an option. Box prices carry none, and a pickup change in the same
        write keeps both fields."""
        twin = self.client.post(f'/api/products/{self.box}/designs', json={'design_names': ['Twin']},
                                headers={**self.headers('manager'), 'Idempotency-Key': 'twin'})
        self.assertEqual(twin.status_code, 201, twin.get_json())
        self.woo.set_id(70, 'PM0100-99')  # the fixture's unknown ID would be on two items now
        self.woo.product(95, kind='variable', name='Echo pair')  # a design sold as an option
        self.woo.variation(95, 96, 'PM0100-07', 'Twin')
        self.turn_on()
        # The secret's pickup changes in the same pass as its price: MK on the website, and no store has it.
        self.woo.products[33]['meta_data'].append({'key': '_popcore_pickup_stores', 'value': ['mk']})
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.popcore_price(self.box, 21.99, 'Echo Clover')  # and a box price
        self.assertEqual(self.prices({'PM0100-04': 6000, 'PM0100-06': 11999, 'PM0100-07': 4000}).status_code, 200)
        self.woo.writes.clear()
        with patch.object(website_sync, '_now', return_value=datetime(2026, 11, 12, 20, 15, 30, tzinfo=timezone.utc)):
            self.run_sync()
        marker = {'key': '_popcore_design_price_at', 'value': '2026-11-12T20:15:30+00:00'}
        writes = {update['id']: (path, update) for path, update in self.woo.writes}
        self.assertEqual(writes[31], ('/products/batch', {'id': 31, 'regular_price': '60.00', 'meta_data': [marker]}))
        self.assertEqual(writes[96], ('/products/95/variations/batch', {'id': 96, 'regular_price': '40.00', 'meta_data': [marker]}))
        self.assertEqual(writes[33], ('/products/batch', {'id': 33, 'regular_price': '119.99', 'meta_data': [
            marker, {'key': '_popcore_pickup_stores', 'value': []}]}))
        self.assertEqual((writes[11][1], writes[12][1]), ({'id': 11, 'regular_price': '21.99'}, {'id': 12, 'regular_price': '263.88'}))
        self.assertEqual([meta['value'] for meta in self.woo.item(33)['meta_data'] if meta['key'] in (
            '_popcore_design_price_at', '_popcore_pickup_stores')], ['2026-11-12T20:15:30+00:00', []])
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT pickup FROM website_items WHERE wc_id=33').fetchone()[0], '[]')
        # Written once; a later price writes the new time over it.
        self.woo.writes.clear()
        self.run_sync()
        self.assertEqual(self.woo.writes, [])
        self.prices({'PM0100-04': 6500})
        with patch.object(website_sync, '_now', return_value=datetime(2026, 11, 13, 1, 2, 3, tzinfo=timezone.utc)):
            self.run_sync()
        self.assertEqual(self.woo.writes, [('/products/batch', {'id': 31, 'regular_price': '65.00', 'meta_data': [
            {'key': '_popcore_design_price_at', 'value': '2026-11-13T01:02:03+00:00'}]})])
        self.assertEqual([meta['value'] for meta in self.woo.item(31)['meta_data'] if meta['key'] == '_popcore_design_price_at'],
                         ['2026-11-13T01:02:03+00:00'])

    def test_the_screen_lists_designs_by_box_with_their_prices_and_website_prices(self):
        self.assertEqual(self.designs('staff').status_code, 403)
        listing = self.designs().get_json()
        self.assertEqual(listing['website'], {'configured': True, 'enabled': False, 'refreshed_at': None})
        self.assertEqual(listing['look_pct'], 30)
        (box,) = listing['boxes']
        self.assertEqual({key: box[key] for key in ('id', 'sku', 'name', 'price_cents', 'ip', 'brand')},
                         {'id': self.box, 'sku': 'PM0100', 'name': 'Echo', 'price_cents': 2099, 'ip': 'Echo IP',
                          'brand': 'POP MART'})
        self.assertEqual([(row['sku'], row['name'], row['box_id'], row['price_cents'], row['state'], row['secret'], row['web'])
                          for row in listing['designs']],
                         [('PM0100-03', 'Rain', self.box, 2099, 'unset', False, []),
                          ('PM0100-04', 'Sun', self.box, 2099, 'unset', False, []),
                          ('PM0100-05', 'Moon', self.box, 2099, 'unset', False, []),
                          ('PM0100-06', '隐藏 Secret Star', self.box, 2099, 'unset', True, [])])
        self.client.post('/api/website/refresh', headers=self.headers('admin'))
        self.run_sync()
        self.assertEqual(self.prices({'PM0100-06': 12000}).status_code, 200)
        rows = self.by_sku()
        self.assertEqual([(item['wc_id'], item['status'], item['published'], item['regular_cents'], item['base_cents'])
                          for sku in ('PM0100-03', 'PM0100-04', 'PM0100-05', 'PM0100-06') for item in rows[sku]['web']],
                         [(30, 'publish', True, 9500, 9500), (31, 'draft', False, 2099, None), (32, 'draft', False, None, None),
                          (33, 'publish', True, 10999, 10999)])
        self.assertEqual((rows['PM0100-06']['state'], rows['PM0100-06']['price_cents'], rows['PM0100-06']['changed_today'],
                          rows['PM0100-06']['set_by']), ('set', 12000, True, 'auth0|manager'))
        self.assertFalse(rows['PM0100-04']['changed_today'])

    def test_a_save_is_refused_as_a_whole_when_a_price_changed_since_the_page_loaded(self):
        response = self.prices({'PM0100-04': 3000, 'PM0100-05': 3100}, stale=True)
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'prices_changed'))
        self.assertEqual({row['price_cents'] for row in self.by_sku().values()}, {2099})
        shown = self.by_sku()
        for change, why in (({'product_id': self.box, 'cents': 100, 'expected_cents': 2099}, 'not a confirmed design'),
                            ({'product_id': self.sun, 'cents': 0, 'expected_cents': 2099}, 'more than $0'),
                            ({'product_id': self.sun, 'cents': 12.5, 'expected_cents': 2099}, 'more than $0'),
                            ({'product_id': self.sun, 'cents': 100_000_001, 'expected_cents': 2099}, 'at most'),
                            ({'product_id': self.sun, 'cents': 100}, 'product_id, cents and expected_cents')):
            response = self.client.post('/api/design-prices', json={'changes': [change]},
                                        headers={**self.headers('manager'), 'Idempotency-Key': f'bad:{why}'})
            self.assertEqual(response.status_code, 400, why)
            self.assertIn(why, response.get_json()['error'])
        twice = [{'product_id': self.sun, 'cents': 100, 'expected_cents': 2099}] * 2
        self.assertEqual(self.client.post('/api/design-prices', json={'changes': twice},
                                          headers={**self.headers('manager'), 'Idempotency-Key': 'twice'}).status_code, 400)
        self.assertEqual(self.client.post('/api/design-prices', json={'changes': twice[:1]},
                                          headers=self.headers('manager')).status_code, 400)  # no Idempotency-Key
        self.assertEqual(self.prices({'PM0100-04': 3000}, role='staff').status_code, 403)
        # The same request sent twice saves once; the same price again changes nothing.
        body = {'changes': [{'product_id': self.sun, 'cents': 3000, 'expected_cents': 2099}]}
        headers = {**self.headers('manager'), 'Idempotency-Key': 'once'}
        first = self.client.post('/api/design-prices', json=body, headers=headers).get_json()
        self.assertEqual(first['saved'], 1)
        self.assertEqual(self.client.post('/api/design-prices', json=body, headers=headers).get_json(), first)
        self.assertEqual(self.prices({'PM0100-04': 3000}, key='again').get_json()['saved'], 0)
        self.assertEqual(shown['PM0100-04']['price_cents'], 2099)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM design_prices').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT price FROM products WHERE id=?', (self.sun,)).fetchone()[0], 30.0)

    def test_a_design_with_a_set_price_can_still_be_removed_and_the_upgrade_is_safe_to_rerun(self):
        self.prices({'PM0100-05': 3000})
        response = self.client.post(f'/api/products/{self.moon}/design/remove', headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM design_prices').fetchone()[0], 0)
            con.execute('DROP TABLE design_prices')
            con.execute("DELETE FROM _migrations WHERE name='design_prices'")
            con.commit()
        db.migrate_db()
        db.migrate_db()
        self.assertEqual(self.by_sku()['PM0100-04']['state'], 'unset')
        self.assertIsNotNone(website_sync)


if __name__ == '__main__':
    unittest.main()
