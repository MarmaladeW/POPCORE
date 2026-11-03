"""损耗 Loss: record broken, stolen or missing stock and handle the damaged pile. See stock_losses.py."""
from flask import Blueprint, jsonify, request

from auth import role_required
from db import get_db
from inventory_commands import InventoryError
from inventory_visibility import InventoryHidden, hidden_response
from stock_losses import act_on_pile, loss_page, record_loss, search_products, undo_loss


bp = Blueprint('losses', __name__)


def _answer(work):
    con = get_db()
    try:
        return jsonify(work(con))
    except InventoryHidden as exc:
        return hidden_response(exc)
    except InventoryError as exc:
        return jsonify({'error': str(exc), 'code': exc.code}), exc.status
    except PermissionError as exc:
        return jsonify({'error': str(exc) or 'Inventory access denied', 'code': 'inventory_forbidden'}), 403
    except ValueError as exc:
        return jsonify({'error': str(exc), 'code': 'invalid_input'}), 400


@bp.get('/api/losses')
@role_required('manager')
def page():
    args = request.args
    return _answer(lambda con: loss_page(con, request.jwt_payload, args.get('store_code'), args.get('month')))


@bp.get('/api/losses/search')
@role_required('manager')
def search():
    args = request.args
    return _answer(lambda con: search_products(con, request.jwt_payload, args.get('store_code'), args.get('q')))


@bp.post('/api/losses')
@role_required('manager')
def record():
    return _answer(lambda con: record_loss(con, request.jwt_payload, request.get_json(silent=True),
                                           request_key=request.headers.get('Idempotency-Key')))


@bp.post('/api/losses/<int:loss_id>/undo')
@role_required('manager')
def undo(loss_id):
    return _answer(lambda con: undo_loss(con, request.jwt_payload, loss_id,
                                         request_key=request.headers.get('Idempotency-Key')))


@bp.post('/api/losses/pile')
@role_required('manager')
def pile():
    return _answer(lambda con: act_on_pile(con, request.jwt_payload, request.get_json(silent=True),
                                           request_key=request.headers.get('Idempotency-Key')))
