"""A live store's daily report takes its sales off the floor (owner, 2026-10-29).

Clover is not connected yet, so the pasted daily report is how a live store's sales leave
stock. Whenever a day's sales change (a report is pasted, replaced or edited), each product's
sales that day are compared with what that day already took off, and only the difference moves:
more sold comes off the floor's saleable stock, less sold goes back. Stock never goes below
zero: what the floor does not have is not taken off, and the next save of that day tries again.
Only days from the store's go-live date on count, because the opening counts were taken before
them. A sealed set sold takes its set size in boxes, since the floor keeps blind boxes as boxes
only; a set with no recorded set size takes nothing, because the size is never assumed.

Every mismatch is kept as a notice for managers and admins (owner, 2026-10-29): sales that could
not come off (not counted here, not enough on the floor, set size unknown) and sales taken off a
product the floor counted the same day, which may already have left them out. A notice closes by
itself once a later save takes the rest off, or when a manager checks it: checking a shortfall
means the floor count already reflects those sales, so they are never taken off later.

The report's stock lines move too (owner, 2026-11-03): 出库入店 upstairs (or the warehouse) to the
floor, a written set size opening whole sets as boxes; 入店 new goods onto the floor; 出店 off the
floor; 卖display a sale from display, which the floor then does not take; 拆display floor to display;
入display upstairs to display; 娃娃机 a prize off the claw. A `(display)` line goes to display instead
of the floor. Each line is kept per day in `report_stock_moves` with what it should move and what it
moved, so a re-pasted day moves only the difference, as sales do, and what a source does not have
waits as a `move_short` notice.

More of the report moves by itself (owner, 2026-11-13), so a raw paste needs no hand steps: 入库 new goods
into upstairs (or the warehouse), a written set size as whole sets, and a blind box's first whole sets give it
its sealed set as the count card does; 出库 off this store's upstairs and onto the other live store at once,
whole sets and boxes to its upstairs or warehouse, or to its floor when the manager says so; 出店入库 floor
to upstairs; 出display入店 display to the floor, a written set size opening the display's whole set as boxes;
出店display off display and out of the store. A day's lines post arrivals first, so what a report brings in
can feed its own later lines. 入娃娃机 refills are not moved here: staff record them on the claw screen.
Days before `report_lines_from` (the day this went live) were recorded by hand, so their reports keep the old
reading of those sections and never move them a second time.
"""
import hashlib
import json
import uuid

from catalog_identity import CatalogConflict
from goods_operations import _balance
from identity_sheet import SUGGESTED_IDENTITY
from inventory_catalog import sealed_set_for
from inventory_commands import (InventoryConflict, InventoryError, _post_inventory_in_transaction, live_store_ids,
                                require_inventory_access, store_is_live)

REPORT_REASON = '日报 Daily report'
RETURN_REASON = '日报更正 Daily report corrected'
# Sales the floor could not take off; a check waives them. counted_same_day only warns.
SHORT_REASONS = ('not_counted', 'not_enough_on_floor', 'set_size_unknown')
# A report's stock lines, by the section they came from (owner, 2026-11-03).
LINE_SECTIONS = {
    'refill': '出库入店', 'stock_in': '入店', 'stock_out': '出店', 'display_sold': '卖display',
    'break_display': '拆display', 'display_stock_in': '入display', 'claw_prize': '娃娃机',
    # Owner, 2026-11-13: these move by the report too.
    'back_in': '入库', 'transfer_out': '出库', 'floor_to_back': '出店入库', 'display_to_floor': '出display入店',
}
# Lines written as sets, boxes or pieces: "X 12*8" is 8 sets of 12, "X*3" is 3 boxes or pieces.
PACKED_SECTIONS = ('refill', 'stock_in', 'back_in', 'transfer_out', 'floor_to_back', 'display_to_floor')
# The order a day's lines post in: what arrives first, then what moves inside the store, then what leaves,
# so a report's own arrivals feed its later lines (DT 2026-11-11: 拆display of a box that day's 入店 brought).
_STAGES = {'back_in': 0, 'stock_in': 1, 'display_to_floor': 2, 'refill': 3, 'display_stock_in': 3,
           'break_display': 4, 'floor_to_back': 5, 'transfer_out': 6}
_LAST_STAGE = 7
MAX_SET_SIZE = 100


class ReportLineError(InventoryError):
    """A report stock line this store cannot move as written; the manager fixes that line."""
    def __init__(self, message, line):
        super().__init__(message, 'report_line_invalid', 409)
        self.line = line


def live_since(con, store_id):
    """The business date a store went live, or None while it is not live."""
    row = con.execute(
        """SELECT MIN(d.business_date) FROM inventory_scope_state s
           JOIN inventory_documents d ON d.id=s.opening_document_id
           WHERE s.store_id=? AND s.opening_verified=1""", (store_id,)).fetchone()
    return row[0] if row else None


