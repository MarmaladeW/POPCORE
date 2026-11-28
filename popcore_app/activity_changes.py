"""What a 动态 line changed (owner, 2026-11-28: "in 动态, admin should be able to click into there and see exactly
what changed"), kept as JSON in activity_log.changes by the same after-request step that writes the line:

- records: each record the request changed (a product or design, a shift, an employee, a setting, a web order …),
  read before the route runs and again after it, with only the fields that changed, before → after. Each kind of
  record names the fields it shows (the readers below), so a password, a PIN, a calendar token, a pickup code, a
  customer's address or a GPS position is never read, let alone kept.
- stock: every inventory document the request posted (all stock writes go through inventory_commands._post_inventory,
  which names each document to the request posting it): product × quantity, from place → to place, and the count at
  each place before → after.

Values are kept as the words people read (a store code, a name, $20.99), not ids, so a line still reads right after
the record is renamed or gone. Lines from before 2026-11-28 have no changes; the detail view says so.
"""
import json
from collections import defaultdict

from flask import g

import people

RECORDS_MAX = 200
STOCK_MAX = 400


def _money(value):
    return None if value is None else f'${float(value):,.2f}'


def _yes(value):
    return None if value is None else ('是 Yes' if value else '否 No')


def _text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _marks(count):
    return ','.join('?' * count)


def _store_codes(con):
    return {row[0]: row[1] for row in con.execute('SELECT id, code FROM stores')}


# ── Records ──────────────────────────────────────────────────────────────────

PRODUCT_FIELDS = (
    ('sku', 'POPCORE ID', _text), ('jizhanming', '记账名 Name', _text), ('name_cn_en', '全名 Full name', _text),
    ('price', '价格 Price', _money), ('brand', '品牌 Brand', _text), ('ip_series', 'IP', _text),
    ('product_type', '类型 Type', _text), ('design_name', '款式 Design', _text),
    ('stock_form', '形态 Stock form', _text), ('stock_unit', '单位 Unit', _text),
    ('identity_status', '核对 Review', _text), ('boxes_per_dan', '盒/端 Boxes per set', _text),
    ('release_date', '发售日期 Release date', _text), ('edition_size', '限量 Edition', _text),
    ('channel', '渠道 Channel', _text), ('style_notes', '款式备注 Style notes', _text), ('notes', '备注 Notes', _text),
    ('hidden', '隐藏款 Secrets', _text), ('hidden_count', '隐藏款数 Secret count', _text),
    ('hidden_has_small', '小隐藏 Small secret', _yes), ('hidden_has_large', '大隐藏 Large secret', _yes),
    ('hidden_prob_small', '小隐藏概率 Small secret odds', _text),
    ('hidden_prob_large', '大隐藏概率 Large secret odds', _text),
    ('is_bestseller', '畅销 Bestseller', _yes), ('stock_counted', '计库存 Stock counted', _yes),
)


def products(con, ids):
    """Products and designs by id, with their series and other names (别名)."""
    ids = sorted({int(pid) for pid in ids if isinstance(pid, int) and not isinstance(pid, bool)})
    if not ids:
        return {}
    marks = _marks(len(ids))
    columns = ', '.join(f'p.{column}' for column, _, _ in PRODUCT_FIELDS)
    rows = con.execute(f"""SELECT p.id, {columns}, s.name AS series FROM products p
                           LEFT JOIN product_series s ON s.id=p.series_id WHERE p.id IN ({marks})""", ids).fetchall()
    aliases = defaultdict(list)
    for row in con.execute(f'SELECT product_id, alias FROM product_aliases WHERE product_id IN ({marks}) ORDER BY id', ids):
        aliases[row[0]].append(row[1])
    found = {}
    for row in rows:
        fields = {label: show(row[column]) for column, label, show in PRODUCT_FIELDS}
        fields['系列 Series'] = _text(row['series'])
        fields['别名 Other names'] = '、'.join(aliases[row['id']]) or None
        label = ' '.join(filter(None, (row['sku'], row['jizhanming'] or row['name_cn_en']))) or f"#{row['id']}"
        found[('product', row['id'])] = {'label': label, 'link': '/products', 'fields': fields}
    return found


def sub_items(con, pid):
    """The designs and whole sets kept under a product (its own series)."""
    if not isinstance(pid, int):
        return []
    return [row[0] for row in con.execute(
        """SELECT p.id FROM products p JOIN product_series s ON s.id=p.series_id
           WHERE s.product_id=? AND p.id<>?""", (pid, pid))]


