"""Disposable checkout adapter. No writes to the POPCORE operational database."""
import io
import json
import os
import re
import sqlite3
import stat
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from flask import Blueprint, jsonify, request, send_file

from auth import role_required
from checkout_access import checkout_access
from db import get_db
from inventory_commands import InventoryError, require_inventory_access
from payment_evidence import prepare_image
from validation import read_date, read_int

bp = Blueprint('clover_sandbox_checkout', __name__, url_prefix='/api/clover-sandbox/checkouts')
IDENTIFIER = re.compile(r'^[A-Za-z0-9]{13}$')
TENDERS = {'cash': 'cash', 'card': 'card', 'creditcard': 'card', 'debitcard': 'card',
           'etransfer': 'e_transfer', 'wechatpay': 'wechat', 'alipay': 'alipay'}


def enabled():
    return bool(os.environ.get('CLOVER_SANDBOX_CHECKOUT_DIR') and
                os.environ.get('CLOVER_SANDBOX_PROBE_PASSWORD') and
                os.environ.get('CLOVER_SANDBOX_STORE_ID', '').isdigit() and
                int(os.environ['CLOVER_SANDBOX_STORE_ID']) > 0)


@bp.errorhandler(InventoryError)
@bp.errorhandler(ValueError)
@bp.errorhandler(PermissionError)
def error(exc):
    if isinstance(exc, InventoryError):
        return jsonify(error=str(exc), code=exc.code), exc.status
    if isinstance(exc, PermissionError):
        return jsonify(error='Sandbox checkout access denied'), 403
    return jsonify(error=str(exc)), 400


@bp.before_request
def configured():
    if not enabled():
        return jsonify(error='Sandbox checkout is not enabled'), 404


@bp.after_request
def private(response):
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@contextmanager
def database():
    root = Path(os.environ['CLOVER_SANDBOX_CHECKOUT_DIR'])
    if not root.is_absolute():
        raise ValueError('Sandbox checkout storage must be an absolute private path')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == 'posix' and stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise ValueError('Sandbox checkout directory must be private (mode 0700)')
    path = root / 'clover-checkout-sandbox.sqlite3'
    if path.is_symlink():
        raise ValueError('Sandbox checkout database must not be a symlink')
    con = sqlite3.connect(path, timeout=10)
    os.chmod(path, 0o600)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA foreign_keys=ON')
        con.executescript('''
            CREATE TABLE IF NOT EXISTS feed (
                id INTEGER PRIMARY KEY CHECK(id=1), store_id INTEGER NOT NULL,
                next_poll REAL NOT NULL DEFAULT 0, fetched_at REAL NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT 'Waiting for Clover');
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY, source_id TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL, seen_at REAL NOT NULL,
                cashier_sub TEXT NOT NULL DEFAULT '', cashier_name TEXT NOT NULL DEFAULT 'Unassigned');
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
                source_id TEXT NOT NULL UNIQUE, tender TEXT NOT NULL, amount INTEGER NOT NULL,
                result TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY, payment_id INTEGER NOT NULL REFERENCES payments(id),
                content_hash TEXT NOT NULL, mime_type TEXT NOT NULL, content BLOB NOT NULL,
                uploader_sub TEXT NOT NULL, UNIQUE(payment_id,content_hash));
        ''')
        store_id = int(os.environ['CLOVER_SANDBOX_STORE_ID'])
        con.execute('INSERT OR IGNORE INTO feed(id,store_id) VALUES (1,?)', (store_id,))
        con.commit()
        if con.execute('SELECT store_id FROM feed').fetchone()[0] != store_id:
            raise ValueError('Sandbox data belongs to another store. Use a new private directory.')
        yield con
    finally:
        con.close()


def scope(con):
    access = checkout_access(get_db(), request.jwt_payload)
    store_id = int(os.environ['CLOVER_SANDBOX_STORE_ID'])
    live = [s for s in access['live_stores'] if s['id'] == store_id]
    own = con.execute('SELECT 1 FROM orders WHERE cashier_sub=? LIMIT 1',
                      (request.jwt_payload['sub'],)).fetchone()
    history = live
    if own and access['role'] == 'staff':
        history = [dict(s) for s in get_db().execute(
            'SELECT id,code,name FROM stores WHERE id=? AND is_active=1', (store_id,))]
    return {**access, 'live_stores': live, 'history_stores': history}


