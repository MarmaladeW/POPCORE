"""员工折扣 Staff discounts (owner, 2026-11-19): "there should be a 员工折扣 discount record, each 正式员工 and above have
4 discount per month, we wanna keep track of that, and sometimes in 汇总 there are 员工折扣, it is easier for us to track
the revenue too."

A record is a 日报 员工折扣 line or one a manager adds by hand. One use is one product (owner, 2026-11-19: "purchasing
ten smiski hipper is still one employee discount"), so a record counts the different products it bought. The 4 uses
come back on the 1st of every calendar month. The "本月剩余N次" a line ends with is the staff's own count and is not read.

Report lines are read when the page is: a month's saved 日报 lines (daily_report_metadata.employee_discounts) are
compared with its records, so a re-paste adds only new lines, a line no longer on the report goes, and a manager's fixes
stay. The text before 使用员工折扣 names the person: the exact name (any case) of an active person links the line, as
does a name a manager picked for before; anything else waits for a manager.
"""
import json
import re
import unicodedata
from collections import Counter
from decimal import Decimal

import ranks
from auth import ROLE_CLAIM, ROLE_HIERARCHY
from checkout_access import business_date
from validation import read_date, read_int

MONTHLY_USES = 4
MONTH_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')
MAX_ITEMS = 30
MAX_USES = 100
MAX_AMOUNT_CENTS = 100_000_00
TEXT_LIMIT = 200
REASON_LIMIT = 300

# How a line says it was paid, by the code the page shows: "emt 54", "卡机18.07".
_METHODS = {
    'emt': ('emt', 'etransfer', 'e-transfer', '转账'),
    'card': ('卡机', '刷卡', 'card', 'debit', 'credit', 'visa', 'mastercard'),
    'cash': ('现金', 'cash'),
    'wechat': ('微信', 'wechat'),
    'alipay': ('支付宝', 'alipay'),
}
PAYMENTS = tuple(_METHODS)
_METHOD = '(?:' + '|'.join(sorted((re.escape(word) for words in _METHODS.values() for word in words),
                                  key=len, reverse=True)) + ')'
# "Celia使用员工折扣购入…"; a line under the 员工折扣 heading may leave 使用员工折扣 out ("staff购买…").
_VERB = re.compile(r'^(?P<who>.*?)\s*(?:使用\s*)?(?:员工\s*折扣\s*)?[,:]?\s*(?:购入|购买)(?P<rest>.*)$', re.S)
_DATE = re.compile(r'^\d{4}[./-]\d{1,2}[./-]\d{1,2}\s*')
_LEFT = re.compile(r'(?:[本今这]个?月\s*)?还?剩(?:余|下)?\s*\d+\s*次?')
# Pieces are split by commas, 、, + and ;, after a written quantity by a space or a payment ("X*1 卡机18.07"), and by
# a space before a payment with its amount ("X*1 (隐藏) emt 20").
_AFTER_QTY = r'(?:(?<=[*×xX]\d)|(?<=[*×xX]\d\d)|(?<=[*×xX]\d\d\d))'
_SPLIT = re.compile(rf'\s*[,、+➕;]\s*|{_AFTER_QTY}\s+(?!\()|{_AFTER_QTY}(?={_METHOD})'
                    rf'|\s+(?={_METHOD}\s*:?\s*\$?\s*\d)', re.I)
# name*qty, name×qty, name x qty (an x right after a Latin letter is part of the name), with a note after it.
_ITEM = re.compile(r'^(?P<name>.*?\S)\s*(?:[*×]|(?<![A-Za-z])[xX])\s*(?P<qty>\d{1,4})\s*(?P<note>\(.*\))?$')
_PAY = re.compile(rf'^(?:(?P<method>{_METHOD})\s*(?:付款?|支付)?\s*:?\s*\$?\s*(?P<amount>\d+(?:\.\d{{1,2}})?)?'
                  rf'|\$?\s*(?P<amount2>\d+(?:\.\d{{1,2}})?)\s*(?P<method2>{_METHOD})?)\s*(?:元|刀|块)?$', re.I)


def _method_code(word):
    word = word.lower()
    return next(code for code, words in _METHODS.items() if word in words)


def name_key(name):
    """A name compared without case or extra spaces: "zm", "ZM" and " zm " are one name."""
    return ' '.join(unicodedata.normalize('NFKC', name or '').split()).casefold()