def shifts(con, ids):
    ids = [shift for shift in ids if isinstance(shift, int)]
    if not ids:
        return {}
    codes = _store_codes(con)
    found = {}
    for row in con.execute(f"""SELECT s.id, s.date, s.start_time, s.end_time, s.actual_end_time, s.store_id, s.position,
                                      s.notes, e.name FROM shifts s LEFT JOIN employees e ON e.id=s.employee_id
                               WHERE s.id IN ({_marks(len(ids))})""", ids):
        found[('shift', row['id'])] = {
            'label': ' '.join(filter(None, (row['name'], row['date']))), 'link': '/schedule',
            'fields': {'员工 Person': _text(row['name']), '日期 Date': _text(row['date']),
                       '开始 Start': _text(row['start_time']), '结束 End': _text(row['end_time']),
                       '实际下班 Actual finish': _text(row['actual_end_time']),
                       '门店 Store': codes.get(row['store_id']), '位置 Position': _text(row['position']),
                       '备注 Notes': _text(row['notes'])}}
    return found


def employees(con, ids=(), subjects=()):
    """People by employee id or account. Never their PIN, calendar token or login id."""
    from ranks import RANK_LABELS
    ids = [emp for emp in ids if isinstance(emp, int)]
    subjects = [sub for sub in subjects if isinstance(sub, str) and sub]
    if not ids and not subjects:
        return {}
    codes = _store_codes(con)
    where = ' OR '.join(filter(None, (ids and f'id IN ({_marks(len(ids))})',
                                      subjects and f'auth0_id IN ({_marks(len(subjects))})')))
    found = {}
    for row in con.execute(f"""SELECT id, name, email, color, is_active, is_schedulable, is_training, regular_since, rank
                               FROM employees WHERE {where}""", (*ids, *subjects)):
        stores = [codes.get(store) for (store,) in con.execute(
            'SELECT store_id FROM employee_stores WHERE employee_id=? ORDER BY store_id', (row['id'],))]
        found[('employee', row['id'])] = {
            'label': row['name'] or f"#{row['id']}", 'link': '/users',
            'fields': {'名字 Name': _text(row['name']), '邮箱 Email': _text(row['email']),
                       '职位 Rank': RANK_LABELS.get(row['rank'], row['rank']), '门店 Stores': '、'.join(filter(None, stores)) or None,
                       '在职 Active': _yes(row['is_active']), '可排班 Schedulable': _yes(row['is_schedulable']),
                       '培训中 Training': _yes(row['is_training']), '转正 Regular since': _text(row['regular_since']),
                       '颜色 Colour': _text(row['color'])}}
    return found


def access(con, subject):
    """Which stores an account may work at (门店操作权限) and see the stock of (库存查看)."""
    if not isinstance(subject, str) or not subject:
        return {}
    codes = _store_codes(con)

    def stores(table):
        return '、'.join(codes.get(row[0]) or '' for row in con.execute(
            f'SELECT store_id FROM {table} WHERE auth0_sub=? ORDER BY store_id', (subject,))) or None
    return {('access', subject): {'label': people.name(con, subject), 'link': '/users',
                                  'fields': {'门店操作权限 Store access': stores('inventory_access'),
                                             '库存查看 Inventory view': stores('inventory_viewers')}}}


SETTING_LABELS = {
    'schedule_month_start_day': '工资月开始日 Wage month starts', 'schedule_required_staff': '最少人数 Staff needed',
    'schedule_open_hours': '营业时间 Opening hours', 'schedule_shift_presets': '班次模板 Shift presets',
    'schedule_positions': '位置 Positions', 'staff_checkout': '员工收银 Staff checkout',
    'checkout_cny_per_cad': '人民币汇率 CNY rate', 'watcher_enabled': '异常提醒 Watcher',
    'watcher_claude': '异常提醒 Claude review', 'watcher_daily_cap_usd': '每日上限 Daily cap (USD)',
    'watcher_review_time': '检查时间 Review time',
}
SECRET_WORDS = ('secret', 'token', 'password', 'pin', 'key')


def settings(con, keys):
    keys = sorted(key for key in keys if isinstance(key, str) and not any(word in key.lower() for word in SECRET_WORDS))
    if not keys:
        return {}
    values = {row[0]: row[1] for row in con.execute(
        f'SELECT key, value FROM app_settings WHERE key IN ({_marks(len(keys))})', keys)}
    return {('settings', 'app'): {'label': '设置 Settings', 'link': '/settings',
                                  'fields': {SETTING_LABELS.get(key, key): _text(values.get(key)) for key in keys}}}


