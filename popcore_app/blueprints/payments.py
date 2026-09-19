"""Scoped tender review and private evidence APIs."""
from checkout_access import require_linked_sale
from flask import Blueprint, jsonify, request, send_file

from auth import ROLE_CLAIM, ROLE_HIERARCHY, login_required, role_required
from db import get_db
from goods_operations import _begin, _remember, _replay
from inventory_commands import InventoryError, InventoryConflict, require_inventory_access
from payment_evidence import evidence_path, prepare_image, publish, remove_unreferenced
from sales_operations import _payment_sale, record_payment_event


bp = Blueprint('payments', __name__)


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _error(exc):
    if isinstance(exc, InventoryError):
        return jsonify({'error': str(exc), 'code': exc.code}), exc.status
    if isinstance(exc, ValueError):
        return jsonify({'error': str(exc), 'code': 'invalid_input'}), 400
    return jsonify({'error': 'Payment access denied', 'code': 'payment_forbidden'}), 403


def _record(payment_id, event_type):
    try:
        return jsonify(record_payment_event(
            get_db(), payment_id, event_type, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key'),
        ))
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/payments/<int:payment_id>/verify')
@role_required('manager')
def verify(payment_id):
    return _record(payment_id, 'verify')


@bp.post('/api/payments/<int:payment_id>/reject')
@role_required('manager')
def reject(payment_id):
    return _record(payment_id, 'reject')


@bp.post('/api/payments/<int:payment_id>/events')
@role_required('manager')
def event(payment_id):
    data = _body()
    return _record(payment_id, data.get('event_type'))


def _evidence_row(con, evidence_id):
    row = con.execute(
        """SELECT e.*, p.recorded_by, p.sale_id, s.store_id
           FROM payment_evidence e
           JOIN sale_payments p ON p.id=e.payment_id
           JOIN sale_documents s ON s.id=p.sale_id
           WHERE e.id=?""", (evidence_id,),
    ).fetchone()
    if row is None:
        raise InventoryConflict('Payment evidence was not found', 'evidence_not_found')
    return dict(row)


def _require_evidence_access(con, row, actor, minimum='staff'):
    if require_linked_sale(con, row['sale_id'], actor, write=minimum == 'manager') and minimum == 'staff':
        return
    require_inventory_access(con, actor, (row['store_id'],), minimum)
    role = actor.get(ROLE_CLAIM, 'viewer')
    if (ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY['manager']
            and row['uploader_sub'] != actor.get('sub')):
        raise PermissionError('Payment evidence access denied')


@bp.post('/api/payments/<int:payment_id>/evidence')
@role_required('staff')
def upload_evidence(payment_id):
    con = get_db()
    published = None
    try:
        payment = _payment_sale(con, payment_id)
        require_linked_sale(con, payment['sale_id'], request.jwt_payload, write=True)
        require_inventory_access(con, request.jwt_payload, (payment['store_id'],), 'staff')
        role = request.jwt_payload.get(ROLE_CLAIM, 'viewer')
        if (ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY['manager']
                and payment['recorded_by'] != request.jwt_payload.get('sub')):
            raise PermissionError('Payment evidence access denied')
        prepared = prepare_image(request.files.get('image'))
        intent = {
            'payment_id': payment_id, 'content_hash': prepared['content_hash'],
            'mime_type': prepared['mime_type'], 'byte_size': prepared['byte_size'],
        }
        key, digest, prior = _replay(
            con, request.headers.get('Idempotency-Key'),
            'payment_evidence_upload', request.jwt_payload, intent,
        )
        if prior:
            return jsonify(prior)
        published = publish(prepared)
        _begin(con)
        require_linked_sale(con, payment['sale_id'], request.jwt_payload, write=True)
        key, digest, prior = _replay(
            con, key, 'payment_evidence_upload', request.jwt_payload, intent,
        )
        if prior:
            con.rollback()
            remove_unreferenced(published, con)
            return jsonify(prior)
        evidence_id = con.execute(
            """INSERT INTO payment_evidence
               (payment_id, object_id, mime_type, byte_size, uploader_sub)
               VALUES (?, ?, ?, ?, ?)""",
            (payment_id, prepared['object_id'], prepared['mime_type'],
             prepared['byte_size'], request.jwt_payload['sub']),
        ).lastrowid
        result = {
            'evidence_id': evidence_id, 'payment_id': payment_id,
            'mime_type': prepared['mime_type'], 'byte_size': prepared['byte_size'],
            'status': 'pending',
        }
        _remember(
            con, key, 'payment_evidence_upload', evidence_id,
            request.jwt_payload, digest, result,
        )
        con.commit()
        return jsonify(result), 201
    except (InventoryError, PermissionError, ValueError) as exc:
        if con.in_transaction:
            con.rollback()
        if published is not None:
            remove_unreferenced(published, con)
        return _error(exc)


@bp.get('/api/payment-evidence/<int:evidence_id>/content')
@login_required
def evidence_content(evidence_id):
    con = get_db()
    try:
        row = _evidence_row(con, evidence_id)
        _require_evidence_access(con, row, request.jwt_payload)
        path = evidence_path(row['object_id'])
        if not path.is_file():
            raise InventoryConflict('Payment evidence file is missing', 'evidence_missing')
        response = send_file(path, mimetype=row['mime_type'], conditional=False)
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
    except (InventoryError, PermissionError, ValueError) as exc:
        return _error(exc)


@bp.post('/api/payment-evidence/<int:evidence_id>/review')
@role_required('manager')
def review_evidence(evidence_id):
    con = get_db()
    try:
        row = _evidence_row(con, evidence_id)
        _require_evidence_access(con, row, request.jwt_payload, 'manager')
        data = _body()
        decision = data.get('decision')
        reason = str(data.get('reason') or '').strip()
        if decision not in {'accepted', 'rejected'} or not reason:
            raise ValueError('decision and reason are required')
        intent = {'evidence_id': evidence_id, 'decision': decision, 'reason': reason}
        _begin(con)
        _require_evidence_access(con, row, request.jwt_payload, 'manager')
        key, digest, prior = _replay(
            con, request.headers.get('Idempotency-Key'),
            'payment_evidence_review', request.jwt_payload, intent,
        )
        if prior:
            con.commit()
            return jsonify(prior)
        row = _evidence_row(con, evidence_id)
        if row['status'] != 'pending':
            raise InventoryConflict('Evidence was already reviewed', 'evidence_state_conflict')
        con.execute(
            """INSERT INTO payment_evidence_reviews
               (evidence_id, decision, reason, reviewer_sub) VALUES (?, ?, ?, ?)""",
            (evidence_id, decision, reason, request.jwt_payload['sub']),
        )
        con.execute(
            'UPDATE payment_evidence SET status=? WHERE id=?', (decision, evidence_id)
        )
        result = {'evidence_id': evidence_id, 'status': decision}
        _remember(
            con, key, 'payment_evidence_review', evidence_id,
            request.jwt_payload, digest, result,
        )
        con.commit()
        return jsonify(result)
    except (InventoryError, PermissionError, ValueError) as exc:
        if con.in_transaction:
            con.rollback()
        return _error(exc)
