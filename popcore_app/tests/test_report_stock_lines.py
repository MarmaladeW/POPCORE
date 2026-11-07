"""A live store's daily report moves its stock lines too (owner, 2026-11-03): 出库入店 upstairs to the
floor (a written set size opens whole sets as boxes), 入店 new goods onto the floor, 出店 off the floor,
卖display a sale from display that the floor then does not take, 拆display floor to display, 入display
upstairs to display and 娃娃机 a prize off the claw. 出库 and 入娃娃机 stay notes. The manager confirms what
a report moves before it saves, and a re-pasted day moves only the difference.

The three saved DT reports (Oct 29–31, the first days after go-live) are replayed on a catalogue made of the
names they use, as a manager would: accept every match, leave out what matches nothing."""
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
from blueprints.sales import _REPORT_NOTE_FIELDS, _dash_design, _parse_token  # noqa: E402
from inventory_opening import reconcile_inventory  # noqa: E402
from matcher import clean_name, normalize  # noqa: E402
from scripts.inventory_opening import main as _opening_cli  # noqa: E402

REPORTS = Path(__file__).resolve().parent / 'fixtures' / 'daily_reports'
LIVE_DATE = '2026-10-29'
STOCK = {'refill', 'stock_in', 'stock_out', 'display_sold', 'break_display', 'display_stock_in', 'claw_prize'}


def report_text(day):
    return (REPORTS / f'DT-{day}.txt').read_text(encoding='utf-8')


class ReportStockLineTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.dt = con.execute("SELECT id FROM stores WHERE code='DT'").fetchone()[0]
            self.at = {row[0]: row[1] for row in con.execute(
                'SELECT code, id FROM inventory_locations WHERE store_id=?', (self.dt,))}
            for subject in ('auth0|manager', 'auth0|admin'):
                con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)', (subject, self.dt))
            con.commit()

    # ── fixture catalogue ────────────────────────────────────────────────────

    def build_catalogue(self, days, *, upstairs_sets=10):
        """One verified product per name the reports use (a blind box, or a whole set where a 出库入店 line
        opens sets), each with stock at every DT location, then DT goes live on Oct 29."""
        names = {}
        for day in days:
            # Read as the live store it is about to be, before any product exists.
            with patch.object(sales, '_is_authoritative', return_value=True):
                parsed = self.parse(report_text(day))
            for item in parsed['failed']:
                if item['raw_name'] == 'SA特殊订单':
                    continue  # the owner: a one-off, left unmatched
                entry = names.setdefault(normalize(clean_name(item['raw_name'])), {'name': item['raw_name'], 'set': None})
                if item['section'] == 'refill' and (item.get('box_size') or 1) > 1 and not item.get('to_display'):
                    entry['set'] = item['box_size']
        with closing(self.connect()) as con:
            self.products = {}
            for number, (key, entry) in enumerate(sorted(names.items()), 1):
                sku = f'RP{number:04d}'
                box = con.execute(
                    """INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, identity_status)
                       VALUES (?, ?, ?, 'random_box', 'box', 'verified')""", (sku, entry['name'], entry['name'])).lastrowid
                self.products[key] = box
                counts = [('floor', box, 'random_box', 100), ('upstairs', box, 'random_box', 30),
                          ('display', box, 'random_box', 5), ('claw', box, 'random_box', 5)]
                if entry['set']:
                    whole = con.execute(
                        """INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, identity_status)
                           VALUES (?, ?, ?, 'sealed_set', 'set', 'verified')""",
                        (f'{sku}-SET', f"{entry['name']} 整端", f"{entry['name']} 整端")).lastrowid
                    con.execute("""INSERT INTO product_conversions(source_product_id, target_product_id, output_per_input, version)
                                   VALUES (?, ?, ?, 1)""", (whole, box, entry['set']))
                    self.products[key + ':set'] = whole
                    counts.append(('upstairs', whole, 'sealed_set', upstairs_sets))
                for code, product, form, quantity in counts:
                    con.execute("""INSERT INTO opening_counts(location_id, product_id, stock_form, quantity, counted_by)
                                   VALUES (?, ?, ?, ?, 'auth0|admin')""", (self.at[code], product, form, quantity))
            con.commit()
        with redirect_stdout(io.StringIO()):
            _opening_cli(['intake', '--db', db.DB_PATH, '--from-app', '--store', 'DT', '--business-date', LIVE_DATE,
                          '--actor-sub', 'auth0|admin', '--apply', '--backup', str(Path(self.tempdir.name) / 'before.db')])

    def product(self, name, whole=False):
        return self.products[normalize(clean_name(name)) + (':set' if whole else '')]

    def balance(self, product, code):
        with closing(self.connect()) as con:
            row = con.execute("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                                 AND disposition='saleable'""", (product, self.at[code])).fetchone()
            return row[0] if row else 0

    def balances(self):
        with closing(self.connect()) as con:
            return {tuple(row[:3]): row[3] for row in con.execute(
                'SELECT product_id, location_id, disposition, quantity FROM inventory_balances')}

    # ── the manager's paste ──────────────────────────────────────────────────

    def parse(self, text, store='DT'):
        response = self.client.post('/api/sales/parse_report', json={'text': text, 'store_code': store},
                                    headers=self.headers('manager'))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    @staticmethod
    def payload_item(item, bucket):
        base = {'product_id': item['product']['id'], 'section': item['section'], 'notes': item.get('note') or '',
                'raw_name': item['raw_name'], 'source_bucket': bucket}
        if item['section'] == 'cash':
            base['qty_cash'] = item['qty']
        elif item['section'] in ('stock_in', 'refill'):
            base.update(box_size=item['box_size'], num_boxes=item['qty'], loose_qty=item.get('loose_qty') or 0,
                        to_display=bool(item.get('to_display')))
        elif item['section'] in STOCK:
            base.update(qty=item['qty'], from_display=bool(item.get('from_display')))
        else:
            base['qty_pos'] = item['qty']
        return base

    def body(self, parsed, keep=lambda item: True):
        items = [self.payload_item(item, 'confirmed') for item in parsed['confirmed'] if keep(item)]
        items += [self.payload_item(item, 'review') for item in parsed['review'] if keep(item)]
        return {'date': parsed['detected_date'], 'store_code': parsed['store'], 'mode': 'replace',
                'classification': 'summary_only', 'stock_mode': parsed['stock_mode'], 'items': items,
                'report_metadata': {'cash_actual': parsed['cash_total_reported'],
                                    'cash_expected': parsed['cash_expected_reported'],
                                    **{key: parsed.get(key, []) for key in _REPORT_NOTE_FIELDS}}}

    def submit(self, body, **extra):
        return self.client.post('/api/sales/submit_daily_report', json={**body, **extra}, headers=self.headers('manager'))

    def confirm_and_save(self, body):
        preview = self.submit(body, dry_run=True)
        self.assertEqual(preview.status_code, 200, preview.get_json())
        saved = self.submit(body, stock_digest=preview.get_json()['stock_digest'])
        self.assertEqual(saved.status_code, 200, saved.get_json())
        return preview.get_json(), saved.get_json()

    def lines_by_raw(self, stock):
        return {line['raw']: line for line in stock['lines']}

    # ── the three saved reports ──────────────────────────────────────────────

    def test_the_three_saved_reports_preview_every_stock_line_and_save_in_one_go(self):
        days = ('2026-10-29', '2026-10-30', '2026-10-31')
        self.build_catalogue(days)
        expected_sections = {
            '2026-10-29': {'stock_in': 11, 'display_sold': 5, 'display_stock_in': 2, 'break_display': 2, 'claw_prize': 1},
            '2026-10-30': {'display_sold': 1, 'break_display': 2, 'stock_in': 14},
            '2026-10-31': {'refill': 22, 'stock_out': 2, 'stock_in': 7, 'display_sold': 8},
        }
        for day in days:
            parsed = self.parse(report_text(day))
            self.assertEqual((parsed['detected_date'], parsed['store'], parsed['stock_mode']), (day, 'DT', 'live'))
            self.assertEqual((parsed['unknown_sections'], parsed['metadata_errors']), ([], []), day)
            rows = parsed['confirmed'] + parsed['review']
            sections = {}
            for item in rows:
                if item['section'] in STOCK:
                    sections[item['section']] = sections.get(item['section'], 0) + 1
            self.assertEqual(sections, expected_sections[day], day)
            self.assertFalse([item['raw_name'] for item in rows if item['flagged']], day)
            # Only the one-off SA特殊订单 stays unmatched; 卖display and 娃娃机 are stock lines, not notes.
            self.assertEqual({item['raw_name'] for item in parsed['failed']}, {'SA特殊订单'} if day == '2026-10-31' else set())
            self.assertEqual((parsed['display_sales'], parsed['claw_prizes'], parsed['display_stock_in']), ([], [], []))

            body = self.body(parsed)
            before = self.balances()
            preview = self.submit(body, dry_run=True)
            self.assertEqual(preview.status_code, 200, preview.get_json())
            stock = preview.get_json()['stock']
            self.assertTrue(preview.get_json()['needs_confirmation'])
            # Every stock line shows what it moves, from where to where, and nothing is short.
            self.assertTrue(stock['lines'])
            for line in stock['lines']:
                self.assertTrue(line.get('from') or line.get('to'), line)
                self.assertEqual((line['short'], line['problem']), (0, None), line)
                self.assertGreater(line['moving'], 0, line)
            self.assertEqual(stock['moves_short'], [])
            # A preview writes nothing.
            self.assertEqual(self.balances(), before)
            with closing(self.connect()) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM daily_sales WHERE date=?', (day,)).fetchone()[0], 0)
                self.assertEqual(con.execute('SELECT COUNT(*) FROM report_stock_moves').fetchone()[0] > 0,
                                 day != '2026-10-29')

            # Saving without confirming what it moves is refused and changes nothing.
            refused = self.submit(body)
            self.assertEqual(refused.status_code, 409)
            self.assertEqual(refused.get_json()['code'], 'stock_confirmation_required')
            self.assertEqual(self.balances(), before)

            saved = self.submit(body, stock_digest=preview.get_json()['stock_digest'])
            self.assertEqual(saved.status_code, 200, saved.get_json())
            self.assertEqual([line['moving'] for line in saved.get_json()['stock']['lines']],
                             [line['moving'] for line in stock['lines']])
            with closing(self.connect()) as con:
                self.assertTrue(reconcile_inventory(con)['ok'])

        # Oct 29: 入店 "Smiski hipper：12*1" is 12 new boxes on the floor; 拆display moves floor → display;
        # 娃娃机 takes the prize off the claw; 入娃娃机 stays a note for the claw screen.
        floor = lambda name: self.balance(self.product(name), 'floor')
        self.assertEqual(self.balance(self.product('蒙奇奇wink'), 'claw'), 4)
        # Oct 29: one sold from display, one opened from the floor for display.
        self.assertEqual(self.balance(self.product('peach riot音乐随行积木挂件'), 'display'), 5 - 1 + 1)
        # Oct 31: 出库入店 "SA dream 12*2" opens 2 whole sets upstairs into 24 floor boxes, less that day's sales.
        self.assertEqual(self.balance(self.product('SA dream', whole=True), 'upstairs'), 8)
        self.assertEqual(self.balance(self.product('星星人农场毛绒', whole=True), 'upstairs'), 9)
        self.assertEqual(self.balance(self.product('Smiski拍拍灯v2'), 'upstairs'), 29)  # "v2*1" is one lamp
        # 入店 "萌粒 快乐开涮：20*2➕19" = 59 boxes, and 出店 takes 可乐搪胶 off the floor.
        self.assertEqual(floor('萌粒 快乐开涮'), 100 + 59 - 2)
        self.assertEqual(floor('可乐搪胶'), 99)
        # "(display)" lines go upstairs → display.
        self.assertEqual(self.balance(self.product('SA Easter Series 2017 - Easter Bunny'), 'display'), 6)
        self.assertEqual(self.balance(self.product('SA Easter Series 2017 - Easter Bunny'), 'upstairs'), 29)
        # 卖display "诡乐园*1" on Oct 31: that day's 随手记 sale came from display, so only Oct 29–30's 2 + 6
        # came off the floor.
        self.assertEqual((self.balance(self.product('诡乐园'), 'display'), floor('诡乐园')), (4, 100 - 2 - 6))
        with closing(self.connect()) as con:
            notes = con.execute("SELECT store_transfers, claw_stock_in FROM daily_report_metadata WHERE date='2026-10-31'"
                                ).fetchone()
            self.assertIn('sa七夕礼盒*2', notes['store_transfers'])
            self.assertIn('Dimoo在你身边:3', con.execute(
                "SELECT claw_stock_in FROM daily_report_metadata WHERE date='2026-10-29'").fetchone()[0])

    def test_the_nov_1_and_2_reports_read_inline_headers_display_lines_and_claw_prizes(self):
        days = ('2026-11-01', '2026-11-02')
        self.build_catalogue(days)
        expected = {
            '2026-11-01': {'claw_prize': 2, 'stock_out': 1, 'display_sold': 8, 'break_display': 1, 'refill': 22},
            '2026-11-02': {'break_display': 1, 'display_sold': 2, 'refill': 18},
        }
        for day in days:
            parsed = self.parse(report_text(day))
            self.assertEqual((parsed['detected_date'], parsed['unknown_sections'], parsed['metadata_errors'],
                              parsed['failed']), (day, [], [], []))
            sections = {}
            for item in parsed['confirmed'] + parsed['review']:
                self.assertFalse(item['flagged'], item)
                if item['section'] in STOCK:
                    sections[item['section']] = sections.get(item['section'], 0) + 1
            self.assertEqual(sections, expected[day], day)
            preview, saved = self.confirm_and_save(self.body(parsed))
            self.assertTrue(all(line['moving'] > 0 and not line['short'] for line in saved['stock']['lines']))
        # "出店：小野哈利波特figure*1（display）" takes the display piece out; "拆display：…*1" puts a floor box there.
        figure = self.product('小野哈利波特figure')
        self.assertEqual(self.balance(figure, 'display'), 5 - 1 + 1)
        # "娃娃机出：" prizes come off the claw.
        self.assertEqual(self.balance(self.product('Labubu十周年figure'), 'claw'), 4)
        # Nov 2: 拆display echo*2, then 卖display Echo*2 — the two 随手记 Echo sales came from display.
        echo = self.product('Echo')
        self.assertEqual(self.balance(echo, 'display'), 5 + 2 - 2)
        # "萌粒 咖啡21*1" opens one set of 21 onto the floor.
        self.assertEqual(self.balance(self.product('萌粒 咖啡', whole=True), 'upstairs'), 9)
        with closing(self.connect()) as con:
            cash = con.execute("SELECT cash_actual_cents, cash_expected_cents FROM daily_report_metadata "
                               "WHERE date='2026-11-02' AND store='DT'").fetchone()
            self.assertEqual(tuple(cash), (39000, 40550))
            self.assertTrue(reconcile_inventory(con)['ok'])

    def test_a_dash_names_a_design_of_the_series_written_before_it(self):
        # The owner's rule (2026-11-01): "X - name" is the design `name` of series X.
        designs = [
            {'id': 1, 'design_name': 'Santa', 'series_name': 'Sonny Angel Christmas 2015 Blind Box Series',
             'head_jzm': 'Sonny Angel Christmas 2015 Blind Box Series', 'head_name': None},
            {'id': 2, 'design_name': 'Santa Claus', 'series_name': 'Sonny Angel Christmas Series 2016',
             'head_jzm': 'Sonny Angel Christmas Series 2016', 'head_name': None},
            {'id': 3, 'design_name': 'Silk Royale', 'series_name': 'Clot', 'head_jzm': 'Clot',
             'head_name': 'Hirono X Clot Series Figures'},
            {'id': 4, 'design_name': 'Psyduck', 'series_name': '宝可梦自然', 'head_jzm': '宝可梦自然', 'head_name': None},
        ]
        self.assertEqual(_dash_design('SA Christmas Series 2016 - Santa Claus', designs), 2)
        # The year written decides the series, even when the design is written by the other series' name.
        self.assertEqual(_dash_design('SA Christmas Series 2015-Santa Claus', designs), 1)
        self.assertEqual(_dash_design('小野clot figure-Silk Royale', designs), 3)
        self.assertEqual(_dash_design('宝可梦自然-psyduck', designs), 4)
        for name in ('ETB- PE', 'etb-pe', '小野哈利波特figure-斯内普', 'Silk Royale', 'SA dream'):
            self.assertIsNone(_dash_design(name, designs), name)

    def test_markhams_report_names_its_store_in_chinese_and_marks_display_sales_inline(self):
        text = (REPORTS / 'MK-2026-11-02.txt').read_text(encoding='utf-8')
        # Read with DT selected: "2026.11.2 太古汇总" still makes it Markham's Nov 2 report.
        with patch.object(sales, '_is_authoritative', return_value=True):
            parsed = self.parse(text, store='DT')
        self.assertEqual((parsed['detected_date'], parsed['store'], parsed['metadata_errors']), ('2026-11-02', 'MK', []))
        rows = parsed['confirmed'] + parsed['review'] + parsed['failed']
        self.assertFalse([item for item in rows if '太古' in item['raw_name']])
        # "labubu喵喵毛绒*1（display）" in 随手记 is a sale that came off display.
        shown = sorted((item['raw_name'], item['qty']) for item in rows if item['section'] == 'display_sold')
        self.assertEqual(shown, [('labubu喵喵毛绒', 1), ('萌粒度假', 1)])
        sold = [item for item in rows if item['section'] == 'cash' and item['raw_name'] == '萌粒度假']
        self.assertEqual([(item['qty'], item['note']) for item in sold], [(1, '')])
        # 卡机 31 + 随手记 44 units, every one still a sale.
        self.assertEqual(sum(item['qty'] for item in rows if item['section'] in ('pos', 'cash')), 75)

    def test_a_written_secret_is_the_only_secret_design_of_its_series(self):
        products = [{'id': 10, 'sku': 'PM0526', 'jizhanming': '坐坐', 'name_cn_en': 'DUODUO', 'product_type': '盲盒'},
                    {'id': 20, 'sku': 'PM0476', 'jizhanming': '高能', 'name_cn_en': 'Energy', 'product_type': '盲盒'}]
        designs = [{'id': 11, 'design_name': 'DUODUO- SECRET', 'head_id': 10},
                   {'id': 12, 'design_name': 'Sleepy', 'head_id': 10},
                   {'id': 21, 'design_name': '隐藏 本我', 'head_id': 20},
                   {'id': 22, 'design_name': 'ID (Secret)', 'head_id': 20}]
        self.assertEqual(sales._secret_design('坐坐隐藏', designs, products, {}), 11)
        self.assertEqual(sales._secret_design('坐坐 secret', designs, products, {}), 11)
        # Two secrets, an unknown series or no secret written: no suggestion.
        for name in ('高能隐藏', '迷你布a隐藏', '坐坐'):
            self.assertIsNone(sales._secret_design(name, designs, products, {}), name)

    def test_a_display_box_sold_as_its_design_is_not_also_taken_off_the_floor(self):
        """MK's display keeps a series' opened piece as the series' box; the sale names the design."""
        with closing(self.connect()) as con:
            head = con.execute("""INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, identity_status)
                                  VALUES ('DD', '坐坐', '坐坐', 'random_box', 'box', 'verified')""").lastrowid
            series = con.execute("INSERT INTO product_series(name, product_id) VALUES ('坐坐', ?)", (head,)).lastrowid
            design = con.execute(
                """INSERT INTO products(sku, name_cn_en, jizhanming, stock_form, stock_unit, identity_status, series_id,
                                        design_name)
                   VALUES ('DD-01', '坐坐 DUODUO- SECRET', '坐坐 DUODUO- SECRET', 'confirmed_design', 'piece', 'verified', ?,
                           'DUODUO- SECRET')""", (series,)).lastrowid
            con.execute('UPDATE products SET series_id=? WHERE id=?', (series, head))
            for code, quantity in (('floor', 10), ('display', 1)):
                con.execute("""INSERT INTO opening_counts(location_id, product_id, stock_form, quantity, counted_by)
                               VALUES (?, ?, 'random_box', ?, 'auth0|admin')""", (self.at[code], head, quantity))
            con.commit()
        with redirect_stdout(io.StringIO()):
            _opening_cli(['intake', '--db', db.DB_PATH, '--from-app', '--store', 'DT', '--business-date', LIVE_DATE,
                          '--empty-location', 'DT-upstairs', '--empty-location', 'DT-claw',
                          '--actor-sub', 'auth0|admin', '--apply', '--backup', str(Path(self.tempdir.name) / 'b.db')])
        body = {'date': LIVE_DATE, 'store_code': 'DT', 'classification': 'summary_only', 'items': [
            {'product_id': design, 'section': 'cash', 'qty_cash': 1, 'raw_name': '坐坐隐藏'},
            {'product_id': head, 'section': 'display_sold', 'qty': 1, 'raw_name': '坐坐隐藏'}]}
        preview, saved = self.confirm_and_save(body)
        self.assertEqual((saved['stock']['short'], saved['stock']['from_display'][0]['product_id']), ([], design))
        self.assertEqual((self.balance(head, 'display'), self.balance(head, 'floor')), (0, 10))

    def test_a_repasted_day_moves_only_the_difference_after_the_manager_confirms(self):
        self.build_catalogue(('2026-10-31',))
        parsed = self.parse(report_text('2026-10-31'))
        self.confirm_and_save(self.body(parsed))
        after_first = self.balances()

        # The same report again moves nothing and needs no confirmation.
        again = self.submit(self.body(parsed), dry_run=True).get_json()
        self.assertFalse(again['needs_confirmation'])
        self.assertTrue(all(line['moving'] == 0 for line in again['stock']['lines']))
        saved = self.submit(self.body(parsed))
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(self.balances(), after_first)

        # A fix: "SA dream 12*2" becomes 1 set and 出店 可乐figure is dropped.
        text = report_text('2026-10-31').replace('SA dream 12*2', 'SA dream 12*1').replace('可乐figure*1\n', '')
        fixed = self.parse(text)
        body = self.body(fixed)
        refused = self.submit(body)
        self.assertEqual(refused.get_json()['code'], 'stock_confirmation_required')
        self.assertEqual(self.balances(), after_first)
        preview, saved = self.confirm_and_save(body)
        changes = {(line['section'], line['moving']) for line in preview['stock']['lines'] if line['moving']}
        self.assertEqual(changes, {('refill', -1), ('stock_out', -1)})
        self.assertEqual(self.balance(self.product('SA dream', whole=True), 'upstairs'), 9)
        self.assertEqual(self.balance(self.product('可乐figure'), 'floor'), 100)
        with closing(self.connect()) as con:
            reasons = [row[0] for row in con.execute(
                "SELECT reason FROM inventory_documents WHERE source_type='daily_report' AND kind='correction' "
                "AND reason LIKE '日报更正%' ORDER BY id")]
            self.assertTrue(any('可乐figure' in reason for reason in reasons), reasons)
            self.assertTrue(reconcile_inventory(con)['ok'])

    def test_a_remembered_stock_line_confirms_only_with_the_same_section_and_display(self):
        # 名称记忆 (owner, 2026-11-03): a stock line's name is remembered once reviewed, and confirms by itself
        # only where it was reviewed: this store, this section, on display or not.
        self.build_catalogue(('2026-10-31',))
        text = report_text('2026-10-31')
        first = self.parse(text)
        self.assertFalse([row for row in first['confirmed'] if row['section'] in STOCK])
        self.confirm_and_save(self.body(first))
        again = self.parse(text)
        self.assertFalse([row['raw_name'] for row in again['review'] if row['section'] in STOCK])
        remembered = [row for row in again['confirmed'] if row['section'] in STOCK]
        self.assertEqual(len(remembered), len([row for row in first['review'] if row['section'] in STOCK]))
        self.assertEqual({row['pick']['state'] for row in remembered}, {'remembered'})
        # Written onto display this time: not the line it was confirmed as, so it is asked again, already picked.
        picked = next(row['product']['id'] for row in first['review'] if row['raw_name'] == '星星人农场毛绒')
        moved = self.parse(text.replace('星星人农场毛绒 6*1', '星星人农场毛绒 6*1（display）'))
        row = next(row for row in moved['review'] if row['raw_name'] == '星星人农场毛绒')
        self.assertEqual((row['section'], row['to_display'], row['product']['id'], row['pick']['state']),
                         ('refill', True, picked, 'confirm'))
        self.assertIn({'store_code': 'DT', 'section': 'refill', 'display': False}, row['pick']['places'])

    def test_what_a_source_lacks_waits_as_a_notice_and_a_check_waives_it(self):
        self.build_catalogue(('2026-10-31',), upstairs_sets=1)
        parsed = self.parse(report_text('2026-10-31'))
        keep = lambda item: item['raw_name'] == 'SA dream' and item['section'] == 'refill'
        preview, saved = self.confirm_and_save(self.body(parsed, keep))
        (line,) = saved['stock']['lines']
        self.assertEqual((line['moving'], line['short'], line['problem']), (1, 1, 'not_enough'))
        whole = self.product('SA dream', whole=True)
        self.assertEqual(self.balance(whole, 'upstairs'), 0)
        notices = self.client.get('/api/sales/report-stock', headers=self.headers('manager')).get_json()['notices']
        (notice,) = notices
        self.assertEqual((notice['product_id'], notice['reason'], notice['quantity'], notice['where']),
                         (whole, 'move_short', 1, ['upstairs']))

        # A set found upstairs and taken off again moves the rest.
        with closing(self.connect()) as con:
            from inventory_commands import post_inventory
            post_inventory(con, {'kind': 'receipt', 'business_date': '2026-10-31', 'lines': [
                {'product_id': whole, 'quantity': 1, 'unit': 'set', 'to_location_id': self.at['upstairs'],
                 'to_disposition': 'saleable', 'expected_versions': {'to': self._version(whole, 'upstairs')}}]},
                actor={'sub': 'auth0|admin', 'https://popcore/role': 'admin'}, request_key='found-a-set')
        retried = self.client.post('/api/sales/report-stock/retry', json={'date': '2026-10-31', 'store_code': 'DT'},
                                   headers=self.headers('manager')).get_json()['stock']
        self.assertEqual([line['moving'] for line in retried['lines']], [1])
        self.assertEqual(self.client.get('/api/sales/report-stock', headers=self.headers('manager')).get_json()['notices'], [])

        # Short again on a re-paste with 3 sets; checked means the count already shows it: it never moves later.
        text = report_text('2026-10-31').replace('SA dream 12*2', 'SA dream 12*3')
        self.confirm_and_save(self.body(self.parse(text), keep))
        (notice,) = self.client.get('/api/sales/report-stock', headers=self.headers('manager')).get_json()['notices']
        checked = self.client.post(f"/api/sales/report-stock/{notice['id']}/check", headers=self.headers('manager'))
        self.assertEqual(checked.status_code, 200, checked.get_json())
        with closing(self.connect()) as con:
            from inventory_commands import post_inventory
            post_inventory(con, {'kind': 'receipt', 'business_date': '2026-10-31', 'lines': [
                {'product_id': whole, 'quantity': 1, 'unit': 'set', 'to_location_id': self.at['upstairs'],
                 'to_disposition': 'saleable', 'expected_versions': {'to': self._version(whole, 'upstairs')}}]},
                actor={'sub': 'auth0|admin', 'https://popcore/role': 'admin'}, request_key='another-set')
        retried = self.client.post('/api/sales/report-stock/retry', json={'date': '2026-10-31', 'store_code': 'DT'},
                                   headers=self.headers('manager')).get_json()['stock']
        self.assertEqual([line['moving'] for line in retried['lines']], [0])
        self.assertEqual(self.balance(whole, 'upstairs'), 1)

    def _version(self, product, code):
        with closing(self.connect()) as con:
            row = con.execute("""SELECT version FROM inventory_balances WHERE product_id=? AND location_id=?
                                 AND disposition='saleable'""", (product, self.at[code])).fetchone()
            return row[0] if row else 0

    def test_a_line_that_cannot_be_moved_as_written_refuses_the_whole_report(self):
        self.build_catalogue(('2026-10-31',))
        # The report says 6 per set; POPCORE records 12 for SA dream.
        text = report_text('2026-10-31').replace('SA dream 12*2', 'SA dream 6*2')
        parsed = self.parse(text)
        before = self.balances()
        response = self.submit(self.body(parsed), dry_run=True)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'report_line_invalid')
        self.assertIn('records 12', response.get_json()['error'])
        self.assertIsInstance(response.get_json()['line'], int)
        self.assertEqual(self.balances(), before)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM daily_sales').fetchone()[0], 0)

    def test_a_day_whose_moves_were_posted_by_hand_is_never_moved_again(self):
        self.build_catalogue(('2026-10-31',))
        from inventory_commands import post_inventory
        product = self.product('可乐搪胶')

        def by_hand(source, reason):
            with closing(self.connect()) as con:
                post_inventory(con, {'kind': 'consume', 'business_date': '2026-10-31', 'reason': reason,
                                     'source_type': 'daily_report', 'source_id': source, 'lines': [
                    {'product_id': product, 'quantity': 1, 'unit': 'box', 'from_location_id': self.at['floor'],
                     'from_disposition': 'saleable', 'expected_versions': {'from': self._version(product, 'floor')}}]},
                    actor={'sub': 'auth0|admin', 'https://popcore/role': 'admin'}, request_key=f'daily-report:{source}')

        parsed = self.parse(report_text('2026-10-31'))
        # An owner's fix posted the same way is not a report line.
        by_hand('DT:2026-10-31:sa-summer:move-1', '店主 11-02：楼下重盘')
        self.assertEqual(self.submit(self.body(parsed), dry_run=True).status_code, 200)
        by_hand('DT:2026-10-31:a:move-27', '日报 2026-10-31 出店 可乐搪胶*1')
        refused = self.submit(self.body(parsed), dry_run=True)
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.get_json()['code'], 'report_moves_recorded')
        # Its sales alone still save.
        sales_only = self.body(parsed, keep=lambda item: item['section'] not in STOCK)
        saved = self.submit(sales_only)
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(saved.get_json()['stock']['lines'], [])

    def test_only_managers_paste_and_a_legacy_store_keeps_its_old_report_paths(self):
        self.build_catalogue(('2026-10-31',))
        parsed = self.parse(report_text('2026-10-31'))
        staff = self.client.post('/api/sales/submit_daily_report', json={**self.body(parsed), 'dry_run': True},
                                 headers=self.headers('staff'))
        self.assertEqual(staff.status_code, 403)
        # MK is not live here: 出库入店 is read as its old upstairs → floor 入店, and 卖display stays a note.
        legacy = self.parse(report_text('2026-10-31').replace('2026.10.31 DT', '2026.10.31 MK'), store='MK')
        self.assertEqual((legacy['store'], legacy['stock_mode']), ('MK', 'legacy'))
        sections = {item['section'] for item in legacy['review'] + legacy['failed']}
        self.assertFalse(sections & {'refill', 'display_sold', 'claw_prize'})
        self.assertTrue(legacy['display_sales'])
        # Lines read for a live store are refused at a store in the other mode.
        body = {**self.body(parsed), 'store_code': 'MK', 'dry_run': True}
        moved = self.client.post('/api/sales/submit_daily_report', json=body, headers=self.headers('admin'))
        self.assertEqual(moved.get_json()['code'], 'report_store_mode_changed')

    def test_a_written_v2_is_part_of_the_name_not_a_set_size(self):
        self.assertEqual({key: _parse_token('Smiski拍拍灯v2*1', 'refill')[key] for key in ('raw_name', 'box_size', 'qty')},
                         {'raw_name': 'Smiski拍拍灯v2', 'box_size': 1, 'qty': 1})
        self.assertEqual({key: _parse_token('Sp不想毛绒12*1', 'refill')[key] for key in ('raw_name', 'box_size', 'qty')},
                         {'raw_name': 'Sp不想毛绒', 'box_size': 12, 'qty': 1})
        line = _parse_token('萌粒 快乐开涮:20*2➕19', 'stock_in')
        self.assertEqual((line['raw_name'], line['box_size'], line['qty'], line['loose_qty']), ('萌粒 快乐开涮', 20, 2, 19))

    def test_format_checks_name_set_sizes_and_display_sales_by_their_line(self):
        # Owner, 2026-11-06: flag what may move stock wrongly, and say where.
        self.build_catalogue(('2026-10-31',))
        text = report_text('2026-10-31')
        line = next(number for number, written in enumerate(text.split('\n'), 1) if 'SA dream 12*2' in written)

        def at(parsed, lines):
            return [check for check in parsed['format_checks'] if check['lines'] == lines]

        wrong_size = at(self.parse(text.replace('SA dream 12*2', 'SA dream 6*2')), [line])
        self.assertEqual([(check['level'], check['section']) for check in wrong_size], [('fix', '出库入店')])
        self.assertIn('POPCORE 记', wrong_size[0]['problem'])
        self.assertEqual(wrong_size[0]['text'], ['SA dream 6*2'])
        no_size = at(self.parse(text.replace('SA dream 12*2', 'SA dream*2')), [line])
        self.assertEqual([check['level'] for check in no_size], ['check'])
        self.assertIn('2 个散盒', no_size[0]['problem'])
        self.assertEqual(at(self.parse(text), [line]), [])

        parsed = self.parse('2026.10.31 DT\n卡机汇总：\nSA dream*1\n卖display：\nSA dream*2\nzzz没有这个*1')
        more = at(parsed, [3, 5])
        self.assertEqual([check['level'] for check in more], ['check'])
        self.assertIn('Display sold 2, sales list 1', more[0]['problem'])
        self.assertIn('没有同名的销售', at(parsed, [6])[0]['problem'])
