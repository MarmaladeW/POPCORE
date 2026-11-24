"""异常提醒 Watcher (owner, 2026-11-24: "monitor any abnormal behavior within the app and notify the admin and the manager",
"make sure that it does not leak any of our private data, in any way").

The rules find stock losses, big count gaps, 日报 cash gaps, many floor-short notices and a stuck sync; admins and that
store's managers see them and get a push that says only how many; once a night Claude sees stand-in codes and numbers,
never a name, and POPCORE puts the names back into its notes."""
import importlib.util
import json
import os
import re
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from support import IsolatedApiCase

import watcher
from blueprints import settings as settings_blueprint, watch

ADMIN, DT_MANAGER, MK_MANAGER, STAFF = 'auth0|admin', 'auth0|manager', 'auth0|mkmanager', 'auth0|staff'
MEMBERS = {'admin': {ADMIN}, 'manager': {DT_MANAGER, MK_MANAGER}}
NOW = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)   # 10:00 in Toronto
TODAY = '2026-11-24'
YESTERDAY = '2026-11-23'


def reply(alerts, summary_zh='S1 今晚有一件要看。', summary_en='One thing to look at at S1.', stop='end_turn'):
    body = json.dumps({'summary_zh': summary_zh, 'summary_en': summary_en, 'alerts': alerts}, ensure_ascii=False)
    return SimpleNamespace(stop_reason=stop, model='claude-opus-5-5',
                           content=[SimpleNamespace(type='thinking', thinking=''), SimpleNamespace(type='text', text=body)],
                           usage=SimpleNamespace(input_tokens=1000, output_tokens=2000, cache_creation_input_tokens=0,
                                                 cache_read_input_tokens=0))


