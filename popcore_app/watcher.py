"""异常提醒 Watcher (owner, 2026-11-24: "I wish to add a small claude api, to monitor any abnormal behavior within the app
and notify the admin and the manager" and "make sure that it does not leak any of our private data, in any way").

POPCORE's own rules do the finding, every hour, inside POPCORE (the owner's picks):
- stock_loss   a 损耗 written off as stolen, missing or other, or 3+ broken at once
- count_gap    a stock count 10+ fewer than the book, or 30+ more, for one product at one store on one day
- cash_gap     a 日报's 现金 实收 and 应收 more than $20 apart
- floor_short  one 日报 leaving 5+ open stock notices (楼下不够, 还没盘点, 库存没移完)
- sync_stuck   the popcore.ca stock sync or the Dex sync failing for an hour, or not run for an hour
Each finding is one alert. A store's alert is for admins and that store's managers; a sync alert, which has no store,
is for admins. High ones (stolen/missing, a big shortfall, $50+ cash, a stuck sync) are pushed to phones at once.

Once a night Claude Opus 5.5 reads the new alerts and, for each, says keep or low (a borderline one that looks normal)
and writes a one-line note in Chinese and English; then the rest are pushed. Claude can never hide a high alert.

What leaves POPCORE, and only for that nightly review: stand-in codes (S1 for a store, P3 for a product, A2 for an
alert) and the numbers in SAFE_FACTS. Never a store, product or person name, a sign-in, a note, a date or any free
text. POPCORE swaps the real names back into Claude's notes itself. Every run keeps exactly what was sent (watch_runs.
sent), so an admin can see it. A phone push says only how many new alerts there are; the details stay in POPCORE.

Off until an admin turns it on in 设置 (watcher_enabled). Without ANTHROPIC_API_KEY, with the Claude switch off or past
the daily cap, the rules still run and the alerts still come, just without Claude's note. The key is read from the
environment when the call is made and is never stored, logged or shown.
"""
import json
import logging
import os
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db

log = logging.getLogger(__name__)
TORONTO = ZoneInfo('America/Toronto')

# ── Settings (设置, admin only; blueprints/settings.py lists them) ──────────
SETTINGS = {
    'watcher_enabled': '0',          # the whole watcher: rules, alerts and pushes
    'watcher_claude': '1',           # the nightly Claude review (needs ANTHROPIC_API_KEY on the server)
    'watcher_daily_cap_usd': '0.50', # the most the Claude review may cost in one Toronto day
    'watcher_review_time': '07:00',  # Toronto time of the nightly review, after the night's reports
}
CAP_MAX_USD = 5

# ── Rules ─────────────────────────────────────────────────────────────────────
WINDOW_DAYS = 3            # the rules look at the last 3 business days; older alerts stay until checked
RULES_EVERY_MINUTES = 60
LOSS_BROKEN_MIN = 3
COUNT_SHORT_MIN = 10
COUNT_SHORT_HIGH = 25
COUNT_OVER_MIN = 30
CASH_GAP_CENTS = 2000
CASH_GAP_HIGH_CENTS = 5000
FLOOR_SHORT_MIN = 5
FLOOR_REASONS = ('not_enough_on_floor', 'not_counted', 'move_short')
SYNC_STUCK_MINUTES = 60
KINDS = ('stock_loss', 'count_gap', 'cash_gap', 'floor_short', 'sync_stuck')

# ── Claude ────────────────────────────────────────────────────────────────────
MODEL = 'claude-opus-5-5'
EFFORT = 'medium'
MAX_TOKENS = 6000
REVIEW_MAX = 40
# US$ per million tokens (input, output). The review is priced by the model that answered (a refused request may be
# answered by a fallback model); an unknown model, and the cap check before a call, use the dearest.
PRICES = {'claude-opus-5-5': (4.0, 20.0), 'claude-opus-5': (5.0, 25.0), 'claude-opus-4-8': (5.0, 25.0)}
DEAREST = (5.0, 25.0)
NOTE_MAX = 200
SUMMARY_MAX = 400

REPLY_SCHEMA = {
    'type': 'object',
    'properties': {
        'summary_zh': {'type': 'string'},
        'summary_en': {'type': 'string'},
        'alerts': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {
                'id': {'type': 'string'},
                'verdict': {'type': 'string', 'enum': ['keep', 'low']},
                'note_zh': {'type': 'string'},
                'note_en': {'type': 'string'},
            },
            'required': ['id', 'verdict', 'note_zh', 'note_en'],
            'additionalProperties': False,
        }},
    },
    'required': ['summary_zh', 'summary_en', 'alerts'],
    'additionalProperties': False,
}

