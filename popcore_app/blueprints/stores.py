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
