"""Scoped store-day closing APIs."""
from flask import Blueprint, jsonify, request

from auth import role_required
from closing_operations import (
    add_cash_count, add_cash_event, add_closing_adjustment, close_closing,
    closing_detail, create_closing, return_closing, submit_closing, update_closing,
)
import clover_checkout
from db import get_db
from inventory_commands import InventoryError
from validation import read_date, read_int


bp = Blueprint('closing', __name__)


def _clover_day(con, *, closing_id=None, store_id=None, business_date=None):
    """Before closing looks at a store day, make sure every Clover order of that day is in POPCORE."""
    try:
        if closing_id is not None:
            session = con.execute('SELECT store_id,business_date FROM closing_sessions WHERE id=?',
                                  (closing_id,)).fetchone()
            if session is None:
                return
            store_id, business_date = session['store_id'], session['business_date']
        cfg = clover_checkout.store_config(read_int(store_id, 'store_id', minimum=1))
        if cfg:
            clover_checkout.sync_day(con, cfg, read_date(business_date, 'business_date'))
    except (InventoryError, PermissionError, ValueError, clover_checkout.ConnectorError):
        pass  # closing then shows "Clover orders not checked" until a scan succeeds


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
        data = _body()
        _clover_day(get_db(), store_id=data.get('store_id'), business_date=data.get('business_date'))
        return jsonify(create_closing(
            get_db(), data, actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key'))), 201
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.get('/api/closing/<int:closing_id>')
@role_required('staff')
def detail(closing_id):
    try:
        _clover_day(get_db(), closing_id=closing_id)
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
        if operation in (submit_closing, close_closing):
            _clover_day(get_db(), closing_id=closing_id)
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
