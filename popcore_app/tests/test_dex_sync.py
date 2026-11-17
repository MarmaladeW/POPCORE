"""图鉴 Dex step 3: POPCORE sends the book to popcore.ca (owner, 2026-11-14: "yes build the POPCORE -> popcore.ca
sync"), behind its own switch, against a pretend website that keeps the contract's book and pictures routes
(docs/dex-book-contract.md, 1.5): find by ref, then website_id; apply only a higher version; parent_unknown; deletes;
quiet. Designs carry the prices recorded in POPCORE (owner, 2026-11-14: "the price history chart on popcore.ca should
show the price change recorded in POPCORE")."""
import io
import json
import os
import sys
import tempfile
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import requests

from support import APP_DIR, IsolatedApiCase

ROOT = APP_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dex_book  # noqa: E402
import dex_sync  # noqa: E402
from blueprints import dex  # noqa: E402
from scripts import fetch_dex_book  # noqa: E402

ENV = {'WEBSITE_URL': 'https://shop.test', 'WEBSITE_KEY': 'ck_test', 'WEBSITE_SECRET': 'cs_test'}
ACTOR = {'sub': 'auth0|manager'}


def _png():
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (4, 4), (200, 30, 30)).save(out, 'PNG')
    return out.getvalue()


PNG = _png()


class Response:
    def __init__(self, status, data, headers=None):
        self.status_code, self._data, self.headers = status, data, headers or {}

    def json(self):
        if self._data is None:
            raise ValueError('no JSON')
        return self._data