def web_order(con, wc_order_id):
    """A web order's state. Never its pickup code, the customer's name or address."""
    if not isinstance(wc_order_id, int):
        return {}
    row = con.execute("""SELECT wc_order_id, number, wc_status, state, store_id, packed_by, problem, dismiss_reason
                         FROM website_orders WHERE wc_order_id=?""", (wc_order_id,)).fetchone()
    if row is None:
        return {}
    return {('web_order', row['wc_order_id']): {
        'label': f"#{row['number']}", 'link': '/web-orders',
        'fields': {'网站状态 Website status': _text(row['wc_status']), 'POPCORE 状态 State': _text(row['state']),
                   '门店 Store': _store_codes(con).get(row['store_id']), '打包 Packed by': _text(row['packed_by']),
                   '问题 Problem': _text(row['problem']), '不处理原因 Dismissed because': _text(row['dismiss_reason'])}}}


def _body_ids(body, key='product_id'):
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        return []
    return [change.get(key) for change in body.get('changes') or [] if isinstance(change, dict)] + [body.get(key)]


def _result_id(result, key='id'):
    return result.get(key) if isinstance(result, dict) else None


# endpoint: what it may change, read the same way before (result None) and after the route.
WATCH = {
    'products.create_product': lambda con, view, body, result: products(con, [_result_id(result)]),
    'products.update_product': lambda con, view, body, result: products(con, [view.get('pid')]),
    'products.patch_product_inventory_identity': lambda con, view, body, result: products(con, [view.get('pid')]),
    'products.edit_product_design': lambda con, view, body, result: products(con, [view.get('pid')]),
    'products.remove_product_design': lambda con, view, body, result: products(con, [view.get('pid')]),
    'products.add_product_designs': lambda con, view, body, result: products(con, sub_items(con, view.get('pid'))),
    'products.remove_product_designs': lambda con, view, body, result: products(con, sub_items(con, view.get('pid'))),
    'products.add_product_sealed_set': lambda con, view, body, result: products(con, sub_items(con, view.get('pid'))),
    'products.bulk_delete_products': lambda con, view, body, result: products(con, _body_ids(body)),
    'products.save_alias': lambda con, view, body, result: products(con, _body_ids(body)),
    'products.delete_alias': lambda con, view, body, result: products(con, [view.get('pid')]),
    'inventory.set_bestseller': lambda con, view, body, result: products(con, [view.get('pid')]),
    'design_prices.design_price_save': lambda con, view, body, result: products(con, _body_ids(body)),
    'schedule.schedule_shifts_create': lambda con, view, body, result: shifts(con, [_result_id(result)]),
    'schedule.schedule_shifts_update': lambda con, view, body, result: shifts(con, [view.get('shift_id')]),
    'schedule.schedule_shifts_delete': lambda con, view, body, result: shifts(con, [view.get('shift_id')]),
    'schedule.schedule_employee_patch': lambda con, view, body, result: employees(con, [view.get('emp_id')]),
    'users.patch_employee_color': lambda con, view, body, result: employees(con, [view.get('employee_id')]),
    'users.patch_employee_schedulable': lambda con, view, body, result: employees(con, [view.get('employee_id')]),
    'users.patch_employee_status': lambda con, view, body, result: employees(con, [view.get('employee_id')]),
    'users.put_employee_stores': lambda con, view, body, result: employees(con, [view.get('employee_id')]),
    'users.create_user': lambda con, view, body, result: employees(con, subjects=[_result_id(result)]),
    'users.update_user': lambda con, view, body, result: employees(con, subjects=[view.get('uid')]),
    'users.delete_user': lambda con, view, body, result: employees(con, subjects=[view.get('uid')]),
    'stores.grant_inventory_access': lambda con, view, body, result: access(con, (body or {}).get('auth0_sub')),
    'stores.change_inventory_viewer': lambda con, view, body, result: access(con, (body or {}).get('auth0_sub')),
    'settings.put_settings': lambda con, view, body, result: settings(con, list(body) if isinstance(body, dict) else []),
    'website.web_orders_step': lambda con, view, body, result: web_order(con, view.get('wc_order_id')),
    'website.website_order_dismiss': lambda con, view, body, result: web_order(con, view.get('wc_order_id')),
    'website.website_order_retry': lambda con, view, body, result: web_order(con, view.get('wc_order_id')),
}


