"""
blueprints/restock.py — restock session workflow (pending → submitted → picking → completed).
"""
import json
from flask import Blueprint, request, jsonify

from db import get_db, _ensure_stock_row
from auth import login_required, role_required
from checkout_access import business_date
from store_access import legacy_store_denied
from validation import SQLITE_INTEGER_MAX, invalid_input, read_int
from blueprints.stores import _resolve_store
from blueprints.stock import _inventory_error, _inventory_location, _is_authoritative
from inventory_commands import (
    InventoryConflict, InventoryError, InventoryValidationError,
    post_inventory, require_inventory_access,
)
from goods_operations import act_on_delivery, create_delivery, delivery_detail
from inventory_visibility import can_see_inventory, hide_balances, hide_quantities
from stock_counting import _image, family_anchors, family_members

bp = Blueprint('restock', __name__)

# One request per product on a sheet; the picker counts at most this many of one product.
MAX_REQUEST = 999
ROLE_ORDER = {'set': 0, 'box': 1, 'item': 1, 'design': 2}


def _restock_access_denied(con, store_id, minimum_role):
    if not _is_authoritative(con, store_id):
        # Legacy sheets stay readable to every signed-in user; changes need that store.
        if minimum_role == 'viewer':
            return None
        return legacy_store_denied(con, request.jwt_payload, store_id)
    try:
        require_inventory_access(
            con, request.jwt_payload, (store_id,), minimum_role
        )
    except PermissionError:
        return jsonify({'error': 'Inventory access denied',
                        'code': 'inventory_forbidden'}), 403
    return None


def _restock_session_items(cur, sid, store_id):
    """Return items for a session, joined with product and live stock info."""
    cur.execute('''
        SELECT ri.id, ri.product_id, ri.requested_qty, ri.warehouse_stock_snapshot,
               ri.found_qty, ri.pick_status,
               p.sku, p.jizhanming, p.name_cn_en, p.ip_series, p.product_type,
               p.boxes_per_dan, p.stock_unit,
               COALESCE(s.upstairs_qty, 0) AS upstairs_qty,
               COALESCE(s.instore_qty,  0) AS instore_qty
        FROM restock_items ri
        JOIN products p ON p.id = ri.product_id
        LEFT JOIN stock s ON s.product_id = ri.product_id AND s.store_id = ?
        WHERE ri.session_id = ?
        ORDER BY ri.id
    ''', (store_id, sid))
    items = [dict(r) for r in cur.fetchall()]
    # Each line names its series card (整端 / 散盒 / 确认款), so the sheet groups them as they were asked for.
    families = {}
    for item in items:
        family = families.get(item['product_id'])
        if family is None:
            family = _family_of(cur.connection, item['product_id'])
            for product_id in family['roles']:
                families[product_id] = family
        item.update(family_id=family['id'], family_title=family['title'],
                    role=family['roles'].get(item['product_id'], 'item'),
                    design_name=family['design_names'].get(item['product_id']),
                    boxes_per_set=family['boxes_per_set'].get(item['product_id']))
    if not can_see_inventory(cur.connection, request.jwt_payload, (store_id,)):
        # Without 库存查看, 补货 says only whether upstairs is out of stock (owner, 2026-11-01).
        items = [hide_quantities({**item, 'instore_qty': None, 'warehouse_stock_snapshot': None},
                                 'upstairs_qty') for item in items]
    return items


def _family_of(con, product_id):
    """The series card a product is restocked from, as on 盘点: a blind box with its sealed sets
    and confirmed designs, a series of designs alone, or a single product."""
    head, series_id, members = family_members(con, product_id)
    if head is not None:
        title = head['label']
    else:
        title = con.execute('SELECT name FROM product_series WHERE id=?', (series_id,)).fetchone()['name']
    return {
        'id': head['id'] if head is not None else members[0][1]['id'],
        'title': title, 'head': head, 'members': members,
        'roles': {product['id']: role for role, product, _ in members},
        'design_names': {product['id']: product['design_name'] for role, product, _ in members
                         if role == 'design'},
        'boxes_per_set': {product['id']: boxes for _, product, boxes in members if boxes},
    }


def _family_stock(con, store_id, product_ids):
    marks = ','.join('?' * len(product_ids))
    return {row['product_id']: row for row in con.execute(
        f"""SELECT product_id, COALESCE(upstairs_qty, 0) AS upstairs_qty,
                   COALESCE(instore_qty, 0) AS instore_qty
            FROM stock WHERE store_id=? AND product_id IN ({marks})""", (store_id, *product_ids))}


