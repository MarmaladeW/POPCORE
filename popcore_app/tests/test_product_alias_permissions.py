"""Product and section aliases are shared matching rules: staff save them while entering reports, viewers cannot."""
from contextlib import closing

from support import IsolatedApiCase


class MatchingAliasPermissionTests(IsolatedApiCase):
    def save(self, role, path, body):
        return self.client.post(path, json=body, headers=self.headers(role))

    def test_viewers_cannot_save_product_or_section_aliases(self):
        for path, body in (('/api/products/aliases', {'product_id': self.product_id, 'alias': 'test alias'}),
                           ('/api/section-aliases', {'alias': '卡机', 'section_type': 'pos'})):
            with self.subTest(path=path):
                self.assertEqual(self.save('viewer', path, body).status_code, 403)
        self.assertEqual(self.snapshot(('product_aliases', 'section_aliases')),
                         {'product_aliases': [], 'section_aliases': []})

    def test_staff_save_product_and_section_aliases(self):
        self.assertEqual(self.save('staff', '/api/products/aliases',
                                   {'product_id': self.product_id, 'alias': 'test alias'}).status_code, 200)
        self.assertEqual(self.save('staff', '/api/section-aliases',
                                   {'alias': '卡机', 'section_type': 'pos'}).status_code, 200)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT product_id FROM product_aliases').fetchone()[0], self.product_id)
            self.assertEqual(tuple(con.execute('SELECT alias_norm, section_type FROM section_aliases').fetchone()),
                             ('卡机', 'pos'))

    def test_a_learned_heading_keeps_its_section(self):
        self.assertEqual(self.save('staff', '/api/section-aliases',
                                   {'alias': 'Claw 机', 'section_type': 'claw'}).status_code, 200)
        before = self.snapshot(('section_aliases',))
        again = self.save('staff', '/api/section-aliases', {'alias': 'claw机', 'section_type': 'claw'})
        self.assertEqual(again.status_code, 200)
        changed = self.save('manager', '/api/section-aliases', {'alias': 'Claw 机', 'section_type': 'pos'})
        self.assertEqual((changed.status_code, changed.get_json()['code']), (409, 'section_choice_conflict'))
        self.assertEqual(self.snapshot(('section_aliases',)), before)


if __name__ == '__main__':
    import unittest
    unittest.main()
