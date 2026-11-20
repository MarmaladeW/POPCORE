"""员工折扣 Staff discounts: staff see who has uses left this month; managers fix, ignore and add. See staff_discounts.py."""
from flask import Blueprint, jsonify, request

from auth import role_required
from db import get_db
from staff_discounts import add_use, fix_use, ignore_use, month_page, restore_use


bp = Blueprint('staff_discounts', __name__)


def _answer(work):
    con = get_db()
    try:
        return jsonify(work(con))
    except LookupError as exc:
        return jsonify({'error': str(exc), 'code': 'not_found'}), 404
    except ValueError as exc:
        return jsonify({'error': str(exc), 'code': 'invalid_input'}), 400


@bp.get('/api/staff-discounts')
@role_required('staff')
def page():
    return _answer(lambda con: month_page(con, request.jwt_payload, request.args.get('month')))


@bp.post('/api/staff-discounts')
@role_required('manager')
def add():
    return _answer(lambda con: add_use(con, request.jwt_payload, request.get_json(silent=True),
                                       request_key=request.headers.get('Idempotency-Key')))


@bp.post('/api/staff-discounts/<int:use_id>/fix')
@role_required('manager')
def fix(use_id):
    return _answer(lambda con: fix_use(con, request.jwt_payload, use_id, request.get_json(silent=True)))


@bp.post('/api/staff-discounts/<int:use_id>/ignore')
@role_required('manager')
def ignore(use_id):
    return _answer(lambda con: ignore_use(con, request.jwt_payload, use_id, request.get_json(silent=True)))


@bp.post('/api/staff-discounts/<int:use_id>/restore')
@role_required('manager')
def restore(use_id):
    return _answer(lambda con: restore_use(con, request.jwt_payload, use_id))