class WatcherCase(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(watch.bp)
        self.app.register_blueprint(settings_blueprint.bp)
        with closing(self.connect()) as con:
            self.dt = con.execute("SELECT id FROM stores WHERE code='DT'").fetchone()[0]
            self.mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            self.loc = {(row['store_id'], row['code']): row['id'] for row in con.execute(
                'SELECT id, store_id, code FROM inventory_locations')}
            self.labubu = con.execute(
                """INSERT INTO products(sku, name_cn_en, jizhanming, product_type, stock_form, stock_unit, identity_status)
                   VALUES ('PM0001', 'Labubu Have a Seat', '坐坐派对', '盲盒', 'random_box', 'box', 'verified')""").lastrowid
            self.card = con.execute(
                """INSERT INTO products(sku, name_cn_en, jizhanming, product_type, stock_form, stock_unit, identity_status)
                   VALUES ('PK0001', 'Pikachu card', '皮卡丘吊卡', '吊卡', 'ordinary', 'piece', 'verified')""").lastrowid
            con.executemany('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)',
                            [(ADMIN, self.dt), (ADMIN, self.mk), (DT_MANAGER, self.dt), (MK_MANAGER, self.mk), (STAFF, self.dt)])
            con.executemany('INSERT INTO employees(auth0_id, name) VALUES (?, ?)',
                            [(ADMIN, 'Owner Olive'), (DT_MANAGER, 'Mia Manager'), (MK_MANAGER, 'Kai Manager'),
                             (STAFF, 'Sam Staff')])
            con.execute("INSERT INTO app_settings(key, value) VALUES ('watcher_enabled', '1')")
            # Everyone turned 通知 on for one device (the staff member too, who must never get an alert).
            con.executemany("INSERT INTO push_subscriptions(endpoint, auth0_sub, p256dh, auth) VALUES (?, ?, 'k', 'a')",
                            [(f'https://fcm.googleapis.com/fcm/send/{who}', who) for who in (ADMIN, DT_MANAGER, MK_MANAGER, STAFF)])
            con.commit()
        self.pushes = []
        send = patch('push_notifications.send', side_effect=self._record_push)
        send.start()
        self.addCleanup(send.stop)
        # No website settings unless a test gives them (other test modules can leave WEBSITE_* in the environment).
        website = patch('website_sync.config', return_value=None)
        website.start()
        self.addCleanup(website.stop)
        roles = patch.object(watcher, 'role_members', return_value=MEMBERS)
        self.roles = roles.start()
        self.addCleanup(roles.stop)
        key = patch.dict(os.environ, {'ANTHROPIC_API_KEY': ''})
        key.start()
        self.addCleanup(key.stop)
        # No test reaches Anthropic: a test that wants a reply stands in for ask_claude.
        never = patch.object(watcher, '_client', side_effect=AssertionError('Unexpected Anthropic call in a test'))
        never.start()
        self.addCleanup(never.stop)

    def _record_push(self, con, rows, message):
        self.pushes.append(({row['auth0_sub'] for row in rows}, message))
        return len(rows), 0

    # ── helpers ──
    def rules(self, now=NOW):
        with closing(self.connect()) as con:
            return watcher.run_rules(con, now)

    def review(self, now=NOW):
        with closing(self.connect()) as con:
            return watcher.run_review(con, now)

    def alerts(self, **where):
        clause = ' AND '.join(f'{name}=?' for name in where) or '1=1'
        with closing(self.connect()) as con:
            return [dict(row) for row in con.execute(f'SELECT * FROM watch_alerts WHERE {clause} ORDER BY id',
                                                     tuple(where.values()))]

    def sql(self, statement, params=()):
        with closing(self.connect()) as con:
            cursor = con.execute(statement, params)
            con.commit()
            return cursor.lastrowid

    def loss(self, reason, quantity, store=None, day=YESTERDAY, product=None):
        store = store or self.dt
        return self.sql("""INSERT INTO stock_losses(store_id, location_id, product_id, quantity, unit, reason, note,
                                                    business_date, actor_sub)
                           VALUES (?, ?, ?, ?, 'box', ?, 'Tall man in a red coat, 4pm', ?, ?)""",
                        (store, self.loc[(store, 'floor')], product or self.labubu, quantity, reason, day, ADMIN))

    def counted(self, store, product, moves, day=YESTERDAY):
        """A saved count's correction: moves = [(place, signed quantity)]."""
        with closing(self.connect()) as con:
            doc = con.execute("""INSERT INTO inventory_documents(kind, request_key, payload_hash, source_type, source_id,
                                                                  actor_sub, business_date, status)
                                 VALUES ('correction', ?, 'x', 'direct_count', ?, ?, ?, 'building')""",
                              (f'count-{product}-{day}-{len(moves)}-{moves}', f'{product}-{day}-{moves}', ADMIN, day)).lastrowid
            for line, (place, quantity) in enumerate(moves, 1):
                location = self.loc[(store, place)]
                con.execute("""INSERT INTO inventory_document_lines(document_id, line_no, product_id, native_unit, quantity,
                                   from_location_id, from_disposition, to_location_id, to_disposition)
                               VALUES (?, ?, ?, 'box', ?, ?, ?, ?, ?)""",
                            (doc, line, product, abs(quantity), location if quantity < 0 else None,
                             'saleable' if quantity < 0 else None, location if quantity > 0 else None,
                             'saleable' if quantity > 0 else None))
                con.execute("""INSERT INTO inventory_movements(document_id, line_no, product_id, location_id, disposition, quantity)
                               VALUES (?, ?, ?, ?, 'saleable', ?)""", (doc, line, product, location, quantity))
            con.execute("UPDATE inventory_documents SET status='posted', posted_at=datetime('now') WHERE id=?", (doc,))
            con.commit()

    def report_cash(self, store_code, day, actual, expected):
        self.sql('INSERT INTO daily_report_metadata(date, store, cash_actual_cents, cash_expected_cents) VALUES (?, ?, ?, ?)',
                 (day, store_code, actual, expected))

    def notices(self, store, day, count, reason='not_enough_on_floor'):
        products = [self.sql("INSERT INTO products(sku, name_cn_en, jizhanming) VALUES (?, ?, ?)", (f'N{store}{day}{i}{reason}', f'Item {i}', f'款{i}'))
                    for i in range(count)]
        return [self.sql("""INSERT INTO report_stock_issues(store_id, business_date, product_id, reason, quantity)
                            VALUES (?, ?, ?, ?, 1)""", (store, day, product, reason)) for product in products]


class RuleTests(WatcherCase):
    def test_losses_stolen_and_missing_are_high_one_broken_piece_is_not_an_alert(self):
        stolen = self.loss('stolen', 1)
        self.loss('broken', 1)
        self.loss('broken', 3)
        self.assertEqual(self.rules(), 2)
        found = {(a['subject_key'], a['severity']) for a in self.alerts()}
        self.assertIn((f'loss:{stolen}', 'high'), found)
        self.assertEqual(sorted(severity for _, severity in found), ['high', 'normal'])
        # The stolen one is pushed at once, to the admin and DT's manager; the broken one waits for the review.
        self.assertEqual(len(self.pushes), 2)
        self.assertEqual({next(iter(people)) for people, _ in self.pushes}, {ADMIN, DT_MANAGER})
        # An undone loss settles its alert on the next pass; a second pass finds nothing new.
        self.sql("UPDATE stock_losses SET undone_at=datetime('now'), undone_by=? WHERE id=?", (ADMIN, stolen))
        self.assertEqual(self.rules(), 0)
        self.assertEqual(self.alerts(subject_key=f'loss:{stolen}')[0]['status'], 'resolved')

    def test_count_gaps_net_per_store_product_and_day(self):
        self.counted(self.dt, self.labubu, [('floor', -12)])
        self.counted(self.dt, self.card, [('floor', -30)])
        self.counted(self.mk, self.labubu, [('floor', 20)])                         # +20: not big enough
        self.counted(self.mk, self.card, [('floor', -15), ('warehouse', 15)])        # moved to the warehouse: no gap
        self.rules()
        gaps = {(a['store_id'], a['product_id']): a for a in self.alerts(kind='count_gap')}
        self.assertEqual(set(gaps), {(self.dt, self.labubu), (self.dt, self.card)})
        self.assertEqual(gaps[(self.dt, self.labubu)]['severity'], 'normal')
        self.assertEqual(gaps[(self.dt, self.card)]['severity'], 'high')
        self.assertEqual(json.loads(gaps[(self.dt, self.labubu)]['facts']), {'net': -12, 'usual': None, 'counts_before': 0})

    def test_count_gap_knows_the_products_usual_gap(self):
        for days_back, net in ((40, -8), (30, -12), (20, -10)):
            self.counted(self.dt, self.labubu, [('floor', net)], day=(NOW.date() - timedelta(days=days_back)).isoformat())
        self.counted(self.dt, self.labubu, [('floor', 35)])
        self.rules()
        [alert] = self.alerts(kind='count_gap')
        self.assertEqual(json.loads(alert['facts']), {'net': 35, 'usual': 10, 'counts_before': 3})

    def test_cash_gap_over_twenty_dollars(self):
        self.report_cash('DT', YESTERDAY, 57500, 60150)   # $26.50 short
        self.report_cash('MK', YESTERDAY, 30000, 36000)   # $60 short: high
        self.report_cash('DT', TODAY, 60000, 60500)       # $5: fine
        self.rules()
        cash = {a['store_id']: a for a in self.alerts(kind='cash_gap')}
        self.assertEqual(set(cash), {self.dt, self.mk})
        self.assertEqual(cash[self.dt]['severity'], 'normal')
        self.assertEqual(cash[self.mk]['severity'], 'high')
        self.assertEqual(json.loads(cash[self.dt]['facts']), {'gap_cents': -2650, 'gap_pct': -4.4, 'gaps_30d': 0})

    def test_many_floor_short_notices_from_one_report(self):
        ids = self.notices(self.dt, YESTERDAY, 4) + self.notices(self.dt, YESTERDAY, 1, 'move_short')
        self.notices(self.mk, YESTERDAY, 4)
        self.rules()
        [alert] = self.alerts(kind='floor_short')
        self.assertEqual(alert['store_id'], self.dt)
        self.assertEqual(json.loads(alert['facts'])['total'], 5)
        # A manager checks one notice: 4 left, the alert settles.
        self.sql("UPDATE report_stock_issues SET status='checked' WHERE id=?", (ids[0],))
        self.rules()
        self.assertEqual(self.alerts(kind='floor_short')[0]['status'], 'resolved')

    def test_old_findings_are_outside_the_window(self):
        self.loss('stolen', 2, day='2026-11-10')
        self.report_cash('DT', '2026-11-10', 0, 90000)
        self.assertEqual(self.rules(), 0)

    def test_a_failing_sync_waits_an_hour_then_is_an_admin_alert(self):
        def state(error, ran):
            with closing(self.connect()) as con:
                con.execute("DELETE FROM website_state")
                con.execute("INSERT INTO website_state(key, value) VALUES ('last_run', ?)", (ran.strftime('%Y-%m-%dT%H:%M:%S'),))
                if error:
                    con.execute("""INSERT INTO website_state(key, value) VALUES ('last_error', '{"code": "website_unreachable"}')""")
                con.commit()
        with patch('website_sync.config', return_value={'url': 'https://popcore.ca'}):
            state(True, NOW)
            self.assertEqual(self.rules(NOW), 0)
            self.assertEqual(self.alerts(kind='sync_stuck')[0]['status'], 'pending')
            later = NOW + timedelta(minutes=61)
            state(True, later)
            self.assertEqual(self.rules(later), 1)
            [alert] = self.alerts(kind='sync_stuck')
            self.assertEqual((alert['status'], alert['store_id']), ('open', None))
            self.assertEqual(json.loads(alert['facts']), {'system': 'website', 'problem': 'failing', 'minutes': 61})
            self.assertEqual([people for people, _ in self.pushes], [{ADMIN}])   # no store: admins only
            fixed = later + timedelta(minutes=30)
            state(False, fixed)
            self.rules(fixed)
            self.assertEqual(self.alerts(kind='sync_stuck')[0]['status'], 'resolved')
            # Not run for over an hour is an alert at once.
            state(False, fixed - timedelta(minutes=90))
            self.assertEqual(self.rules(fixed), 1)
            self.assertEqual(json.loads(self.alerts(kind='sync_stuck')[0]['facts'])['problem'], 'not_running')

    def test_no_website_settings_no_sync_alert(self):
        with patch('website_sync.config', return_value=None):
            self.assertEqual(self.rules(), 0)

    def test_switched_off_does_nothing(self):
        self.sql("UPDATE app_settings SET value='0' WHERE key='watcher_enabled'")
        self.loss('stolen', 5)
        self.assertEqual(self.rules(), 0)
        with patch.object(watcher, 'connect', side_effect=self.connect), patch.object(watcher, 'run_rules') as rules:
            watcher.run_due(NOW)
        rules.assert_not_called()
        self.assertEqual(self.alerts(), [])
        self.assertEqual(self.pushes, [])

    def test_run_due_runs_rules_hourly_and_one_review_a_day(self):
        self.loss('broken', 4)
        with patch.object(watcher, 'connect', side_effect=self.connect):
            watcher.run_due(NOW - timedelta(hours=4))          # 06:00 Toronto: rules only
            with closing(self.connect()) as con:
                kinds = [row[0] for row in con.execute('SELECT kind FROM watch_runs ORDER BY id')]
            self.assertEqual(kinds, ['rules'])
            watcher.run_due(NOW - timedelta(hours=3, minutes=50))   # ten minutes later: nothing
            watcher.run_due(NOW)                                    # 10:00: rules + the day's review
            watcher.run_due(NOW + timedelta(minutes=10))            # the review is done for the day
        with closing(self.connect()) as con:
            kinds = [row[0] for row in con.execute('SELECT kind FROM watch_runs ORDER BY id')]
            outcome = con.execute("SELECT outcome FROM watch_runs WHERE kind='review'").fetchone()[0]
        self.assertEqual(kinds, ['rules', 'rules', 'review'])
        self.assertEqual(outcome, 'no_key')


class PrivacyTests(WatcherCase):
    """Only stand-in codes and numbers leave POPCORE."""

    def make_alerts(self):
        self.loss('stolen', 2)
        self.loss('broken', 6, store=self.mk, product=self.card)
        self.counted(self.dt, self.card, [('floor', -14)])
        self.report_cash('MK', YESTERDAY, 50000, 53000)
        self.notices(self.dt, YESTERDAY, 6)
        self.rules()

    def sent_payload(self):
        with closing(self.connect()) as con:
            alerts = con.execute("SELECT * FROM watch_alerts WHERE status='open' ORDER BY id").fetchall()
            sent, kept = watcher.build_payload(con, alerts, NOW.date())
        return json.dumps(sent, ensure_ascii=False), sent, kept

    def test_payload_has_only_codes_numbers_and_fixed_words(self):
        self.make_alerts()
        text, sent, kept = self.sent_payload()
        self.assertEqual(len(sent['alerts']), 5)
        private = ['DT', 'MK', 'Downtown', 'Markham', 'PM0001', 'PK0001', 'Labubu', '坐坐派对', '皮卡丘', 'Pikachu',
                   'auth0', 'Olive', 'Mia', 'Tall man', 'red coat', '2026', 'Item', '款']
        for word in private:
            self.assertNotIn(word, text, word)
        allowed_words = set().union(*watcher.ENUMS.values(), watcher.KINDS, {'high', 'normal'}, watcher.FLOOR_REASONS)
        for alert in sent['alerts']:
            for key, value in alert.items():
                if key in ('id', 'store', 'product'):
                    self.assertRegex(value, r'^[ASP]\d+$')
                elif key == 'by_reason':
                    self.assertTrue(set(value) <= set(watcher.FLOOR_REASONS) and all(type(n) is int for n in value.values()))
                elif isinstance(value, str):
                    self.assertIn(value, allowed_words, (key, value))
                else:
                    self.assertTrue(value is None or type(value) in (int, float), (key, value))
        # The codes' meaning stays in POPCORE.
        self.assertEqual(set(kept['stores'].values()), {self.dt, self.mk})

    def test_review_restores_names_and_never_lowers_a_high_alert(self):
        self.make_alerts()
        os.environ['ANTHROPIC_API_KEY'] = 'sk-test-not-real'
        _, sent, kept = self.sent_payload()
        by_kind = {item['kind'] + ('-' + item.get('reason', '') if item['kind'] == 'stock_loss' else ''): item for item in sent['alerts']}
        stolen, gap = by_kind['stock_loss-stolen'], by_kind['count_gap']
        answers = [
            {'id': stolen['id'], 'verdict': 'low', 'note_zh': f"{stolen['store']} 的 {stolen['product']} 被偷 2 盒，A1 请看监控。",
             'note_en': f"{stolen['product']} stolen at {stolen['store']}; check S9 too."},
            {'id': gap['id'], 'verdict': 'low', 'note_zh': '和平时差不多。', 'note_en': 'Close to the usual gap.'},
            {'id': 'A99', 'verdict': 'keep', 'note_zh': 'x', 'note_en': 'x'},
        ]
        calls = []
        with patch.object(watcher, 'ask_claude', side_effect=lambda payload: calls.append(payload) or reply(answers)):
            self.assertEqual(self.review(), 'ok')
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0]), sent)
        loss = self.alerts(kind='stock_loss', severity='high')[0]
        self.assertEqual(loss['verdict'], 'keep')                      # Claude cannot hide a high alert
        self.assertEqual(loss['note_zh'], 'DT 的 坐坐派对 被偷 2 盒， 请看监控。')
        self.assertEqual(loss['note_en'], '坐坐派对 stolen at DT; check ? too.')
        self.assertEqual(self.alerts(kind='count_gap')[0]['verdict'], 'low')
        with closing(self.connect()) as con:
            run = con.execute("SELECT * FROM watch_runs WHERE kind='review'").fetchone()
        self.assertEqual(run['sent'], calls[0])                           # what went out is on record
        self.assertEqual(run['summary_zh'], 'DT 今晚有一件要看。')
        self.assertAlmostEqual(run['cost_usd'], 1000 * 4 / 1e6 + 2000 * 20 / 1e6)
        # After the review the rest are pushed, not the one Claude marked low; the push names nothing.
        bodies = [message['body'] for _, message in self.pushes]
        self.assertTrue(bodies)
        for body in bodies:
            self.assertRegex(body, r'^\d+ 条新提醒 · \d+ new\. 打开 POPCORE 查看 Open POPCORE to see\.$')
        self.assertIsNone(self.alerts(kind='count_gap')[0]['pushed_at'])

    def test_no_key_claude_off_or_cap_means_no_call_and_the_alerts_still_go_out(self):
        self.make_alerts()
        with patch.object(watcher, 'ask_claude') as ask:
            self.assertEqual(self.review(), 'no_key')
            ask.assert_not_called()
        self.assertTrue(all(a['pushed_at'] for a in self.alerts()))
        self.loss('missing', 1, day=TODAY)
        self.rules()
        os.environ['ANTHROPIC_API_KEY'] = 'sk-test-not-real'
        self.sql("INSERT INTO app_settings(key, value) VALUES ('watcher_daily_cap_usd', '0.10')")
        with patch.object(watcher, 'ask_claude') as ask:
            self.assertEqual(self.review(), 'cap')
            ask.assert_not_called()
        self.loss('missing', 2, day=TODAY)
        self.rules()
        self.sql("INSERT INTO app_settings(key, value) VALUES ('watcher_claude', '0')")
        with patch.object(watcher, 'ask_claude') as ask:
            self.assertEqual(self.review(), 'claude_off')
            ask.assert_not_called()

    def test_cap_counts_what_was_spent_today(self):
        self.make_alerts()
        os.environ['ANTHROPIC_API_KEY'] = 'sk-test-not-real'
        self.sql("INSERT INTO watch_runs(kind, business_date, outcome, cost_usd) VALUES ('review', ?, 'ok', 0.40)", (TODAY,))
        with patch.object(watcher, 'ask_claude') as ask:
            self.assertEqual(self.review(), 'cap')        # 0.40 spent + the most this call could cost > 0.50
            ask.assert_not_called()

    def test_a_failed_call_keeps_only_the_errors_kind(self):
        self.make_alerts()
        os.environ['ANTHROPIC_API_KEY'] = 'sk-test-not-real'
        with patch.object(watcher, 'ask_claude', side_effect=RuntimeError('sk-test-not-real leaked in a message')):
            self.assertEqual(self.review(), 'failed')
        with closing(self.connect()) as con:
            run = con.execute("SELECT * FROM watch_runs WHERE kind='review'").fetchone()
        self.assertEqual(run['error'], 'RuntimeError')
        self.assertGreater(run['cost_usd'], 0)                 # counted against the cap, to be safe
        self.assertTrue(all(a['reviewed_at'] and a['pushed_at'] for a in self.alerts()))

    def test_refused_or_cut_off_reply_adds_no_notes(self):
        self.make_alerts()
        os.environ['ANTHROPIC_API_KEY'] = 'sk-test-not-real'
        with patch.object(watcher, 'ask_claude', return_value=reply([], stop='refusal')):
            self.assertEqual(self.review(), 'refused')
        self.assertTrue(all(a['note_zh'] is None for a in self.alerts()))

    def test_the_call_uses_opus_with_fallbacks_and_a_json_schema(self):
        captured = {}

        class Messages:
            def create(self, **kwargs):
                captured.update(kwargs)
                return reply([])

        fake = SimpleNamespace(beta=SimpleNamespace(messages=Messages()))
        with patch.object(watcher, '_client', return_value=fake):
            watcher.ask_claude('{"alerts":[]}')
        self.assertEqual(captured['model'], 'claude-opus-5-5')
        self.assertEqual(captured['fallbacks'], 'default')
        self.assertEqual(captured['betas'], ['server-side-fallback-2026-07-01'])
        self.assertEqual(captured['output_config']['format']['schema'], watcher.REPLY_SCHEMA)
        self.assertEqual(captured['messages'], [{'role': 'user', 'content': '{"alerts":[]}'}])
        self.assertNotIn('temperature', captured)
        self.assertNotIn('thinking', captured)

    @unittest.skipUnless(importlib.util.find_spec('anthropic'), 'the anthropic SDK is not installed')
    def test_the_sdk_sends_the_prompt_and_the_codes_and_nothing_else(self):
        """The real SDK, through a stand-in transport (no network): the whole HTTP request that would leave."""
        import anthropic
        import httpx2
        seen = []

        def answer(request):
            seen.append(request)
            return httpx2.Response(200, json={
                'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'model': 'claude-opus-5-5',
                'content': [{'type': 'text', 'text': '{"summary_zh": "", "summary_en": "", "alerts": []}'}],
                'stop_reason': 'end_turn', 'stop_sequence': None, 'usage': {'input_tokens': 10, 'output_tokens': 5}})

        client = anthropic.Anthropic(api_key='sk-test-not-real', max_retries=0,
                                     http_client=httpx2.Client(transport=httpx2.MockTransport(answer)))
        self.make_alerts()
        text, _, _ = self.sent_payload()
        with patch.object(watcher, '_client', return_value=client):
            response = watcher.ask_claude(text)
        [request] = seen
        self.assertEqual(str(request.url), 'https://api.anthropic.com/v1/messages')
        self.assertEqual(request.headers['anthropic-beta'], 'server-side-fallback-2026-07-01')
        body = json.loads(request.content)
        self.assertEqual(set(body), {'model', 'max_tokens', 'fallbacks', 'system', 'output_config', 'messages'})
        self.assertEqual(body['messages'], [{'role': 'user', 'content': text}])
        self.assertEqual(body['system'], watcher.SYSTEM)
        self.assertEqual(watcher._reply(response)[1], 'ok')

    def test_the_prompt_names_nothing_private(self):
        for word in ('DT', 'MK', 'Downtown', 'Markham', 'POP MART', 'popcore.ca', 'popcore.store'):
            self.assertNotIn(word, watcher.SYSTEM)


