"""待盘点 Count next time (owner, 2026-11-22: "mark the eevee as need to be counted next time we count").

A manager or admin with the store's inventory access marks a product (any form, a confirmed design too) at one or
more places of one store, saying why: from a 日报库存核对 report-stock notice or from the product's stock details.
The mark shows first at that store's next count: on 盘点 above the search (and on the product's card), and in a
分区盘点 section count that starts, which takes every open mark of its store into its lines whatever section the
product is in. It closes by itself when a saved count covers that product at that place: a 盘点 save (with or
without a section count) or an approved inventory count. A manager can also take a mark off. Closed marks are kept
with who counted it and when, so a notice can show its mark was counted.
"""
from inventory_commands import InventoryConflict, InventoryError, InventoryValidationError, require_inventory_access
from validation import read_int

MAX_REASON = 200
MAX_PLACES = 8
# What a count card leaves out at a place (stock_counting.BOXES_ONLY): whole sets are counted upstairs.
NO_SETS_AT = {'floor'}
_LABEL = "COALESCE(NULLIF(p.jizhanming,''), NULLIF(p.name_cn_en,''), p.sku)"
_FIELDS = f"""m.id, m.store_id, m.location_id, l.code AS place, l.name AS place_name, m.product_id, p.sku,
              {_LABEL} AS name, p.design_name, p.stock_form, m.reason, m.source_type, m.source_id, m.marked_by,
              m.marked_at, m.status, m.closed_by, m.closed_at"""
_FROM = """FROM count_marks m JOIN inventory_locations l ON l.id=m.location_id JOIN products p ON p.id=m.product_id"""


def _people(con, subjects):
    subjects = sorted({subject for subject in subjects if subject})
    if not subjects:
        return {}
    marks = ','.join('?' * len(subjects))
    return {row[0]: row[1] for row in con.execute(
        f'SELECT auth0_id, name FROM employees WHERE auth0_id IN ({marks})', subjects)}


def _shown(con, rows):
    rows = [dict(row) for row in rows]
    names = _people(con, [row['marked_by'] for row in rows] + [row['closed_by'] for row in rows])
    for row in rows:
        row['marked_by'] = names.get(row['marked_by']) or 'someone'
        row['closed_by'] = (names.get(row['closed_by']) or 'someone') if row['closed_by'] else None
        image = con.execute("""SELECT filename FROM hidden_images WHERE product_id=?
                               ORDER BY CASE WHEN image_type='general' THEN 0 ELSE 1 END, id LIMIT 1""",
                            (row['product_id'],)).fetchone()
        row['image'] = image[0] if image else None
    return rows


def open_marks(con, store_id, location_id=None, product_id=None):
    """Open marks at a store (one place, one product), oldest first, as shown."""
    where, params = ['m.store_id=?', "m.status='open'"], [store_id]
    if location_id is not None:
        where.append('m.location_id=?')
        params.append(location_id)
    if product_id is not None:
        where.append('m.product_id=?')
        params.append(product_id)
    return _shown(con, con.execute(f"SELECT {_FIELDS} {_FROM} WHERE {' AND '.join(where)} ORDER BY m.marked_at, m.id",
                                   params))


def _store(con, store_code):
    store = con.execute('SELECT id, code FROM stores WHERE code=? AND is_active=1',
                        ((store_code or '').strip().upper(),)).fetchone()
    if store is None:
        raise InventoryValidationError('Choose one store')
    return store


def _require_manager(con, actor, store):
    try:
        require_inventory_access(con, actor, [store['id']], 'manager')
    except PermissionError:
        raise PermissionError(f"Only a manager or admin with {store['code']} inventory access can mark products "
                              'to count next time') from None


def list_marks(con, actor, store_code, product_id=None):
    """The store's open marks, for whoever may count there (盘点 lists them above the search)."""
    from stock_counting import _read, _scope  # stock_counting closes marks when it saves

    def work():
        scope = _scope(con, actor, store_code)
        product = None if product_id in (None, '') else read_int(product_id, 'product_id', minimum=1)
        return {'store': scope['store'], 'marks': open_marks(con, scope['store']['id'], product_id=product)}
    return _read(con, work)


def _read_reason(data):
    reason = ' '.join(str(data.get('reason') or '').split())
    if not reason:
        raise InventoryValidationError('Say why it needs counting', 'reason_required')
    if len(reason) > MAX_REASON:
        raise InventoryValidationError(f'Keep the reason under {MAX_REASON} characters')
    return reason


