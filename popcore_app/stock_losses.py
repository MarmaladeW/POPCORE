"""损耗 Loss (owner, 2026-11-03): managers and admins record stock that was broken, stolen or went missing.

Only managers and admins record losses, at a live store and with its 门店操作权限 Store operations access.
Broken goods are kept aside: they move from saleable stock to the damaged pile at the same location, where
a manager later puts them back on sale (to sell as 瑕疵) or throws them away. Stolen, missing and other
losses leave stock at once. A record keeps the product's price at that moment, so the page can say what a
month's losses were worth. A wrong record is undone with the exact reversal of its stock document, which
for broken goods needs them still on the damaged pile.
"""
import re
from collections import defaultdict

from catalog_identity import normalize_design_name, product_alias_text_sql
from checkout_access import business_date
from goods_operations import _balance, _begin, _remember, _replay
from inventory_commands import (InventoryConflict, InventoryError, InventoryValidationError,
                                _post_inventory_in_transaction, require_inventory_access, store_is_live)
from inventory_visibility import require_inventory_visible
from operational_reports import _inventory_identity_valid, _product_name
from validation import read_int

REASONS = {'broken': '破损 Broken', 'stolen': '被偷 Stolen', 'missing': '丢失 Missing', 'other': '其他 Other'}
PILE_ACTIONS = {'restore': '损坏放回可售 Back to sale', 'discard': '损坏报废 Thrown away'}
UNDO_REASON = '损耗撤销 Loss undone'
SEARCH_LIMIT = 20
NOTE_LIMIT = 500
MAX_QUANTITY = 100000
MONTH_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')
PRODUCT_SQL = '''SELECT p.id,p.sku,p.jizhanming,p.name_cn_en,p.price,p.stock_form,p.stock_unit,p.identity_status,
                        p.series_id,p.design_name,ps.name AS series_name
                 FROM products p LEFT JOIN product_series ps ON ps.id=p.series_id'''


def cents(price):
    """A product's price in cents, or None when it has none."""
    return round(price * 100) if price is not None and price > 0 else None


def _scope(con, actor, store_code, *, need_live=True):
    """The store and its reviewed locations, if actor may record losses there."""
    store = con.execute('SELECT id, code, name FROM stores WHERE code=? AND is_active=1',
                        ((store_code or '').strip().upper(),)).fetchone()
    if store is None:
        raise InventoryValidationError('Choose one store')
    store = {**dict(store), 'live': store_is_live(con, store['id'])}
    if not store['live']:
        if need_live:
            raise InventoryConflict(f"{store['code']} is not live yet: 损耗 starts once its stock is counted",
                                    'inventory_legacy_mode')
        return store, []
    try:
        require_inventory_access(con, actor, (store['id'],), 'manager')
    except PermissionError:
        raise PermissionError(f"损耗 Loss at {store['code']} is for managers and admins with 门店操作权限 Store "
                              f"operations access for {store['code']}.") from None
    require_inventory_visible(con, actor, (store['id'],), store['code'])
    locations = [dict(row) for row in con.execute(
        '''SELECT l.id, l.code, l.name FROM inventory_locations l
           JOIN inventory_scope_state ss ON ss.location_id=l.id
           WHERE l.store_id=? AND l.is_active=1 AND ss.opening_verified=1 ORDER BY l.id''', (store['id'],))]
    return store, locations


def _read(con, work):
    """Run a read with one consistent snapshot."""
    con.execute('BEGIN')
    try:
        return work()
    finally:
        if con.in_transaction:
            con.rollback()


def _product(con, product_id):
    product = con.execute(PRODUCT_SQL + ' WHERE p.id=?', (product_id,)).fetchone()
    if product is None or not _inventory_identity_valid(product):
        raise InventoryConflict('This product has no checked stock type yet. Set it in Products first.',
                                'identity_unverified')
    return product


