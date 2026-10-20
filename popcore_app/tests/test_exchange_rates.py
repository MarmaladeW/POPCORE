"""Today's CAD→CNY reference rate for WeChat Pay and Alipay when Settings has none."""
import json
from contextlib import closing
from unittest.mock import patch

import requests

from test_receiving import ReceivingFixture
import exchange_rates

LOOKUP = exchange_rates.cny_per_cad  # the real lookup; every test case otherwise patches it off


def answer(payload, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    return result


class ExchangeRateTests(ReceivingFixture):
    def setUp(self):
        super().setUp()
        exchange_rates._cache.update(value=None, at=0.0, failed_at=0.0)
        self.addCleanup(exchange_rates._cache.update, value=None, at=0.0, failed_at=0.0)

    def test_rate_is_rounded_cached_and_has_a_second_source(self):
        ecb = answer({'base': 'CAD', 'date': '2026-10-20', 'rates': {'CNY': 4.75285}})
        with patch('exchange_rates.requests.get', return_value=ecb) as get:
            self.assertEqual(LOOKUP(), {'rate': '4.7529', 'date': '2026-10-20', 'source': 'European Central Bank'})
            LOOKUP()
        self.assertEqual(get.call_count, 1)  # looked up once an hour, not on every screen refresh
        exchange_rates._cache.update(value=None, at=0.0, failed_at=0.0)
        backup = answer({'result': 'success', 'time_last_update_utc': 'Tue, 20 Oct 2026 00:02:32 +0000',
                         'rates': {'CNY': 4.745375}})
        with patch('exchange_rates.requests.get', side_effect=[requests.ConnectionError('down'), backup]):
            self.assertEqual(LOOKUP(), {'rate': '4.7454', 'date': '20 Oct 2026', 'source': 'ExchangeRate-API'})

    def test_no_rate_when_every_source_fails_or_is_absurd(self):
        with patch('exchange_rates.requests.get', return_value=answer({'date': 'x', 'rates': {'CNY': 4000}})):
            self.assertIsNone(LOOKUP())
        with patch('exchange_rates.requests.get') as get:
            self.assertIsNone(LOOKUP())  # after a failure, nobody is asked again for a while
        get.assert_not_called()

    def test_yuan_are_worked_out_as_the_checkout_screen_does(self):
        # The same cases as convertCadCentsToCnyFen in the frontend's checkoutPricing.test.ts.
        self.assertEqual(exchange_rates.cad_to_fen(3200, '5.20'), 16640)
        self.assertEqual(exchange_rates.cad_to_fen(1, '5.5'), 6)
        self.assertEqual(exchange_rates.cad_to_fen(1, '0.5'), 1)
        self.assertEqual(exchange_rates.cad_to_fen(2400, '4.7528'), 11407)
        for rate in ('', '0', '5.12345', 'bad', '100.0001', '\uff15.2', None, 5.2):
            self.assertIsNone(exchange_rates.cad_to_fen(3200, rate), rate)
        for cents in (-1, 1.5, True):
            self.assertIsNone(exchange_rates.cad_to_fen(cents, '5.2'), cents)

    def test_access_offers_settings_first_then_the_live_rate(self):
        with patch('blueprints.checkout.exchange_rates.cny_per_cad',
                   return_value={'rate': '4.7528', 'date': '2026-10-20', 'source': 'European Central Bank'}):
            live = self.client.get('/api/checkouts/access', headers=self.headers('admin')).get_json()
        self.assertEqual((live['cny_per_cad'], live['cny_rate_source']),
                         ('4.7528', 'European Central Bank reference rate, 2026-10-20'))
        with closing(self.connect()) as con:
            con.execute("INSERT INTO app_settings(key,value) VALUES ('checkout_cny_per_cad','5.2500')")
            con.commit()
        with patch('blueprints.checkout.exchange_rates.cny_per_cad') as lookup:
            settings = self.client.get('/api/checkouts/access', headers=self.headers('admin')).get_json()
        self.assertEqual((settings['cny_per_cad'], settings['cny_rate_source']), ('5.2500', 'Settings'))
        lookup.assert_not_called()
