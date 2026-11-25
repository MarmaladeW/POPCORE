"""移货 Move stock: one way to move goods, whatever the move is (owner, 2026-11-24).

"all of these in the app, but I wanna simple things down, simple down all of the moving around of stock, because right
now a lot of the different entries does the same thing with the stock, what I want is a easy to use app. Keep all of
the existing data."

Every stock move is three answers: from where, to where, which goods and how many. The places are the store's own
(楼上 or 仓库, 楼下, Display, 娃娃机), the other store, and two ends outside the stock: 新到货 (goods arriving from
outside) and 不在了 (broken, lost, given away, a claw prize). So 补货 is 楼上 → 楼下, a delivery is 新到货 → 楼上, a
transfer is 楼上 → the other store, 入 display is anywhere → Display, a claw prize is 娃娃机 → 不在了. One request
saves the whole move at once through the same ledger as every other stock change (inventory_commands); nothing waits
for a second step.

- Whole sets (整端) live only upstairs and in the warehouse; the floor, display and the claw hold boxes. A set that
  comes down lands as its boxes, as it always did.
- The move is what happened: when POPCORE holds fewer at the source than were moved, it moves what it has and the
  rest still arrives at the destination (the source count was low; the line says so and the source is worth
  counting). Going 不在了 takes only what POPCORE has.
- Loose boxes taken from a back room that has only whole sets open as many sets as they need; the boxes not moved
  stay loose there.
- 不在了 is a loss, kept where losses always were (stock_losses, the manager's 损耗 page): 坏了 sets the goods aside on
  the damaged pile at the place (owner, 2026-11-03), where a manager puts them back on sale or throws them away; 丢了,
  送人 / 赠品 and 其他 leave stock at once. A claw prize is not a loss and only leaves the claw.
- 撤销: the person who recorded it, that day, or a manager puts a move back, while the goods are still where it left
  them.
"""
import json

from flask import Blueprint, jsonify, request

import activity
import checkout_access
import people
from auth import role_required
from db import get_db
from goods_operations import _balance, _begin, _remember, _replay
from inventory_catalog import sealed_set_for
from inventory_commands import (InventoryConflict, InventoryError, InventoryValidationError,
                                _post_inventory_in_transaction, require_inventory_access, store_is_live)
from report_stock import _available, _name, _set_for_box, _stock_product, can_have_set
from validation import read_date, read_int

PLACE_NAMES = {'upstairs': '楼上', 'warehouse': '仓库', 'floor': '楼下', 'display': 'Display', 'claw': '娃娃机'}
ORDER = ('upstairs', 'warehouse', 'floor', 'display', 'claw')
BACK = ('upstairs', 'warehouse')        # where whole sets are kept
NEW, GONE = 'new', 'gone'
REASONS = {'claw_prize': '娃娃机出奖', 'broken': '坏了', 'lost': '丢了', 'gift': '送人 / 赠品', 'other': '其他'}
# How each reason reads on the 损耗 page (stock_losses.reason); a claw prize is not a loss.
LOSS_REASONS = {'broken': 'broken', 'lost': 'missing', 'gift': 'other', 'other': 'other'}
MAX_LINES, MAX_UNITS, MAX_SET_SIZE = 60, 100000, 100


def _manager(actor):
    from checkout_operations import manager
    return manager(actor)


def _locations(con, store_id):
    return {row['code']: dict(row) for row in con.execute(
        'SELECT id, code, store_id FROM inventory_locations WHERE store_id=? AND is_active=1', (store_id,))}


def places(con, store_id, actor):
    """The answers this store offers for 从哪里 and 到哪里."""
    store_id = read_int(store_id, 'store_id', minimum=1)
    require_inventory_access(con, actor, (store_id,), 'staff')
    here = _locations(con, store_id)
    own = [{'code': code, 'name': PLACE_NAMES[code]} for code in ORDER if code in here]
    others = [{'code': f"store:{row['code']}", 'name': f"{row['code']} 店"} for row in con.execute(
        'SELECT id, code FROM stores WHERE is_active=1 AND id<>? ORDER BY id', (store_id,))
        if store_is_live(con, row['id']) and any(code in _locations(con, row['id']) for code in BACK)]
    return {'from': own + [{'code': NEW, 'name': '新到货'}], 'to': own + others + [{'code': GONE, 'name': '不在了'}],
            'reasons': [{'code': code, 'name': name} for code, name in REASONS.items()],
            # What most moves are: down from the back room to the floor.
            'usual': {'from': next((code for code in BACK if code in here), None), 'to': 'floor' if 'floor' in here else None}}