class FakeBook:
    """popcore.ca's half of the contract, as section 3, 5 and 6 say. Rows are kept by (kind, item, website_id)."""

    def __init__(self):
        self.rows = {}  # (kind, item, website_id) -> row
        self.next_id = 9000
        self.calls = []  # (method, path, body)
        self.batches = []  # (quiet, [ref, ...])
        self.missing = False  # the website's half is not there: every route 404
        self.down = False  # unreachable
        self.status = None  # answer this HTTP status instead
        self.refuse = {}  # ref -> code
        self.live_listing = set()  # website ids a published listing points at
        self.during = None  # called once while a batch is being answered
        self.pictures = []
        # Listings (contract section 7): the shop's products, each {type, kind, ids: {role: ID}, unusual}.
        self.shop = {}
        self.next_product = 8100
        self.listing_status = None  # answer the listings route with this HTTP status (400, 503)
        self.listing_bad = set()  # IDs answered item_malformed
        self.listing_fail = set()  # IDs answered error listing_failed
        self.listing_silent = set()  # IDs left out of the answer
        self.listing_calls = []  # each call's items

    def listing(self, ids, type_='simple', kind='ordinary', unusual=False):
        """A listing the shop already has: ids {role: ID}, role single / set (a blind box's options) or own."""
        self.next_product += 1
        self.shop[self.next_product] = {'type': type_, 'kind': kind, 'ids': dict(ids), 'unusual': unusual}
        return self.next_product

    def by_ref(self, ref):
        return next((row for row in self.rows.values() if row.get('ref') == ref), None)

    def preload(self, kind, website_id, name, item=False, version=None):
        """A row the website already has (the load gave POPCORE its number); it carries no ref yet."""
        self.rows[(kind, item, website_id)] = {'kind': kind, 'item': item, 'website_id': website_id, 'name': name,
                                               'ref': None, 'version': version}

    def request(self, method, url, params=None, json=None, files=None, data=None, auth=None, **kwargs):
        if self.down:
            raise requests.ConnectionError('down')
        path = urlsplit(url).path.removeprefix('/wp-json/popcore/v1')
        self.calls.append((method, path, json))
        if self.missing:
            return Response(404, {'code': 'rest_no_route', 'message': 'No route was found matching the URL and request method.'})
        if auth != ('ck_test', 'cs_test'):
            return Response(401, {'code': 'rest_forbidden', 'message': 'Sorry.'})
        if self.status:
            return Response(self.status, {'message': 'Busy'})
        if method == 'GET' and path == '/book':
            rows = sorted(self.rows.values(), key=lambda row: (row['kind'], row['website_id']))
            size, page = int(params.get('per_page', 100)), int(params.get('page', 1))
            pages = max(1, -(-len(rows) // size))
            return Response(200, [dict(row) for row in rows[(page - 1) * size:page * size]], {'X-WP-TotalPages': str(pages)})
        if method == 'POST' and path == '/pictures':
            self.pictures.append((data['ref'], files['file']))
            picture_id = 7000 + len(self.pictures)
            return Response(200, {'picture_id': picture_id, 'url': f'https://shop.test/p/{picture_id}.png',
                                  'thumb_url': f'https://shop.test/p/{picture_id}-150.png'})
        if method == 'POST' and path == '/book':
            if self.during:
                during, self.during = self.during, None
                during()
            self.batches.append((json['quiet'], [entry['ref'] for entry in json['entries']]))
            return Response(200, {'results': [self.apply(entry) for entry in json['entries']]})
        if method == 'POST' and path == '/listings':
            if self.listing_status == 400:
                return Response(400, {'code': 'listings_malformed', 'message': 'The body must be a JSON object with an "items" list. Nothing was made.'})
            if self.listing_status == 503:
                return Response(503, {'code': 'dex_missing', 'message': 'The Dex plugin (popcore-dex) is not active, so listings cannot be made.'})
            if not isinstance(json, dict) or not isinstance(json.get('items'), list) or len(json['items']) > 50:
                return Response(400, {'code': 'listings_malformed', 'message': 'A call holds at most 50 items.'})
            self.listing_calls.append(json['items'])
            results = [self.make(item) for item in json['items']]
            return Response(200, {'results': [one for one in results if one['popcore_id'] not in self.listing_silent]})
        return Response(404, {'message': 'No route'})

    # Listings, as the catalog's PCAT_Listings makes them (contract section 7, package 3) -------------------------

    def holders(self, popcore_id):
        return [(number, role) for number, product in self.shop.items() for role, held in product['ids'].items()
                if held.upper() == popcore_id.upper()]

    def make(self, item):
        popcore_id = str(item.get('popcore_id') or '').upper()
        answer = lambda status, product_id=None, code=None, message=None: {
            'popcore_id': popcore_id or None, 'status': status, 'product_id': product_id,
            **({'code': code, 'message': message} if code else {})}
        shape_ok = (popcore_id and item.get('kind') in ('blind_box', 'ordinary', 'confirmed_design')
                    and isinstance(item.get('name'), str) and item['name'].strip()
                    and isinstance(item.get('book_ref'), int) and item['book_ref'] > 0
                    and all(item.get(key) is None or (isinstance(item[key], int) and item[key] >= 0)
                            for key in ('price_cents', 'set_price_cents', 'boxes_per_set'))
                    and (item['kind'] != 'blind_box' or item.get('set_id') in (None, f"{item['popcore_id']}-SET")))
        if not shape_ok or popcore_id in self.listing_bad:
            return answer('refused', code='item_malformed', message='"name" must be the starting name.')
        ids = {'single': popcore_id, **({'set': f'{popcore_id}-SET'} if item.get('set_id') else {})} \
            if item['kind'] == 'blind_box' else {'own': popcore_id}
        listings, held = set(), set()
        for role, one in ids.items():
            found = self.holders(one)
            if not found:
                continue
            if len(found) > 1:
                return answer('refused', code='id_in_use', message=f'{one} is on more than one item already.')
            number, held_role = found[0]
            product = self.shop[number]
            mine = held_role == role and (product['kind'] == item['kind'] or role != 'own')
            if not mine:
                return answer('refused', number, 'id_in_use', f'{one} is already on #{number}, which is not this item.')
            listings.add(number)
            held.add(role)
        if len(listings) > 1:
            return answer('refused', code='id_in_use', message='Its Single Box and Whole Set IDs are on two different listings.')
        if listings:
            number = listings.pop()
            missing = {role: one for role, one in ids.items() if role not in held}
            if not missing:
                return answer('exists', number)
            if self.shop[number]['type'] != 'variable':
                return answer('refused', number, 'listing_not_variable', f'#{number} is a simple listing.')
            if self.shop[number]['unusual']:
                return answer('refused', number, 'options_unusual', 'Its options are not Single Box / Whole Set.')
            self.shop[number]['ids'].update(missing)
            return answer('completed', number)
        row = self.by_ref(item['book_ref'])
        if row is None or (row['kind'] == 'design') != (item['kind'] == 'confirmed_design'):
            return answer('refused', code='book_unknown', message=f"book_ref {item['book_ref']} is not known.")
        if popcore_id in self.listing_fail:
            return answer('error', code='listing_failed', message='Nothing was made for this item: boom.')
        number = self.listing(ids, 'variable' if item['kind'] == 'blind_box' else 'simple', item['kind'])
        self.shop[number].update(name=item['name'], item=item)
        return answer('created', number)

    def known(self, ref):
        return ref is None or self.by_ref(ref) is not None

    def apply(self, entry):
        ref = entry['ref']
        row = self.by_ref(ref)
        if row is None and entry['website_id'] is not None:
            row = self.rows.get((entry['kind'], entry['item'], entry['website_id']))
        if entry['deleted']:
            if row is None:
                return {'ref': ref, 'website_id': None, 'status': 'ok'}
            if row['website_id'] in self.live_listing:
                return {'ref': ref, 'website_id': row['website_id'], 'status': 'refused', 'code': 'has_live_listing',
                        'message': 'A published listing points at it.'}
            del self.rows[(row['kind'], row['item'], row['website_id'])]
            return {'ref': ref, 'website_id': row['website_id'], 'status': 'ok'}
        if ref in self.refuse:
            return {'ref': ref, 'website_id': None, 'status': 'refused', 'code': self.refuse[ref], 'message': 'Not taken.'}
        if not all(self.known(entry[field]) for field in ('parent_ref', 'partner_ref', 'character_ref', 'ip_ref')):
            return {'ref': ref, 'website_id': None, 'status': 'refused', 'code': 'parent_unknown', 'message': 'Unknown parent.'}
        if row is None:
            self.next_id += 1
            row = {'kind': entry['kind'], 'item': entry['item'], 'website_id': self.next_id, 'version': None}
            self.rows[(entry['kind'], entry['item'], self.next_id)] = row
        row['ref'] = ref
        if row['version'] is None or entry['version'] > row['version']:
            row.update(version=entry['version'], name=entry['name'], entry=entry)
        return {'ref': ref, 'website_id': row['website_id'], 'status': 'ok'}


class DexSyncTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(dex.bp)
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.site = FakeBook()
        self.web = patch('requests.request', side_effect=self.site.request)
        self.web.start()
        self.addCleanup(self.web.stop)
        with closing(self.connect()) as con:
            for sku, form in (('PM0100', 'random_box'), ('PM0100-01', 'confirmed_design'), ('PM0100-02', 'confirmed_design')):
                con.execute('INSERT INTO products(sku, name_cn_en, jizhanming, stock_form) VALUES (?, ?, ?, ?)',
                            (sku, f'Echo {sku}', f'Echo {sku}', form))
            self.items = {row[0]: row[1] for row in con.execute('SELECT sku, id FROM products')}
            con.commit()

    # Helpers ---------------------------------------------------------------------------------------------

    def add(self, kind, name, parent=None, **fields):
        with closing(self.connect()) as con:
            return dex_book.create(con, {'kind': kind, 'name': name, 'parent_id': parent, **fields}, ACTOR)['id']

    def change(self, entry_id, **fields):
        with closing(self.connect()) as con:
            return dex_book.update(con, entry_id, fields, ACTOR)

    def remove(self, entry_id):
        with closing(self.connect()) as con:
            return dex_book.delete(con, entry_id, ACTOR)

    def book(self, designs=2, link=True):
        company = self.add('company', 'POP MART')
        ip = self.add('ip', 'HIRONO', company)
        series = self.add('series', 'Echo Series', ip)
        product = self.add('product', 'Figures', series, blind_box=True, product_id=self.items['PM0100'] if link else None,
                           extras={'format': 'Figures'})
        made = [self.add('design', f'Design {n:02d}', product, rarity='normal') for n in range(designs)]
        return company, ip, series, product, made

    def row(self, entry_id):
        with closing(self.connect()) as con:
            found = con.execute('SELECT * FROM dex_entries WHERE id=?', (entry_id,)).fetchone()
            return dict(found) if found else None

    def price(self, sku, cents, at):
        with closing(self.connect()) as con:
            con.execute("""INSERT INTO design_prices(product_id, cents, previous_cents, source, set_by, set_at)
                           VALUES (?, ?, NULL, 'design_prices', 'auth0|manager', ?)""", (self.items[sku], cents, at))
            con.commit()

    def turn_on(self, **body):
        response = self.client.post('/api/dex/sync/switch', json={'on': True, **body}, headers=self.headers('admin'))
        return response

    def on(self):
        with closing(self.connect()) as con:
            dex_sync.set_state(con, 'enabled', '1')
            con.commit()

    def run_pass(self):
        dex_sync.background_sync()

    def status(self):
        response = self.client.get('/api/dex/sync', headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def sent(self):
        """Every entry posted, in order."""
        return [entry for method, path, body in self.site.calls if (method, path) == ('POST', '/book') for entry in body['entries']]

    # Off -------------------------------------------------------------------------------------------------

    def test_nothing_is_sent_or_written_while_off_or_without_settings(self):
        self.book()
        before = self.snapshot(['dex_entries', 'dex_sync_state'])
        self.run_pass()
        self.assertEqual(self.site.calls, [])
        self.assertEqual(self.snapshot(['dex_entries', 'dex_sync_state']), before)
        self.on()
        with patch.dict(os.environ, {'WEBSITE_KEY': ''}):
            before = self.snapshot(['dex_entries', 'dex_sync_state'])
            self.run_pass()
        self.assertEqual(self.site.calls, [])
        self.assertEqual(self.snapshot(['dex_entries', 'dex_sync_state']), before)

    # Order, batches, numbers -----------------------------------------------------------------------------

    def test_a_whole_book_goes_in_batches_parents_first_and_ends_with_nothing_waiting(self):
        company, ip, series, product, designs = self.book(designs=120)
        partner = self.add('partner', 'Disney')
        character = self.add('character', 'Hirono', ip)
        self.change(series, partner_id=partner, character_id=character)
        self.assertEqual(self.status()['counts']['waiting'], 126)
        self.on()
        self.run_pass()
        sizes = [len(refs) for quiet, refs in self.site.batches]
        self.assertTrue(all(size <= 50 for size in sizes), sizes)
        self.assertEqual(sum(sizes), 126)
        kinds = [entry['kind'] for entry in self.sent()]
        self.assertEqual(kinds, sorted(kinds, key=dex_book.KINDS.index))
        self.assertTrue(all(quiet for quiet, refs in self.site.batches))  # the first full send is quiet
        counts = self.status()['counts']
        self.assertEqual((counts['waiting'], counts['sent'], counts['refused'], counts['behind']), (0, 126, 0, 0))
        for entry_id in (company, partner, ip, character, series, product, *designs):
            row = self.row(entry_id)
            self.assertEqual(row['sent_version'], row['version'])
            self.assertEqual(self.site.by_ref(entry_id)['website_id'], row['website_id'])
        self.assertTrue(self.status()['first_send_done'])
        # Later changes are not quiet.
        self.change(designs[0], name='Renamed')
        self.run_pass()
        self.assertEqual(self.site.batches[-1], (False, [designs[0]]))

    def test_the_entry_shape_follows_the_contract(self):
        company, ip, series, product, designs = self.book(designs=1)
        self.change(designs[0], product_id=self.items['PM0100-01'], extras={'weight': '120g', 'market_prices': [{'price': 9}]})
        self.on()
        self.run_pass()
        shapes = {entry['ref']: entry for entry in self.sent()}
        self.assertEqual(shapes[series], {
            'ref': series, 'website_id': None, 'kind': 'series', 'parent_ref': ip, 'partner_ref': None,
            'character_ref': None, 'ip_ref': None, 'name': 'Echo Series', 'sort_order': 10, 'picture_id': None,
            'blind_box': None, 'item': False, 'rarity': None, 'popcore_id': None, 'extras': {}, 'version': 1,
            'deleted': False})
        self.assertEqual(shapes[product]['full_name'], 'HIRONO Echo Series Figures')
        self.assertEqual((shapes[product]['popcore_id'], shapes[product]['blind_box']), ('PM0100', True))
        design = shapes[designs[0]]
        self.assertEqual(design['full_name'], 'HIRONO Echo Series Figures - Design 00')
        self.assertEqual((design['popcore_id'], design['rarity']), ('PM0100-01', 'normal'))
        self.assertEqual(design['extras'], {'weight': '120g', 'price_history': []})  # 1.5: no market_prices

    def test_a_loaded_entry_is_found_by_its_website_number(self):
        company = self.add('company', 'POP MART')
        ip = self.add('ip', 'HIRONO', company)
        with closing(self.connect()) as con:
            dex_book.set_website_number(con, ip, 537, 0, ACTOR)
        self.site.preload('ip', 537, 'Hirono', version=None)
        self.on()
        self.run_pass()
        self.assertEqual(len(self.site.rows), 2)  # the company is new; the IP is the website's own row
        self.assertEqual(self.site.rows[('ip', False, 537)]['ref'], ip)
        self.assertEqual(self.row(ip)['website_id'], 537)

    # Refused, behind, failed -----------------------------------------------------------------------------

    def test_a_refused_entry_waits_with_its_children_until_it_changes_or_a_manager_retries(self):
        company, ip, series, product, designs = self.book()
        self.site.refuse[series] = 'bad_name'
        self.on()
        self.run_pass()
        state = self.status()
        self.assertEqual((state['counts']['refused'], state['counts']['behind'], state['counts']['sent']), (1, 3, 2))
        self.assertEqual(state['refused'][0]['id'], series)
        self.assertEqual((state['refused'][0]['code'], state['refused'][0]['message']), ('bad_name', 'Not taken.'))
        self.assertEqual(json.loads(self.row(series)['send_error'])['version'], self.row(series)['version'])
        sent_before = len(self.sent())
        self.run_pass()
        self.run_pass()
        self.assertEqual(len(self.sent()), sent_before)  # not re-sent every 30 seconds
        # A change sends it again (still refused here); a manager's retry too, and then its children follow.
        self.change(series, name='Echo Series 2')
        self.run_pass()
        self.assertIn(series, self.site.batches[-1][1])
        del self.site.refuse[series]
        response = self.client.post('/api/dex/sync/retry', json={'id': series}, headers=self.headers('manager'))
        self.assertEqual(response.get_json(), {'retried': 1})
        self.assertEqual(self.client.post('/api/dex/sync/retry', json={'id': series}, headers=self.headers('staff')).status_code, 403)
        self.run_pass()
        counts = self.status()['counts']
        self.assertEqual((counts['refused'], counts['behind'], counts['waiting']), (0, 0, 0))

    def test_a_product_with_no_format_is_held_with_what_sits_under_it_until_one_is_picked(self):
        """Contract 1.9 section 4: format goes on every product, and nothing is sent with a guess."""
        company, ip, series, product, designs = self.book()
        bare = self.add('product', 'Mystery box', series, blind_box=True)
        under = self.add('design', 'Hidden', bare, rarity='secret')
        old = self.add('product', 'Old line', series, extras={'format': 'Figures'})
        with closing(self.connect()) as con:  # a row the website has, then deleted: its delete must wait too
            dex_book.set_website_number(con, old, 77, False, ACTOR)
        self.site.preload('product', 77, 'Old line')
        self.remove(old)
        state = self.status()
        self.assertEqual((state['counts']['held'], state['counts']['behind']), (1, 1))
        self.assertEqual(state['held'], [{'id': bare, 'kind': 'product', 'name': 'Mystery box',
                                          'place': ['POP MART', 'HIRONO', 'Echo Series']}])
        self.assertEqual(state['refused'], [])
        preview = self.client.get('/api/dex/sync/preview', headers=self.headers('manager')).get_json()
        self.assertEqual((preview['create'], preview['held'], preview['delete']), (8, 1, 1))
        entry = self.client.get(f'/api/dex/sync/entries/{bare}', headers=self.headers('staff')).get_json()
        self.assertEqual((entry['state'], entry['code']), ('held', 'format_missing'))
        self.on()
        self.run_pass()
        self.assertEqual({entry['ref'] for entry in self.sent()}, {company, ip, series, product, *designs})
        self.assertTrue(self.status()['deletes_held'])
        self.assertIsNone(self.row(bare)['send_error'])  # held is worked out, never stored
        self.assertFalse(self.status()['first_send_done'])  # it is still part of the first, quiet send
        response = self.client.post('/api/dex/sync/retry', json={'id': bare}, headers=self.headers('manager'))
        self.assertEqual(response.status_code, 409)  # not refused by the website: a retry cannot send it
        self.change(bare, extras={'format': 'Fragrance Blind Box'})
        self.run_pass()
        shapes = {entry['ref']: entry for entry in self.sent()}
        self.assertEqual(shapes[bare]['extras']['format'], 'Fragrance Blind Box')
        self.assertIn(under, shapes)
        self.assertEqual([quiet for quiet, refs in self.site.batches if bare in refs], [True])
        self.assertIsNone(self.row(old))  # the delete went once every live entry was accepted
        counts = self.status()['counts']
        self.assertEqual((counts['held'], counts['behind'], counts['waiting']), (0, 0, 0))
        self.assertTrue(self.status()['first_send_done'])

    def test_aliases_and_tags_are_always_sent_as_lists(self):
        company, ip, series, product, designs = self.book(designs=0)
        with closing(self.connect()) as con:  # the load kept one series' tags as the website's comma text
            con.execute("""UPDATE dex_entries SET extras='{"tags": "cute, , sweet", "aliases": " "}' WHERE id=?""", (series,))
            con.commit()
        self.change(ip, extras={'aliases': ['拉布布']})
        self.on()
        self.run_pass()
        shapes = {entry['ref']: entry for entry in self.sent()}
        self.assertEqual(shapes[series]['extras'], {'tags': ['cute', 'sweet'], 'aliases': None})
        self.assertEqual(shapes[ip]['extras'], {'aliases': ['拉布布']})

    def test_a_child_refused_only_because_its_parent_was_refused_in_the_same_batch_is_not_marked(self):
        company, ip, series, product, designs = self.book()
        self.site.refuse[ip] = 'bad_name'
        self.on()
        self.run_pass()
        self.assertIsNone(self.row(series)['send_error'])
        self.assertEqual(self.status()['counts']['behind'], 4)

    def test_a_failed_call_marks_nothing_and_the_next_pass_tries_again(self):
        company, ip, series, product, designs = self.book(link=False)
        self.on()
        for broken in ('down', 'status', 'auth'):
            if broken == 'down':
                self.site.down = True
            elif broken == 'status':
                self.site.status = 500
            else:
                self.env.stop()
                self.env = patch.dict(os.environ, {**ENV, 'WEBSITE_KEY': 'ck_wrong'})
                self.env.start()
            before = self.snapshot(['dex_entries'])
            self.run_pass()
            self.assertEqual(self.snapshot(['dex_entries']), before)
            error = self.status()['last_error']
            self.assertIn(error['code'], ('website_unavailable', 'website_error', 'website_auth'))
            self.assertIsNotNone(self.status()['last_run'])
            self.site.down, self.site.status = False, None
        self.env.stop()
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.run_pass()
        state = self.status()
        self.assertIsNone(state['last_error'])
        self.assertEqual(state['counts']['waiting'], 0)

    def test_an_unexpected_failure_is_kept_as_the_last_error_and_not_raised(self):
        self.book()
        self.on()
        with patch.object(dex_sync, 'run_once', side_effect=RuntimeError('boom')), self.assertLogs('dex_sync', 'ERROR'):
            self.run_pass()
        self.assertEqual(self.status()['last_error']['code'], 'dex_sync_failed')

    def test_an_entry_changed_while_being_sent_goes_again(self):
        company, ip, series, product, designs = self.book()
        self.on()
        self.site.during = lambda: self.change(designs[0], name='Changed meanwhile')
        self.run_pass()
        versions = [entry['version'] for entry in self.sent() if entry['ref'] == designs[0]]
        self.assertEqual(versions, [1, 2])  # the answer for version 1 left it waiting; the next batch sent version 2
        self.assertEqual(self.site.by_ref(designs[0])['name'], 'Changed meanwhile')
        self.assertEqual(self.status()['counts']['waiting'], 0)

    # Deletes ---------------------------------------------------------------------------------------------

    def test_deletes_go_last_children_first_and_rows_go_once_confirmed(self):
        company, ip, series, product, designs = self.book(link=False)
        other = self.add('series', 'Never sent', ip)
        self.on()
        self.run_pass()
        self.change(ip, name='HIRONO!')
        self.remove(series)
        unsent = self.add('series', 'Made and deleted', ip)
        self.remove(unsent)
        self.assertIsNone(self.row(unsent))  # the website never had it: gone at once
        self.assertIsNotNone(self.row(designs[0])['deleted_at'])
        preview = self.client.get('/api/dex/sync/preview', headers=self.headers('manager')).get_json()
        self.assertEqual((preview['delete'], preview['update']), (4, 2))  # the IP and the series still under it
        self.assertEqual([one['kind'] for one in preview['deletes']], ['design', 'design', 'product', 'series'])
        self.assertEqual(preview['deletes'][-1]['place'], ['POP MART', 'HIRONO!'])
        self.site.calls.clear()
        self.run_pass()
        sent = self.sent()
        self.assertEqual([entry['ref'] for entry in sent[:2]], [ip, other])  # changes first, then deletes, children first
        self.assertEqual([entry['ref'] for entry in sent[2:]], [*designs, product, series])
        self.assertTrue(all(entry['deleted'] for entry in sent[2:]))
        for entry_id in (*designs, product, series):
            self.assertIsNone(self.row(entry_id))
            self.assertIsNone(self.site.by_ref(entry_id))
        self.assertIsNotNone(self.site.by_ref(other))

    def test_deletes_wait_until_every_live_entry_was_accepted(self):
        """What the book moved out from under a deleted row must be on the website before the delete goes."""
        company, ip, series, product, designs = self.book(designs=1, link=False)
        self.on()
        self.run_pass()
        kept = self.add('series', 'Crying Again Series', ip)
        self.change(product, parent_id=kept)  # the line moves out, then its old series is deleted
        self.remove(series)
        self.site.refuse[product] = 'not_now'
        self.run_pass()
        self.assertIsNotNone(self.site.by_ref(series))  # not deleted: the line has not arrived at its new place
        self.assertIsNotNone(self.row(series))
        status = self.status()
        self.assertTrue(status['deletes_held'])
        self.assertEqual((status['counts']['refused'], status['counts']['deletes']), (1, 1))
        self.site.calls.clear()
        self.run_pass()
        self.assertEqual(self.sent(), [])  # held, not retried every pass
        del self.site.refuse[product]
        self.client.post('/api/dex/sync/retry', json={'all': True}, headers=self.headers('manager'))
        self.run_pass()
        self.assertIsNone(self.site.by_ref(series))
        self.assertIsNone(self.row(series))
        self.assertEqual(self.site.by_ref(product)['entry']['parent_ref'], kept)
        self.assertFalse(self.status()['deletes_held'])

    def test_a_parent_is_not_deleted_while_a_delete_under_it_is_refused(self):
        company, ip, series, product, designs = self.book(designs=1, link=False)
        self.on()
        self.run_pass()
        self.site.live_listing.add(self.row(designs[0])['website_id'])
        self.remove(series)
        self.run_pass()
        for entry_id in (designs[0], product, series):  # the design's delete was refused; nothing above it was sent
            self.assertIsNotNone(self.site.by_ref(entry_id))
            self.assertIsNotNone(self.row(entry_id))
        self.assertEqual([entry['ref'] for entry in self.sent() if entry['deleted']], [designs[0]])
        self.site.live_listing.clear()
        self.client.post('/api/dex/sync/retry', json={'all': True}, headers=self.headers('manager'))
        self.run_pass()
        for entry_id in (designs[0], product, series):
            self.assertIsNone(self.site.by_ref(entry_id))
            self.assertIsNone(self.row(entry_id))

    def test_a_pass_with_nothing_new_takes_no_write_lock(self):
        """The till writes to the same database: a pass that has nothing to do only reads."""
        self.book()
        self.on()
        self.run_pass()
        with closing(self.connect()) as till, closing(self.connect()) as con:
            con.execute('PRAGMA busy_timeout=0')
            till.execute('BEGIN IMMEDIATE')  # the till is in the middle of a sale
            try:
                self.assertEqual(dex_sync.notice(con), 0)
                self.assertFalse(con.in_transaction)
            finally:
                till.rollback()

    def test_a_refused_delete_is_kept_and_listed(self):
        company, ip, series, product, designs = self.book(designs=0, link=False)
        self.on()
        self.run_pass()
        self.site.live_listing.add(self.row(product)['website_id'])
        self.remove(product)
        self.run_pass()
        self.assertIsNotNone(self.row(product))
        refused = self.status()['refused']
        self.assertEqual((refused[0]['id'], refused[0]['code'], refused[0]['deleted']), (product, 'has_live_listing', True))

    def test_a_deleted_row_with_a_website_number_never_sent_is_sent_as_deleted(self):
        company = self.add('company', 'POP MART')
        ip = self.add('ip', 'NORI', company)
        with closing(self.connect()) as con:
            dex_book.set_website_number(con, ip, 41, 0, ACTOR)
        self.site.preload('ip', 41, 'NORI')
        self.remove(ip)
        self.on()
        self.run_pass()
        self.assertIsNone(self.row(ip))
        self.assertNotIn(('ip', False, 41), self.site.rows)

    # Versions ------------------------------------------------------------------------------------------

    def test_renaming_or_moving_an_entry_raises_the_version_of_everything_under_it(self):
        company, ip, series, product, designs = self.book()
        before = {entry_id: self.row(entry_id)['version'] for entry_id in (company, ip, series, product, *designs)}
        self.change(ip, name='HIRONO 2')
        after = {entry_id: self.row(entry_id)['version'] for entry_id in before}
        self.assertEqual(after[company], before[company])
        for entry_id in (ip, series, product, *designs):
            self.assertEqual(after[entry_id], before[entry_id] + 1)
        other = self.add('ip', 'MOLLY', company)
        self.change(series, parent_id=other)
        for entry_id in (product, *designs):
            self.assertEqual(self.row(entry_id)['version'], before[entry_id] + 2)
        self.change(series, extras={'designer': 'Lang'})  # not a rename: nothing under it changes
        self.assertEqual(self.row(product)['version'], before[product] + 2)

    # Price history (contract 1.5) ---------------------------------------------------------------------------

    def test_a_design_sends_every_recorded_price_and_a_new_price_sends_it_again(self):
        company, ip, series, product, designs = self.book()
        self.change(designs[0], product_id=self.items['PM0100-01'])
        for cents, at in ((2599, '2026-11-01 10:00:00'), (2799, '2026-11-07 10:00:00'), (2999, '2026-11-13 10:00:00')):
            self.price('PM0100-01', cents, at)
        self.on()
        self.run_pass()
        shapes = {entry['ref']: entry for entry in self.sent()}
        self.assertEqual(shapes[designs[0]]['extras']['price_history'], [
            {'price': 25.99, 'recorded_at': '2026-11-01T10:00:00Z'}, {'price': 27.99, 'recorded_at': '2026-11-07T10:00:00Z'},
            {'price': 29.99, 'recorded_at': '2026-11-13T10:00:00Z'}])
        self.assertEqual(shapes[designs[1]]['extras']['price_history'], [])  # never priced: the chart shows nothing
        self.assertEqual(shapes[product]['extras']['price_history'], [])  # stocked, no price recorded yet
        version = self.row(designs[0])['version']
        self.price('PM0100-01', 3199, '2026-11-14 10:00:00')
        self.assertEqual(self.status()['counts']['waiting'], 1)
        self.assertEqual(self.client.get('/api/dex/sync/preview', headers=self.headers('manager')).get_json()['update'], 1)
        self.run_pass()
        last = self.sent()[-1]
        self.assertEqual((last['ref'], last['version']), (designs[0], version + 1))
        self.assertEqual(len(last['extras']['price_history']), 4)
        self.assertEqual(self.site.by_ref(designs[0])['entry']['extras']['price_history'][-1]['price'], 31.99)
        self.assertEqual(self.status()['counts']['waiting'], 0)

    def test_a_stocked_product_sends_its_own_price_changes(self):
        """Owner, 2026-11-14, asked whether every item's price changes should be kept: "Yes, every item"."""
        company, ip, series, product, designs = self.book(designs=0)
        loose = self.add('product', 'Not stocked', series, extras={'format': 'Merchandise'})
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET price=19.99 WHERE sku=?', ('PM0100',))
            con.execute('UPDATE products SET price=0 WHERE sku=?', ('PM0100',))  # not a point
            con.execute('UPDATE products SET price=24.99 WHERE sku=?', ('PM0100',))
            con.commit()
        self.on()
        self.run_pass()
        shapes = {entry['ref']: entry for entry in self.sent()}
        self.assertEqual([point['price'] for point in shapes[product]['extras']['price_history']], [19.99, 24.99])
        self.assertRegex(shapes[product]['extras']['price_history'][0]['recorded_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertNotIn('price_history', shapes[loose]['extras'])  # no POPCORE item: the website's own points stay
        self.assertEqual(self.status()['counts']['waiting'], 0)
        version = self.row(product)['version']
        with closing(self.connect()) as con:
            con.execute('UPDATE products SET price=22 WHERE sku=?', ('PM0100',))
            con.commit()
        self.assertEqual(self.status()['counts']['waiting'], 1)
        self.run_pass()
        last = self.sent()[-1]
        self.assertEqual((last['ref'], last['version']), (product, version + 1))
        self.assertEqual([point['price'] for point in last['extras']['price_history']], [19.99, 24.99, 22.0])
        self.assertEqual(self.status()['counts']['waiting'], 0)

    def test_a_new_popcore_id_on_a_linked_item_sends_the_entry_again(self):
        company, ip, series, product, designs = self.book(designs=0)
        self.on()
        self.run_pass()
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET sku='PM0101' WHERE id=?", (self.items['PM0100'],))
            con.commit()
        self.run_pass()
        self.assertEqual(self.sent()[-1]['popcore_id'], 'PM0101')

    # The switch ----------------------------------------------------------------------------------------------

    def test_the_switch_is_for_admins_and_refused_while_the_website_half_is_missing(self):
        self.book()
        self.assertEqual(self.client.post('/api/dex/sync/switch', json={'on': True}, headers=self.headers('manager')).status_code, 403)
        self.site.missing = True
        response = self.turn_on()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'website_half_missing')
        self.assertIn('not there yet', response.get_json()['error'])
        self.assertFalse(self.status()['enabled'])
        self.assertEqual(self.status()['website_check']['code'], 'website_half_missing')
        self.site.missing = False
        self.env.stop()
        self.env = patch.dict(os.environ, {**ENV, 'WEBSITE_KEY': 'ck_wrong'})
        self.env.start()
        self.assertEqual(self.turn_on().get_json()['code'], 'website_half_missing')
        self.assertFalse(self.status()['enabled'])
        self.env.stop()
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        response = self.turn_on()
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()['enabled'])
        self.assertEqual(self.site.calls[-1][:2], ('GET', '/book'))
        off = self.client.post('/api/dex/sync/switch', json={'on': False}, headers=self.headers('admin'))
        self.assertFalse(off.get_json()['enabled'])

    def test_turning_on_with_deletes_needs_their_count_typed(self):
        company, ip, series, product, designs = self.book(designs=0, link=False)
        with closing(self.connect()) as con:
            dex_book.set_website_number(con, series, 77, 0, ACTOR)
        self.remove(series)
        response = self.turn_on()
        self.assertEqual((response.status_code, response.get_json()['code']), (400, 'confirm_deletes'))
        self.assertEqual(self.turn_on(confirm_deletes=2).status_code, 400)  # the product had no number: gone, not sent
        preview = self.client.get('/api/dex/sync/preview', headers=self.headers('manager')).get_json()
        self.assertEqual(preview['delete'], 1)
        self.assertEqual([one['name'] for one in preview['deletes']], ['Echo Series'])
        response = self.turn_on(confirm_deletes='1')
        self.assertEqual(response.status_code, 200, response.get_json())

    # Pictures ------------------------------------------------------------------------------------------------

    def test_a_picture_is_passed_to_the_website_and_kept_on_the_entry(self):
        company, ip, series, product, designs = self.book(designs=0)
        version = self.row(series)['version']
        response = self.client.post(f'/api/dex/entries/{series}/picture', data={'file': (io.BytesIO(PNG), 'echo.png')},
                                    headers=self.headers('manager'), content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['thumb_url'], 'https://shop.test/p/7001-150.png')
        row = self.row(series)
        self.assertEqual((row['picture_id'], row['version']), (7001, version + 1))
        self.assertEqual(self.site.pictures[0][0], str(series))
        self.assertEqual(self.site.pictures[0][1][2], 'image/png')
        bad = self.client.post(f'/api/dex/entries/{series}/picture', data={'file': (io.BytesIO(b'not an image'), 'x.png')},
                               headers=self.headers('manager'), content_type='multipart/form-data')
        self.assertEqual(bad.get_json()['code'], 'invalid_image')
        staff = self.client.post(f'/api/dex/entries/{series}/picture', data={'file': (io.BytesIO(PNG), 'echo.png')},
                                 headers=self.headers('staff'), content_type='multipart/form-data')
        self.assertEqual(staff.status_code, 403)
        self.site.missing = True
        missing = self.client.post(f'/api/dex/entries/{series}/picture', data={'file': (io.BytesIO(PNG), 'echo.png')},
                                   headers=self.headers('manager'), content_type='multipart/form-data')
        self.assertEqual((missing.status_code, missing.get_json()['code']), (409, 'website_half_missing'))
        self.assertEqual(self.row(series)['picture_id'], 7001)

    # The entry's state, and reading the website's book ------------------------------------------------------

    def test_an_entry_page_reads_its_state(self):
        company, ip, series, product, designs = self.book(designs=0)
        self.site.refuse[product] = 'bad_type'
        self.on()
        self.run_pass()
        read = lambda entry_id: self.client.get(f'/api/dex/sync/entries/{entry_id}', headers=self.headers('staff')).get_json()
        self.assertEqual(read(series)['state'], 'sent')
        self.assertEqual((read(product)['state'], read(product)['code']), ('refused', 'bad_type'))
        self.change(series, name='Echo Series 3')
        self.assertEqual(read(series)['state'], 'waiting')

    def test_the_fetch_script_pages_through_the_website_book(self):
        for number in range(1, 1203):
            self.site.preload('design', number, f'Design {number}')
        out = Path(tempfile.mkdtemp(dir=ROOT / '.local' / 'tmp')) / 'book-export.json'
        with redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(fetch_dex_book.main(['--out', str(out)]), 0)
        document = json.loads(out.read_text())
        self.assertEqual(len(document['entries']), 1202)
        self.assertIn('made_at', document)
        self.assertEqual(json.loads(printed.getvalue())['entries'], 1202)
        pages = [path for method, path, body in self.site.calls if method == 'GET']
        self.assertEqual(len(pages), 3)
        self.assertEqual(self.site.calls[0][1], '/book')
        self.site.missing = True
        with patch('sys.stderr', io.StringIO()):
            self.assertEqual(fetch_dex_book.main(['--out', str(out.with_name('again.json'))]), 1)
        self.assertFalse(out.with_name('again.json').exists())

    # The migration --------------------------------------------------------------------------------------------

    def test_the_migration_runs_twice_and_starts_from_todays_links_and_prices(self):
        import db
        company, ip, series, product, designs = self.book()
        self.change(designs[0], product_id=self.items['PM0100-01'])
        self.price('PM0100-01', 2599, '2026-11-01 10:00:00')
        with closing(self.connect()) as con:
            con.execute("UPDATE dex_entries SET sync_price_id=NULL, sync_popcore_id=NULL")
            con.execute("DELETE FROM _migrations WHERE name='dex_sync'")
            con.commit()
        db.migrate_db()
        db.migrate_db()
        with closing(self.connect()) as con:
            price_id = con.execute('SELECT MAX(id) FROM design_prices').fetchone()[0]
        self.assertEqual((self.row(designs[0])['sync_price_id'], self.row(designs[0])['sync_popcore_id']), (price_id, 'PM0100-01'))
        self.assertEqual((self.row(product)['sync_price_id'], self.row(product)['sync_popcore_id']), (None, 'PM0100'))
        self.assertIsNone(self.row(designs[1])['sync_popcore_id'])
        with closing(self.connect()) as con:
            self.assertEqual(dex_sync.notice(con), 0)  # nothing new to notice: no version raised


LIVE_DATE = '2026-11-01'
UNITS = {'random_box': 'box', 'sealed_set': 'set', 'confirmed_design': 'piece', 'ordinary': 'piece'}


class DexListingsTests(IsolatedApiCase):
    """图鉴 Dex step 4 (contract 1.9.6 sections 7 and 10): listings for stocked items with no listing, from a preview
    list a manager approves, sent only while the Dex switch is on and once the book entry is on the website. Sonny
    Angel Makeup Angel (DR0133, DR0133-SET, DR0133-01 to -05) is the contract's test: the box and its Whole Set are on
    popcore.ca already, the five designs are not."""

    def setUp(self):
        super().setUp()
        from scripts.inventory_opening import main as opening_cli
        self.app.register_blueprint(dex.bp)
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.site = FakeBook()
        self.web = patch('requests.request', side_effect=self.site.request)
        self.web.start()
        self.addCleanup(self.web.stop)
        with closing(self.connect()) as con:
            dt = con.execute("SELECT id FROM stores WHERE code='DT'").fetchone()[0]
            self.locations = {row[0]: row[1] for row in con.execute(
                'SELECT code, id FROM inventory_locations WHERE store_id=?', (dt,))}
            self.items = {}
            for sku, name, form, price, per_set in (
                    ('DR0133', 'Sonny Angel x Shu Uemura Makeup Angel Series', 'random_box', 29.99, 6),
                    ('DR0133-SET', 'Sonny Angel x Shu Uemura Makeup Angel Series · 整端 Sealed set', 'sealed_set', 179.94, 6),
                    ('DR0133-01', 'Makeup Angel · Artsy Peacock', 'confirmed_design', 28.99, None),
                    ('DR0133-02', 'Makeup Angel · Zen Deer', 'confirmed_design', 30.99, None),
                    ('DR0133-03', 'Makeup Angel · Lava Fox', 'confirmed_design', 30.99, None),
                    ('DR0133-04', 'Makeup Angel · Solar Tanuki', 'confirmed_design', 30.99, None),
                    ('DR0133-05', 'Makeup Angel · Shu-Shu Monkey', 'confirmed_design', 29.99, None),
                    ('PM0100', 'Echo Series Figures', 'random_box', 15.99, 12),
                    ('PM0100-SET', 'Echo Series 整端', 'sealed_set', 191.88, 12),
                    ('PM0200', 'Bear Phone Strap', 'ordinary', 12.00, None),
                    ('PM0300', 'Twinkle Figures', 'random_box', 16.99, None),
                    ('PM0400', 'Not In The Book Plush', 'ordinary', 20.00, None),
                    ('PM0500', 'Sold Out Figures', 'random_box', 15.99, None)):
                self.items[sku] = con.execute(
                    """INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, price, product_type,
                                            boxes_per_dan, identity_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'verified')""",
                    (sku, name, name, form, UNITS[form], price, '周边' if form == 'ordinary' else '盲盒',
                     per_set)).lastrowid
            con.execute("""INSERT INTO product_conversions(source_product_id, target_product_id, output_per_input, version)
                           VALUES (?, ?, 6, 1)""", (self.items['DR0133-SET'], self.items['DR0133']))
            # The manager's prices for four designs (14 Nov); -05 has none, so it never takes the box's price.
            for sku, cents in (('DR0133-01', 2899), ('DR0133-02', 3099), ('DR0133-03', 3099), ('DR0133-04', 3099)):
                con.execute("""INSERT INTO design_prices(product_id, cents, previous_cents, source, set_by, set_at)
                               VALUES (?, ?, 2999, 'design_prices', 'auth0|manager', '2026-11-14 19:12:24')""",
                            (self.items[sku], cents))
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|admin', ?)", (dt,))
            for sku, code, quantity in (('DR0133', 'floor', 181), ('DR0133-SET', 'upstairs', 38),
                                        *((f'DR0133-0{n}', 'upstairs', 50 + n) for n in range(1, 6)),
                                        ('PM0100', 'floor', 20), ('PM0200', 'floor', 3), ('PM0300', 'upstairs', 4),
                                        ('PM0400', 'floor', 2), ('PM0500', 'floor', 0)):
                form = con.execute('SELECT stock_form FROM products WHERE id=?', (self.items[sku],)).fetchone()[0]
                con.execute("""INSERT INTO opening_counts(location_id, product_id, stock_form, quantity, counted_by)
                               VALUES (?, ?, ?, ?, 'auth0|admin')""", (self.locations[code], self.items[sku], form, quantity))
            con.commit()
        empty = [arg for code in sorted(self.locations) if code not in ('floor', 'upstairs')
                 for arg in ('--empty-location', f'DT-{code}')]
        with redirect_stdout(io.StringIO()):
            opening_cli(['intake', '--db', db_path(), '--from-app', '--store', 'DT', *empty, '--business-date', LIVE_DATE,
                         '--actor-sub', 'auth0|admin', '--apply', '--backup', str(Path(self.tempdir.name) / 'before-dt.db')])
        # The book, as live holds Makeup Angel (designs 3877-3881 on line 515), and Echo, Bear, Twinkle, Sold Out.
        self.company = self.add('company', 'Dreams')
        self.ip = self.add('ip', 'Sonny Angel', self.company)
        series = self.add('series', 'Makeup Angel Series', self.ip)
        self.box = self.add('product', 'Limited Mini Figure', series, blind_box=True, product_id=self.items['DR0133'],
                            extras={'format': 'Figures'})
        self.designs = [self.add('design', name, self.box, rarity='normal', product_id=self.items[f'DR0133-0{n}'])
                        for n, name in enumerate(('Artsy Peacock', 'Zen Deer', 'Lava Fox', 'Solar Tanuki',
                                                  'Shu-Shu Monkey'), 1)]
        echo = self.add('series', 'Echo Series', self.ip)
        self.echo = self.add('product', 'Figures', echo, blind_box=True, product_id=self.items['PM0100'],
                             extras={'format': 'Figures'})
        self.bear = self.add('product', 'Bear Phone Strap', self.ip, product_id=self.items['PM0200'],
                             extras={'format': 'Merchandise'})
        twinkle = self.add('series', 'Twinkle Series', self.ip)
        self.twinkle = self.add('product', 'Figures', twinkle, blind_box=True, product_id=self.items['PM0300'],
                                extras={'format': 'Figures'})
        self.add('product', 'Sold Out Figures', self.ip, product_id=self.items['PM0500'], extras={'format': 'Figures'})
        # popcore.ca has DR0133 and DR0133-SET on one listing (series 537), and the stock sync's last read saw them.
        self.site.listing({'single': 'DR0133', 'set': 'DR0133-SET'}, 'variable', 'blind_box')
        self.read({'DR0133': self.items['DR0133'], 'DR0133-SET': self.items['DR0133-SET']})

    # Helpers ---------------------------------------------------------------------------------------------

    def add(self, kind, name, parent=None, **fields):
        with closing(self.connect()) as con:
            return dex_book.create(con, {'kind': kind, 'name': name, 'parent_id': parent, **fields}, ACTOR)['id']

    def read(self, ids, at='2026-11-01T12:00:00'):
        """The stock sync's catalogue read: these POPCORE IDs on website items (variations of one listing)."""
        with closing(self.connect()) as con:
            con.execute('DELETE FROM website_items')
            for number, (popcore_id, product_id) in enumerate(ids.items(), 1):
                con.execute("""INSERT INTO website_items(wc_id, parent_id, kind, sku, popcore_id, name, status, product_id)
                               VALUES (?, 1, 'variation', '', ?, ?, 'publish', ?)""",
                            (100 + number, popcore_id, popcore_id, product_id))
            import website_sync
            website_sync.set_state(con, 'refreshed_at', at)
            website_sync.set_state(con, 'refresh_requested', None)
            con.commit()

    def on(self):
        with closing(self.connect()) as con:
            dex_sync.set_state(con, 'enabled', '1')
            con.commit()

    def run_pass(self):
        dex_sync.background_sync()

    def preview(self, role='manager'):
        response = self.client.get('/api/dex/listings', headers=self.headers(role))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def approve(self, **body):
        return self.client.post('/api/dex/listings/approve', json=body, headers=self.headers('manager'))

    def row(self, sku):
        with closing(self.connect()) as con:
            found = con.execute('SELECT * FROM dex_listings WHERE product_id=?', (self.items[sku],)).fetchone()
            return dict(found) if found else None

    def refresh_asked(self):
        import website_sync
        with closing(self.connect()) as con:
            return website_sync.state(con, 'refresh_requested')

    def sent(self):
        return [item for call in self.site.listing_calls for item in call]

    def by_id(self, preview):
        return {one['popcore_id']: one for one in preview['candidates']}

    # The preview -----------------------------------------------------------------------------------------

    def test_the_preview_lists_stocked_items_with_no_listing_and_nothing_goes_without_approval(self):
        preview = self.preview()
        found = self.by_id(preview)
        # DR0133 and DR0133-SET are on popcore.ca; PM0500 has no stock; the sealed sets are never items of their own.
        self.assertEqual(sorted(found), ['DR0133-01', 'DR0133-02', 'DR0133-03', 'DR0133-04', 'DR0133-05',
                                         'PM0100', 'PM0200', 'PM0300', 'PM0400'])
        self.assertEqual([one['popcore_id'] for one in preview['candidates']][:3], ['PM0100', 'PM0300', 'PM0200'])
        self.assertEqual(preview['catalogue_read_at'], '2026-11-01T12:00:00')
        self.assertEqual({key: preview['counts'][key] for key in ('candidates', 'ready', 'book_waiting', 'not_in_book',
                                                                  'approvable', 'new', 'complete')},
                         {'candidates': 9, 'ready': 0, 'book_waiting': 8, 'not_in_book': 1, 'approvable': 8,
                          'new': 9, 'complete': 0})
        design = found['DR0133-01']
        self.assertEqual({key: design[key] for key in ('kind', 'name', 'book_ref', 'price_cents', 'popcore_type',
                                                       'state', 'missing', 'action', 'stock')},
                         {'kind': 'confirmed_design', 'name': 'Sonny Angel Makeup Angel Series Limited Mini Figure - Artsy Peacock',
                          'book_ref': self.designs[0], 'price_cents': 2899, 'popcore_type': '盲盒',
                          'state': 'book_waiting', 'missing': ['DR0133-01'], 'action': 'new', 'stock': 51})
        self.assertNotIn('set_id', design)
        self.assertIsNone(found['DR0133-05']['price_cents'])  # no manager price: never the box's 29.99
        self.assertEqual((found['PM0400']['state'], found['PM0400']['entry'], found['PM0400']['book_ref']),
                         ('not_in_book', None, None))
        echo = found['PM0100']
        self.assertEqual({key: echo[key] for key in ('kind', 'set_id', 'boxes_per_set', 'price_cents', 'set_price_cents',
                                                     'missing', 'stock', 'set_stock')},
                         {'kind': 'blind_box', 'set_id': 'PM0100-SET', 'boxes_per_set': 12, 'price_cents': 1599,
                          'set_price_cents': 19188, 'missing': ['PM0100', 'PM0100-SET'], 'stock': 20, 'set_stock': 0})
        twinkle = found['PM0300']  # no Whole Set in POPCORE
        self.assertEqual((twinkle['set_id'], twinkle['boxes_per_set'], twinkle['set_price_cents']), (None, None, None))
        self.assertEqual(self.client.get('/api/dex/listings', headers=self.headers('staff')).status_code, 403)
        # Switch on, nothing approved: the book goes, no listing does.
        self.on()
        self.run_pass()
        self.assertEqual(self.site.listing_calls, [])
        self.assertEqual(self.preview()['counts']['ready'], 8)

    def test_no_catalogue_read_means_no_preview(self):
        with closing(self.connect()) as con:
            con.execute("DELETE FROM website_state WHERE key='refreshed_at'")
            con.commit()
        preview = self.preview()
        self.assertEqual((preview['catalogue_read_at'], preview['candidates'], preview['counts']['candidates']), (None, [], 0))

    # Makeup Angel ----------------------------------------------------------------------------------------

    def test_makeup_angel_five_designs_are_created_tied_to_their_box_and_dex_designs(self):
        response = self.approve(all=True, count=8)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json(), {'approved': 8})
        preview = self.preview()
        self.assertEqual((preview['counts']['approved'], preview['counts']['candidates']), (8, 1))
        self.assertTrue(all(one['waits'] == 'book' for one in preview['approved']))
        # The switch is off: nothing goes, and nothing is written.
        before = self.snapshot(['dex_listings', 'dex_entries'])
        self.run_pass()
        self.assertEqual(self.site.calls, [])
        self.assertEqual(self.snapshot(['dex_listings', 'dex_entries']), before)
        self.on()
        self.run_pass()
        # The book went first, then one listings call: boxes and items before the designs.
        self.assertEqual(len(self.site.listing_calls), 1)
        self.assertEqual([item['popcore_id'] for item in self.sent()],
                         ['PM0100', 'PM0300', 'PM0200', 'DR0133-01', 'DR0133-02', 'DR0133-03', 'DR0133-04', 'DR0133-05'])
        designs = {item['popcore_id']: item for item in self.sent() if item['kind'] == 'confirmed_design'}
        self.assertEqual(designs['DR0133-03'], {
            'popcore_id': 'DR0133-03', 'kind': 'confirmed_design',
            'name': 'Sonny Angel Makeup Angel Series Limited Mini Figure - Lava Fox', 'book_ref': self.designs[2],
            'popcore_type': '盲盒', 'price_cents': 3099})
        self.assertEqual([designs[f'DR0133-0{n}']['price_cents'] for n in range(1, 6)], [2899, 3099, 3099, 3099, None])
        self.assertEqual([designs[f'DR0133-0{n}']['book_ref'] for n in range(1, 6)], self.designs)
        echo = self.sent()[0]
        self.assertEqual(echo, {'popcore_id': 'PM0100', 'kind': 'blind_box', 'name': 'Sonny Angel Echo Series Figures',
                                'book_ref': self.echo, 'popcore_type': '盲盒', 'price_cents': 1599,
                                'set_id': 'PM0100-SET', 'boxes_per_set': 12, 'set_price_cents': 19188})
        self.assertNotIn('set_id', self.sent()[2])  # an ordinary item carries no Whole Set
        for n in range(1, 6):
            row = self.row(f'DR0133-0{n}')
            self.assertEqual(row['state'], 'created')
            made = self.site.shop[row['website_product_id']]
            self.assertEqual((made['type'], made['kind'], made['ids']), ('simple', 'confirmed_design', {'own': f'DR0133-0{n}'}))
            self.assertEqual(self.site.by_ref(made['item']['book_ref'])['kind'], 'design')
        self.assertEqual(self.site.shop[self.row('PM0100')['website_product_id']]['ids'],
                         {'single': 'PM0100', 'set': 'PM0100-SET'})
        self.assertEqual(self.refresh_asked(), '1')  # the stock sync reads the catalogue again next
        preview = self.preview()
        self.assertEqual((preview['counts']['approved'], preview['counts']['created'], preview['counts']['done']), (0, 8, 8))
        self.assertEqual(sorted(one['popcore_id'] for one in preview['candidates']), ['PM0400'])
        # Answered items are never sent again, and the name went once only.
        self.run_pass()
        self.assertEqual(len(self.site.listing_calls), 1)

    def test_a_box_whose_whole_set_is_missing_is_completed_and_one_already_whole_answers_exists(self):
        # The read saw PM0100's Single Box only; popcore.ca holds it on a variable listing.
        number = self.site.listing({'single': 'PM0100'}, 'variable', 'blind_box')
        self.read({'DR0133': self.items['DR0133'], 'PM0100': self.items['PM0100']})
        preview = self.by_id(self.preview())
        # DR0133-SET is missing from this read, so Makeup Angel's box is offered too.
        self.assertEqual((preview['PM0100']['action'], preview['PM0100']['missing']), ('complete', ['PM0100-SET']))
        self.assertEqual((preview['DR0133']['action'], preview['DR0133']['missing']), ('complete', ['DR0133-SET']))
        self.on()
        self.run_pass()  # the book goes: the boxes are ready
        self.assertEqual(self.approve(product_ids=[self.items['PM0100'], self.items['DR0133']]).status_code, 200)
        self.run_pass()
        self.assertEqual((self.row('PM0100')['state'], self.row('PM0100')['website_product_id']), ('completed', number))
        self.assertEqual(self.site.shop[number]['ids'], {'single': 'PM0100', 'set': 'PM0100-SET'})
        self.assertEqual(self.row('DR0133')['state'], 'exists')  # the website's own check: it has both already
        self.assertEqual(self.sent()[0]['set_price_cents'], 17994)
        self.assertEqual(self.refresh_asked(), '1')

    def test_an_exists_answer_alone_asks_for_no_catalogue_read(self):
        self.read({})  # a stale read: it saw nothing, popcore.ca has Makeup Angel's box
        self.on()
        self.run_pass()
        self.approve(product_ids=[self.items['DR0133']])
        self.run_pass()
        self.assertEqual(self.row('DR0133')['state'], 'exists')
        self.assertIsNone(self.refresh_asked())

    # Refusals ----------------------------------------------------------------------------------------------

    def test_every_refusal_code_is_kept_listed_and_not_sent_again(self):
        self.site.listing({'single': 'PM0300'}, 'simple', 'blind_box')  # simple listing: no Whole Set option to add
        self.site.listing({'own': 'DR0133-02'}, 'simple', 'ordinary')  # the ID is on another kind of item
        self.site.listing({'single': 'PM0100'}, 'variable', 'blind_box', unusual=True)
        self.site.listing_bad.add('DR0133-03')
        self.site.listing_fail.add('DR0133-04')
        self.on()
        self.run_pass()
        # popcore.ca lost Bear's row: its book_ref is unknown there.
        self.site.rows = {key: row for key, row in self.site.rows.items() if row.get('ref') != self.bear}
        # Twinkle gets a Whole Set in POPCORE, so the website must add an option to its simple listing.
        with closing(self.connect()) as con:
            con.execute("""INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, price, identity_status)
                           VALUES ('PM0300-SET', 'Twinkle 整端', 'Twinkle 整端', 'sealed_set', 'set', 101.94, 'verified')""")
            con.execute("UPDATE products SET boxes_per_dan=6 WHERE sku='PM0300'")
            con.commit()
        self.read({'DR0133': self.items['DR0133'], 'DR0133-SET': self.items['DR0133-SET'], 'PM0300': self.items['PM0300']})
        self.assertEqual(self.approve(all=True, count=8).status_code, 200)
        self.run_pass()
        codes = {one['popcore_id']: (one['state'], one['code']) for one in self.preview()['refused']}
        self.assertEqual(codes, {
            'PM0100': ('refused', 'options_unusual'), 'PM0300': ('refused', 'listing_not_variable'),
            'PM0200': ('refused', 'book_unknown'), 'DR0133-02': ('refused', 'id_in_use'),
            'DR0133-03': ('refused', 'item_malformed'), 'DR0133-04': ('error', 'listing_failed')})
        refused = {one['popcore_id']: one for one in self.preview()['refused']}
        self.assertIn('simple listing', refused['PM0300']['message'])
        self.assertEqual({self.row(sku)['state'] for sku in ('DR0133-01', 'DR0133-05')}, {'created'})
        self.assertEqual(self.preview()['counts']['refused'], 6)
        self.assertIsNone(self.row('PM0200')['website_product_id'])
        # Not sent again every pass.
        self.run_pass()
        self.run_pass()
        self.assertEqual(len(self.site.listing_calls), 1)
        # Sent again when what POPCORE sends changes: a new manager price for Zen Deer.
        with closing(self.connect()) as con:
            con.execute("UPDATE products SET price=32.99 WHERE sku='DR0133-02'")
            con.execute("""INSERT INTO design_prices(product_id, cents, previous_cents, source, set_by)
                           VALUES (?, 3299, 3099, 'design_prices', 'auth0|manager')""", (self.items['DR0133-02'],))
            con.commit()
        self.run_pass()
        self.assertEqual([item['popcore_id'] for item in self.site.listing_calls[-1]], ['DR0133-02'])
        self.assertEqual(self.site.listing_calls[-1][0]['price_cents'], 3299)
        # Or when a manager retries: one, then all.
        self.site.listing_fail.clear()
        retry = self.client.post('/api/dex/listings/retry', json={'product_id': self.items['DR0133-04']},
                                 headers=self.headers('manager'))
        self.assertEqual(retry.get_json(), {'retried': 1})
        self.run_pass()
        self.assertEqual(self.row('DR0133-04')['state'], 'created')
        self.assertEqual(self.client.post('/api/dex/listings/retry', json={'product_id': self.items['DR0133-04']},
                                          headers=self.headers('manager')).status_code, 409)
        self.site.listing_bad.clear()
        self.assertEqual(self.client.post('/api/dex/listings/retry', json={'all': True},
                                          headers=self.headers('manager')).get_json(), {'retried': 5})
        self.run_pass()
        self.assertEqual(self.row('DR0133-03')['state'], 'created')
        self.assertEqual(self.row('PM0200')['state'], 'refused')
        # Withdrawn: back in the preview.
        withdrawn = self.client.post('/api/dex/listings/withdraw', json={'product_id': self.items['PM0200']},
                                     headers=self.headers('manager'))
        self.assertEqual(withdrawn.get_json(), {'withdrawn': 1})
        self.assertIn('PM0200', self.by_id(self.preview()))
        self.assertEqual(self.client.post('/api/dex/listings/withdraw', json={'product_id': self.items['DR0133-03']},
                                          headers=self.headers('manager')).status_code, 409)

    def test_an_item_with_no_answer_stays_approved_and_goes_again(self):
        self.on()
        self.run_pass()
        self.site.listing_silent.add('DR0133-01')
        self.approve(product_ids=[self.items['DR0133-01'], self.items['DR0133-02']])
        self.run_pass()
        self.assertEqual((self.row('DR0133-01')['state'], self.row('DR0133-02')['state']), ('approved', 'created'))
        self.site.listing_silent.clear()
        self.run_pass()
        # The website made it the first time without saying so: sent again, it answers exists, never a second one.
        self.assertEqual(self.row('DR0133-01')['state'], 'exists')
        self.assertEqual(len(self.site.holders('DR0133-01')), 1)

    # Failed calls ------------------------------------------------------------------------------------------

    def test_a_failed_listings_call_marks_nothing_and_the_next_pass_tries_again(self):
        self.on()
        self.run_pass()
        self.approve(product_ids=[self.items['DR0133-01']])
        for broken, code in ((400, 'listings_malformed'), (503, 'dex_missing'), ('down', 'website_unavailable'),
                             ('missing', 'website_half_missing'), ('auth', 'website_auth')):
            if broken == 'down':
                self.site.down = True
            elif broken == 'missing':
                self.site.missing = True
            elif broken == 'auth':
                self.env.stop()
                self.env = patch.dict(os.environ, {**ENV, 'WEBSITE_KEY': 'ck_wrong'})
                self.env.start()
            else:
                self.site.listing_status = broken
            before = self.snapshot(['dex_listings'])
            self.run_pass()
            self.assertEqual(self.snapshot(['dex_listings']), before)
            error = self.preview()['last_error']
            self.assertEqual(error['code'], code, error)
            if broken == 503:
                self.assertIn('not active', error['message'])
            if broken == 'missing':
                self.assertIn('listings route', error['message'])
            self.site.down = self.site.missing = False
            self.site.listing_status = None
        self.env.stop()
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.run_pass()
        self.assertEqual(self.row('DR0133-01')['state'], 'created')
        self.assertIsNone(self.preview()['last_error'])
        # The book's own last error is the book's: a listings failure never shows there.
        status = self.client.get('/api/dex/sync', headers=self.headers('manager')).get_json()
        self.assertIsNone(status['last_error'])

    def test_a_status_popcore_cannot_read_marks_the_item_and_a_crash_is_kept_apart(self):
        self.on()
        self.run_pass()
        self.approve(product_ids=[self.items['DR0133-01']])
        original = self.site.make
        self.site.make = lambda item: {**original(item), 'status': 'maybe'}
        self.run_pass()
        self.assertEqual((self.row('DR0133-01')['state'], self.row('DR0133-01')['code']), ('error', 'website_answer'))
        with patch.object(dex_sync, 'send_listings', side_effect=RuntimeError('boom')), self.assertLogs('dex_sync', 'ERROR'):
            self.run_pass()
        self.assertEqual(self.preview()['last_error']['code'], 'dex_listings_failed')

    # Waiting for the book ----------------------------------------------------------------------------------

    def test_an_approved_item_waits_until_its_book_entry_is_on_the_website(self):
        self.site.refuse[self.designs[0]] = 'possible_duplicate'
        self.approve(product_ids=[self.items['DR0133-01'], self.items['DR0133-02']])
        self.on()
        self.run_pass()
        self.assertEqual([item['popcore_id'] for item in self.sent()], ['DR0133-02'])
        waiting = {one['popcore_id']: one for one in self.preview()['approved']}
        self.assertEqual((waiting['DR0133-01']['state'], waiting['DR0133-01']['waits']), ('approved', 'book'))
        self.assertIsNone(self.row('DR0133-01')['sent_at'])
        # Taken off the book: it is not in the book, and still waits.
        with closing(self.connect()) as con:
            dex_book.update(con, self.designs[0], {'product_id': None}, ACTOR)
        self.assertEqual(self.preview()['approved'][0]['waits'], 'not_in_book')
        self.run_pass()
        self.assertEqual(len(self.site.listing_calls), 1)

    # Approving -----------------------------------------------------------------------------------------------

    def test_approving_follows_the_list(self):
        self.assertEqual(self.client.post('/api/dex/listings/approve', json={'all': True, 'count': 8},
                                          headers=self.headers('staff')).status_code, 403)
        response = self.approve(all=True, count=9)
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'list_changed'))
        response = self.approve(product_ids=[self.items['PM0400']])  # not in the book
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'not_listable'))
        response = self.approve(product_ids=[self.items['DR0133']])  # it has its listing
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.approve(product_ids=['DR0133-01']).status_code, 400)
        self.assertEqual(self.approve().status_code, 400)
        self.assertEqual(self.approve(product_ids=[self.items['DR0133-01'], self.items['DR0133-01']]).get_json(), {'approved': 1})
        self.assertEqual(self.approve(product_ids=[self.items['DR0133-01']]).status_code, 409)  # approved already
        row = self.row('DR0133-01')
        self.assertEqual((row['state'], row['popcore_id'], row['approved_by']), ('approved', 'DR0133-01', 'auth0|manager'))
        self.assertEqual(self.client.post('/api/dex/listings/withdraw', json={'product_id': self.items['DR0133-01']},
                                          headers=self.headers('manager')).get_json(), {'withdrawn': 1})
        self.assertIsNone(self.row('DR0133-01'))
        # An item made earlier whose ID a newer catalogue read still lacks (its draft was deleted) shows again.
        self.on()
        self.run_pass()
        self.approve(product_ids=[self.items['DR0133-05']])
        self.run_pass()
        self.assertNotIn('DR0133-05', self.by_id(self.preview()))
        self.read({'DR0133': self.items['DR0133'], 'DR0133-SET': self.items['DR0133-SET']}, at='2099-01-01T00:00:00')
        again = self.by_id(self.preview())['DR0133-05']
        self.assertTrue(again['again'])
        self.assertEqual(self.approve(product_ids=[self.items['DR0133-05']]).status_code, 200)
        self.assertEqual(self.row('DR0133-05')['state'], 'approved')

    def test_an_item_removed_from_popcore_leaves_the_list(self):
        self.approve(product_ids=[self.items['PM0200']])
        with closing(self.connect()) as con:
            con.execute('PRAGMA foreign_keys=OFF')
            con.execute('DELETE FROM products WHERE id=?', (self.items['PM0200'],))
            con.commit()
        self.assertIsNone(self.row('PM0200'))

    def test_the_migration_runs_twice(self):
        import db
        with closing(self.connect()) as con:
            con.execute("DELETE FROM _migrations WHERE name='dex_listings'")
            con.commit()
        db.migrate_db()
        db.migrate_db()
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM _migrations WHERE name='dex_listings'").fetchone()[0], 1)


def db_path():
    import db
    return db.DB_PATH
