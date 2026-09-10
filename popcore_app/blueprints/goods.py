"""Scoped goods receiving, delivery, and physical count APIs."""
from flask import Blueprint, jsonify, request

from auth import login_required, role_required
from db import get_db
from goods_operations import (
    act_on_delivery, cancel_receipt, create_count, create_delivery, create_receipt,
    delivery_detail, post_receipt, transition_count,
    restock_suggestions, save_target, update_receipt,
)
from inventory_commands import InventoryError
from catalog_identity import resolve_barcode


bp = Blueprint('goods', __name__)


def _error(exc):
    if isinstance(exc, InventoryError):
        return jsonify({'error': str(exc), 'code': exc.code}), exc.status
    return jsonify({'error': 'Inventory access denied',
                    'code': 'inventory_forbidden'}), 403


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@bp.get('/api/goods/barcodes/resolve')
@role_required('staff')
def barcode_resolve():
    try:
        return jsonify(resolve_barcode(
            get_db(), request.args.get('code'), request.args.get('purpose', 'receive')
        ))
    except ValueError as exc:
        return jsonify({'error': str(exc), 'code': 'invalid_input'}), 400


@bp.post('/api/goods/receipts')
@role_required('staff')
def receipt_create():
    try:
        result = create_receipt(get_db(), _body(), actor=request.jwt_payload,
                                request_key=request.headers.get('Idempotency-Key'))
        return jsonify(result), 201
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.get('/api/goods/receipts/<int:receipt_id>')
@login_required
def receipt_detail(receipt_id):
    con = get_db()
    row = con.execute(
        """SELECT r.* FROM goods_receipts r
           JOIN inventory_access a ON a.store_id=r.store_id
           WHERE r.id=? AND a.auth0_sub=?""",
        (receipt_id, request.jwt_payload.get('sub')),
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Receipt not found'}), 404
    result = dict(row)
    result['lines'] = [dict(line) for line in con.execute(
        'SELECT * FROM goods_receipt_lines WHERE receipt_id=? ORDER BY line_no',
        (receipt_id,),
    )]
    return jsonify(result)


@bp.patch('/api/goods/receipts/<int:receipt_id>')
@role_required('staff')
def receipt_update(receipt_id):
    try:
        return jsonify(update_receipt(
            get_db(), receipt_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.post('/api/goods/receipts/<int:receipt_id>/post')
@role_required('staff')
def receipt_post(receipt_id):
    try:
        return jsonify(post_receipt(
            get_db(), receipt_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.post('/api/goods/receipts/<int:receipt_id>/cancel')
@role_required('staff')
def receipt_cancel(receipt_id):
    try:
        return jsonify(cancel_receipt(
            get_db(), receipt_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.post('/api/goods/transfers')
@role_required('manager')
def transfer_create():
    try:
        result = create_delivery(
            get_db(), _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key'), kind='transfer')
        return jsonify(result), 201
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.get('/api/goods/transfers/<int:delivery_id>')
@login_required
def transfer_detail(delivery_id):
    try:
        return jsonify(delivery_detail(get_db(), delivery_id,
                                       actor=request.jwt_payload))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.post('/api/goods/transfers/<int:delivery_id>/<action>')
@role_required('staff')
def transfer_action(delivery_id, action):
    try:
        return jsonify(act_on_delivery(
            get_db(), delivery_id, action, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.post('/api/goods/counts')
@role_required('staff')
def count_create():
    try:
        result = create_count(get_db(), _body(), actor=request.jwt_payload,
                              request_key=request.headers.get('Idempotency-Key'))
        return jsonify(result), 201
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.get('/api/goods/counts/<int:count_id>')
@login_required
def count_detail(count_id):
    con = get_db()
    row = con.execute(
        """SELECT c.* FROM inventory_counts c
           JOIN inventory_access a ON a.store_id=c.store_id
           WHERE c.id=? AND a.auth0_sub=?""",
        (count_id, request.jwt_payload.get('sub')),
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Count not found'}), 404
    result = dict(row)
    result['lines'] = [dict(line) for line in con.execute(
        'SELECT * FROM inventory_count_lines WHERE count_id=? ORDER BY line_no',
        (count_id,),
    )]
    return jsonify(result)


@bp.post('/api/goods/counts/<int:count_id>/<action>')
@role_required('staff')
def count_action(count_id, action):
    if action not in {'submit', 'approve', 'return'}:
        return jsonify({'error': 'Count action not found'}), 404
    try:
        return jsonify(transition_count(
            get_db(), count_id, action, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.put('/api/goods/targets')
@role_required('manager')
def target_save():
    try:
        return jsonify(save_target(
            get_db(), _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.get('/api/goods/restock-suggestions')
@role_required('staff')
def suggestion_list():
    try:
        return jsonify(restock_suggestions(
            get_db(), request.args.get('location_id'), actor=request.jwt_payload
        ))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)