def cents(value):
    if type(value) is not int or not 0 <= value <= 9_007_199_254_740_991:
        raise ValueError('Clover returned an invalid amount')
    return value


def tender(value):
    label = str(value or 'Unknown tender')[:80]
    return TENDERS.get(re.sub(r'[\s_-]', '', label.casefold()), label)


def validate_order(value):
    if not isinstance(value, dict) or not IDENTIFIER.fullmatch(str(value.get('id', ''))):
        raise ValueError('Clover returned an invalid order')
    if value.get('currency') != 'CAD':
        raise ValueError('Sandbox checkout requires CAD orders')
    cents(value.get('total'))
    created = value.get('createdTime')
    if type(created) is not int or not 0 < created < 32_503_680_000_000:
        raise ValueError('Clover returned an invalid order date')
    if not isinstance(value.get('items'), list) or not isinstance(value.get('payments'), list):
        raise ValueError('Clover returned an incomplete order')
    for item in value['items']:
        if not isinstance(item, dict):
            raise ValueError('Clover returned an invalid item')
    ids = set()
    for payment in value['payments']:
        if not isinstance(payment, dict) or not IDENTIFIER.fullmatch(str(payment.get('id', ''))):
            raise ValueError('Clover returned an invalid payment')
        if payment['id'] in ids:
            raise ValueError('Clover returned a duplicate payment')
        ids.add(payment['id'])
        cents(payment.get('amount'))
    return value


def sync(con):
    # ponytail: one bounded latest-20 rehearsal feed; production needs durable webhook ingestion.
    now = time.time()
    with con:
        acquired = con.execute('UPDATE feed SET next_poll=? WHERE id=1 AND next_poll<=?',
                               (now + 15, now)).rowcount
    if not acquired:
        return
    try:
        response = requests.get('http://127.0.0.1:5055/clover-sandbox/orders',
                                auth=('popcore', os.environ['CLOVER_SANDBOX_PROBE_PASSWORD']),
                                timeout=(1, 10), allow_redirects=False)
        if response.status_code != 200:
            raise ValueError('Sandbox probe unavailable')
        payload = response.json()
        if not isinstance(payload, dict) or payload.get('environment') != 'sandbox':
            raise ValueError('Sandbox source required')
        rows = payload.get('orders')
        if not isinstance(rows, list) or len(rows) > 20:
            raise ValueError('Unexpected sandbox feed')
        rows = [validate_order(row) for row in rows if not (
            isinstance(row, dict) and row.get('paymentState') == 'OPEN'
            and row.get('total') is None and row.get('items') == [] and row.get('payments') == [])]
        if len({row['id'] for row in rows}) != len(rows):
            raise ValueError('Duplicate sandbox order')
        fetched = time.time()
        with con:
            for row in rows:
                con.execute('''INSERT INTO orders(source_id,payload,seen_at) VALUES (?,?,?)
                    ON CONFLICT(source_id) DO UPDATE SET payload=excluded.payload,seen_at=excluded.seen_at''',
                    (row['id'], json.dumps(row), fetched))
                order_id = con.execute('SELECT id FROM orders WHERE source_id=?', (row['id'],)).fetchone()[0]
                con.execute('UPDATE payments SET active=0 WHERE order_id=?', (order_id,))
                for payment in row['payments']:
                    prior = con.execute('SELECT order_id FROM payments WHERE source_id=?', (payment['id'],)).fetchone()
                    if prior and prior['order_id'] != order_id:
                        raise ValueError('Payment belongs to a different sandbox order')
                    con.execute('''INSERT INTO payments(order_id,source_id,tender,amount,result) VALUES (?,?,?,?,?)
                        ON CONFLICT(source_id) DO UPDATE SET tender=excluded.tender,amount=excluded.amount,
                        result=excluded.result,active=1''', (order_id, payment['id'], tender(payment.get('tenderLabel')),
                        payment['amount'], str(payment.get('result', 'UNKNOWN'))))
            con.execute("UPDATE feed SET fetched_at=?,next_poll=?,error='' WHERE id=1", (fetched, fetched + .5))
    except (requests.RequestException, ValueError):
        with con:
            con.execute('UPDATE feed SET next_poll=?,error=? WHERE id=1',
                        (time.time() + 2, 'Clover sync unavailable. Showing the last received snapshot.'))