def parse_line(text):
    """Read one 员工折扣 line: who, the products and their quantities, what was paid and how, and the uses it counts.

    `readable` is False when the line has no 购入/购买, no product, or a piece that is neither a product with its
    quantity nor a payment; such a line waits for a manager.
    """
    line = unicodedata.normalize('NFKC', text or '').strip()
    read = {'name': None, 'items': [], 'uses': 0, 'amount_cents': None, 'payment': None, 'readable': False}
    match = _VERB.match(line)
    if not match:
        return read
    read['name'] = _DATE.sub('', match['who']).strip(' :,-') or None
    items, methods, amount, unread = {}, [], None, False
    for piece in _SPLIT.split(_LEFT.sub(' ', match['rest'])):
        piece = piece.strip()
        if not piece:
            continue
        paid = _PAY.match(piece)
        if paid:
            method = paid['method'] or paid['method2']
            if method and _method_code(method) not in methods:
                methods.append(_method_code(method))
            written = paid['amount'] or paid['amount2']
            if written:
                amount = (amount or 0) + int(Decimal(written) * 100)
            continue
        item = _ITEM.match(piece)
        if item and int(item['qty']) > 0:
            name = ' '.join(item['name'].split()) + (f" {item['note']}" if item['note'] else '')
            key = name_key(name).replace(' ', '')
            if key in items:
                items[key]['qty'] += int(item['qty'])
            else:
                items[key] = {'name': name, 'qty': int(item['qty'])}
            continue
        unread = True
    read.update(items=list(items.values()), uses=len(items), amount_cents=amount,
                payment='+'.join(methods) or None, readable=bool(items) and not unread)
    return read


def read_month(value):
    """A calendar month, YYYY-MM; this month in Toronto when none is given."""
    if value in (None, ''):
        return business_date()[:7]
    if not isinstance(value, str) or not MONTH_RE.match(value):
        raise ValueError('month must be YYYY-MM')
    return value


def month_bounds(month):
    year, number = int(month[:4]), int(month[5:7])
    return f'{month}-01', f'{year + number // 12:04d}-{number % 12 + 1:02d}-01'


def _matcher(con):
    """Who a written name is: one active person with exactly that name, else the person a manager picked for it."""
    people = {}
    for row in con.execute('SELECT id, name FROM employees WHERE is_active=1'):
        people.setdefault(name_key(row['name']), []).append(row['id'])
    aliases = {row['alias_norm']: row['employee_id'] for row in con.execute(
        '''SELECT a.alias_norm, a.employee_id FROM staff_discount_aliases a
           JOIN employees e ON e.id=a.employee_id WHERE e.is_active=1''')}

    def match(written):
        key = name_key(written)
        if not key:
            return None
        found = people.get(key, [])
        return found[0] if len(found) == 1 else aliases.get(key)
    return match