def _stock_product(con, product):
    """(product that holds the stock, units per one sold). A sealed set sells as its boxes, by the
    set size recorded for it; with no set size recorded the units are unknown (None)."""
    if product['stock_form'] != 'sealed_set':
        return product, 1
    box = con.execute(
        """SELECT p.*, c.output_per_input AS boxes_per_set FROM product_conversions c
           JOIN products p ON p.id=c.target_product_id
           WHERE c.source_product_id=? ORDER BY c.version DESC LIMIT 1""", (product['id'],)).fetchone()
    if box is None or not box['boxes_per_set'] or box['boxes_per_set'] < 1:
        return product, None
    return box, box['boxes_per_set']


def _loose(con, product_id, location_id):
    """Saleable boxes not kept in an opened set: what a sale can take without asking."""
    kept = con.execute(
        """SELECT COALESCE(SUM(remaining_qty), 0) FROM inventory_open_sets
           WHERE random_product_id=? AND location_id=? AND remaining_qty>0""",
        (product_id, location_id)).fetchone()[0]
    return max(0, _balance(con, product_id, location_id, 'saleable')['quantity'] - kept)


def _counted_since(con, location_id, day, product_id, since):
    """When the floor last counted this product on `day` after `since` (None: any time that day)."""
    row = con.execute(
        """SELECT MAX(c.created_at) FROM inventory_counts c JOIN inventory_count_lines l ON l.count_id=c.id
           WHERE c.location_id=? AND c.business_date=? AND c.status='approved' AND l.product_id=?
             AND (? IS NULL OR c.created_at > ?)""", (location_id, day, product_id, since, since)).fetchone()
    return row[0]


def _floor(con, store_id):
    return con.execute(
        "SELECT id FROM inventory_locations WHERE store_id=? AND code='floor' AND is_active=1", (store_id,),
    ).fetchone()


def _require_manager(con, actor, store_id):
    """Only managers and admins with this store's inventory access paste reports (owner, 2026-10-29)."""
    try:
        require_inventory_access(con, actor, [store_id], 'manager')
    except PermissionError as exc:
        raise InventoryError('Only a manager or admin with access to this store can change its daily report',
                             'inventory_forbidden', 403) from exc


def _name(product):
    return product['jizhanming'] or product['name_cn_en'] or product['sku']


def _locations(con, store_id):
    return {row['code']: row['id'] for row in con.execute(
        'SELECT code, id FROM inventory_locations WHERE store_id=? AND is_active=1', (store_id,))}


def _set_for_box(con, box_id):
    """The sealed set (整端) that opens into this blind box, with its recorded boxes per set."""
    return con.execute(
        """SELECT p.*, c.output_per_input AS boxes_per_set FROM product_conversions c
           JOIN products p ON p.id=c.source_product_id
           WHERE c.target_product_id=? AND p.stock_form='sealed_set'
           ORDER BY c.version DESC, c.id DESC LIMIT 1""", (box_id,)).fetchone()


def lines_from(con):
    """The first business date whose report moves 入库, 出库, 出店入库, 出display入店 and 出店display by itself
    (app_settings report_lines_from, set when this went live), or None when every day does. Earlier days had
    them recorded by hand."""
    row = con.execute("SELECT value FROM app_settings WHERE key='report_lines_from'").fetchone()
    return (row[0] or '').strip() or None if row else None


def other_store(con, store_id):
    """Where 出库 goes (owner, 2026-11-13: off this store and onto the other at once): the one other live
    store, with its upstairs or warehouse and its floor where their opening is verified. None when no other
    store is live, or more than one is and the report cannot say which."""
    live = live_store_ids(con) - {store_id}
    stores = [row for row in con.execute('SELECT id, code FROM stores WHERE is_active=1 ORDER BY id') if row['id'] in live]
    if len(stores) != 1:
        return None
    at = {row['code']: row['id'] for row in con.execute(
        """SELECT l.code, l.id FROM inventory_locations l JOIN inventory_scope_state ss ON ss.location_id=l.id
           WHERE l.store_id=? AND l.is_active=1 AND ss.opening_verified=1""", (stores[0]['id'],))}
    return {'id': stores[0]['id'], 'code': stores[0]['code'], 'back': at.get('upstairs') or at.get('warehouse'),
            'floor': at.get('floor')}


def can_have_set(con, product):
    """A blind box with no sealed set yet, whose first whole sets can give it one as the count card does: a
    blind box, or a product not counted yet whose type says blind box (盲盒), with a SKU whose -SET is free.
    A report line alone never makes anything else a blind box."""
    form = product['stock_form'] or (SUGGESTED_IDENTITY.get((product['product_type'] or '').strip()) or (None, None))[0]
    return (form == 'random_box' and bool(product['sku'])
            and not con.execute('SELECT 1 FROM products WHERE sku=?', (f"{product['sku']}-SET",)).fetchone())


