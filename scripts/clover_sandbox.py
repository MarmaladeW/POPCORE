"""Single-merchant sandbox probe. No POPCORE imports or Clover business-data writes."""
import argparse
from contextlib import contextmanager
import hmac
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlencode, urlsplit

import requests
from flask import Flask, Response, abort, jsonify, redirect, render_template_string, request, session

PREFIX = '/clover-sandbox'
API = 'https://apisandbox.dev.clover.com'
AUTHORIZE = 'https://sandbox.dev.clover.com/oauth/v2/authorize'
IDENTIFIER = re.compile(r'[A-Za-z0-9]{13}\Z')
MAX_SAFE_INTEGER = 2**53 - 1


class CloverError(Exception):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def _safe_cents(value):
    return type(value) is int and 0 <= value <= MAX_SAFE_INTEGER


def suggest_payable(total_cents):
    if not _safe_cents(total_cents):
        return None
    if total_cents == 0:
        return 0
    if total_cents < 100:
        return max(1, (total_cents * 390 + 199) // 400)
    suggestion = ((total_cents * 1950 + 99_999) // 200_000) * 100
    if suggestion >= total_cents:
        suggestion -= 100
    return max(1, suggestion)


def discount_quote(subtotal_cents, tax_cents, target_cents):
    if not all(_safe_cents(value) for value in (subtotal_cents, tax_cents, target_cents)):
        return None
    total = subtotal_cents + tax_cents
    if target_cents <= 0 or target_cents > total or subtotal_cents == 0 or total > MAX_SAFE_INTEGER:
        return None
    ideal = target_cents * subtotal_cents // total
    best = None
    for remaining in range(max(0, ideal - 2), min(subtotal_cents, ideal + 3) + 1):
        predicted = remaining + (remaining * tax_cents * 2 + subtotal_cents) // (subtotal_cents * 2)
        candidate = (abs(predicted - target_cents), predicted, remaining)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return None
    _, predicted, remaining = best
    return {'discountCents': subtotal_cents - remaining, 'predictedTotalCents': predicted,
            'savingsCents': total - target_cents, 'warning': (total - target_cents) * 5 > total}


def money(cents):
    return '${}.{:02d}'.format(cents // 100, cents % 100) if _safe_cents(cents) else '—'


def device_order(value):
    if (not isinstance(value, dict) or not IDENTIFIER.fullmatch(str(value.get('id', '')))
            or value.get('currency') != 'CAD'
            or type(value.get('createdTime')) is not int
            or not 0 < value['createdTime'] < 32_503_680_000_000
            or (value.get('total') is not None and not _safe_cents(value['total']))):
        raise ValueError('Invalid device order')
    items = value.get('items')
    discounts = value.get('discounts')
    if not isinstance(items, list) or len(items) > 200 or not isinstance(discounts, list) or len(discounts) > 100:
        raise ValueError('Invalid device order contents')
    safe_items, ids = [], set()
    for item in items:
        if (not isinstance(item, dict) or not IDENTIFIER.fullmatch(str(item.get('id', '')))
                or item['id'] in ids or not isinstance(item.get('name'), str)
                or not 0 < len(item['name']) <= 240):
            raise ValueError('Invalid device line item')
        ids.add(item['id'])
        safe = {key: item.get(key) for key in ('id', 'name', 'price',
                'priceWithModifiersAndItemAndOrderDiscounts', 'unitQty',
                'unitName', 'discountAmount', 'orderLevelDiscountAmount')}
        if any(safe[key] is not None and not _safe_cents(safe[key]) for key in (
                'price', 'priceWithModifiersAndItemAndOrderDiscounts', 'discountAmount',
                'orderLevelDiscountAmount')):
            raise ValueError('Invalid device line-item amount')
        if safe['unitQty'] is not None and (type(safe['unitQty']) is not int or
                                            not 0 <= safe['unitQty'] <= 1_000_000_000):
            raise ValueError('Invalid device quantity')
        if safe['unitName'] is not None and (not isinstance(safe['unitName'], str) or
                                             len(safe['unitName']) > 40):
            raise ValueError('Invalid device unit')
        safe_items.append(safe)
    safe_discounts = []
    for discount in discounts:
        if (not isinstance(discount, dict) or not IDENTIFIER.fullmatch(str(discount.get('id', '')))
                or not isinstance(discount.get('name'), str)
                or len(discount['name']) > 240):
            raise ValueError('Invalid device discount')
        amount, percentage = discount.get('amount'), discount.get('percentage')
        if (amount is not None and (type(amount) is not int or abs(amount) > MAX_SAFE_INTEGER)
                or percentage is not None and (type(percentage) is not int or abs(percentage) > 1_000_000)):
            raise ValueError('Invalid device discount amount')
        safe_discounts.append({key: discount.get(key) for key in ('id', 'name', 'amount', 'percentage')})
    return dict(id=value['id'], currency='CAD', createdTime=value['createdTime'],
                total=value.get('total'), items=safe_items, discounts=safe_discounts)


def cloud_matches_device(cloud, device):
    if device['total'] is not None and cloud.get('total') != device['total']:
        return False
    def item_key(item):
        return (item.get('id'), item.get('unitQty'), item.get('price'),
                item.get('priceWithModifiersAndItemAndOrderDiscounts'))
    return ({item_key(item) for item in cloud['items']} ==
            {item_key(item) for item in device['items']} and
            {(item.get('id'), item.get('amount'), item.get('percentage'))
             for item in cloud['discounts']} ==
            {(item.get('id'), item.get('amount'), item.get('percentage'))
             for item in device['discounts']})


def create_app(config):
    config = dict(config)
    public = config['PUBLIC_URL'].rstrip('/')
    url = urlsplit(public)
    if (url.path != PREFIX or url.query or url.fragment or url.username or url.password
            or not url.hostname or (url.scheme != 'https' and not (
                url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1')))):
        raise ValueError('PUBLIC_URL must be HTTPS ending in /clover-sandbox (HTTP loopback is allowed).')
    for key in ('SESSION_SECRET', 'ADMIN_PASSWORD', 'WEBHOOK_PATH_KEY'):
        if len(config.get(key, '')) < 32:
            raise ValueError(key + ' must contain at least 32 characters.')
    data = Path(config['DATA_DIR']).resolve()
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(data, 0o700)
    db_path = data / 'sandbox.sqlite3'
    fd = os.open(db_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    os.chmod(db_path, 0o600)

    @contextmanager
    def database():
        db = sqlite3.connect(db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    with database() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS states (nonce TEXT PRIMARY KEY, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                merchant TEXT, object_id TEXT, kind TEXT, source_time INTEGER,
                received REAL NOT NULL, PRIMARY KEY(merchant, object_id, kind, source_time));
            CREATE TABLE IF NOT EXISTS device_cursor (
                id INTEGER PRIMARY KEY CHECK(id=1), sequence INTEGER NOT NULL,
                payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS device_orders (
                order_id TEXT PRIMARY KEY, payload TEXT NOT NULL, received REAL NOT NULL,
                reconciled INTEGER NOT NULL DEFAULT 0);
        ''')

    def setting(key):
        with database() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return row['value'] if row else None

    def save(key, value):
        with database() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (key, value))

    def save_tokens(tokens):
        if not isinstance(tokens, dict) or not isinstance(tokens.get('access_token'), str):
            raise CloverError('Invalid token response; reconnect to Clover sandbox.')
        save('tokens', json.dumps({k: tokens[k] for k in (
            'access_token', 'refresh_token', 'access_token_expiration', 'refresh_token_expiration')
            if k in tokens}))

    rate_lock = threading.Lock()
    retry_at = 0
    rate_failures = 0

    def clover(method, path, **kwargs):
        nonlocal retry_at, rate_failures
        # Only token exchanges may POST. There is deliberately no generic write API.
        if method != 'GET' and path not in ('/oauth/v2/token', '/oauth/v2/refresh'):
            raise ValueError('Sandbox probe does not write Clover orders, payments or stock.')
        with rate_lock:
            remaining = retry_at - time.monotonic()
            if remaining > 0:
                raise CloverError('Clover is limiting requests. Waiting before retrying.', math.ceil(remaining))
        try:
            result = requests.request(method, API + path, timeout=(5, 15), allow_redirects=False, **kwargs)
            if result.status_code == 429:
                with rate_lock:
                    rate_failures = min(rate_failures + 1, 6)
                    delay = min(30, 2 ** (rate_failures - 1))
                    try:
                        delay = max(delay, int(result.headers.get('Retry-After', '1')))
                    except ValueError:
                        pass
                    retry_at = max(retry_at, time.monotonic() + delay)
                    delay = math.ceil(retry_at - time.monotonic())
                raise CloverError('Clover is limiting requests. Waiting before retrying.', delay)
            if result.status_code != 200:
                raise CloverError('Clover sandbox returned HTTP ' + str(result.status_code) +
                                  '. Check permissions or reconnect; provider response is not displayed.')
            payload = result.json()
            if not isinstance(payload, dict):
                raise ValueError('Expected an object')
            with rate_lock:
                if time.monotonic() >= retry_at:
                    rate_failures = 0
            return payload
        except (requests.RequestException, ValueError):
            raise CloverError('Clover sandbox could not be read. Retry or reconnect.') from None

    # ponytail: one process serializes refresh-token rotation; use a cross-process lock before scaling.
    token_lock = threading.Lock()

    def access_token():
        with token_lock:
            tokens = json.loads(setting('tokens') or '{}')
            if not tokens.get('access_token'):
                raise CloverError('Connect the Canadian test merchant first.')
            if tokens.get('access_token_expiration', 0) <= time.time() + 60:
                if not tokens.get('refresh_token') or tokens.get('refresh_token_expiration', 0) <= time.time():
                    raise CloverError('Sandbox authorization expired. Reconnect the test merchant.')
                tokens = clover('POST', '/oauth/v2/refresh', json={
                    'client_id': config['CLOVER_APP_ID'], 'refresh_token': tokens['refresh_token']})
                save_tokens(tokens)
            return tokens['access_token']

    app = Flask(__name__)
    app.config.update(SECRET_KEY=config['SESSION_SECRET'], MAX_CONTENT_LENGTH=128 * 1024,
                      SESSION_COOKIE_NAME='popcore_clover_sandbox', SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SECURE=url.scheme == 'https', SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_PATH=PREFIX)
    app.extensions['clover_db'] = database
    app.extensions['clover_save_tokens'] = save_tokens

    @app.before_request
    def protect():
        if request.endpoint in ('webhook', 'health', 'device_snapshot'):
            return None
        auth = request.authorization
        if not (auth and auth.type == 'basic' and auth.username == 'popcore'
                and hmac.compare_digest((auth.password or '').encode(), config['ADMIN_PASSWORD'].encode())):
            return Response('Sandbox administrator login required.', 401,
                            {'WWW-Authenticate': 'Basic realm="POPCORE sandbox"'})

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
                                 'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"})
        return response

    @app.errorhandler(CloverError)
    def provider_error(error):
        response = jsonify(error=str(error))
        response.status_code = 502
        if error.retry_after is not None:
            response.headers['Retry-After'] = str(error.retry_after)
        return response

    def missing():
        return [k for k in ('CLOVER_APP_ID', 'CLOVER_APP_SECRET', 'CLOVER_MERCHANT_ID') if not config.get(k)]

    @app.get(PREFIX + '/health')
    def health():
        return jsonify(status='ok', environment='sandbox')

    @app.post(PREFIX + '/device-snapshot')
    def device_snapshot():
        secret = config.get('DEVICE_BRIDGE_SECRET', '')
        device_id = config.get('DEVICE_BRIDGE_ID', '')
        if len(secret) < 32 or not device_id:
            abort(404)
        supplied = request.headers.get('Authorization', '')
        if not supplied.startswith('Bearer ') or not hmac.compare_digest(supplied[7:], secret):
            abort(401)
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            abort(400)
        if value.get('merchantId') != config.get('CLOVER_MERCHANT_ID') or value.get('deviceId') != device_id:
            abort(403)
        sequence = value.get('sequence')
        if type(sequence) is not int or not 0 < sequence <= MAX_SAFE_INTEGER:
            abort(400)
        try:
            order = device_order(value.get('order'))
        except ValueError:
            abort(400)
        body = json.dumps(order, sort_keys=True, separators=(',', ':'))
        with database() as db:
            db.execute('BEGIN IMMEDIATE')
            cursor = db.execute('SELECT sequence,payload FROM device_cursor WHERE id=1').fetchone()
            if cursor and sequence <= cursor['sequence']:
                if sequence == cursor['sequence'] and body == cursor['payload']:
                    return jsonify(accepted=True, replay=True)
                abort(409)
            db.execute('INSERT INTO device_cursor(id,sequence,payload) VALUES (1,?,?) '
                       'ON CONFLICT(id) DO UPDATE SET sequence=excluded.sequence,payload=excluded.payload',
                       (sequence, body))
            db.execute('INSERT INTO device_orders(order_id,payload,received) VALUES (?,?,?) '
                       'ON CONFLICT(order_id) DO UPDATE SET payload=excluded.payload,received=excluded.received,'
                       'reconciled=CASE WHEN device_orders.payload=excluded.payload '
                       'THEN device_orders.reconciled ELSE 0 END',
                       (order['id'], body, time.time()))
        return jsonify(accepted=True, replay=False)

    @app.get(PREFIX + '/')
    def home():
        with database() as db:
            events = [dict(r) for r in db.execute('SELECT * FROM events ORDER BY received DESC LIMIT 20')]
        return render_template_string('''<!doctype html><html lang="en"><meta charset="utf-8">
            <meta name="viewport" content="width=device-width,initial-scale=1"><title>POPCORE Clover sandbox</title>
            <style>body{font:17px system-ui;max-width:850px;margin:32px auto;padding:0 20px;line-height:1.5}
            code,pre{overflow-wrap:anywhere;white-space:pre-wrap}table{width:100%;border-collapse:collapse}
            th,td{text-align:left;padding:8px;border-bottom:1px solid #ddd}a{color:#075a9c}</style>
            <h1>POPCORE · Clover sandbox</h1>
            <p>Isolated connection test. No real-store connection or inventory posting.</p>
            <p>{{ 'Connected to the configured test merchant.' if connected else 'Not connected to Clover yet.' }}</p>
            {% if missing %}<p>Local configuration still needed: {{ missing|join(', ') }}.</p>{% endif %}
            <p><a href="{{ prefix }}/connect">Connect Canadian test merchant</a> ·
            <a href="{{ prefix }}/review">Review sandbox checkout</a> ·
            <a href="{{ prefix }}/orders">Read test orders (JSON)</a></p>
            <h2>Clover dashboard settings</h2>
            <p>Site URL: <code>{{ public }}/</code></p>
            <p>Alternate Launch Path: <code>{{ prefix }}/connect</code></p>
            <p>OAuth response: <strong>Code</strong>. CORS domain: blank.</p>
            <p>Webhook URL (keep private): <code>{{ public }}/webhooks/{{ hook }}</code></p>
            <p>Webhook verification code: <code>{{ verification or 'Not received yet' }}</code></p>
            <p>Webhook authentication: {{ 'configured' if hook_auth else 'not configured; event delivery is blocked' }}.</p>
            <h2>Latest notifications</h2><p>Reload this page after changing a test order.
            Notification time includes Clover delivery delay; it is not device scan latency.</p>
            <table><tr><th>Object</th><th>Change</th><th>Clover timestamp (ms)</th></tr>
            {% for e in events %}<tr><td>{{ e.object_id }}</td><td>{{ e.kind }}</td><td>{{ e.source_time }}</td></tr>
            {% endfor %}</table></html>''', prefix=PREFIX, public=public, missing=missing(),
            connected=bool(setting('tokens')), events=events, hook=config['WEBHOOK_PATH_KEY'],
            verification=setting('verification'), hook_auth=bool(config.get('CLOVER_WEBHOOK_AUTH')))

    @app.get(PREFIX + '/connect')
    def connect():
        if missing():
            return jsonify(error='Complete local configuration first.', missing=missing()), 503
        if not all(IDENTIFIER.fullmatch(config[k]) for k in ('CLOVER_APP_ID', 'CLOVER_MERCHANT_ID')):
            abort(400)
        nonce = secrets.token_urlsafe(32)
        with database() as db:
            db.execute('DELETE FROM states WHERE expires < ?', (time.time(),))
            db.execute('INSERT INTO states VALUES (?, ?)', (nonce, time.time() + 600))
        session['oauth_state'] = nonce
        return redirect(AUTHORIZE + '?' + urlencode({
            'merchant_id': config['CLOVER_MERCHANT_ID'],
            'client_id': config['CLOVER_APP_ID'], 'response_type': 'code',
            'redirect_uri': public + '/callback', 'state': nonce}))

    @app.get(PREFIX + '/callback')
    def callback():
        state = request.args.get('state', '')
        if not state or not hmac.compare_digest(state, session.get('oauth_state', '')):
            abort(400)
        session.pop('oauth_state', None)
        with database() as db:
            consumed = db.execute('DELETE FROM states WHERE nonce=? AND expires>?',
                                  (state, time.time())).rowcount
        if not consumed or request.args.get('error'):
            abort(400)
        merchant = request.args.get('merchant_id') or request.args.get('merchantId')
        code = request.args.get('code', '')
        if merchant != config.get('CLOVER_MERCHANT_ID') or not code or len(code) > 4096:
            abort(400)
        save_tokens(clover('POST', '/oauth/v2/token', json={
            'client_id': config['CLOVER_APP_ID'], 'client_secret': config['CLOVER_APP_SECRET'], 'code': code}))
        return redirect(PREFIX + '/')

    tender_labels = {}
    tender_lock = threading.Lock()
    tender_retry_after = 0
    tender_lookup_running = False
    # One probe worker shares the same sample across checkout, review and diagnostics.
    orders_lock = threading.Lock()
    orders_condition = threading.Condition(orders_lock)
    orders_payload = None
    orders_fetched_at = 0
    orders_next_read = 0
    orders_error = False
    orders_failure = None
    orders_running = False
    orders_token = None

    def lookup_tender(token, merchant, payment_id, tender_id):
        nonlocal tender_retry_after, tender_lookup_running
        try:
            detail = clover('GET', '/v3/merchants/' + merchant + '/payments/' + payment_id,
                            headers={'Authorization': 'Bearer ' + token},
                            params={'expand': 'tender,employee,order'})
            tender = detail.get('tender')
            label = tender.get('label') if isinstance(tender, dict) and tender.get('id') == tender_id else None
        except CloverError:
            label = None
        with tender_lock:
            if label:
                tender_labels[tender_id] = label
            else:
                tender_retry_after = time.monotonic() + 30
            tender_lookup_running = False

    def refresh_cloud(merchant):
        nonlocal orders_payload, orders_fetched_at, orders_next_read, orders_error
        nonlocal orders_failure, orders_running, orders_token
        try:
            token = access_token()
            payload = clover('GET', '/v3/merchants/' + merchant + '/orders',
                             headers={'Authorization': 'Bearer ' + token},
                             params={'limit': 20, 'orderBy': 'modifiedTime DESC',
                                     'expand': 'lineItems,discounts,payments,employee'})
            with orders_condition:
                orders_payload, orders_fetched_at, orders_token = payload, time.time(), token
                orders_next_read = time.monotonic() + 1
                orders_error, orders_failure = False, None
        except CloverError as error:
            with orders_condition:
                orders_next_read = time.monotonic() + (error.retry_after or 2)
                orders_error, orders_failure = True, error
        finally:
            with orders_condition:
                orders_running = False
                orders_condition.notify_all()

    def read_orders():
        nonlocal tender_lookup_running, orders_running, orders_next_read
        merchant = config.get('CLOVER_MERCHANT_ID', '')
        if not IDENTIFIER.fullmatch(merchant):
            raise CloverError('Set the Canadian test merchant ID first.')
        with database() as db:
            device_rows = db.execute('SELECT order_id,payload,received,reconciled '
                                     'FROM device_orders ORDER BY received DESC LIMIT 20').fetchall()
        with orders_condition:
            start = not orders_running and time.monotonic() >= orders_next_read
            if start:
                orders_running = True
                if device_rows:
                    threading.Thread(target=refresh_cloud, args=(merchant,), daemon=True).start()
            elif orders_running and not device_rows:
                orders_condition.wait_for(lambda: not orders_running, timeout=20)
        if start and not device_rows:
            refresh_cloud(merchant)
        with orders_condition:
            if orders_payload is None and not device_rows:
                raise orders_failure or CloverError('Clover sandbox could not be read. Retry or reconnect.')
            payload = orders_payload or {'elements': []}
            fetched_at, token = orders_fetched_at, orders_token
            cloud_unavailable = orders_error or orders_payload is None
        payment_ids = {payment.get('id', '') for order in payload.get('elements', [])
                       for payment in (order.get('payments') or {}).get('elements', [])}
        if any(not IDENTIFIER.fullmatch(payment_id) for payment_id in payment_ids):
            raise CloverError('Clover sandbox returned an invalid payment identifier.')
        # Only show order facts needed for this probe, never raw customer/card/token data.
        rows = []
        lookup_attempted = False
        for order in payload.get('elements', []):
            row = {k: order.get(k) for k in (
                'id', 'total', 'currency', 'paymentState', 'state', 'testMode', 'createdTime',
                'clientCreatedTime', 'modifiedTime', 'unpaidBalance', 'manualTransaction', 'payType')}
            row['orderTypeId'] = (order.get('orderType') or {}).get('id')
            row['employeeId'] = (order.get('employee') or {}).get('id')
            row['items'] = [{**{k: item.get(k) for k in (
                'id', 'itemCode', 'name', 'price', 'priceWithModifiersAndItemAndOrderDiscounts',
                'unitQty', 'unitName', 'discountAmount', 'orderLevelDiscountAmount', 'refunded',
                'isRevenue', 'createdTime')}, 'itemId': (item.get('item') or {}).get('id')}
                for item in (order.get('lineItems') or {}).get('elements', [])]
            row['discounts'] = [{k: discount.get(k) for k in ('id', 'name', 'amount', 'percentage')}
                                for discount in (order.get('discounts') or {}).get('elements', [])]
            row['payments'] = []
            for payment in (order.get('payments') or {}).get('elements', []):
                payment_id = payment.get('id', '')
                safe_payment = {k: payment.get(k) for k in (
                    'id', 'amount', 'tipAmount', 'taxAmount', 'result', 'createdTime',
                    'modifiedTime', 'offline')}
                tender = payment.get('tender') or {}
                tender_id = tender.get('id')
                safe_payment['tenderId'] = tender_id
                safe_payment['tenderLabel'] = tender.get('label')
                safe_payment['employeeId'] = (payment.get('employee') or {}).get('id')
                if token and tender_id and not safe_payment['tenderLabel']:
                    with tender_lock:
                        if tender_id not in tender_labels and not lookup_attempted and not tender_lookup_running and time.monotonic() >= tender_retry_after:
                            lookup_attempted = True
                            tender_lookup_running = True
                            threading.Thread(target=lookup_tender, args=(token, merchant, payment_id, tender_id), daemon=True).start()
                        safe_payment['tenderLabel'] = tender_labels.get(tender_id)
                row['payments'].append(safe_payment)
            rows.append(row)
        by_id = {row['id']: row for row in rows}
        for device_row in device_rows:
            device = json.loads(device_row['payload'])
            cloud = by_id.get(device['id'])
            if cloud and cloud_matches_device(cloud, device):
                if not device_row['reconciled']:
                    with database() as db:
                        db.execute('UPDATE device_orders SET reconciled=1 WHERE order_id=? AND payload=?',
                                   (device['id'], device_row['payload']))
                cloud['source'] = 'cloud'
                cloud['seen_at'] = fetched_at
                cloud['reconciled'] = True
                continue
            if cloud and device_row['reconciled']:
                continue
            by_id[device['id']] = {**device, 'paymentState': 'OPEN', 'payments': [],
                                   'source': 'device', 'seen_at': device_row['received'],
                                   'reconciled': False}
        rows = sorted(by_id.values(), key=lambda row: row.get('seen_at', fetched_at), reverse=True)[:20]
        for row in rows:
            row.setdefault('source', 'cloud')
            row.setdefault('seen_at', fetched_at)
            row.setdefault('reconciled', True)
        return rows, max([fetched_at, *[row['received'] for row in device_rows]]), cloud_unavailable

    @app.get(PREFIX + '/orders')
    def orders():
        rows, fetched_at, cloud_unavailable = read_orders()
        return jsonify(environment='sandbox', fetched_at=fetched_at, orders=rows,
                       cloud_unavailable=cloud_unavailable,
                       note='Up to 20 current orders; amounts are Clover minor units. Refresh to read lifecycle changes. No Clover or POPCORE data is written.')

    @app.get(PREFIX + '/review')
    def review():
        rows, _, _ = read_orders()
        for order in rows:
            prices = []
            line_discount = 0
            item_order_discount = 0
            has_line_discount = False
            for item in order['items']:
                for key in ('discountAmount', 'orderLevelDiscountAmount'):
                    amount = item.get(key)
                    if amount not in (None, 0):
                        has_line_discount = True
                    if _safe_cents(amount):
                        if key == 'discountAmount':
                            line_discount += amount
                        else:
                            item_order_discount += amount
                if prices is None:
                    continue
                price = item.get('priceWithModifiersAndItemAndOrderDiscounts')
                if price is None:
                    price = item.get('price')
                if not _safe_cents(price):
                    prices = None
                    continue
                prices.append(price)
            subtotal = sum(prices) if prices is not None else None
            total = order.get('total')
            target = suggest_payable(total)
            order['hasDiscount'] = bool(order['discounts']) or has_line_discount
            order['lineDiscountCents'] = line_discount
            order['itemOrderDiscountCents'] = item_order_discount
            order['quote'] = (discount_quote(subtotal, total - subtotal, target)
                              if order.get('paymentState') == 'OPEN' and not order['payments']
                              and not order['hasDiscount'] and _safe_cents(total)
                              and _safe_cents(subtotal) and subtotal <= total and target else None)
            order['target'] = target
        return render_template_string('''<!doctype html><html lang="en"><meta charset="utf-8">
            <meta name="viewport" content="width=device-width,initial-scale=1"><title>Sandbox checkout review</title>
            <style>body{font:16px system-ui;max-width:900px;margin:32px auto;padding:0 20px;line-height:1.5}
            article{border:1px solid #ddd;border-radius:12px;padding:20px;margin:18px 0}.facts{display:flex;gap:24px;flex-wrap:wrap}
            .quote{background:#f4f7f4;border-radius:8px;padding:12px 16px}.quote strong{font-size:1.35rem;display:block}
            table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px;border-bottom:1px solid #eee}
            code{overflow-wrap:anywhere}a{color:#075a9c}.muted{color:#596273}</style>
            <p><a href="{{ prefix }}/">← Sandbox status</a></p><h1>Sandbox checkout review</h1>
            <p>Read-only. Enter any suggested discount manually in the Clover emulator. No POPCORE inventory is changed.</p>
            {% for order in orders %}<article><h2><code>{{ order.id }}</code></h2>
            <div class="facts"><p>Status <strong>{{ order.paymentState or order.state or 'Unknown' }}</strong></p>
            <p>Current Clover total <strong>{{ money(order.total) }}</strong></p></div>
            {% if order['quote'] %}<div class="quote"><span>Suggested customer payment</span><strong>{{ money(order['target']) }}</strong>
            <span>Pre-tax discount to enter in Clover</span><strong>{{ money(order['quote']['discountCents']) }}</strong>
            <small>Estimated Clover total: {{ money(order['quote']['predictedTotalCents']) }}. Confirm the total on Clover before payment.</small></div>
            {% elif order['hasDiscount'] %}<p class="quote"><strong>Discount entered in Clover</strong> This order will not receive another suggestion.</p>
            {% else %}<p class="muted">Waiting for a usable Clover total before calculating a discount.</p>{% endif %}
            <h3>Items</h3><table><tr><th>Item</th><th>Current price</th></tr>
            {% for item in order['items'] %}<tr><td>{{ item.name or item.itemCode or item.id }}</td><td>{{ money(item.priceWithModifiersAndItemAndOrderDiscounts if item.priceWithModifiersAndItemAndOrderDiscounts is not none else item.price) }}</td></tr>{% endfor %}</table>
            {% if order['discounts'] or order['lineDiscountCents'] or order['itemOrderDiscountCents'] %}<h3>Discounts</h3><ul>
            {% for discount in order['discounts'] %}<li>{{ discount.name or 'Discount' }} — {{ money(discount.amount) }}</li>{% endfor %}
            {% if order['lineDiscountCents'] %}<li>Line-item discounts — {{ money(order['lineDiscountCents']) }}</li>{% endif %}
            {% if order['itemOrderDiscountCents'] and not order['discounts'] %}<li>Order-level discount allocation — {{ money(order['itemOrderDiscountCents']) }}</li>{% endif %}</ul>{% endif %}
            {% if order['payments'] %}<h3>Payments</h3><ul>{% for payment in order['payments'] %}<li>{{ payment.tenderLabel or 'Tender' }} — {{ money(payment.amount) }} · {{ payment.result or 'Unknown' }}</li>{% endfor %}</ul>{% endif %}
            </article>{% else %}<p>No sandbox orders found yet.</p>{% endfor %}</html>''',
            prefix=PREFIX, orders=rows, money=money)

    @app.post(PREFIX + '/webhooks/<path_key>')
    def webhook(path_key):
        if not hmac.compare_digest(path_key, config['WEBHOOK_PATH_KEY']):
            abort(404)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400)
        if set(body) == {'verificationCode'} and not config.get('CLOVER_WEBHOOK_AUTH'):
            code = body['verificationCode']
            if not isinstance(code, str) or not 1 <= len(code) <= 256:
                abort(400)
            save('verification', code)
            return '', 200
        expected = config.get('CLOVER_WEBHOOK_AUTH', '')
        if not expected or not hmac.compare_digest(request.headers.get('X-Clover-Auth', ''), expected):
            abort(401)
        merchants = body.get('merchants')
        if body.get('appId') != config.get('CLOVER_APP_ID') or not isinstance(merchants, dict):
            abort(400)
        pending = []
        for merchant, events in merchants.items():
            if merchant != config.get('CLOVER_MERCHANT_ID') or not isinstance(events, list):
                abort(400)
            for event in events:
                if (not isinstance(event, dict) or not isinstance(event.get('objectId'), str)
                        or not re.fullmatch(r'[A-Z]{1,2}:[A-Za-z0-9]{13}', event['objectId'])
                        or event.get('type') not in ('CREATE', 'UPDATE', 'DELETE')
                        or type(event.get('ts')) is not int or event['ts'] < 0):
                    abort(400)
                pending.append((merchant, event['objectId'], event['type'], event['ts'], time.time()))
        with database() as db:
            db.executemany('INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?)', pending)
        return '', 200

    return app


def load_config(path):
    """Read a private JSON file, without importing the production environment."""
    return json.loads(Path(path).read_text())


def from_config():
    """Gunicorn factory; only the configuration path goes in the environment."""
    return create_app(load_config(os.environ['CLOVER_SANDBOX_CONFIG']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--port', type=int, default=5055)
    args = parser.parse_args()
    # Access logs would expose OAuth query codes and the private webhook URL.
    logging.getLogger('werkzeug').disabled = True
    create_app(load_config(args.config)).run(host='127.0.0.1', port=args.port, debug=False)
