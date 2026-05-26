"""
blueprints/stores.py — store directory and shared store-resolution helper.
"""
import re
from flask import Blueprint, request, jsonify

from db import get_db
from auth import login_required, role_required

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
