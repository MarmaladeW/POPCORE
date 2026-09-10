"""Scoped trade slot APIs."""
from flask import Blueprint, jsonify, request, send_file

from auth import login_required, role_required
from condition_evidence import evidence_path, publish, remove_unreferenced
from db import get_db
from goods_operations import _begin, _remember, _replay
from inventory_commands import InventoryError
from payment_evidence import prepare_image
from trade_operations import (
    _case, case_detail, create_condition_case, create_slot,
    decide_condition_case, list_slots, open_slot, record_inspection,
    require_case_access, sell_slot, slot_detail, swap_slot, trade_setup,
)


bp = Blueprint('trades', __name__)


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _error(exc):
    if isinstance(exc, InventoryError):
        return jsonify({'error': str(exc), 'code': exc.code}), exc.status
    if isinstance(exc, ValueError):
        return jsonify({'error': str(exc), 'code': 'invalid_input'}), 400
    return jsonify({'error': 'Trade access denied', 'code': 'trade_forbidden'}), 403


@bp.get('/api/trade-slots')
@role_required('staff')
def slots():
    try:
        return jsonify(list_slots(
            get_db(), actor=request.jwt_payload,
            store_id=request.args.get('store_id'),
        ))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.get('/api/trade-setup')
@role_required('staff')
def setup():
    try:
        return jsonify(trade_setup(
            get_db(), actor=request.jwt_payload,
            store_id=request.args.get('store_id'),
        ))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.get('/api/trade-slots/<int:slot_id>')
@role_required('staff')
def detail(slot_id):
    try:
        return jsonify(slot_detail(get_db(), slot_id, actor=request.jwt_payload))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/trade-slots')
@role_required('manager')
def create():
    try:
        return jsonify(create_slot(
            get_db(), _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key'),
        )), 201
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/trade-slots/<int:slot_id>/open')
@role_required('staff')
def open_trade_slot(slot_id):
    try:
        return jsonify(open_slot(
            get_db(), slot_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key'),
        ))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/trade-slots/<int:slot_id>/inspections')
@role_required('staff')
def inspect(slot_id):
    try:
        return jsonify(record_inspection(
            get_db(), slot_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key'),
        )), 201
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/trade-slots/<int:slot_id>/swap')
@role_required('staff')
def swap(slot_id):
    try:
        return jsonify(swap_slot(
            get_db(), slot_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key'),
        ))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/trade-slots/<int:slot_id>/sale')
@role_required('staff')
def sale(slot_id):
    try:
        return jsonify(sell_slot(
            get_db(), slot_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key'),
        ))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/condition-cases')
@role_required('staff')
def create_case():
    try:
        return jsonify(create_condition_case(
            get_db(), _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key'),
        )), 201
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.get('/api/condition-cases/<int:case_id>')
@login_required
def condition_detail(case_id):
    try:
        return jsonify(case_detail(get_db(), case_id, actor=request.jwt_payload))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/condition-cases/<int:case_id>/decision')
@role_required('manager')
def condition_decision(case_id):
    try:
        return jsonify(decide_condition_case(
            get_db(), case_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key'),
        ))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


def _evidence(con, evidence_id):
    row = con.execute(
        """SELECT e.*, c.store_id, c.created_by
           FROM condition_evidence e JOIN condition_cases c ON c.id=e.case_id
           WHERE e.id=?""", (evidence_id,),
    ).fetchone()
    if row is None:
        raise ValueError('Condition evidence was not found')
    return dict(row)


@bp.post('/api/condition-cases/<int:case_id>/evidence')
@role_required('staff')
def condition_upload(case_id):
    con = get_db()
    published = None
    try:
        case = _case(con, case_id)
        require_case_access(con, case, request.jwt_payload)
        prepared = prepare_image(request.files.get('image'))
        intent = {
            'case_id': case_id, 'content_hash': prepared['content_hash'],
            'mime_type': prepared['mime_type'], 'byte_size': prepared['byte_size'],
        }
        key, digest, prior = _replay(
            con, request.headers.get('Idempotency-Key'),
            'condition_evidence_upload', request.jwt_payload, intent,
        )
        if prior:
            return jsonify(prior)
        published = publish(prepared)
        _begin(con)
        key, digest, prior = _replay(
            con, key, 'condition_evidence_upload', request.jwt_payload, intent,
        )
        if prior:
            con.rollback()
            remove_unreferenced(published, con)
            return jsonify(prior)
        evidence_id = con.execute(
            """INSERT INTO condition_evidence
               (case_id, object_id, mime_type, byte_size, content_hash, uploaded_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (case_id, prepared['object_id'], prepared['mime_type'],
             prepared['byte_size'], prepared['content_hash'],
             request.jwt_payload['sub']),
        ).lastrowid
        result = {'evidence_id': evidence_id, 'case_id': case_id,
                  'mime_type': prepared['mime_type'],
                  'byte_size': prepared['byte_size']}
        _remember(con, key, 'condition_evidence_upload', evidence_id,
                  request.jwt_payload, digest, result)
        con.commit()
        return jsonify(result), 201
    except (InventoryError, PermissionError, ValueError) as exc:
        if con.in_transaction:
            con.rollback()
        if published is not None:
            remove_unreferenced(published, con)
        return _error(exc)


@bp.get('/api/condition-evidence/<int:evidence_id>/content')
@login_required
def condition_content(evidence_id):
    try:
        con = get_db()
        row = _evidence(con, evidence_id)
        require_case_access(con, row, request.jwt_payload)
        path = evidence_path(row['object_id'])
        if not path.is_file():
            raise ValueError('Condition evidence file is missing')
        response = send_file(path, mimetype=row['mime_type'], conditional=False)
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)
