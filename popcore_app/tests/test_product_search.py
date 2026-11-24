"""A misspelt word finds the product in every product search, and a search that already found
products finds exactly the same ones in the same order (popcore-work#32)."""
import unittest
from contextlib import closing

from support import IsolatedApiCase

import product_search
from product_search import fold, load_vocabulary, respellings, token_alternatives, typo_words


class TypoRuleTests(unittest.TestCase):
    def test_fold_drops_case_accents_and_apostrophes(self):
        self.assertEqual(fold('Ｐｏｋémon’s'), 'pokemons')
        self.assertEqual(fold("Sonny's `Angel` Ø"), 'sonnys angel o')
        self.assertEqual(fold('骷髅熊猫 がぎ'), '骷髅熊猫 がぎ')
        self.assertEqual(fold(None), '')

    def test_three_letters_or_fewer_are_never_replaced(self):
        self.assertEqual(typo_words('sny', {'sony': 3, 'sonny': 9}), [])
        self.assertEqual(typo_words('ab', {'abcd': 1}), [])

    def test_four_letters_take_a_missing_letter_or_a_swap_never_a_wrong_letter(self):
        self.assertEqual(typo_words('park', {'pack': 9}), [])
        self.assertEqual(typo_words('miku', {'mika': 2}), [])
        self.assertEqual(typo_words('park', {'pack': 9, 'parks': 1}), ['parks'])
        self.assertEqual(typo_words('sony', {'sonny': 3}), ['sonny'])
        self.assertEqual(typo_words('mlik', {'milk': 1}), ['milk'])
        self.assertEqual(typo_words('sjul', {'skullpanda': 4}), [])  # no word start under 5 letters

    def test_five_to_seven_letters_take_one_slip(self):
        for typed in ('hirno', 'hirnoo', 'hironoo', 'hixono'):
            self.assertEqual(typo_words(typed, {'hirono': 4}), ['hirono'], typed)
        self.assertEqual(typo_words('hixxno', {'hirono': 4}), [])

    def test_eight_letters_and_more_take_two_slips_but_not_two_dropped_letters(self):
        self.assertEqual(typo_words('skulpnada', {'skullpanda': 4}), ['skullpanda'])
        self.assertEqual(typo_words('skxlpxnda', {'skullpanda': 4}), [])
        self.assertEqual(typo_words('creature', {'create': 3}), [])
        self.assertEqual(typo_words('creatures', {'creature': 3}), ['creature'])

    def test_the_start_of_a_longer_word_counts_while_still_typing(self):
        self.assertEqual(typo_words('sjull', {'skullpanda': 4}), ['skullpanda'])
        self.assertEqual(typo_words('akull', {'skullpanda': 4}), [])  # not the same first letter
        self.assertEqual(typo_words('sjxll', {'skullpanda': 4}), [])  # two slips

    def test_closest_first_and_only_the_ties_with_the_best(self):
        # A whole word before a word start; fewer slips beat everything.
        self.assertEqual(typo_words('sjull', {'skullpanda': 4, 'skull': 1}), ['skull', 'skullpanda'])
        self.assertEqual(typo_words('chocolste', {'chocolate': 1, 'chocolates': 9}), ['chocolate'])
        # The same first letter beats another one; then the word more products carry; then a to z.
        self.assertEqual(typo_words('tames', {'games': 9, 'tamer': 1}), ['tamer'])
        self.assertEqual(typo_words('moly', {'molly': 2, 'moldy': 5}), ['moldy', 'molly'])
        self.assertEqual(typo_words('moly', {'molly': 5, 'moldy': 5}), ['moldy', 'molly'])
        self.assertEqual(typo_words('tabes', {'tabesa': 1, 'tabesb': 1, 'tabesc': 1, 'tabesd': 1}),
                         ['tabesa', 'tabesb', 'tabesc'])

    def test_only_plain_words_are_replaced_or_offered(self):
        self.assertEqual(typo_words('骷髅熊猫猫', {'骷髅熊猫': 3, 'skullpanda': 3}), [])
        self.assertEqual(typo_words('sk-ull', {'skull': 3}), [])
        # A code is exact or wrong: a mistyped POPCORE ID never offers its neighbours.
        self.assertEqual(typo_words('cl00537', {'cl00536': 1, 'cl00538': 1}), [])
        self.assertEqual(typo_words('mega40', {'mega400': 9}), [])
        self.assertEqual(typo_words('skul', {'sku': 3, 'skül': 2}), [])


