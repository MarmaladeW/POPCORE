"""可疑人员 Watch list routes (owner, 2026-11-19). See watch_list.py.

Everyone signed in with a role reads the list and its photos, the store computer's viewer account included; managers
and admins (店长 and 副店长 are managers) add, edit, resolve, reopen and delete.
"""
from flask import Blueprint, jsonify, request, send_file

from auth import ROLE_CLAIM, ROLE_HIERARCHY, role_required
from db import get_db
import watch_list
from watch_list import WatchListError


bp = Blueprint('watch_list', __name__)


def _answer(work, status=200):
    con = get_db()
    try:
        return jsonify(work(con)), status
    except WatchListError as exc:
        if con.in_transaction:
            con.rollback()
        return jsonify({'error': str(exc), 'code': exc.code}), exc.status


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@bp.get('/api/watch-list')
@role_required('viewer')
def page():
    manager = ROLE_HIERARCHY.get(request.jwt_payload.get(ROLE_CLAIM), 0) >= ROLE_HIERARCHY['manager']
    return _answer(lambda con: watch_list.list_page(con, manager))


@bp.get('/api/watch-list/photos/<int:photo_id>')
@role_required('viewer')
def photo(photo_id):
    try:
        path, mime_type = watch_list.photo_file(get_db(), photo_id)
    except WatchListError as exc:
        return jsonify({'error': str(exc), 'code': exc.code}), exc.status
    response = send_file(path, mimetype=mime_type, conditional=False)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@bp.post('/api/watch-list')
@role_required('manager')
def create():
    return _answer(lambda con: watch_list.create(con, request.jwt_payload, request.form,
                                                 request.files.getlist('photos')), 201)


@bp.patch('/api/watch-list/<int:entry_id>')
@role_required('manager')
def update(entry_id):
    return _answer(lambda con: watch_list.update(con, request.jwt_payload, entry_id, _body()))


@bp.post('/api/watch-list/<int:entry_id>/photos')
@role_required('manager')
def add_photos(entry_id):
    return _answer(lambda con: watch_list.add_photos(con, request.jwt_payload, entry_id,
                                                     request.files.getlist('photos')), 201)


@bp.delete('/api/watch-list/<int:entry_id>/photos/<int:photo_id>')
@role_required('manager')
def remove_photo(entry_id, photo_id):
    return _answer(lambda con: watch_list.remove_photo(con, request.jwt_payload, entry_id, photo_id))


@bp.post('/api/watch-list/<int:entry_id>/resolve')
@role_required('manager')
def resolve(entry_id):
    return _answer(lambda con: watch_list.resolve(con, request.jwt_payload, entry_id, _body()))


@bp.post('/api/watch-list/<int:entry_id>/reopen')
@role_required('manager')
def reopen(entry_id):
    return _answer(lambda con: watch_list.reopen(con, request.jwt_payload, entry_id))


@bp.delete('/api/watch-list/<int:entry_id>')
@role_required('manager')
def delete(entry_id):
    return _answer(lambda con: watch_list.delete(con, entry_id))
