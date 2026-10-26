"""Single-merchant Clover connector for POPCORE. No POPCORE imports.

It holds the merchant's OAuth tokens, reads orders (cloud, plus device snapshots for speed),
receives Clover webhooks, and performs the only Clover writes POPCORE makes: opening an order
for a sale POPCORE started (custom lines, its tax and reference) so card is taken on the device,
labelling or completing an order it settled (one POPCORE discount, the order total, its tenders
and a reference) and deleting an order nobody paid. Writes need the writer key as well as the
admin login.
"""
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
ENVIRONMENTS = {
    'sandbox': ('https://apisandbox.dev.clover.com', 'https://sandbox.dev.clover.com/oauth/v2/authorize'),
    'production': ('https://api.clover.com', 'https://www.clover.com/oauth/v2/authorize'),
}
IDENTIFIER = re.compile(r'[A-Za-z0-9]{13}\Z')
# The only Clover writes: a new order POPCORE started, one order of the configured merchant, its
# lines, POPCORE discount and tenders.
WRITE_PATH = re.compile(r'/v3/merchants/([A-Za-z0-9]{13})/orders(/[A-Za-z0-9]{13}'
                        r'(/payments|/discounts|/discounts/[A-Za-z0-9]{13}|/bulk_line_items)?)?\Z')
# A sale started in POPCORE: its checkout reference becomes the Clover order's external reference, which
# Clover ("Invoice ID") limits to 12 characters.
REFERENCE = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,11}\Z')
SENT_KEY = re.compile(r'popcore-[A-Za-z0-9]{1,12}-\d{1,15}\Z')  # POPCORE instance and checkout
MAX_SENT_UNITS = 100
PAYMENT_KEY = re.compile(r'popcore-[A-Za-z0-9]{1,12}-\d{1,15}-\d{1,15}\Z')
POPCORE_DISCOUNTS = ('Cash discount', 'Rounding')
# POPCORE tender -> Clover tender labelKey (cash) or the custom tender labels the merchant created.
POPCORE_TENDERS = {'cash': ('com.clover.tender.cash',), 'e_transfer': ('e-transfer', 'etransfer', 'interac e-transfer'),
                   'wechat': ('wechat pay', 'wechat', 'wechatpay'), 'alipay': ('alipay',)}
MAX_SAFE_INTEGER = 2**53 - 1
TAX_SCALE = 10_000_000  # Clover tax rates: 1000000 = 10%
# A label POPCORE puts on a Clover order it recorded as paid, such as "Paid 2026-10-20 11:23".
LABEL = re.compile(r'[ -~]{1,40}')
TENDER_TTL = 600
ORDER_TTL = 2
# The companion re-sends open orders every 2 s: a device copy older than this is no longer ahead of the cloud.
DEVICE_FRESH = 30


class CloverError(Exception):
    def __init__(self, message, retry_after=None, status=None):
        super().__init__(message)
        self.retry_after = retry_after
        self.status = status


class Unsupported(Exception):
    """Clover's total can't be reproduced exactly for this order."""


def _safe_cents(value):
    return type(value) is int and 0 <= value <= MAX_SAFE_INTEGER


def valid_reference(reference):
    """POPCORE's label for a settled order: an optional POPCORE-<n> reference and an optional title."""
    if reference is None:
        return True
    if not isinstance(reference, dict):
        return False
    number, title = reference.get('external_reference_id'), reference.get('title')
    if number is not None and not re.fullmatch(r'POPCORE-\d{1,15}', str(number)):
        return False
    if title is not None and not (isinstance(title, str) and LABEL.fullmatch(title)):
        return False
    return number is not None or title is not None


def _half_up(numerator, denominator):
    return (2 * numerator + denominator) // (2 * denominator)