def detail(con, row, access):
    source = json.loads(row['payload'])
    live = bool(access['live_stores'])
    owner = row['cashier_sub'] == request.jwt_payload['sub']
    feed = con.execute('SELECT * FROM feed').fetchone()
    fresh = not feed['error'] and time.time() - row['seen_at'] <= 5
    payments = con.execute('SELECT * FROM payments WHERE order_id=? ORDER BY id', (row['id'],)).fetchall()
    received = sum(p['amount'] for p in payments if p['active'] and p['result'] == 'SUCCESS')
    total = source['total']
    paid = source.get('paymentState') == 'PAID' and received == total and total > 0
    status = 'completed' if paid else 'open'
    if not (live and (access['role'] != 'staff' or status == 'open') or owner and access['role'] == 'staff'):
        raise PermissionError('Checkout history access denied')
    has_discount = bool(source.get('discounts')) or any(
        item.get('discountAmount') or item.get('orderLevelDiscountAmount') for item in source['items'])
    prices = [item.get('priceWithModifiersAndItemAndOrderDiscounts')
              if item.get('priceWithModifiersAndItemAndOrderDiscounts') is not None else item.get('price')
              for item in source['items']]
    simple = bool(prices) and all(type(p) is int and p >= 0 for p in prices) and all(
        item.get('unitQty') in (None, 0, 1000) and not item.get('refunded') for item in source['items'])
    subtotal = sum(prices) if simple else 0
    quote_available = simple and 0 < subtotal <= total and not has_discount
    totals_known = simple and subtotal <= total
    writable = live and (owner or access['role'] in ('admin', 'manager'))
    return dict(id=row['id'], source='clover-sandbox', store_id=feed['store_id'],
        reference=source['id'], register_name='Clover sandbox', business_date=datetime.fromtimestamp(
            source['createdTime'] / 1000, ZoneInfo('America/Toronto')).date().isoformat(),
        status=status, version=1, sale_id=None, cashier_name=row['cashier_name'], cashier_sub=row['cashier_sub'],
        can_manage=False, can_refund=False, can_process=live and owner and status == 'open' and fresh,
        can_claim=live and not row['cashier_sub'] and status == 'open' and fresh,
        received_cents=received, remaining_cents=max(0, total - received), refunded_cents=0,
        refund_due_cents=0, abandoned_reason=None, refunds=[], quote_available=quote_available,
        totals_known=totals_known, source_fresh=fresh, source_seen_at=row['seen_at'], has_discount=has_discount,
        attempts=[dict(id=p['id'], tender=p['tender'], amount_cents=p['amount'],
            status=('completed' if p['result'] == 'SUCCESS' else 'failed') if p['active'] else 'cancelled',
            can_upload=writable and p['active'] and p['result'] == 'SUCCESS',
            photos=[dict(r) for r in con.execute('SELECT id FROM evidence WHERE payment_id=? ORDER BY id', (p['id'],))])
            for p in payments],
        order=dict(subtotal_cents=subtotal if totals_known else 0, source_tax_cents=total-subtotal if totals_known else 0,
            gross_cents=total, reduction_cents=0, collected_cents=total,
            lines=[dict(product_name_snapshot=str(item.get('name') or 'Clover item')[:240],
                quantity=(item['unitQty']/1000 if type(item.get('unitQty')) is int and item['unitQty'] > 0 else 1),
                unit=str(item.get('unitName') or 'each')) for item in source['items']]))


def order_row(con, order_id):
    row = con.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
    if row is None:
        raise ValueError('Sandbox order not found')
    return row


@bp.get('/access')
@role_required('staff')
def access():
    with database() as con:
        return jsonify(scope(con))


