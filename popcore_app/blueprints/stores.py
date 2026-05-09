"""
blueprints/stores.py — store directory and shared store-resolution helper.
"""
from flask import Blueprint, jsonify

from db import get_db
from auth import login_required

bp = Blueprint('stores', __name__)


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
        'SELECT id, code, name FROM stores WHERE is_active = 1 ORDER BY id'
    ).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])
