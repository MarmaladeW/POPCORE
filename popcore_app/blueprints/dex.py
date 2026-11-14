"""图鉴 Dex: the book in POPCORE (owner, 2026-11-12: "The Dex itself lives on POPCORE"). Any signed-in role
reads it; managers and admins write it. The rules are in dex_book.py and docs/dex-book.md."""
from flask import Blueprint, jsonify, request

import dex_book
import dex_load
from auth import login_required, role_required
from db import get_db

bp = Blueprint('dex', __name__)


def _run(work, status=200):
    con = get_db()
    try:
        return jsonify(work(con)), status
    except dex_book.DexError as exc:
        if con.in_transaction:
            con.rollback()
        return jsonify(error=str(exc), code=exc.code), exc.status


def _number(name):
    value = request.args.get(name, '')
    try:
        return int(value)
    except ValueError:
        raise dex_book.DexError(f'{name} must be a number.', 'invalid_input')


@bp.get('/api/dex/entries')
@login_required
def dex_list():
    def work(con):
        if 'parent' in request.args:
            return dex_book.children(con, _number('parent'))
        if 'named_ip' in request.args:  # contract 1.3: products and designs elsewhere that name this IP
            return dex_book.named(con, _number('named_ip'))
        if request.args.get('kind') == 'ip':  # every live IP, for the IP picker
            return dex_book.ips(con)
        if 'q' in request.args:
            kinds = [kind for kind in request.args.get('kinds', '').split(',') if kind]
            return dex_book.search(con, request.args.get('q'), kinds)
        return dex_book.top(con, request.args.get('kind'))
    return _run(work)


@bp.get('/api/dex/entries/<int:entry_id>')
@login_required
def dex_detail(entry_id):
    return _run(lambda con: dex_book.detail(con, entry_id))


@bp.get('/api/dex/entries/<int:entry_id>/removal')
@login_required
def dex_removal(entry_id):
    """What a delete would remove, for the confirm. Nothing changes."""
    def work(con):
        plan = dex_book.removal(con, entry_id)
        return {key: plan[key] for key in ('counts', 'linked', 'used_by')}
    return _run(work)


@bp.get('/api/dex/entries/<int:entry_id>/prices')
@login_required
def dex_prices(entry_id):
    """A design's price trend: the prices managers set for its POPCORE item (design_prices), and the website's
    market prices in its extras. Any signed-in role reads it; nothing changes."""
    return _run(lambda con: dex_book.price_history(con, entry_id))


@bp.get('/api/dex/unlinked')
@login_required
def dex_unlinked():
    """POPCORE items not in the book yet (no live book entry links them); never sealed sets. Each carries
    `suggestion` ({id, path, score}) or null: never linked automatically."""
    def work(con):
        page = _number('page') if request.args.get('page') else 1
        per_page = _number('per_page') if request.args.get('per_page') else 50
        found = dex_book.unlinked(con, request.args.get('q', ''), page, per_page)
        dex_load.suggestions_from_db(con, found['items'])  # the closest book entry by name, for a person to link
        dex_book.unlinked_names(con, found['items'])  # their full names and IP tags, beside
        return found
    return _run(work)


@bp.post('/api/dex/entries')
@role_required('manager')
def dex_create():
    return _run(lambda con: dex_book.create(con, request.get_json(silent=True), request.jwt_payload), 201)


@bp.patch('/api/dex/entries/<int:entry_id>')
@role_required('manager')
def dex_update(entry_id):
    return _run(lambda con: dex_book.update(con, entry_id, request.get_json(silent=True), request.jwt_payload))


@bp.delete('/api/dex/entries/<int:entry_id>')
@role_required('manager')
def dex_delete(entry_id):
    return _run(lambda con: dex_book.delete(con, entry_id, request.jwt_payload))


@bp.post('/api/dex/reorder')
@role_required('manager')
def dex_reorder():
    return _run(lambda con: dex_book.reorder(con, request.get_json(silent=True), request.jwt_payload))