def read(con, endpoint, view, body, result=None):
    """What this request may change, as {(type, id): record}; {} when its kind of change keeps no fields."""
    watcher = WATCH.get(endpoint)
    return watcher(con, view or {}, body, result) if watcher else {}


def _fields(before, after):
    changed = []
    for label in list(after) + [label for label in before if label not in after]:
        old, new = before.get(label), after.get(label)
        if old != new:
            changed.append({'label': label, 'before': old, 'after': new})
    return changed


def diff(before, after):
    """The records that changed, with only the fields that changed. A new record lists what it was made with; a
    removed one what it had."""
    found = []
    for key in list(after) + [key for key in before if key not in after]:
        old, new = before.get(key), after.get(key)
        record = new or old
        if old is None:
            op, fields = 'add', [{'label': label, 'before': None, 'after': value}
                                 for label, value in new['fields'].items() if value is not None]
        elif new is None:
            op, fields = 'remove', [{'label': label, 'before': value, 'after': None}
                                    for label, value in old['fields'].items() if value is not None]
        else:
            op, fields = 'edit', _fields(old['fields'], new['fields'])
            if not fields:
                continue
        found.append({'type': key[0], 'label': record['label'], 'link': record.get('link'), 'op': op, 'fields': fields})
    return found


def secret_fields(endpoint, body):
    """Changes the request made that are kept as a word only: a new password is never kept, only that there was one;
    a role lives in Auth0, so what it was before is not known here."""
    if endpoint != 'users.update_user' or not isinstance(body, dict):
        return []
    fields = []
    if body.get('role'):
        fields.append({'label': '角色 Role', 'before': None, 'after': str(body['role'])})
    if body.get('password'):
        fields.append({'label': '密码 Password', 'before': None, 'after': '已改 Changed'})
    return fields


# ── Stock ────────────────────────────────────────────────────────────────────

PLACES = {'upstairs': '楼上', 'warehouse': '仓库', 'floor': '楼下', 'display': 'Display', 'claw': '娃娃机'}
DISPOSITIONS = {'hold': '留货', 'damaged': '损坏', 'transit': '在路上', 'trade': '交换', 'display': '展示'}


def track_document(document_id, request_key):
    """inventory_commands names each document it builds to the request posting it."""
    g.setdefault('activity_documents', []).append((document_id, request_key))


def _outside(document, side):
    """Where goods come from or go to when that end is not a stock place."""
    kind, source = document['kind'], document['source_type'] or ''
    if kind == 'opening':
        return '开账 Opening'
    if kind == 'correction':
        return '盘点 Count' if 'count' in source else '调整 Adjusted'
    if side == 'from':
        return '进货 Arrived'
    if 'loss' in source or 'damaged' in source:
        return '损耗 Loss'
    if source in ('daily_report', 'website_order', 'sale_document', 'checkout', 'sale'):
        return '卖出 Sold'
    return '出库存 Out'