def _sets_and_units(con, line, item, product, create=False):
    """A line written as sets, boxes or pieces: (its whole set or None, the box or piece, boxes per set, whole
    sets, loose units). With no whole set recorded every unit written is one box or piece. `create` gives a
    blind box its sealed set on its first whole sets (owner, 2026-11-13: 入库 "hellokitty苹果贴贴12*8"), as the
    count card does; inside a preview that is rolled back with everything else."""
    name, size, sets, loose = _name(product), item['box_size'], item['num_boxes'], item['loose_qty']
    if product['stock_form'] == 'sealed_set':
        box, per = _stock_product(con, product)
        if per is None:
            raise ReportLineError(f'{name}: how many boxes a set holds was never recorded; record it in 产品 Products', line)
        if size not in (1, per):
            raise ReportLineError(f'{name}: the report says {size} per set, POPCORE records {per}', line)
        return product, box, per, sets, loose
    whole = _set_for_box(con, product['id']) if size > 1 and product['stock_form'] in (None, 'random_box') else None
    if whole is None and create and size > 1 and can_have_set(con, product):
        if size > MAX_SET_SIZE:
            raise ReportLineError(f'{name}: a whole set cannot hold {size} boxes; check the line', line)
        try:
            sealed_set_for(con, product['id'], size)
        except (InventoryError, CatalogConflict) as exc:
            raise ReportLineError(f'{name}: its whole set (整端) could not be added: {exc}', line) from exc
        product = con.execute('SELECT * FROM products WHERE id=?', (product['id'],)).fetchone()
        whole = _set_for_box(con, product['id'])
    if whole is None:
        return None, product, 1, 0, size * sets + loose
    if size != whole['boxes_per_set']:
        raise ReportLineError(f"{name}: the report says {size} per set, POPCORE records {whole['boxes_per_set']}", line)
    return whole, product, whole['boxes_per_set'], sets, loose


def _line_moves(con, line, item, product, at, back, other=None):
    """What one report stock line moves: (kind, from product, from location, to product, to location,
    destination units per source unit, source units). 整端 whole sets stay upstairs; the floor and display
    hold blind boxes as boxes, so a set that comes down arrives as its recorded number of boxes."""
    section, name = item['section'], _name(product)

    def need(code, what):
        if code not in at:
            raise ReportLineError(f'{name}: this store has no {what}', line)
        return at[code]

    if product['stock_form'] == 'sealed_set' and (item.get('to_display') or section in (
            'display_sold', 'break_display', 'display_stock_in', 'claw_prize')):
        raise ReportLineError(f'{name} is a whole set (整端); pick its box or design for this line', line)
    if section in ('refill', 'stock_in'):
        if section == 'refill' and back is None:
            raise ReportLineError(f'{name}: this store has no upstairs or warehouse', line)
        size, sets, loose = item['box_size'], item['num_boxes'], item['loose_qty']
        if item.get('to_display'):
            display = need('display', 'display')
            units = size * sets + loose
            if section == 'refill':
                return [('move', product, back, product, display, 1, units)]
            return [('receipt', None, None, product, display, 1, units)]
        floor = need('floor', 'floor')
        whole, box, per, sets, loose = _sets_and_units(con, line, item, product)
        if section == 'stock_in':
            return [('receipt', None, None, box, floor, 1, per * sets + loose)]
        moves = []
        if whole is not None and sets:
            moves.append(('correction', whole, back, box, floor, per, sets))
        if loose:
            moves.append(('move', box, back, box, floor, 1, loose))
        return moves
    if section in ('back_in', 'transfer_out', 'floor_to_back', 'display_to_floor'):
        if section != 'display_to_floor' and back is None:
            raise ReportLineError(f'{name}: this store has no upstairs or warehouse', line)
        whole, box, per, sets, loose = _sets_and_units(con, line, item, product, create=section == 'back_in')
        if section == 'back_in':
            # New goods straight into upstairs or the warehouse: whole sets stay whole there.
            return ([('receipt', None, None, whole, back, 1, sets)] if whole is not None and sets else []) + (
                [('receipt', None, None, box, back, 1, loose)] if loose else [])
        if section == 'floor_to_back':
            # The floor holds boxes only, so what goes back up is boxes.
            return [('move', box, need('floor', 'floor'), box, back, 1, per * sets + loose)]
        if section == 'display_to_floor':
            display, floor = need('display', 'display'), need('floor', 'floor')
            return ([('correction', whole, display, box, floor, per, sets)] if whole is not None and sets else []) + (
                [('move', box, display, box, floor, 1, loose)] if loose else [])
        if other is None:
            raise ReportLineError(f'{name}: 出库 goes to the other store, and POPCORE cannot tell which store that is '
                                  '(none other is live, or more than one); record it with 调拨 Transfer', line)
        if item.get('to_floor'):
            # The other store's floor holds boxes only: a whole set arrives there as its boxes.
            if other['floor'] is None:
                raise ReportLineError(f"{name}: {other['code']} has no counted floor to receive this", line)
            return ([('correction', whole, back, box, other['floor'], per, sets)] if whole is not None and sets else []) + (
                [('move', box, back, box, other['floor'], 1, loose)] if loose else [])
        if other['back'] is None:
            raise ReportLineError(f"{name}: {other['code']} has no counted upstairs or warehouse to receive this", line)
        return ([('move', whole, back, whole, other['back'], 1, sets)] if whole is not None and sets else []) + (
            [('move', box, back, box, other['back'], 1, loose)] if loose else [])
    qty = item['qty']
    if section == 'stock_out':
        box, per = _stock_product(con, product)
        if per is None:
            raise ReportLineError(f'{name}: how many boxes a set holds was never recorded; record it in 产品 Products', line)
        source = need('display', 'display') if item.get('from_display') else need('floor', 'floor')
        return [('consume', box, source, None, None, 1, qty * per)]
    if section == 'display_sold':
        return [('consume', product, need('display', 'display'), None, None, 1, qty)]
    if section == 'break_display':
        return [('move', product, need('floor', 'floor'), product, need('display', 'display'), 1, qty)]
    if section == 'display_stock_in':
        if back is None:
            raise ReportLineError(f'{name}: this store has no upstairs or warehouse', line)
        return [('move', product, back, product, need('display', 'display'), 1, qty)]
    if section == 'claw_prize':
        return [('consume', product, need('claw', 'claw machine'), None, None, 1, qty)]
    raise ReportLineError(f'{name}: {section} is not a report stock section', line)


