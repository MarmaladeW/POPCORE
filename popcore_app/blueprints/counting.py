"""盘点 Count: find a product, count it (with its sealed sets and designs) on one card, and see
how much of a location is counted. See stock_counting.py."""
from flask import Blueprint, jsonify, request

import count_marks
import count_sections
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
        return jsonify({'error': str(exc) or 'Inventory access denied',
                        'code': getattr(exc, 'code', 'inventory_forbidden')}), 403
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


# 分区盘点 Section count: the manager enters what the counter found, one section at a time
# (owner, 2026-11-20). See count_sections.py.

def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@bp.get('/api/counting/sections')
@role_required('manager')
def sections():
    return _answer(lambda con: count_sections.sections_overview(con, request.jwt_payload, request.args.get('store_code')))


@bp.get('/api/counting/sections/proposal')
@role_required('manager')
def sections_proposal():
    return _answer(lambda con: count_sections.sections_proposal(con, request.jwt_payload, request.args.get('store_code')))


@bp.put('/api/counting/sections')
@role_required('manager')
def save_sections():
    body = _body()
    return _answer(lambda con: count_sections.save_layout(con, request.jwt_payload, body.get('store_code'), body))


@bp.get('/api/counting/counters')
@role_required('manager')
def counters():
    return _answer(lambda con: count_sections.counters(con, request.jwt_payload, request.args.get('store_code')))


@bp.get('/api/counting/section-counts')
@role_required('manager')
def section_count_history():
    return _answer(lambda con: count_sections.history(con, request.jwt_payload, request.args.get('store_code')))


@bp.post('/api/counting/section-counts')
@role_required('manager')
def start_section_count():
    body = _body()
    return _answer(lambda con: count_sections.start_count(con, request.jwt_payload, body.get('store_code'), body))


@bp.get('/api/counting/section-counts/<int:section_count_id>')
@role_required('manager')
def section_count(section_count_id):
    return _answer(lambda con: count_sections.count_view(con, request.jwt_payload, section_count_id))


@bp.get('/api/counting/section-counts/<int:section_count_id>/summary')
@role_required('manager')
def section_count_summary(section_count_id):
    return _answer(lambda con: count_sections.summary(con, request.jwt_payload, section_count_id))


@bp.post('/api/counting/section-counts/<int:section_count_id>/finish')
@role_required('manager')
def finish_section_count(section_count_id):
    return _answer(lambda con: count_sections.finish_count(con, request.jwt_payload, section_count_id))


# 待盘点 Count next time (owner, 2026-11-22): a manager marks a product at a place to count next time; the
# next count there shows it first and closes it. See count_marks.py.

@bp.get('/api/counting/marks')
@role_required('staff')
def marks():
    args = request.args
    return _answer(lambda con: count_marks.list_marks(con, request.jwt_payload, args.get('store_code'),
                                                      args.get('product_id')))


@bp.post('/api/counting/marks')
@role_required('manager')
def add_mark():
    body = _body()
    return _answer(lambda con: count_marks.mark(con, request.jwt_payload, body))


@bp.post('/api/counting/marks/<int:mark_id>/remove')
@role_required('manager')
def remove_mark(mark_id):
    return _answer(lambda con: count_marks.remove(con, request.jwt_payload, mark_id))
