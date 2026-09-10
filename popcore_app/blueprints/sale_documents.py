"""Scoped individual sale document APIs."""
from flask import Blueprint, jsonify, request

from auth import login_required, role_required
from db import get_db
from inventory_commands import InventoryError
from sales_operations import (
    add_sale_payments, add_sale_source, allocate_sale, create_sale,
    create_sale_reconciliation, post_sale, record_sale_return, sale_detail,
    update_sale,
)


bp = Blueprint('sale_documents', __name__)


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _error(exc):
    if isinstance(exc, InventoryError):
        return jsonify({'error': str(exc), 'code': exc.code}), exc.status
    if isinstance(exc, ValueError):
        return jsonify({'error': str(exc), 'code': 'invalid_input'}), 400
    return jsonify({'error': 'Sale access denied', 'code': 'sale_forbidden'}), 403


@bp.post('/api/sale-documents')
@role_required('staff')
def create():
    try:
        return jsonify(create_sale(
            get_db(), _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key'))), 201
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.get('/api/sale-documents/<int:sale_id>')
@login_required
def detail(sale_id):
    try:
        return jsonify(sale_detail(get_db(), sale_id, actor=request.jwt_payload))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.patch('/api/sale-documents/<int:sale_id>')
@role_required('staff')
def update(sale_id):
    try:
        return jsonify(update_sale(
            get_db(), sale_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/sale-documents/<int:sale_id>/post')
@role_required('staff')
def post(sale_id):
    try:
        return jsonify(post_sale(
            get_db(), sale_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/sale-documents/<int:sale_id>/allocate')
@role_required('manager')
def allocate(sale_id):
    try:
        return jsonify(allocate_sale(
            get_db(), sale_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/sale-documents/<int:sale_id>/payments')
@role_required('staff')
def payments(sale_id):
    try:
        return jsonify(add_sale_payments(
            get_db(), sale_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/sale-documents/<int:sale_id>/source-links')
@role_required('manager')
def source_link(sale_id):
    try:
        return jsonify(add_sale_source(
            get_db(), sale_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/sale-documents/<int:sale_id>/returns')
@role_required('manager')
def sale_return(sale_id):
    try:
        return jsonify(record_sale_return(
            get_db(), sale_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/sale-reconciliations')
@role_required('manager')
def reconciliation():
    try:
        return jsonify(create_sale_reconciliation(
            get_db(), _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key'))), 201
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)
