"""A raw daily report needs no hand steps (owner, 2026-11-13). The DT and MK reports of Nov 11–12 were
prepared by hand before they could be pasted; the paste now does that work itself:

- 入库 puts new goods into upstairs (DT) or the warehouse (MK): "S*N" is N whole sets of S, and the first
  whole sets of a blind box give it its sealed set as the count card does; a bare "*N" is boxes or pieces;
  "Series-Design*N" is design pieces.
- 出库 comes off this store's upstairs or warehouse and goes onto the other store at once, whole sets as
  whole sets, into its upstairs or warehouse, or onto its floor when the manager ticks that.
- A line marked 预订单 is a special order: no sale, no stock, listed as left out.
- 出店入库 is floor → upstairs, 出display入店 is display → floor (a written set size opens the display's whole
  set as boxes) and 出店display is off display and out of the store.
- A day's lines post arrivals first, so 拆display of a box that the same report's 入店 brought is never short.
- 出店 "X：12*1" is 12, and a design written as a note is offered as the design.

The four raw texts are in fixtures/daily_reports as staff sent them and are replayed, in the order they were
pasted on 2026-11-13, on a catalogue made of the names they use with both stores live, as a manager would:
accept every match, pick the product for a mistyped name, leave out what matches nothing."""
import io
import sys
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from support import APP_DIR, IsolatedApiCase, db

ROOT = APP_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from blueprints import sales  # noqa: E402
from checkout_access import business_date  # noqa: E402
from blueprints.sales import _REPORT_NOTE_FIELDS, _detect_section_type, _note_design, _parse_token  # noqa: E402
from inventory_commands import post_inventory  # noqa: E402
from inventory_opening import reconcile_inventory  # noqa: E402
from matcher import clean_name, normalize  # noqa: E402
from scripts.inventory_opening import main as _opening_cli  # noqa: E402

REPORTS = Path(__file__).resolve().parent / 'fixtures' / 'daily_reports'
LIVE_DATE = '2026-11-01'
PACKED = {'refill', 'stock_in', 'back_in', 'transfer_out', 'floor_to_back', 'display_to_floor'}
STOCK = PACKED | {'stock_out', 'display_sold', 'break_display', 'display_stock_in', 'claw_prize'}
RAW = (('DT', '2026-11-11'), ('MK', '2026-11-11'), ('DT', '2026-11-12'), ('MK', '2026-11-12'))
ADMIN = {'sub': 'auth0|admin', 'https://popcore/role': 'admin'}

# Names staff mistyped, and the product the manager picks for each.
TYPOS = {'hellokitty苹果趴趴': 'hellokitty苹果贴贴', 'sa lookong back': 'sa looking back'}
# The owner, 2026-11-13: "forget about the 娃衣". It matches nothing and is left out.
UNMATCHED = {'娃衣 黑金lv套装'}
# Lines written "Series-Design": pieces of these designs, not products of their own.
DESIGNS = {'SA shu': ('Shu-Shu Monkey', 'Solar Tanuki', 'Zen Deer'), 'labubu喵喵毛绒': ('Midnight Kitty',),
           '小野哈利波特figure': ('Harry Potter - Special Edition (Big Secret)',)}
DESIGN_LINES = {'SA shu-shushu monkey', 'SA shu-solar Tanuki', 'SA shu-Zen Deer', 'Labubu喵喵毛绒-Midnight Kitty',
                '小野哈利波特figure-Harry Potter-Special Edition'}
# Blind boxes whose whole set POPCORE does not have yet: 入库 gives them one.
FIRST_SETS = {'hellokitty苹果贴贴', '萌粒 三丽鸥炸虾'}
# A manager added it the evening of the report (PM0768); nobody has counted it anywhere yet.
NEW_PRODUCT = '萌粒 三丽鸥炸虾'
# What the stores really held before these reports, where the hand work of 2026-11-13 depended on it:
# (store, place, name, whole set?) → quantity. Everything else gets the same generous stock.
HELD = {
    ('DT', 'upstairs', 'SA shu', False): 153, ('DT', 'upstairs', 'SA shu', True): 5, ('MK', 'warehouse', 'SA shu', True): 22,
    ('DT', 'floor', '小野哈利波特figure', False): 0, ('DT', 'upstairs', '小野哈利波特figure', True): 0,
    ('DT', 'upstairs', '诡乐园', False): 0,
    ('DT', 'upstairs', 'labubu喵喵毛绒', True): 7, ('MK', 'warehouse', 'labubu喵喵毛绒', True): 0,
    ('DT', 'upstairs', '宝可梦趴趴', True): 0, ('MK', 'warehouse', '宝可梦趴趴', True): 1,
    ('DT', 'display', 'smiski exercising', True): 1,  # DT's display kept a whole set
}


def key_of(name):
    return normalize(clean_name(name))


def report_text(store, day):
    return (REPORTS / f'{store}-{day}.txt').read_text(encoding='utf-8')


class ReportSelfSufficientTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.stores = {row['code']: row['id'] for row in con.execute("SELECT id, code FROM stores WHERE code IN ('DT', 'MK')")}
            self.at = {(row['store'], row['code']): row['id'] for row in con.execute(
                """SELECT s.code AS store, l.code, l.id FROM inventory_locations l JOIN stores s ON s.id=l.store_id
                   WHERE l.is_active=1""")}
            for subject in ('auth0|manager', 'auth0|admin'):
                for store_id in self.stores.values():
                    con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)', (subject, store_id))
            # A manager of DT alone.
            con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)', ('auth0|dt-only', self.stores['DT']))
            # The paste has moved these sections by itself since before the reports replayed here.
            self.went_live = con.execute("SELECT value FROM app_settings WHERE key='report_lines_from'").fetchone()[0]
            con.execute("UPDATE app_settings SET value=? WHERE key='report_lines_from'", (LIVE_DATE,))
            con.commit()
        self.back = {'DT': 'upstairs', 'MK': 'warehouse'}

    # ── fixture catalogue ────────────────────────────────────────────────────

    def build_catalogue(self):
        """One verified blind box per name the four reports use, a whole set where a line moves whole sets, the
        designs they name, stock at every location of both stores, and then both stores go live."""
        names = {}
        for store, day in RAW:
            # Read as the live stores they are about to be, before any product exists.
            with patch.object(sales, '_is_authoritative', return_value=True), \
                    patch.object(sales, 'other_store', return_value={'code': 'other'}):
                parsed = self.parse(report_text(store, day), store)
            for item in parsed['failed']:
                if item['raw_name'] in TYPOS or item['raw_name'] in UNMATCHED or item['raw_name'] in DESIGN_LINES:
                    continue
                entry = names.setdefault(key_of(item['raw_name']), {'name': item['raw_name'], 'set': None})
                moves_sets = item['section'] in ('refill', 'back_in', 'transfer_out', 'display_to_floor')
                if moves_sets and (item.get('box_size') or 1) > 1 and not item.get('to_display'):
                    entry['set'] = item['box_size']
        self.products, self.designs = {}, {}
        with closing(self.connect()) as con:
            for number, (key, entry) in enumerate(sorted(names.items()), 1):
                sku = f'RP{number:04d}'
                if key == key_of(NEW_PRODUCT):
                    self.products[key] = con.execute(
                        "INSERT INTO products(sku, name_cn_en, jizhanming, product_type) VALUES ('PM0768', ?, ?, '盲盒')",
                        (entry['name'], entry['name'])).lastrowid
                    continue
                box = con.execute(
                    """INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, identity_status)
                       VALUES (?, ?, ?, 'random_box', 'box', 'verified')""", (sku, entry['name'], entry['name'])).lastrowid
                self.products[key] = box
                counts = [(code, box, 'random_box', {'floor': 100, 'display': 5, 'claw': 5}.get(code, 30))
                          for code in ('floor', 'upstairs', 'warehouse', 'display', 'claw')]
                if entry['set'] and key not in {key_of(name) for name in FIRST_SETS}:
                    whole = con.execute(
                        """INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, identity_status)
                           VALUES (?, ?, ?, 'sealed_set', 'set', 'verified')""",
                        (f'{sku}-SET', f"{entry['name']} 整端", f"{entry['name']} 整端")).lastrowid
                    con.execute("""INSERT INTO product_conversions(source_product_id, target_product_id, output_per_input, version)
                                   VALUES (?, ?, ?, 1)""", (whole, box, entry['set']))
                    self.products[key + ':set'] = whole
                    counts += [(code, whole, 'sealed_set', 10) for code in ('upstairs', 'warehouse')]
                    counts.append(('display', whole, 'sealed_set', None))
                held = {(store, code, whole): quantity for (store, code, name, whole), quantity in HELD.items()
                        if key_of(name) == key}
                for store in self.stores:
                    for code, product, form, quantity in counts:
                        quantity = held.get((store, code, form == 'sealed_set'), quantity)
                        if (store, code) not in self.at or quantity is None:
                            continue
                        con.execute("""INSERT INTO opening_counts(location_id, product_id, stock_form, quantity, counted_by)
                                       VALUES (?, ?, ?, ?, 'auth0|admin')""", (self.at[store, code], product, form, quantity))
            for head, designs in DESIGNS.items():
                head_id = self.products[key_of(head)]
                series = con.execute('INSERT INTO product_series(name, product_id) VALUES (?, ?)', (head, head_id)).lastrowid
                con.execute('UPDATE products SET series_id=? WHERE id=?', (series, head_id))
                for number, design in enumerate(designs, 1):
                    self.designs[design] = con.execute(
                        """INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, identity_status, series_id,
                                                design_name)
                           VALUES (?, ?, ?, 'confirmed_design', 'piece', 'verified', ?, ?)""",
                        (f'D{series}-{number:02d}', f'{head} {design}', f'{head} {design}', series, design)).lastrowid
            con.commit()
        for store in self.stores:
            with redirect_stdout(io.StringIO()):
                _opening_cli(['intake', '--db', db.DB_PATH, '--from-app', '--store', store, '--business-date', LIVE_DATE,
                              '--actor-sub', 'auth0|admin', '--apply',
                              '--backup', str(Path(self.tempdir.name) / f'before-{store}.db')])
        # DT's upstairs design pieces, as counted before Nov 11.
        for design, quantity in (('Shu-Shu Monkey', 2), ('Solar Tanuki', 9), ('Zen Deer', 6), ('Midnight Kitty', 2)):
            self.receive(self.designs[design], 'DT', 'upstairs', quantity, 'piece')

    def receive(self, product, store, code, quantity, unit='box'):
        with closing(self.connect()) as con:
            post_inventory(con, {'kind': 'receipt', 'business_date': LIVE_DATE, 'lines': [
                {'product_id': product, 'quantity': quantity, 'unit': unit, 'to_location_id': self.at[store, code],
                 'to_disposition': 'saleable', 'expected_versions': {'to': self.version(product, store, code)}}]},
                actor=ADMIN, request_key=f'fixture:{product}:{store}:{code}:{quantity}')

    def product(self, name, whole=False):
        return self.products[key_of(name) + (':set' if whole else '')]

    def whole_set_of(self, name):
        with closing(self.connect()) as con:
            row = con.execute("""SELECT p.id, p.sku, c.output_per_input FROM product_conversions c
                                 JOIN products p ON p.id=c.source_product_id WHERE c.target_product_id=?""",
                              (self.product(name),)).fetchone()
            return tuple(row) if row else None

    def balance(self, product, store, code):
        with closing(self.connect()) as con:
            row = con.execute("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                                 AND disposition='saleable'""", (product, self.at[store, code])).fetchone()
            return row[0] if row else 0

    def version(self, product, store, code):
        with closing(self.connect()) as con:
            row = con.execute("""SELECT version FROM inventory_balances WHERE product_id=? AND location_id=?
                                 AND disposition='saleable'""", (product, self.at[store, code])).fetchone()
            return row[0] if row else 0

    def balances(self):
        with closing(self.connect()) as con:
            return {tuple(row[:3]): row[3] for row in con.execute(
                'SELECT product_id, location_id, disposition, quantity FROM inventory_balances')}

    # ── the manager's paste ──────────────────────────────────────────────────

    def parse(self, text, store, role='manager'):
        response = self.client.post('/api/sales/parse_report', json={'text': text, 'store_code': store},
                                    headers=self.headers(role))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    @staticmethod
    def payload_item(item, product_id, bucket, to_floor=False):
        base = {'product_id': product_id, 'section': item['section'], 'notes': item.get('note') or '',
                'raw_name': item['raw_name'], 'source_bucket': bucket}
        if item['section'] == 'cash':
            base['qty_cash'] = item['qty']
        elif item['section'] in PACKED:
            base.update(box_size=item['box_size'], num_boxes=item['qty'], loose_qty=item.get('loose_qty') or 0,
                        to_display=bool(item.get('to_display')), to_floor=to_floor)
        elif item['section'] in STOCK:
            base.update(qty=item['qty'], from_display=bool(item.get('from_display')))
        else:
            base['qty_pos'] = item['qty']
        return base

    def body(self, parsed, keep=lambda item: True, to_floor=()):
        """What the manager sends: every match accepted, a mistyped name given its product, the rest left out.
        `to_floor` names the 出库 lines ticked 到对方楼下."""
        floor = lambda item: item['section'] == 'transfer_out' and item['raw_name'] in to_floor
        items = [self.payload_item(item, item['product']['id'], 'confirmed', floor(item))
                 for item in parsed['confirmed'] if keep(item)]
        items += [self.payload_item(item, item['product']['id'], 'review', floor(item))
                  for item in parsed['review'] if keep(item)]
        items += [self.payload_item(item, self.product(TYPOS[item['raw_name']]), 'failed')
                  for item in parsed['failed'] if item['raw_name'] in TYPOS and keep(item)]
        return {'date': parsed['detected_date'], 'store_code': parsed['store'], 'mode': 'replace',
                'classification': 'summary_only', 'stock_mode': parsed['stock_mode'], 'items': items,
                'report_metadata': {'cash_actual': parsed['cash_total_reported'],
                                    'cash_expected': parsed['cash_expected_reported'],
                                    **{key: parsed.get(key, []) for key in _REPORT_NOTE_FIELDS}}}

    def submit(self, body, role='manager', **extra):
        return self.client.post('/api/sales/submit_daily_report', json={**body, **extra}, headers=self.headers(role))

    def confirm_and_save(self, body, role='manager'):
        preview = self.submit(body, role, dry_run=True)
        self.assertEqual(preview.status_code, 200, preview.get_json())
        saved = self.submit(body, role, stock_digest=preview.get_json()['stock_digest'])
        self.assertEqual(saved.status_code, 200, saved.get_json())
        return preview.get_json(), saved.get_json()

    @staticmethod
    def rows(parsed, section=None):
        return [item for item in parsed['confirmed'] + parsed['review'] + parsed['failed']
                if section is None or item['section'] == section]

    @staticmethod
    def sections(parsed):
        count = {}
        for item in parsed['confirmed'] + parsed['review'] + parsed['failed']:
            if item['section'] in STOCK:
                count[item['section']] = count.get(item['section'], 0) + 1
        return count

    @staticmethod
    def line(stock, section, raw):
        """The preview's line of a section whose text starts as the report wrote it."""
        found = [line for line in stock['lines'] if line['section'] == section and line['raw'].startswith(raw)]
        assert len(found) == 1, (section, raw, [line['raw'] for line in stock['lines'] if line['section'] == section])
        return found[0]

    def moved(self, line):
        """(from store or None, from place, taken, to store or None, to place, added) of a stock line."""
        source, target = line.get('from') or {}, line.get('to') or {}
        return (source.get('store'), source.get('location'), source.get('before', 0) - source.get('after', 0),
                target.get('store'), target.get('location'), target.get('after', 0) - target.get('before', 0))

    def paste(self, store, day, to_floor=()):
        parsed = self.parse(report_text(store, day), store)
        self.assertEqual((parsed['detected_date'], parsed['store'], parsed['stock_mode']), (day, store, 'live'))
        self.assertEqual((parsed['unknown_sections'], parsed['metadata_errors']), ([], []), (store, day))
        self.assertFalse([item['raw_name'] for item in self.rows(parsed) if item['flagged']], (store, day))
        preview, saved = self.confirm_and_save(self.body(parsed, to_floor=to_floor))
        self.assertEqual([(line['line_key'], line['moving'], line['short']) for line in saved['stock']['lines']],
                         [(line['line_key'], line['moving'], line['short']) for line in preview['stock']['lines']])
        with closing(self.connect()) as con:
            self.assertTrue(reconcile_inventory(con)['ok'])
        return parsed, saved['stock']

    # ── the four raw reports ─────────────────────────────────────────────────

    def test_the_four_raw_reports_move_what_was_done_by_hand(self):
        self.build_catalogue()
        figure, shu = self.product('小野哈利波特figure'), self.product('SA shu')
        kitty, special = self.designs['Midnight Kitty'], self.designs['Harry Potter - Special Edition (Big Secret)']
        self.assertIsNone(self.whole_set_of('hellokitty苹果贴贴'))

        # ── DT, Nov 11 ───────────────────────────────────────────────────────
        text = report_text('DT', '2026-11-11')
        parsed = self.parse(text, 'DT')
        self.assertEqual(self.sections(parsed), {
            'claw_prize': 2, 'refill': 10, 'stock_in': 4, 'stock_out': 2, 'back_in': 6, 'transfer_out': 1,
            'break_display': 2, 'floor_to_back': 1})
        # 入库 and 出库 are stock lines now: nothing waits in Failed and nothing is only a note.
        self.assertEqual((parsed['failed'], parsed['unknown_sections'], parsed['store_transfers']), ([], [], []))
        written = {item['raw_name']: item for item in self.rows(parsed, 'back_in')}
        self.assertEqual({name: (item['box_size'], item['qty']) for name, item in written.items()}, {
            'hellokitty苹果贴贴': (12, 8), 'SA shu': (6, 43), '小野哈利波特figure': (12, 11),
            'SA shu-shushu monkey': (1, 2), 'SA shu-solar Tanuki': (1, 4), 'SA shu-Zen Deer': (1, 4)})
        # "Series-Design*N" is pieces of that design.
        self.assertEqual({name: item['product']['id'] for name, item in written.items() if '-' in name}, {
            'SA shu-shushu monkey': self.designs['Shu-Shu Monkey'], 'SA shu-solar Tanuki': self.designs['Solar Tanuki'],
            'SA shu-Zen Deer': self.designs['Zen Deer']})
        (inline,) = self.rows(parsed, 'floor_to_back')
        self.assertEqual((inline['raw_name'], inline['qty'], inline['product']['id']),
                         ('小野哈利波特figure-Harry Potter-Special Edition', 1, special))
        # The first whole sets of a blind box are said before they are added.
        new_set = [check for check in parsed['format_checks'] if '还没有整端' in check['problem']]
        self.assertEqual([(check['level'], check['section'], check['text']) for check in new_set],
                         [('check', '入库', ['hellokitty苹果贴贴12*8'])])

        body = self.body(parsed, to_floor={'SA shu'})  # the owner said these 30 went onto MK's floor
        before = self.balances()
        preview = self.submit(body, dry_run=True)
        self.assertEqual(preview.status_code, 200, preview.get_json())
        stock = preview.get_json()['stock']
        # A preview writes nothing: no stock, no sales, and the whole set it will add does not exist yet.
        self.assertEqual(self.balances(), before)
        self.assertIsNone(self.whole_set_of('hellokitty苹果贴贴'))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM daily_sales').fetchone()[0], 0)
        saved = self.submit(body, stock_digest=preview.get_json()['stock_digest'])
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual([(line['line_key'], line['moving'], line['short']) for line in saved.get_json()['stock']['lines']],
                         [(line['line_key'], line['moving'], line['short']) for line in stock['lines']])

        # 入库: whole sets into upstairs, the Hellokitty set created with 12 per set, design pieces as pieces.
        set_id, set_sku, per = self.whole_set_of('hellokitty苹果贴贴')
        self.assertEqual((set_sku, per, self.balance(set_id, 'DT', 'upstairs')),
                         (f"{self.sku(self.product('hellokitty苹果贴贴'))}-SET", 12, 8))
        self.assertEqual(self.moved(self.line(stock, 'back_in', 'hellokitty苹果贴贴')), (None, None, 0, None, 'upstairs', 8))
        # The same numbers as the counts entered by hand on 2026-11-13: SA shu sets 5 → 48, figure sets 0 → 11.
        self.assertEqual(self.balance(self.product('SA shu', whole=True), 'DT', 'upstairs'), 5 + 43)
        self.assertEqual(self.balance(self.product('小野哈利波特figure', whole=True), 'DT', 'upstairs'), 0 + 11)
        self.assertEqual([self.balance(self.designs[name], 'DT', 'upstairs')
                          for name in ('Shu-Shu Monkey', 'Solar Tanuki', 'Zen Deer')], [2 + 2, 9 + 4, 6 + 4])
        # 入店 "hello Kitty苹果贴贴12*3" the same day is 36 boxes of that set onto the floor.
        self.assertEqual(self.moved(self.line(stock, 'stock_in', 'hello Kitty苹果贴贴')), (None, None, 0, None, 'floor', 36))
        # 出库 "SA shu*30": 30 boxes off DT's upstairs and onto MK at once (its floor, as ticked). 出库入店
        # "sa shu*14" took 14 more boxes down the same day.
        self.assertEqual(self.moved(self.line(stock, 'transfer_out', 'SA shu')), (None, 'upstairs', 30, 'MK', 'floor', 30))
        self.assertEqual((self.balance(shu, 'DT', 'upstairs'), self.balance(shu, 'MK', 'floor'),
                          self.balance(shu, 'MK', 'warehouse')), (153 - 14 - 30, 100 + 30, 30))
        # 拆display "小野哈利波特figure*1": DT's floor had none until this report's own 入店 12*4 arrived.
        broke = self.line(stock, 'break_display', '小野哈利波特figure')
        self.assertEqual((broke['moving'], broke['short']), (1, 0))
        self.assertEqual(self.balance(figure, 'DT', 'display'), 5 + 1)
        self.assertEqual(stock['moves_short'], [{'product_id': special, 'sku': self.sku(special), 'reason': 'move_short',
                                                 'name': '小野哈利波特figure Harry Potter - Special Edition (Big Secret)',
                                                 'quantity': 1, 'where': ['floor']}])
        # 出店入库 is floor → upstairs. DT's floor had no such piece (the owner: it came from elsewhere), so
        # the line waits as a notice instead of taking anything.
        up = self.line(stock, 'floor_to_back', '小野哈利波特figure-Harry Potter-Special Edition')
        self.assertEqual((up['from']['location'], up['to']['location'], up['moving'], up['short']), ('floor', 'upstairs', 0, 1))
        # Every other line moved in full, and the 124 units sold came off the floor (on 2026-11-13 the real
        # floor took 121 and lacked 3: one never counted, two Angry Molly).
        self.assertEqual([line['raw'] for line in stock['lines'] if line['short']], [up['raw']])
        self.assertEqual((stock['taken'], stock['short']), (124, []))
        with closing(self.connect()) as con:
            self.assertTrue(reconcile_inventory(con)['ok'])

        # ── MK, Nov 11 ───────────────────────────────────────────────────────
        parsed, stock = self.paste('MK', '2026-11-11')
        # The four pre-orders are left out, by line, as written.
        self.assertEqual([(entry['line'], entry['reason']) for entry in parsed['left_out']],
                         [(8, 'pre_order'), (9, 'pre_order'), (10, 'pre_order'), (11, 'pre_order')])
        self.assertEqual(parsed['left_out'][0]['text'], '火影忍者三代*1（ITACHI）（预订单）')
        self.assertFalse([item for item in self.rows(parsed) if '火影' in item['raw_name']])
        # "sa lookong back" is offered as SA looking back for the manager to accept.
        (typo,) = [item for item in parsed['review'] if item['raw_name'] == 'sa lookong back']
        self.assertEqual(typo['product']['id'], self.product('sa looking back'))
        self.assertEqual((self.sections(parsed), stock['lines'], stock['taken'], stock['short']), ({}, [], 69, []))

        # ── DT, Nov 12 ───────────────────────────────────────────────────────
        parsed = self.parse(report_text('DT', '2026-11-12'), 'DT')
        # 入display "诡乐园*1" is a new piece onto display (owner, 2026-11-22), read as 入店 to display.
        self.assertEqual(self.sections(parsed), {'stock_in': 5, 'display_to_floor': 1, 'refill': 9, 'transfer_out': 3})
        self.assertEqual({item['raw_name'] for item in parsed['failed']}, set(TYPOS) & {'hellokitty苹果趴趴'} | UNMATCHED)
        # "labubu喵喵毛绒*1（midnight kitty）" offers the design first, the series' box second.
        (noted,) = [item for item in parsed['review'] if item.get('note') == 'midnight kitty']
        self.assertEqual((noted['section'], noted['product']['id']), ('cash', kitty))
        self.assertIn(self.product('labubu喵喵毛绒'), [candidate['id'] for candidate in noted['candidates']])
        self.assertEqual({item['raw_name']: (item['box_size'], item['qty']) for item in self.rows(parsed, 'transfer_out')},
                         {'etb-pe': (1, 2), 'pf-w': (1, 2), 'labubu喵喵毛绒': (9, 3)})
        parsed, stock = self.paste('DT', '2026-11-12')
        # "出display 入店：smiski exercising 12*1" opens the display's whole set as 12 boxes on the floor.
        opened = self.line(stock, 'display_to_floor', 'smiski exercising')
        self.assertEqual(self.moved(opened), (None, 'display', 1, None, 'floor', 12))
        self.assertEqual(self.balance(self.product('smiski exercising', whole=True), 'DT', 'display'), 0)
        # 出库 with no tick lands in MK's warehouse: pieces as pieces, whole sets as whole sets.
        for name, product, quantity in (('etb-pe', self.product('etb-pe'), 2), ('pf-w', self.product('pf-w'), 2),
                                        ('labubu喵喵毛绒', self.product('labubu喵喵毛绒', whole=True), 3)):
            sent = self.line(stock, 'transfer_out', name)
            self.assertEqual((sent['from']['product_id'], self.moved(sent)),
                             (product, (None, 'upstairs', quantity, 'MK', 'warehouse', quantity)), name)
        self.assertEqual((self.balance(self.product('labubu喵喵毛绒', whole=True), 'MK', 'warehouse'),
                          self.balance(self.product('labubu喵喵毛绒', whole=True), 'DT', 'upstairs')), (3, 7 - 3))
        # The Midnight Kitty sale took the piece 出库入店 brought down on Nov 11.
        self.assertEqual((self.balance(kitty, 'DT', 'floor'), self.balance(kitty, 'DT', 'upstairs')), (0, 1))
        # 入display "诡乐园*1" puts a new piece on DT's display (it once looked for it upstairs, which had none).
        self.assertEqual([(line['section'], line['short']) for line in stock['lines'] if line['short']], [])
        self.assertEqual(self.moved(self.line(stock, 'stock_in', '诡乐园 1（display）')), (None, None, 0, None, 'display', 1))
        self.assertEqual(stock['short'], [])

        # ── MK, Nov 12 ───────────────────────────────────────────────────────
        shrimp = self.product(NEW_PRODUCT)
        parsed = self.parse(report_text('MK', '2026-11-12'), 'MK')
        self.assertEqual(self.sections(parsed), {'back_in': 2, 'transfer_out': 1, 'refill': 4, 'stock_out': 6, 'stock_in': 4})
        self.assertEqual((parsed['failed'], parsed['display_stock_out'], parsed['store_transfers']), ([], [], []))
        # 出店 "X：12*1" is 12, and still matches its product.
        out = {item['raw_name']: (item['qty'], bool(item.get('from_display')), item['product']['id'])
               for item in self.rows(parsed, 'stock_out') if not item.get('from_display')}
        self.assertEqual(out, {name: (quantity, False, self.product(name)) for name, quantity in (
            ('诡乐园', 5), ('宝可梦趴趴', 3), ('Smiski exercising', 12), ('SA animal ver2', 12), ('Molly100% S2', 9))})
        # 出店display is 出店 from display.
        self.assertEqual([(item['raw_name'], item['qty']) for item in self.rows(parsed, 'stock_out') if item.get('from_display')],
                         [('诡乐园', 1)])
        # DT's 出库 of Nov 11 already put 30 SA shu at MK, so MK's own SA shu arrivals (入库 6*21, 入店 *36) are
        # pointed out; they were other goods (the owner, 2026-11-13) and stay on the report.
        self.assertEqual(sorted(check['lines'] for check in parsed['format_checks'] if '不然会加两次' in check['problem']),
                         [[51], [74]])
        parsed, stock = self.paste('MK', '2026-11-12')
        # 入库 into MK's warehouse; the new product's first whole sets give it its set (15 per set) and make it
        # a counted blind box, so the same report's 入店 15*4 and its 7 sales move too.
        self.assertEqual(self.balance(self.product('SA shu', whole=True), 'MK', 'warehouse'), 22 + 21)
        set_id, set_sku, per = self.whole_set_of(NEW_PRODUCT)
        self.assertEqual((set_sku, per, self.balance(set_id, 'MK', 'warehouse')), ('PM0768-SET', 15, 9))
        with closing(self.connect()) as con:
            self.assertEqual(tuple(con.execute('SELECT stock_form, stock_unit, identity_status FROM products WHERE id=?',
                                               (shrimp,)).fetchone()), ('random_box', 'box', 'verified'))
        self.assertEqual(self.balance(shrimp, 'MK', 'floor'), 15 * 4 - 7)
        # 出库 "宝可梦趴趴：8*1" is one whole set from MK's warehouse onto DT's upstairs.
        sent = self.line(stock, 'transfer_out', '宝可梦趴趴')
        self.assertEqual((sent['from']['product_id'], self.moved(sent)),
                         (self.product('宝可梦趴趴', whole=True), (None, 'warehouse', 1, 'DT', 'upstairs', 1)))
        self.assertEqual((self.balance(self.product('宝可梦趴趴', whole=True), 'DT', 'upstairs'),
                          self.balance(self.product('宝可梦趴趴', whole=True), 'MK', 'warehouse')), (1, 0))
        # 出店display takes MK's display piece; the 出店 lines take 12, 12 and 9 boxes off the floor.
        self.assertEqual(self.balance(self.product('诡乐园'), 'MK', 'display'), 5 - 1)
        gone = {line['raw'].split('（')[0].split(' ')[0]: line['moving'] for line in stock['lines']
                if line['section'] == 'stock_out' and line['from']['location'] == 'floor'}
        self.assertEqual(gone, {'诡乐园': 5, '宝可梦趴趴': 3, 'Smiski': 12, 'SA': 12, 'Molly100%': 9})
        self.assertEqual(([line['raw'] for line in stock['lines'] if line['short']], stock['short']), ([], []))

    def sku(self, product):
        with closing(self.connect()) as con:
            return con.execute('SELECT sku FROM products WHERE id=?', (product,)).fetchone()[0]

    # ── a re-pasted day ──────────────────────────────────────────────────────

    def test_a_repasted_day_moves_only_the_difference_of_入库_and_出库(self):
        self.build_catalogue()
        # The floor has the piece 出店入库 takes up, so no line of this day is left short.
        self.receive(self.designs['Harry Potter - Special Edition (Big Secret)'], 'DT', 'floor', 1, 'piece')
        text = report_text('DT', '2026-11-11')
        self.confirm_and_save(self.body(self.parse(text, 'DT')))
        shu, whole = self.product('SA shu'), self.product('SA shu', whole=True)
        after_first = self.balances()
        self.assertEqual((self.balance(whole, 'DT', 'upstairs'), self.balance(shu, 'MK', 'warehouse'),
                          self.balance(shu, 'DT', 'upstairs')), (48, 60, 109))

        # The same report again moves nothing and asks nothing; the set it added is not added twice.
        again = self.submit(self.body(self.parse(text, 'DT')), dry_run=True).get_json()
        self.assertFalse(again['needs_confirmation'])
        self.assertEqual(len(again['stock']['lines']), 28)
        self.assertTrue(all(line['moving'] == 0 and not line['short'] for line in again['stock']['lines']))
        self.assertEqual(self.submit(self.body(self.parse(text, 'DT'))).status_code, 200)
        self.assertEqual(self.balances(), after_first)

        # A fix: 43 sets were 40, and the 出库 never happened.
        fixed = self.parse(text.replace('SA shu 6*43', 'SA shu 6*40').replace('出库：\nSA shu*30\n', ''), 'DT')
        self.assertEqual(self.sections(fixed).get('transfer_out'), None)
        body = self.body(fixed)
        refused = self.submit(body)
        self.assertEqual(refused.get_json()['code'], 'stock_confirmation_required')
        self.assertEqual(self.balances(), after_first)
        preview, saved = self.confirm_and_save(body)
        changes = {(line['section'], line['moving']) for line in preview['stock']['lines'] if line['moving']}
        self.assertEqual(changes, {('back_in', -3), ('transfer_out', -30)})
        back = next(line for line in preview['stock']['lines'] if line['section'] == 'transfer_out')
        self.assertEqual((back['to']['store'], back['to']['location'], back['to']['before'], back['to']['after'],
                          back['from']['before'], back['from']['after']), ('MK', 'warehouse', 60, 30, 109, 139))
        self.assertEqual((self.balance(whole, 'DT', 'upstairs'), self.balance(shu, 'MK', 'warehouse'),
                          self.balance(shu, 'DT', 'upstairs')), (45, 30, 139))
        with closing(self.connect()) as con:
            reasons = [row[0] for row in con.execute(
                "SELECT reason FROM inventory_documents WHERE source_type='daily_report' AND reason LIKE '日报更正%'")]
            self.assertTrue(any('出库' in reason and 'SA shu' in reason for reason in reasons), reasons)
            self.assertTrue(reconcile_inventory(con)['ok'])

    # ── 出库 ─────────────────────────────────────────────────────────────────

    def test_出库_lands_in_the_other_stores_warehouse_unless_ticked_and_needs_only_this_stores_manager(self):
        self.build_catalogue()
        shu, whole = self.product('SA shu'), self.product('SA shu', whole=True)
        text = '2026.11.12 DT\n出库：\nSA shu*30\nSA shu 6*2'
        parsed = self.parse(text, 'DT', role='manager:dt-only')
        self.assertEqual([(item['box_size'], item['qty']) for item in self.rows(parsed, 'transfer_out')], [(1, 30), (6, 2)])
        # A bare *N of a blind box with a whole set is read as boxes, and the check says so.
        (loose,) = [check for check in parsed['format_checks'] if check['lines'] == [3]]
        self.assertEqual((loose['level'], loose['section']), ('check', '出库'))
        self.assertIn('30 个散盒送去另一家店', loose['problem'])
        # A manager of DT alone sends it: the paste is DT's, and the owner's rule puts it onto MK at once.
        preview, saved = self.confirm_and_save(self.body(parsed), role='manager:dt-only')
        self.assertEqual((self.balance(shu, 'DT', 'upstairs'), self.balance(shu, 'MK', 'warehouse')), (153 - 30, 30 + 30))
        self.assertEqual((self.balance(whole, 'DT', 'upstairs'), self.balance(whole, 'MK', 'warehouse')), (5 - 2, 22 + 2))
        self.assertEqual({(line['to']['store'], line['to']['location']) for line in saved['stock']['lines']}, {('MK', 'warehouse')})

        # Ticked 到对方楼下: boxes onto MK's floor, and a whole set arrives there as its boxes (a floor holds boxes).
        preview, saved = self.confirm_and_save(self.body(parsed, to_floor={'SA shu'}), role='manager:dt-only')
        self.assertEqual((self.balance(shu, 'MK', 'warehouse'), self.balance(whole, 'MK', 'warehouse')), (30, 22))
        self.assertEqual(self.balance(shu, 'MK', 'floor'), 100 + 30 + 2 * 6)
        self.assertEqual((self.balance(shu, 'DT', 'upstairs'), self.balance(whole, 'DT', 'upstairs')), (123, 3))
        # More than upstairs has: what it has moves, the rest waits as a notice at DT.
        card = self.product('便利店吊卡')
        more = self.parse('2026.11.12 DT\n出库：\n便利店吊卡*31', 'DT')
        preview, saved = self.confirm_and_save(self.body(more))
        (line,) = [line for line in saved['stock']['lines'] if line['wanted']]
        self.assertEqual((line['moving'], line['short'], line['problem']), (30, 1, 'not_enough'))
        self.assertEqual((self.balance(card, 'DT', 'upstairs'), self.balance(card, 'MK', 'warehouse')), (0, 60))
        # The SA shu lines are no longer on the day's report: all of it is back at DT.
        self.assertEqual((self.balance(shu, 'DT', 'upstairs'), self.balance(whole, 'DT', 'upstairs'),
                          self.balance(shu, 'MK', 'floor')), (153, 5, 100))
        notices = self.client.get('/api/sales/report-stock', headers=self.headers('manager')).get_json()['notices']
        self.assertEqual([(notice['store_code'], notice['reason'], notice['quantity'], notice['where']) for notice in notices],
                         [('DT', 'move_short', 1, ['upstairs'])])
        with closing(self.connect()) as con:
            self.assertTrue(reconcile_inventory(con)['ok'])

    def test_the_receiving_stores_report_is_told_what_出库_already_put_there(self):
        # 出库 adds the goods at the other store at once, so that store's own report must not add them again
        # (2026-11-13: "do not add them again if the receiving store lists them arriving").
        self.build_catalogue()
        self.confirm_and_save(self.body(self.parse('2026.11.12 DT\n出库：\nlabubu喵喵毛绒 9*3\netb-pe*2', 'DT')))
        arrived = '2026.11.13 太古\n入库：\nlabubu喵喵毛绒 9*3\n小王子 12*1\n入店：\netb-pe*2\n出库入店：\netb-pe*1'
        parsed = self.parse(arrived, 'MK')
        told = {tuple(check['lines']): check for check in parsed['format_checks'] if '不然会加两次' in check['problem']}
        self.assertEqual(sorted(told), [(3,), (6,)])
        self.assertEqual((told[3,]['level'], told[3,]['section'], told[3,]['text']), ('check', '入库', ['labubu喵喵毛绒 9*3']))
        self.assertIn('dt 2026-11-12 的 出库 已经把 3 set「labubu喵喵毛绒 整端」加到这家店的仓库', told[3,]['problem'].lower())
        self.assertIn('移除这一行', told[3,]['problem'])
        # Written as new goods onto the floor: they are in the warehouse already, so it is a 出库入店.
        self.assertIn('改成 出库入店', told[6,]['problem'])
        # Goods that went onto MK's floor and are then written 入店 are simply left out.
        self.confirm_and_save(self.body(self.parse('2026.11.12 DT\n出库：\netb-pe*2', 'DT'), to_floor={'etb-pe'}))
        again = self.parse(arrived, 'MK')
        (floor,) = [check for check in again['format_checks'] if check['lines'] == [6] and '不然会加两次' in check['problem']]
        self.assertIn('楼下', floor['problem'])
        self.assertIn('移除这一行', floor['problem'])
        # Days later it is other goods: nothing is said. The sender's own report is never told either.
        later = self.parse(arrived.replace('2026.11.13', '2026.11.16'), 'MK')
        self.assertFalse([check for check in later['format_checks'] if '不然会加两次' in check['problem']])
        own = self.parse('2026.11.13 DT\n入库：\nlabubu喵喵毛绒 9*3', 'DT')
        self.assertFalse([check for check in own['format_checks'] if '不然会加两次' in check['problem']])

    def test_出库_stays_a_note_while_no_other_store_is_live(self):
        with closing(self.connect()) as con:
            box = con.execute("""INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, identity_status)
                                 VALUES ('ONE', 'SA shu', 'SA shu', 'random_box', 'box', 'verified')""").lastrowid
            for code in ('floor', 'upstairs', 'display', 'claw'):
                con.execute("""INSERT INTO opening_counts(location_id, product_id, stock_form, quantity, counted_by)
                               VALUES (?, ?, 'random_box', 30, 'auth0|admin')""", (self.at['DT', code], box))
            con.commit()
        with redirect_stdout(io.StringIO()):
            _opening_cli(['intake', '--db', db.DB_PATH, '--from-app', '--store', 'DT', '--business-date', LIVE_DATE,
                          '--actor-sub', 'auth0|admin', '--apply', '--backup', str(Path(self.tempdir.name) / 'one.db')])
        parsed = self.parse('2026.11.12 DT\n入库：\nSA shu*2\n出库：\nSA shu*30', 'DT')
        # 入库 moves at any live store; 出库 has nowhere to go yet, so it is kept as the note it always was.
        self.assertEqual((self.sections(parsed), parsed['store_transfers']), ({'back_in': 1}, ['SA shu*30']))
        # A line sent as 出库 anyway is refused by its line, and nothing saves.
        body = self.body(parsed)
        body['items'].append({'product_id': box, 'section': 'transfer_out', 'box_size': 1, 'num_boxes': 30, 'loose_qty': 0,
                              'raw_name': 'SA shu', 'source_bucket': 'review'})
        refused = self.submit(body, dry_run=True)
        self.assertEqual((refused.status_code, refused.get_json()['code'], refused.get_json()['line']),
                         (409, 'report_line_invalid', 2))
        self.assertIn('调拨 Transfer', refused.get_json()['error'])
        self.assertEqual(self.balance(box, 'DT', 'upstairs'), 30)

    # ── days recorded by hand ────────────────────────────────────────────────

    def test_a_day_before_the_change_went_live_keeps_the_old_reading(self):
        # Until the change went live 入库 was counted in and 出库 was a 调拨 Transfer, by hand: the report of
        # such a day, pasted again, must not move them a second time.
        self.assertEqual(self.went_live, business_date())  # a new database starts on the day it is set up
        self.build_catalogue()
        with closing(self.connect()) as con:
            con.execute("UPDATE app_settings SET value='2026-11-12' WHERE key='report_lines_from'")
            con.commit()
        old = self.parse(report_text('DT', '2026-11-11'), 'DT')
        self.assertEqual((old['stock_mode'], old['unknown_sections'], old['store_transfers']), ('live', ['入库:'], ['SA shu*30']))
        self.assertFalse(set(self.sections(old)) & {'back_in', 'transfer_out', 'floor_to_back', 'display_to_floor'})
        self.assertEqual({item['reason'] for item in old['failed'] if item.get('unknown_header')}, {'unknown_section'})
        # The headings it reads the old way are named once, with why.
        (why,) = [check for check in old['format_checks'] if '早于 2026-11-12' in check['problem']]
        self.assertEqual((why['level'], why['lines'], why['text'][:2]), ('check', [120, 128, 135], ['入库：', '出库：']))
        # Saved with 入库 skipped, as on 2026-11-13, it moves none of them.
        keep = lambda item: not item.get('unknown_header') and '出店入库' not in item['raw_name']
        shu, whole = self.product('SA shu'), self.product('SA shu', whole=True)
        preview, saved = self.confirm_and_save(self.body(old, keep))
        self.assertFalse({line['section'] for line in saved['stock']['lines']} & {'back_in', 'transfer_out'})
        self.assertEqual((self.balance(whole, 'DT', 'upstairs'), self.balance(shu, 'MK', 'warehouse'),
                          self.balance(shu, 'DT', 'upstairs')), (5, 30, 153 - 14))
        self.assertIsNone(self.whole_set_of('hellokitty苹果贴贴'))
        # The pre-order rule and the posting order are not stock sections: they hold on any day.
        self.assertEqual(self.balance(self.product('小野哈利波特figure'), 'DT', 'display'), 5 + 1)
        # From that day on the report moves them itself.
        new = self.parse(report_text('DT', '2026-11-12'), 'DT')
        self.assertEqual((self.sections(new)['transfer_out'], new['store_transfers']), (3, []))
        self.assertFalse([check for check in new['format_checks'] if '早于' in check['problem']])

    # ── the other new headings ───────────────────────────────────────────────

    def test_出店入库_goes_up_from_the_floor_and_出display入店_comes_down_from_display(self):
        self.build_catalogue()
        figure, exercising = self.product('小野哈利波特figure'), self.product('smiski exercising')
        special = self.designs['Harry Potter - Special Edition (Big Secret)']
        self.receive(special, 'DT', 'floor', 2, 'piece')
        self.receive(figure, 'DT', 'floor', 20)
        parsed = self.parse('2026.11.12 DT\n出店入库：小野哈利波特figure-Harry Potter-Special Edition*1\n'
                            '出店入库：\n小野哈利波特figure 12*1\n出display 入店：\nsmiski exercising*2', 'DT')
        self.assertEqual(self.sections(parsed), {'floor_to_back': 2, 'display_to_floor': 1})
        preview, saved = self.confirm_and_save(self.body(parsed))
        self.assertTrue(all(line['moving'] > 0 and not line['short'] for line in saved['stock']['lines']))
        self.assertEqual((self.balance(special, 'DT', 'floor'), self.balance(special, 'DT', 'upstairs')), (1, 1))
        # The floor holds boxes: "12*1" goes back up as 12 boxes, not as a whole set.
        self.assertEqual((self.balance(figure, 'DT', 'floor'), self.balance(figure, 'DT', 'upstairs'),
                          self.balance(self.product('小野哈利波特figure', whole=True), 'DT', 'upstairs')), (8, 42, 0))
        # "*2" with no set size is two display boxes; the display's whole set stays whole.
        self.assertEqual((self.balance(exercising, 'DT', 'display'), self.balance(exercising, 'DT', 'floor'),
                          self.balance(self.product('smiski exercising', whole=True), 'DT', 'display')), (3, 102, 1))

    def test_a_legacy_store_reads_the_new_headings_as_it_always_did(self):
        parsed = self.parse('2026.11.12 DT\n卡机汇总：\nTest Product*1\n入库：\nTest Product 6*2\n出display 入店：\n'
                            'Test Product*1\n出店display：\nTest Product*1', 'DT')
        self.assertEqual((parsed['stock_mode'], parsed['unknown_sections']), ('legacy', ['入库:', '出display 入店:']))
        self.assertEqual(parsed['display_stock_out'], ['Test Product*1'])
        self.assertEqual({item['reason'] for item in parsed['failed']}, {'unknown_section'})
        # Only a live store's report reads them; a section choice (Skip) for 入库 is still accepted.
        self.assertEqual(_detect_section_type('入库:', {}), ('unknown', ''))
        self.assertEqual(_detect_section_type('入库：', {}, live=True), ('back_in', ''))
        self.assertEqual(_detect_section_type('出店入库：X*1', {}, live=True), ('floor_to_back', 'X*1'))
        self.assertEqual(_detect_section_type('出display 入店：', {}, live=True), ('display_to_floor', ''))
        self.assertEqual(_detect_section_type('出店display：', {}, live=True), ('display_stock_out', ''))
        self.assertEqual(_detect_section_type('入仓库', {}, live=True), ('back_in', ''))

    # ── pre-orders, 出店 sizes, design notes ─────────────────────────────────

    def test_a_pre_order_is_left_out_however_it_is_marked(self):
        text = ('2026.11.12 DT\n卡机汇总：\nTest Product*1（预订单）\nTest Product*2 预订\nTest Product*1（pain）（预定）\n'
                'Test Product*3，Test Product*1(Pre-order)\n预订单：\nTest Product*4\n随手记汇总：\nTest Product*5')
        parsed = self.parse(text, 'DT')
        self.assertEqual([(entry['line'], entry['text'], entry['reason']) for entry in parsed['left_out']], [
            (3, 'Test Product*1（预订单）', 'pre_order'), (4, 'Test Product*2 预订', 'pre_order'),
            (5, 'Test Product*1（pain）（预定）', 'pre_order'), (6, 'Test Product*1(Pre-order)', 'pre_order'),
            (8, 'Test Product*4', 'pre_order')])
        # Only the two real sales are left to save.
        self.assertEqual(sorted((item['line'], item['section'], item['qty']) for item in self.rows(parsed)),
                         [(6, 'pos', 3), (10, 'cash', 5)])
        self.assertEqual((parsed['metadata_errors'], parsed['unknown_sections']), ([], []))

    def test_a_colon_before_the_number_makes_a_出店_line_a_pack(self):
        read = lambda token: {key: _parse_token(token, 'stock_out')[key] for key in ('raw_name', 'qty', 'flagged')}
        self.assertEqual(read('Smiski exercising:12*1'), {'raw_name': 'Smiski exercising', 'qty': 12, 'flagged': False})
        self.assertEqual(read('SA animal ver2: 12*1'), {'raw_name': 'SA animal ver2', 'qty': 12, 'flagged': False})
        self.assertEqual(read('Molly100% S2: 9*2'), {'raw_name': 'Molly100% S2', 'qty': 18, 'flagged': False})
        self.assertEqual(read('诡乐园:5'), {'raw_name': '诡乐园', 'qty': 5, 'flagged': False})
        # Without the colon a trailing number may be the name's own, as before: read as 2, and a check says so.
        alone = _parse_token('Alpha 12*2', 'stock_out')
        self.assertEqual((alone['raw_name'], alone['qty'], alone['name_number']), ('Alpha 12', 2, 12))
        # The original text is kept for the record; it is not part of the name the line is matched by.
        self.assertEqual(sales.report_match_key('SA animal ver2', '原文: SA animal ver2: 12*1'), ('sa animal ver2', ''))
        self.assertEqual(sales.report_match_query('SA animal ver2', '原文: SA animal ver2: 12*1'), 'SA animal ver2')

    def test_a_design_written_as_a_note_is_offered_only_for_its_own_series(self):
        designs = [
            {'id': 4, 'design_name': 'Midnight Kitty', 'series_name': 'labubu喵喵毛绒', 'head_jzm': 'labubu喵喵毛绒', 'head_name': None},
            {'id': 5, 'design_name': 'Midnight Kitty', 'series_name': 'Other', 'head_jzm': '别的系列', 'head_name': None},
            {'id': 6, 'design_name': 'Circus', 'series_name': 'Shelter', 'head_jzm': 'Shelter', 'head_name': None},
        ]
        self.assertEqual(_note_design('labubu喵喵毛绒', 'midnight kitty', designs), 4)
        self.assertEqual(_note_design('Labubu喵喵毛绒', 'Midnight  Kitty；原文: x', designs), 4)
        self.assertEqual(_note_design('shelter', 'Circus', designs), 6)
        # A note that names no design of that series, a place mark, or no note at all: nothing is offered.
        for name, note in (('labubu喵喵毛绒', '蓝色'), ('labubu喵喵毛绒', 'display'), ('labubu喵喵毛绒', ''),
                           ('SA dream', 'midnight kitty'), ('labubu喵喵毛绒', 'k')):
            self.assertIsNone(_note_design(name, note, designs), (name, note))


if __name__ == '__main__':
    import unittest
    unittest.main()