def stock(con, documents):
    """The stock lines of the inventory documents a request posted, in order, each place's count before → after."""
    places, labels, lines = {}, {}, []

    def place(location_id, disposition):
        if location_id not in places:
            row = con.execute("""SELECT l.code, l.name, s.code AS store FROM inventory_locations l
                                 JOIN stores s ON s.id=l.store_id WHERE l.id=?""", (location_id,)).fetchone()
            places[location_id] = f"{row['store']} {PLACES.get(row['code'], row['name'])}" if row else f'#{location_id}'
        extra = DISPOSITIONS.get(disposition)
        return f'{places[location_id]} · {extra}' if extra else places[location_id]

    def label(product_id):
        if product_id not in labels:
            row = con.execute('SELECT sku, jizhanming, name_cn_en FROM products WHERE id=?', (product_id,)).fetchone()
            labels[product_id] = (' '.join(filter(None, (row['sku'], row['jizhanming'] or row['name_cn_en'])))
                                  if row else f'#{product_id}')
        return labels[product_id]

    seen = set()
    for document_id, request_key in documents:
        if document_id in seen:
            continue
        seen.add(document_id)
        document = con.execute("""SELECT id, kind, source_type, reason, stored_result FROM inventory_documents
                                  WHERE id=? AND request_key=? AND status='posted'""",
                               (document_id, request_key)).fetchone()
        if document is None:   # rolled back with the request (its id may be another document's by now)
            continue
        moves = con.execute("""SELECT line_no, product_id, location_id, disposition, quantity FROM inventory_movements
                               WHERE document_id=? ORDER BY id""", (document_id,)).fetchall()
        try:
            after = {(row['product_id'], row['location_id'], row['disposition']): row['quantity']
                     for row in json.loads(document['stored_result'] or '{}').get('balances') or []}
        except (TypeError, ValueError):
            after = {}
        net = defaultdict(int)
        for move in moves:
            net[(move['product_id'], move['location_id'], move['disposition'])] += move['quantity']
        running = {}
        for key, change in net.items():
            if key not in after:
                row = con.execute("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                                     AND disposition=?""", key).fetchone()
                after[key] = row[0] if row else change
            running[key] = after[key] - change
        by_line = {}
        for move in moves:
            key = (move['product_id'], move['location_id'], move['disposition'])
            start = running[key]
            running[key] += move['quantity']
            side = {'place': place(move['location_id'], move['disposition']), 'before': start, 'after': running[key]}
            line = by_line.setdefault(move['line_no'], {})
            line['from' if move['quantity'] < 0 else 'to'] = side
            line['from_product' if move['quantity'] < 0 else 'to_product'] = move['product_id']
            line['from_qty' if move['quantity'] < 0 else 'to_qty'] = abs(move['quantity'])
        lines.extend(_doc_lines(document, list(by_line.values()), label))
    return lines


def _doc_lines(document, moves, label):
    """One line per document line. A document that takes one thing off and puts another on (a set brought down to the
    floor as its boxes) reads as one line: the set from upstairs, into its boxes on the floor."""
    off = [line for line in moves if 'to' not in line]
    on = [line for line in moves if 'from' not in line]
    if len(off) == 1 and len(on) == 1 and len(moves) == 2:
        moves = [{**off[0], **on[0]}]
    found = []
    for line in moves:
        entry = {'product': label(line.get('from_product') or line.get('to_product')),
                 'qty': line.get('from_qty') or line.get('to_qty'),
                 'from': line.get('from') or {'place': _outside(document, 'from')},
                 'to': line.get('to') or {'place': _outside(document, 'to')}}
        if 'from_product' in line and 'to_product' in line and (
                line['to_product'] != line['from_product'] or line['to_qty'] != line['from_qty']):
            entry['into'] = f"{label(line['to_product'])} ×{line['to_qty']}"   # a set opened as its boxes
        if document['reason']:
            entry['why'] = document['reason']
        found.append(entry)
    return found


def collect(con, endpoint, view, body, result, before, documents):
    """What one 动态 line changed, or None when the request changed nothing that is kept."""
    records = diff(before or {}, read(con, endpoint, view, body, result)) if endpoint in WATCH else []
    extra = secret_fields(endpoint, body)
    if extra:
        person = next((record for record in records if record['type'] == 'employee'), None)
        if person is None:
            records.append({'type': 'employee', 'label': people.name(con, (view or {}).get('uid')) or '',
                            'link': '/users', 'op': 'edit', 'fields': extra})
        else:
            person['fields'] = extra + person['fields']
    lines = stock(con, documents) if documents else []
    if not records and not lines:
        return None
    changes = {'v': 1}
    if records:
        changes['records'] = records[:RECORDS_MAX]
        if len(records) > RECORDS_MAX:
            changes['records_more'] = len(records) - RECORDS_MAX
    if lines:
        changes['stock'] = lines[:STOCK_MAX]
        if len(lines) > STOCK_MAX:
            changes['stock_more'] = len(lines) - STOCK_MAX
    return changes


def brief(changes, most=3, width=24):
    """The line's few words for an edit, from what really changed ("价格 Price $19.99 → $20.99"), so the list says
    what moved without opening it. None for anything but edits, whose own words stay."""
    records = (changes or {}).get('records') or []
    if not records or any(record['op'] != 'edit' for record in records):
        return None

    def cut(value):
        value = '—' if value is None else value.replace('\n', ' ')
        return value if len(value) <= width else value[:width - 1] + '…'
    fields = [field for record in records for field in record['fields']]
    words = [f"{field['label']} {cut(field['before'])} → {cut(field['after'])}" for field in fields[:most]]
    return '、'.join(words) + (f' +{len(fields) - most}' if len(fields) > most else '')