def _editable_session(con, sid):
    """(session, None) for a sheet the caller may change, else (None, error response)."""
    session = con.execute('SELECT id, status, store_id FROM restock_sessions WHERE id=?', (sid,)).fetchone()
    if session is None:
        return None, (jsonify({'error': 'Session not found'}), 404)
    denied = _restock_access_denied(con, session['store_id'], 'staff')
    if denied is not None:
        return None, denied
    if session['status'] != 'pending':
        return None, (jsonify({'error': '只能在 pending 状态下修改清单'}), 403)
    return session, None


@bp.get('/api/restock/session/<int:sid>/series-search')
@role_required('staff')
def search_restock_series(sid):
    """Search finds the series, not its parts: one result per 整端 / 散盒 / 确认款 card."""
    text = (request.args.get('q') or '').strip()
    con = get_db()
    try:
        session, denied = _editable_session(con, sid)
        if denied is not None:
            return denied
        if not text:
            return jsonify({'results': []})
        visible = can_see_inventory(con, request.jwt_payload, (session['store_id'],))
        on_sheet = {row[0] for row in con.execute(
            'SELECT product_id FROM restock_items WHERE session_id=?', (sid,))}
        results = []
        for anchor in family_anchors(con, text):
            family = _family_of(con, anchor)
            stock = _family_stock(con, session['store_id'], list(family['roles']))
            back = {}
            for _, product, _ in family['members']:
                unit = product['stock_unit'] or 'piece'
                row = stock.get(product['id'])
                back[unit] = back.get(unit, 0) + (row['upstairs_qty'] if row else 0)
            head = family['head']
            result = {
                'product_id': family['id'], 'title': family['title'],
                'sku': head['sku'] if head is not None else None,
                'ip_series': head['ip_series'] if head is not None else None,
                'kind': family['members'][0][0] if head is not None else 'series',
                'sets': sum(1 for role, *_ in family['members'] if role == 'set'),
                'designs': sum(1 for role, *_ in family['members'] if role == 'design'),
                'image': _image(con, head['id']) if head is not None else None,
                'on_sheet': sum(1 for product_id in family['roles'] if product_id in on_sheet),
                'back': back,
            }
            if not visible:
                result['back'] = None
                result['out_of_stock'] = sum(back.values()) <= 0
                result['stock_hidden'] = True
            results.append(result)
        return jsonify({'results': results})
    finally:
        con.close()


@bp.get('/api/restock/session/<int:sid>/series')
@role_required('staff')
def restock_series_card(sid):
    """One series to ask for: how many 整端 whole sets, 散盒 single boxes and each 确认款 design."""
    con = get_db()
    try:
        product_id = read_int(request.args.get('product_id'), 'product_id', minimum=1)
        session, denied = _editable_session(con, sid)
        if denied is not None:
            return denied
        family = _family_of(con, product_id)
        ids = list(family['roles'])
        stock = _family_stock(con, session['store_id'], ids)
        marks = ','.join('?' * len(ids))
        requested = {row[0]: row[1] for row in con.execute(
            f'SELECT product_id, requested_qty FROM restock_items WHERE session_id=? AND product_id IN ({marks})',
            (sid, *ids))}
        visible = can_see_inventory(con, request.jwt_payload, (session['store_id'],))
        items = []
        # Whole sets first, then single boxes, then the designs in their own order (sorted is stable).
        for role, product, boxes in sorted(family['members'], key=lambda member: ROLE_ORDER[member[0]]):
            row = stock.get(product['id'])
            item = {
                'product_id': product['id'], 'role': role, 'sku': product['sku'], 'name': product['label'],
                'design_name': product['design_name'], 'stock_unit': product['stock_unit'],
                'boxes_per_set': boxes, 'requested_qty': requested.get(product['id'], 0),
                'upstairs_qty': row['upstairs_qty'] if row else 0,
                'instore_qty': row['instore_qty'] if row else 0,
            }
            if not visible:
                hide_quantities(item, 'upstairs_qty')
                item['instore_qty'] = None
            items.append(item)
        head = family['head']
        roles = family['roles'].values()
        return jsonify({
            'product_id': family['id'], 'title': family['title'],
            'sku': head['sku'] if head is not None else None,
            'ip_series': head['ip_series'] if head is not None else None,
            'image': _image(con, head['id']) if head is not None else None,
            # A blind box with no sealed set has no whole sets recorded upstairs (盘点 adds the set).
            'no_whole_set': 'box' in roles and 'set' not in roles,
            'items': items,
        })
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400
    except InventoryError as exc:
        return jsonify({'error': str(exc), 'code': exc.code}), exc.status
    finally:
        con.close()