def money(cents):
    return '${}.{:02d}'.format(cents // 100, cents % 100) if _safe_cents(cents) else '—'


def t_enabled(tender):
    return tender.get('enabled') is not False and tender.get('visible') is not False


def clover_calc(lines, discounts, extra=0):
    """Clover's documented order total (docs.clover.com "Calculate order totals").

    Line prices, line discounts (percentage, then amount), order discounts (percentage per line,
    then amounts allocated in proportion), tax per tax-rate object on the combined discounted
    amount of the lines sharing it, half up. `extra` is a further order amount discount.
    Returns (total, tax, discounted subtotal). Raises Unsupported when that is not exact."""
    amounts, rates = [], []
    for line in lines:
        price = line.get('price')
        if (not _safe_cents(price) or line.get('unitQty') not in (None, 0, 1000) or line.get('refunded')
                or line.get('exchanged') or line.get('modifications')):
            raise Unsupported('modified, weighted or refunded line')
        amount = price
        line_discounts = line.get('discounts') or []
        for discount in line_discounts:
            if type(discount.get('percentage')) is int and discount['percentage'] > 0:
                amount -= _half_up(price * discount['percentage'], 100)
        for discount in line_discounts:
            if not discount.get('percentage'):
                if type(discount.get('amount')) is not int:
                    raise Unsupported('line discount without amount')
                amount += discount['amount'] if discount['amount'] < 0 else -discount['amount']
        amounts.append(max(0, amount))
        rates.append(tuple(sorted((rate['id'], rate['rate']) for rate in line.get('taxRates') or [])))
    percentages = [d for d in discounts if d.get('percentage')]
    if len(percentages) > 1:
        raise Unsupported('several percentage discounts')
    for discount in percentages:
        if type(discount['percentage']) is not int or discount['percentage'] < 0:
            raise Unsupported('percentage discount')
        amounts = [a - _half_up(a * discount['percentage'], 100) for a in amounts]
    off = extra
    for discount in discounts:
        if not discount.get('percentage'):
            if type(discount.get('amount')) is not int:
                raise Unsupported('order discount without amount')
            off += abs(discount['amount'])
    subtotal = sum(amounts)
    off = min(off, subtotal)
    discounted = subtotal - off
    groups = {}
    for amount, line_rates in zip(amounts, rates):
        for rate in line_rates:
            groups.setdefault(rate, 0)
            groups[rate] += amount
    tax = 0
    for (rate_id, rate), taxable in groups.items():
        if type(rate) is not int or rate < 0:
            raise Unsupported('tax rate')
        if subtotal:
            tax += _half_up(taxable * discounted * rate, subtotal * TAX_SCALE)
    if len(groups) > 1 and off and subtotal:
        # Clover may allocate the amount discount per line before taxing; both readings must agree.
        allocated, left = [], off
        for index, amount in enumerate(amounts):
            share = left if index == len(amounts) - 1 else _half_up(off * amount, subtotal)
            share = min(share, left)
            allocated.append(amount - share)
            left -= share
        per_line = {}
        for amount, line_rates in zip(allocated, rates):
            for rate in line_rates:
                per_line[rate] = per_line.get(rate, 0) + amount
        if sum(_half_up(v * rate[1], TAX_SCALE) for rate, v in per_line.items()) != tax:
            raise Unsupported('tax groups disagree')
    return discounted + tax, tax, discounted


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
                'unitName', 'discountAmount', 'orderLevelDiscountAmount',
                'itemCode', 'itemId')}
        if any(safe[key] is not None and (type(safe[key]) is not int or abs(safe[key]) > MAX_SAFE_INTEGER)
               for key in ('discountAmount', 'orderLevelDiscountAmount')) or any(
                safe[key] is not None and not _safe_cents(safe[key])
                for key in ('price', 'priceWithModifiersAndItemAndOrderDiscounts')):
            raise ValueError('Invalid device line-item amount')
        if safe['unitQty'] is not None and (type(safe['unitQty']) is not int or
                                            not 0 <= safe['unitQty'] <= 1_000_000_000):
            raise ValueError('Invalid device quantity')
        if safe['unitName'] is not None and (not isinstance(safe['unitName'], str) or
                                             len(safe['unitName']) > 40):
            raise ValueError('Invalid device unit')
        # Barcode and catalogue item reference identify the product; never customer data.
        if safe['itemCode'] is not None and (not isinstance(safe['itemCode'], str) or
                                             len(safe['itemCode']) > 80):
            raise ValueError('Invalid device item code')
        if safe['itemId'] is not None and not IDENTIFIER.fullmatch(str(safe['itemId'])):
            raise ValueError('Invalid device item reference')
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
        return (item.get('id'), item.get('unitQty'), item.get('price'))
    def discount_key(item):
        return (item.get('id'), item.get('amount'), item.get('percentage'))
    return ({item_key(item) for item in cloud['items']} == {item_key(item) for item in device['items']}
            and {discount_key(d) for d in cloud['discounts']} == {discount_key(d) for d in device['discounts']})


