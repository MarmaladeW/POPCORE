"""Scoped store-day closing APIs."""
from flask import Blueprint, jsonify, request

from auth import role_required
from closing_operations import (
    add_cash_count, add_cash_event, add_closing_adjustment, close_closing,
    closing_detail, create_closing, return_closing, submit_closing, update_closing,
)
from db import get_db
from inventory_commands import InventoryError


bp = Blueprint('closing', __name__)


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _error(exc):
    if isinstance(exc, InventoryError):
        return jsonify({'error': str(exc), 'code': exc.code}), exc.status
    if isinstance(exc, ValueError):
        return jsonify({'error': str(exc), 'code': 'invalid_input'}), 400
    return jsonify({'error': 'Closing access denied', 'code': 'closing_forbidden'}), 403


@bp.post('/api/closing')
@role_required('staff')
def create():
    try:
        return jsonify(create_closing(
            get_db(), _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key'))), 201
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.get('/api/closing/<int:closing_id>')
@role_required('staff')
def detail(closing_id):
    try:
        return jsonify(closing_detail(get_db(), closing_id, actor=request.jwt_payload))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.patch('/api/closing/<int:closing_id>')
@role_required('staff')
def update(closing_id):
    try:
        return jsonify(update_closing(
            get_db(), closing_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/closing/<int:closing_id>/cash-events')
@role_required('staff')
def cash_event(closing_id):
    try:
        return jsonify(add_cash_event(
            get_db(), closing_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/closing/<int:closing_id>/cash-counts')
@role_required('staff')
def cash_count(closing_id):
    try:
        return jsonify(add_cash_count(
            get_db(), closing_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


def _action(closing_id, operation):
    try:
        return jsonify(operation(
            get_db(), closing_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/closing/<int:closing_id>/submit')
@role_required('staff')
def submit(closing_id):
    return _action(closing_id, submit_closing)


@bp.post('/api/closing/<int:closing_id>/return')
@role_required('manager')
def return_for_changes(closing_id):
    return _action(closing_id, return_closing)


@bp.post('/api/closing/<int:closing_id>/close')
@role_required('manager')
def close(closing_id):
    return _action(closing_id, close_closing)


@bp.post('/api/closing/<int:closing_id>/adjustments')
@role_required('manager')
def adjustment(closing_id):
    return _action(closing_id, add_closing_adjustment)