def mark(con, actor, data):
    """Mark one product at the places named. A place already marked keeps its first mark."""
    if not isinstance(data, dict):
        raise InventoryValidationError('Send the product, the places and why as JSON')
    product_id = read_int(data.get('product_id'), 'product_id', minimum=1)
    places = data.get('locations')
    if (not isinstance(places, list) or not places or len(places) > MAX_PLACES
            or any(not isinstance(place, str) for place in places)):
        raise InventoryValidationError('Pick where to count it', 'locations_required')
    places = list(dict.fromkeys(place.strip().lower() for place in places))
    reason = _read_reason(data)
    notice_id = data.get('notice_id')
    notice_id = None if notice_id is None else read_int(notice_id, 'notice_id', minimum=1)
    con.execute('BEGIN IMMEDIATE')
    try:
        store = _store(con, data.get('store_code'))
        _require_manager(con, actor, store)
        product = con.execute('SELECT id, stock_form FROM products WHERE id=?', (product_id,)).fetchone()
        if product is None:
            raise InventoryError('Product not found', 'not_found', 404)
        locations = {row['code']: row['id'] for row in con.execute(
            'SELECT id, code FROM inventory_locations WHERE store_id=? AND is_active=1', (store['id'],))}
        unknown = [place for place in places if place not in locations]
        if unknown:
            raise InventoryValidationError(f"{store['code']} has no place {', '.join(unknown)}", 'location_unknown')
        if product['stock_form'] == 'sealed_set' and NO_SETS_AT & set(places):
            raise InventoryValidationError('整端 Whole sets are counted upstairs, not on the floor. Mark the box on '
                                           'the floor instead.', 'whole_sets_upstairs')
        if notice_id is not None:
            notice = con.execute('SELECT store_id, product_id FROM report_stock_issues WHERE id=?',
                                 (notice_id,)).fetchone()
            if notice is None or notice['store_id'] != store['id'] or notice['product_id'] != product_id:
                raise InventoryValidationError('That notice is for another product or store', 'notice_mismatch')
        added, already = [], []
        for place in places:
            if con.execute("SELECT 1 FROM count_marks WHERE location_id=? AND product_id=? AND status='open'",
                           (locations[place], product_id)).fetchone():
                already.append(place)
                continue
            con.execute(
                """INSERT INTO count_marks(store_id, location_id, product_id, reason, source_type, source_id, marked_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (store['id'], locations[place], product_id, reason,
                 'report_stock_issue' if notice_id else None, notice_id, actor['sub']))
            added.append(place)
        con.commit()
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
    return {'added': added, 'already': already, 'marks': open_marks(con, store['id'], product_id=product_id)}


def remove(con, actor, mark_id):
    """A manager takes a mark off (marked by mistake): kept as removed."""
    mark_id = read_int(mark_id, 'mark_id', minimum=1)
    con.execute('BEGIN IMMEDIATE')
    try:
        row = con.execute('SELECT m.*, s.code FROM count_marks m JOIN stores s ON s.id=m.store_id WHERE m.id=?',
                          (mark_id,)).fetchone()
        if row is None:
            raise InventoryError('Mark not found', 'not_found', 404)
        _require_manager(con, actor, {'id': row['store_id'], 'code': row['code']})
        if row['status'] != 'open':
            raise InventoryConflict('This mark is already counted or taken off', 'mark_closed')
        con.execute("""UPDATE count_marks SET status='removed', closed_by=?, closed_at=datetime('now')
                       WHERE id=?""", (actor['sub'], mark_id))
        con.commit()
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
    return {'id': mark_id, 'status': 'removed', 'marks': open_marks(con, row['store_id'], product_id=row['product_id'])}


def close_counted(con, actor, location_id, product_ids, count_id=None):
    """Inside a count's write transaction: what it counted at this place is no longer to count."""
    product_ids = sorted(set(product_ids))
    if not product_ids:
        return 0
    marks = ','.join('?' * len(product_ids))
    return con.execute(
        f"""UPDATE count_marks SET status='counted', closed_by=?, closed_at=datetime('now'), count_id=?
            WHERE location_id=? AND status='open' AND product_id IN ({marks})""",
        (actor.get('sub'), count_id, location_id, *product_ids)).rowcount


def notice_marks(con, notices):
    """For report-stock notices: each one's open marks of its product at its store, and the marks made from it
    that a count has closed since (so the manager knows it was counted and can check the notice)."""
    if not notices:
        return {}
    pairs = {(notice['store_id'], notice['product_id']) for notice in notices}
    found = {}
    for store_id, product_id in pairs:
        found[(store_id, product_id)] = open_marks(con, store_id, product_id=product_id)
    ids = [notice['id'] for notice in notices]
    counted = {}
    for row in _shown(con, con.execute(
            f"""SELECT {_FIELDS} {_FROM} WHERE m.source_type='report_stock_issue' AND m.status='counted'
                AND m.source_id IN ({','.join('?' * len(ids))}) ORDER BY m.closed_at, m.id""", ids)):
        counted.setdefault(row['source_id'], []).append(row)
    return {notice['id']: {'marks': found[(notice['store_id'], notice['product_id'])],
                           'counted_marks': counted.get(notice['id'], [])} for notice in notices}