class AccessTests(WatcherCase):
    def setUp(self):
        super().setUp()
        self.loss('stolen', 1)                                      # DT, high
        self.report_cash('MK', YESTERDAY, 50000, 53000)              # MK
        self.rules()
        self.sql("""INSERT INTO watch_alerts(kind, subject_key, facts, severity, status)
                    VALUES ('sync_stuck', 'sync:dex', '{"system": "dex", "problem": "failing", "minutes": 75}', 'high', 'open')""")
        self.headers_for = {
            'admin': {'Authorization': 'Bearer admin:admin'},
            'dt': {'Authorization': 'Bearer manager:manager'},
            'mk': {'Authorization': 'Bearer manager:mkmanager'},
            'staff': {'Authorization': 'Bearer staff:staff'},
        }

    def get(self, who):
        return self.client.get('/api/watch', headers=self.headers_for[who])

    def test_staff_cannot_see_alerts(self):
        self.assertEqual(self.get('staff').status_code, 403)

    def test_managers_see_their_stores_admins_see_all(self):
        dt = self.get('dt').get_json()
        self.assertEqual([(a['kind'], a['store']) for a in dt['alerts']], [('stock_loss', 'DT')])
        self.assertEqual(dt['alerts'][0]['title_zh'], '坐坐派对 损耗 1盒 · 被偷')
        self.assertNotIn('status', dt)
        mk = self.get('mk').get_json()
        self.assertEqual([(a['kind'], a['store']) for a in mk['alerts']], [('cash_gap', 'MK')])
        admin = self.get('admin').get_json()
        self.assertEqual({a['kind'] for a in admin['alerts']}, {'stock_loss', 'cash_gap', 'sync_stuck'})
        self.assertEqual(admin['status']['model'], 'claude-opus-5-5')
        self.assertFalse(admin['status']['has_key'])

    def test_check_an_alert(self):
        dt_alert = self.get('dt').get_json()['alerts'][0]['id']
        refused = self.client.post(f'/api/watch/{dt_alert}/check', headers=self.headers_for['mk'])
        self.assertEqual(refused.status_code, 404)
        sync = next(a['id'] for a in self.get('admin').get_json()['alerts'] if a['kind'] == 'sync_stuck')
        self.assertEqual(self.client.post(f'/api/watch/{sync}/check', headers=self.headers_for['dt']).status_code, 404)
        self.assertEqual(self.client.post(f'/api/watch/{dt_alert}/check', headers=self.headers_for['dt']).status_code, 200)
        dt = self.get('dt').get_json()
        self.assertEqual(dt['alerts'], [])
        self.assertEqual((dt['recent'][0]['id'], dt['recent'][0]['checked_by']), (dt_alert, 'Mia Manager'))

    def test_check_now_is_for_admins_and_needs_the_switch(self):
        self.assertEqual(self.client.post('/api/watch/run', headers=self.headers_for['dt']).status_code, 403)
        self.sql("UPDATE app_settings SET value='0' WHERE key='watcher_enabled'")
        self.assertEqual(self.client.post('/api/watch/run', headers=self.headers_for['admin']).status_code, 409)
        self.sql("UPDATE app_settings SET value='1' WHERE key='watcher_enabled'")
        response = self.client.post('/api/watch/run', headers=self.headers_for['admin'])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['status']['review_requested'])   # no key: nothing to ask Claude

    def test_manager_home_shows_the_alerts_of_the_chosen_store(self):
        today = self.client.get('/api/today', query_string={'store_code': 'DT'}, headers=self.headers_for['dt']).get_json()
        self.assertEqual([row['label'] for row in today['sections']['watch']['rows']],
                         ['坐坐派对 损耗 1盒 · 被偷 · 坐坐派对: 1 box written off as stolen'])
        admin = self.client.get('/api/today', query_string={'store_code': 'DT'}, headers=self.headers_for['admin']).get_json()
        self.assertEqual({row['store_id'] for row in admin['sections']['watch']['rows']}, {self.dt, None})
        staff = self.client.get('/api/today', query_string={'store_code': 'DT'}, headers=self.headers_for['staff']).get_json()
        self.assertNotIn('watch', staff['sections'])

    def test_settings_switch_cap_and_time(self):
        def put(body):
            return self.client.put('/api/settings', json=body, headers=self.headers_for['admin'])
        self.assertEqual(put({'watcher_daily_cap_usd': '9'}).status_code, 400)
        self.assertEqual(put({'watcher_enabled': 'yes'}).status_code, 400)
        self.assertEqual(put({'watcher_review_time': '7am'}).status_code, 400)
        self.assertEqual(put({'watcher_enabled': False, 'watcher_daily_cap_usd': '0.5', 'watcher_review_time': '06:30'}).status_code, 200)
        self.assertEqual(self.client.put('/api/settings', json={'watcher_enabled': '1'}, headers=self.headers_for['dt']).status_code, 403)
        values = self.client.get('/api/settings', headers=self.headers_for['admin']).get_json()
        self.assertEqual((values['watcher_enabled'], values['watcher_daily_cap_usd'], values['watcher_review_time']),
                         ('0', '0.50', '06:30'))