SYSTEM = """You check alerts that POPCORE raised. POPCORE is the stock and sales app of a small chain of toy stores (blind boxes, figures). Its own rules already found each alert; you judge them and write short notes for the store managers.

Every store is a stand-in code (S1, S2 ...) and every product one too (P1, P2 ...). You never see names and must not guess them. Use the codes exactly as given; POPCORE puts the real names back.

Alert kinds and their numbers:
- stock_loss: a manager wrote stock off. quantity, unit (box/set/piece), reason (stolen/missing/broken/other), losses_30d = other write-offs at that store in the 30 days before.
- count_gap: a stock count found a different number than the book. net = counted minus book (negative = fewer than expected); usual = this product's typical absolute gap at that store in earlier counts (null when never counted there before); counts_before = how many earlier count days.
- cash_gap: a daily report's cash taken differs from the cash expected. gap_dollars (negative = short), gap_pct = the gap as a percentage of expected, gaps_30d = other days with a gap this big in the 30 days before.
- floor_short: one daily report left many stock notices. total, by_reason (not_enough_on_floor = sold more than the floor had; not_counted = sold something never counted there; move_short = a stock line could not move in full).
- sync_stuck: the sync to the shop website (website) or to the product book (dex) is failing or has not run; minutes = for how long.
days_ago: 0 = today, 1 = yesterday (store time). severity high = always shown and already sent to phones.

For each alert:
- verdict "keep" when a manager should look at it; "low" when it looks like a normal pattern (for example a count gap close to that product's usual gap, or a big surplus on a product's first count). Never "low" for severity high. When unsure, keep.
- note_zh (Simplified Chinese) and note_en (English): one short sentence each, at most 30 words, saying why it matters or why it looks normal and what to check. Use only the numbers given.

Then summary_zh and summary_en: at most two sentences on all alerts together, the most important first. Do not mention the alert ids (A1 ...)."""

# The only facts Claude sees, per kind: numbers and fixed words, checked by type before anything is sent.
ENUMS = {'unit': {'box', 'set', 'piece'}, 'reason': {'stolen', 'missing', 'broken', 'other'},
         'system': {'website', 'dex'}, 'problem': {'failing', 'not_running'}}
SAFE_FACTS = {
    'stock_loss': ('quantity', 'unit', 'reason', 'losses_30d'),
    'count_gap': ('net', 'usual', 'counts_before'),
    'cash_gap': ('gap_dollars', 'gap_pct', 'gaps_30d'),
    'floor_short': ('total', 'by_reason'),
    'sync_stuck': ('system', 'problem', 'minutes'),
}

UNIT_ZH = {'box': '盒', 'set': '端', 'piece': '个'}
REASON_TEXT = {'stolen': ('被偷', 'stolen'), 'missing': ('丢失', 'missing'), 'broken': ('损坏', 'broken'),
               'other': ('其他', 'other')}
SYSTEM_TEXT = {'website': ('网站库存同步', 'Website stock sync'), 'dex': ('图鉴同步', 'Dex sync')}


class WatcherError(ValueError):
    pass


# ── Time ─────────────────────────────────────────────────────────────────────

def _utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _sql(moment):
    """The form SQLite's datetime('now') writes, in UTC."""
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _parse(text):
    try:
        moment = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _today(now):
    return now.astimezone(TORONTO).date()


