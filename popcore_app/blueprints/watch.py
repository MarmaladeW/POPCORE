"""blueprints/watch.py — 异常提醒 Alerts from the watcher (watcher.py): managers see their stores' alerts, admins all."""
from flask import Blueprint, jsonify, request

import watcher
from auth import role_required
from db import get_db

bp = Blueprint('watch', __name__)


@bp.get('/api/watch')
@role_required('manager')
def list_alerts():
    return jsonify(watcher.view(get_db(), request.jwt_payload))


@bp.post('/api/watch/<int:alert_id>/check')
@role_required('manager')
def check_alert(alert_id):
    try:
        watcher.check(get_db(), request.jwt_payload, alert_id)
    except LookupError:
        return jsonify(error='Alert not found', code='not_found'), 404
    return jsonify(ok=True)


@bp.post('/api/watch/run')
@role_required('admin')
def run_now():
    """立即检查 Check now: the rules at once; Claude's review within ten minutes (the scheduler runs it)."""
    con = get_db()
    if watcher.settings(con)['watcher_enabled'] != '1':
        return jsonify(error='Turn the watcher on in Settings first', code='watcher_off'), 409
    found = watcher.run_rules(con)
    if watcher.settings(con)['watcher_claude'] == '1' and watcher.has_key():
        watcher.ask_for_review(con)
    return jsonify(found=found, status=watcher.status(con))
