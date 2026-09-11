"""
blueprints/stores.py — store directory and shared store-resolution helper.
"""
import re
from flask import Blueprint, request, jsonify

from db import get_db
from auth import login_required, role_required
from inventory_commands import require_inventory_access

bp = Blueprint('stores', __name__)

_HEX_RE = re.compile(r'^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')


def _is_valid_hex(color: str) -> bool:
    return bool(_HEX_RE.match(color))


def _resolve_store(con, store_code):
    """Return (store_id, store_code) or None if code is invalid / inactive."""
    row = con.execute(
        'SELECT id, code FROM stores WHERE code = ? AND is_active = 1',
        (store_code,),
    ).fetchone()
    return (row['id'], row['code']) if row else None


@bp.route('/api/stores')
@login_required
def list_stores():
    con = get_db()
    rows = con.execute(
        "SELECT id, code, name, COALESCE(color, '#6366f1') AS color"
        " FROM stores WHERE is_active = 1 ORDER BY id"
    ).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/stores/<int:store_id>/color', methods=['PATCH'])
@role_required('manager')
def patch_store_color(store_id):
    data  = request.get_json() or {}
    color = (data.get('color') or '').strip()
    if not _is_valid_hex(color):
        return jsonify({'error': 'color must be a valid hex color (#RGB or #RRGGBB)'}), 400
    con = get_db()
    row = con.execute('SELECT id FROM stores WHERE id = ?', (store_id,)).fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'Store not found'}), 404
    con.execute('UPDATE stores SET color = ? WHERE id = ?', (color, store_id))
    con.commit()
    updated = con.execute(
        "SELECT id, code, name, COALESCE(color, '#6366f1') AS color"
        " FROM stores WHERE id = ?", (store_id,)
    ).fetchone()
    con.close()
    return jsonify(dict(updated))


@bp.route('/api/stores', methods=['POST'])
@role_required('admin')
def create_store():
    data = request.get_json() or {}
    code = (data.get('code') or '').strip().upper()
    name = (data.get('name') or '').strip()
    if not code or not name:
        return jsonify({'error': '门店代码和名称均为必填 / Code and name are required'}), 400
    if len(code) > 10:
        return jsonify({'error': '门店代码最长10字符 / Code max 10 chars'}), 400
    con = get_db()
    existing = con.execute('SELECT id FROM stores WHERE code = ?', (code,)).fetchone()
    if existing:
        return jsonify({'error': f'门店代码 {code} 已存在 / Store code already exists'}), 409
    con.execute(
        "INSERT INTO stores (code, name, is_active) VALUES (?, ?, 1)",
        (code, name),
    )
    con.commit()
    row = con.execute(
        "SELECT id, code, name, COALESCE(color, '#6366f1') AS color FROM stores WHERE code = ?",
        (code,),
    ).fetchone()
    return jsonify(dict(row)), 201


@bp.route('/api/stores/<int:store_id>', methods=['DELETE'])
@role_required('admin')
def delete_store(store_id):
    con = get_db()
    row = con.execute('SELECT id, code FROM stores WHERE id = ?', (store_id,)).fetchone()
    if not row:
        return jsonify({'error': '门店不存在 / Store not found'}), 404
    store_code = row['code']
    if con.execute('SELECT 1 FROM daily_sales WHERE store = ? LIMIT 1', (store_code,)).fetchone():
        return jsonify({'error': '该门店有关联销售记录，无法删除 / Store has linked sales data'}), 400
    if con.execute('SELECT 1 FROM stock WHERE store_id = ? LIMIT 1', (store_id,)).fetchone():
        return jsonify({'error': '该门店有关联库存记录，无法删除 / Store has linked stock data'}), 400
    con.execute('DELETE FROM stores WHERE id = ?', (store_id,))
    con.commit()
    return jsonify({'ok': True})

# Inventory locations and permissions are separate from Schedule store membership.
@bp.route('/api/inventory/locations')
@login_required
def list_inventory_locations():
    con = get_db()
    try:
        requested_code = request.args.get('store_code', '').strip().upper()
        if requested_code:
            store = con.execute(
                """SELECT DISTINCT s.id
                   FROM stores s
                   JOIN inventory_locations l ON l.store_id=s.id
                   WHERE s.code=? AND s.is_active=1 AND l.is_active=1""",
                (requested_code,),
            ).fetchone()
            if store is None:
                return jsonify({'error': 'Inventory store not found',
                                'code': 'inventory_store_missing'}), 404
            try:
                require_inventory_access(
                    con, request.jwt_payload, (store['id'],), 'viewer'
                )
            except PermissionError:
                return jsonify({'error': 'Inventory access denied',
                                'code': 'inventory_forbidden'}), 403
            store_filter = 'AND s.id=?'
            params = (request.jwt_payload['sub'], store['id'])
        else:
            store_filter = ''
            params = (request.jwt_payload['sub'],)

        rows = con.execute(
            f"""SELECT l.id, l.store_id, s.code AS store_code,
                       l.code, l.name, l.is_active,
                       ss.opening_verified, ss.opening_document_id
                FROM inventory_locations l
                JOIN stores s ON s.id=l.store_id
                JOIN inventory_access ia ON ia.store_id=s.id
                                         AND ia.auth0_sub=?
                LEFT JOIN inventory_scope_state ss
                       ON ss.store_id=s.id AND ss.location_id=l.id
                WHERE s.is_active=1 AND l.is_active=1 {store_filter}
                ORDER BY s.code, l.code""",
            params,
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['is_active'] = bool(item['is_active'])
            item['opening_verified'] = bool(item['opening_verified'])
            result.append(item)
        return jsonify(result)
    finally:
        con.close()


@bp.route('/api/inventory/access', methods=['POST'])
@role_required('admin')
def grant_inventory_access():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Expected a JSON object',
                        'code': 'invalid_input'}), 400
    subject = data.get('auth0_sub')
    store_id = data.get('store_id')
    if not isinstance(subject, str) or not subject.strip():
        return jsonify({'error': 'auth0_sub is required',
                        'code': 'invalid_input'}), 400
    if type(store_id) is not int or store_id < 1:
        return jsonify({'error': 'store_id must be a positive integer',
                        'code': 'invalid_input'}), 400
    con = get_db()
    try:
        if not con.execute(
            """SELECT 1 FROM inventory_locations
               WHERE store_id=? AND is_active=1 LIMIT 1""", (store_id,)
        ).fetchone():
            return jsonify({'error': 'Inventory store not found',
                            'code': 'inventory_store_missing'}), 404
        con.execute(
            "INSERT OR IGNORE INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)",
            (subject.strip(), store_id),
        )
        con.commit()
        return jsonify({'ok': True, 'auth0_sub': subject.strip(),
                        'store_id': store_id}), 201
    finally:
        con.close()