def _minutes(later, earlier):
    return max(0, int((later - earlier).total_seconds() // 60))


# ── Settings ─────────────────────────────────────────────────────────────────

def settings(con):
    result = dict(SETTINGS)
    for row in con.execute("SELECT key, value FROM app_settings WHERE key LIKE 'watcher_%'"):
        if row[0] in result:
            result[row[0]] = row[1]
    return result


def check_setting(key, value):
    """(clean value, None), or (None, why not) for one watcher setting from 设置."""
    text = str(value).strip() if isinstance(value, (str, int, float, bool)) else ''
    if key in ('watcher_enabled', 'watcher_claude'):
        if value is True or text in ('1', 'true'):
            return '1', None
        if value is False or text in ('0', 'false'):
            return '0', None
        return None, f'{key} must be 0 or 1'
    if key == 'watcher_daily_cap_usd':
        if not re.fullmatch(r'\d{1,2}(?:\.\d{1,2})?', text) or not 0 <= float(text) <= CAP_MAX_USD:
            return None, f'watcher_daily_cap_usd must be a dollar amount from 0 to {CAP_MAX_USD}'
        return f'{float(text):.2f}', None
    if key == 'watcher_review_time':
        if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', text):
            return None, 'watcher_review_time must be HH:MM'
        return text, None
    return None, f'Unknown watcher setting {key}'


def _flag(con, key, value):
    """Internal state beside the settings (not shown in 设置)."""
    if value is None:
        con.execute('DELETE FROM app_settings WHERE key=?', (key,))
    else:
        con.execute('INSERT INTO app_settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (key, value))


def has_key():
    return bool(os.environ.get('ANTHROPIC_API_KEY', '').strip())


# ── The rules ────────────────────────────────────────────────────────────────

def _losses(con, since):
    for row in con.execute(
            """SELECT l.id, l.store_id, l.product_id, l.quantity, l.unit, l.reason, l.business_date,
                      (SELECT COUNT(*) FROM stock_losses o WHERE o.store_id=l.store_id AND o.undone_at IS NULL
                          AND o.id != l.id AND o.business_date >= date(l.business_date, '-30 days')
                          AND o.business_date <= l.business_date) AS others
               FROM stock_losses l WHERE l.business_date >= ? AND l.undone_at IS NULL""", (since,)):
        if row['reason'] == 'broken' and row['quantity'] < LOSS_BROKEN_MIN:
            continue
        yield {'kind': 'stock_loss', 'subject_key': f"loss:{row['id']}", 'store_id': row['store_id'],
               'product_id': row['product_id'], 'business_date': row['business_date'],
               'severity': 'high' if row['reason'] in ('stolen', 'missing') else 'normal',
               'facts': {'quantity': row['quantity'], 'unit': row['unit'], 'reason': row['reason'],
                         'losses_30d': row['others']}}


def _count_gaps(con, since):
    """Saved counts (盘点), netted per store, product and day so a piece moved between two places counted the same day
    is no gap. Daily-report corrections are sales, not counts, and are left out."""
    days = defaultdict(list)
    for row in con.execute(
            """SELECT l.store_id, m.product_id, d.business_date, SUM(m.quantity) AS net
               FROM inventory_documents d JOIN inventory_movements m ON m.document_id=d.id
               JOIN inventory_locations l ON l.id=m.location_id
               WHERE d.kind='correction' AND d.source_type='direct_count' AND d.status='posted'
                 AND d.business_date >= date(?, '-90 days')
               GROUP BY l.store_id, m.product_id, d.business_date""", (since,)):
        days[(row['store_id'], row['product_id'])].append((row['business_date'], row['net']))
    for (store_id, product_id), history in days.items():
        history.sort()
        for day, net in history:
            if day < since or not (net <= -COUNT_SHORT_MIN or net >= COUNT_OVER_MIN):
                continue
            start = (date.fromisoformat(day) - timedelta(days=90)).isoformat()
            before = [abs(n) for d, n in history if start <= d < day and n]
            yield {'kind': 'count_gap', 'subject_key': f'count:{store_id}:{product_id}:{day}', 'store_id': store_id,
                   'product_id': product_id, 'business_date': day,
                   'severity': 'high' if net <= -COUNT_SHORT_HIGH else 'normal',
                   'facts': {'net': net, 'usual': round(statistics.median(before)) if before else None,
                             'counts_before': len(before)}}


def _cash_gaps(con, since):
    reports = defaultdict(list)
    for row in con.execute(
            """SELECT m.date, s.id AS store_id, m.cash_actual_cents - m.cash_expected_cents AS gap,
                      m.cash_expected_cents AS expected
               FROM daily_report_metadata m JOIN stores s ON s.code=m.store
               WHERE m.cash_actual_cents IS NOT NULL AND m.cash_expected_cents IS NOT NULL
                 AND m.date >= date(?, '-30 days')""", (since,)):
        reports[row['store_id']].append((row['date'], row['gap'], row['expected']))
    for store_id, days in reports.items():
        for day, gap, expected in days:
            if day < since or abs(gap) < CASH_GAP_CENTS:
                continue
            start = (date.fromisoformat(day) - timedelta(days=30)).isoformat()
            others = sum(1 for d, g, _ in days if start <= d < day and abs(g) >= CASH_GAP_CENTS)
            yield {'kind': 'cash_gap', 'subject_key': f'cash:{store_id}:{day}', 'store_id': store_id,
                   'product_id': None, 'business_date': day,
                   'severity': 'high' if abs(gap) >= CASH_GAP_HIGH_CENTS else 'normal',
                   'facts': {'gap_cents': gap, 'gap_pct': round(100 * gap / expected, 1) if expected else None,
                             'gaps_30d': others}}


def _floor_short(con, since):
    marks = ','.join('?' for _ in FLOOR_REASONS)
    reports = defaultdict(dict)
    for row in con.execute(
            f"""SELECT store_id, business_date, reason, COUNT(*) AS n FROM report_stock_issues
                WHERE status='open' AND business_date >= ? AND reason IN ({marks})
                GROUP BY store_id, business_date, reason""", (since, *FLOOR_REASONS)):
        reports[(row['store_id'], row['business_date'])][row['reason']] = row['n']
    for (store_id, day), by_reason in reports.items():
        total = sum(by_reason.values())
        if total >= FLOOR_SHORT_MIN:
            yield {'kind': 'floor_short', 'subject_key': f'floor:{store_id}:{day}', 'store_id': store_id,
                   'product_id': None, 'business_date': day, 'severity': 'normal',
                   'facts': {'total': total, 'by_reason': by_reason}}


RULES = (('stock_loss', _losses), ('count_gap', _count_gaps), ('cash_gap', _cash_gaps), ('floor_short', _floor_short))


def _sync_problems(con, now):
    """{system: (problem, minutes)} for the syncs that should be running here; problem is None when healthy."""
    import dex_sync
    import website_sync
    found = {}
    try:
        configured = website_sync.config() is not None
    except ValueError:  # invalid website settings: app.py leaves the sync off
        return {'website': ('failing', 0)}
    if not configured:
        return found
    found['website'] = _sync_state(website_sync.state(con, 'last_run'), website_sync.state(con, 'last_error'), now)
    if dex_sync.enabled(con):
        found['dex'] = _sync_state(dex_sync.state(con, 'last_run'), dex_sync.state(con, 'last_error'), now)
    return found


def _sync_state(last_run, last_error, now):
    ran = _parse(last_run) if last_run else None
    if ran is None or _minutes(now, ran) >= SYNC_STUCK_MINUTES:
        return 'not_running', _minutes(now, ran) if ran else SYNC_STUCK_MINUTES
    return ('failing', 0) if last_error else (None, 0)


# ── Alerts ───────────────────────────────────────────────────────────────────

def _save(con, item, stamp, status='open'):
    """Add or refresh one finding; True when it is new (or back after it had settled)."""
    facts = json.dumps(item['facts'], sort_keys=True)
    row = con.execute('SELECT id, status, facts, severity FROM watch_alerts WHERE kind=? AND subject_key=?',
                      (item['kind'], item['subject_key'])).fetchone()
    if row is None:
        con.execute("""INSERT INTO watch_alerts(kind, subject_key, store_id, product_id, business_date, facts, severity,
                                                status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item['kind'], item['subject_key'], item['store_id'], item['product_id'], item['business_date'],
                     facts, item['severity'], status, stamp, stamp))
        return True
    if row['status'] == 'resolved':
        con.execute("""UPDATE watch_alerts SET status=?, facts=?, severity=?, verdict=NULL, note_zh=NULL, note_en=NULL,
                           reviewed_at=NULL, pushed_at=NULL, checked_by=NULL, checked_at=NULL, created_at=?, updated_at=?
                       WHERE id=?""", (status, facts, item['severity'], stamp, stamp, row['id']))
        return True
    if row['status'] in ('open', 'pending') and (row['facts'] != facts or row['severity'] != item['severity']):
        rose = item['severity'] == 'high' and row['severity'] != 'high'
        con.execute(f"""UPDATE watch_alerts SET facts=?, severity=?, updated_at=?
                            {', verdict=NULL, pushed_at=NULL' if rose else ''} WHERE id=?""",
                    (facts, item['severity'], stamp, row['id']))
    return False


def _sync_alerts(con, now, stamp):
    new = 0
    problems = _sync_problems(con, now)
    for system in ('website', 'dex'):
        key = f'sync:{system}'
        row = con.execute("SELECT * FROM watch_alerts WHERE kind='sync_stuck' AND subject_key=?", (key,)).fetchone()
        problem, minutes = problems.get(system, (None, 0))
        if problem is None:
            if row is not None and row['status'] == 'pending':
                con.execute('DELETE FROM watch_alerts WHERE id=?', (row['id'],))
            elif row is not None and row['status'] in ('open', 'checked'):
                con.execute("UPDATE watch_alerts SET status='resolved', updated_at=? WHERE id=?", (stamp, row['id']))
            continue
        item = {'kind': 'sync_stuck', 'subject_key': key, 'store_id': None, 'product_id': None, 'business_date': None,
                'severity': 'high', 'facts': {'system': system, 'problem': problem, 'minutes': minutes}}
        if row is None or row['status'] == 'resolved':
            # A failing sync waits an hour as pending before it is an alert; one that has not run for an hour is one.
            status = 'open' if problem == 'not_running' else 'pending'
            new += _save(con, item, stamp, status) and status == 'open'
        elif row['status'] in ('pending', 'open'):
            first = _parse(row['created_at']) or now
            if problem == 'failing':
                item['facts']['minutes'] = _minutes(now, first)
            if row['status'] == 'pending' and (problem == 'not_running' or _minutes(now, first) >= SYNC_STUCK_MINUTES):
                con.execute("UPDATE watch_alerts SET status='open', updated_at=? WHERE id=?", (stamp, row['id']))
                new += 1
            _save(con, item, stamp)
    return new


def run_rules(con, now=None):
    """One pass of the rules; the number of new alerts. Pushes the new high ones."""
    now = now or _utcnow()
    if settings(con)['watcher_enabled'] != '1':
        return 0
    stamp, today = _sql(now), _today(now)
    since = (today - timedelta(days=WINDOW_DAYS)).isoformat()
    if con.in_transaction:
        con.commit()
    con.execute('BEGIN IMMEDIATE')
    try:
        new = 0
        for kind, rule in RULES:
            found = list(rule(con, since))
            for item in found:
                new += _save(con, item, stamp)
            # What the rules no longer find in the window settled itself (a loss undone, a report fixed, notices checked).
            keys = {item['subject_key'] for item in found}
            for row in con.execute("SELECT id, subject_key FROM watch_alerts WHERE kind=? AND status='open' AND business_date >= ?",
                                   (kind, since)).fetchall():
                if row['subject_key'] not in keys:
                    con.execute("UPDATE watch_alerts SET status='resolved', updated_at=? WHERE id=?", (stamp, row['id']))
        new += _sync_alerts(con, now, stamp)
        con.execute("INSERT INTO watch_runs(kind, business_date, started_at, outcome, found) VALUES ('rules', ?, ?, 'ok', ?)",
                    (today.isoformat(), stamp, new))
        con.commit()
    except Exception:
        con.rollback()
        raise
    push_new(con, high_only=True)
    return new


# ── What Claude sees ─────────────────────────────────────────────────────────

def _int(value):
    return value if type(value) is int else None


def _safe_facts(kind, facts):
    """Only the SAFE_FACTS of one alert, each checked: whole numbers, one decimal, or one of the ENUMS words."""
    out = {}
    if kind == 'cash_gap':
        gap = _int(facts.get('gap_cents'))
        out['gap_dollars'] = round(gap / 100) if gap is not None else None
        pct = facts.get('gap_pct')
        out['gap_pct'] = round(float(pct), 1) if type(pct) in (int, float) else None
        out['gaps_30d'] = _int(facts.get('gaps_30d'))
        return out
    for name in SAFE_FACTS[kind]:
        value = facts.get(name)
        if name in ENUMS:
            out[name] = value if isinstance(value, str) and value in ENUMS[name] else None
        elif name == 'by_reason':
            out[name] = {reason: _int(value.get(reason)) or 0 for reason in FLOOR_REASONS} if isinstance(value, dict) else {}
        else:
            out[name] = _int(value)
    return out


def build_payload(con, alerts, today):
    """(what is sent, what stays here): the alerts as stand-in codes and SAFE_FACTS, and the codes' real meaning."""
    stores, products, ids = {}, {}, {}

    def code(table, prefix, value):
        if value not in table:
            table[value] = f'{prefix}{len(table) + 1}'
        return table[value]

    items = []
    for alert in alerts:
        alert_code = f'A{len(ids) + 1}'
        ids[alert_code] = alert['id']
        item = {'id': alert_code, 'kind': alert['kind'], 'severity': alert['severity']}
        if alert['store_id'] is not None:
            item['store'] = code(stores, 'S', alert['store_id'])
        if alert['product_id'] is not None:
            item['product'] = code(products, 'P', alert['product_id'])
        if alert['business_date']:
            try:
                item['days_ago'] = max(0, (today - date.fromisoformat(alert['business_date'])).days)
            except ValueError:
                pass
        item.update(_safe_facts(alert['kind'], json.loads(alert['facts'] or '{}')))
        items.append(item)
    return {'alerts': items}, {'ids': ids, 'stores': {v: k for k, v in stores.items()},
                               'products': {v: k for k, v in products.items()}}


def _names(con, kept):
    """The real names behind the codes, for POPCORE's own screens only."""
    names = {}
    for store_code, store_id in kept['stores'].items():
        row = con.execute('SELECT code FROM stores WHERE id=?', (store_id,)).fetchone()
        names[store_code] = row['code'] if row else '?'
    for product_code, product_id in kept['products'].items():
        names[product_code] = product_label(con, product_id)
    return names


TOKEN = re.compile(r'(?<![A-Za-z0-9])([SPA])(\d{1,4})(?![A-Za-z0-9])')


def restore(text, names, limit):
    """Claude's note with POPCORE's names back in; a code POPCORE did not send becomes '?'."""
    if not isinstance(text, str):
        return None
    text = TOKEN.sub(lambda m: names.get(m.group(0), '?') if m.group(1) != 'A' else '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:limit] or None


# ── The nightly review ───────────────────────────────────────────────────────

def _client():
    """The Anthropic client (tests stand in for it). The key comes from the environment at this moment only."""
    import anthropic
    return anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', '').strip(), timeout=45.0, max_retries=2)


def ask_claude(sent):
    """One Messages API call with the stand-in payload; the response. Claude Opus 5.5 with server-side fallbacks
    (a declined request is retried on Anthropic's recommended model) and a JSON schema for the reply."""
    return _client().beta.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        betas=['server-side-fallback-2026-07-01'],
        fallbacks='default',
        system=SYSTEM,
        output_config={'effort': EFFORT, 'format': {'type': 'json_schema', 'schema': REPLY_SCHEMA}},
        messages=[{'role': 'user', 'content': sent}],
    )


def worst_cost(sent):
    """The most one call could cost: every character a token, the whole output used, at the dearest price."""
    return (len(SYSTEM) + len(sent)) * DEAREST[0] / 1e6 + MAX_TOKENS * DEAREST[1] / 1e6


def _cost(model, usage):
    price_in, price_out = PRICES.get(model, DEAREST)
    tokens_in = sum(int(getattr(usage, name, 0) or 0)
                    for name in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'))
    tokens_out = int(getattr(usage, 'output_tokens', 0) or 0)
    return tokens_in, tokens_out, tokens_in * price_in / 1e6 + tokens_out * price_out / 1e6


def spent_today(con, today):
    return con.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM watch_runs WHERE kind='review' AND business_date=?",
                       (today.isoformat(),)).fetchone()[0]


def _reply(response):
    """(data, outcome) from Claude's response: the checked JSON, or None and why not."""
    if getattr(response, 'stop_reason', None) == 'refusal':
        return None, 'refused'
    if getattr(response, 'stop_reason', None) == 'max_tokens':
        return None, 'cut_off'
    text = next((block.text for block in getattr(response, 'content', None) or []
                 if getattr(block, 'type', None) == 'text'), None)
    try:
        data = json.loads(text or '')
    except ValueError:
        return None, 'bad_reply'
    if not isinstance(data, dict) or not isinstance(data.get('alerts'), list):
        return None, 'bad_reply'
    return data, 'ok'


def run_review(con, now=None):
    """The nightly review: Claude's verdict and note on every open alert not yet reviewed, then the pushes.
    Returns the run's outcome."""
    now = now or _utcnow()
    cfg = settings(con)
    if cfg['watcher_enabled'] != '1':
        return 'off'
    today, stamp = _today(now), _sql(now)
    alerts = con.execute(
        """SELECT * FROM watch_alerts WHERE status='open' AND reviewed_at IS NULL
           ORDER BY severity='high' DESC, id""").fetchall()
    _flag(con, 'watcher_review_requested', None)
    run = con.execute("INSERT INTO watch_runs(kind, business_date, started_at, outcome, found) VALUES ('review', ?, ?, 'running', ?)",
                      (today.isoformat(), stamp, len(alerts))).lastrowid
    con.commit()
    try:
        outcome, fields = _review(con, cfg, alerts[:REVIEW_MAX], today, run)
    except Exception as exc:  # noqa: BLE001 — the alerts are still marked and pushed; only the error's kind is kept
        if con.in_transaction:
            con.rollback()
        log.warning('Watcher review failed: %s', type(exc).__name__)
        outcome, fields = 'failed', {'error': type(exc).__name__[:80]}
    if alerts:
        marks = ','.join('?' for _ in alerts)
        con.execute(f'UPDATE watch_alerts SET reviewed_at=? WHERE id IN ({marks})', (stamp, *[a['id'] for a in alerts]))
    con.execute(f"UPDATE watch_runs SET outcome=?{''.join(f', {name}=?' for name in fields)} WHERE id=?",
                (outcome, *fields.values(), run))
    con.commit()
    push_new(con, high_only=False)
    return outcome


def _review(con, cfg, alerts, today, run):
    if not alerts:
        return 'none', {}
    if cfg['watcher_claude'] != '1':
        return 'claude_off', {}
    if not has_key():
        return 'no_key', {}
    sent_data, kept = build_payload(con, alerts, today)
    sent = json.dumps(sent_data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if spent_today(con, today) + worst_cost(sent) > float(cfg['watcher_daily_cap_usd']):
        return 'cap', {}
    # What goes out is kept before it goes, so it is on record even if the call never comes back.
    con.execute('UPDATE watch_runs SET sent=?, model=? WHERE id=?', (sent, MODEL, run))
    con.commit()
    try:
        response = ask_claude(sent)
    except Exception as exc:  # noqa: BLE001 — never raised into the scheduler; only the error's kind is kept
        log.warning('Watcher review call failed: %s', type(exc).__name__)
        status = getattr(exc, 'status_code', None)
        return 'failed', {'error': f'{type(exc).__name__}{f" {status}" if status else ""}'[:80],
                          'cost_usd': worst_cost(sent)}  # a failed call may still have been billed: count the most
    model = str(getattr(response, 'model', None) or MODEL)
    tokens_in, tokens_out, cost = _cost(model, getattr(response, 'usage', None))
    fields = {'model': model[:60], 'input_tokens': tokens_in, 'output_tokens': tokens_out, 'cost_usd': cost}
    data, outcome = _reply(response)
    if data is None:
        return outcome, fields
    names = _names(con, kept)
    severity = {alert['id']: alert['severity'] for alert in alerts}
    for entry in data['alerts']:
        if not isinstance(entry, dict) or entry.get('id') not in kept['ids']:
            continue
        alert_id = kept['ids'][entry['id']]
        verdict = 'low' if entry.get('verdict') == 'low' and severity[alert_id] != 'high' else 'keep'
        con.execute('UPDATE watch_alerts SET verdict=?, note_zh=?, note_en=? WHERE id=?',
                    (verdict, restore(entry.get('note_zh'), names, NOTE_MAX),
                     restore(entry.get('note_en'), names, NOTE_MAX), alert_id))
    fields['summary_zh'] = restore(data.get('summary_zh'), names, SUMMARY_MAX)
    fields['summary_en'] = restore(data.get('summary_en'), names, SUMMARY_MAX)
    return 'ok', fields


# ── Who is told ──────────────────────────────────────────────────────────────

def role_members():
    """{'admin': subjects, 'manager': subjects} from Auth0, where the roles live; None when Auth0 cannot be reached."""
    from auth import _get_role_map
    from blueprints.users import _fetch_role_member_ids
    try:
        role_ids = _get_role_map()
    except Exception as exc:  # noqa: BLE001
        log.warning('Watcher could not read roles: %s', type(exc).__name__)
        return None
    members = {}
    for role in ('admin', 'manager'):
        ids = _fetch_role_member_ids(role_ids[role]) if role in role_ids else []
        if ids is None:
            return None
        members[role] = set(ids)
    return members


def recipients(con, store_id, members):
    """Admins, and for a store's alert that store's managers (inventory_access there); never a removed account."""
    people = set(members.get('admin', ()))
    if store_id is not None:
        managers = members.get('manager', set())
        people |= {row[0] for row in con.execute('SELECT auth0_sub FROM inventory_access WHERE store_id=?', (store_id,))
                   if row[0] in managers}
    removed = {row[0] for row in con.execute('SELECT auth0_id FROM employees WHERE is_active=0 AND auth0_id IS NOT NULL')}
    return people - removed


def push_message(count):
    """What a phone shows: how many, never what."""
    return {'title': 'POPCORE 异常提醒 Alerts', 'body': f'{count} 条新提醒 · {count} new. 打开 POPCORE 查看 Open POPCORE to see.',
            'url': '/watch', 'tag': 'watch-alerts'}


def push_new(con, *, high_only):
    """Push the open alerts not pushed yet: high ones at once, the others after the review (not those Claude marked
    low). One message per person with their count. With Auth0 out of reach they wait for the next run."""
    import push_notifications as push
    rows = con.execute(
        f"""SELECT id, store_id FROM watch_alerts WHERE status='open' AND pushed_at IS NULL
            AND (severity='high' OR ({'0' if high_only else '1'} AND COALESCE(verdict, 'keep')='keep'))""").fetchall()
    if not rows:
        return 0
    members = role_members()
    if members is None:
        return 0
    counts = Counter()
    for row in rows:
        counts.update(recipients(con, row['store_id'], members))
    sent = 0
    for subject, count in sorted(counts.items()):
        devices = con.execute('SELECT * FROM push_subscriptions WHERE auth0_sub=?', (subject,)).fetchall()
        if devices:
            sent += push.send(con, devices, push_message(count))[0]
    marks = ','.join('?' for _ in rows)
    con.execute(f'UPDATE watch_alerts SET pushed_at=? WHERE id IN ({marks})', (_sql(_utcnow()), *[r['id'] for r in rows]))
    con.commit()
    return sent


# ── The scheduler ────────────────────────────────────────────────────────────

def connect():
    import website_sync
    return website_sync.connect()


def run_due(now=None):
    """Every 10 minutes from app.py: the rules once an hour, the review once a day after watcher_review_time (or when
    an admin asked for one). Nothing while the watcher is off."""
    now = now or _utcnow()
    con = connect()
    try:
        cfg = settings(con)
        if cfg['watcher_enabled'] != '1':
            return
        today = _today(now)
        last = con.execute("SELECT MAX(started_at) FROM watch_runs WHERE kind='rules'").fetchone()[0]
        last = _parse(last) if last else None
        asked = con.execute("SELECT 1 FROM app_settings WHERE key='watcher_review_requested'").fetchone() is not None
        hour, minute = (int(part) for part in cfg['watcher_review_time'].split(':'))
        local = now.astimezone(TORONTO)
        # One review a day: a run that started counts, even one a restart cut short, so the day is never paid twice.
        due = (local.hour, local.minute) >= (hour, minute) and con.execute(
            "SELECT 1 FROM watch_runs WHERE kind='review' AND business_date=?", (today.isoformat(),)).fetchone() is None
        if last is None or _minutes(now, last) >= RULES_EVERY_MINUTES - 5 or due or asked:
            run_rules(con, now)
        if due or asked:
            run_review(con, now)
    finally:
        con.close()


def ask_for_review(con):
    _flag(con, 'watcher_review_requested', '1')
    con.commit()


# ── What people see ──────────────────────────────────────────────────────────

def product_label(con, product_id):
    row = con.execute('SELECT jizhanming, name_cn_en, sku FROM products WHERE id=?', (product_id,)).fetchone()
    if row is None:
        return '?'
    return (row['jizhanming'] or row['name_cn_en'] or row['sku'] or '?').strip()


def describe(con, alert):
    """(中文, English) title of one alert, with POPCORE's own names."""
    facts = json.loads(alert['facts'] or '{}')
    kind = alert['kind']
    product = product_label(con, alert['product_id']) if alert['product_id'] is not None else ''
    if kind == 'stock_loss':
        zh_reason, en_reason = REASON_TEXT.get(facts.get('reason'), ('其他', 'other'))
        quantity, unit = facts.get('quantity'), facts.get('unit')
        return (f'{product} 损耗 {quantity}{UNIT_ZH.get(unit, "")} · {zh_reason}',
                f'{product}: {quantity} {unit or ""} written off as {en_reason}'.replace('  ', ' '))
    if kind == 'count_gap':
        net, usual = facts.get('net') or 0, facts.get('usual')
        zh = f'{product} 盘点{"少" if net < 0 else "多"} {abs(net)} 个' + (f'，平时差约 {usual}' if usual is not None else '，第一次盘点')
        en = (f'{product}: count found {abs(net)} {"fewer" if net < 0 else "more"} than the book'
              + (f' (usually about {usual} off)' if usual is not None else ' (first count)'))
        return zh, en
    if kind == 'cash_gap':
        gap = (facts.get('gap_cents') or 0) / 100
        return (f'日报现金{"少" if gap < 0 else "多"} ${abs(gap):.2f}（实收 vs 应收）',
                f'Daily report cash {"short" if gap < 0 else "over"} by ${abs(gap):.2f} (taken vs expected)')
    if kind == 'floor_short':
        total = facts.get('total')
        return f'一份日报留下 {total} 条库存差异', f'{total} stock notices from one daily report'
    if kind == 'sync_stuck':
        zh_name, en_name = SYSTEM_TEXT.get(facts.get('system'), ('同步', 'Sync'))
        minutes = facts.get('minutes') or 0
        if facts.get('problem') == 'not_running':
            return f'{zh_name} {minutes} 分钟没运行', f'{en_name} has not run for {minutes} min'
        return f'{zh_name}一直出错（{minutes} 分钟）', f'{en_name} has been failing for {minutes} min'
    return kind, kind


def _visible_where(con, actor):
    """SQL and parameters for the alerts this person may see: all for admins, their stores' for managers."""
    from auth import ROLE_CLAIM
    if actor.get(ROLE_CLAIM) == 'admin':
        return '1=1', []
    stores = [row[0] for row in con.execute('SELECT store_id FROM inventory_access WHERE auth0_sub=?', (actor.get('sub'),))]
    if not stores:
        return '0=1', []
    return f"store_id IN ({','.join('?' for _ in stores)})", stores


def _alert_json(con, alert, stores, people):
    zh, en = describe(con, alert)
    return {'id': alert['id'], 'kind': alert['kind'], 'severity': alert['severity'], 'status': alert['status'],
            'store': stores.get(alert['store_id']), 'business_date': alert['business_date'],
            'title_zh': zh, 'title_en': en, 'verdict': alert['verdict'],
            'note_zh': alert['note_zh'], 'note_en': alert['note_en'],
            'created_at': alert['created_at'], 'checked_at': alert['checked_at'],
            'checked_by': people.get(alert['checked_by']) if alert['checked_by'] else None}


def view(con, actor):
    """The 异常提醒 page: open alerts first, high first; the last two weeks' settled ones; for admins the status."""
    from auth import ROLE_CLAIM
    where, params = _visible_where(con, actor)
    stores = {row['id']: row['code'] for row in con.execute('SELECT id, code FROM stores')}
    people = {row['auth0_id']: row['name'] for row in con.execute('SELECT auth0_id, name FROM employees WHERE auth0_id IS NOT NULL')}
    open_rows = con.execute(
        f"""SELECT * FROM watch_alerts WHERE status='open' AND {where}
            ORDER BY severity='high' DESC, COALESCE(verdict, 'keep')='low', created_at DESC, id DESC""", params).fetchall()
    settled = con.execute(
        f"""SELECT * FROM watch_alerts WHERE status IN ('checked', 'resolved') AND updated_at >= datetime('now', '-14 days')
            AND {where} ORDER BY updated_at DESC, id DESC LIMIT 30""", params).fetchall()
    result = {'alerts': [_alert_json(con, row, stores, people) for row in open_rows],
              'recent': [_alert_json(con, row, stores, people) for row in settled],
              'admin': actor.get(ROLE_CLAIM) == 'admin'}
    if result['admin']:
        result['status'] = status(con)
    return result


def status(con, now=None):
    """For admins: the switches, today's spend, the last runs and exactly what the last review sent."""
    now = now or _utcnow()
    cfg = settings(con)
    rules = con.execute("SELECT started_at, found FROM watch_runs WHERE kind='rules' ORDER BY id DESC LIMIT 1").fetchone()
    review = con.execute("SELECT * FROM watch_runs WHERE kind='review' ORDER BY id DESC LIMIT 1").fetchone()
    last_sent = con.execute("SELECT started_at, sent FROM watch_runs WHERE kind='review' AND sent IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    return {
        'enabled': cfg['watcher_enabled'] == '1', 'claude': cfg['watcher_claude'] == '1', 'has_key': has_key(),
        'model': MODEL, 'daily_cap_usd': float(cfg['watcher_daily_cap_usd']), 'review_time': cfg['watcher_review_time'],
        'spent_today_usd': round(spent_today(con, _today(now)), 4),
        'review_requested': con.execute("SELECT 1 FROM app_settings WHERE key='watcher_review_requested'").fetchone() is not None,
        'last_rules': {'at': rules['started_at'], 'found': rules['found']} if rules else None,
        'last_review': {'at': review['started_at'], 'outcome': review['outcome'], 'found': review['found'],
                        'cost_usd': round(review['cost_usd'] or 0, 4), 'error': review['error'],
                        'summary_zh': review['summary_zh'], 'summary_en': review['summary_en']} if review else None,
        'last_sent': {'at': last_sent['started_at'], 'payload': json.loads(last_sent['sent'])} if last_sent else None,
    }


def check(con, actor, alert_id):
    """已查看 Checked: the alert leaves the open list. A store's alert for its managers and admins; a sync one for admins."""
    where, params = _visible_where(con, actor)
    row = con.execute(f'SELECT id, status FROM watch_alerts WHERE id=? AND {where}', (alert_id, *params)).fetchone()
    if row is None:
        raise LookupError('Alert not found')
    if row['status'] == 'open':
        con.execute("UPDATE watch_alerts SET status='checked', checked_by=?, checked_at=?, updated_at=? WHERE id=?",
                    (actor.get('sub'), _sql(_utcnow()), _sql(_utcnow()), alert_id))
        con.commit()


def today_rows(con, role, store_ids):
    """The 异常提醒 section of the manager home: open alerts of these stores (and the store-less ones for admins),
    except those Claude marked low."""
    marks = ','.join('?' for _ in store_ids)
    clauses = [f'store_id IN ({marks})'] if store_ids else []
    if role == 'admin':
        clauses.append('store_id IS NULL')
    if not clauses:
        return []
    rows = con.execute(
        f"""SELECT * FROM watch_alerts WHERE status='open' AND COALESCE(verdict, 'keep')='keep' AND ({' OR '.join(clauses)})
            ORDER BY severity='high' DESC, created_at DESC, id DESC""", store_ids).fetchall()
    out = []
    for row in rows:
        zh, en = describe(con, row)
        out.append({'source_id': row['id'], 'type': 'watch_alert', 'link': '/watch', 'store_id': row['store_id'],
                    'status': row['severity'], 'version': None,
                    'updated_at': row['business_date'] or (row['created_at'] or '')[:10], 'label': f'{zh} · {en}'})
    return out
