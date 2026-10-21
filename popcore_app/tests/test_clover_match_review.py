"""Clover match decisions stay editable until applied, and applying merges safely one product at a time."""
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from support import IsolatedApiCase  # noqa: E402
import import_clover_items as importer  # noqa: E402
import clover_match_review as review  # noqa: E402
from blueprints import clover_matches  # noqa: E402

URL = '/api/catalog/clover-matches'


class CloverMatchReviewTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(clover_matches.bp)
        with closing(self.connect()) as con:
            importer.import_items(con, [
                {'clover_item_name': name, 'price': price, 'category': 'POPMART', 'clover_item_id': None, 'product_code': None}
                for name, price in (('Test Product Series Figures', 19.99), ('Other Series Figures', 26.99))])
            ids = dict(con.execute('SELECT clover_item_name, id FROM products WHERE clover_item_name IS NOT NULL'))
            self.clover_id, self.other_id = ids['Test Product Series Figures'], ids['Other Series Figures']
            con.execute("INSERT INTO products(sku, jizhanming, name_cn_en) VALUES ('OLD-2', '没用', 'Unused thing')")
            con.execute("INSERT INTO products(sku, jizhanming, name_cn_en) VALUES ('OLD-3', '留着', 'Keep me')")
            self.unused_id, self.keep_id = (r[0] for r in con.execute("SELECT id FROM products WHERE sku IN ('OLD-2','OLD-3') ORDER BY sku"))
            self.outcomes = review.load_rows(con, [
                {'legacy_id': self.product_id, 'jizhanming': 'Test Product', 'legacy_name': 'Test Product',
                 'decision': 'Test Product Series Figures', 'filled_by': 'Claude', 'confidence': 'high', 'why': 'same name'},
                {'legacy_id': self.unused_id, 'jizhanming': '没用', 'legacy_name': 'Unused thing', 'decision': 'delete'},
                {'legacy_id': self.keep_id, 'jizhanming': '留着', 'legacy_name': 'Keep me', 'decision': 'Not On Clover'},
                {'legacy_id': 999, 'jizhanming': 'x', 'legacy_name': 'x', 'decision': 'keep'},
            ], 'sheet:test')
            con.commit()

    def items(self, role='manager'):
        response = self.client.get(URL, headers=self.headers(role))
        self.assertEqual(response.status_code, 200, response.get_json())
        return {i['legacy_product_id']: i for i in response.get_json()['items']}

    def test_sheet_rows_load_only_when_the_legacy_product_still_matches(self):
        self.assertEqual(dict(self.outcomes), {'loaded': 2, 'problem': 1, 'legacy product not found': 1})
        items = self.items()
        self.assertEqual((items[self.product_id]['decision'], items[self.product_id]['target_name'], items[self.product_id]['source'],
                          items[self.product_id]['status'], items[self.product_id]['references']),
                         ('merge', 'Test Product Series Figures', 'claude', 'ready', 1))
        self.assertEqual((items[self.keep_id]['decision'], items[self.keep_id]['status']), (None, 'undecided'))
        self.assertIn('Not On Clover', items[self.keep_id]['note'])
        with closing(self.connect()) as con:
            renamed = review.load_rows(con, [{'legacy_id': self.unused_id, 'jizhanming': '别的', 'legacy_name': 'Unused thing', 'decision': 'keep'}], 'sheet:test')
        self.assertEqual(dict(renamed), {'legacy name differs': 1})

    def test_staff_cannot_see_or_change_the_review(self):
        self.assertEqual(self.client.get(URL, headers=self.headers('staff')).status_code, 403)
        self.assertEqual(self.client.put(f'{URL}/{self.product_id}', json={'decision': 'keep'}, headers=self.headers('staff')).status_code, 403)

    def test_edits_are_kept_when_the_sheet_is_loaded_again(self):
        response = self.client.put(f'{URL}/{self.product_id}', json={'decision': 'merge', 'target_product_id': self.other_id, 'note': 'checked box'},
                                   headers=self.headers('manager:owner'))
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual((response.get_json()['target_name'], response.get_json()['source'], response.get_json()['updated_by']),
                         ('Other Series Figures', 'edited', 'auth0|owner'))
        bad = self.client.put(f'{URL}/{self.product_id}', json={'decision': 'merge', 'target_product_id': self.keep_id}, headers=self.headers('manager'))
        self.assertEqual(bad.status_code, 409)
        with closing(self.connect()) as con:
            outcome = review.load_rows(con, [{'legacy_id': self.product_id, 'jizhanming': 'Test Product', 'legacy_name': 'Test Product',
                                              'decision': 'Test Product Series Figures'}], 'sheet:test')
            con.commit()
        self.assertEqual(dict(outcome), {'kept app edit': 1})
        self.assertEqual(self.items()[self.product_id]['target_product_id'], self.other_id)

    def test_apply_merges_stock_records_the_outcome_and_locks_the_row(self):
        response = self.client.post(f'{URL}/apply', json={'legacy_product_ids': [self.product_id, self.unused_id, self.keep_id]},
                                    headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        results = {r['legacy_product_id']: r for r in response.get_json()['results']}
        self.assertEqual(results[self.product_id]['status'], 'applied')
        self.assertIn('merged into Test Product Series Figures', results[self.product_id]['message'])
        self.assertEqual((results[self.unused_id]['status'], results[self.unused_id]['message']), ('applied', 'deleted'))
        self.assertEqual((results[self.keep_id]['status'], results[self.keep_id]['message']), ('refused', 'nothing to apply'))
        with closing(self.connect()) as con:
            self.assertIsNone(con.execute('SELECT 1 FROM products WHERE id=?', (self.product_id,)).fetchone())
            self.assertEqual(con.execute('SELECT instore_qty, upstairs_qty FROM stock WHERE product_id=?', (self.clover_id,)).fetchone()[:], (2, 10))
            self.assertIsNotNone(con.execute("SELECT 1 FROM product_aliases WHERE product_id=? AND alias='Test Product'", (self.clover_id,)).fetchone())
        items = self.items()
        self.assertEqual((items[self.product_id]['status'], items[self.product_id]['legacy_name'], items[self.product_id]['references']),
                         ('applied', 'Test Product', None))
        again = self.client.post(f'{URL}/apply', json={'legacy_product_ids': [self.product_id]}, headers=self.headers('manager'))
        self.assertEqual(again.get_json()['results'][0]['message'], 'already applied')
        self.assertEqual(self.client.put(f'{URL}/{self.product_id}', json={'decision': 'keep'}, headers=self.headers('manager')).status_code, 409)

    def test_refused_rows_change_nothing(self):
        self.client.put(f'{URL}/{self.product_id}', json={'decision': 'delete'}, headers=self.headers('manager'))
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET name_cn_en='Renamed' WHERE id=?", (self.unused_id,))
            con.commit()
        before = self.snapshot(['products', 'stock', 'clover_match_reviews'])
        response = self.client.post(f'{URL}/apply', json={'legacy_product_ids': [self.product_id, self.unused_id]}, headers=self.headers('manager'))
        messages = {r['legacy_product_id']: r['message'] for r in response.get_json()['results']}
        self.assertIn('still has sales or stock records', messages[self.product_id])
        self.assertIn('changed since it was reviewed', messages[self.unused_id])
        self.assertEqual(self.snapshot(['products', 'stock', 'clover_match_reviews']), before)
        self.assertEqual(self.client.post(f'{URL}/apply', json={'legacy_product_ids': []}, headers=self.headers('manager')).status_code, 400)