@bp.put('/api/restock/session/<int:sid>/series')
@role_required('staff')
def save_restock_series(sid):
    """Set what one series asks for in one save: each part's quantity, 0 takes it off the sheet."""
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    try:
        product_id = read_int(data.get('product_id'), 'product_id', minimum=1)
        entries = data.get('entries')
        if not isinstance(entries, list) or not entries:
            raise ValueError('entries must list at least one product')
        wanted = {}
        for entry in entries:
            entry = entry if isinstance(entry, dict) else {}
            member = read_int(entry.get('product_id'), 'product_id', minimum=1)
            quantity = read_int(entry.get('requested_qty'), 'requested_qty')
            if quantity > MAX_REQUEST:
                raise ValueError(f'requested_qty must be at most {MAX_REQUEST}')
            if member in wanted:
                raise ValueError('product_id is listed twice')
            wanted[member] = quantity
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400

    con = get_db()
    con.execute('BEGIN IMMEDIATE')
    try:
        session, denied = _editable_session(con, sid)
        if denied is not None:
            return denied
        try:
            family = _family_of(con, product_id)
        except InventoryError as exc:
            return jsonify({'error': str(exc), 'code': exc.code}), exc.status
        if set(wanted) - set(family['roles']):
            return jsonify({'error': 'A product is not part of this series', 'code': 'not_in_series'}), 400
        for member, quantity in wanted.items():
            if quantity:
                con.execute('''
                    INSERT INTO restock_items (session_id, product_id, requested_qty)
                    VALUES (?, ?, ?)
                    ON CONFLICT(session_id, product_id) DO UPDATE SET
                        requested_qty = excluded.requested_qty
                ''', (sid, member, quantity))
            else:
                con.execute('DELETE FROM restock_items WHERE session_id=? AND product_id=?', (sid, member))
        con.commit()
        return jsonify({'ok': True})
    finally:
        if con.in_transaction:
            con.rollback()
        con.close()


@bp.route('/api/restock/sessions/today')
@login_required
def restock_sessions_today():
    today = business_date()
    con = get_db()
    store_code = (request.args.get('store_code') or '').strip().upper()
    if not store_code:
        con.close()
        return jsonify({'error': 'store_code is required'}), 400
    resolved = _resolve_store(con, store_code)
    if resolved is None:
        con.close()
        return jsonify({'error': 'Invalid store code'}), 400
    store_id, store_code = resolved
    denied = _restock_access_denied(con, store_id, 'viewer')
    if denied is not None:
        con.close()
        return denied
    cur = con.cursor()
    cur.execute('''
        SELECT rs.id, rs.date, rs.status, rs.created_at, rs.submitted_at, rs.completed_at,
               rs.store_id,
               COUNT(ri.id)                                                          AS item_count,
               COALESCE(SUM(ri.requested_qty), 0)                                   AS total_requested,
               COALESCE(SUM(CASE WHEN ri.pick_status='found' THEN ri.found_qty ELSE 0 END), 0)
                                                                                    AS total_found
        FROM restock_sessions rs
        LEFT JOIN restock_items ri ON ri.session_id = rs.id
        WHERE rs.date = ? AND rs.store_id = ?
        GROUP BY rs.id
        ORDER BY rs.created_at DESC
    ''', (today, store_id))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@bp.route('/api/restock/sessions', methods=['POST'])
@role_required('staff')
def create_restock_session():
    today = business_date()
    data  = request.get_json(silent=True) or {}
    con   = get_db()
    store_code = (data.get('store_code') or '').strip().upper()
    if not store_code:
        con.close()
        return jsonify({'error': 'store_code is required'}), 400
    resolved = _resolve_store(con, store_code)
    if resolved is None:
        con.close()
        return jsonify({'error': 'Invalid store code'}), 400
    store_id, store_code = resolved
    denied = _restock_access_denied(con, store_id, 'staff')
    if denied is not None:
        con.close()
        return denied
    cur = con.cursor()
    cur.execute(
        "INSERT INTO restock_sessions (date, status, store_id) VALUES (?, 'pending', ?)",
        (today, store_id),
    )
    con.commit()
    sid = cur.lastrowid
    cur.execute('SELECT * FROM restock_sessions WHERE id = ?', (sid,))
    row = dict(cur.fetchone())
    con.close()
    return jsonify(row), 201