def _place(con, store_id, code, side):
    """(location or None, display name, the place's store id) for a 从哪里 / 到哪里 answer."""
    if not isinstance(code, str):
        raise InventoryValidationError(f'{side} is required')
    if code == (NEW if side == 'from' else GONE):
        return None, '新到货' if side == 'from' else '不在了', None
    if code.startswith('store:') and side == 'to':
        other = con.execute('SELECT id, code FROM stores WHERE code=? AND is_active=1 AND id<>?', (code[6:], store_id)).fetchone()
        if other is None or not store_is_live(con, other['id']):
            raise InventoryValidationError('That store cannot receive stock')
        there = _locations(con, other['id'])
        back = next((there[name] for name in BACK if name in there), None)
        if back is None:
            raise InventoryValidationError('That store has no upstairs or warehouse')
        return back, f"{other['code']} 店", other['id']
    location = _locations(con, store_id).get(code)
    if location is None or code not in PLACE_NAMES:
        raise InventoryValidationError(f'{side} is not a place at this store')
    return location, PLACE_NAMES[code], store_id


def _holds_sets(location):
    return location is not None and location['code'] in BACK


def _read(con, data, actor):
    """Validate a move and resolve each line to its products: no stock is read or changed here."""
    if not isinstance(data, dict):
        raise InventoryValidationError('body must be an object')
    store_id = read_int(data.get('store_id'), 'store_id', minimum=1)
    require_inventory_access(con, actor, (store_id,), 'staff')
    if not store_is_live(con, store_id):
        raise InventoryConflict('这家店的库存还没盘点上线。This store is not counted in POPCORE yet.', 'inventory_legacy_mode')
    source, source_name, _ = _place(con, store_id, data.get('from'), 'from')
    target, target_name, target_store = _place(con, store_id, data.get('to'), 'to')
    if source is None and target is None:
        raise InventoryValidationError('新到货 has to arrive somewhere in the store')
    if source is not None and target is not None and source['id'] == target['id']:
        raise InventoryValidationError('从哪里 and 到哪里 are the same place')
    reason, note = data.get('reason'), data.get('note') or ''
    if not isinstance(note, str) or len(note) > 300:
        raise InventoryValidationError('note must be text up to 300 characters')
    note = note.strip()
    if target is None:
        if reason not in REASONS:
            raise InventoryValidationError('选一个原因 Choose why it is gone')
        if reason == 'other' and not note:
            raise InventoryValidationError('写一下原因 Say what happened')
    else:
        reason = None
    raw_lines = data.get('lines')
    if not isinstance(raw_lines, list) or not raw_lines or len(raw_lines) > MAX_LINES:
        raise InventoryValidationError(f'Add 1 to {MAX_LINES} products')
    lines, seen = [], set()
    for raw in raw_lines:
        if not isinstance(raw, dict):
            raise InventoryValidationError('Each product must be an object')
        product = con.execute('SELECT * FROM products WHERE id=?', (read_int(raw.get('product_id'), 'product_id', minimum=1),)).fetchone()
        if product is None:
            raise InventoryValidationError('product_id is invalid')
        name = _name(product)
        if product['identity_status'] != 'verified' or not product['stock_form']:
            raise InventoryConflict(f'「{name}」还没核对好，告诉店长。This product is not reviewed yet.', 'identity_unverified')
        if not product['stock_counted']:
            raise InventoryConflict(f'「{name}」不计库存，不用移。This product is sold without a stock count.', 'stock_not_counted')
        sets, units = read_int(raw.get('sets') or 0, 'sets', minimum=0), read_int(raw.get('units') or 0, 'units', minimum=0)
        if product['stock_form'] == 'sealed_set':
            # A set picked by itself is so many whole sets of its box.
            box, per = _stock_product(con, product)
            if per is None:
                raise InventoryConflict(f'「{name}」没记每端几盒，先在 产品 里记。Its set size was never recorded.', 'set_size_unknown')
            whole, sets, units = product, sets + units, 0
        else:
            box, whole = product, _set_for_box(con, product['id']) if product['stock_form'] == 'random_box' else None
            per = whole['boxes_per_set'] if whole is not None else None
        if box['id'] in seen:
            raise InventoryValidationError(f'「{_name(box)}」写了两次，合成一行。The same product is listed twice.')
        seen.add(box['id'])
        if sets + units < 1 or sets > MAX_UNITS or units > MAX_UNITS:
            raise InventoryValidationError(f'「{_name(box)}」填数量 Enter how many')
        set_size = raw.get('set_size')
        if sets and whole is None:
            # New goods may bring a blind box its first whole sets, as a count card does: say how many boxes a set holds.
            if source is None and _holds_sets(target) and set_size is not None and can_have_set(con, box):
                set_size = read_int(set_size, 'set_size', minimum=2)
                if set_size > MAX_SET_SIZE:
                    raise InventoryValidationError(f'A whole set cannot hold {set_size} boxes')
            else:
                raise InventoryConflict(f'「{_name(box)}」没有整端，按盒填。This product has no whole set; enter boxes.', 'no_whole_set')
        else:
            set_size = None
        if sets and source is not None and not _holds_sets(source):
            raise InventoryConflict(f'{source_name}只有散盒，「{_name(box)}」按盒填。Only boxes are kept there; enter boxes.', 'no_sets_here')
        lines.append({'box': box, 'whole': whole, 'per': per, 'sets': sets, 'units': units, 'set_size': set_size})
    return {'store_id': store_id, 'source': source, 'target': target, 'source_name': source_name, 'target_name': target_name,
            'target_store': target_store, 'from': data['from'], 'to': data['to'], 'reason': reason, 'note': note, 'lines': lines}


