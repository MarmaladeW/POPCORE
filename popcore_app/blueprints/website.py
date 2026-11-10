"""Website stock and price sync routes (popcore.ca); see website_sync.py and docs/website-sync.md.

Managers see the sync, what the next push would change and the web orders, settle orders POPCORE
could not fill, and OK the price changes that wait for them. Only admins reread the catalogue or
turn the sync on and off.
"""
from flask import Blueprint, jsonify, request

import website_sync as sync
from auth import role_required
from db import get_db

bp = Blueprint('website', __name__)


def _config():
    try:
        return sync.config(), None
    except ValueError as exc:
        return None, (jsonify(error=str(exc), code='settings_invalid'), 400)


def _not_configured():
    return jsonify(error='No website key is set on the server (WEBSITE_URL, WEBSITE_KEY, WEBSITE_SECRET).',
                   code='not_configured'), 400


@bp.get('/api/website/status')
@role_required('manager')
def website_status():
    con = get_db()
    return jsonify(sync.status(con))


@bp.get('/api/website/preview')
@role_required('manager')
def website_preview():
    cfg, error = _config()
    if error:
        return error
    if cfg is None:
        return _not_configured()
    con = get_db()
    if sync.state(con, 'refreshed_at') is None:
        return jsonify(error='The website’s products have not been read yet.', code='not_refreshed'), 409
    return jsonify(sync.preview(con, cfg))


@bp.post('/api/website/refresh')
@role_required('admin')
def website_refresh():
    """Ask the background job to reread the website's catalogue (it takes a minute or two)."""
    cfg, error = _config()
    if error:
        return error
    if cfg is None:
        return _not_configured()
    con = get_db()
    sync.set_state(con, 'refresh_requested', '1')
    con.commit()
    return jsonify(sync.status(con)), 202


@bp.post('/api/website/switch')
@role_required('admin')
def website_switch():
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('on'), bool):
        return jsonify(error='on must be true or false', code='invalid_input'), 400
    cfg, error = _config()
    if error:
        return error
    if cfg is None and data['on']:
        return _not_configured()
    con = get_db()
    try:
        con.execute('BEGIN IMMEDIATE')
        sync.switch(con, data['on'], request.jwt_payload['sub'])
        con.commit()
    except sync.WebsiteError as exc:
        con.rollback()
        return jsonify(error=str(exc), code=exc.code), 409
    return jsonify(sync.status(con))


@bp.post('/api/website/prices/<int:wc_id>/ok')
@role_required('manager')
def website_price_ok(wc_id):
    """A manager's OK for one website product's held price changes, as {"key": the ok_key the preview gave}."""
    data = request.get_json(silent=True) or {}
    key = data.get('key')
    if not isinstance(key, str) or not key:
        return jsonify(error='key must be the ok_key of the prices shown', code='invalid_input'), 400
    cfg, error = _config()
    if error:
        return error
    if cfg is None:
        return _not_configured()
    con = get_db()
    if sync.state(con, 'refreshed_at') is None:
        return jsonify(error='The website’s products have not been read yet.', code='not_refreshed'), 409
    try:
        sync.approve_prices(con, cfg, wc_id, key, request.jwt_payload['sub'])
        con.commit()
    except sync.WebsiteError as exc:
        con.rollback()
        return jsonify(error=str(exc), code=exc.code), 409
    return jsonify(ok=True)


@bp.get('/api/website/orders')
@role_required('manager')
def website_orders():
    con = get_db()
    return jsonify(orders=sync.orders(con))


@bp.post('/api/website/orders/<int:wc_order_id>/retry')
@role_required('manager')
def website_order_retry(wc_order_id):
    cfg, error = _config()
    if error:
        return error
    if cfg is None:
        return _not_configured()
    con = get_db()
    if sync.state_of(con, wc_order_id) not in ('needs_review', 'cancel_failed'):
        return jsonify(error='Only an order waiting for review can be tried again', code='not_waiting'), 409
    return jsonify(state=sync.retry_order(con, cfg, wc_order_id))


@bp.post('/api/website/orders/<int:wc_order_id>/dismiss')
@role_required('manager')
def website_order_dismiss(wc_order_id):
    data = request.get_json(silent=True) or {}
    con = get_db()
    try:
        con.execute('BEGIN IMMEDIATE')
        sync.dismiss_order(con, wc_order_id, request.jwt_payload['sub'], data.get('reason'))
        con.commit()
    except ValueError as exc:
        con.rollback()
        return jsonify(error=str(exc), code='invalid_input'), 400
    return jsonify(state=sync.state_of(con, wc_order_id))