def create_app(config):
    config = dict(config)
    environment = config.get('CLOVER_ENVIRONMENT', 'sandbox')
    if environment not in ENVIRONMENTS:
        raise ValueError('CLOVER_ENVIRONMENT must be sandbox or production.')
    api, authorize = ENVIRONMENTS[environment]
    public = config['PUBLIC_URL'].rstrip('/')
    url = urlsplit(public)
    if (url.path != PREFIX or url.query or url.fragment or url.username or url.password
            or not url.hostname or (url.scheme != 'https' and not (
                url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1')))):
        raise ValueError('PUBLIC_URL must be HTTPS ending in /clover-sandbox (HTTP loopback is allowed).')
    for key in ('SESSION_SECRET', 'ADMIN_PASSWORD', 'WEBHOOK_PATH_KEY'):
        if len(config.get(key, '')) < 32:
            raise ValueError(key + ' must contain at least 32 characters.')
    writer_key = config.get('POPCORE_WRITER_KEY', '')
    if writer_key and len(writer_key) < 32:
        raise ValueError('POPCORE_WRITER_KEY must contain at least 32 characters.')
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
            CREATE TABLE IF NOT EXISTS sent_orders (
                key TEXT PRIMARY KEY, reference TEXT NOT NULL, order_id TEXT UNIQUE, created REAL NOT NULL);
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
            raise CloverError('Invalid token response; reconnect to Clover.')
        save('tokens', json.dumps({k: tokens[k] for k in (
            'access_token', 'refresh_token', 'access_token_expiration', 'refresh_token_expiration')
            if k in tokens}))

    rate_lock = threading.Lock()
    retry_at = 0
    rate_failures = 0
    merchant = config.get('CLOVER_MERCHANT_ID', '')

    def clover(method, path, **kwargs):
        nonlocal retry_at, rate_failures
        # Token exchanges, plus POPCORE settlement writes to orders of the configured merchant.
        # There is deliberately no generic write API.
        write = WRITE_PATH.fullmatch(path)
        if method != 'GET' and path not in ('/oauth/v2/token', '/oauth/v2/refresh') and not (
                method in ('POST', 'DELETE') and write and write.group(1) == merchant):
            raise ValueError('The connector only writes POPCORE settlement to a configured merchant order.')
        with rate_lock:
            remaining = retry_at - time.monotonic()
            if remaining > 0:
                raise CloverError('Clover is limiting requests. Waiting before retrying.', math.ceil(remaining))
        try:
            result = requests.request(method, api + path, timeout=(5, 15), allow_redirects=False, **kwargs)
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
                raise CloverError('Clover is limiting requests. Waiting before retrying.', delay, 429)
            if result.status_code == 404:
                raise CloverError('Clover has no such order.', status=404)
            if result.status_code != 200:
                # Only Clover's own short reason is shown, never the rest of its response.
                try:
                    reason = result.json().get('message') if result.content else None
                except (ValueError, AttributeError):
                    reason = None
                reason = re.sub(r'[^ -~]', '', reason).strip()[:160] if isinstance(reason, str) else ''
                raise CloverError('Clover returned HTTP ' + str(result.status_code) + '. ' +
                                  (f'Clover says: {reason}' if reason else 'Check permissions or reconnect.'),
                                  status=result.status_code)
            payload = result.json() if result.content else {}
            if isinstance(payload, list):  # bulk line items answer with the created lines
                payload = {'elements': payload}
            if not isinstance(payload, dict):
                raise ValueError('Expected an object')
            with rate_lock:
                if time.monotonic() >= retry_at:
                    rate_failures = 0
            return payload
        except (requests.RequestException, ValueError):
            raise CloverError('Clover could not be read. Retry or reconnect.') from None

    # ponytail: one process serializes refresh-token rotation; use a cross-process lock before scaling.
    token_lock = threading.Lock()

    def access_token():
        with token_lock:
            tokens = json.loads(setting('tokens') or '{}')
            if not tokens.get('access_token'):
                raise CloverError('Connect the Clover merchant first.')
            if tokens.get('access_token_expiration', 0) <= time.time() + 60:
                if not tokens.get('refresh_token') or tokens.get('refresh_token_expiration', 0) <= time.time():
                    raise CloverError('Clover authorization expired. Reconnect the merchant.')
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

    def require_writer():
        supplied = request.headers.get('X-Popcore-Writer', '')
        if not writer_key:
            abort(404)
        if not hmac.compare_digest(supplied.encode(), writer_key.encode()):
            abort(403)

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
                                 'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"})
        return response

    @app.errorhandler(CloverError)
    def provider_error(error):
        response = jsonify(error=str(error), code='not_found' if error.status == 404 else 'clover_unavailable')
        response.status_code = 404 if error.status == 404 else 502
        if error.retry_after is not None:
            response.headers['Retry-After'] = str(error.retry_after)
        return response

    def conflict(code, message, **facts):
        return jsonify(error=message, code=code, **facts), 409

    def missing():
        return [k for k in ('CLOVER_APP_ID', 'CLOVER_APP_SECRET', 'CLOVER_MERCHANT_ID') if not config.get(k)]

    @app.get(PREFIX + '/health')
    def health():
        return jsonify(status='ok', environment=environment)

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
        if value.get('merchantId') != merchant or value.get('deviceId') != device_id:
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
            <meta name="viewport" content="width=device-width,initial-scale=1"><title>POPCORE Clover connector</title>
            <style>body{font:17px system-ui;max-width:850px;margin:32px auto;padding:0 20px;line-height:1.5}
            code,pre{overflow-wrap:anywhere;white-space:pre-wrap}table{width:100%;border-collapse:collapse}
            th,td{text-align:left;padding:8px;border-bottom:1px solid #ddd}a{color:#075a9c}</style>
            <h1>POPCORE · Clover {{ environment }}</h1>
            <p>{{ 'Connected to the configured merchant.' if connected else 'Not connected to Clover yet.' }}
            POPCORE writes: {{ 'enabled' if writer else 'off (no writer key)' }}.</p>
            {% if missing %}<p>Local configuration still needed: {{ missing|join(', ') }}.</p>{% endif %}
            <p><a href="{{ prefix }}/connect">Connect the Clover merchant</a> ·
            <a href="{{ prefix }}/orders">Read current orders (JSON)</a></p>
            <h2>Clover dashboard settings</h2>
            <p>Site URL: <code>{{ public }}/</code></p>
            <p>Alternate Launch Path: <code>{{ prefix }}/connect</code></p>
            <p>OAuth response: <strong>Code</strong>. CORS domain: blank.</p>
            <p>Webhook URL (keep private): <code>{{ public }}/webhooks/{{ hook }}</code></p>
            <p>Webhook verification code: <code>{{ verification or 'Not received yet' }}</code></p>
            <p>Webhook authentication: {{ 'configured' if hook_auth else 'not configured; event delivery is blocked' }}.</p>
            <h2>Latest notifications</h2><p>Reload this page after changing an order.
            Notification time includes Clover delivery delay; it is not device scan latency.</p>
            <table><tr><th>Object</th><th>Change</th><th>Clover timestamp (ms)</th></tr>
            {% for e in events %}<tr><td>{{ e.object_id }}</td><td>{{ e.kind }}</td><td>{{ e.source_time }}</td></tr>
            {% endfor %}</table></html>''', prefix=PREFIX, public=public, missing=missing(),
            connected=bool(setting('tokens')), events=events, hook=config['WEBHOOK_PATH_KEY'],
            verification=setting('verification'), hook_auth=bool(config.get('CLOVER_WEBHOOK_AUTH')),
            environment=environment, writer=bool(writer_key))

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
        return redirect(authorize + '?' + urlencode({
            'merchant_id': merchant, 'client_id': config['CLOVER_APP_ID'], 'response_type': 'code',
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
        callback_merchant = request.args.get('merchant_id') or request.args.get('merchantId')
        code = request.args.get('code', '')
        if callback_merchant != merchant or not code or len(code) > 4096:
            abort(400)
        save_tokens(clover('POST', '/oauth/v2/token', json={
            'client_id': config['CLOVER_APP_ID'], 'client_secret': config['CLOVER_APP_SECRET'], 'code': code}))
        return redirect(PREFIX + '/')

    # Tender names change rarely: one cached list instead of a lookup per payment.
    tender_lock = threading.Lock()
    tender_cache = {'at': 0.0, 'items': None, 'running': False, 'retry_at': 0.0}

    def load_tenders(token):
        payload = clover('GET', f'/v3/merchants/{merchant}/tenders', headers={'Authorization': 'Bearer ' + token})
        items = {t['id']: {k: t.get(k) for k in ('id', 'label', 'labelKey', 'enabled', 'visible')}
                 for t in payload.get('elements', []) if isinstance(t, dict) and IDENTIFIER.fullmatch(str(t.get('id', '')))}
        with tender_lock:
            tender_cache.update(at=time.monotonic(), items=items)
        return items

    def refresh_tenders_later(token):
        def run():
            try:
                load_tenders(token)
            except CloverError:
                with tender_lock:
                    tender_cache['retry_at'] = time.monotonic() + 30
            finally:
                with tender_lock:
                    tender_cache['running'] = False
        with tender_lock:
            due = (tender_cache['items'] is None or time.monotonic() - tender_cache['at'] > TENDER_TTL)
            if not due or tender_cache['running'] or time.monotonic() < tender_cache['retry_at']:
                return tender_cache['items'] or {}
            tender_cache['running'] = True
        threading.Thread(target=run, daemon=True).start()
        with tender_lock:
            return tender_cache['items'] or {}

    def tenders_now(token):
        with tender_lock:
            fresh = tender_cache['items'] is not None and time.monotonic() - tender_cache['at'] <= TENDER_TTL
            items = tender_cache['items']
        return items if fresh else load_tenders(token)

    # One connector worker shares the same sample across POPCORE, the companion and diagnostics.
    orders_lock = threading.Lock()
    orders_condition = threading.Condition(orders_lock)
    orders_payload = None
    orders_fetched_at = 0
    orders_next_read = 0
    orders_error = False
    orders_failure = None
    orders_running = False
    orders_token = None
    order_cache = {}
    order_locks = {}

    def order_lock(order_id):
        with orders_lock:
            return order_locks.setdefault(order_id, threading.Lock())

    ORDER_EXPAND = 'lineItems,discounts,payments,refunds,credits,employee'

    def safe_row(order, tenders):
        """Only the order facts POPCORE needs; never customer, card or token data."""
        row = {k: order.get(k) for k in (
            'id', 'total', 'currency', 'paymentState', 'state', 'testMode', 'createdTime',
            'clientCreatedTime', 'modifiedTime', 'unpaidBalance', 'manualTransaction', 'payType',
            'title', 'externalReferenceId')}
        row['orderTypeId'] = (order.get('orderType') or {}).get('id')
        row['employeeId'] = (order.get('employee') or {}).get('id')
        row['employeeName'] = (order.get('employee') or {}).get('name')
        row['items'] = [{**{k: item.get(k) for k in (
            'id', 'itemCode', 'name', 'price', 'priceWithModifiersAndItemAndOrderDiscounts',
            'unitQty', 'unitName', 'discountAmount', 'orderLevelDiscountAmount', 'refunded',
            'isRevenue', 'createdTime')}, 'itemId': (item.get('item') or {}).get('id')}
            for item in (order.get('lineItems') or {}).get('elements', [])]
        row['discounts'] = [{k: discount.get(k) for k in ('id', 'name', 'amount', 'percentage')}
                            for discount in (order.get('discounts') or {}).get('elements', [])]
        row['payments'] = []
        for payment in (order.get('payments') or {}).get('elements', []):
            safe_payment = {k: payment.get(k) for k in (
                'id', 'amount', 'tipAmount', 'taxAmount', 'result', 'createdTime',
                'modifiedTime', 'offline', 'externalPaymentId')}
            tender = payment.get('tender') or {}
            known = tenders.get(tender.get('id')) or {}
            safe_payment['tenderId'] = tender.get('id')
            safe_payment['tenderLabel'] = tender.get('label') or known.get('label')
            safe_payment['tenderLabelKey'] = tender.get('labelKey') or known.get('labelKey')
            safe_payment['employeeId'] = (payment.get('employee') or {}).get('id')
            row['payments'].append(safe_payment)
        row['refunds'] = [{'id': r.get('id'), 'amount': r.get('amount'),
                           'paymentId': (r.get('payment') or {}).get('id')}
                          for r in (order.get('refunds') or {}).get('elements', []) if isinstance(r, dict)]
        row['credits'] = [{'id': c.get('id'), 'amount': c.get('amount')}
                          for c in (order.get('credits') or {}).get('elements', []) if isinstance(c, dict)]
        return row

    def check_payment_ids(order):
        for payment in (order.get('payments') or {}).get('elements', []):
            if not IDENTIFIER.fullmatch(str(payment.get('id', ''))):
                raise CloverError('Clover returned an invalid payment identifier.')

    def refresh_cloud():
        nonlocal orders_payload, orders_fetched_at, orders_next_read, orders_error
        nonlocal orders_failure, orders_running, orders_token
        try:
            token = access_token()
            payload = clover('GET', '/v3/merchants/' + merchant + '/orders',
                             headers={'Authorization': 'Bearer ' + token},
                             params={'limit': 20, 'orderBy': 'modifiedTime DESC', 'expand': ORDER_EXPAND})
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
        nonlocal orders_running
        if not IDENTIFIER.fullmatch(merchant):
            raise CloverError('Set the Clover merchant ID first.')
        with database() as db:
            device_rows = db.execute('SELECT order_id,payload,received,reconciled '
                                     'FROM device_orders ORDER BY received DESC LIMIT 20').fetchall()
        with orders_condition:
            start = not orders_running and time.monotonic() >= orders_next_read
            if start:
                orders_running = True
                if device_rows:
                    threading.Thread(target=refresh_cloud, daemon=True).start()
            elif orders_running and not device_rows:
                orders_condition.wait_for(lambda: not orders_running, timeout=20)
        if start and not device_rows:
            refresh_cloud()
        with orders_condition:
            if orders_payload is None and not device_rows:
                raise orders_failure or CloverError('Clover could not be read. Retry or reconnect.')
            payload = orders_payload or {'elements': []}
            fetched_at, token = orders_fetched_at, orders_token
            cloud_unavailable = orders_error or orders_payload is None
        unlabelled = any(not (payment.get('tender') or {}).get('label')
                         for order in payload.get('elements', [])
                         for payment in (order.get('payments') or {}).get('elements', []))
        tenders = refresh_tenders_later(token) if token and unlabelled else {}
        rows = []
        for order in payload.get('elements', []):
            # One malformed order must not hide the others.
            try:
                check_payment_ids(order)
            except CloverError:
                continue
            rows.append(safe_row(order, tenders))
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
            if cloud and (device_row['reconciled'] or time.time() - device_row['received'] > DEVICE_FRESH):
                continue
            by_id[device['id']] = {**device, 'paymentState': 'OPEN', 'payments': [], 'refunds': [],
                                   'credits': [], 'source': 'device', 'seen_at': device_row['received'],
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
        return jsonify(environment=environment, merchant_id=merchant, fetched_at=fetched_at, orders=rows,
                       cloud_unavailable=cloud_unavailable,
                       note='Up to 20 current orders; amounts are Clover minor units.')

    @app.get(PREFIX + '/orders/scan')
    def scan():
        """Every order created in [since, until), oldest first, for store-day closing."""
        try:
            since, until = int(request.args['since']), int(request.args['until'])
            offset = int(request.args.get('offset', 0))
        except (KeyError, ValueError):
            abort(400)
        if not 0 <= since < until or not 0 <= offset <= 10_000:
            abort(400)
        token = access_token()
        payload = clover('GET', f'/v3/merchants/{merchant}/orders',
                         headers={'Authorization': 'Bearer ' + token},
                         params=[('filter', f'createdTime>={since}'), ('filter', f'createdTime<{until}'),
                                 ('orderBy', 'createdTime ASC'), ('limit', 100), ('offset', offset),
                                 ('expand', ORDER_EXPAND)])
        tenders = tenders_now(token)
        rows = []
        for order in payload.get('elements', []):
            try:
                check_payment_ids(order)
            except CloverError:
                continue
            row = safe_row(order, tenders)
            row.update(source='cloud', seen_at=time.time(), reconciled=True)
            rows.append(row)
        return jsonify(environment=environment, merchant_id=merchant, fetched_at=time.time(), orders=rows,
                       more=len(payload.get('elements', [])) == 100)

    def fetch_order(order_id, *, fresh=False):
        """(raw Clover order, line items with tax rates), cached briefly per order."""
        cached = order_cache.get(order_id)
        if cached and not fresh and time.monotonic() - cached[0] <= ORDER_TTL:
            return cached[1], cached[2]
        token = access_token()
        auth = {'Authorization': 'Bearer ' + token}
        base = f'/v3/merchants/{merchant}/orders/{order_id}'
        order = clover('GET', base, headers=auth, params={'expand': ORDER_EXPAND})
        check_payment_ids(order)
        lines = clover('GET', base + '/line_items', headers=auth,
                       params={'expand': 'taxRates,modifications,discounts', 'limit': 200}).get('elements', [])
        order_cache[order_id] = (time.monotonic(), order, lines)
        return order, lines

    def calc_lines(lines):
        return [{'price': line.get('price'), 'unitQty': line.get('unitQty'), 'refunded': line.get('refunded'),
                 'exchanged': line.get('exchanged'),
                 'modifications': (line.get('modifications') or {}).get('elements', []),
                 'discounts': (line.get('discounts') or {}).get('elements', []),
                 'taxRates': [{'id': r.get('id'), 'rate': r.get('rate')}
                              for r in (line.get('taxRates') or {}).get('elements', [])]}
                for line in lines]

    def verify_pricing(order, lines):
        """Can POPCORE reproduce Clover's current total? Only then may it set a new one."""
        discounts = (order.get('discounts') or {}).get('elements', [])
        if order.get('serviceCharge') or order.get('isVat'):
            return {'verified': False, 'reason': 'Service charge or VAT pricing'}
        foreign = [d for d in discounts if d.get('name') not in POPCORE_DISCOUNTS]
        try:
            total, tax, _ = clover_calc(calc_lines(lines), foreign)
        except Unsupported as error:
            return {'verified': False, 'reason': str(error)}
        if total != order.get('total') and not any(d.get('name') in POPCORE_DISCOUNTS for d in discounts):
            return {'verified': False, 'reason': 'Clover total does not match its items'}
        return {'verified': True, 'tax_cents': tax}

    @app.get(PREFIX + '/orders/<order_id>')
    def one_order(order_id):
        if not IDENTIFIER.fullmatch(order_id):
            abort(400)
        order, lines = fetch_order(order_id)
        row = safe_row(order, refresh_tenders_later(access_token()))
        row.update(source='cloud', seen_at=time.time(), reconciled=True)
        pricing = verify_pricing(order, lines)
        with database() as db:
            device_row = db.execute('SELECT payload,received,reconciled FROM device_orders WHERE order_id=?',
                                    (order_id,)).fetchone()
        if device_row and not device_row['reconciled']:
            device = json.loads(device_row['payload'])
            if cloud_matches_device(row, device):
                with database() as db:
                    db.execute('UPDATE device_orders SET reconciled=1 WHERE order_id=? AND payload=?',
                               (order_id, device_row['payload']))
            elif time.time() - device_row['received'] <= DEVICE_FRESH:
                # Same rule as the order list: the device is ahead of Clover's cloud copy, so its items win.
                row = {**device, 'paymentState': 'OPEN', 'payments': [], 'refunds': [], 'credits': [],
                       'source': 'device', 'seen_at': device_row['received'], 'reconciled': False}
                pricing = {'verified': False, 'reason': "The Clover device is ahead of Clover's cloud copy."}
        return jsonify(environment=environment, merchant_id=merchant, fetched_at=time.time(), order=row,
                       pricing=pricing)

    @app.post(PREFIX + '/orders/<order_id>/settle')
    def settle(order_id):
        """Complete an order POPCORE settled: its discount, Clover total, reference and tenders.

        Idempotent: tenders carry POPCORE's key as externalPaymentId, an identical POPCORE
        discount is reused, and the total and reference are plain field updates. Refuses when the
        order no longer looks the way POPCORE priced it."""
        nonlocal orders_next_read
        require_writer()
        body = request.get_json(silent=True)
        if not IDENTIFIER.fullmatch(order_id) or not IDENTIFIER.fullmatch(merchant) or not isinstance(body, dict):
            abort(400)
        target, payments, name = body.get('target_total'), body.get('payments'), body.get('discount_name')
        expected, reference = body.get('expected'), body.get('reference')
        if (not _safe_cents(target) or not isinstance(payments, list) or len(payments) > 10
                or name not in (None, *POPCORE_DISCOUNTS) or not isinstance(expected, dict)
                or not valid_reference(reference)):
            abort(400)
        for payment in payments:
            if not (isinstance(payment, dict) and isinstance(payment.get('key'), str)
                    and PAYMENT_KEY.fullmatch(payment['key']) and payment.get('tender') in POPCORE_TENDERS
                    and _safe_cents(payment.get('amount')) and payment['amount'] > 0):
                abort(400)
        if len({payment['key'] for payment in payments}) != len(payments):
            abort(400)
        with order_lock(order_id):
            token = access_token()
            auth = {'Authorization': 'Bearer ' + token}
            base = f'/v3/merchants/{merchant}/orders/{order_id}'
            order, lines = fetch_order(order_id, fresh=True)
            discounts = (order.get('discounts') or {}).get('elements', [])
            ours = [d for d in discounts if d.get('name') in POPCORE_DISCOUNTS]
            foreign = [d for d in discounts if d.get('name') not in POPCORE_DISCOUNTS]
            clover_payments = [p for p in (order.get('payments') or {}).get('elements', [])
                               if not str(p.get('externalPaymentId') or '').startswith('popcore-')]
            written = {p.get('externalPaymentId'): p for p in (order.get('payments') or {}).get('elements', [])
                       if str(p.get('externalPaymentId') or '').startswith('popcore-')}
            def quantity(value):
                return None if value in (None, 0, 1000) else value
            now_facts = {
                'items': sorted([line.get('id'), line.get('price'), quantity(line.get('unitQty'))]
                                for line in (order.get('lineItems') or {}).get('elements', [])),
                'discounts': sorted([d.get('id'), d.get('amount'), d.get('percentage')] for d in foreign),
                'payments': sorted([p.get('id'), p.get('amount')] for p in clover_payments
                                   if p.get('result') == 'SUCCESS'),
            }
            supplied = {key: sorted(expected.get(key) or []) for key in now_facts}
            supplied['items'] = sorted([i[0], i[1], quantity(i[2])] for i in supplied['items'] if len(i) == 3)
            if supplied != now_facts:
                return conflict('clover_changed', 'The order changed on Clover after POPCORE priced it.',
                                current=now_facts)
            paid_on_clover = sum(p.get('amount') or 0 for p in clover_payments if p.get('result') == 'SUCCESS')
            received = sum(p['amount'] for p in payments)
            if paid_on_clover + received != target:
                return conflict('amounts_differ', 'POPCORE tenders and Clover payments do not add up to the target.')
            existing_tax = sum(p.get('taxAmount') or 0 for p in clover_payments if p.get('result') == 'SUCCESS')
            discount, tax = None, None
            if payments:
                try:
                    calc_input = calc_lines(lines)
                    base_total, base_tax, discounted = clover_calc(calc_input, foreign)
                except Unsupported as error:
                    return conflict('calc_unverified', f'Clover pricing is not exact here ({error}).')
                if not ours and base_total != order.get('total'):
                    return conflict('calc_unverified', 'Clover total does not match its items.')
                if target > base_total:
                    return conflict('overpaid', 'More money than the Clover order.', clover_total=base_total)
                if target == base_total or name is None:
                    if target != base_total:
                        return conflict('discount_required', 'A discount is needed but none was named.')
                    planned, total, tax = 0, base_total, base_tax
                else:
                    low, high = 0, discounted
                    while low < high:
                        middle = (low + high) // 2
                        if clover_calc(calc_input, foreign, middle)[0] <= target:
                            high = middle
                        else:
                            low = middle + 1
                    planned = low
                    total, tax, _ = clover_calc(calc_input, foreign, planned)
                if target - total > 5 or total < paid_on_clover:
                    return conflict('unreachable', 'Clover cannot land on this amount.', clover_total=total)
                # Clover must never show more money than POPCORE received: the largest tender
                # carries the cent difference Clover's tax rounding leaves.
                adjust = sorted(range(len(payments)), key=lambda i: payments[i]['amount'])[-1]
                amounts = [p['amount'] - ((target - total) if i == adjust else 0) for i, p in enumerate(payments)]
                if min(amounts) <= 0:
                    return conflict('unreachable', 'Rounding would leave a tender at zero.')
                # Resolve every tender before the first write, so a missing one changes nothing.
                tenders, tender_ids = tenders_now(token), {}
                for payment in payments:
                    names = POPCORE_TENDERS[payment['tender']]
                    tender = next((t for t in tenders.values() if (t.get('labelKey') in names or
                                   str(t.get('label') or '').casefold() in names)), None)
                    if payment['key'] not in written and (tender is None or not t_enabled(tender)):
                        return conflict('tender_missing', 'Create and enable a "' + (
                            'Cash' if payment['tender'] == 'cash' else names[0].title()) + '" tender on Clover first.')
                    tender_ids[payment['key']] = tender['id'] if tender else None
                for existing in ours:
                    if planned and existing.get('name') == name and abs(existing.get('amount') or 0) == planned:
                        discount = existing
                    else:
                        clover('DELETE', f"{base}/discounts/{existing['id']}", headers=auth)
                if planned and discount is None:
                    created = clover('POST', base + '/discounts', headers=auth,
                                     json={'name': name, 'amount': -planned})
                    discount = {'id': created.get('id'), 'name': name, 'amount': -planned}
                update = {'total': total}
            else:
                total = order.get('total')
                update = {}
            update = {key: value for key, value in update.items() if order.get(key) != value}
            if update:
                clover('POST', base, headers=auth, json=update)
            # The POPCORE number is a label: Clover refuses a new external reference once the order has
            # a payment, and a refused label never stops the order being completed.
            label = {}
            if reference:
                if reference.get('external_reference_id') and not any(
                        p.get('result') == 'SUCCESS' for p in clover_payments):
                    label['externalReferenceId'] = reference['external_reference_id']
                if reference.get('title'):
                    label['title'] = reference['title']
            label = {key: value for key, value in label.items() if order.get(key) != value}
            if label:
                try:
                    clover('POST', base, headers=auth, json=label)
                except CloverError as error:
                    if error.status != 400:
                        raise
            note = (reference or {}).get('title') or 'Recorded in POPCORE' + (
                f" ({reference['external_reference_id']})" if (reference or {}).get('external_reference_id') else '')
            results = []
            if payments:
                tax_left = max(0, tax - existing_tax)
                share_base = sum(amounts)
                for index, (payment, amount) in enumerate(zip(payments, amounts)):
                    share = tax_left if index == len(payments) - 1 else _half_up(tax_left * amount, share_base)
                    share = min(share, tax_left)
                    tax_left -= share
                    if payment['key'] in written:
                        results.append({'key': payment['key'], 'id': written[payment['key']].get('id'),
                                        'amount': written[payment['key']].get('amount'),
                                        'taxAmount': written[payment['key']].get('taxAmount')})
                        continue
                    created = clover('POST', base + '/payments', headers=auth, json={
                        'amount': amount, 'tender': {'id': tender_ids[payment['key']]},
                        'externalPaymentId': payment['key'], 'result': 'SUCCESS',
                        'note': note, 'taxAmount': share})
                    results.append({'key': payment['key'], 'id': created.get('id'), 'amount': amount,
                                    'taxAmount': share})
            order, _ = fetch_order(order_id, fresh=True)
            with orders_condition:
                orders_next_read = 0
            success = sum(p.get('amount') or 0 for p in (order.get('payments') or {}).get('elements', [])
                          if p.get('result') == 'SUCCESS')
            paid = order.get('paymentState') == 'PAID' and success == order.get('total')
            if payments and not paid:
                return conflict('not_paid', 'Clover did not record the order as paid.',
                                clover_total=order.get('total'), paid_cents=success)
            return jsonify(total=order.get('total'), paymentState=order.get('paymentState'), paid=paid,
                           discount=discount, tax_cents=tax, difference_cents=target - (order.get('total') or 0)
                           if payments else 0, payments=results,
                           externalReferenceId=order.get('externalReferenceId'))

    @app.post(PREFIX + '/orders/<order_id>/label')
    def label_order(order_id):
        """Mark an order POPCORE recorded as paid without paying it on Clover: its title and note.

        Idempotent: nothing is written when the order already carries the label."""
        nonlocal orders_next_read
        require_writer()
        body = request.get_json(silent=True)
        text = body.get('label') if isinstance(body, dict) else None
        if not IDENTIFIER.fullmatch(order_id) or not IDENTIFIER.fullmatch(merchant) or not (
                isinstance(text, str) and LABEL.fullmatch(text)):
            abort(400)
        with order_lock(order_id):
            auth = {'Authorization': 'Bearer ' + access_token()}
            base = f'/v3/merchants/{merchant}/orders/{order_id}'
            order, _ = fetch_order(order_id, fresh=True)
            note = order.get('note') or ''
            update = {} if order.get('title') == text else {'title': text}
            if text not in note and len(f'{note}\n{text}' if note else text) <= 255:
                update['note'] = f'{note}\n{text}' if note else text
            if update:
                try:
                    clover('POST', base, headers=auth, json=update)
                except CloverError as error:
                    # Clover may keep a note it will not change; the title alone still marks the order.
                    if error.status != 400 or 'note' not in update or 'title' not in update:
                        raise
                    clover('POST', base, headers=auth, json={'title': text})
            order_cache.pop(order_id, None)
            with orders_condition:
                orders_next_read = 0
            return jsonify(title=text, written=sorted(update), paymentState=order.get('paymentState'),
                           total=order.get('total'))

    def sent_order_body(body):
        """A POPCORE-started order: its reference, lines (name, price, quantity), discount and total."""
        if not isinstance(body, dict):
            return None
        key, reference, lines, title = body.get('key'), body.get('reference'), body.get('lines'), body.get('title')
        discount, expected, tax_rate = body.get('discount'), body.get('expected_total'), body.get('tax_rate')
        if (not isinstance(key, str) or not SENT_KEY.fullmatch(key)
                or type(tax_rate) is not int or not 0 < tax_rate < TAX_SCALE
                or not isinstance(reference, str) or not REFERENCE.fullmatch(reference)
                or not (title is None or isinstance(title, str) and LABEL.fullmatch(title))
                or not isinstance(lines, list) or not lines or not _safe_cents(expected)):
            return None
        units = []
        for line in lines:
            if not (isinstance(line, dict) and isinstance(line.get('name'), str)
                    and 0 < len(line['name'].strip()) <= 127 and '\n' not in line['name']
                    and _safe_cents(line.get('price')) and line['price'] > 0
                    and type(line.get('quantity')) is int and 0 < line['quantity'] <= MAX_SENT_UNITS):
                return None
            units += [{'name': line['name'].strip(), 'price': line['price']}] * line['quantity']
        if len(units) > MAX_SENT_UNITS:
            return None
        if discount is not None and not (isinstance(discount, dict) and isinstance(discount.get('name'), str)
                                         and LABEL.fullmatch(discount['name'])
                                         and discount['name'] not in POPCORE_DISCOUNTS
                                         and _safe_cents(discount.get('amount')) and discount['amount'] > 0):
            return None
        return {'key': key, 'reference': reference, 'title': title, 'tax_rate': tax_rate, 'units': units, 'discount': discount, 'expected': expected}

    def find_sent(auth, reference):
        """An order from the last two days carrying this external reference that no other POPCORE sale
        sent, or None: the order a lost reply already created."""
        since = int((time.time() - 2 * 86400) * 1000)
        payload = clover('GET', f'/v3/merchants/{merchant}/orders', headers=auth, params=[
            ('filter', f'createdTime>={since}'), ('orderBy', 'createdTime DESC'), ('limit', 100)])
        with database() as db:
            taken = {row['order_id'] for row in db.execute('SELECT order_id FROM sent_orders WHERE order_id IS NOT NULL')}
        return next((o['id'] for o in payload.get('elements', [])
                     if isinstance(o, dict) and o.get('externalReferenceId') == reference
                     and IDENTIFIER.fullmatch(str(o.get('id', ''))) and o['id'] not in taken), None)

    @app.post(PREFIX + '/orders/create')
    def create_order():
        """Open a Clover order for a sale POPCORE started, so the customer pays it by card on the device.

        Custom lines (no Clover item) carry the merchant's default tax rates. Idempotent by POPCORE's
        reference: the order ID is kept here, an order Clover already has under that external
        reference is reused, and every step is skipped when Clover already shows it. Refuses, writing
        nothing, when Clover's total for these lines is not the total POPCORE agreed."""
        nonlocal orders_next_read
        require_writer()
        sent = sent_order_body(request.get_json(silent=True))
        if sent is None or not IDENTIFIER.fullmatch(merchant):
            abort(400)
        key, reference = sent['key'], sent['reference']
        with order_lock(key):
            auth = {'Authorization': 'Bearer ' + access_token()}
            known = [r for r in clover('GET', f'/v3/merchants/{merchant}/tax_rates', headers=auth,
                                       params={'limit': 100}).get('elements', [])
                     if isinstance(r, dict) and IDENTIFIER.fullmatch(str(r.get('id', '')))]
            # The merchant's rate at the percentage POPCORE priced with; a default one first.
            matching = [r for r in known if r.get('rate') == sent['tax_rate']]
            matching = [r for r in matching if r.get('isDefault')] or matching
            if len(matching) != 1:
                return conflict('tax_missing', 'Clover has no single tax rate at the rate POPCORE priced with.',
                                rates=[{k: r.get(k) for k in ('name', 'rate', 'isDefault')} for r in known])
            rates = [{k: matching[0].get(k) for k in ('id', 'name', 'rate')}]
            discounts = [{'name': sent['discount']['name'], 'amount': -sent['discount']['amount']}] \
                if sent['discount'] else []
            total, tax, _ = clover_calc([{'price': u['price'], 'taxRates': rates} for u in sent['units']], discounts)
            if total != sent['expected']:
                return conflict('amounts_differ', f"Clover would charge {money(total)}, not {money(sent['expected'])}.",
                                clover_total=total, tax_cents=tax)
            with database() as db:
                row = db.execute('SELECT reference,order_id FROM sent_orders WHERE key=?', (key,)).fetchone()
            if row and row['reference'] != reference:
                return conflict('clover_changed', 'This POPCORE sale was sent with another reference.')
            order_id = row['order_id'] if row else None
            created = False
            if order_id is None:
                order_id = find_sent(auth, reference)
            if order_id is None:
                fields = {'state': 'open', 'externalReferenceId': reference}
                if sent['title']:
                    fields['title'] = sent['title']
                order_id = clover('POST', f'/v3/merchants/{merchant}/orders', headers=auth, json=fields).get('id')
                if not IDENTIFIER.fullmatch(str(order_id or '')):
                    raise CloverError('Clover did not return the new order.')
                created = True
            with database() as db:
                db.execute('INSERT INTO sent_orders(key,reference,order_id,created) VALUES (?,?,?,?) ON CONFLICT(key) '
                           'DO UPDATE SET order_id=excluded.order_id', (key, reference, order_id, time.time()))
            base = f'/v3/merchants/{merchant}/orders/{order_id}'
            try:
                order, lines = fetch_order(order_id, fresh=True)
            except CloverError as error:
                if error.status == 404:
                    return conflict('deleted', 'The Clover order for this sale was deleted.', order_id=order_id)
                raise
            if order.get('externalReferenceId') != reference:
                return conflict('clover_changed', 'That Clover order belongs to another sale.', order_id=order_id)
            # An order already being paid on the device is never rewritten.
            if order.get('paymentState') in (None, 'OPEN') and not (order.get('payments') or {}).get('elements'):
                wanted = sorted((u['name'], u['price']) for u in sent['units'])
                have = sorted((line.get('name'), line.get('price')) for line in lines)
                if not have:
                    clover('POST', base + '/bulk_line_items', headers=auth, json={'items': [
                        {'name': u['name'], 'price': u['price'], 'taxRates': rates}
                        for u in sent['units']]})
                elif have != wanted:
                    return conflict('clover_changed', 'The Clover order no longer has the lines POPCORE sent.',
                                    order_id=order_id)
                present = (order.get('discounts') or {}).get('elements', [])
                if discounts and not any(d.get('name') == discounts[0]['name'] and d.get('amount') == discounts[0]['amount']
                                         for d in present):
                    clover('POST', base + '/discounts', headers=auth, json=discounts[0])
                order, lines = fetch_order(order_id, fresh=True)
                missing = [line.get('name') for line in lines
                           if not (line.get('taxRates') or {}).get('elements')]
                if missing:
                    return conflict('tax_not_applied', 'Clover did not apply tax to the lines POPCORE sent.',
                                    order_id=order_id)
                if order.get('total') != total:
                    clover('POST', base, headers=auth, json={'total': total})
                order, lines = fetch_order(order_id, fresh=True)
            with orders_condition:
                orders_next_read = 0
            row = safe_row(order, {})
            row.update(source='cloud', seen_at=time.time(), reconciled=True)
            return jsonify(environment=environment, merchant_id=merchant, created=created, order=row,
                           pricing=verify_pricing(order, lines))

    @app.delete(PREFIX + '/orders/<order_id>')
    def delete_order(order_id):
        """Delete an order nobody paid, so Clover matches a cancellation in POPCORE."""
        nonlocal orders_next_read
        require_writer()
        if not IDENTIFIER.fullmatch(order_id):
            abort(400)
        with order_lock(order_id):
            try:
                order, _ = fetch_order(order_id, fresh=True)
            except CloverError as error:
                if error.status == 404:
                    return jsonify(deleted=True, missing=True)
                raise
            if order.get('paymentState') not in (None, 'OPEN') or any(
                    p.get('result') == 'SUCCESS' for p in (order.get('payments') or {}).get('elements', [])):
                return conflict('has_payments', 'Clover keeps orders that have payments.')
            token = access_token()
            try:
                clover('DELETE', f'/v3/merchants/{merchant}/orders/{order_id}',
                       headers={'Authorization': 'Bearer ' + token})
            except CloverError as error:
                if error.status != 404:
                    raise
            order_cache.pop(order_id, None)
            with database() as db:
                db.execute('DELETE FROM device_orders WHERE order_id=?', (order_id,))
            with orders_condition:
                orders_next_read = 0
            return jsonify(deleted=True, missing=False)

    @app.post(PREFIX + '/webhooks/<path_key>')
    def webhook(path_key):
        nonlocal orders_next_read
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
        for event_merchant, events in merchants.items():
            if event_merchant != merchant or not isinstance(events, list):
                abort(400)
            for event in events:
                if (not isinstance(event, dict) or not isinstance(event.get('objectId'), str)
                        or not re.fullmatch(r'[A-Z]{1,2}:[A-Za-z0-9]{13}', event['objectId'])
                        or event.get('type') not in ('CREATE', 'UPDATE', 'DELETE')
                        or type(event.get('ts')) is not int or event['ts'] < 0):
                    abort(400)
                pending.append((event_merchant, event['objectId'], event['type'], event['ts'], time.time()))
        with database() as db:
            db.executemany('INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?)', pending)
        # A Clover change makes the next read fresh instead of waiting for the poll.
        with orders_condition:
            orders_next_read = min(orders_next_read, time.monotonic())
        for _, object_id, *_ in pending:
            order_cache.pop(object_id.split(':')[-1], None)
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
