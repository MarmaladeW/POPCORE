"""盘点 Count: find a product, count it (with its sealed sets and designs) on one card, and see
how much of a location is counted. See stock_counting.py."""
from flask import Blueprint, jsonify, request

from auth import role_required
from db import get_db
from inventory_commands import InventoryError
from stock_counting import count_card, count_progress, count_scope, save_counts, search_targets


bp = Blueprint('counting', __name__)


def _answer(work):
    try:
        return jsonify(work(get_db()))
    except InventoryError as exc:
        return jsonify({'error': str(exc), 'code': exc.code, **getattr(exc, 'details', {})}), exc.status
    except PermissionError as exc:
        return jsonify({'error': str(exc) or 'Inventory access denied', 'code': 'inventory_forbidden'}), 403
    except ValueError as exc:
        return jsonify({'error': str(exc), 'code': 'invalid_input'}), 400


@bp.get('/api/counting/scope')
@role_required('staff')
def scope():
    return _answer(lambda con: count_scope(con, request.jwt_payload, request.args.get('store_code')))


@bp.get('/api/counting/search')
@role_required('staff')
def search():
    args = request.args
    return _answer(lambda con: search_targets(
        con, request.jwt_payload, args.get('store_code'), args.get('location') or '', args.get('q')))


@bp.get('/api/counting/card')
@role_required('staff')
def card():
    args = request.args
    return _answer(lambda con: count_card(
        con, request.jwt_payload, args.get('store_code'), args.get('location') or '', args.get('product_id')))


@bp.post('/api/counting/counts')
@role_required('staff')
def save():
    data = request.get_json(silent=True)
    body = data if isinstance(data, dict) else {}
    return _answer(lambda con: save_counts(
        con, request.jwt_payload, body.get('store_code'), body.get('location') or '', data,
        request_key=request.headers.get('Idempotency-Key')))


@bp.get('/api/counting/progress')
@role_required('staff')
def progress():
    args = request.args
    return _answer(lambda con: count_progress(
        con, request.jwt_payload, args.get('store_code'), args.get('location') or ''))
