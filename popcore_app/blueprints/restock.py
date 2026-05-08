"""
blueprints/restock.py — restock session workflow (pending → submitted → picking → completed).
"""
from datetime import date
from flask import Blueprint, request, jsonify

from db import get_db, _ensure_stock_row
from auth import login_required, role_required
from blueprints.stores import _resolve_store

bp = Blueprint('restock', __name__)


def _restock_session_items(cur, sid, store_id):
    """Return items for a session, joined with product and live stock info."""
    cur.execute('''
        SELECT ri.id, ri.product_id, ri.requested_qty, ri.warehouse_stock_snapshot,
               ri.found_qty, ri.pick_status,
               p.sku, p.jizhanming, p.name_cn_en, p.ip_series, p.product_type,
               p.boxes_per_dan, p.dan_per_xiang,
               COALESCE(s.upstairs_qty, 0) AS upstairs_qty,
               COALESCE(s.instore_qty,  0) AS instore_qty
        FROM restock_items ri
        JOIN products p ON p.id = ri.product_id
        LEFT JOIN stock s ON s.product_id = ri.product_id AND s.store_id = ?
        WHERE ri.session_id = ?
        ORDER BY ri.id
    ''', (store_id, sid))
    return [dict(r) for r in cur.fetchall()]


@bp.route('/api/restock/sessions/today')
@login_required
def restock_sessions_today():
    today = date.today().isoformat()
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
    today = date.today().isoformat()
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
    - completed: reverse stock movements first, then delete.
    """
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT status, store_id FROM restock_sessions WHERE id = ?', (sid,))
    sess = cur.fetchone()
    if not sess:
        con.close()
        return jsonify({'error': 'Session not found'}), 404

    status   = sess['status']
    store_id = sess['store_id']
    reversed_count = 0

    if status == 'completed':
        cur.execute('''
            SELECT product_id, qty_change
            FROM stock_movements
            WHERE session_id = ? AND movement_type = 'restock_in' AND location = 'store'
        ''', (sid,))
        movements = cur.fetchall()
        for m in movements:
            pid = m['product_id']
            qty = m['qty_change']
            cur.execute('''
                UPDATE stock
                SET instore_qty  = MAX(0, instore_qty  - ?),
                    upstairs_qty = upstairs_qty + ?,
                    last_updated = datetime('now')
                WHERE product_id = ? AND store_id = ?
            ''', (qty, qty, pid, store_id))
            reversed_count += 1
        cur.execute('DELETE FROM stock_movements WHERE session_id = ?', (sid,))
        cur.execute(
            "DELETE FROM stock_transactions WHERE notes LIKE ?",
            (f'%session#{sid}%',),
        )

    cur.execute('DELETE FROM restock_items WHERE session_id = ?', (sid,))
    cur.execute('DELETE FROM restock_sessions WHERE id = ?', (sid,))
    con.commit()
    con.close()
    return jsonify({'ok': True, 'reversed_count': reversed_count})


@bp.route('/api/restock/session/today')
@login_required
def restock_session_today():
    """Legacy: return the most recent pending session today for a given store, or 404."""
    today = date.today().isoformat()
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
    session['items'] = _restock_session_items(cur, sid, store_id)
    con.close()
    return jsonify(session)


@bp.route('/api/restock/items', methods=['POST'])
@role_required('staff')
def add_restock_item():
    data = request.get_json() or {}
    sid  = data.get('session_id')
    pid  = data.get('product_id')
    qty  = data.get('requested_qty')
    if not sid or not pid or not qty or int(qty) <= 0:
        return jsonify({'error': 'session_id, product_id, requested_qty 必须填写且有效'}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT status FROM restock_sessions WHERE id = ?', (int(sid),))
    sess = cur.fetchone()
    if not sess:
        con.close()
        return jsonify({'error': 'Session not found'}), 404
    if sess['status'] != 'pending':
        con.close()
        return jsonify({'error': '只能在 pending 状态下修改清单'}), 403

    cur.execute('''
        INSERT INTO restock_items (session_id, product_id, requested_qty)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id, product_id) DO UPDATE SET
            requested_qty = excluded.requested_qty
    ''', (int(sid), int(pid), int(qty)))
    con.commit()
    item_id = cur.lastrowid
    con.close()
    return jsonify({'ok': True, 'id': item_id}), 201


@bp.route('/api/restock/items/<int:iid>', methods=['DELETE'])
@role_required('staff')
def delete_restock_item(iid):
    con = get_db()
    cur = con.cursor()
    cur.execute('''
        SELECT rs.status FROM restock_items ri
        JOIN restock_sessions rs ON rs.id = ri.session_id
        WHERE ri.id = ?
    ''', (iid,))
    row = cur.fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'Item not found'}), 404
    if row['status'] != 'pending':
        con.close()
        return jsonify({'error': '只能在 pending 状态下删除条目'}), 400
    cur.execute('DELETE FROM restock_items WHERE id = ?', (iid,))
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
        SELECT ri.*, rs.status AS session_status, rs.id AS session_id
        FROM restock_items ri
        JOIN restock_sessions rs ON rs.id = ri.session_id
        WHERE ri.id = ?
    ''', (iid,))
    item = cur.fetchone()
    if not item:
        con.close()
        return jsonify({'error': 'Item not found'}), 404
    if item['session_status'] not in ('submitted', 'picking'):
        con.close()
        return jsonify({'error': '只能在 submitted/picking 状态下更新拣货结果'}), 400

    if pick_status == 'not_found':
        found_qty = 0
    else:
        found_qty = data.get('found_qty')
        if found_qty is None:
            con.close()
            return jsonify({'error': 'found 时须提供 found_qty'}), 400
        found_qty = int(found_qty)
        if found_qty < 1:
            con.close()
            return jsonify({'error': 'found_qty 须 >= 1'}), 400
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
    effective = min(found_qty, actual_upstairs_qty) guards against negatives.
    """
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT status, store_id FROM restock_sessions WHERE id = ?', (sid,))
    sess = cur.fetchone()
    if not sess:
        con.close()
        return jsonify({'error': 'Session not found'}), 404
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
    today = date.today().isoformat()
    synced_details = []

    for item in found_items:
        pid = item['product_id']
        requested_found = item['found_qty']
        _ensure_stock_row(cur, pid, store_id)
        cur.execute('SELECT upstairs_qty FROM stock WHERE product_id=? AND store_id=?',
                    (pid, store_id))
        stock_row = cur.fetchone()
        actual_warehouse = stock_row['upstairs_qty'] if stock_row else 0
        effective_qty = min(requested_found, actual_warehouse)
        if effective_qty <= 0:
            synced_details.append({'product_id': pid, 'requested': requested_found,
                                   'found': requested_found, 'effective': 0})
            continue

        cur.execute('''
            UPDATE stock
            SET upstairs_qty = upstairs_qty - ?,
                instore_qty  = instore_qty  + ?,
                last_updated = datetime('now')
            WHERE product_id = ? AND store_id = ?
        ''', (effective_qty, effective_qty, pid, store_id))
        cur.execute('''
            INSERT INTO stock_movements (product_id, session_id, movement_type, qty_change, location)
            VALUES (?, ?, 'restock_out', ?, 'warehouse')
        ''', (pid, sid, -effective_qty))
        cur.execute('''
            INSERT INTO stock_movements (product_id, session_id, movement_type, qty_change, location)
            VALUES (?, ?, 'restock_in', ?, 'store')
        ''', (pid, sid, effective_qty))
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