class SearchFixture(IsolatedApiCase):
    """A small catalogue in stock at DT, a live store, so all four searches can be asked."""
    PRODUCTS = (
        ('SP-WARM', '骷髅熊猫温度', 'Skullpanda Warmth Series Figures'),
        ('SP-SOUND', '骷髅熊猫声音', 'Skullpanda The Sound Series Plush'),
        ('LB-MAC', '拉布布马卡龙', 'Labubu Macaron Plush Pendant'),
        ('LB-SIT', '拉布布坐坐', 'Labubu Have a Seat Plush'),
        ('PK-SLV', '卡套', 'Pack of Card Sleeves'),
        ('HR-SHEL', '小野避难所', 'Hirono Shelter Figure'),
        ('SM-BATH', 'Smiski 浴室', 'Smiski Bath Series'),
        ('JJ-KAI', '咒术回战', 'Jujutsu Kaisen Jelly Figure'),
    )

    def setUp(self):
        super().setUp()
        self.grant_inventory_view('auth0|staff', 'auth0|manager')
        with closing(self.connect()) as con:
            upstairs = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='upstairs'",
                                   (self.store_id,)).fetchone()[0]
            for subject in ('auth0|staff', 'auth0|manager'):
                con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)', (subject, self.store_id))
            con.execute("UPDATE inventory_mode SET mode='authoritative'")
            con.execute('UPDATE inventory_scope_state SET opening_verified=1 WHERE store_id=?', (self.store_id,))
            self.ids = {}
            for sku, shop_name, name in self.PRODUCTS:
                self.ids[sku] = con.execute(
                    """INSERT INTO products(sku, jizhanming, name_cn_en, search_blob, stock_form, stock_unit,
                                            identity_status)
                       VALUES (?, ?, ?, ?, 'ordinary', 'piece', 'verified')""",
                    (sku, shop_name, name, f'{sku} {shop_name} {name}'.lower())).lastrowid
                con.execute('INSERT INTO stock(product_id, store_id, upstairs_qty, instore_qty) VALUES (?, ?, 4, 1)',
                            (self.ids[sku], self.store_id))
                con.execute("INSERT INTO inventory_balances VALUES (?, ?, 'saleable', 4, 1)", (self.ids[sku], upstairs))
            con.execute("""INSERT INTO product_barcodes(code, product_id, code_kind, input_unit, quantity_per_scan)
                           VALUES ('6941234567890', ?, 'manufacturer', 'piece', 1)""", (self.ids['JJ-KAI'],))
            con.commit()
        self.skus = {product_id: sku for sku, product_id in self.ids.items()}
        self.skus[self.product_id] = 'TEST-1'

    def get(self, path, role='staff', **params):
        response = self.client.get(path, query_string=params, headers=self.headers(role))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response

    def products(self, q, **params):
        return [row['sku'] for row in self.get('/api/products/search', q=q, **params).get_json()]

    def stock(self, q):
        return [row['sku'] for row in self.get('/api/stock', store_code='DT', q=q).get_json()]

    def stock_export(self, q):
        lines = self.get('/api/stock/export', role='manager', store_code='DT', q=q).get_data(as_text=True).splitlines()
        return [line.lstrip('﻿').split(',')[0] for line in lines[1:]]

    def losses(self, q):
        return [row['sku'] for row in
                self.get('/api/losses/search', role='manager', store_code='DT', q=q).get_json()['results']]

    def counting(self, q):
        return [self.skus[row['product_id']] for row in
                self.get('/api/counting/search', store_code='DT', location='upstairs', q=q).get_json()['results']]


