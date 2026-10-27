"""Clover checkout pricing, sale money and keys (pure functions)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import clover_pricing as pricing  # noqa: E402
from sales_operations import _optional_cents  # noqa: E402,F401  (same app path as production)


def order(*prices, total, discounts=(), payments=(), **extra):
    return {'id': 'ABCDEFGHIJKLM', 'currency': 'CAD', 'total': total,
            'items': [{'id': f'L{n:012d}', 'name': f'Item {n}', 'price': price}
                      for n, price in enumerate(prices)],
            'discounts': list(discounts), 'payments': list(payments), **extra}


def due(snapshot, *, clover=0, popcore=0, plan=None, verified=True):
    return pricing.amount_due(pricing.summarize(snapshot), clover_cents=clover, popcore_cents=popcore,
                              plan=plan, verified=verified)


class PricingTests(unittest.TestCase):
    def test_cash_price_matches_the_frontend_vectors(self):
        # Above $15: at least $1 off, or 2.5% when that is more, rounded down (owner, 2026-10-20).
        for total, expected in ((4134, 4000), (712, 700), (9914, 9600), (2000, 1900), (2259, 2100),
                                (6777, 6600), (4259, 4100), (1599, 1400), (1501, 1400), (1500, 1400),
                                (1499, 1400), (1, 1), (20, 19), (51, 50), (99, 97), (0, 0)):
            self.assertEqual(pricing.suggest_payable(total), expected, total)
        for value in (-1, 1.5, True, 2**53):
            with self.assertRaises(ValueError):
                pricing.suggest_payable(value)

    # The same vectors are in popcore_app/frontend/src/lib/checkoutPricing.test.ts: both sides must agree.
    def test_under_ten_dollars_the_cash_price_is_the_previous_whole_dollar(self):
        # Owner, 2026-10-27: $5.10 is $5, $9.99 is $9; a whole-dollar total keeps its price.
        for total, expected in ((510, 500), (999, 900), (712, 700), (101, 100), (199, 100), (950, 900),
                                (100, 100), (500, 500), (900, 900), (1000, 900), (1099, 1000)):
            self.assertEqual(pricing.suggest_payable(total), expected, total)
        self.assertEqual(due(order(442, total=500), popcore=1, plan='pay'), (500, 'exact'))
        self.assertEqual(due(order(452, total=510), popcore=1, plan='pay'), (500, 'cash_discount'))

    def test_cash_is_rounded_to_five_cents_the_canadian_way(self):
        for cents, expected in ((2160, 2160), (2161, 2160), (2162, 2160), (2163, 2165), (2164, 2165), (2165, 2165),
                                (2166, 2165), (2167, 2165), (2168, 2170), (2169, 2170), (747, 745), (0, 0)):
            self.assertEqual(pricing.nickel(cents), expected, cents)
        for rest, expected in ((747, 745), (2163, 2165), (19, 20), (97, 95), (3, 5), (2, 2), (1, 1), (0, 0)):
            self.assertEqual(pricing.cash_rest(rest), expected, rest)
        # The cash part of a card split: $20.37 by card on $42.00 leaves $21.63, collected as $21.65.
        self.assertEqual(pricing.cash_limit(4200, cash_cents=0, other_cents=2037), 2165)
        self.assertEqual(pricing.settled_due(4200, cash_cents=2165, other_cents=2037), 4202)
        # An agreed-discount order of $7.47 paid in cash: $7.45.
        self.assertEqual(pricing.settled_due(747, cash_cents=745, other_cents=0), 745)
        # Exact payments, or cash that does not pay the rest, leave the amount due as it was.
        self.assertEqual(pricing.settled_due(747, cash_cents=0, other_cents=747), 747)
        self.assertEqual(pricing.settled_due(4200, cash_cents=1000, other_cents=0), 4200)
        self.assertEqual(pricing.cash_limit(4100, cash_cents=1000, other_cents=0), 3100)
        collect = pricing.collect(pricing.summarize(order(700, total=747, discounts=[
            {'id': 'D00000000005', 'name': 'Agreed', 'amount': -44, 'percentage': None}])),
            clover_cents=0, popcore_cents=0, verified=True)
        self.assertEqual((collect['pay']['rest_cents'], collect['pay']['cash_rest_cents']), (747, 745))

    def test_cash_rounding_shows_in_the_sale_money(self):
        down = pricing.sale_money(pricing.summarize(order(700, total=791)), 745)
        self.assertEqual((down['gross_cents'], down['reduction_cents'], down['rounding_cents'], down['collected_cents']),
                         (791, 46, 0, 745))
        # Cash rounded up past an order with no discount: $7.48 collected as $7.50.
        up = pricing.sale_money(pricing.summarize(order(662, total=748)), 750)
        self.assertEqual((up['gross_cents'], up['reduction_cents'], up['rounding_cents'], up['collected_cents']),
                         (748, 0, 2, 750))
        self.assertEqual(up['gross_cents'] - up['reduction_cents'] + up['rounding_cents'], up['collected_cents'])

    def test_whole_dollar_matches_the_card_split_vectors(self):
        for total, expected in ((2831, 2800), (3191, 3100), (999, 999), (1000, 1000), (1099, 1000)):
            self.assertEqual(pricing.whole_dollar(total), expected, total)

    def test_amount_due_follows_the_owner_pricing_table(self):
        plain = order(1770, total=2000)
        self.assertEqual(due(plain, clover=2000), (2000, 'clover'))
        self.assertEqual(due(plain, popcore=1, plan='pay'), (1900, 'cash_discount'))
        self.assertEqual(due(plain, popcore=1, plan='split_card'), (2000, 'exact'))
        split = order(2565, total=2898)
        self.assertEqual(due(split, clover=1000, popcore=1, plan='split_card'), (2800, 'split_card'))
        self.assertEqual(due(split, popcore=1000, plan='split_card'), (2800, 'split_card'))
        covered = order(1814, total=2050)
        self.assertEqual(due(covered, clover=2000, popcore=50, plan='split_card'), (2050, 'exact'))
        rewardup = order(1770, total=1774, discounts=[
            {'id': 'D00000000001', 'name': 'RewardUp $2 off', 'amount': -200, 'percentage': None}])
        self.assertEqual(due(rewardup, popcore=1, plan='pay'), (1700, 'rounding'))
        self.assertEqual(due(rewardup, clover=1774), (1774, 'clover'))
        legacy = order(1770, total=1911, discounts=[
            {'id': 'D00000000002', 'name': 'CA$0.89 Off', 'amount': -89, 'percentage': None}])
        self.assertTrue(pricing.summarize(legacy)['cash_priced'])
        self.assertEqual(due(legacy, popcore=1, plan='pay'), (1911, 'exact'))
        self.assertEqual(due(plain, popcore=1, plan='pay', verified=False), (2000, 'exact'))
        weighted = order(1770, total=2000)
        weighted['items'][0]['unitQty'] = 1500
        self.assertEqual(due(weighted, popcore=1, plan='pay'), (2000, 'exact'))
        already = order(1770, total=1900, discounts=[
            {'id': 'D00000000003', 'name': 'Cash discount', 'amount': -89, 'percentage': None}])
        self.assertEqual(due(already, popcore=1, plan='pay'), (1900, 'exact'))
        with self.assertRaises(ValueError):
            due(order(1770, total=None), popcore=1, plan='pay')

    def test_price_options_show_each_tender_before_payment(self):
        options = pricing.price_options(pricing.summarize(order(4259, total=4813)), verified=True)
        self.assertEqual(options, {'card_cents': 4813, 'card_blocked': False,
                                   'cash_cents': 4600, 'cash_mode': 'cash_discount', 'cash_label': 'Cash discount',
                                   'split_cents': 4800, 'split_mode': 'split_card', 'split_label': 'Rounding'})

    def test_sale_money_keeps_the_sale_invariants(self):
        cases = (
            (order(1770, total=2000), 1900, (1770, 230, 2000, 100)),
            (order(1000, total=1130), 1100, (1000, 130, 1130, 30)),
            (order(1770, total=1774, discounts=[{'id': 'D00000000001', 'name': 'RewardUp',
                                                 'amount': -200, 'percentage': None}]),
             1700, (1770, 204, 1974, 274)),
            (order(2565, total=2898), 2800, (2565, 333, 2898, 98)),
            (order(1770, 1999, total=4259, discounts=[{'id': 'D00000000004', 'name': 'Member 10%',
                                                       'amount': None, 'percentage': 10}]),
             4200, (3769, 4259 - (3769 - 377), 3769 + 4259 - (3769 - 377), 3769 + 4259 - (3769 - 377) - 4200)),
        )
        for snapshot, collected, (subtotal, tax, gross, reduction) in cases:
            money = pricing.sale_money(pricing.summarize(snapshot), collected)
            self.assertEqual((money['subtotal_cents'], money['source_tax_cents'], money['gross_cents'],
                              money['reduction_cents'], money['rounding_cents'], money['collected_cents']),
                             (subtotal, tax, gross, reduction, 0, collected))
            self.assertEqual(money['gross_cents'], money['subtotal_cents'] + money['source_tax_cents'])
            self.assertEqual(money['collected_cents'], money['gross_cents'] - money['reduction_cents'])
        weighted = order(1770, total=2655)
        weighted['items'][0]['unitQty'] = 1500
        money = pricing.sale_money(pricing.summarize(weighted), 2655)
        self.assertEqual((money['subtotal_cents'], money['source_tax_cents'], money['gross_cents'],
                          money['reduction_cents']), (None, None, 2655, 0))
        with self.assertRaises(ValueError):  # only cash rounding (at most 2¢) may pass the order total
            pricing.sale_money(pricing.summarize(order(1770, total=2000)), 2003)

    def test_fingerprint_ignores_popcore_writes_but_not_other_changes(self):
        before = order(1770, total=2000, payments=[{'id': 'PAY000000001', 'amount': 500, 'result': 'SUCCESS'}])
        after = order(1770, total=1900, discounts=[{'id': 'DPOPCORE0001', 'name': 'Cash discount', 'amount': -89}],
                      payments=[*before['payments'], {'id': 'PAY000000002', 'amount': 1400, 'result': 'SUCCESS',
                                                      'externalPaymentId': 'popcore-pc-7-9'}])
        self.assertEqual(pricing.fingerprint(before), pricing.fingerprint(after))
        refunded = {**after, 'refunds': [{'id': 'REF000000001', 'amount': 500}]}
        self.assertNotEqual(pricing.fingerprint(after), pricing.fingerprint(refunded))
        added = order(1770, 999, total=3129)
        self.assertNotEqual(pricing.fingerprint(before, payments=False), pricing.fingerprint(added, payments=False))
        self.assertEqual(pricing.fingerprint(before, payments=False),
                         pricing.fingerprint({**before, 'payments': []}, payments=False))

    def test_payment_keys_and_tenders(self):
        key = pricing.payment_key('pc', 12, 34)
        self.assertEqual(key, 'popcore-pc-12-34')
        self.assertEqual(pricing.parse_payment_key(key), ('pc', 12, 34))
        for bad in (None, 'popcore-pc-x-1', 'other-pc-1-2', 'popcore-toolonginstance-1-2'):
            self.assertIsNone(pricing.parse_payment_key(bad))
        with self.assertRaises(ValueError):
            pricing.payment_key('bad tag', 1, 2)
        for label, key_, tender in (('Credit Card', 'com.clover.tender.credit_card', 'card'),
                                    ('Debit Card', None, 'card'), ('Cash', 'com.clover.tender.cash', 'cash'),
                                    ('E-transfer', None, 'e_transfer'), ('Interac e-Transfer', None, 'e_transfer'),
                                    ('WeChat', None, 'wechat'), ('WeChat Pay', None, 'wechat'),
                                    ('Alipay', None, 'alipay'), ('Gift card', None, None), (None, None, None)):
            self.assertEqual(pricing.tender_for(label, key_), tender, label)


if __name__ == '__main__':
    unittest.main()