def _describe(item):
    """The line as the report wrote it, for the reason on its stock documents."""
    text = item.get('raw_name') or ''
    if item.get('notes'):
        text += f"（{item['notes']}）"
    if item['section'] in PACKED_SECTIONS:
        size, sets, loose = item['box_size'], item['num_boxes'], item['loose_qty']
        amount = (f'{size}*{sets}' if size > 1 else str(sets)) + (f'+{loose}' if loose else '')
    else:
        amount = str(item['qty'])
    mark = '（display）' if item.get('to_display') else '（到对方楼下）' if item.get('to_floor') else ''
    return f"{text} {amount}{mark}".strip()


def plan_report_lines(con, store_id, items):
    """Resolve a report's stock lines (pairs of payload line number and item) to the balances each
    moves, merged by what they move. Raises ReportLineError for a line this store cannot move."""
    at = _locations(con, store_id)
    back = at.get('upstairs') or at.get('warehouse')
    other = other_store(con, store_id)
    planned = {}
    # 入库 first: a set it adds is the set the day's other lines of that box then open or send.
    for line, item in sorted(items, key=lambda pair: pair[1]['section'] != 'back_in'):
        product = con.execute('SELECT * FROM products WHERE id=?', (item['product_id'],)).fetchone()
        for kind, source, source_at, target, target_at, ratio, units in _line_moves(con, line, item, product, at, back,
                                                                                   other):
            if units < 1:
                continue
            shape = [item['section'], kind, source and source['id'], source_at, target and target['id'], target_at, ratio]
            if item['section'] == 'back_in' and target['stock_form'] == 'sealed_set':
                # A set this report adds has no id until it saves: its line is known by its box, so the
                # preview the manager confirmed and the save are the same line.
                shape[4] = f"set-of:{_stock_product(con, target)[0]['id']}"
            key = hashlib.sha256(json.dumps(shape).encode()).hexdigest()[:16]
            entry = planned.setdefault(key, {
                'line_key': key, 'section': item['section'], 'kind': kind,
                'from_product_id': source and source['id'], 'from_location_id': source_at,
                'to_product_id': target and target['id'], 'to_location_id': target_at,
                'ratio': ratio, 'wanted': 0, 'raw': []})
            entry['wanted'] += units
            entry['raw'].append(_describe(item))
    return [{**entry, 'raw': '；'.join(entry['raw'])} for entry in planned.values()]


def save_report_lines(con, store_id, day, lines):
    """This report is now the day: its lines are what the day should move. A line no longer on it
    wants nothing, so the next sync puts back what it moved."""
    con.execute('UPDATE report_stock_moves SET wanted=0 WHERE store_id=? AND business_date=?', (store_id, day))
    for line in lines:
        con.execute(
            """INSERT INTO report_stock_moves(store_id, business_date, line_key, section, kind, from_product_id,
                   from_location_id, to_product_id, to_location_id, ratio, wanted, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(store_id, business_date, line_key) DO UPDATE SET wanted=excluded.wanted, raw=excluded.raw""",
            (store_id, day, line['line_key'], line['section'], line['kind'], line['from_product_id'],
             line['from_location_id'], line['to_product_id'], line['to_location_id'], line['ratio'],
             line['wanted'], line['raw']))


def _available(con, product, location_id):
    if product['stock_form'] == 'random_box':
        return _loose(con, product['id'], location_id)
    return _balance(con, product['id'], location_id, 'saleable')['quantity']


def _places(con, store_id):
    """Every active location as (code, the store's code when it is another store's): a line may reach the
    other store's upstairs, warehouse or floor."""
    return {row['id']: (row['code'], None if row['store_id'] == store_id else row['store_code'])
            for row in con.execute("""SELECT l.id, l.code, l.store_id, s.code AS store_code
                                      FROM inventory_locations l JOIN stores s ON s.id=l.store_id""")}