def _available(con, product, location_id):
    """What a loss can take here: saleable stock, less blind boxes kept for an opened set."""
    balance = _balance(con, product['id'], location_id, 'saleable')
    if product['stock_form'] != 'random_box':
        return balance['quantity'], balance
    kept = con.execute(
        """SELECT COALESCE(SUM(remaining_qty), 0) FROM inventory_open_sets
           WHERE random_product_id=? AND location_id=? AND remaining_qty>0""",
        (product['id'], location_id)).fetchone()[0]
    return max(0, balance['quantity'] - kept), balance


def _note(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise InventoryValidationError('note must be text')
    value = ' '.join(value.split())
    if len(value) > NOTE_LIMIT:
        raise InventoryValidationError(f'Keep the note under {NOTE_LIMIT} characters')
    return value or None


def _location_of(locations, location_id):
    location = next((item for item in locations if item['id'] == location_id), None)
    if location is None:
        raise InventoryValidationError('Choose a counted location of this store')
    return location


def _records(con, where, params):
    rows = con.execute(
        f'''SELECT x.*, l.name AS location_name, l.code AS location_code,
                   COALESCE(NULLIF(e.name,''), x.actor_sub) AS actor_name,
                   COALESCE(NULLIF(u.name,''), x.undone_by) AS undone_by_name
            FROM stock_losses x JOIN inventory_locations l ON l.id=x.location_id
            LEFT JOIN employees e ON e.auth0_id=x.actor_sub
            LEFT JOIN employees u ON u.auth0_id=x.undone_by
            WHERE {where} ORDER BY x.business_date DESC, x.id DESC''', params).fetchall()
    products = {row['id']: row for row in con.execute(
        PRODUCT_SQL + f" WHERE p.id IN ({','.join('?' * len(rows))})", [row['product_id'] for row in rows])} \
        if rows else {}
    records = []
    for row in rows:
        product = products[row['product_id']]
        price = cents(row['unit_price'])
        records.append({
            'id': row['id'], 'business_date': row['business_date'], 'created_at': row['created_at'],
            'product_id': row['product_id'], 'name': _product_name(product), 'sku': product['sku'],
            'unit': row['unit'], 'quantity': row['quantity'], 'reason': row['reason'], 'note': row['note'],
            'location_id': row['location_id'], 'location_code': row['location_code'],
            'location_name': row['location_name'], 'unit_price_cents': price,
            'value_cents': price * row['quantity'] if price is not None else None,
            'actor_name': row['actor_name'], 'undone': row['undo_document_id'] is not None,
            'undone_at': row['undone_at'], 'undone_by_name': row['undone_by_name'],
        })
    return records


def search_products(con, actor, store_code, query):
    """Products in stock at this store matching a name, 记账名, SKU, design name, alias or an exact
    barcode, each with what a loss can take at each location."""
    text = (query or '').strip()

    def work():
        store, locations = _scope(con, actor, store_code)
        if not text or not locations:
            return {'results': []}
        by_id = {location['id']: location for location in locations}
        here = defaultdict(dict)
        for row in con.execute(
                f'''SELECT product_id, location_id, quantity FROM inventory_balances
                    WHERE disposition='saleable' AND quantity>0
                      AND location_id IN ({','.join('?' * len(by_id))})''', list(by_id)):
            here[row['product_id']][row['location_id']] = row['quantity']
        needle = normalize_design_name(text)
        scanned = {row[0] for row in con.execute('SELECT product_id FROM product_barcodes WHERE code=?', (text,))}
        hits = []
        for row in con.execute(PRODUCT_SQL.replace(
                'ps.name AS series_name', f"ps.name AS series_name, {product_alias_text_sql('p')} AS alias_text")):
            if row['id'] not in here or not _inventory_identity_valid(row):
                continue
            exact = row['id'] in scanned or (row['sku'] or '').casefold() == text.casefold()
            haystack = normalize_design_name(' '.join(str(row[field] or '') for field in (
                'sku', 'jizhanming', 'name_cn_en', 'design_name', 'alias_text', 'series_name')))
            if exact or needle in haystack:
                hits.append((0 if exact else 1 if haystack.startswith(needle) else 2, _product_name(row), row))
        results = []
        for _, name, product in sorted(hits, key=lambda hit: (hit[0], hit[1]))[:SEARCH_LIMIT]:
            places = []
            for location in locations:
                if location['id'] not in here[product['id']]:
                    continue
                available, balance = _available(con, product, location['id'])
                if available:
                    places.append({'location_id': location['id'], 'code': location['code'], 'name': location['name'],
                                   'available': available, 'version': balance['version']})
            if places:
                results.append({'product_id': product['id'], 'name': name, 'sku': product['sku'],
                                'unit': product['stock_unit'], 'stock_form': product['stock_form'],
                                'price_cents': cents(product['price']), 'places': places})
        return {'results': results}
    return _read(con, work)


def loss_page(con, actor, store_code, month=None):
    """A month's losses at one store, their totals by reason, and the damaged pile now."""
    month = (month or business_date()[:7]).strip()
    if not MONTH_RE.match(month):
        raise InventoryValidationError('month must be YYYY-MM')

    def work():
        store, locations = _scope(con, actor, store_code, need_live=False)
        if not store['live']:
            return {'live': False, 'store': store, 'month': month}
        records = _records(con, 'x.store_id=? AND x.business_date LIKE ?', (store['id'], f'{month}-%'))
        totals = {reason: {'units': 0, 'value_cents': 0, 'unpriced_units': 0, 'records': 0} for reason in REASONS}
        for record in records:
            if record['undone']:
                continue
            total = totals[record['reason']]
            total['records'] += 1
            total['units'] += record['quantity']
            if record['value_cents'] is None:
                total['unpriced_units'] += record['quantity']
            else:
                total['value_cents'] += record['value_cents']
        pile = []
        if locations:
            by_id = {location['id']: location for location in locations}
            rows = con.execute(
                f'''SELECT product_id, location_id, quantity, version FROM inventory_balances
                    WHERE disposition='damaged' AND quantity>0
                      AND location_id IN ({','.join('?' * len(by_id))})''', list(by_id)).fetchall()
            products = {row['id']: row for row in con.execute(
                PRODUCT_SQL + f" WHERE p.id IN ({','.join('?' * len(rows))})",
                [row['product_id'] for row in rows])} if rows else {}
            for row in rows:
                product, location = products[row['product_id']], by_id[row['location_id']]
                price = cents(product['price'])
                pile.append({'product_id': product['id'], 'name': _product_name(product), 'sku': product['sku'],
                             'unit': product['stock_unit'], 'location_id': location['id'],
                             'location_code': location['code'], 'location_name': location['name'],
                             'quantity': row['quantity'], 'version': row['version'], 'price_cents': price,
                             'value_cents': price * row['quantity'] if price is not None else None})
            pile.sort(key=lambda item: (item['location_id'], item['name']))
        return {'live': True, 'store': store, 'month': month, 'today': business_date(), 'locations': locations,
                'records': records, 'totals': totals,
                'units': sum(total['units'] for total in totals.values()),
                'value_cents': sum(total['value_cents'] for total in totals.values()),
                'unpriced_units': sum(total['unpriced_units'] for total in totals.values()), 'pile': pile}
    return _read(con, work)


def _quantity(value):
    quantity = read_int(value, 'quantity', minimum=1)
    if quantity > MAX_QUANTITY:
        raise InventoryValidationError(f'quantity must be at most {MAX_QUANTITY}')
    return quantity


def record_loss(con, actor, data, *, request_key):
    """Take broken, stolen, missing or otherwise lost stock off one location."""
    if not isinstance(data, dict):
        raise InventoryValidationError('Send the loss as JSON')
    reason = data.get('reason')
    if reason not in REASONS:
        raise InventoryValidationError('Choose what happened: 破损, 被偷, 丢失 or 其他', 'reason_required')
    note = _note(data.get('note'))
    if reason == 'other' and not note:
        raise InventoryValidationError('Say what happened for 其他 Other', 'note_required')
    intent = {'store_code': (data.get('store_code') or '').strip().upper(),
              'product_id': read_int(data.get('product_id'), 'product_id', minimum=1),
              'location_id': read_int(data.get('location_id'), 'location_id', minimum=1),
              'quantity': _quantity(data.get('quantity')), 'reason': reason, 'note': note,
              'expected_version': read_int(data.get('expected_version'), 'expected_version', minimum=0)}
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'stock_loss', actor, intent)
        if prior is not None:
            con.commit()
            return prior
        store, locations = _scope(con, actor, intent['store_code'])
        location = _location_of(locations, intent['location_id'])
        product = _product(con, intent['product_id'])
        available, balance = _available(con, product, location['id'])
        name = _product_name(product)
        if balance['version'] != intent['expected_version']:
            raise InventoryConflict(f"Stock of {name} at {location['name']} changed since you looked "
                                    '(a sale, a count or a move). Check the number and try again.', 'stale_version')
        if intent['quantity'] > available:
            raise InventoryConflict(f"{location['name']} has only {available} of {name} to take off.",
                                    'insufficient_stock')
        today = business_date()
        loss_id = con.execute(
            '''INSERT INTO stock_losses (store_id, location_id, product_id, quantity, unit, reason, note,
                                         unit_price, business_date, actor_sub)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (store['id'], location['id'], product['id'], intent['quantity'], product['stock_unit'], reason, note,
             product['price'] if cents(product['price']) is not None else None, today, actor['sub'])).lastrowid
        line = {'product_id': product['id'], 'unit': product['stock_unit'], 'quantity': intent['quantity'],
                'from_location_id': location['id'], 'from_disposition': 'saleable',
                'expected_versions': {'from': balance['version']}}
        if reason == 'broken':
            # Broken goods are kept aside on the damaged pile here (owner, 2026-11-03).
            line.update(to_location_id=location['id'], to_disposition='damaged')
            line['expected_versions']['to'] = _balance(con, product['id'], location['id'], 'damaged')['version']
        document = _post_inventory_in_transaction(con, {
            'kind': 'move' if reason == 'broken' else 'consume', 'business_date': today,
            'source_type': 'stock_loss', 'source_id': str(loss_id),
            'reason': f'损耗 {REASONS[reason]}' + (f': {note}' if note else ''), 'lines': [line],
        }, actor=actor, request_key=f'loss:{key}', minimum_role='manager')
        con.execute('UPDATE stock_losses SET document_id=? WHERE id=?', (document['document_id'], loss_id))
        result = {'loss': _records(con, 'x.id=?', (loss_id,))[0]}
        _remember(con, key, 'stock_loss', loss_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def undo_loss(con, actor, loss_id, *, request_key):
    """Put a wrongly recorded loss back exactly as it was taken off."""
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'stock_loss_undo', actor, {'loss_id': loss_id})
        if prior is not None:
            con.commit()
            return prior
        loss = con.execute('''SELECT x.*, s.code AS store_code FROM stock_losses x JOIN stores s ON s.id=x.store_id
                              WHERE x.id=?''', (loss_id,)).fetchone()
        if loss is None or loss['document_id'] is None:
            raise InventoryError('This loss record does not exist', 'not_found', 404)
        _scope(con, actor, loss['store_code'])
        if loss['undo_document_id'] is not None:
            raise InventoryConflict('This loss was already undone', 'already_undone')
        product = _product(con, loss['product_id'])
        saleable = _balance(con, product['id'], loss['location_id'], 'saleable')
        line = {'product_id': product['id'], 'unit': loss['unit'], 'quantity': loss['quantity'],
                'to_location_id': loss['location_id'], 'to_disposition': 'saleable',
                'expected_versions': {'to': saleable['version']}}
        if loss['reason'] == 'broken':
            damaged = _balance(con, product['id'], loss['location_id'], 'damaged')
            if damaged['quantity'] < loss['quantity']:
                raise InventoryConflict(
                    f"Only {damaged['quantity']} of {_product_name(product)} are on the damaged pile now (some were "
                    'put back on sale or thrown away), so this record cannot be undone.', 'pile_changed')
            line.update(from_location_id=loss['location_id'], from_disposition='damaged')
            line['expected_versions']['from'] = damaged['version']
        document = _post_inventory_in_transaction(con, {
            'kind': 'correction', 'business_date': business_date(), 'correction_of': loss['document_id'],
            'source_type': 'stock_loss_undo', 'source_id': str(loss_id), 'reason': UNDO_REASON, 'lines': [line],
        }, actor=actor, request_key=f'loss-undo:{key}')
        con.execute("UPDATE stock_losses SET undo_document_id=?, undone_by=?, undone_at=datetime('now') WHERE id=?",
                    (document['document_id'], actor['sub'], loss_id))
        result = {'loss': _records(con, 'x.id=?', (loss_id,))[0]}
        _remember(con, key, 'stock_loss_undo', loss_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def act_on_pile(con, actor, data, *, request_key):
    """Put damaged goods back on sale, or throw them away, at the location they are kept."""
    if not isinstance(data, dict):
        raise InventoryValidationError('Send the action as JSON')
    action = data.get('action')
    if action not in PILE_ACTIONS:
        raise InventoryValidationError('action must be restore or discard')
    intent = {'store_code': (data.get('store_code') or '').strip().upper(), 'action': action,
              'product_id': read_int(data.get('product_id'), 'product_id', minimum=1),
              'location_id': read_int(data.get('location_id'), 'location_id', minimum=1),
              'quantity': _quantity(data.get('quantity')), 'note': _note(data.get('note')),
              'expected_version': read_int(data.get('expected_version'), 'expected_version', minimum=0)}
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'damaged_pile', actor, intent)
        if prior is not None:
            con.commit()
            return prior
        store, locations = _scope(con, actor, intent['store_code'])
        location = _location_of(locations, intent['location_id'])
        product = _product(con, intent['product_id'])
        damaged = _balance(con, product['id'], location['id'], 'damaged')
        name = _product_name(product)
        if damaged['version'] != intent['expected_version']:
            raise InventoryConflict(f"The damaged pile of {name} at {location['name']} changed since you looked. "
                                    'Check the number and try again.', 'stale_version')
        if intent['quantity'] > damaged['quantity']:
            raise InventoryConflict(f"{location['name']} has only {damaged['quantity']} of {name} on the damaged pile.",
                                    'insufficient_stock')
        line = {'product_id': product['id'], 'unit': product['stock_unit'], 'quantity': intent['quantity'],
                'from_location_id': location['id'], 'from_disposition': 'damaged',
                'expected_versions': {'from': damaged['version']}}
        if action == 'restore':
            line.update(to_location_id=location['id'], to_disposition='saleable')
            line['expected_versions']['to'] = _balance(con, product['id'], location['id'], 'saleable')['version']
        document = _post_inventory_in_transaction(con, {
            'kind': 'move' if action == 'restore' else 'consume', 'business_date': business_date(),
            'source_type': 'damaged_pile', 'source_id': key,
            'reason': PILE_ACTIONS[action] + (f": {intent['note']}" if intent['note'] else ''), 'lines': [line],
        }, actor=actor, request_key=f'pile:{key}', minimum_role='manager')
        result = {'document_id': document['document_id'], 'action': action, 'quantity': intent['quantity']}
        _remember(con, key, 'damaged_pile', document['document_id'], actor, digest, result)
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