def sync_month(con, month):
    """Bring a month's records in line with its saved 日报 lines. A line is keyed by store, day, its text and which
    copy of that text it is on the day; a manager's fix is never undone, and a line no longer on its report goes unless
    a manager fixed it."""
    first, after = month_bounds(month)
    wanted = {}
    for row in con.execute('SELECT date, store, employee_discounts FROM daily_report_metadata WHERE date >= ? AND date < ?'
                           ' ORDER BY date, store', (first, after)):
        try:
            lines = json.loads(row['employee_discounts'] or '[]')
        except ValueError:
            continue
        copies = Counter()
        for line in lines if isinstance(lines, list) else []:
            text = line.strip() if isinstance(line, str) else ''
            if text:
                copies[text] += 1
                wanted[(row['store'], row['date'], text, copies[text])] = True
    existing = {(row['store'], row['date'], row['raw_text'], row['occurrence']): row for row in con.execute(
        """SELECT id, store, date, raw_text, occurrence, written_name, employee_id, edited_at FROM staff_discount_uses
           WHERE source='report' AND date >= ? AND date < ?""", (first, after))}
    match = _matcher(con)
    changed = False
    for key in wanted:
        if key in existing:
            continue
        store, day, text, occurrence = key
        read = parse_line(text)
        con.execute("""INSERT OR IGNORE INTO staff_discount_uses
                       (date, store, source, raw_text, occurrence, written_name, employee_id, items, uses, amount_cents,
                        payment, readable)
                       VALUES (?, ?, 'report', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (day, store, text, occurrence, read['name'], match(read['name']),
                     json.dumps(read['items'], ensure_ascii=False), read['uses'], read['amount_cents'], read['payment'],
                     int(read['readable'])))
        changed = True
    for key, row in existing.items():
        if row['edited_at'] is not None:
            continue
        if key not in wanted:
            con.execute('DELETE FROM staff_discount_uses WHERE id=?', (row['id'],))
            changed = True
        elif row['employee_id'] is None and (person := match(row['written_name'])):
            con.execute('UPDATE staff_discount_uses SET employee_id=? WHERE id=?', (person, row['id']))
            changed = True
    if changed:
        con.commit()


_ENTRY_SQL = '''SELECT u.*, e.name AS person_name, e.rank AS person_rank, s.name AS store_name,
                       ed.name AS edited_by_name, cr.name AS created_by_name
                FROM staff_discount_uses u
                LEFT JOIN employees e ON e.id=u.employee_id
                LEFT JOIN stores s ON s.code=u.store
                LEFT JOIN employees ed ON ed.auth0_id=u.edited_by
                LEFT JOIN employees cr ON cr.auth0_id=u.created_by'''


def _state(row):
    if row['ignored_reason'] is not None:
        return 'ignored', None
    if row['employee_id'] is None:
        return 'waiting', 'person'
    if not row['readable']:
        return 'waiting', 'reading'
    return 'counted', None


def _entry(row, manager):
    state, waiting_for = _state(row)
    entry = {
        'id': row['id'], 'date': row['date'], 'store': row['store'], 'store_name': row['store_name'],
        'source': row['source'], 'employee_id': row['employee_id'], 'name': row['person_name'],
        'rank': row['person_rank'], 'written_name': row['written_name'], 'items': json.loads(row['items']),
        'uses': row['uses'], 'payment': row['payment'], 'readable': bool(row['readable']), 'state': state,
        'waiting_for': waiting_for,
        'ignored_reason': row['ignored_reason'], 'edited': row['edited_at'] is not None,
        'edited_by_name': row['edited_by_name'], 'created_by_name': row['created_by_name'],
    }
    if manager:
        # What was paid is the store's revenue: managers only (financial reports are manager-only).
        entry.update(amount_cents=row['amount_cents'], raw_text=row['raw_text'])
    return entry


def _is_manager(actor):
    return ROLE_HIERARCHY.get(actor.get(ROLE_CLAIM), 0) >= ROLE_HIERARCHY['manager']


def month_page(con, actor, month_value):
    """The month's people, records, lines waiting for a manager and, for managers, what was paid."""
    month = read_month(month_value)
    sync_month(con, month)
    manager = _is_manager(actor)
    first, after = month_bounds(month)
    rows = con.execute(f'{_ENTRY_SQL} WHERE u.date >= ? AND u.date < ? ORDER BY u.date DESC, u.store, u.id',
                       (first, after)).fetchall()
    entries = [_entry(row, manager) for row in rows]
    used = Counter()
    for entry in entries:
        if entry['state'] == 'counted':
            used[entry['employee_id']] += entry['uses']
    eligible = ','.join('?' for _ in ranks.DISCOUNT_RANKS)
    users = ','.join('?' for _ in used)
    people = []
    for row in con.execute(
            f"""SELECT e.id, e.name, e.rank, e.is_active FROM employees e
                WHERE (e.is_active=1 AND e.rank IN ({eligible})){f' OR e.id IN ({users})' if used else ''}
                ORDER BY {ranks.order_by('e')}""", (*ranks.DISCOUNT_RANKS, *used)):
        allowed = row['rank'] in ranks.DISCOUNT_RANKS
        count = used[row['id']]
        people.append({'employee_id': row['id'], 'name': row['name'], 'rank': row['rank'],
                       'rank_label': ranks.RANK_LABELS.get(row['rank']), 'eligible': allowed,
                       'active': bool(row['is_active']), 'used': count,
                       'left': max(0, MONTHLY_USES - count) if allowed else None,
                       'over': max(0, count - MONTHLY_USES) if allowed else 0})
    page = {
        'month': month, 'this_month': business_date()[:7], 'today': business_date(), 'monthly_uses': MONTHLY_USES,
        'can_manage': manager, 'people': people,
        'uses': [entry for entry in entries if entry['state'] == 'counted'],
        'waiting': [entry for entry in entries if entry['state'] == 'waiting'],
        'ignored': [entry for entry in entries if entry['state'] == 'ignored'],
    }
    if manager:
        stores = [dict(row) for row in con.execute('SELECT code, name FROM stores WHERE is_active=1 ORDER BY id')]
        order = {row['code']: index for index, row in enumerate(con.execute('SELECT code FROM stores ORDER BY id'))}
        totals = {}
        for entry in entries:
            if entry['state'] == 'ignored':
                continue
            total = totals.setdefault(entry['store'], {'store': entry['store'], 'name': entry['store_name'],
                                                       'amount_cents': 0, 'records': 0, 'no_amount': 0})
            total['records'] += 1
            if entry['amount_cents'] is None:
                total['no_amount'] += 1
            else:
                total['amount_cents'] += entry['amount_cents']
        by_store = sorted(totals.values(), key=lambda total: (order.get(total['store'], len(order)), total['store']))
        page.update(
            totals={'stores': by_store, 'amount_cents': sum(total['amount_cents'] for total in by_store),
                    'records': sum(total['records'] for total in by_store),
                    'no_amount': sum(total['no_amount'] for total in by_store)},
            stores=stores,
            employees=[{'employee_id': row['id'], 'name': row['name'], 'rank': row['rank'],
                        'rank_label': ranks.RANK_LABELS.get(row['rank'])} for row in con.execute(
                f'SELECT e.id, e.name, e.rank FROM employees e WHERE e.is_active=1 ORDER BY {ranks.order_by("e")}')])
    return page


def _one(con, use_id):
    row = con.execute(f'{_ENTRY_SQL} WHERE u.id=?', (use_id,)).fetchone()
    return _entry(row, True)


def _record(con, use_id):
    row = con.execute('SELECT * FROM staff_discount_uses WHERE id=?', (use_id,)).fetchone()
    if row is None:
        raise LookupError('No such 员工折扣 record')
    return row


def _object(body):
    if not isinstance(body, dict):
        raise ValueError('Expected an object')
    return body


def _person(con, value):
    employee_id = read_int(value, 'employee_id', minimum=1)
    if not con.execute('SELECT 1 FROM employees WHERE id=? AND is_active=1', (employee_id,)).fetchone():
        raise ValueError('employee_id must be an active person')
    return employee_id


def _amount(value):
    if value is None or value == '':
        return None
    text = str(value).strip().lstrip('$') if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ''
    if not re.fullmatch(r'\d+(?:\.\d{1,2})?', text) or int(Decimal(text) * 100) > MAX_AMOUNT_CENTS:
        raise ValueError('amount must be a dollar amount with at most two decimals')
    return int(Decimal(text) * 100)


def _payment(value):
    if value is None or value == '':
        return None
    parts = value.split('+') if isinstance(value, str) else [None]
    if any(part not in PAYMENTS for part in parts) or len(set(parts)) != len(parts):
        raise ValueError(f'payment must be one of {", ".join(PAYMENTS)}')
    return value


def _items(value):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ITEMS:
        raise ValueError(f'items must list 1 to {MAX_ITEMS} products')
    items = {}
    for item in value:
        name = ' '.join(item.get('name').split()) if isinstance(item, dict) and isinstance(item.get('name'), str) else ''
        if not name or len(name) > TEXT_LIMIT:
            raise ValueError('items need a product name')
        qty = read_int(item.get('qty', 1), 'qty', minimum=1)
        if qty > 9999:
            raise ValueError('qty must be at most 9999')
        key = name_key(name).replace(' ', '')
        if key in items:
            items[key]['qty'] += qty
        else:
            items[key] = {'name': name, 'qty': qty}
    return list(items.values())


def add_use(con, actor, body, request_key=None):
    """A manager adds a use by hand: person, day, store, the products, and what was paid."""
    body = _object(body)
    if request_key is not None and (not isinstance(request_key, str) or not 0 < len(request_key) <= TEXT_LIMIT):
        raise ValueError('Idempotency-Key is not valid')
    if request_key:
        done = con.execute('SELECT id FROM staff_discount_uses WHERE request_key=?', (request_key,)).fetchone()
        if done:
            return {'ok': True, 'use': _one(con, done['id']), 'replayed': True}
    employee_id = _person(con, body.get('employee_id'))
    day = read_date(body.get('date'))
    store = body.get('store').strip().upper() if isinstance(body.get('store'), str) else ''
    if not con.execute('SELECT 1 FROM stores WHERE code=? AND is_active=1', (store,)).fetchone():
        raise ValueError('store must be an active store')
    items = _items(body.get('items'))
    amount = _amount(body.get('amount'))
    payment = _payment(body.get('payment'))
    use_id = con.execute("""INSERT INTO staff_discount_uses
                            (date, store, source, employee_id, items, uses, amount_cents, payment, readable, request_key,
                             created_by)
                            VALUES (?, ?, 'manual', ?, ?, ?, ?, ?, 1, ?, ?)""",
                         (day, store, employee_id, json.dumps(items, ensure_ascii=False), len(items), amount, payment,
                          request_key or None, actor.get('sub'))).lastrowid
    con.commit()
    return {'ok': True, 'use': _one(con, use_id)}


def fix_use(con, actor, use_id, body):
    """A manager fixes a record (the person, the uses it counts, the amount, the payment), or picks the person for one
    that waits. The name a line wrote is remembered for the person picked, and other lines waiting on it follow."""
    body = _object(body)
    row = _record(con, use_id)
    changes = {}
    if 'employee_id' in body:
        changes['employee_id'] = _person(con, body['employee_id'])
    if 'uses' in body:
        uses = read_int(body['uses'], 'uses')
        if uses > MAX_USES:
            raise ValueError(f'uses must be at most {MAX_USES}')
        changes.update(uses=uses, readable=1)  # a manager's count reads the line
    if 'amount' in body:
        changes['amount_cents'] = _amount(body['amount'])
    if 'payment' in body:
        changes['payment'] = _payment(body['payment'])
    if not changes:
        raise ValueError('Nothing to change')
    assignments = ', '.join(f'{column}=?' for column in changes)
    con.execute(f"UPDATE staff_discount_uses SET {assignments}, edited_by=?, edited_at=datetime('now') WHERE id=?",
                (*changes.values(), actor.get('sub'), use_id))
    key = name_key(row['written_name'])
    if 'employee_id' in changes and row['employee_id'] is None and key:
        con.execute("""INSERT INTO staff_discount_aliases(alias_norm, alias, employee_id, created_by) VALUES (?, ?, ?, ?)
                       ON CONFLICT(alias_norm) DO UPDATE SET alias=excluded.alias, employee_id=excluded.employee_id,
                       created_by=excluded.created_by, created_at=datetime('now')""",
                    (key, row['written_name'], changes['employee_id'], actor.get('sub')))
        waiting = [other['id'] for other in con.execute(
            """SELECT id, written_name FROM staff_discount_uses
               WHERE source='report' AND employee_id IS NULL AND edited_at IS NULL AND written_name IS NOT NULL""")
                   if name_key(other['written_name']) == key]
        con.executemany('UPDATE staff_discount_uses SET employee_id=? WHERE id=?',
                        [(changes['employee_id'], other) for other in waiting])
    con.commit()
    return {'ok': True, 'use': _one(con, use_id)}


def ignore_use(con, actor, use_id, body):
    """不算 Ignored: the record stays on the page with the manager's reason and counts nothing."""
    body = _object(body)
    _record(con, use_id)
    reason = ' '.join(body.get('reason').split()) if isinstance(body.get('reason'), str) else ''
    if not reason or len(reason) > REASON_LIMIT:
        raise ValueError('reason is needed to ignore a record')
    con.execute("""UPDATE staff_discount_uses SET ignored_reason=?, edited_by=?, edited_at=datetime('now')
                   WHERE id=?""", (reason, actor.get('sub'), use_id))
    con.commit()
    return {'ok': True, 'use': _one(con, use_id)}


def restore_use(con, actor, use_id):
    """算回 Count again: an ignored record counts once more."""
    _record(con, use_id)
    con.execute("""UPDATE staff_discount_uses SET ignored_reason=NULL, edited_by=?, edited_at=datetime('now')
                   WHERE id=?""", (actor.get('sub'), use_id))
    con.commit()
    return {'ok': True, 'use': _one(con, use_id)}
