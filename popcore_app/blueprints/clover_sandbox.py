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
from checkout_operations import read_order_note
from db import get_db
from inventory_commands import InventoryConflict, InventoryError
from payment_evidence import prepare_image
from validation import read_date, read_int

bp = Blueprint('clover_sandbox_checkout', __name__, url_prefix='/api/clover-sandbox/checkouts')
IDENTIFIER = re.compile(r'^[A-Za-z0-9]{13}$')
TENDERS = {'cash': 'cash', 'card': 'card', 'creditcard': 'card', 'debitcard': 'card',
           'etransfer': 'e_transfer', 'wechatpay': 'wechat', 'alipay': 'alipay'}
# Card is taken on Clover; every other tender is received and recorded here while the
# Clover order stays open. The Clover app itself never writes payments or tenders.
MANUAL_TENDERS = ('cash', 'e_transfer', 'wechat', 'alipay')


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
            CREATE TABLE IF NOT EXISTS hidden_orders (
                order_id INTEGER PRIMARY KEY REFERENCES orders(id));
            CREATE TABLE IF NOT EXISTS order_notes (
                order_id INTEGER PRIMARY KEY REFERENCES orders(id), note TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS order_state (
                order_id INTEGER PRIMARY KEY REFERENCES orders(id), version INTEGER NOT NULL DEFAULT 1,
                payable INTEGER, settled_at REAL, settled_total INTEGER, settled_clover INTEGER);
            CREATE TABLE IF NOT EXISTS manual_payments (
                id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
                tender TEXT NOT NULL CHECK(tender IN ('cash','e_transfer','wechat','alipay')),
                amount INTEGER NOT NULL CHECK(amount>0),
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','cancelled')),
                recorded_by TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS manual_evidence (
                id INTEGER PRIMARY KEY, payment_id INTEGER NOT NULL REFERENCES manual_payments(id),
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
    source = value.get('source', 'cloud')
    if source not in ('cloud', 'device'):
        raise ValueError('Unknown Clover order source')
    if value.get('total') is None:
        if source != 'device':
            raise ValueError('Clover returned an invalid amount')
    else:
        cents(value['total'])
    if source == 'device' and (value.get('paymentState') != 'OPEN' or value.get('payments') != []):
        raise ValueError('Device snapshots cannot confirm payments')
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
    retry_delay = 2
    try:
        response = requests.get('http://127.0.0.1:5055/clover-sandbox/orders',
                                auth=('popcore', os.environ['CLOVER_SANDBOX_PROBE_PASSWORD']),
                                timeout=(1, 10), allow_redirects=False)
        if response.status_code != 200:
            try:
                retry_delay = max(retry_delay, int(response.headers.get('Retry-After', '2')))
            except ValueError:
                pass
            raise ValueError('Sandbox probe unavailable')
        payload = response.json()
        if not isinstance(payload, dict) or payload.get('environment') != 'sandbox':
            raise ValueError('Sandbox source required')
        cloud_unavailable = payload.get('cloud_unavailable', False)
        if type(cloud_unavailable) is not bool:
            raise ValueError('Unexpected sandbox cloud state')
        rows = payload.get('orders')
        if not isinstance(rows, list) or len(rows) > 20:
            raise ValueError('Unexpected sandbox feed')
        rows = [validate_order(row) for row in rows if not (
            isinstance(row, dict) and row.get('source') != 'device' and row.get('paymentState') == 'OPEN'
            and row.get('total') is None and row.get('payments') == [])]
        if len({row['id'] for row in rows}) != len(rows):
            raise ValueError('Duplicate sandbox order')
        fetched = payload.get('fetched_at')
        if type(fetched) not in (int, float) or not 0 < fetched <= time.time() + 1:
            raise ValueError('Sandbox probe returned an invalid fetch time')
        seen = {}
        for row in rows:
            stamp = row.get('seen_at', fetched)
            if type(stamp) not in (int, float) or not 0 < stamp <= time.time() + 1:
                raise ValueError('Sandbox probe returned an invalid order time')
            seen[row['id']] = stamp
        with con:
            for row in rows:
                con.execute('''INSERT INTO orders(source_id,payload,seen_at) VALUES (?,?,?)
                    ON CONFLICT(source_id) DO UPDATE SET payload=excluded.payload,seen_at=excluded.seen_at''',
                    (row['id'], json.dumps(row), seen[row['id']]))
                order_id = con.execute('SELECT id FROM orders WHERE source_id=?', (row['id'],)).fetchone()[0]
                con.execute('DELETE FROM hidden_orders WHERE order_id=?', (order_id,))
                con.execute('UPDATE payments SET active=0 WHERE order_id=?', (order_id,))
                for payment in row['payments']:
                    prior = con.execute('SELECT order_id FROM payments WHERE source_id=?', (payment['id'],)).fetchone()
                    if prior and prior['order_id'] != order_id:
                        raise ValueError('Payment belongs to a different sandbox order')
                    con.execute('''INSERT INTO payments(order_id,source_id,tender,amount,result) VALUES (?,?,?,?,?)
                        ON CONFLICT(source_id) DO UPDATE SET tender=excluded.tender,amount=excluded.amount,
                        result=excluded.result,active=1''', (order_id, payment['id'], tender(payment.get('tenderLabel')),
                        payment['amount'], str(payment.get('result', 'UNKNOWN'))))
            con.execute('UPDATE feed SET fetched_at=?,next_poll=?,error=? WHERE id=1',
                        (max([fetched, *seen.values()]), time.time() + .5,
                         'Clover cloud unavailable. Device items may still update; payment confirmation is delayed.'
                         if cloud_unavailable else ''))
    except (requests.RequestException, ValueError):
        with con:
            con.execute('UPDATE feed SET next_poll=?,error=? WHERE id=1',
                        (time.time() + retry_delay, 'Clover sync unavailable. Showing the last received snapshot.'))


def product_for_code(code):
    """Exact barcode match in the POPCORE catalogue, read-only. Ambiguous codes stay unmapped."""
    if not isinstance(code, str) or not code.strip():
        return None
    rows = get_db().execute('''SELECT DISTINCT b.product_id AS id,
        COALESCE(NULLIF(p.jizhanming,''),p.name_cn_en) AS name
        FROM product_barcodes b JOIN products p ON p.id=b.product_id WHERE b.code=?''',
        (code.strip(),)).fetchall()
    return dict(rows[0]) if len(rows) == 1 else None


def product_for_item(item_id, code):
    """Clover item ID first (set by the item import once the API is connected), barcode second."""
    if isinstance(item_id, str) and IDENTIFIER.fullmatch(item_id):
        row = get_db().execute('''SELECT id, COALESCE(NULLIF(jizhanming,''),name_cn_en) AS name
            FROM products WHERE clover_item_id=?''', (item_id,)).fetchone()
        if row:
            return dict(row)
    return product_for_code(code)


def detail(con, row, access):
    source = json.loads(row['payload'])
    live = bool(access['live_stores'])
    owner = row['cashier_sub'] == request.jwt_payload['sub']
    feed = con.execute('SELECT * FROM feed').fetchone()
    device = source.get('source') == 'device'
    fresh = (device or not feed['error']) and time.time() - row['seen_at'] <= 5
    state = con.execute('SELECT * FROM order_state WHERE order_id=?', (row['id'],)).fetchone()
    payments = con.execute('SELECT * FROM payments WHERE order_id=? ORDER BY id', (row['id'],)).fetchall()
    manual = con.execute('SELECT * FROM manual_payments WHERE order_id=? ORDER BY id', (row['id'],)).fetchall()
    clover_received = sum(p['amount'] for p in payments if p['active'] and p['result'] == 'SUCCESS')
    manual_received = sum(p['amount'] for p in manual if p['status'] == 'completed')
    received = clover_received + manual_received
    total = source['total']
    # Customer pays `payable`: the Clover total, or the rounded amount the cashier confirmed here.
    payable = state['payable'] if state and state['payable'] is not None else total
    settled = bool(state and state['settled_at'])
    if manual_received:
        paid = received == payable and payable > 0
        if paid and not settled:
            # Money recorded here is POPCORE's record: fix the settlement so a later Clover change is flagged.
            with con:
                con.execute('INSERT OR IGNORE INTO order_state(order_id) VALUES (?)', (row['id'],))
                con.execute('''UPDATE order_state SET settled_at=?,settled_total=?,settled_clover=?
                    WHERE order_id=? AND settled_at IS NULL''', (time.time(), total, clover_received, row['id']))
            state = con.execute('SELECT * FROM order_state WHERE order_id=?', (row['id'],)).fetchone()
            settled = True
    else:
        paid = not device and source.get('paymentState') == 'PAID' and received == total and total > 0
    status = 'completed' if settled or paid else 'open'
    if not (live and (access['role'] != 'staff' or status == 'open') or owner and access['role'] == 'staff'):
        raise PermissionError('Checkout history access denied')
    changed_after_settlement = settled and (total != state['settled_total'] or clover_received != state['settled_clover'])
    has_discount = bool(source.get('discounts')) or any(
        item.get('discountAmount') or item.get('orderLevelDiscountAmount') for item in source['items'])
    cash_discount_applied = any(
        type(discount.get('amount')) is int and discount['amount'] < 0
        and discount.get('percentage') is None
        and discount.get('name') == f"CA${-discount['amount'] // 100}.{(-discount['amount']) % 100:02d} Off"
        for discount in source.get('discounts') or [] if isinstance(discount, dict))
    prices = [item.get('priceWithModifiersAndItemAndOrderDiscounts')
              if item.get('priceWithModifiersAndItemAndOrderDiscounts') is not None else item.get('price')
              for item in source['items']]
    simple = bool(prices) and all(type(p) is int and p >= 0 for p in prices) and all(
        item.get('unitQty') in (None, 0, 1000) and not item.get('refunded') for item in source['items'])
    subtotal = sum(prices) if simple else 0
    quote_available = total is not None and simple and 0 < subtotal <= total and not has_discount
    totals_known = total is not None and simple and subtotal <= total
    writable = live and (owner or access['role'] in ('admin', 'manager'))
    note_row = con.execute('SELECT note FROM order_notes WHERE order_id=?', (row['id'],)).fetchone()
    lines = []
    for item in source['items']:
        product = product_for_item(item.get('itemId'), item.get('itemCode'))
        lines.append(dict(product_name_snapshot=str(item.get('name') or 'Clover item')[:240],
            quantity=(item['unitQty']/1000 if type(item.get('unitQty')) is int and item['unitQty'] > 0 else 1),
            unit=str(item.get('unitName') or 'each'), item_code=str(item.get('itemCode') or '')[:80] or None,
            product_id=product['id'] if product else None, product_name=product['name'] if product else None,
            mapped=product is not None))
    attempts = [dict(id=p['id'], source='clover', tender=p['tender'], amount_cents=p['amount'],
            status=('completed' if p['result'] == 'SUCCESS' else 'failed') if p['active'] else 'cancelled',
            can_upload=writable and p['active'] and p['result'] == 'SUCCESS',
            photos=[dict(r) for r in con.execute('SELECT id FROM evidence WHERE payment_id=? ORDER BY id', (p['id'],))])
            for p in payments]
    attempts += [dict(id=p['id'], source='popcore', tender=p['tender'], amount_cents=p['amount'], status=p['status'],
            can_upload=writable and p['status'] != 'cancelled',
            photos=[dict(r) for r in con.execute('SELECT id FROM manual_evidence WHERE payment_id=? ORDER BY id', (p['id'],))])
            for p in manual]
    return dict(id=row['id'], source='clover-sandbox', store_id=feed['store_id'],
        reference=source['id'], register_name='Clover sandbox', business_date=datetime.fromtimestamp(
            source['createdTime'] / 1000, ZoneInfo('America/Toronto')).date().isoformat(),
        status=status, version=state['version'] if state else 1, sale_id=None,
        cashier_name=row['cashier_name'], cashier_sub=row['cashier_sub'],
        can_manage=False, can_refund=False, can_process=live and owner and status == 'open' and fresh and total is not None,
        can_record=writable and status == 'open' and total is not None,
        can_claim=live and not row['cashier_sub'] and status == 'open' and fresh,
        received_cents=received, clover_received_cents=clover_received,
        remaining_cents=max(0, (payable or 0) - received), refunded_cents=0,
        refund_due_cents=0, abandoned_reason=None, refunds=[], quote_available=quote_available,
        totals_known=totals_known, total_pending=total is None, source_detail='device' if device else 'cloud',
        source_fresh=fresh, source_seen_at=row['seen_at'], has_discount=has_discount,
        cash_discount_applied=cash_discount_applied, settled_in_popcore=settled,
        changed_after_settlement=changed_after_settlement,
        can_hide=not fresh and status == 'open' and (owner or access['role'] in ('manager', 'admin')),
        note=note_row['note'] if note_row else '', can_note=owner or access['role'] in ('manager', 'admin'),
        attempts=attempts,
        order=dict(subtotal_cents=subtotal if totals_known else 0, source_tax_cents=total-subtotal if totals_known else 0,
            gross_cents=total or 0, reduction_cents=max(0, total - payable) if total is not None and payable is not None else 0,
            collected_cents=payable or 0, lines=lines))


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
        where, values = ['id NOT IN (SELECT order_id FROM hidden_orders)'], []
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


@bp.post('/<int:order_id>/notes')
@role_required('staff')
def save_note(order_id):
    with database() as con:
        access = scope(con)
        value = detail(con, order_row(con, order_id), access)
        if not value['can_note']:
            raise PermissionError('Order note access denied')
        note, expected = read_order_note(request.get_json(silent=True) or {})
        with con:
            con.execute('INSERT OR IGNORE INTO order_notes(order_id) VALUES (?)', (order_id,))
            current = con.execute('SELECT note FROM order_notes WHERE order_id=?', (order_id,)).fetchone()['note']
            if current != note:
                if current != expected or not con.execute('UPDATE order_notes SET note=? WHERE order_id=? AND note=?',
                                                          (note, order_id, expected)).rowcount:
                    return jsonify(error='Order note changed. Refresh before saving.'), 409
        return jsonify(detail(con, order_row(con, order_id), access))


@bp.post('/<int:order_id>/hide')
@role_required('staff')
def hide(order_id):
    with database() as con:
        value = detail(con, order_row(con, order_id), scope(con))
        if not value['can_hide']:
            return jsonify(error='Only a stale sandbox snapshot you own can be removed'), 409
        with con:
            con.execute('INSERT OR IGNORE INTO hidden_orders(order_id) VALUES (?)', (order_id,))
        return jsonify(hidden=True)


@bp.post('/<int:order_id>/claim')
@role_required('staff')
def claim(order_id):
    with database() as con:
        access = scope(con)
        value = detail(con, order_row(con, order_id), access)
        actor = request.jwt_payload
        if not access['live_stores']:
            raise PermissionError('Checkout shift required')
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


def body():
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def bump_version(con, order_id, data):
    """Inside the caller's transaction: check expected_version, then advance it."""
    expected = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    con.execute('INSERT OR IGNORE INTO order_state(order_id) VALUES (?)', (order_id,))
    changed = con.execute('UPDATE order_state SET version=version+1 WHERE order_id=? AND version=?',
                          (order_id, expected)).rowcount
    if not changed:
        raise InventoryConflict('Checkout changed. Refresh before continuing.', 'checkout_state_conflict')


@bp.post('/<int:order_id>/attempts')
@role_required('staff')
def start_attempt(order_id):
    with database() as con:
        access = scope(con)
        data = body()
        value = detail(con, order_row(con, order_id), access)
        if not value['can_process']:
            raise InventoryConflict('This order cannot take a payment right now', 'checkout_state_conflict')
        tender = data.get('tender')
        if tender == 'card':
            raise ValueError('Card is taken on Clover. Choose the tender received in POPCORE.')
        if tender not in MANUAL_TENDERS:
            raise ValueError('Choose cash, e-transfer, WeChat Pay or Alipay')
        total = value['order']['gross_cents']
        payable = read_int(data.get('payable_cents'), 'payable_cents', minimum=1)
        manual_done = any(a['source'] == 'popcore' and a['status'] == 'completed' for a in value['attempts'])
        if manual_done and payable != value['order']['collected_cents']:
            raise ValueError('The amount is fixed once a payment is recorded')
        if payable > total or payable < value['received_cents']:
            raise ValueError('Customer pays must be between the money already received and the Clover total')
        amount = read_int(data.get('amount_cents'), 'amount_cents', minimum=1)
        if amount > payable - value['received_cents']:
            raise ValueError('Amount must be within the remaining balance')
        with con:
            bump_version(con, order_id, data)
            con.execute("UPDATE manual_payments SET status='cancelled' WHERE order_id=? AND status='pending'", (order_id,))
            con.execute('INSERT INTO manual_payments(order_id,tender,amount,recorded_by,created_at) VALUES (?,?,?,?,?)',
                        (order_id, tender, amount, request.jwt_payload['sub'], time.time()))
            con.execute('UPDATE order_state SET payable=? WHERE order_id=?', (payable, order_id))
        return jsonify(detail(con, order_row(con, order_id), access))


@bp.post('/<int:order_id>/complete')
@role_required('staff')
def complete(order_id):
    with database() as con:
        access = scope(con)
        data = body()
        value = detail(con, order_row(con, order_id), access)
        if not value['can_record']:
            raise PermissionError('Checkout cashier access denied')
        attempt_id = read_int(data.get('attempt_id'), 'attempt_id', minimum=1)
        attempt = next((a for a in value['attempts'] if a['source'] == 'popcore' and a['id'] == attempt_id
                        and a['status'] == 'pending'), None)
        if attempt is None:
            raise InventoryConflict('This payment attempt is no longer pending', 'attempt_state_conflict')
        total, payable = value['order']['gross_cents'], value['order']['collected_cents']
        if payable > total or attempt['amount_cents'] > payable - value['received_cents']:
            raise InventoryConflict('Clover total changed. Start the payment again.', 'checkout_state_conflict')
        with con:
            bump_version(con, order_id, data)
            con.execute("UPDATE manual_payments SET status='completed' WHERE id=? AND status='pending'", (attempt_id,))
        # detail() fixes the settlement itself once recorded money equals the confirmed amount.
        return jsonify(detail(con, order_row(con, order_id), access))


@bp.post('/<int:order_id>/manual/<int:payment_id>/evidence')
@role_required('staff')
def upload_manual(order_id, payment_id):
    with database() as con:
        value = detail(con, order_row(con, order_id), scope(con))
        payment = next((p for p in value['attempts'] if p['source'] == 'popcore' and p['id'] == payment_id), None)
        if not payment or not payment['can_upload']:
            raise PermissionError('Payment evidence access denied')
        image = prepare_image(request.files.get('image'))
        with con:
            con.execute('''INSERT OR IGNORE INTO manual_evidence(payment_id,content_hash,mime_type,content,uploader_sub)
                VALUES (?,?,?,?,?)''', (payment_id,image['content_hash'],image['mime_type'],image['content'],request.jwt_payload['sub']))
        photo = con.execute('SELECT id FROM manual_evidence WHERE payment_id=? AND content_hash=?',
                            (payment_id,image['content_hash'])).fetchone()
        return jsonify(id=photo['id'], attempt_id=payment_id), 201


@bp.get('/<int:order_id>/manual-evidence/<int:photo_id>')
@role_required('staff')
def manual_content(order_id, photo_id):
    with database() as con:
        access = scope(con)
        value = detail(con, order_row(con, order_id), access)
        if value['cashier_sub'] != request.jwt_payload['sub'] and access['role'] == 'staff':
            raise PermissionError('Payment evidence access denied')
        photo = con.execute('''SELECT e.* FROM manual_evidence e JOIN manual_payments p ON p.id=e.payment_id
            WHERE e.id=? AND p.order_id=?''', (photo_id, order_id)).fetchone()
        if photo is None:
            raise ValueError('Photo does not belong to this sandbox order')
        return send_file(io.BytesIO(photo['content']), mimetype=photo['mime_type'])


@bp.post('/<int:order_id>/attempts/<int:payment_id>/evidence')
@role_required('staff')
def upload(order_id, payment_id):
    with database() as con:
        value = detail(con, order_row(con, order_id), scope(con))
        payment = next((p for p in value['attempts'] if p['id'] == payment_id), None)
        if not payment or not payment['can_upload']:
            raise PermissionError('Payment evidence access denied')
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
