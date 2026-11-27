"""POP MART 价格 POP MART prices: managers see POPCORE's prices beside POP MART Canada's and POP MART US's, ask for a
read now, and match a product by hand where its name differs from POP MART's (popmart_prices.py,
docs/popmart-prices.md). Nothing here changes a price."""
from flask import Blueprint, jsonify, request

import popmart_prices
from auth import role_required
from db import get_db

bp = Blueprint('popmart_prices', __name__)


@bp.get('/api/popmart-prices')
@role_required('manager')
def popmart_price_list():
    return jsonify(popmart_prices.listing(get_db()))


@bp.post('/api/popmart-prices/read')
@role_required('manager')
def popmart_price_read():
    """The background job starts it within a minute; the page asks again to see it finish."""
    popmart_prices.request_read(get_db(), request.jwt_payload.get('sub'))
    return jsonify(requested=True), 202


@bp.put('/api/popmart-prices/links/<int:product_id>')
@role_required('manager')
def popmart_price_link(product_id):
    con = get_db()
    try:
        popmart_prices.link(con, product_id, (request.get_json(silent=True) or {}).get('title'), request.jwt_payload.get('sub'))
    except ValueError as exc:
        con.rollback()
        return jsonify(error=str(exc)), 400
    return jsonify(linked=True)


@bp.delete('/api/popmart-prices/links/<int:product_id>')
@role_required('manager')
def popmart_price_unlink(product_id):
    popmart_prices.unlink(get_db(), product_id)
    return jsonify(linked=False)