class MisspeltWordTests(SearchFixture):
    def test_known_words_are_kept_and_unknown_ones_replaced(self):
        with closing(self.connect()) as con:
            self.assertEqual(token_alternatives(con, ['skullpanda', 'sjull', 'plsh', 'SKULPANDA']),
                             [['skullpanda'], ['skullpanda'], ['plush'], ['skullpanda']])
            # A part of a word is known; 3 letters, Chinese, "park" and a word nothing is close to stay as typed.
            for typed in ('skul', 'smk', '骷髅熊毛', 'park', 'zzzzqqq', 'sp-wram', '100%'):
                self.assertEqual(token_alternatives(con, [typed]), [[typed]], typed)
            self.assertEqual(token_alternatives(con, []), [])
            self.assertEqual(respellings(con, 'labbubu plsh'), ['labubu plush'])
            self.assertEqual(respellings(con, 'labubu plush'), [])
            self.assertEqual(respellings(con, '拉布布 park'), [])

    def test_vocabulary_holds_plain_words_of_four_letters_and_how_many_products_carry_them(self):
        with closing(self.connect()) as con:
            vocabulary = load_vocabulary(con)
        self.assertEqual((vocabulary['skullpanda'], vocabulary['plush'], vocabulary['series']), (2, 3, 3))
        self.assertIn('smiski', vocabulary)   # a Latin word beside Chinese ones
        self.assertNotIn('the', vocabulary)
        self.assertNotIn('sp', vocabulary)

    def test_a_word_added_since_the_word_list_was_read_is_still_known(self):
        with closing(self.connect()) as con:
            self.assertEqual(token_alternatives(con, ['labubi']), [['labubu']])
            con.execute("INSERT INTO products(sku, jizhanming, name_cn_en) VALUES ('NEW-1', 'Labubi', 'Labubi Special')")
            con.commit()
            self.assertEqual(token_alternatives(con, ['labubi']), [['labubi']])
        self.assertEqual(self.products('labubi')[0], 'NEW-1')

    def test_the_word_list_is_read_once_and_kept(self):
        with closing(self.connect()) as con:
            first = load_vocabulary(con)
            self.assertIs(load_vocabulary(con), first)
            product_search._vocabulary.clear()
            self.assertIsNot(load_vocabulary(con), first)

    def test_products_search_finds_the_misspelt_product_first(self):
        self.assertEqual(sorted(self.products('sjull')[:2]), ['SP-SOUND', 'SP-WARM'])
        self.assertEqual(sorted(self.products('skulpanda', limit=2)), ['SP-SOUND', 'SP-WARM'])
        self.assertEqual(sorted(self.products('labbubu plsh')[:2]), ['LB-MAC', 'LB-SIT'])
        self.assertEqual(self.products('hirno shleter')[0], 'HR-SHEL')
        self.assertEqual(self.products('skullpanda wramth')[0], 'SP-WARM')
        self.assertEqual(self.products('ＳＫＵＬＬＰＡＮＤＡ sound')[0], 'SP-SOUND')

    def test_products_search_puts_an_exact_sku_or_barcode_first(self):
        self.assertEqual(self.products('6941234567890'), ['JJ-KAI'])
        self.assertEqual(self.products('jj-kai')[0], 'JJ-KAI')
        with closing(self.connect()) as con:
            # A product named after another one's SKU outscored that product before.
            con.execute("""INSERT INTO products(sku, jizhanming, name_cn_en, search_blob)
                           VALUES ('ZZ-9', 'pk-slv pk-slv', 'pk-slv copy', 'pk-slv')""")
            con.commit()
        self.assertEqual(self.products('PK-SLV')[:2], ['PK-SLV', 'ZZ-9'])
        self.assertEqual(self.products('pk-slv', product_type='盲盒'), [])  # filters still apply

    def test_stock_list_and_export_find_the_misspelt_product(self):
        for search in (self.stock, self.stock_export):
            self.assertEqual(search('sjull'), ['SP-WARM', 'SP-SOUND'])
            self.assertEqual(search('labbubu plsh'), ['LB-SIT', 'LB-MAC'])
            self.assertEqual(search('skullpanda wramth'), ['SP-WARM'])
            self.assertEqual(search('park'), [])
            self.assertEqual(search('zzzzqqq'), [])

    def test_losses_search_finds_the_misspelt_product(self):
        self.assertEqual(self.losses('sjull'), ['SP-SOUND', 'SP-WARM'])
        self.assertEqual(self.losses('Labbubu Macaron'), ['LB-MAC'])
        self.assertEqual(self.losses('labubu plsh'), [])  # the words are not side by side in any name, as before
        self.assertEqual(self.losses('park'), [])
        self.assertEqual(self.losses('骷髅熊毛'), [])

    def test_counting_search_finds_the_misspelt_product(self):
        self.assertEqual(self.counting('sjull'), ['SP-WARM', 'SP-SOUND'])
        self.assertEqual(self.counting('Labbubu Macaron'), ['LB-MAC'])
        self.assertEqual(self.counting('park'), [])
        self.assertEqual(self.counting('骷髅熊毛'), [])


class UnchangedResultsTests(SearchFixture):
    """What each search answered before popcore-work#32, for texts whose words products carry (or
    that nothing is close to). These lists were produced by the code before the change."""

    def test_products_search_answers_as_before(self):
        for q, expected in PRODUCTS_BEFORE.items():
            self.assertEqual(self.products(q), expected, q)
        self.assertEqual(self.products('plush', limit=2), PRODUCTS_BEFORE['plush'][:2])

    def test_stock_list_and_export_answer_as_before(self):
        for q, expected in STOCK_BEFORE.items():
            self.assertEqual(self.stock(q), expected, q)
            self.assertEqual(self.stock_export(q), expected, q)

    def test_losses_search_answers_as_before(self):
        for q, expected in LOSSES_BEFORE.items():
            self.assertEqual(self.losses(q), expected, q)

    def test_counting_search_answers_as_before(self):
        for q, expected in COUNTING_BEFORE.items():
            self.assertEqual(self.counting(q), expected, q)


