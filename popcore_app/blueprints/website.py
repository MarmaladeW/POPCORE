"""Website stock and price sync routes (popcore.ca); see website_sync.py and docs/website-sync.md.

Managers see the sync, what the next push would change and the web orders, settle orders POPCORE
could not fill, and OK the price changes that wait for them. Only admins reread the catalogue or
turn the sync on and off.

网站订单 Web orders (website_board.py): anyone with store operations access for a store, staff or a 查看者 Viewer
counter account (owner, 2026-11-19), sees its orders and the name each delivery goes to (owner, 2026-11-25), finds a
pickup order by its code and marks pickup orders Ready and Handed over. Viewers can do nothing else anywhere: every other write needs staff or above.
"""
import os

from flask import g, Blueprint, jsonify, request

import push_notifications as push
import website_board as board
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


# ── 网站订单 Web orders board (website_board.py) ────────────────────────────

def _board_error(exc):
    if isinstance(exc, board.BoardError):
        return jsonify(error=str(exc), code=exc.code), exc.status
    # The website could not be reached or refused: nothing changed in POPCORE.
    return jsonify(error=f'The website did not answer as expected: {exc}', code=exc.code), 502


def _private(response):
    """The board (each delivery's name, owner 2026-11-25) and the code box's answer (the customer's first name) carry
    names read live from the website: no cache may keep them."""
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.get('/api/website/board')
@role_required('viewer')
def web_orders_board():
    cfg, error = _config()
    if error:
        return error
    con = get_db()
    # Without a website key the board is empty but still says which stores it would show.
    delivery = os.environ.get('WEBSITE_DELIVERY_STORE', 'DT').strip().upper() or 'DT'
    result = board.board(con, cfg or {'delivery_store': delivery}, request.jwt_payload['sub'],
                         request.args.get('store') or None)
    return _private(jsonify(configured=cfg is not None, enabled=sync.enabled(con), last_run=sync.state(con, 'last_run'),
                            **result))


@bp.post('/api/website/board/code')
@role_required('viewer')
def web_orders_code():
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('code'), str) or not data['code'].strip():
        return jsonify(error='Type the pickup code', code='invalid_input'), 400
    cfg, error = _config()
    if error:
        return error
    if cfg is None:
        return _not_configured()
    store = data.get('store') if isinstance(data.get('store'), str) else None
    try:
        return _private(jsonify(board.find_code(get_db(), cfg, data['code'], request.jwt_payload['sub'], store)))
    except (board.BoardError, sync.WebsiteError) as exc:
        return _board_error(exc)


@bp.post('/api/website/board/orders/<int:wc_order_id>/ready', defaults={'action': 'ready'})
@bp.post('/api/website/board/orders/<int:wc_order_id>/handed-over', defaults={'action': 'handed_over'})
@role_required('viewer')
def web_orders_step(wc_order_id, action):
    data = request.get_json(silent=True) or {}
    cfg, error = _config()
    if error:
        return error
    if cfg is None:
        return _not_configured()
    try:
        # On the store computer the note names the person who typed their PIN (pins.py), not the shared account.
        result, noted = board.step(get_db(), cfg, wc_order_id, action, request.jwt_payload['sub'],
                                   data.get('code') if isinstance(data.get('code'), str) else None,
                                   by=(g.get('acting_person') or {}).get('sub'))
    except (board.BoardError, sync.WebsiteError) as exc:
        return _board_error(exc)
    return jsonify(result=result, noted=noted)


# ── Notifications for new web orders (push_notifications.py) ───────────────

@bp.get('/api/website/notifications')
@role_required('viewer')
def web_orders_notifications():
    """POPCORE's public push key, and how many devices this person has turned on."""
    con = get_db()
    return jsonify(key=push.public_key(), devices=push.devices(con, request.jwt_payload['sub']))


@bp.post('/api/website/notifications')
@role_required('viewer')
def web_orders_notify_me():
    """Turn notifications on for this device, as {"subscription": the browser's PushSubscription JSON}."""
    data = request.get_json(silent=True) or {}
    con = get_db()
    try:
        push.subscribe(con, request.jwt_payload['sub'], data.get('subscription'))
    except push.PushError as exc:
        return jsonify(error=str(exc), code='invalid_input'), 400
    return jsonify(devices=push.devices(con, request.jwt_payload['sub']))


@bp.post('/api/website/notifications/off')
@role_required('viewer')
def web_orders_notify_off():
    data = request.get_json(silent=True) or {}
    con = get_db()
    if isinstance(data.get('endpoint'), str):
        push.unsubscribe(con, request.jwt_payload['sub'], data['endpoint'])
    return jsonify(devices=push.devices(con, request.jwt_payload['sub']))


@bp.post('/api/website/notifications/test')
@role_required('viewer')
def web_orders_notify_test():
    """A test notification to every device of this person."""
    con = get_db()
    rows = con.execute('SELECT * FROM push_subscriptions WHERE auth0_sub=?', (request.jwt_payload['sub'],)).fetchall()
    sent, failed = push.send(con, rows, {'title': '网站订单 Web orders', 'body': '通知已开启 Notifications are on.',
                                         'url': '/web-orders', 'tag': 'web-order-test'})
    return jsonify(sent=sent, failed=failed, devices=push.devices(con, request.jwt_payload['sub']))