@bp.route('/api/restock/session/<int:sid>', methods=['DELETE'])
@role_required('staff')
def delete_restock_session(sid):
    """
    Cancel / delete a restock session.
    - pending / submitted / picking: delete items then session (no stock changes).
    - completed: retain posted stock history and require a later correction.
    """
    con = get_db()
    con.execute('BEGIN IMMEDIATE')
    cur = con.cursor()
    cur.execute('SELECT status, store_id FROM restock_sessions WHERE id = ?', (sid,))
    sess = cur.fetchone()
    if not sess:
        con.rollback()
        con.close()
        return jsonify({'error': 'Session not found'}), 404

    denied = _restock_access_denied(con, sess['store_id'], 'staff')
    if denied is not None:
        con.rollback()
        con.close()
        return denied

    status = sess['status']

    if cur.execute(
        'SELECT 1 FROM inventory_deliveries WHERE restock_session_id=?', (sid,)
    ).fetchone():
        con.rollback()
        con.close()
        return jsonify({
            'error': 'A started goods delivery must be received, returned, or resolved',
            'code': 'delivery_already_started',
        }), 409

    if status == 'completed':
        con.rollback()
        con.close()
        return jsonify({
            'error': 'Completed restocks are retained; record a correction instead',
            'code': 'completed_restock_retained',
        }), 409

    cur.execute('DELETE FROM restock_items WHERE session_id = ?', (sid,))
    cur.execute('DELETE FROM restock_sessions WHERE id = ?', (sid,))
    con.commit()
    con.close()
    return jsonify({'ok': True, 'reversed_count': 0})


@bp.route('/api/restock/session/today')
@login_required
def restock_session_today():
    """Legacy: return the most recent pending session today for a given store, or 404."""
    today = business_date()
    con   = get_db()
    store_code = (request.args.get('store_code') or '').strip().upper()
    if not store_code:
        con.close()
        return jsonify({'error': 'store_code is required'}), 400
    resolved = _resolve_store(con, store_code)
    if resolved is None:
        con.close()
        return jsonify({'error': 'Invalid store code'}), 400
    store_id, store_code = resolved
    denied = _restock_access_denied(con, store_id, 'viewer')
    if denied is not None:
        con.close()
        return denied
    cur = con.cursor()
    cur.execute(
        "SELECT * FROM restock_sessions"
        " WHERE date = ? AND status = 'pending' AND store_id = ?"
        " ORDER BY created_at DESC LIMIT 1",
        (today, store_id),
    )
    row = cur.fetchone()
    con.close()
    if not row:
        return jsonify({'error': 'No pending session today'}), 404
    return jsonify(dict(row))


@bp.route('/api/restock/session/<int:sid>')
@login_required
def get_restock_session(sid):
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT * FROM restock_sessions WHERE id = ?', (sid,))
    row = cur.fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'Session not found'}), 404
    session  = dict(row)
    store_id = session['store_id']
    denied = _restock_access_denied(con, store_id, 'viewer')
    if denied is not None:
        con.close()
        return denied
    session['items'] = _restock_session_items(cur, sid, store_id)
    delivery = cur.execute(
        'SELECT id FROM inventory_deliveries WHERE restock_session_id=?', (sid,)
    ).fetchone()
    if delivery:
        try:
            session['delivery'] = delivery_detail(
                con, delivery['id'], actor=request.jwt_payload
            )
        except PermissionError:
            con.close()
            return jsonify({'error': 'Inventory access denied'}), 403
    con.close()
    return jsonify(session)


def _restock_delivery(con, sid):
    existing = con.execute(
        'SELECT id FROM inventory_deliveries WHERE restock_session_id=?', (sid,)
    ).fetchone()
    if existing:
        return existing['id']
    session = con.execute(
        'SELECT id, date, status, store_id FROM restock_sessions WHERE id=?',
        (sid,),
    ).fetchone()
    if session is None or session['status'] not in {'submitted', 'picking'}:
        raise InventoryConflict('Restock session is not ready',
                                'restock_state_conflict')
    floor = _inventory_location(con, session['store_id'], 'floor')
    back = _inventory_location(con, session['store_id'], 'upstairs', 'warehouse')
    if floor is None or back is None:
        raise InventoryConflict('Reviewed inventory locations are missing',
                                'inventory_store_missing')
    lines = [dict(row) for row in con.execute(
        """SELECT ri.product_id, p.stock_unit AS unit,
                  ri.requested_qty AS requested_quantity
           FROM restock_items ri JOIN products p ON p.id=ri.product_id
           WHERE ri.session_id=? ORDER BY ri.id""", (sid,)
    )]
    if not lines:
        raise InventoryValidationError('Restock session has no items')
    # Left uncommitted: the first action commits it, or a refusal rolls it back.
    created = create_delivery(con, {
        'source_location_id': back, 'destination_location_id': floor,
        'business_date': session['date'], 'restock_session_id': sid,
        'lines': lines,
    }, actor=request.jwt_payload, request_key=f'restock-delivery:{sid}',
        kind='restock', commit=False)
    return created['id']