def _sync_lines(con, actor, store_id, store_code, day, counted):
    """Move each of the day's stock lines by the difference between what it should move and what it
    moved (or a manager checked). Arrivals post first and what leaves posts last; a line still short is
    tried again while other lines keep moving, so a line fed by another line of the same report is never
    left short by the order they were written or reviewed in. Returns each line's effect, the products a
    source was short of and the documents posted."""
    places = _places(con, store_id)
    where = lambda location_id: ':'.join(filter(None, reversed(places.get(location_id, ('', None)))))
    product = lambda product_id: con.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
    stored = lambda key: con.execute("""SELECT * FROM report_stock_moves WHERE store_id=? AND business_date=?
                                        AND line_key=?""", (store_id, day, key)).fetchone()
    results, documents = {}, []
    rows = con.execute("""SELECT rowid, * FROM report_stock_moves WHERE store_id=? AND business_date=?
                          ORDER BY rowid""", (store_id, day)).fetchall()
    pending = [row['line_key'] for row in sorted(rows, key=lambda row: (_STAGES.get(row['section'], _LAST_STAGE),
                                                                         row['rowid']))]
    while pending:
        moved_any, again = False, []
        for key in pending:
            row = stored(key)
            if not (row['wanted'] or row['moved'] or row['checked']):
                if not row['posts']:
                    con.execute('DELETE FROM report_stock_moves WHERE store_id=? AND business_date=? AND line_key=?',
                                (store_id, day, key))
                continue
            source = product(row['from_product_id']) if row['from_product_id'] else None
            target = product(row['to_product_id']) if row['to_product_id'] else None
            out = row['wanted'] - row['moved'] - row['checked']
            sides = [(side, item, location_id, 1 if side == 'from' else row['ratio'])
                     for side, item, location_id in (('from', source, row['from_location_id']),
                                                     ('to', target, row['to_location_id'])) if item is not None]
            before = {side: _balance(con, item['id'], location_id, 'saleable')['quantity']
                      for side, item, location_id, _ in sides}
            # Forward a line takes from its source; putting it back takes from where it went.
            taking = [entry for entry in sides if entry[0] == ('from' if out > 0 else 'to')]
            moving, missing, problem = 0, 0, None
            if out:
                need = abs(out)
                if any(item['identity_status'] != 'verified' for _, item, _, _ in sides):
                    missing, problem = need, 'not_counted'
                else:
                    if taking:
                        _, item, location_id, per = taking[0]
                        moving = min(need, _available(con, item, location_id) // per)
                    else:
                        moving = need
                    missing = need - moving
                    problem = 'not_enough' if missing else None
                if moving:
                    sign = 1 if out > 0 else -1
                    lines = []
                    for side, item, location_id, per in sides:
                        adds = (side == 'to') == (sign > 0)
                        balance = _balance(con, item['id'], location_id, 'saleable')
                        lines.append({'product_id': item['id'], 'unit': item['stock_unit'], 'quantity': moving * per,
                                      f"{'to' if adds else 'from'}_location_id": location_id,
                                      f"{'to' if adds else 'from'}_disposition": 'saleable',
                                      'expected_versions': {'to' if adds else 'from': balance['version']}})
                    kind = row['kind'] if sign > 0 else 'correction'
                    if kind == 'move':
                        # A move is one line from its source to its destination.
                        lines = [{**lines[0], **lines[1], 'expected_versions': {**lines[0]['expected_versions'],
                                                                                **lines[1]['expected_versions']}}]
                    sequence = row['posts'] + 1
                    label = LINE_SECTIONS.get(row['section'], row['section'])
                    reason = f"{REPORT_REASON if sign > 0 else RETURN_REASON} {day} {label}：{row['raw']}"
                    # The report is this store's: its manager's paste also puts 出库 onto the other store
                    # (owner, 2026-11-13), so only this store's access is asked for.
                    documents.append(_post_inventory_in_transaction(con, {
                        'kind': kind, 'business_date': day, 'source_type': 'daily_report',
                        'source_id': f"{store_code}:{day}:{key}:{sequence}", 'reason': reason, 'lines': lines,
                    }, actor=actor, request_key=f"daily-report:{store_code}:{day}:{key}:{sequence}",
                        access_store_ids=(store_id,), minimum_role='manager')['document_id'])
                    since = row['updated_at'] if row['posts'] else None
                    con.execute("""UPDATE report_stock_moves SET moved=moved+?, posts=?, updated_at=datetime('now')
                                   WHERE store_id=? AND business_date=? AND line_key=?""",
                                (sign * moving, sequence, store_id, day, key))
                    for _, item, location_id, per in sides:
                        # A count of this store the same day may already show what the line moved.
                        when = None if places[location_id][1] else _counted_since(con, location_id, day, item['id'], since)
                        if when:
                            _, quantity, _ = counted.get(item['id'], (item, 0, when))
                            counted[item['id']] = (item, quantity + moving * per, when)
                    moved_any = True
                if missing and problem == 'not_enough':
                    again.append(key)
            result = results.setdefault(key, {
                'line_key': key, 'section': row['section'], 'label': LINE_SECTIONS.get(row['section']),
                'raw': row['raw'], 'kind': row['kind'], 'ratio': row['ratio'], 'wanted': row['wanted'],
                'moved_before': row['moved'], 'checked': row['checked'], 'moving': 0, 'document_id': None,
                'sides': sides, 'before': before})
            result['moving'] += (1 if out > 0 else -1) * moving
            # Each side as this line left it, before the day's later lines touch the same stock.
            result.update(short=missing, problem=problem, short_at=taking[0] if taking else sides[0],
                          document_id=documents[-1] if moving else result['document_id'],
                          after={side: _balance(con, item['id'], location_id, 'saleable')['quantity']
                                 for side, item, location_id, _ in sides})
        # Lines still short try again only while something else moved: it may have brought what they lack.
        pending = again if moved_any else []
    short = {}
    for result in results.values():
        sides, before, after = result.pop('sides'), result.pop('before'), result.pop('after')
        _, item, location_id, per = result.pop('short_at')
        if result['short']:
            entry = short.setdefault(item['id'], [item, 0, set()])
            entry[1] += result['short'] * per
            entry[2].add('not_counted' if result['problem'] == 'not_counted' else where(location_id))
        for side, item, location_id, per in sides:
            result[side] = {'product_id': item['id'], 'sku': item['sku'], 'name': _name(item), 'unit': item['stock_unit'],
                            'location': places[location_id][0], 'store': places[location_id][1], 'per': per,
                            'before': before[side], 'after': after[side]}
    return list(results.values()), short, documents


def _display_relief(con, store_id, day, wanted):
    """卖display: a sale that came from display is not taken off the floor. It relieves the product
    sold, or the series it belongs to when the sale was written under the series (owner, 2026-11-03),
    or a design of its series that sold, when display keeps that piece as the series' box (MK's display)."""
    relief = {}
    for row in con.execute(
            """SELECT p.id, p.series_id, SUM(m.wanted) AS sold FROM report_stock_moves m JOIN products p ON p.id=m.from_product_id
               WHERE m.store_id=? AND m.business_date=? AND m.section='display_sold' AND m.wanted>0
               GROUP BY p.id""", (store_id, day)).fetchall():
        target = row['id']
        if target not in wanted and row['series_id'] is not None:
            head = con.execute('SELECT product_id FROM product_series WHERE id=?', (row['series_id'],)).fetchone()
            target = head['product_id'] if head else None
        if target not in wanted and row['series_id'] is not None:
            sold = [product_id for product_id in wanted if wanted[product_id] > relief.get(product_id, 0)]
            design = con.execute(
                f"""SELECT id FROM products WHERE series_id=? AND stock_form='confirmed_design'
                    AND id IN ({','.join('?' * len(sold)) or 'NULL'}) ORDER BY id LIMIT 1""",
                (row['series_id'], *sold)).fetchone()
            target = design['id'] if design else target
        if target in wanted:
            give = min(row['sold'], wanted[target] - relief.get(target, 0))
            if give > 0:
                relief[target] = relief.get(target, 0) + give
    return relief


def sync_report_stock(con, actor, store_id, store_code, day):
    """Inside the caller's write transaction: move a live store's stock to match its daily report on
    `day`, its stock lines first and then its sales off the floor (less what sold from display).
    Returns what moved and what could not, or None when the day does not count."""
    since = live_since(con, store_id)
    if not store_is_live(con, store_id) or since is None or day < since:
        return None
    floor = _floor(con, store_id)
    if floor is None:
        return None
    _require_manager(con, actor, store_id)
    counted = {}
    # The stock lines move first, so boxes that came down or arrived that day can sell.
    lines, moves_short, documents = _sync_lines(con, actor, store_id, store_code, day, counted)
    wanted, products, unknown_size = {}, {}, set()
    for row in con.execute(
            """SELECT p.*, SUM(s.qty_sold) AS sold
               FROM daily_sales s JOIN products p ON p.id=s.product_id
               WHERE s.date=? AND s.store=? GROUP BY p.id HAVING SUM(s.qty_sold) > 0""", (day, store_code)):
        product, per = _stock_product(con, row)
        if per is None:
            unknown_size.add(product['id'])
            per = 1
        wanted[product['id']] = wanted.get(product['id'], 0) + row['sold'] * per
        products[product['id']] = product
    relief = _display_relief(con, store_id, day, wanted)
    for product_id, units in relief.items():
        wanted[product_id] -= units
    kept = {row['product_id']: row for row in con.execute(
        'SELECT * FROM report_stock_takes WHERE store_id=? AND business_date=?', (store_id, day))}
    for product_id in set(kept) - set(products):
        products[product_id] = con.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
    take_lines, return_lines, short, changes = [], [], {}, {}
    for product_id in sorted(set(wanted) | set(kept)):
        product = products[product_id]
        taken = kept[product_id]['taken'] if product_id in kept else 0
        checked = kept[product_id]['checked'] if product_id in kept else 0
        delta = wanted.get(product_id, 0) - taken - checked
        if delta < 0:
            # Fewer sold: sales a manager already checked off go first, since they never moved.
            unchecked = min(checked, -delta)
            give = -delta - unchecked
            if give:
                balance = _balance(con, product_id, floor['id'], 'saleable')
                return_lines.append({'product_id': product_id, 'unit': product['stock_unit'], 'quantity': give,
                                     'to_location_id': floor['id'], 'to_disposition': 'saleable',
                                     'expected_versions': {'to': balance['version']}})
            changes[product_id] = (-give, -unchecked)
            continue
        if delta == 0:
            continue
        if product_id in unknown_size:
            short[product_id] = (product, delta, 'set_size_unknown')
            continue
        if product['identity_status'] != 'verified':
            short[product_id] = (product, delta, 'not_counted')
            continue
        balance = _balance(con, product_id, floor['id'], 'saleable')
        available = (_loose(con, product_id, floor['id']) if product['stock_form'] == 'random_box'
                     else balance['quantity'])
        take = min(delta, available)
        if take:
            take_lines.append({'product_id': product_id, 'unit': product['stock_unit'], 'quantity': take,
                               'from_location_id': floor['id'], 'from_disposition': 'saleable',
                               'expected_versions': {'from': balance['version']}})
            changes[product_id] = (take, 0)
            last = kept[product_id]['updated_at'] if product_id in kept and taken else None
            when = _counted_since(con, floor['id'], day, product_id, last)
            if when:
                _, quantity, _ = counted.get(product_id, (product, 0, when))
                counted[product_id] = (product, quantity + take, when)
        if take < delta:
            short[product_id] = (product, delta - take, 'not_enough_on_floor')
    for kind, ledger, reason in (('consume', take_lines, REPORT_REASON), ('correction', return_lines, RETURN_REASON)):
        if not ledger:
            continue
        # A fresh key each time: what moves is always the difference from what this day already took.
        key = uuid.uuid4().hex
        document = _post_inventory_in_transaction(con, {
            'kind': kind, 'business_date': day, 'source_type': 'daily_report',
            'source_id': f'{store_code}:{day}:{key[:12]}', 'reason': reason, 'lines': ledger,
        }, actor=actor, request_key=f'inventory:daily-report:{key}', minimum_role='manager')
        documents.append(document['document_id'])
    for product_id, (taken, checked) in changes.items():
        if product_id in kept:
            # updated_at marks the last time this day took stock, for the same-day count check.
            con.execute("""UPDATE report_stock_takes SET taken=taken+?, checked=checked+?,
                               updated_at=CASE WHEN ? THEN datetime('now') ELSE updated_at END
                           WHERE store_id=? AND business_date=? AND product_id=?""",
                        (taken, checked, taken != 0, store_id, day, product_id))
        else:
            con.execute('INSERT INTO report_stock_takes(store_id, business_date, product_id, taken) VALUES (?, ?, ?, ?)',
                        (store_id, day, product_id, taken))
    _keep_notices(con, store_id, day, short, counted, moves_short)
    return {
        'business_date': day, 'documents': documents,
        'lines': lines,
        'from_display': [{'product_id': product_id, 'sku': products[product_id]['sku'],
                          'name': _name(products[product_id]), 'quantity': units}
                         for product_id, units in relief.items()],
        'moves_short': [{'product_id': item['id'], 'sku': item['sku'], 'name': _name(item), 'quantity': quantity,
                         'reason': 'move_short', 'where': sorted(where)}
                        for item, quantity, where in moves_short.values()],
        'taken': sum(taken for taken, _ in changes.values() if taken > 0),
        'returned': -sum(taken for taken, _ in changes.values() if taken < 0),
        'short': [{'product_id': product['id'], 'sku': product['sku'], 'name': _name(product),
                   'quantity': quantity, 'reason': reason} for product, quantity, reason in short.values()],
        'counted_same_day': [{'product_id': product['id'], 'sku': product['sku'], 'name': _name(product),
                              'quantity': quantity, 'counted_at': when}
                             for product, quantity, when in counted.values()],
    }


def _keep_notices(con, store_id, day, short, counted, moves_short):
    """Keep the day's notices in step with this save: open what it found, close shortfalls it settled."""
    for product_id, (_, quantity, where) in moves_short.items():
        con.execute(
            """INSERT INTO report_stock_issues(store_id, business_date, product_id, reason, quantity, detail)
               VALUES (?, ?, ?, 'move_short', ?, ?)
               ON CONFLICT(store_id, business_date, product_id, reason) DO UPDATE
               SET quantity=excluded.quantity, detail=excluded.detail, status='open', updated_at=datetime('now'),
                   checked_by=NULL, checked_at=NULL""",
            (store_id, day, product_id, quantity, ','.join(sorted(where))))
    for row in con.execute(
            """SELECT id, product_id FROM report_stock_issues
               WHERE store_id=? AND business_date=? AND status='open' AND reason='move_short'""",
            (store_id, day)).fetchall():
        if row['product_id'] not in moves_short:
            con.execute("UPDATE report_stock_issues SET status='resolved', updated_at=datetime('now') WHERE id=?",
                        (row['id'],))
    for product_id, (_, quantity, reason) in short.items():
        con.execute(
            """INSERT INTO report_stock_issues(store_id, business_date, product_id, reason, quantity)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(store_id, business_date, product_id, reason) DO UPDATE
               SET quantity=excluded.quantity, status='open', updated_at=datetime('now'),
                   checked_by=NULL, checked_at=NULL""", (store_id, day, product_id, reason, quantity))
    marks = ','.join('?' * len(SHORT_REASONS))
    for row in con.execute(
            f"""SELECT id, product_id, reason FROM report_stock_issues
                WHERE store_id=? AND business_date=? AND status='open' AND reason IN ({marks})""",
            (store_id, day, *SHORT_REASONS)).fetchall():
        if short.get(row['product_id'], (None, 0, None))[2] != row['reason']:
            con.execute("UPDATE report_stock_issues SET status='resolved', updated_at=datetime('now') WHERE id=?",
                        (row['id'],))
    for product_id, (_, quantity, when) in counted.items():
        con.execute(
            """INSERT INTO report_stock_issues(store_id, business_date, product_id, reason, quantity, detail)
               VALUES (?, ?, ?, 'counted_same_day', ?, ?)
               ON CONFLICT(store_id, business_date, product_id, reason) DO UPDATE
               SET quantity=CASE WHEN status='open' THEN quantity+excluded.quantity ELSE excluded.quantity END,
                   detail=excluded.detail, status='open', updated_at=datetime('now'),
                   checked_by=NULL, checked_at=NULL""", (store_id, day, product_id, quantity, when))


def open_notices(con, store_ids):
    """Open notices at these stores, oldest day first, with each product's floor and upstairs stock now."""
    if not store_ids:
        return []
    marks = ','.join('?' * len(store_ids))
    notices = []
    for row in con.execute(
            f"""SELECT i.*, s.code AS store_code, p.sku, p.jizhanming, p.name_cn_en, p.stock_unit
                FROM report_stock_issues i JOIN stores s ON s.id=i.store_id JOIN products p ON p.id=i.product_id
                WHERE i.status='open' AND i.store_id IN ({marks}) ORDER BY i.business_date, i.store_id, i.id""",
            tuple(store_ids)):
        stock = {code: con.execute(
            """SELECT COALESCE(SUM(b.quantity), 0) FROM inventory_balances b JOIN inventory_locations l ON l.id=b.location_id
               WHERE l.store_id=? AND l.code=? AND b.product_id=? AND b.disposition='saleable'""",
            (row['store_id'], code, row['product_id'])).fetchone()[0]
            for code in ('floor', 'upstairs', 'warehouse', 'display', 'claw')}
        notices.append({'id': row['id'], 'store_id': row['store_id'], 'store_code': row['store_code'],
                        'business_date': row['business_date'], 'product_id': row['product_id'],
                        'sku': row['sku'], 'name': _name(row), 'unit': row['stock_unit'],
                        'reason': row['reason'], 'quantity': row['quantity'], 'counted_at': row['detail'] if row['reason'] == 'counted_same_day' else None,
                        'floor': stock['floor'], 'upstairs': stock['upstairs'], 'stock': stock,
                        'where': row['detail'].split(',') if row['reason'] == 'move_short' and row['detail'] else [],
                        'updated_at': row['updated_at']})
    return notices


def check_notice(con, actor, notice_id):
    """Inside the caller's write transaction: a manager checked this notice. A checked shortfall
    is taken as already reflected in the floor count, so it is never taken off later."""
    row = con.execute('SELECT * FROM report_stock_issues WHERE id=?', (notice_id,)).fetchone()
    if row is None:
        raise InventoryError('Notice not found', 'not_found', 404)
    _require_manager(con, actor, row['store_id'])
    if row['status'] != 'open':
        raise InventoryConflict('Someone already checked or settled this notice', 'notice_closed')
    if row['reason'] == 'move_short':
        # The count already reflects what these lines did not move: they never move it later.
        for line in con.execute('SELECT * FROM report_stock_moves WHERE store_id=? AND business_date=?',
                                (row['store_id'], row['business_date'])).fetchall():
            out = line['wanted'] - line['moved'] - line['checked']
            if (out > 0 and line['from_product_id'] == row['product_id']) or (
                    out < 0 and line['to_product_id'] == row['product_id']):
                con.execute("""UPDATE report_stock_moves SET checked=checked+?
                               WHERE store_id=? AND business_date=? AND line_key=?""",
                            (out, row['store_id'], row['business_date'], line['line_key']))
    elif row['reason'] in SHORT_REASONS:
        con.execute(
            """INSERT INTO report_stock_takes(store_id, business_date, product_id, taken, checked) VALUES (?, ?, ?, 0, ?)
               ON CONFLICT(store_id, business_date, product_id) DO UPDATE SET checked=checked+excluded.checked""",
            (row['store_id'], row['business_date'], row['product_id'], row['quantity']))
    con.execute("""UPDATE report_stock_issues SET status='checked', checked_by=?, checked_at=datetime('now'),
                   updated_at=datetime('now') WHERE id=?""", (actor['sub'], notice_id))
    return {'id': notice_id, 'status': 'checked'}
