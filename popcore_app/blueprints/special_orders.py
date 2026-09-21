"""Authenticated Special Orders routes."""
from flask import Blueprint, jsonify, request

from auth import role_required
from db import get_db
from inventory_commands import InventoryError
from special_order_operations import (
    add_payment,
    complete_order,
    correct_order,
    create_order,
    list_orders,
    order_detail,
)


bp = Blueprint('special_orders', __name__)


@bp.errorhandler(InventoryError)
@bp.errorhandler(ValueError)
@bp.errorhandler(PermissionError)
def error(exc):
    if isinstance(exc, InventoryError):
        return jsonify(error=str(exc), code=exc.code), exc.status
    if isinstance(exc, PermissionError):
        return jsonify(error=str(exc)), 403
    return jsonify(error=str(exc)), 400


def body():
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


@bp.get('/api/special-orders')
@role_required('staff')
def index():
    return jsonify(list_orders(
        get_db(), request.jwt_payload, request.args.get('status', 'open')
    ))


@bp.get('/api/special-orders/<int:order_id>')
@role_required('staff')
def detail(order_id):
    return jsonify(order_detail(get_db(), order_id, request.jwt_payload))


@bp.post('/api/special-orders')
@role_required('staff')
def create():
    result = create_order(
        get_db(), body(), request.jwt_payload,
        request.headers.get('Idempotency-Key'),
    )
    return jsonify(result), 201


@bp.post('/api/special-orders/<int:order_id>/payments')
@role_required('staff')
def payment(order_id):
    return jsonify(add_payment(
        get_db(), order_id, body(), request.jwt_payload,
        request.headers.get('Idempotency-Key'),
    ))


@bp.post('/api/special-orders/<int:order_id>/complete')
@role_required('staff')
def complete(order_id):
    return jsonify(complete_order(
        get_db(), order_id, body(), request.jwt_payload,
        request.headers.get('Idempotency-Key'),
    ))


@bp.patch('/api/special-orders/<int:order_id>')
@role_required('staff')
def correct(order_id):
    return jsonify(correct_order(
        get_db(), order_id, body(), request.jwt_payload,
        request.headers.get('Idempotency-Key'),
    ))