@bp.post('/api/restock/session/<int:sid>/pick', defaults={'action': 'dispatch'})
@bp.post('/api/restock/session/<int:sid>/receive', defaults={'action': 'receive'})
@bp.post('/api/restock/session/<int:sid>/return', defaults={'action': 'return'})
@bp.post('/api/restock/session/<int:sid>/resolve-loss', defaults={'action': 'resolve_loss'})
@bp.post('/api/restock/session/<int:sid>/short-close', defaults={'action': 'short_close'})
@role_required('staff')
def restock_goods_action(sid, action):
    con = get_db()
    try:
        session = con.execute(
            'SELECT store_id FROM restock_sessions WHERE id=?', (sid,)
        ).fetchone()
        if session:
            denied = _restock_access_denied(con, session['store_id'], 'staff')
            if denied is not None:
                con.close()
                return denied
        delivery_id = _restock_delivery(con, sid)
        try:
            result = act_on_delivery(
                con, delivery_id, action, request.get_json(silent=True) or {},
                actor=request.jwt_payload,
                request_key=request.headers.get('Idempotency-Key'),
            )
        finally:
            # A delivery created for a refused first action is never kept.
            if con.in_transaction:
                con.rollback()
        return jsonify(result)
    except PermissionError:
        return jsonify({'error': 'Inventory access denied',
                        'code': 'inventory_forbidden'}), 403
    except InventoryError as exc:
        return jsonify({'error': str(exc), 'code': exc.code}), exc.status


@bp.route('/api/restock/items', methods=['POST'])
@role_required('staff')
def add_restock_item():
    data = request.get_json() or {}
    try:
        sid = read_int(data.get('session_id'), 'session_id', minimum=1)
        pid = read_int(data.get('product_id'), 'product_id', minimum=1)
        qty = read_int(data.get('requested_qty'), 'requested_qty', minimum=1)
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400

    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT status, store_id FROM restock_sessions WHERE id = ?', (sid,))
    sess = cur.fetchone()
    if not sess:
        con.close()
        return jsonify({'error': 'Session not found'}), 404
    denied = _restock_access_denied(con, sess['store_id'], 'staff')
    if denied is not None:
        con.close()
        return denied
    if sess['status'] != 'pending':
        con.close()
        return jsonify({'error': '只能在 pending 状态下修改清单'}), 403

    if cur.execute('SELECT 1 FROM products WHERE id = ?', (pid,)).fetchone() is None:
        con.close()
        return jsonify(error='Product not found', code='not_found'), 404

    cur.execute('''
        INSERT INTO restock_items (session_id, product_id, requested_qty)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id, product_id) DO UPDATE SET
            requested_qty = excluded.requested_qty
    ''', (sid, pid, qty))
    con.commit()
    item_id = cur.lastrowid
    con.close()
    return jsonify({'ok': True, 'id': item_id}), 201


@bp.route('/api/restock/items/<int:iid>', methods=['DELETE'])
@role_required('staff')
def delete_restock_item(iid):
    con = get_db()
    con.execute('BEGIN IMMEDIATE')
    cur = con.cursor()
    cur.execute('''
        SELECT rs.status, rs.store_id FROM restock_items ri
        JOIN restock_sessions rs ON rs.id = ri.session_id
        WHERE ri.id = ?
    ''', (iid,))
    row = cur.fetchone()
    if not row:
        con.rollback()
        con.close()
        return jsonify({'error': 'Item not found'}), 404
    denied = _restock_access_denied(con, row['store_id'], 'staff')
    if denied is not None:
        con.rollback()
        con.close()
        return denied
    if row['status'] != 'pending':
        con.rollback()
        con.close()
        return jsonify({'error': '只能在 pending 状态下删除条目'}), 400
    cur.execute('''
        DELETE FROM restock_items
        WHERE id = ? AND session_id IN (
            SELECT id FROM restock_sessions WHERE status = 'pending'
        )
    ''', (iid,))
    if cur.rowcount != 1:
        con.rollback()
        con.close()
        return jsonify({'error': '补货状态已改变，无法删除条目'}), 409
    con.commit()
    con.close()
    return jsonify({'ok': True})