class PushTests(WatcherCase):
    def test_store_alert_goes_to_admins_and_that_stores_managers_only(self):
        self.sql("UPDATE employees SET is_active=0 WHERE auth0_id=?", (DT_MANAGER,))
        self.loss('stolen', 1)
        self.rules()
        self.assertEqual([people for people, _ in self.pushes], [{ADMIN}])   # a removed manager gets nothing

    def test_auth0_out_of_reach_keeps_the_push_for_later(self):
        self.roles.return_value = None
        self.loss('stolen', 1)
        self.rules()
        self.assertEqual(self.pushes, [])
        self.assertIsNone(self.alerts()[0]['pushed_at'])
        self.roles.return_value = MEMBERS
        self.rules()
        self.assertEqual(len(self.pushes), 2)

    def test_one_message_per_person_with_their_count(self):
        self.loss('stolen', 1)
        self.loss('missing', 1)
        self.loss('stolen', 1, store=self.mk)
        self.rules()
        counts = {next(iter(people)): message['body'].split(' ')[0] for people, message in self.pushes}
        self.assertEqual(counts, {ADMIN: '3', DT_MANAGER: '2', MK_MANAGER: '1'})


class RestoreTests(WatcherCase):
    def test_codes_next_to_chinese_are_restored_and_unknown_ones_hidden(self):
        names = {'S1': 'DT', 'P2': '坐坐派对'}
        self.assertEqual(watcher.restore('S1店P2少了，S3不明', names, 100), 'DT店坐坐派对少了，?不明')
        self.assertEqual(watcher.restore('SP2 is a word, P22x too', names, 100), 'SP2 is a word, P22x too')
        self.assertIsNone(watcher.restore(None, names, 100))
        self.assertEqual(len(watcher.restore('长' * 500, names, 200)), 200)
        self.assertFalse(re.search(r'\bA\d', watcher.restore('see A12 and A3', names, 100)))
