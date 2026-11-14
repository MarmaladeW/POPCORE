"""图鉴 Dex: the book lives in POPCORE (owner, 2026-11-12: "The Dex itself lives on POPCORE"). Step 1 is the
table and its screens, empty: which kind sits under which, names, partners and characters, links to POPCORE
items, the refined copy's keys, versions, reorder, delete, search, and who may write."""
import sqlite3
from contextlib import closing

from support import IsolatedApiCase

import db
from blueprints import dex


class DexBookTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(dex.bp)
        with closing(self.connect()) as con:
            for sku, form in (('PM0100', 'random_box'), ('PM0100-01', 'confirmed_design'),
                              ('PM0100-02', 'confirmed_design'), ('PM0100-SET', 'sealed_set'), ('PM0200', None)):
                con.execute("INSERT INTO products(sku, name_cn_en, jizhanming, stock_form) VALUES (?, ?, ?, ?)",
                            (sku, f'Echo {sku}', f'Echo {sku}', form))
            self.items = {row[0]: row[1] for row in con.execute('SELECT sku, id FROM products')}
            con.commit()

    def post(self, role='manager', **body):
        return self.client.post('/api/dex/entries', json=body, headers=self.headers(role))

    def add(self, kind, name, parent=None, **fields):
        response = self.post(kind=kind, name=name, parent_id=parent, **fields)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()['id']

    def patch(self, entry_id, role='manager', **body):
        return self.client.patch(f'/api/dex/entries/{entry_id}', json=body, headers=self.headers(role))

    def get(self, url, role='staff'):
        response = self.client.get(url, headers=self.headers(role))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def row(self, entry_id):
        with closing(self.connect()) as con:
            return con.execute('SELECT * FROM dex_entries WHERE id=?', (entry_id,)).fetchone()

    def book(self):
        """POP MART › Labubu › The Monsters › Echo box › two designs."""
        company = self.add('company', 'POP MART')
        ip = self.add('ip', 'Labubu', company)
        series = self.add('series', 'The Monsters', ip)
        product = self.add('product', 'Echo box', series, blind_box=True)
        designs = [self.add('design', name, product, rarity='normal') for name in ('Sun', 'Moon')]
        return company, ip, series, product, designs

    def test_migration_runs_twice_and_only_adds_the_table(self):
        with closing(self.connect()) as con:
            before = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}
            con.execute("DELETE FROM _migrations WHERE name IN ('dex_entries', 'dex_entries_website_item', 'dex_entries_ip')")
            con.commit()
        db.migrate_db()
        db.migrate_db()
        with closing(self.connect()) as con:
            after = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}
            self.assertEqual(before, after)
            self.assertIn('dex_entries', after)
            self.assertTrue({'idx_dex_entries_parent', 'idx_dex_entries_product', 'idx_dex_entries_website_item',
                             'idx_dex_entries_ip'} <= after)
            self.assertIn('ip_id', {row[1] for row in con.execute('PRAGMA table_info(dex_entries)')})
            self.assertEqual(con.execute("SELECT COUNT(*) FROM _migrations WHERE name='dex_entries'").fetchone()[0], 1)
            self.assertNotIn('idx_dex_entries_website', after)  # replaced by the one with website_item

    def test_allowed_parents(self):
        company = self.add('company', 'POP MART')
        partner = self.add('partner', 'Disney')
        ip = self.add('ip', 'Labubu', company)
        self.add('character', 'Zimomo', ip)
        self.add('character', 'Snow White', partner)  # contract 1.2: a partner's own characters
        series_under_ip = self.add('series', 'The Monsters', ip)
        series_under_company = self.add('series', 'Mega Space', company)
        self.add('product', 'Echo box', series_under_ip)
        self.add('product', 'IP plush', ip)
        product = self.add('product', 'Company pin', company)
        self.add('design', 'Sun', product)
        # Owner, 2026-11-14: goods that carry only an outside name sit straight under that partner.
        under_partner = self.add('series', 'Battle Moment Series', partner)
        self.add('product', 'Figures', under_partner)
        self.add('product', 'Pikachu Figure - Chef', partner)
        self.assertTrue(series_under_company and partner)
        refused = [('company', company), ('partner', company), ('ip', None), ('ip', ip), ('character', company),
                   ('series', None), ('series', product), ('product', product), ('product', None), ('design', partner),
                   ('design', series_under_ip), ('design', None), ('ip', partner)]
        for kind, parent in refused:
            response = self.post(kind=kind, name='X', parent_id=parent)
            self.assertEqual(response.status_code, 400, (kind, parent, response.get_json()))
            self.assertIn(response.get_json()['code'], ('parent_kind', 'parent_required'))
        self.assertEqual(self.post(kind='ip', name='X', parent_id=99999).status_code, 404)
        self.assertEqual(self.post(kind='shelf', name='X').status_code, 400)

    def test_names_are_required_and_unique_per_kind_and_parent(self):
        company = self.add('company', 'POP MART')
        other = self.add('company', 'Dreams')
        for name in ('', '   ', None):
            response = self.post(kind='ip', name=name, parent_id=company)
            self.assertEqual((response.status_code, response.get_json()['code']), (400, 'name_required'))
        ip = self.add('ip', '  Labubu  ', company)
        self.assertEqual(self.row(ip)['name'], 'Labubu')
        response = self.post(kind='ip', name='LABUBU', parent_id=company)
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'name_taken'))
        self.add('ip', 'Labubu', other)            # another parent
        self.add('series', 'Labubu', company)      # another kind
        self.add('company', 'Labubu')
        self.assertEqual(self.post(kind='company', name='pop mart').status_code, 409)
        # A rename to a sibling's name is refused too; renaming to itself in another case is fine.
        twin = self.add('ip', 'Crybaby', company)
        self.assertEqual(self.patch(twin, name='labubu').status_code, 409)
        self.assertEqual(self.patch(ip, name='LABUBU').status_code, 200)

    def test_partner_character_blind_box_and_rarity_fields(self):
        company, ip, series, product, (sun, moon) = self.book()
        partner = self.add('partner', 'Disney')
        character = self.add('character', 'Zimomo', ip)
        self.assertEqual(self.patch(series, partner_id=partner, character_id=character).status_code, 200)
        self.assertEqual(self.patch(product, partner_id=partner, character_id=character).status_code, 200)
        self.assertEqual(self.patch(sun, character_id=character).status_code, 200)
        for entry, body, code in (
                (sun, {'partner_id': partner}, 'field_not_allowed'),
                (ip, {'character_id': character}, 'field_not_allowed'),
                (series, {'partner_id': company}, 'partner_not_found'),
                (series, {'character_id': ip}, 'character_not_found'),
                (series, {'blind_box': True}, 'field_not_allowed'),
                (product, {'rarity': 'secret'}, 'field_not_allowed'),
                (sun, {'rarity': 'rare'}, 'invalid_input'),
                (product, {'blind_box': 'yes'}, 'invalid_input')):
            response = self.patch(entry, **body)
            self.assertEqual((response.status_code, response.get_json()['code']), (400, code), body)
        for rarity in ('normal', 'popular', 'secret', 'super_secret', 'special'):
            self.assertEqual(self.patch(moon, rarity=rarity).status_code, 200)
        self.assertEqual(self.patch(product, blind_box=False).get_json()['blind_box'], False)
        found = self.get(f'/api/dex/entries/{product}')
        self.assertEqual((found['partner']['name'], found['character']['name'], found['ip']['name']),
                         ('Disney', 'Zimomo', 'Labubu'))

    def test_linking_popcore_items(self):
        company, ip, series, product, (sun, moon) = self.book()
        items = self.items
        for entry, sku in ((product, 'PM0100-01'), (product, 'PM0100-SET'), (sun, 'PM0100'), (sun, 'PM0200'), (sun, 'PM0100-SET')):
            response = self.patch(entry, product_id=items[sku])
            self.assertEqual((response.status_code, response.get_json()['code']), (400, 'product_form'), sku)
        self.assertEqual(self.patch(series, product_id=items['PM0100']).get_json()['code'], 'field_not_allowed')
        self.assertEqual(self.patch(product, product_id=99999).status_code, 404)
        linked = self.patch(product, product_id=items['PM0100']).get_json()
        self.assertEqual(linked['product'], {'id': items['PM0100'], 'sku': 'PM0100', 'name': 'Echo PM0100'})
        self.assertEqual(self.patch(sun, product_id=items['PM0100-01']).status_code, 200)
        # One POPCORE item, one live entry.
        other = self.add('product', 'Echo again', series)
        response = self.patch(other, product_id=items['PM0100'])
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'product_taken'))
        self.assertEqual(self.patch(moon, product_id=items['PM0100-01']).status_code, 409)
        self.assertEqual(self.patch(other, product_id=items['PM0200']).status_code, 200)  # an ordinary item
        listed = self.get(f'/api/dex/entries?parent={series}')
        self.assertEqual([(row['name'], row['product_sku'], row['child_count']) for row in listed],
                         [('Echo box', 'PM0100', 2), ('Echo again', 'PM0200', 0)])
        self.assertEqual(self.patch(product, product_id=None).get_json()['product'], None)  # unlink
        with closing(self.connect()) as con:  # the database refuses a second link too
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute('UPDATE dex_entries SET product_id=? WHERE id=?', (items['PM0100-01'], moon))

    def test_a_book_link_never_blocks_removing_or_deleting_the_item(self):
        company, ip, series, product, (sun, moon) = self.book()
        other = self.add('product', 'Pin', series)
        self.assertEqual(self.patch(sun, product_id=self.items['PM0100-01']).status_code, 200)
        self.assertEqual(self.patch(other, product_id=self.items['PM0200']).status_code, 200)
        # The design is removed as if the book did not link it; the book design stays, unlinked.
        response = self.client.post(f"/api/products/{self.items['PM0100-01']}/design/remove", headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        # So is an ordinary item deleted from the catalogue.
        response = self.client.post('/api/products/bulk_delete', json=[self.items['PM0200']], headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        for entry_id in (sun, other):
            row = self.row(entry_id)
            self.assertIsNone(row['product_id'])
            self.assertIsNone(row['deleted_at'])
            self.assertEqual(row['version'], 2)  # the unlink by the database keeps the version
        self.assertIsNone(self.get(f'/api/dex/entries/{sun}')['product'])

    def test_extras_take_only_the_contract_keys_for_the_kind(self):
        company, ip, series, product, (sun, moon) = self.book()
        prices = [{'price': 120, 'recorded_at': '2026-11-01', 'source': 'collector'}]
        self.assertEqual(self.patch(sun, extras={'weight': 112, 'shake_note': 'rattles', 'market_prices': prices}).status_code, 200)
        self.assertEqual(self.patch(sun, extras={'weight': 113}).get_json()['extras'],
                         {'weight': 113, 'shake_note': 'rattles', 'market_prices': prices})  # keys sent replace, the rest stay
        self.assertEqual(self.patch(ip, extras={'is_original': True, 'aliases': ['拉布布']}).status_code, 200)
        for entry, extras in ((sun, {'retail_price': 20}), (company, {'description': 'x'}), (series, {'colour': 'red'})):
            response = self.patch(entry, extras=extras)
            self.assertEqual((response.status_code, response.get_json()['code']), (400, 'extras_unknown'), extras)
        self.assertEqual(self.patch(sun, extras=['weight']).status_code, 400)
        response = self.post(kind='product', name='Bad', parent_id=series, extras={'weight': 1})
        self.assertEqual(response.get_json()['code'], 'extras_unknown')
        self.assertEqual(self.post(kind='company', name='Odd', website_id=5).status_code, 400)  # not settable here

    def test_every_change_raises_the_version(self):
        company, ip, series, product, designs = self.book()
        row = self.row(series)
        self.assertEqual((row['version'], row['sent_version'], row['extras']), (1, 0, '{}'))
        self.assertEqual(row['updated_by'], 'auth0|manager')
        self.assertEqual(self.patch(series, name='The Monsters II').get_json()['version'], 2)
        self.assertEqual(self.patch(series, name='The Monsters II').get_json()['version'], 2)  # nothing changed
        self.assertEqual(self.patch(series, extras={'designer': 'Kasing Lung'}, role='admin').get_json()['version'], 3)
        self.assertEqual(self.row(series)['updated_by'], 'auth0|admin')

    def test_moving_checks_the_new_parent_and_the_name(self):
        company, ip, series, product, designs = self.book()
        other_ip = self.add('ip', 'Crybaby', company)
        self.add('series', 'Echo', other_ip)
        moved = self.patch(series, parent_id=other_ip).get_json()
        self.assertEqual([step['name'] for step in moved['path']], ['POP MART', 'Crybaby'])
        self.assertEqual(self.patch(series, parent_id=company).status_code, 200)  # a series may sit under a company
        self.assertEqual(self.patch(product, parent_id=ip).status_code, 200)
        self.assertEqual(self.patch(product, parent_id=designs[0]).get_json()['code'], 'parent_kind')
        self.assertEqual(self.patch(ip, parent_id=None).get_json()['code'], 'parent_required')
        clash = self.add('series', 'Clash', ip)
        self.assertEqual(self.patch(clash, parent_id=other_ip, name='echo').status_code, 409)
        self.assertEqual(self.patch(company, parent_id=ip).get_json()['code'], 'parent_kind')

    def test_staff_and_viewers_read_but_cannot_write(self):
        company, ip, series, product, designs = self.book()
        for role in ('viewer', 'staff'):
            self.assertEqual([row['name'] for row in self.get('/api/dex/entries?kind=company', role)], ['POP MART'])
            self.assertEqual(self.get(f'/api/dex/entries/{ip}', role)['allowed_children'], ['character', 'series', 'product'])
            self.assertEqual(self.post(role, kind='company', name='Dreams').status_code, 403)
            self.assertEqual(self.patch(ip, role, name='X').status_code, 403)
            self.assertEqual(self.client.delete(f'/api/dex/entries/{product}', headers=self.headers(role)).status_code, 403)
            self.assertEqual(self.client.post('/api/dex/reorder', json={'parent_id': product, 'ids': designs},
                                              headers=self.headers(role)).status_code, 403)
        self.assertEqual(self.client.get('/api/dex/entries?kind=company').status_code, 401)
        self.assertEqual(self.row(ip)['name'], 'Labubu')

    def test_reorder_renumbers_one_group(self):
        company, ip, series, product, (sun, moon) = self.book()
        star = self.add('design', 'Star', product)
        self.assertEqual([row['name'] for row in self.get(f'/api/dex/entries?parent={product}')], ['Sun', 'Moon', 'Star'])
        response = self.client.post('/api/dex/reorder', json={'parent_id': product, 'ids': [sun, star, moon]},
                                    headers=self.headers('manager'))
        self.assertEqual([row['name'] for row in response.get_json()], ['Sun', 'Star', 'Moon'])
        self.assertEqual([self.row(i)['sort_order'] for i in (sun, star, moon)], [10, 20, 30])
        self.assertEqual([self.row(i)['version'] for i in (sun, star, moon)], [1, 2, 2])  # Sun kept its number
        for ids in ([star, sun], [star, sun, moon, product], [star, star, moon]):
            response = self.client.post('/api/dex/reorder', json={'parent_id': product, 'ids': ids}, headers=self.headers('manager'))
            self.assertIn(response.status_code, (400, 409), ids)
        dreams = self.add('company', 'Dreams')
        response = self.client.post('/api/dex/reorder', json={'parent_id': None, 'ids': [dreams, company]},
                                    headers=self.headers('manager'))
        self.assertEqual([row['name'] for row in response.get_json()], ['Dreams', 'POP MART'])

    def test_deleting_a_partner_removes_what_sits_under_it_and_keeps_the_refusals(self):
        partner = self.add('partner', 'Pokémon')
        series = self.add('series', 'Battle Moment Series', partner)
        line = self.add('product', 'Figures', series)
        item = self.add('product', 'Pikachu Figure - Chef', partner)
        self.patch(line, product_id=self.items['PM0100'])
        response = self.client.delete(f'/api/dex/entries/{partner}', headers=self.headers('manager'))
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'dex_linked'))
        self.patch(line, product_id=None)
        company = self.add('company', 'POP MART')
        other = self.add('series', 'Pokémon Collab Series', company, partner_id=partner)
        response = self.client.delete(f'/api/dex/entries/{partner}', headers=self.headers('manager'))
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'dex_in_use'))
        self.patch(other, partner_id=None)
        self.assertEqual(self.get(f'/api/dex/entries/{partner}/removal')['counts'], {'partner': 1, 'series': 1, 'product': 2})
        response = self.client.delete(f'/api/dex/entries/{partner}', headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(self.connect()) as con:
            left = {row[0] for row in con.execute('SELECT id FROM dex_entries')}
        self.assertFalse({partner, series, line, item} & left)

    def test_delete_rules(self):
        company, ip, series, product, (sun, moon) = self.book()
        partner = self.add('partner', 'Disney')
        character = self.add('character', 'Zimomo', ip)
        self.patch(product, partner_id=partner, product_id=self.items['PM0100'])

        def delete(entry_id):
            return self.client.delete(f'/api/dex/entries/{entry_id}', headers=self.headers('manager'))
        # Linked below: refused, nothing changes.
        response = delete(series)
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'dex_linked'))
        self.assertEqual(self.get(f'/api/dex/entries/{series}/removal')['counts'], {'series': 1, 'product': 1, 'design': 2})
        # Named as a partner or a character: refused.
        self.assertEqual(delete(partner).get_json()['code'], 'dex_in_use')
        self.patch(sun, character_id=character)
        self.assertEqual(delete(character).get_json()['code'], 'dex_in_use')
        self.patch(product, product_id=None, partner_id=None)
        self.patch(sun, character_id=None)
        self.assertEqual(delete(partner).status_code, 200)
        # Rows the website has are kept with deleted_at; the others go.
        with closing(self.connect()) as con:
            con.execute('UPDATE dex_entries SET website_id=537 WHERE id=?', (series,))
            con.execute('UPDATE dex_entries SET website_id=9001 WHERE id=?', (sun,))
            con.commit()
        response = delete(series)
        self.assertEqual(response.get_json(), {'removed': 2, 'kept_for_website': 2,
                                               'counts': {'series': 1, 'product': 1, 'design': 2}})
        self.assertIsNone(self.row(product))
        self.assertIsNone(self.row(moon))
        kept = self.row(series)
        self.assertIsNotNone(kept['deleted_at'])
        self.assertEqual(kept['version'], 2)
        self.assertIsNotNone(self.row(sun)['deleted_at'])
        # Deleted rows never appear in a read.
        self.assertEqual(self.client.get(f'/api/dex/entries/{series}', headers=self.headers()).status_code, 404)
        self.assertEqual([row['name'] for row in self.get(f'/api/dex/entries?parent={ip}')], ['Zimomo'])
        self.assertEqual(self.get('/api/dex/entries?q=Sun'), [])
        self.assertEqual(self.get(f'/api/dex/entries/{ip}')['child_count'], 1)
        self.assertEqual(delete(series).status_code, 404)
        # A name used by a deleted row is free again.
        self.add('series', 'The Monsters', ip)

    def test_search_by_name_with_the_path(self):
        company, ip, series, product, designs = self.book()
        self.add('series', 'Monsters Mini', company)
        found = self.get('/api/dex/entries?q=monsters')
        self.assertEqual([(row['name'], [step['name'] for step in row['path']]) for row in found],
                         [('The Monsters', ['POP MART', 'Labubu']), ('Monsters Mini', ['POP MART'])])  # shortest first
        self.assertEqual([row['name'] for row in self.get('/api/dex/entries?q=o&kinds=ip,company')], ['POP MART'])
        self.assertEqual(self.get('/api/dex/entries?q=%25'), [])  # a % is a letter, not a wildcard
        self.assertEqual(self.get('/api/dex/entries?q=+'), [])
        for index in range(60):
            self.add('design', f'Design {index}', product)
        self.assertEqual(len(self.get('/api/dex/entries?q=design')), 50)

    def test_reads_list_the_top_and_children_with_details(self):
        self.assertEqual(self.get('/api/dex/entries?kind=company'), [])  # an empty book
        self.assertEqual(self.get('/api/dex/entries?kind=partner'), [])
        company, ip, series, product, (sun, moon) = self.book()
        self.add('partner', 'Disney')
        self.assertEqual([row['name'] for row in self.get('/api/dex/entries?kind=partner')], ['Disney'])
        entry = self.get(f'/api/dex/entries/{sun}')
        self.assertEqual([step['kind'] for step in entry['path']], ['company', 'ip', 'series', 'product'])
        self.assertEqual((entry['allowed_children'], entry['rarity'], entry['extras']), ([], 'normal', {}))
        self.assertEqual(self.get(f'/api/dex/entries/{product}')['blind_box'], True)
        self.assertEqual(self.client.get('/api/dex/entries?kind=series', headers=self.headers()).status_code, 400)
        self.assertEqual(self.client.get('/api/dex/entries?parent=x', headers=self.headers()).status_code, 400)
        self.assertEqual(self.client.get('/api/dex/entries?parent=99999', headers=self.headers()).status_code, 404)


    # Contract 1.3 (owner, 2026-11-13): a series with several IPs stays under its company; each design names its IP.

    def mixed(self):
        """POP MART › 15th Anniversary (no IP above) › Anniversary box › Sun, Moon; and the Labubu tree."""
        company, ip, series, product, designs = self.book()
        crybaby = self.add('ip', 'Crybaby', company)
        anniversary = self.add('series', '15th Anniversary', company)
        box = self.add('product', 'Anniversary box', anniversary)
        sun, moon = (self.add('design', name, box) for name in ('Sun', 'Moon'))
        return {'company': company, 'labubu': ip, 'crybaby': crybaby, 'monsters': series, 'echo': product,
                'echo_designs': designs, 'anniversary': anniversary, 'box': box, 'sun': sun, 'moon': moon}

    def code(self, response):
        return response.status_code, response.get_json().get('code')

    def test_migration_keeps_older_code_working(self):
        # Older code writes rows without ip_id and reads them with its own columns.
        with closing(self.connect()) as con:
            con.execute("INSERT INTO dex_entries(kind, name) VALUES ('company', 'Old code')")
            con.commit()
            row = con.execute("SELECT id, kind, name, ip_id FROM dex_entries WHERE name='Old code'").fetchone()
            self.assertIsNone(row['ip_id'])
        self.assertEqual(self.get(f"/api/dex/entries/{row['id']}")['named_ip'], None)

    def test_a_design_with_no_ip_above_names_its_ip(self):
        book = self.mixed()
        named = self.patch(book['sun'], ip_id=book['labubu']).get_json()
        self.assertEqual((named['ip_id'], named['named_ip'], named['version']),
                         (book['labubu'], {'id': book['labubu'], 'name': 'Labubu'}, 2))
        self.assertEqual(self.patch(book['sun'], ip_id=book['labubu']).get_json()['version'], 2)  # nothing changed
        self.assertEqual(self.patch(book['sun'], ip_id=book['crybaby']).get_json()['version'], 3)
        self.assertEqual(self.patch(book['box'], ip_id=book['crybaby']).status_code, 200)  # a product too
        star = self.add('design', 'Star', book['box'], ip_id=book['labubu'])  # set when made
        self.assertEqual(self.row(star)['ip_id'], book['labubu'])
        # A product straight under the company names one too.
        pin = self.add('product', 'Company pin', book['company'], ip_id=book['labubu'])
        self.assertEqual(self.row(pin)['ip_id'], book['labubu'])
        cleared = self.patch(book['sun'], ip_id=None).get_json()
        self.assertEqual((cleared['ip_id'], cleared['named_ip'], cleared['version']), (None, None, 4))

    def test_ip_id_rules(self):
        book = self.mixed()
        partner = self.add('partner', 'Disney')
        character = self.add('character', 'Zimomo', book['labubu'])
        for entry, value, expected in (
                (book['anniversary'], book['labubu'], (400, 'field_not_allowed')),   # not on a series
                (book['crybaby'], book['labubu'], (400, 'field_not_allowed')),       # nor an IP
                (character, book['labubu'], (400, 'field_not_allowed')),
                (book['sun'], book['company'], (400, 'ip_not_found')),             # must be an IP
                (book['sun'], book['anniversary'], (400, 'ip_not_found')),
                (book['sun'], partner, (400, 'ip_not_found')),
                (book['sun'], 99999, (400, 'ip_not_found')),
                (book['sun'], True, (400, 'invalid_input')),
                (book['sun'], 'Labubu', (400, 'invalid_input')),
                # An IP sits above: its IP is the one above, even when it is the same IP.
                (book['echo_designs'][0], book['crybaby'], (400, 'ip_above')),
                (book['echo_designs'][0], book['labubu'], (400, 'ip_above')),
                (book['echo'], book['crybaby'], (400, 'ip_above'))):
            self.assertEqual(self.code(self.patch(entry, ip_id=value)), expected, (entry, value))
        plush = self.add('product', 'IP plush', book['labubu'])  # a product straight under an IP
        self.assertEqual(self.code(self.patch(plush, ip_id=book['crybaby'])), (400, 'ip_above'))
        self.assertEqual(self.code(self.post(kind='design', name='Star', parent_id=book['echo'], ip_id=book['crybaby'])),
                         (400, 'ip_above'))
        # A deleted IP cannot be named.
        dropped = self.add('ip', 'Dropped', book['company'])
        with closing(self.connect()) as con:
            con.execute("UPDATE dex_entries SET deleted_at=datetime('now') WHERE id=?", (dropped,))
            con.commit()
        self.assertEqual(self.code(self.patch(book['sun'], ip_id=dropped)), (400, 'ip_not_found'))
        self.assertIsNone(self.row(book['sun'])['ip_id'])

    def test_moving_the_entry_checks_its_ip_but_not_what_is_under_a_moved_parent(self):
        book = self.mixed()
        self.patch(book['sun'], ip_id=book['crybaby'])
        # Sun itself moves under an IP's product: refused while it names an IP, fine when cleared in the same change.
        self.assertEqual(self.code(self.patch(book['sun'], parent_id=book['echo'], name='Sun 15')), (400, 'ip_above'))
        self.assertEqual(self.patch(book['sun'], parent_id=book['echo'], name='Sun 15', ip_id=None).status_code, 200)
        # Moving a parent does not re-check what sits under it (docs/dex-book.md).
        self.patch(book['moon'], ip_id=book['crybaby'])
        self.assertEqual(self.patch(book['anniversary'], parent_id=book['labubu']).status_code, 200)
        self.assertEqual(self.row(book['moon'])['ip_id'], book['crybaby'])
        # Moon's next own change is checked again.
        self.assertEqual(self.code(self.patch(book['moon'], name='Moon II')), (400, 'ip_above'))
        self.assertEqual(self.patch(book['moon'], name='Moon II', ip_id=None).status_code, 200)

    def test_reads_carry_the_named_ip(self):
        book = self.mixed()
        self.patch(book['sun'], ip_id=book['labubu'])
        self.patch(book['moon'], ip_id=book['crybaby'])
        self.patch(book['box'], ip_id=book['labubu'])
        rows = self.get(f"/api/dex/entries?parent={book['box']}")
        self.assertEqual([(row['name'], row['named_ip']) for row in rows],
                         [('Sun', {'id': book['labubu'], 'name': 'Labubu'}), ('Moon', {'id': book['crybaby'], 'name': 'Crybaby'})])
        self.assertEqual(self.get(f"/api/dex/entries/{book['sun']}")['named_ip']['name'], 'Labubu')
        self.assertIsNone(self.get(f"/api/dex/entries/{book['echo']}")['named_ip'])
        # An IP's page lists what names it elsewhere, products first, each with its path.
        labubu = self.get(f"/api/dex/entries/{book['labubu']}")
        self.assertEqual([(row['kind'], row['name'], [step['name'] for step in row['path']]) for row in labubu['named_by']],
                         [('product', 'Anniversary box', ['POP MART', '15th Anniversary']),
                          ('design', 'Sun', ['POP MART', '15th Anniversary', 'Anniversary box'])])
        self.assertEqual([row['name'] for row in self.get(f"/api/dex/entries?named_ip={book['crybaby']}")], ['Moon'])
        self.assertEqual(self.get(f"/api/dex/entries?named_ip={book['labubu']}"), labubu['named_by'])
        self.assertEqual(self.get(f"/api/dex/entries/{book['sun']}")['named_by'], [])
        self.assertEqual(self.code(self.client.get(f"/api/dex/entries?named_ip={book['company']}", headers=self.headers())),
                         (400, 'invalid_input'))
        self.assertEqual(self.client.get('/api/dex/entries?named_ip=99999', headers=self.headers()).status_code, 404)
        self.assertEqual(self.client.get('/api/dex/entries?named_ip=x', headers=self.headers()).status_code, 400)
        # Every live IP, for the picker, with its company; any signed-in role reads it.
        for role in ('viewer', 'staff'):
            listed = self.get('/api/dex/entries?kind=ip', role)
            self.assertEqual([(row['name'], [step['name'] for step in row['path']]) for row in listed],
                             [('Crybaby', ['POP MART']), ('Labubu', ['POP MART'])])
        # A deleted design no longer shows on the IP's page.
        self.assertEqual(self.client.delete(f"/api/dex/entries/{book['moon']}", headers=self.headers('manager')).status_code, 200)
        self.assertEqual(self.get(f"/api/dex/entries?named_ip={book['crybaby']}"), [])

    def test_an_ip_named_by_a_live_entry_cannot_be_deleted(self):
        book = self.mixed()
        self.patch(book['sun'], ip_id=book['crybaby'])

        def delete(entry_id):
            return self.client.delete(f'/api/dex/entries/{entry_id}', headers=self.headers('manager'))
        self.assertEqual(self.code(delete(book['crybaby'])), (409, 'dex_in_use'))
        self.assertEqual(self.get(f"/api/dex/entries/{book['crybaby']}/removal")['used_by'],
                         [{'id': book['sun'], 'kind': 'design', 'name': 'Sun'}])
        self.assertIsNotNone(self.row(book['crybaby']))
        self.patch(book['sun'], ip_id=None)
        self.assertEqual(delete(book['crybaby']).status_code, 200)
        # Deleting what names an IP frees it; deleting a company takes both together.
        self.patch(book['moon'], ip_id=book['labubu'])
        self.assertEqual(self.code(delete(book['labubu'])), (409, 'dex_in_use'))
        self.assertEqual(delete(book['company']).status_code, 200)
        self.assertIsNone(self.row(book['moon']))

    # The full name (naming standard rule 7, owner 2026-11-14): put together, never typed; and the IP tag.

    def raw(self, kind, name, parent=None, **fields):
        """An entry written straight to the table, for shapes the editor's parent rule may not offer yet."""
        columns = ['kind', 'name', 'parent_id', *fields]
        with closing(self.connect()) as con:
            cur = con.execute(f'INSERT INTO dex_entries({", ".join(columns)}) VALUES ({", ".join("?" * len(columns))})',
                              (kind, name, parent, *fields.values()))
            con.commit()
            return cur.lastrowid

    def named_book(self):
        company = self.add('company', 'POP MART')
        hirono = self.add('ip', 'HIRONO', company)
        monsters = self.add('ip', 'THE MONSTERS', company)
        echo = self.add('series', 'Echo Series', hirono)
        figures = self.add('product', 'Figures', echo, blind_box=True)
        sun = self.add('design', 'Sun', figures)
        prince = self.add('series', 'HIRONO × Le Petit Prince Series', hirono)
        prince_figures = self.add('product', 'Figures', prince)
        salon = self.add('series', 'the monsters hair salon series', monsters)  # the IP in another case
        pendant = self.add('product', 'Vinyl Plush Pendant', salon)
        pianist = self.add('product', 'HIRONO The Pianist Figure', hirono)  # a single item
        anniversary = self.add('series', 'Celebrating the Moment POP MART 15th Anniversary Series', company)
        mixed = self.add('product', 'Figures', anniversary)
        spotlight = self.add('design', 'Spotlight', mixed, ip_id=monsters)
        loong = self.add('design', 'Loong', mixed)
        pin = self.add('product', 'Company pin', company)
        pokemon = self.add('partner', 'Pokémon')
        pikachu = self.raw('series', 'Pikachu Series', pokemon)  # goods that carry only the partner's name
        pika_figures = self.raw('product', 'Figures', pikachu)
        pika_design = self.raw('design', 'Sleepy', pika_figures)
        plush = self.raw('product', 'Pikachu Plush', pokemon)
        return locals()

    def test_full_name_is_put_together_from_the_place_in_the_book(self):
        book = self.named_book()
        expected = {
            'figures': 'HIRONO Echo Series Figures',
            'sun': 'HIRONO Echo Series Figures - Sun',
            'echo': 'HIRONO Echo Series',
            'prince': 'HIRONO × Le Petit Prince Series',            # the series already starts with its IP
            'prince_figures': 'HIRONO × Le Petit Prince Series Figures',
            'salon': 'the monsters hair salon series',               # in any case
            'pendant': 'the monsters hair salon series Vinyl Plush Pendant',
            'pianist': 'HIRONO The Pianist Figure',                  # a single item: its own name, as it is
            'anniversary': 'Celebrating the Moment POP MART 15th Anniversary Series',  # no IP above
            'mixed': 'Celebrating the Moment POP MART 15th Anniversary Series Figures',
            'spotlight': 'Celebrating the Moment POP MART 15th Anniversary Series Figures - Spotlight',
            'pin': 'Company pin',
            'pikachu': 'Pokémon Pikachu Series',                    # a series straight under a partner
            'pika_figures': 'Pokémon Pikachu Series Figures',
            'pika_design': 'Pokémon Pikachu Series Figures - Sleepy',
            'plush': 'Pikachu Plush',
            'hirono': 'HIRONO', 'company': 'POP MART', 'pokemon': 'Pokémon',
        }
        for key, name in expected.items():
            self.assertEqual(self.get(f'/api/dex/entries/{book[key]}')['full_name'], name, key)
        # Children carry it too; the stored name stays the type word alone.
        rows = self.get(f"/api/dex/entries?parent={book['echo']}")
        self.assertEqual([(row['name'], row['full_name']) for row in rows], [('Figures', 'HIRONO Echo Series Figures')])
        self.assertEqual(self.row(book['figures'])['name'], 'Figures')
        # A rename above reaches every full name under it, with nothing typed twice.
        self.assertEqual(self.patch(book['echo'], name='Echo Series 2').status_code, 200)
        self.assertEqual(self.get(f"/api/dex/entries/{book['sun']}")['full_name'], 'HIRONO Echo Series 2 Figures - Sun')
        self.assertEqual(self.patch(book['hirono'], name='HIRONO!').status_code, 200)
        self.assertEqual(self.get(f"/api/dex/entries/{book['sun']}")['full_name'], 'HIRONO! Echo Series 2 Figures - Sun')

    def test_the_ip_prefix_is_left_out_only_for_whole_words(self):
        from dex_book import full_name
        ip = [{'kind': 'company', 'name': 'POP MART'}, {'kind': 'ip', 'name': 'DIMOO'}]
        self.assertEqual(full_name('series', 'DIMOO World × Disney Series', ip), 'DIMOO World × Disney Series')
        self.assertEqual(full_name('series', 'DIMOOS Series', ip), 'DIMOO DIMOOS Series')
        self.assertEqual(full_name('series', 'DIMOO', ip), 'DIMOO')
        self.assertEqual(full_name('product', 'Figures', ip + [{'kind': 'series', 'name': 'Dimoo-Dating Series'}]),
                         'Dimoo-Dating Series Figures')
        self.assertEqual(full_name('design', 'Sun', []), 'Sun')  # nothing above it (never in a live book)

    def test_every_read_carries_the_ip_tag(self):
        book = self.named_book()
        tag = lambda key: self.get(f'/api/dex/entries/{book[key]}')['ip']
        hirono = {'id': book['hirono'], 'kind': 'ip', 'name': 'HIRONO'}
        monsters = {'id': book['monsters'], 'kind': 'ip', 'name': 'THE MONSTERS'}
        pokemon = {'id': book['pokemon'], 'kind': 'partner', 'name': 'Pokémon'}
        for key in ('echo', 'figures', 'sun', 'pianist'):
            self.assertEqual(tag(key), hirono, key)                          # the IP above
        self.assertEqual(tag('spotlight'), monsters)                        # the IP it names
        self.assertEqual(self.patch(book['mixed'], ip_id=book['hirono']).status_code, 200)
        self.assertEqual(tag('loong'), hirono)                              # else the IP its product names
        self.assertEqual(tag('spotlight'), monsters)                        # its own comes first
        self.assertEqual(tag('mixed'), hirono)
        for key in ('pikachu', 'pika_figures', 'pika_design', 'plush'):
            self.assertEqual(tag(key), pokemon, key)                        # else the partner it sits under
        for key in ('anniversary', 'pin', 'company', 'hirono', 'pokemon'):
            self.assertIsNone(tag(key), key)
        character = self.add('character', 'Hirono', book['hirono'])
        self.assertEqual(tag('hirono'), None)
        self.assertEqual(self.get(f'/api/dex/entries/{character}')['ip'], hirono)
        # The editor's IP (above, or itself) is still there as ip_above.
        self.assertEqual(self.get(f"/api/dex/entries/{book['hirono']}")['ip_above']['name'], 'HIRONO')
        self.assertIsNone(self.get(f"/api/dex/entries/{book['spotlight']}")['ip_above'])
        # Lists, the top, the IP picker, search and an IP's page carry it too.
        self.assertEqual([row['ip'] for row in self.get(f"/api/dex/entries?parent={book['mixed']}")], [monsters, hirono])
        self.assertEqual({row['ip'] for row in self.get('/api/dex/entries?kind=company')}, {None})
        self.assertEqual({row['ip'] for row in self.get('/api/dex/entries?kind=ip')}, {None})
        self.assertEqual([(row['full_name'], row['ip']) for row in self.get('/api/dex/entries?q=sleepy')],
                         [('Pokémon Pikachu Series Figures - Sleepy', pokemon)])
        self.assertEqual([row['full_name'] for row in self.get(f"/api/dex/entries?named_ip={book['monsters']}")],
                         ['Celebrating the Moment POP MART 15th Anniversary Series Figures - Spotlight'])

    def test_search_finds_an_entry_by_the_words_of_its_full_name(self):
        book = self.named_book()
        found = self.get('/api/dex/entries?q=hirono+echo+figures')
        self.assertEqual([(row['id'], row['full_name']) for row in found], [(book['figures'], 'HIRONO Echo Series Figures')])
        self.assertEqual([row['id'] for row in self.get('/api/dex/entries?q=HIRONO%20sun')], [book['sun']])
        self.assertEqual([row['id'] for row in self.get('/api/dex/entries?q=echo+figures+hirono')], [book['figures']])
        # Every word must be in the full name, and one in the entry's own name.
        self.assertEqual(self.get('/api/dex/entries?q=hirono+echo+moon'), [])
        self.assertEqual([row['id'] for row in self.get('/api/dex/entries?q=pikachu+figures')], [book['pika_figures']])
        self.assertEqual([row['id'] for row in self.get('/api/dex/entries?q=hirono+figures&kinds=product')],
                         [book['figures'], book['prince_figures']])  # "Figure" is not "figures"
        # A name holding the whole text comes first.
        self.assertEqual(self.get('/api/dex/entries?q=echo+series')[0]['id'], book['echo'])

    def test_unlinked_items_carry_the_full_names_of_their_book_entries(self):
        book = self.named_book()
        self.assertEqual(self.patch(book['figures'], product_id=self.items['PM0100']).status_code, 200)
        with closing(self.connect()) as con:  # PM0100-01 is a design of the PM0100 box
            family = con.execute("INSERT INTO product_series(name, product_id) VALUES ('Echo box', ?)",
                                 (self.items['PM0100'],)).lastrowid
            con.execute('UPDATE products SET series_id=? WHERE id=?', (family, self.items['PM0100-01']))
            con.commit()
        listed = {item['sku']: item for item in self.get('/api/dex/unlinked')['items']}
        design = listed['PM0100-01']
        self.assertEqual(design['box_entry'], {'id': book['figures'], 'name': 'Figures'})
        self.assertEqual((design['box_entry_named']['full_name'], design['box_entry_named']['ip']['name']),
                         ('HIRONO Echo Series Figures', 'HIRONO'))
        for item in listed.values():
            if item['suggestion']:
                self.assertEqual(item['suggested_entry']['id'], item['suggestion']['id'])
            else:
                self.assertIsNone(item['suggested_entry'])
        self.assertIsNone(listed['PM0200']['box_entry_named'])

    # A design's price trend (owner: "we should store these data, so it becomes a price trend over time").

    def test_price_history_reads_the_prices_set_for_the_designs_item(self):
        book = self.named_book()
        item = self.items['PM0100-01']
        prices = [{'price': 140, 'recorded_at': '2026-11-02', 'source': 'collector'}, {'price': 'x'}, 'bad']
        self.assertEqual(self.patch(book['sun'], extras={'market_prices': prices}).status_code, 200)
        unlinked = self.get(f"/api/dex/entries/{book['sun']}/prices", 'viewer')
        self.assertEqual(unlinked, {'linked': False, 'current_cents': None, 'points': [],
                                    'website': [{'price': 140, 'recorded_at': '2026-11-02', 'source': 'collector'}]})
        self.assertEqual(self.patch(book['sun'], product_id=item).status_code, 200)
        self.assertEqual(self.get(f"/api/dex/entries/{book['sun']}/prices")['points'], [])  # nothing set yet
        with closing(self.connect()) as con:
            con.execute("INSERT INTO employees(name, auth0_id) VALUES ('Mia', 'auth0|manager')")
            for cents, previous, at, by in ((2500, None, '2026-11-10 15:00:00', 'auth0|manager'),
                                            (2800, 2500, '2026-11-12 03:30:00', 'auth0|someone'),
                                            (3200, 2800, '2026-11-13 18:00:00', 'auth0|manager')):
                con.execute("""INSERT INTO design_prices(product_id, cents, previous_cents, source, set_by, set_at)
                               VALUES (?, ?, ?, 'design_prices', ?, ?)""", (item, cents, previous, by, at))
            con.execute('UPDATE products SET price=32 WHERE id=?', (item,))
            con.commit()
            before = con.execute('SELECT COUNT(*), MAX(id) FROM design_prices').fetchone()[:]
        for role in ('viewer', 'staff', 'manager'):
            trend = self.get(f"/api/dex/entries/{book['sun']}/prices", role)
            self.assertEqual((trend['linked'], trend['current_cents']), (True, 3200))
            self.assertEqual([(p['cents'], p['set_at'], p['set_by'], p['set_by_name']) for p in trend['points']],
                             [(3200, '2026-11-13 18:00:00', 'auth0|manager', 'Mia'),
                              (2800, '2026-11-12 03:30:00', 'auth0|someone', None),
                              (2500, '2026-11-10 15:00:00', 'auth0|manager', 'Mia')])
            self.assertEqual(len(trend['website']), 1)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*), MAX(id) FROM design_prices').fetchone()[:], before)
        self.assertEqual(self.code(self.client.get(f"/api/dex/entries/{book['figures']}/prices", headers=self.headers())),
                         (400, 'invalid_input'))
        self.assertEqual(self.client.get('/api/dex/entries/99999/prices', headers=self.headers()).status_code, 404)
        self.assertEqual(self.client.get(f"/api/dex/entries/{book['sun']}/prices").status_code, 401)


if __name__ == '__main__':
    import unittest
    unittest.main()