PRODUCTS_BEFORE = {
    'skullpanda': ['SP-WARM', 'SP-SOUND', 'LB-MAC', 'SM-BATH', 'PK-SLV', 'JJ-KAI'],
    'labubu plush': ['LB-MAC', 'LB-SIT', 'SP-SOUND', 'TEST-1', 'HR-SHEL', 'JJ-KAI', 'PK-SLV'],
    'plush': ['SP-SOUND', 'LB-MAC', 'LB-SIT', 'HR-SHEL', 'SP-WARM'],
    'series': ['SM-BATH', 'SP-WARM', 'SP-SOUND', 'TEST-1', 'HR-SHEL', 'JJ-KAI', 'PK-SLV', 'LB-SIT'],
    '骷髅熊猫': ['SP-WARM', 'SP-SOUND'],
    '拉布布 马卡龙': ['LB-MAC', 'LB-SIT'],
    'skul': ['SP-WARM', 'SP-SOUND', 'SM-BATH', 'JJ-KAI'],
    'labubu skullpanda': ['SP-WARM', 'SP-SOUND', 'LB-MAC', 'LB-SIT', 'SM-BATH', 'JJ-KAI', 'PK-SLV', 'HR-SHEL'],
    'smiski bath': ['SM-BATH', 'SP-WARM', 'SP-SOUND', 'JJ-KAI', 'LB-SIT'],
    'card sleeves pack': ['PK-SLV', 'TEST-1', 'SP-SOUND', 'SP-WARM', 'LB-MAC', 'LB-SIT', 'SM-BATH', 'HR-SHEL'],
    'test': ['TEST-1', 'SM-BATH', 'SP-WARM', 'SP-SOUND', 'HR-SHEL', 'LB-MAC', 'LB-SIT', 'JJ-KAI', 'PK-SLV'],
    '布布': ['LB-MAC', 'LB-SIT'],
    'sp-': ['SP-WARM', 'SP-SOUND', 'TEST-1', 'LB-MAC', 'LB-SIT', 'PK-SLV'],
    'park': ['SP-WARM', 'PK-SLV', 'SP-SOUND', 'LB-MAC'],
    'zzzzqqq': [],
    '100%': [],
    'sm-bath': ['SM-BATH', 'SP-WARM', 'LB-SIT', 'LB-MAC', 'SP-SOUND'],
}
STOCK_BEFORE = {
    'skullpanda': ['SP-WARM', 'SP-SOUND'],
    'labubu plush': ['LB-SIT', 'LB-MAC'],
    'plush': ['SP-SOUND', 'LB-SIT', 'LB-MAC'],
    'series': ['SP-WARM', 'SP-SOUND', 'SM-BATH'],
    '骷髅熊猫': ['SP-WARM', 'SP-SOUND'],
    '拉布布 马卡龙': ['LB-MAC'],
    'skul': ['SP-WARM', 'SP-SOUND'],
    'labubu skullpanda': [],
    'smiski bath': ['SM-BATH'],
    'sp-': ['SP-WARM', 'SP-SOUND'],
    'park': [],
    'zzzzqqq': [],
    'SM-BATH': ['SM-BATH'],
}
LOSSES_BEFORE = {
    'skullpanda': ['SP-SOUND', 'SP-WARM'],
    'labubu': ['LB-SIT', 'LB-MAC'],
    'plush': ['LB-SIT', 'LB-MAC', 'SP-SOUND'],
    'series': ['SM-BATH', 'SP-SOUND', 'SP-WARM'],
    '骷髅熊猫': ['SP-SOUND', 'SP-WARM'],
    'skul': ['SP-SOUND', 'SP-WARM'],
    'Labubu Macaron': ['LB-MAC'],
    'labubu plush': [],
    'SP-WARM': ['SP-WARM'],
    '6941234567890': ['JJ-KAI'],
    'sp-': ['SP-SOUND', 'SP-WARM'],
    'park': [],
    'zzzzqqq': [],
}
COUNTING_BEFORE = {
    'skullpanda': ['SP-WARM', 'SP-SOUND'],
    'labubu': ['LB-MAC', 'LB-SIT'],
    'plush': ['SP-SOUND', 'LB-MAC', 'LB-SIT'],
    'series': ['SP-WARM', 'SP-SOUND', 'SM-BATH'],
    '骷髅熊猫': ['SP-WARM', 'SP-SOUND'],
    'skul': ['SP-WARM', 'SP-SOUND'],
    'Labubu Macaron': ['LB-MAC'],
    'labubu plush': [],
    'SP-WARM': ['SP-WARM'],
    '6941234567890': ['JJ-KAI'],
    'sp-': ['SP-WARM', 'SP-SOUND'],
    'park': [],
    'zzzzqqq': [],
}


if __name__ == '__main__':
    unittest.main()