@bp.route('/api/restock/session/<int:sid>/submit', methods=['POST'])
@role_required('staff')
def submit_restock_session(sid):
    """pending → submitted. Snapshots current warehouse stock into each item."""
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT status, store_id FROM restock_sessions WHERE id = ?', (sid,))
    sess = cur.fetchone()
    if not sess:
        con.close()
        return jsonify({'error': 'Session not found'}), 404
    denied = _restock_access_denied(con, sess['store_id'], 'staff')
    if denied is not None:
        con.close()
        return denied
    if sess['status'] != 'pending':
        con.close()
        return jsonify({'error': f'当前状态 {sess["status"]} 不可提交'}), 400
    store_id = sess['store_id']

    cur.execute('''
        UPDATE restock_items
        SET warehouse_stock_snapshot = COALESCE(
            (SELECT s.upstairs_qty FROM stock s
             WHERE s.product_id = restock_items.product_id AND s.store_id = ?),
            0
        )
        WHERE session_id = ?
    ''', (store_id, sid))
    cur.execute(
        "UPDATE restock_sessions SET status='submitted', submitted_at=datetime('now') WHERE id=?",
        (sid,),
    )
    con.commit()
    con.close()
    return jsonify({'ok': True})


@bp.route('/api/restock/session/<int:sid>/picking-list')
@login_required
def restock_picking_list(sid):
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT status, store_id FROM restock_sessions WHERE id = ?', (sid,))
    sess = cur.fetchone()
    if not sess:
        con.close()
        return jsonify({'error': 'Session not found'}), 404
    store_id = sess['store_id']
    denied = _restock_access_denied(con, store_id, 'viewer')
    if denied is not None:
        con.close()
        return denied
    items = _restock_session_items(cur, sid, store_id)
    con.close()
    return jsonify({'session_status': sess['status'], 'items': items})