def _steps(con, move, line):
    """What one line posts, as (kind, [ledger lines], units POPCORE lacked at the source). Stock is read now."""
    source, target = move['source'], move['target']
    box, whole, per, sets, units = line['box'], line['whole'], line['per'], line['sets'], line['units']
    steps, missing = [], 0

    def side(product, location, quantity, name, disposition='saleable'):
        return {'product_id': product['id'], 'unit': product['stock_unit'], 'quantity': quantity,
                f'{name}_location_id': location['id'], f'{name}_disposition': disposition}

    def gone(product, quantity):
        # 坏了 is set aside on the damaged pile at the same place (owner, 2026-11-03); anything else leaves stock.
        if move['reason'] == 'broken':
            return 'move', [{**side(product, source, quantity, 'from'), **side(product, source, quantity, 'to', 'damaged')}]
        return 'consume', [side(product, source, quantity, 'from')]

    if source is None:
        # 新到货: whole sets stay whole in a back room; anywhere else they arrive as their boxes.
        if sets and _holds_sets(target):
            steps.append(('receipt', [side(whole, target, sets, 'to')]))
            sets = 0
        arriving = units + sets * (per or 0)
        if arriving:
            steps.append(('receipt', [side(box, target, arriving, 'to')]))
        return steps, 0
    sets_taken = 0
    if sets:
        have = sets_taken = min(sets, _balance(con, whole['id'], source['id'], 'saleable')['quantity'])
        short = sets - have
        if target is None:
            if have:
                steps.append(gone(whole, have))
        elif _holds_sets(target):
            if have:
                steps.append(('move', [{**side(whole, source, have, 'from'), **side(whole, target, have, 'to')}]))
            if short:
                steps.append(('receipt', [side(whole, target, short, 'to')]))
        else:
            if have:
                steps.append(('correction', [side(whole, source, have, 'from'), side(box, target, have * per, 'to')]))
            if short:
                steps.append(('receipt', [side(box, target, short * per, 'to')]))
        missing += short * per
    opened = 0
    if units:
        have = min(units, _available(con, box, source['id']))
        short = units - have
        if short and whole is not None and per and _holds_sets(source):
            # Loose boxes taken out of whole sets: the sets they need open here, and what is not moved stays loose.
            opened = min(_balance(con, whole['id'], source['id'], 'saleable')['quantity'] - sets_taken, -(-short // per))
            if opened > 0:
                steps.append(('correction', [side(whole, source, opened, 'from'), side(box, source, opened * per, 'to')]))
                have = min(units, have + opened * per)
                short = units - have
        if target is None:
            if have:
                steps.append(gone(box, have))
        else:
            if have:
                steps.append(('move', [{**side(box, source, have, 'from'), **side(box, target, have, 'to')}]))
            if short:
                steps.append(('receipt', [side(box, target, short, 'to')]))
        missing += short
    line['opened'] = max(opened, 0)
    return steps, missing


def product(con, store_id, product_id, actor):
    """What the screen needs to ask the right quantities for one product: its unit, boxes per whole set, and
    whether new goods may give it its first whole sets."""
    store_id = read_int(store_id, 'store_id', minimum=1)
    require_inventory_access(con, actor, (store_id,), 'staff')
    row = con.execute('SELECT * FROM products WHERE id=?', (read_int(product_id, 'product_id', minimum=1),)).fetchone()
    if row is None:
        raise InventoryValidationError('product_id is invalid')
    if row['stock_form'] == 'sealed_set':
        row = _stock_product(con, row)[0]     # a set is asked for as whole sets of its box
    whole = _set_for_box(con, row['id']) if row['stock_form'] == 'random_box' else None
    return {'product_id': row['id'], 'sku': row['sku'], 'name': _name(row), 'unit': row['stock_unit'],
            'per_set': whole['boxes_per_set'] if whole is not None else None,
            'can_have_set': whole is None and can_have_set(con, row),
            'movable': row['identity_status'] == 'verified' and bool(row['stock_form']) and bool(row['stock_counted'])}


def _line_view(line, missing=None):
    return {'product_id': line['box']['id'], 'sku': line['box']['sku'], 'name': _name(line['box']), 'per_set': line['per'],
            'unit': line['box']['stock_unit'], 'sets': line['sets'], 'units': line['units'],
            **({} if missing is None else {'missing': missing})}


def check(con, data, actor):
    """What a move would do, before it is saved: each line by name with its set size, and where POPCORE holds fewer
    than are being moved (`missing`). Nothing is written."""
    move = _read(con, data, actor)
    lines = []
    for line in move['lines']:
        _, missing = _steps(con, move, line)
        lines.append({**_line_view(line, missing), 'opened': line.get('opened', 0)})
    return {'from': move['from'], 'to': move['to'], 'from_name': move['source_name'], 'to_name': move['target_name'], 'lines': lines}


def _post(con, kind, ledger, reason, store_id, actor, key):
    """Post one ledger document with the balances as they stand now."""
    lines = []
    for entry in ledger:
        versions = {name: _balance(con, entry['product_id'], entry[f'{name}_location_id'], entry[f'{name}_disposition'])['version']
                    for name in ('from', 'to') if f'{name}_location_id' in entry}
        lines.append({**entry, 'expected_versions': versions})
    source_type, _, source_id = key.partition(':')
    return _post_inventory_in_transaction(con, {
        'kind': kind, 'business_date': checkout_access.business_date(), 'source_type': source_type, 'source_id': source_id,
        'reason': reason, 'lines': lines,
    }, actor=actor, request_key=f'inventory:{key}', access_store_ids=(store_id,), minimum_role='staff')['document_id']


def create(con, data, actor, request_key):
    """Save one move: every line at once, or nothing."""
    move = _read(con, data, actor)
    intent = {'store_id': move['store_id'], 'from': move['from'], 'to': move['to'], 'reason': move['reason'], 'note': move['note'],
              'lines': [[line['box']['id'], line['sets'], line['units'], line['set_size']] for line in move['lines']]}
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'stock_move', actor, intent)
        if prior:
            con.commit()
            return view(con, prior['id'], actor)
        move = _read(con, data, actor)
        move_id = con.execute(
            """INSERT INTO stock_moves(store_id, business_date, from_code, to_code, from_name, to_name, reason, note, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (move['store_id'], checkout_access.business_date(), move['from'], move['to'], move['source_name'],
             move['target_name'], move['reason'], move['note'], actor['sub'])).lastrowid
        label = f"移货 {move['source_name']} → {move['target_name']}" + (f" · {REASONS[move['reason']]}" if move['reason'] else '') \
            + (f" · {move['note']}" if move['note'] else '')
        for number, line in enumerate(move['lines'], 1):
            if line['set_size']:
                # A blind box's first whole sets give it its sealed set (1 set = this many boxes).
                sealed_set_for(con, line['box']['id'], line['set_size'])
                line['whole'], line['per'] = _set_for_box(con, line['box']['id']), line['set_size']
            steps, missing = _steps(con, move, line)
            for part, (kind, ledger) in enumerate(steps, 1):
                document = _post(con, kind, ledger, label, move['store_id'], actor, f'stock_move:{move_id}:{number}:{part}')
                if move['target'] is None and move['reason'] in LOSS_REASONS and kind in ('consume', 'move'):
                    _loss(con, move, ledger[0], document, actor)
            con.execute("""INSERT INTO stock_move_lines(move_id, line_no, product_id, sets, units, per_set, missing, posted)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (move_id, number, line['box']['id'], line['sets'], line['units'], line['per'], missing,
                         json.dumps([[kind, ledger] for kind, ledger in steps])))
        _remember(con, key, 'stock_move', move_id, actor, digest, {'id': move_id})
        con.commit()
    except Exception:
        con.rollback()
        raise
    return view(con, move_id, actor)


def _loss(con, move, entry, document, actor):
    """不在了 is a loss like any other: one row where the manager's 损耗 page reads them, at the price of that day."""
    price = con.execute('SELECT price FROM products WHERE id=?', (entry['product_id'],)).fetchone()['price']
    note = ' · '.join(filter(None, ('送人 / 赠品' if move['reason'] == 'gift' else '', move['note'])))
    con.execute("""INSERT INTO stock_losses(store_id, location_id, product_id, quantity, unit, reason, note, unit_price,
                                            business_date, document_id, actor_sub)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (move['store_id'], entry['from_location_id'], entry['product_id'], entry['quantity'], entry['unit'],
                 LOSS_REASONS[move['reason']], note or None, price if price and price > 0 else None,
                 checkout_access.business_date(), document, actor['sub']))


def _reverse(kind, ledger):
    """The ledger document that puts one posted step back."""
    flip = {'from': 'to', 'to': 'from'}

    def turned(entry):
        return {('_'.join([flip[key.split('_', 1)[0]], key.split('_', 1)[1]]) if key.split('_', 1)[0] in flip else key): value
                for key, value in entry.items()}
    return {'receipt': 'consume', 'consume': 'receipt', 'move': 'move', 'correction': 'correction'}[kind], [turned(entry) for entry in ledger]


def undo(con, move_id, actor, request_key):
    """撤销: put a move back, while the goods are still where it left them."""
    move_id = read_int(move_id, 'move_id', minimum=1)
    _begin(con)
    try:
        row = con.execute('SELECT * FROM stock_moves WHERE id=?', (move_id,)).fetchone()
        if row is None:
            raise InventoryValidationError('That move does not exist')
        require_inventory_access(con, actor, (row['store_id'],), 'staff')
        key, digest, prior = _replay(con, request_key, 'stock_move_undo', actor, {'id': move_id})
        if prior:
            con.commit()
            return view(con, move_id, actor)
        if row['undone_at']:
            raise InventoryConflict('这一笔已经撤销了。This move was already undone.', 'stock_move_undone')
        if not _manager(actor) and (row['created_by'] != actor['sub'] or row['business_date'] != checkout_access.business_date()):
            raise PermissionError('Only the person who recorded it, that day, or a manager can undo a move')
        label = f"撤销移货 #{move_id} {row['from_name']} → {row['to_name']}"
        try:
            for line in con.execute('SELECT * FROM stock_move_lines WHERE move_id=? ORDER BY line_no DESC', (move_id,)).fetchall():
                for part, (kind, ledger) in reversed(list(enumerate(json.loads(line['posted']), 1))):
                    back_kind, back_ledger = _reverse(kind, ledger)
                    put_back = _post(con, back_kind, back_ledger, label, row['store_id'], actor,
                                     f"stock_move_undo:{move_id}:{line['line_no']}:{part}")
                    # A loss this step recorded counts for nothing once it is put back.
                    con.execute("""UPDATE stock_losses SET undo_document_id=?, undone_by=?, undone_at=datetime('now')
                                   WHERE undo_document_id IS NULL AND document_id=(
                                       SELECT id FROM inventory_documents WHERE source_type='stock_move' AND source_id=?)""",
                                (put_back, actor['sub'], f"{move_id}:{line['line_no']}:{part}"))
        except InventoryConflict as exc:
            if exc.code in ('insufficient_stock', 'stale_version'):
                raise InventoryConflict('货已经不在那里了（卖了或又移走了），撤销不了；数量不对请用 盘点 改。'
                                        'The goods are no longer where this move left them; correct the count instead.',
                                        'stock_move_gone') from exc
            raise
        con.execute("UPDATE stock_moves SET undone_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), undone_by=? WHERE id=?",
                    (actor['sub'], move_id))
        _remember(con, key, 'stock_move_undo', move_id, actor, digest, {'id': move_id})
        con.commit()
    except Exception:
        con.rollback()
        raise
    return view(con, move_id, actor)


def text_of(line):
    """"米菲圣诞 1端" / "SA Santa 4盒" / "蒙奇奇 2" for a saved line."""
    unit = {'box': '盒', 'set': '端'}.get(line['unit'], '')
    parts = ([f"{line['sets']}端"] if line['sets'] else []) + ([f"{line['units']}{unit}"] if line['units'] else [])
    return f"{line['name']} {'➕'.join(parts)}"


def view(con, move_id, actor=None, today=None):
    row = con.execute('SELECT * FROM stock_moves WHERE id=?', (move_id,)).fetchone()
    lines = []
    for line in con.execute("""SELECT l.*, p.sku, p.jizhanming, p.name_cn_en, p.stock_unit FROM stock_move_lines l
                               JOIN products p ON p.id=l.product_id WHERE l.move_id=? ORDER BY l.line_no""", (move_id,)):
        entry = {'product_id': line['product_id'], 'sku': line['sku'], 'name': _name(line), 'unit': line['stock_unit'],
                 'sets': line['sets'], 'units': line['units'], 'per_set': line['per_set'], 'missing': line['missing']}
        lines.append({**entry, 'text': text_of(entry)})
    today = today or checkout_access.business_date()
    mine = bool(actor) and actor.get('sub') == row['created_by']
    return {'id': row['id'], 'store_id': row['store_id'], 'business_date': row['business_date'], 'created_at': row['created_at'],
            'from': row['from_code'], 'to': row['to_code'], 'from_name': row['from_name'], 'to_name': row['to_name'],
            'reason': REASONS.get(row['reason']), 'note': row['note'], 'by': people.name(con, row['created_by']), 'lines': lines,
            'undone': {'at': row['undone_at'], 'by': people.name(con, row['undone_by'])} if row['undone_at'] else None,
            'can_undo': bool(actor) and not row['undone_at'] and (_manager(actor) or (mine and row['business_date'] == today))}


def day(con, store_id, business_date, actor):
    """A store's moves on one day, newest first."""
    store_id = read_int(store_id, 'store_id', minimum=1)
    require_inventory_access(con, actor, (store_id,), 'staff')
    today = checkout_access.business_date()
    business_date = read_date(business_date or today, 'business_date')
    return {'store_id': store_id, 'business_date': business_date, 'today': today,
            'moves': [view(con, row['id'], actor, today) for row in con.execute(
                'SELECT id FROM stock_moves WHERE store_id=? AND business_date=? ORDER BY id DESC', (store_id, business_date))]}


def migrate(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS stock_moves (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id      INTEGER NOT NULL REFERENCES stores(id),
        business_date TEXT    NOT NULL,
        from_code     TEXT    NOT NULL,
        to_code       TEXT    NOT NULL,
        from_name     TEXT    NOT NULL,
        to_name       TEXT    NOT NULL,
        reason        TEXT,
        note          TEXT    NOT NULL DEFAULT '',
        created_by    TEXT    NOT NULL,
        created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        undone_at     TEXT,
        undone_by     TEXT
    )""")
    cur.execute('CREATE INDEX IF NOT EXISTS idx_stock_moves_store_day ON stock_moves(store_id, business_date, id)')
    cur.execute("""CREATE TABLE IF NOT EXISTS stock_move_lines (
        move_id    INTEGER NOT NULL REFERENCES stock_moves(id),
        line_no    INTEGER NOT NULL,
        product_id INTEGER NOT NULL REFERENCES products(id),
        sets       INTEGER NOT NULL DEFAULT 0,
        units      INTEGER NOT NULL DEFAULT 0,
        per_set    INTEGER,
        missing    INTEGER NOT NULL DEFAULT 0,
        posted     TEXT    NOT NULL DEFAULT '[]',
        PRIMARY KEY (move_id, line_no)
    )""")


# ── Routes ───────────────────────────────────────────────────────────────────

bp = Blueprint('stock_moves', __name__)


@bp.errorhandler(InventoryError)
@bp.errorhandler(PermissionError)
@bp.errorhandler(ValueError)
def _error(exc):
    if isinstance(exc, InventoryError):
        return jsonify(error=str(exc), code=exc.code), exc.status
    if isinstance(exc, PermissionError):
        return jsonify(error='你没有这家店的门店操作权限。You do not have store access here.', code='inventory_forbidden'), 403
    return jsonify(error=str(exc), code='invalid_input'), 400


def _body():
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


@bp.get('/api/stock-moves/places')
@role_required('staff')
def stock_move_places():
    return jsonify(places(get_db(), request.args.get('store_id'), request.jwt_payload))


@bp.get('/api/stock-moves/products/<int:product_id>')
@role_required('staff')
def stock_move_product(product_id):
    return jsonify(product(get_db(), request.args.get('store_id'), product_id, request.jwt_payload))


@bp.post('/api/stock-moves/check')
@role_required('staff')
def stock_move_check():
    return jsonify(check(get_db(), _body(), request.jwt_payload))


@bp.get('/api/stock-moves')
@role_required('staff')
def stock_move_day():
    return jsonify(day(get_db(), request.args.get('store_id'), request.args.get('date'), request.jwt_payload))


def _note(move, word):
    goods = '、'.join(line['text'] for line in move['lines'][:6]) + ('…' if len(move['lines']) > 6 else '')
    short = sum(1 for line in move['lines'] if line['missing'])
    activity.note(store_id=move['store_id'], kind='stock', audience='store', link='/move',
                  summary=f"{word} · {move['from_name']} → {move['to_name']}",
                  detail=' · '.join(filter(None, [goods, move['reason'], move['note'],
                                                  f'{short} 项电脑里不够 short on record' if short else ''])))


@bp.post('/api/stock-moves')
@role_required('staff')
def stock_move_create():
    move = create(get_db(), _body(), request.jwt_payload, request.headers.get('Idempotency-Key'))
    _note(move, '移货 Moved stock')
    return jsonify(move), 201


@bp.post('/api/stock-moves/<int:move_id>/undo')
@role_required('staff')
def stock_move_undo(move_id):
    move = undo(get_db(), move_id, request.jwt_payload, request.headers.get('Idempotency-Key'))
    _note(move, '撤销移货 Undid a move')
    return jsonify(move)
