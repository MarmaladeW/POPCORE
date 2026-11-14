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
        return Response(404, {'message': 'No route'})

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
        product = self.add('product', 'Figures', series, blind_box=True, product_id=self.items['PM0100'] if link else None)
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
        self.assertNotIn('price_history', shapes[product]['extras'])
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