@bp.route('/api/restock/items/<int:iid>/pick', methods=['PATCH'])
@role_required('staff')
def pick_restock_item(iid):
    data = request.get_json() or {}
    pick_status = data.get('pick_status', '')
    if pick_status not in ('found', 'not_found'):
        return jsonify({'error': 'pick_status 必须为 found 或 not_found'}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute('''
        SELECT ri.*, rs.status AS session_status, rs.id AS session_id,
               rs.store_id AS store_id
        FROM restock_items ri
        JOIN restock_sessions rs ON rs.id = ri.session_id
        WHERE ri.id = ?
    ''', (iid,))
    item = cur.fetchone()
    if not item:
        con.close()
        return jsonify({'error': 'Item not found'}), 404
    denied = _restock_access_denied(con, item['store_id'], 'staff')
    if denied is not None:
        con.close()
        return denied
    if item['session_status'] not in ('submitted', 'picking'):
        con.close()
        return jsonify({'error': '只能在 submitted/picking 状态下更新拣货结果'}), 400
    if cur.execute(
        'SELECT 1 FROM inventory_deliveries WHERE restock_session_id=?',
        (item['session_id'],),
    ).fetchone():
        con.close()
        return jsonify({
            'error': 'Picked quantities are locked after physical dispatch',
            'code': 'delivery_already_started',
        }), 409

    if pick_status == 'not_found':
        found_qty = 0
    else:
        found_qty = data.get('found_qty')
        if found_qty is None:
            con.close()
            return jsonify({'error': 'found 时须提供 found_qty'}), 400
        try:
            found_qty = read_int(found_qty, 'found_qty', minimum=1)
        except ValueError as exc:
            con.close()
            return jsonify(invalid_input(exc)), 400
        if found_qty > item['requested_qty']:
            con.close()
            return jsonify({'error': f'found_qty ({found_qty}) 不可超过 requested_qty ({item["requested_qty"]})'}), 400

    cur.execute(
        'UPDATE restock_items SET found_qty=?, pick_status=? WHERE id=?',
        (found_qty, pick_status, iid),
    )
    if item['session_status'] == 'submitted':
        cur.execute("UPDATE restock_sessions SET status='picking' WHERE id=?", (item['session_id'],))
    con.commit()
    con.close()
    return jsonify({'ok': True, 'found_qty': found_qty, 'pick_status': pick_status})


@bp.route('/api/restock/session/<int:sid>/complete', methods=['POST'])
@role_required('staff')
def complete_restock_session(sid):
    """
    picking/submitted → completed.
    Validates all items picked, then syncs stock in a single transaction.
    A shortage leaves the complete session unchanged.
    """
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT status, store_id FROM restock_sessions WHERE id = ?', (sid,))
    sess = cur.fetchone()
    if not sess:
        con.close()
        return jsonify({'error': 'Session not found'}), 404
    denied = _restock_access_denied(con, sess['store_id'], 'staff')
    if denied is not None:
        con.close()
        return denied
    if _is_authoritative(con, sess['store_id']):
        store_id = sess['store_id']
        delivery = con.execute(
            'SELECT status FROM inventory_deliveries WHERE restock_session_id=?',
            (sid,),
        ).fetchone()
        if delivery and delivery['status'] != 'completed':
            con.close()
            return jsonify({
                'error': 'Receive or resolve picked goods before completion',
                'code': 'delivery_receipt_required',
            }), 409
        if sess['status'] == 'completed':
            prior = con.execute(
                """SELECT stored_result FROM inventory_documents
                   WHERE source_type='restock_session' AND source_id=?
                     AND status='posted'""", (str(sid),)
            ).fetchone()
            try:
                require_inventory_access(
                    con, request.jwt_payload, (store_id,), 'staff'
                )
            except PermissionError:
                con.close()
                return jsonify({'error': 'Inventory access denied',
                                'code': 'inventory_forbidden'}), 403
            visible = can_see_inventory(con, request.jwt_payload, (store_id,))
            con.close()
            if prior and prior['stored_result']:
                result = json.loads(prior['stored_result'])
                return jsonify(result if visible else hide_balances(result))
            return jsonify({'error': 'Completed restock has no inventory document',
                            'code': 'reconciliation_required'}), 409
        if sess['status'] not in ('submitted', 'picking'):
            con.close()
            return jsonify({'error': f'当前状态 {sess["status"]} 不可完成'}), 400
        pending = con.execute(
            """SELECT 1 FROM restock_items
               WHERE session_id=? AND pick_status='pending' LIMIT 1""", (sid,)
        ).fetchone()
        if pending:
            con.close()
            return jsonify({'error': '还有未确认拣货', 'code': 'restock_pending'}), 409
        floor = _inventory_location(con, store_id, 'floor')
        back = _inventory_location(con, store_id, 'upstairs', 'warehouse')
        if floor is None or back is None:
            con.close()
            return jsonify({'error': 'Reviewed inventory locations are missing',
                            'code': 'inventory_store_missing'}), 409
        found_items = con.execute(
            """SELECT ri.product_id, SUM(ri.found_qty) AS found_qty,
                      p.stock_unit
               FROM restock_items ri JOIN products p ON p.id=ri.product_id
               WHERE ri.session_id=? AND ri.pick_status='found' AND ri.found_qty>0
               GROUP BY ri.product_id, p.stock_unit""", (sid,)
        ).fetchall()
        if not found_items:
            try:
                require_inventory_access(
                    con, request.jwt_payload, (store_id,), 'staff'
                )
            except PermissionError:
                con.close()
                return jsonify({'error': 'Inventory access denied',
                                'code': 'inventory_forbidden'}), 403
            con.execute('BEGIN IMMEDIATE')
            updated = con.execute(
                """UPDATE restock_sessions
                   SET status='completed', completed_at=datetime('now')
                   WHERE id=? AND status IN ('submitted','picking')""", (sid,)
            )
            if updated.rowcount != 1:
                con.rollback()
                con.close()
                return jsonify({'error': 'Restock session changed before completion',
                                'code': 'restock_state_conflict'}), 409
            con.commit()
            con.close()
            return jsonify({'ok': True, 'synced': 0, 'items': []})
        lines = []
        for item in found_items:
            source = con.execute(
                """SELECT version FROM inventory_balances
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (item['product_id'], back),
            ).fetchone()
            target = con.execute(
                """SELECT version FROM inventory_balances
                   WHERE product_id=? AND location_id=? AND disposition='saleable'""",
                (item['product_id'], floor),
            ).fetchone()
            lines.append({
                'product_id': item['product_id'], 'quantity': item['found_qty'],
                'unit': item['stock_unit'], 'from_location_id': back,
                'from_disposition': 'saleable', 'to_location_id': floor,
                'to_disposition': 'saleable', 'expected_versions': {
                    'from': source['version'] if source else 0,
                    'to': target['version'] if target else 0,
                },
            })
        try:
            result = post_inventory(
                con, {'kind': 'restock_complete',
                      'business_date': business_date(),
                      'source_type': 'restock_session', 'source_id': str(sid),
                      'reason': f'Restock session {sid}', 'lines': lines},
                actor=request.jwt_payload, request_key=f'restock-session-{sid}',
            )
            if not can_see_inventory(con, request.jwt_payload, (store_id,)):
                result = hide_balances(result)
            con.close()
            return jsonify(result)
        except PermissionError:
            con.close()
            return jsonify({'error': 'Inventory access denied',
                            'code': 'inventory_forbidden'}), 403
        except InventoryError as exc:
            con.close()
            return _inventory_error(exc)
    con.execute('BEGIN IMMEDIATE')
    sess = cur.execute(
        'SELECT status, store_id FROM restock_sessions WHERE id = ?', (sid,)
    ).fetchone()
    if sess['status'] not in ('submitted', 'picking'):
        con.close()
        return jsonify({'error': f'当前状态 {sess["status"]} 不可完成'}), 400
    store_id = sess['store_id']

    cur.execute(
        "SELECT COUNT(*) FROM restock_items WHERE session_id=? AND pick_status='pending'",
        (sid,),
    )
    pending_count = cur.fetchone()[0]
    if pending_count > 0:
        cur.execute(
            "SELECT ri.id, p.jizhanming FROM restock_items ri JOIN products p ON p.id=ri.product_id "
            "WHERE ri.session_id=? AND ri.pick_status='pending'",
            (sid,),
        )
        unhandled = [dict(r) for r in cur.fetchall()]
        con.close()
        return jsonify({'error': f'还有 {pending_count} 条未确认拣货', 'unhandled': unhandled}), 400

    cur.execute(
        "SELECT * FROM restock_items WHERE session_id=? AND pick_status='found' AND found_qty > 0",
        (sid,),
    )
    found_items = [dict(r) for r in cur.fetchall()]
    today = business_date()
    synced_details = []

    for item in found_items:
        stock_row = cur.execute(
            '''SELECT upstairs_qty, instore_qty FROM stock
               WHERE product_id=? AND store_id=?''',
            (item['product_id'], store_id),
        ).fetchone()
        available = stock_row['upstairs_qty'] if stock_row else 0
        instore = stock_row['instore_qty'] if stock_row else 0
        if item['found_qty'] > available:
            con.rollback()
            visible = can_see_inventory(con, request.jwt_payload, (store_id,))
            con.close()
            return jsonify({
                'error': f'Insufficient upstairs stock ({available})' if visible
                         else 'Insufficient upstairs stock',
                'code': 'insufficient_stock',
                'product_id': item['product_id'],
            }), 409
        try:
            read_int(instore + item['found_qty'], 'instore_qty')
        except ValueError as exc:
            con.rollback()
            con.close()
            return jsonify(invalid_input(exc)), 400

    for item in found_items:
        pid = item['product_id']
        requested_found = item['found_qty']
        _ensure_stock_row(cur, pid, store_id)
        effective_qty = requested_found

        cur.execute('''
            UPDATE stock
            SET upstairs_qty = upstairs_qty - ?,
                instore_qty  = instore_qty  + ?,
                last_updated = datetime('now')
            WHERE product_id = ? AND store_id = ? AND upstairs_qty >= ?
              AND instore_qty <= ?
        ''', (effective_qty, effective_qty, pid, store_id, effective_qty,
              SQLITE_INTEGER_MAX - effective_qty))
        if cur.rowcount != 1:
            con.rollback()
            con.close()
            return jsonify({
                'error': 'Stock changed before restock completion',
                'code': 'insufficient_stock',
                'product_id': pid,
            }), 409
        cur.execute('''
            INSERT INTO stock_movements
                (product_id, session_id, movement_type, qty_change, location, store_id)
            VALUES (?, ?, 'restock_out', ?, 'warehouse', ?)
        ''', (pid, sid, -effective_qty, store_id))
        cur.execute('''
            INSERT INTO stock_movements
                (product_id, session_id, movement_type, qty_change, location, store_id)
            VALUES (?, ?, 'restock_in', ?, 'store', ?)
        ''', (pid, sid, effective_qty, store_id))
        cur.execute('''
            INSERT INTO stock_transactions (product_id, txn_type, qty, location, date, notes, store_id)
            VALUES (?, 'ru_dian', ?, 'upstairs->instore', ?, ?, ?)
        ''', (pid, effective_qty, today, f'补货入店 session#{sid}', store_id))
        synced_details.append({'product_id': pid, 'requested': requested_found,
                               'found': requested_found, 'effective': effective_qty})

    cur.execute(
        "UPDATE restock_sessions SET status='completed', completed_at=datetime('now') WHERE id=?",
        (sid,),
    )
    con.commit()
    con.close()
    return jsonify({'ok': True, 'synced': len(synced_details), 'items': synced_details})