@bp.get('')
@role_required('staff')
def queue():
    with database() as con:
        access = scope(con)
        history = request.args.get('view', 'live') == 'history'
        if request.args.get('view', 'live') not in ('live', 'history'):
            raise ValueError('view must be live or history')
        store_id = read_int(request.args.get('store_id'), 'store_id', minimum=1)
        if store_id not in {s['id'] for s in access['history_stores' if history else 'live_stores']}:
            raise PermissionError('Store access denied')
        sync(con)
        where, values = ['1=1'], []
        if history and access['role'] == 'staff':
            where.append('cashier_sub=?'); values.append(request.jwt_payload['sub'])
        if request.args.get('before_id'):
            where.append('id<?'); values.append(read_int(request.args['before_id'], 'before_id', minimum=1))
        date = read_date(request.args['business_date'], 'business_date') if request.args.get('business_date') else None
        orders = []
        for row in con.execute('SELECT * FROM orders WHERE ' + ' AND '.join(where) + ' ORDER BY id DESC', values):
            try:
                value = detail(con, row, access)
            except PermissionError:
                continue
            if (history or value['status'] == 'open') and (not date or value['business_date'] == date):
                orders.append(value)
                if len(orders) > 100:
                    break
        feed = con.execute('SELECT * FROM feed').fetchone()
        connected = not feed['error'] and time.time() - feed['fetched_at'] <= 5
        return jsonify(orders=orders[:100], next_before_id=orders[99]['id'] if len(orders)>100 else None,
                       clover=dict(connected=connected, sandbox=True, fetched_at=feed['fetched_at'],
                                   message=feed['error'] or ('' if connected else 'Waiting for a fresh Clover snapshot')))


@bp.get('/<int:order_id>')
@role_required('staff')
def get_order(order_id):
    with database() as con:
        return jsonify(detail(con, order_row(con, order_id), scope(con)))


@bp.post('/<int:order_id>/claim')
@role_required('staff')
def claim(order_id):
    with database() as con:
        access = scope(con)
        value = detail(con, order_row(con, order_id), access)
        actor = request.jwt_payload
        if not access['live_stores']:
            raise PermissionError('Checkout shift required')
        require_inventory_access(get_db(), actor, (value['store_id'],), 'staff')
        if value['cashier_sub'] == actor['sub']:
            return jsonify(value)
        if not value['can_claim']:
            return jsonify(error='This order is no longer available to claim'), 409
        employee = get_db().execute('SELECT name FROM employees WHERE auth0_id=?', (actor['sub'],)).fetchone()
        name = employee['name'] if employee else 'Admin'
        with con:
            changed = con.execute("UPDATE orders SET cashier_sub=?,cashier_name=? WHERE id=? AND cashier_sub=''",
                                  (actor['sub'], name, order_id)).rowcount
        if not changed:
            return jsonify(error='Another cashier has picked up this order'), 409
        return jsonify(detail(con, order_row(con, order_id), access))


@bp.post('/<int:order_id>/attempts/<int:payment_id>/evidence')
@role_required('staff')
def upload(order_id, payment_id):
    with database() as con:
        value = detail(con, order_row(con, order_id), scope(con))
        payment = next((p for p in value['attempts'] if p['id'] == payment_id), None)
        if not payment or not payment['can_upload']:
            raise PermissionError('Payment evidence access denied')
        require_inventory_access(get_db(), request.jwt_payload, (value['store_id'],), 'staff')
        image = prepare_image(request.files.get('image'))
        with con:
            con.execute('''INSERT OR IGNORE INTO evidence(payment_id,content_hash,mime_type,content,uploader_sub)
                VALUES (?,?,?,?,?)''', (payment_id,image['content_hash'],image['mime_type'],image['content'],request.jwt_payload['sub']))
        photo = con.execute('SELECT id FROM evidence WHERE payment_id=? AND content_hash=?',
                            (payment_id,image['content_hash'])).fetchone()
        return jsonify(id=photo['id'], attempt_id=payment_id), 201


@bp.get('/<int:order_id>/evidence/<int:photo_id>')
@role_required('staff')
def content(order_id, photo_id):
    with database() as con:
        value = detail(con, order_row(con, order_id), scope(con))
        if value['cashier_sub'] != request.jwt_payload['sub'] and scope(con)['role'] == 'staff':
            raise PermissionError('Payment evidence access denied')
        photo = con.execute('''SELECT e.* FROM evidence e JOIN payments p ON p.id=e.payment_id
            WHERE e.id=? AND p.order_id=?''', (photo_id, order_id)).fetchone()
        if photo is None:
            raise ValueError('Photo does not belong to this sandbox order')
        return send_file(io.BytesIO(photo['content']), mimetype=photo['mime_type'])
