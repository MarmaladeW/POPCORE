"""确认款价格 Design prices: a manager sets confirmed designs' prices in bulk (owner, 2026-11-12), and the
website sync sends them on (design_prices.py, website_sync.py, docs/website-sync.md)."""
from flask import Blueprint, jsonify, request

import design_prices
from auth import role_required
from db import get_db
from inventory_commands import InventoryError

bp = Blueprint('design_prices', __name__)


def _run(work):
    con = get_db()
    try:
        return work(con)
    except InventoryError as exc:
        con.rollback()
        return jsonify(error=str(exc), code=exc.code), exc.status


@bp.get('/api/design-prices')
@role_required('manager')
def design_price_list():
    return _run(lambda con: jsonify(design_prices.listing(con)))


@bp.post('/api/design-prices/check')
@role_required('manager')
def design_price_check():
    """What a change would do, in POPCORE and on the website, before it is saved. Nothing is kept."""
    return _run(lambda con: jsonify(design_prices.check(con, request.get_json(silent=True), actor=request.jwt_payload)))


@bp.post('/api/design-prices')
@role_required('manager')
def design_price_save():
    return _run(lambda con: jsonify(design_prices.save(
        con, request.get_json(silent=True), actor=request.jwt_payload,
        request_key=request.headers.get('Idempotency-Key'))))
