"""Clover orders as native POPCORE checkouts.

Clover scans the items and takes card. POPCORE prices every other tender, records it, finalizes
the sale and then completes the Clover order (its discount, total, tenders and reference), so
both systems end each sale in the same state, linked by the Clover order ID. Amounts never come
from the browser: they come from Clover's order and the owner-approved rules in clover_pricing.

Order of trust: Clover's cloud order for items and Clover payments; device snapshots only for
items while unpaid; POPCORE for its own tenders and the sale.
"""
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

import clover_pricing as pricing
import db
from checkout_access import REGISTER_NAME, REGISTER_SUB, register_actor, require_checkout
from checkout_operations import checkout_detail, checkout_row, cny_charge, finalize_sale, manager
from goods_operations import _begin, _remember, _replay
from inventory_commands import InventoryConflict, InventoryError, InventoryValidationError
from sales_operations import _sale_input, record_late_adjustment
from validation import read_int

TORONTO = ZoneInfo('America/Toronto')
IDENTIFIER = pricing.IDENTIFIER
FRESH_SECONDS = 10
WRITEBACK_BACKOFF = (5, 15, 60, 300)
POPCORE_TENDERS = ('cash', 'e_transfer', 'wechat', 'alipay')
# Review reasons that stop an open order from being finalized automatically.
OPEN_BLOCKERS = ('overpaid', 'tender_unknown', 'clover_deleted')
# How POPCORE marks a sale it recorded on the Clover order (owner, 2026-10-20): "label" leaves the order
# open on Clover and titles it "Paid <date> <time>"; "pay" adds POPCORE's discount, total and tenders so
# Clover shows it paid. Sales paid entirely on Clover need neither.
WRITEBACK_MODES = ('label', 'pay')
PERMANENT_WRITE_ERRORS = ('clover_changed', 'overpaid', 'calc_unverified', 'unreachable', 'amounts_differ',
                          'discount_required', 'tender_missing', 'not_paid', 'writer_disabled', 'wrong_merchant')


class ConnectorError(Exception):
    def __init__(self, message, *, code='clover_unavailable', status=None, retry_after=None, body=None):
        super().__init__(message)
        self.code, self.status, self.retry_after, self.body = code, status, retry_after, body or {}


# ── Configuration and the connector ─────────────────────────────────────────

def config():
    """Clover settings from the environment, or None when Clover checkout is off."""
    url = os.environ.get('CLOVER_CONNECTOR_URL', '').strip().rstrip('/')
    password = os.environ.get('CLOVER_CONNECTOR_PASSWORD', '')
    merchant = os.environ.get('CLOVER_MERCHANT_ID', '').strip()
    store = os.environ.get('CLOVER_STORE_ID', '').strip()
    if not (url and password and merchant and store):
        return None
    parts = urlsplit(url)
    loopback = parts.hostname in ('127.0.0.1', 'localhost')
    instance = os.environ.get('CLOVER_INSTANCE', 'pc').strip()
    if (parts.scheme not in ('https', 'http') or (parts.scheme == 'http' and not loopback)
            or parts.username or parts.password or parts.query or parts.fragment
            or not IDENTIFIER.fullmatch(merchant) or not store.isdigit() or int(store) < 1
            or not pricing.INSTANCE.fullmatch(instance)):
        raise ValueError('Clover connection settings are invalid')
    since = os.environ.get('CLOVER_IMPORT_SINCE', '').strip()
    since_ms = None
    if since:
        moment = datetime.fromisoformat(since)
        since_ms = int((moment if moment.tzinfo else moment.replace(tzinfo=TORONTO)).timestamp() * 1000)
    writeback = os.environ.get('CLOVER_WRITEBACK', 'label').strip() or 'label'
    if writeback not in WRITEBACK_MODES:
        raise ValueError('CLOVER_WRITEBACK must be label or pay')
    return {'url': url, 'password': password, 'merchant': merchant, 'store_id': int(store),
            'writer_key': os.environ.get('CLOVER_CONNECTOR_WRITER_KEY', ''), 'instance': instance,
            'environment': os.environ.get('CLOVER_ENVIRONMENT', 'sandbox').strip(), 'since_ms': since_ms,
            'writeback': writeback}


def store_config(store_id):
    """The Clover settings when this store takes Clover orders, else None."""
    try:
        cfg = config()
    except ValueError:
        return None
    return cfg if cfg and cfg['store_id'] == store_id else None


def call(cfg, method, path, *, body=None, params=None, writer=False, timeout=(3, 20)):
    headers = {}
    if writer:
        if len(cfg['writer_key']) < 32:
            raise ConnectorError('POPCORE only reads Clover here.', code='writer_disabled')
        headers['X-Popcore-Writer'] = cfg['writer_key']
    try:
        response = requests.request(method, cfg['url'] + path, json=body, params=params, headers=headers,
                                    auth=('popcore', cfg['password']), timeout=timeout, allow_redirects=False)
    except requests.RequestException as exc:
        raise ConnectorError('Clover connection unavailable.') from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    if response.status_code != 200:
        retry = str(response.headers.get('Retry-After', ''))
        code = data.get('code') or ('not_found' if response.status_code == 404 else 'clover_unavailable')
        raise ConnectorError(str(data.get('error') or f'Clover connection returned HTTP {response.status_code}.')[:300],
                             code=str(code), status=response.status_code,
                             retry_after=int(retry) if retry.isdigit() else None, body=data)
    if data.get('environment') not in (None, cfg['environment']) or data.get('merchant_id') not in (None, cfg['merchant']):
        raise ConnectorError('The Clover connection is for another merchant or environment.', code='wrong_merchant')
    return data


def connect():
    """A connection for background work, configured like get_db()."""
    con = sqlite3.connect(db.DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    con.execute('PRAGMA busy_timeout = 10000')
    return con


# ── Clover order facts ──────────────────────────────────────────────────────

def toronto_date(ms):
    return datetime.fromtimestamp(ms / 1000, TORONTO).date().isoformat()


def validate(row):
    """A connector order row reduced to what POPCORE keeps. Raises ValueError when malformed."""
    if not isinstance(row, dict) or not IDENTIFIER.fullmatch(str(row.get('id', ''))):
        raise ValueError('Clover returned an invalid order')
    if row.get('currency') != 'CAD':
        raise ValueError('Clover order is not in CAD')
    source = row.get('source', 'cloud')
    total, created, seen = row.get('total'), row.get('createdTime'), row.get('seen_at')
    if (source not in ('cloud', 'device') or (total is not None and not pricing.safe_cents(total))
            or type(created) is not int or not 0 < created < 32_503_680_000_000
            or type(seen) not in (int, float) or not 0 < seen <= time.time() + 5):
        raise ValueError('Clover returned an invalid order')
    items, discounts = row.get('items'), row.get('discounts') or []
    payments, refunds = row.get('payments') or [], (row.get('refunds') or []) + (row.get('credits') or [])
    if not all(isinstance(x, list) for x in (items, discounts, payments, refunds)) or not all(
            isinstance(x, dict) for x in [*items, *discounts, *payments, *refunds]):
        raise ValueError('Clover returned an incomplete order')
    clean_payments = []
    for payment in payments:
        if not IDENTIFIER.fullmatch(str(payment.get('id', ''))) or not pricing.safe_cents(payment.get('amount')):
            raise ValueError('Clover returned an invalid payment')
        clean_payments.append({k: payment.get(k) for k in (
            'id', 'amount', 'result', 'tenderLabel', 'tenderLabelKey', 'externalPaymentId', 'taxAmount')})
    if source == 'device' and clean_payments:
        raise ValueError('Device snapshots cannot confirm payments')
    return {
        'id': row['id'], 'currency': 'CAD', 'source': source, 'total': total, 'createdTime': created,
        'paymentState': row.get('paymentState') if source == 'cloud' else None, 'seen_at': float(seen),
        'items': [{k: i.get(k) for k in ('id', 'name', 'price', 'unitQty', 'unitName', 'itemCode', 'itemId',
                                         'discountAmount', 'refunded')} for i in items],
        'discounts': [{k: d.get(k) for k in ('id', 'name', 'amount', 'percentage')} for d in discounts],
        'payments': clean_payments,
        'refunds': [{'id': r.get('id'), 'amount': r.get('amount')} for r in refunds],
        'externalReferenceId': row.get('externalReferenceId'),
        'employeeName': str(row.get('employeeName') or '')[:80] or None,
    }


def product_for_item(con, item_id, code):
    """Clover item id first (set by the Clover item import), then an unambiguous one-unit barcode."""
    if isinstance(item_id, str) and IDENTIFIER.fullmatch(item_id):
        row = con.execute("""SELECT id, stock_unit AS unit, COALESCE(NULLIF(jizhanming,''),name_cn_en) AS name
            FROM products WHERE clover_item_id=?""", (item_id,)).fetchone()
        if row:
            return dict(row)
    if isinstance(code, str) and code.strip():
        rows = con.execute("""SELECT p.id, p.stock_unit AS unit, COALESCE(NULLIF(p.jizhanming,''),p.name_cn_en) AS name,
            b.input_unit, b.quantity_per_scan FROM product_barcodes b JOIN products p ON p.id=b.product_id
            WHERE b.code=?""", (code.strip(),)).fetchall()
        if len({r['id'] for r in rows}) == 1 and all(
                r['quantity_per_scan'] == 1 and r['input_unit'] in (None, r['unit']) for r in rows):
            return {k: rows[0][k] for k in ('id', 'unit', 'name')}
    return None


def lines_for(con, snapshot):
    """Sale lines: matched products merged per product; anything else stays as Clover's text."""
    mapped, raw = {}, []
    for item in snapshot['items']:
        whole = item.get('unitQty') in (None, 0, 1000)
        price = item['price'] if pricing.safe_cents(item.get('price')) else None
        product = product_for_item(con, item.get('itemId'), item.get('itemCode')) if whole else None
        if product:
            entry = mapped.setdefault(product['id'], {'quantity': 0, 'prices': set(), 'unit': product['unit']})
            entry['quantity'] += 1
            entry['prices'].add(price)
            continue
        text = f"Clover item: {item.get('name') or 'Item'}"
        if item.get('itemCode'):
            text += f" [{item['itemCode']}]"
        if not whole:
            text += f" x{item['unitQty'] / 1000:g} {item.get('unitName') or 'unit'}"
        raw.append({'quantity': 1, 'raw_product_text': text[:240], 'unit_price_cents': price if whole else None})
    return [{'product_id': product_id, 'unit': entry['unit'], 'quantity': entry['quantity'],
             'unit_price_cents': next(iter(entry['prices'])) if len(entry['prices']) == 1 else None}
            for product_id, entry in mapped.items()] + raw


def _intent(con, store_id, snapshot, *, due):
    summary = pricing.summarize(snapshot)
    return _sale_input(con, {
        'store_id': store_id, 'business_date': toronto_date(snapshot['createdTime']), 'entry_mode': 'already_paid',
        'source': {'system': 'popcore_checkout', 'account': str(store_id), 'reference': snapshot['id']},
        **pricing.sale_money(summary, due), 'lines': lines_for(con, snapshot)}, register_actor())


def _verified(snapshot, cfg):
    """Whether POPCORE may price this order itself (cash discount, rounding).

    In label mode nothing is written to Clover, so Clover's own total is enough. In pay mode POPCORE
    must be able to reproduce Clover's total, because it writes the discounted total back."""
    if cfg and cfg['writeback'] == 'label':
        return True
    facts = snapshot.get('_pricing') or {}
    return bool(facts.get('verified') and facts.get('total') == snapshot.get('total')
                and facts.get('fingerprint') == pricing.fingerprint(snapshot, payments=False))


# ── Import ──────────────────────────────────────────────────────────────────

def ensure_feed(con, cfg):
    feed = con.execute('SELECT * FROM clover_feed WHERE merchant_id=?', (cfg['merchant'],)).fetchone()
    if feed is None:
        if not con.execute('SELECT 1 FROM stores WHERE id=? AND is_active=1', (cfg['store_id'],)).fetchone():
            raise ValueError('The Clover store does not exist')
        since = cfg['since_ms'] if cfg['since_ms'] is not None else int(time.time() * 1000)
        _begin(con)
        try:
            con.execute('INSERT OR IGNORE INTO clover_feed(merchant_id,store_id,import_since_ms) VALUES (?,?,?)',
                        (cfg['merchant'], cfg['store_id'], since))
            # The register records Clover sales nobody picked up. Its authority is this store only;
            # an admin can revoke it, and it is never granted again behind their back.
            con.execute('INSERT OR IGNORE INTO inventory_access(auth0_sub,store_id) VALUES (?,?)',
                        (REGISTER_SUB, cfg['store_id']))
            con.commit()
        except Exception:
            con.rollback()
            raise
        feed = con.execute('SELECT * FROM clover_feed WHERE merchant_id=?', (cfg['merchant'],)).fetchone()
    if feed['store_id'] != cfg['store_id']:
        raise ValueError('This Clover merchant is linked to another store')
    if cfg['since_ms'] is not None and feed['import_since_ms'] != cfg['since_ms']:
        with con:
            con.execute('UPDATE clover_feed SET import_since_ms=? WHERE merchant_id=?', (cfg['since_ms'], cfg['merchant']))
        feed = con.execute('SELECT * FROM clover_feed WHERE merchant_id=?', (cfg['merchant'],)).fetchone()
    return feed


def _set_review(con, checkout, link, reason, detail):
    if link['review_reason'] == reason:
        return
    con.execute('UPDATE clover_orders SET review_reason=?,review_detail=? WHERE checkout_id=?',
                (reason, detail[:300], checkout['id']))
    con.execute('UPDATE checkout_orders SET version=version+1 WHERE id=?', (checkout['id'],))
    record_late_adjustment(con, checkout['store_id'], checkout['business_date'], 'clover_review', checkout['id'],
                           f'Clover review after store-day close: {reason}', REGISTER_SUB)


def _import_payments(con, checkout, link, snapshot):
    """Clover's own payments (never POPCORE's mirrored tenders) as completed attempts."""
    current = {p['id']: p for p in snapshot['payments'] if not pricing.is_popcore_payment(p)}
    known = {a['provider_payment_id']: a for a in con.execute(
        "SELECT * FROM checkout_attempts WHERE checkout_id=? AND origin='clover'", (checkout['id'],))}
    changed = False
    for payment_id, payment in current.items():
        success = payment.get('result') == 'SUCCESS' and payment['amount'] > 0
        attempt = known.get(payment_id)
        if attempt is None:
            if not success:
                continue
            tender = pricing.tender_for(payment.get('tenderLabel'), payment.get('tenderLabelKey'))
            if tender is None:
                if payment.get('tenderLabel') or payment.get('tenderLabelKey'):
                    _set_review(con, checkout, link, 'tender_unknown',
                                f"Clover tender {payment.get('tenderLabel') or payment.get('tenderLabelKey')} has no POPCORE tender")
                continue  # Clover's tender name arrives on a later read
            con.execute('''INSERT INTO checkout_attempts(checkout_id,tender,amount_cents,assigned_to,status,recorded_by,
                origin,provider_payment_id,provider_amount_cents) VALUES (?,?,?,?,'completed',?,'clover',?,?)''',
                (checkout['id'], tender, payment['amount'], checkout['created_by'], REGISTER_SUB, payment_id,
                 payment['amount']))
            changed = True
        elif attempt['status'] == 'completed' and (not success or payment['amount'] != attempt['amount_cents']):
            con.execute("UPDATE checkout_attempts SET status='cancelled' WHERE id=?", (attempt['id'],))
            changed = True
    for payment_id, attempt in known.items():
        if payment_id not in current and attempt['status'] == 'completed':
            con.execute("UPDATE checkout_attempts SET status='cancelled' WHERE id=?", (attempt['id'],))
            changed = True
    if changed:
        con.execute('UPDATE checkout_orders SET version=version+1 WHERE id=?', (checkout['id'],))


def _received(con, checkout_id):
    clover_cents = popcore_cents = 0
    for attempt in con.execute("SELECT origin,amount_cents FROM checkout_attempts WHERE checkout_id=? AND status='completed'",
                               (checkout_id,)):
        if attempt['origin'] == 'clover':
            clover_cents += attempt['amount_cents']
        else:
            popcore_cents += attempt['amount_cents']
    return clover_cents, popcore_cents


def _settle_if_paid(con, cfg, checkout_id, *, actor):
    """Inside the caller's transaction: finalize once the money received equals the amount due."""
    checkout = con.execute('SELECT * FROM checkout_orders WHERE id=?', (checkout_id,)).fetchone()
    link = con.execute('SELECT * FROM clover_orders WHERE checkout_id=?', (checkout_id,)).fetchone()
    snapshot = json.loads(link['snapshot'])
    if checkout['status'] != 'open' or checkout['abandoned_reason'] or snapshot.get('total') is None:
        return False
    clover_cents, popcore_cents = _received(con, checkout_id)
    summary = pricing.summarize(snapshot)
    due, mode = pricing.amount_due(summary, clover_cents=clover_cents, popcore_cents=popcore_cents,
                                   plan=link['plan'], verified=_verified(snapshot, cfg))
    received = clover_cents + popcore_cents
    if received > due and popcore_cents:
        if link['review_reason'] is None:
            _set_review(con, checkout, link, 'overpaid',
                        f'Received {received} cents but the customer owes {due}. Take back the payment recorded in '
                        'POPCORE and record what the customer really paid.')
        return False
    if link['review_reason'] == 'overpaid':
        # The extra payment was taken back.
        con.execute('UPDATE clover_orders SET review_reason=NULL,review_detail=NULL WHERE checkout_id=?', (checkout_id,))
        con.execute('UPDATE checkout_orders SET version=version+1 WHERE id=?', (checkout_id,))
    elif link['review_reason'] in OPEN_BLOCKERS:
        return False
    if not received or received != due:
        return False
    if popcore_cents == 0 and snapshot.get('paymentState') != 'PAID':
        return False  # paid only on Clover: wait until Clover itself says the order is paid
    intent = _intent(con, checkout['store_id'], snapshot, due=due)
    finalize_sale(con, dict(checkout), intent, actor=actor, key=f"clover:{link['merchant_id']}:{link['order_id']}")
    # Clover already shows a sale it was paid for in full; only money recorded in POPCORE is marked there.
    writes = bool(cfg and len(cfg['writer_key']) >= 32 and popcore_cents)
    con.execute('''UPDATE clover_orders SET pricing_mode=?,due_cents=?,final_fingerprint=?,clover_total_cents=?,
        writeback_state=?,writeback_next_at=0,writeback_tries=0,writeback_error=NULL WHERE checkout_id=?''',
        (mode, due, pricing.fingerprint(snapshot), snapshot['total'], 'pending' if writes else 'none', checkout_id))
    con.execute('UPDATE checkout_orders SET version=version+1 WHERE id=?', (checkout_id,))
    record_late_adjustment(con, checkout['store_id'], checkout['business_date'], 'checkout', checkout_id,
                           'Clover sale recorded after store-day close', actor['sub'])
    return True


def apply_order(con, cfg, feed, snapshot, *, pricing_facts=None):
    """Create or refresh the checkout of one Clover order, import its payments, finalize when paid."""
    link = con.execute('SELECT * FROM clover_orders WHERE merchant_id=? AND order_id=?',
                       (cfg['merchant'], snapshot['id'])).fetchone()
    if pricing_facts is not None:
        snapshot['_pricing'] = {'verified': bool(pricing_facts.get('verified')), 'total': snapshot['total'],
                                'fingerprint': pricing.fingerprint(snapshot, payments=False)}
    if link is None:
        if snapshot['createdTime'] < feed['import_since_ms'] or snapshot['total'] is None or not snapshot['items']:
            return None
        return _create(con, cfg, snapshot)
    if snapshot['seen_at'] < link['seen_at']:
        return link['checkout_id']
    return _refresh(con, cfg, link, snapshot)


def _create(con, cfg, snapshot):
    store_id = cfg['store_id']
    business_date = toronto_date(snapshot['createdTime'])
    snapshot['_cloud_seen'] = snapshot['source'] == 'cloud'
    _begin(con)
    try:
        intent = _intent(con, store_id, snapshot, due=snapshot['total'])
        checkout_id = con.execute('''INSERT INTO checkout_orders(store_id,business_date,reference,payload,created_by)
            VALUES (?,?,?,?,?)''', (store_id, business_date, snapshot['id'], json.dumps(intent), REGISTER_SUB)).lastrowid
        con.execute('''INSERT INTO clover_orders(checkout_id,merchant_id,order_id,created_ms,snapshot,fingerprint,source,
            state,seen_at) VALUES (?,?,?,?,?,?,?,?,?)''',
            (checkout_id, cfg['merchant'], snapshot['id'], snapshot['createdTime'], json.dumps(snapshot),
             pricing.fingerprint(snapshot, payments=False), snapshot['source'],
             snapshot['paymentState'] or 'DEVICE', snapshot['seen_at']))
        record_late_adjustment(con, store_id, business_date, 'checkout', checkout_id,
                               'Clover order imported after store-day close', REGISTER_SUB)
        checkout = con.execute('SELECT * FROM checkout_orders WHERE id=?', (checkout_id,)).fetchone()
        link = con.execute('SELECT * FROM clover_orders WHERE checkout_id=?', (checkout_id,)).fetchone()
        if snapshot['source'] == 'cloud':
            _import_payments(con, checkout, link, snapshot)
        _settle_if_paid(con, cfg, checkout_id, actor=register_actor())
        con.commit()
        return checkout_id
    except Exception:
        con.rollback()
        raise


def _change_kind(old, new):
    if new.get('refunds'):
        return 'clover_refunded', 'Clover recorded a refund, void or credit on this order.'
    old_ids = {p['id'] for p in old.get('payments') or [] if not pricing.is_popcore_payment(p)}
    added = [p for p in new['payments'] if not pricing.is_popcore_payment(p) and p['id'] not in old_ids
             and p.get('result') == 'SUCCESS']
    if added:
        return 'clover_payment_added', 'Clover took another payment after the sale was recorded.'
    return 'clover_changed', 'The order changed on Clover after the sale was recorded.'


def _refresh(con, cfg, link, snapshot):
    checkout_id = link['checkout_id']
    stored = json.loads(link['snapshot'])
    _begin(con)
    try:
        checkout = con.execute('SELECT * FROM checkout_orders WHERE id=?', (checkout_id,)).fetchone()
        link = con.execute('SELECT * FROM clover_orders WHERE checkout_id=?', (checkout_id,)).fetchone()
        if checkout['status'] == 'cancelled' and link['state'] == 'DELETED':
            con.commit()  # deleted on Clover: a read from before the delete must not bring it back
            return checkout_id
        merged = dict(snapshot)
        merged['_cloud_seen'] = stored.get('_cloud_seen') or snapshot['source'] == 'cloud'
        if snapshot['source'] == 'device':
            # A device snapshot only knows items; Clover's payments and state stay as last read.
            merged.update(payments=stored.get('payments', []), refunds=stored.get('refunds', []),
                          paymentState=stored.get('paymentState'))
        if '_pricing' not in merged and stored.get('_pricing'):
            merged['_pricing'] = stored['_pricing']
        state = merged.get('paymentState') or link['state']
        if checkout['status'] == 'open':
            changed = (pricing.fingerprint(merged, payments=False) != link['fingerprint']
                       or merged['total'] != stored.get('total'))
            if changed and merged['total'] is not None and merged['items']:
                intent = _intent(con, checkout['store_id'], merged, due=merged['total'])
                con.execute('UPDATE checkout_orders SET payload=?,version=version+1 WHERE id=?',
                            (json.dumps(intent), checkout_id))
                if '_pricing' not in snapshot and cfg['writeback'] == 'pay':
                    # Pay mode: the new items need Clover's exact pricing check before a cash price is offered.
                    con.execute('UPDATE clover_orders SET fetch_next_at=0 WHERE checkout_id=?', (checkout_id,))
            con.execute('''UPDATE clover_orders SET snapshot=?,fingerprint=?,source=?,state=?,seen_at=?
                WHERE checkout_id=?''', (json.dumps(merged), pricing.fingerprint(merged, payments=False),
                                        snapshot['source'], state, snapshot['seen_at'], checkout_id))
            if snapshot['source'] == 'cloud':
                link = con.execute('SELECT * FROM clover_orders WHERE checkout_id=?', (checkout_id,)).fetchone()
                _import_payments(con, checkout, link, merged)
            _settle_if_paid(con, cfg, checkout_id, actor=register_actor())
        elif snapshot['source'] == 'cloud':
            if (checkout['status'] == 'completed' and link['final_fingerprint']
                    and pricing.fingerprint(snapshot) != link['final_fingerprint'] and not link['review_reason']):
                reason, detail = _change_kind(stored, snapshot)
                _set_review(con, checkout, link, reason, detail)
            con.execute('UPDATE clover_orders SET snapshot=?,source=?,state=?,seen_at=? WHERE checkout_id=?',
                        (json.dumps(merged), 'cloud', state, snapshot['seen_at'], checkout_id))
        con.commit()
        return checkout_id
    except Exception:
        con.rollback()
        raise


def _deleted_on_clover(con, link):
    _begin(con)
    try:
        checkout = con.execute('SELECT * FROM checkout_orders WHERE id=?', (link['checkout_id'],)).fetchone()
        link = con.execute('SELECT * FROM clover_orders WHERE checkout_id=?', (link['checkout_id'],)).fetchone()
        clover_cents, popcore_cents = _received(con, checkout['id'])
        con.execute("UPDATE clover_orders SET state='DELETED',delete_pending=0 WHERE checkout_id=?", (checkout['id'],))
        if checkout['status'] == 'open' and not clover_cents + popcore_cents:
            con.execute("UPDATE checkout_orders SET status='cancelled',version=version+1 WHERE id=?", (checkout['id'],))
            con.execute("UPDATE checkout_attempts SET status='cancelled' WHERE checkout_id=? AND status='pending'",
                        (checkout['id'],))
            record_late_adjustment(con, checkout['store_id'], checkout['business_date'], 'checkout', checkout['id'],
                                   'Clover order deleted after store-day close', REGISTER_SUB)
        elif checkout['status'] != 'cancelled':
            _set_review(con, checkout, link, 'clover_deleted', 'The order was deleted on Clover after money was recorded.')
        con.commit()
    except Exception:
        con.rollback()
        raise


def refresh_one(con, cfg, link):
    """Read one order straight from Clover, with whether POPCORE can reproduce its total."""
    with con:
        # Screens poll every second; Clover is asked about one order at most every 3 seconds,
        # also while a device order has not reached Clover's cloud yet.
        con.execute('UPDATE clover_orders SET fetch_next_at=? WHERE checkout_id=?', (time.time() + 3, link['checkout_id']))
    try:
        data = call(cfg, 'GET', f"/orders/{link['order_id']}", timeout=(3, 15))
    except ConnectorError as exc:
        stored = json.loads(link['snapshot'])
        if exc.code == 'not_found' and stored.get('_cloud_seen'):
            _deleted_on_clover(con, link)
            return 'deleted'
        raise
    with con:
        con.execute('UPDATE clover_orders SET fetch_next_at=? WHERE checkout_id=?', (time.time() + 10, link['checkout_id']))
    feed = ensure_feed(con, cfg)
    apply_order(con, cfg, feed, validate(data.get('order')), pricing_facts=data.get('pricing') or {})
    return 'ok'


def _feed_done(con, cfg, *, error, next_in, fetched=None):
    with con:
        if fetched is None:
            con.execute('UPDATE clover_feed SET next_poll=?,error=? WHERE merchant_id=?',
                        (time.time() + next_in, error, cfg['merchant']))
        else:
            con.execute('UPDATE clover_feed SET fetched_at=?,ok_at=?,next_poll=?,error=? WHERE merchant_id=?',
                        (fetched, time.time(), time.time() + next_in, error, cfg['merchant']))


def sync(con, cfg, *, force=False):
    """Import Clover's latest orders; at most one connector read per second across all workers."""
    feed = ensure_feed(con, cfg)
    now = time.time()
    _begin(con)
    try:
        acquired = con.execute('UPDATE clover_feed SET next_poll=? WHERE merchant_id=? AND next_poll<=?',
                               (now + 15, cfg['merchant'], now + (10**9 if force else 0))).rowcount
        con.commit()
    except Exception:
        con.rollback()
        raise
    if not acquired:
        return False
    try:
        data = call(cfg, 'GET', '/orders', timeout=(3, 12))
        rows, fetched = data.get('orders'), data.get('fetched_at')
        if not isinstance(rows, list) or type(fetched) not in (int, float):
            raise ConnectorError('Clover returned an unexpected order list.')
    except ConnectorError as exc:
        _feed_done(con, cfg, error='Clover sync unavailable. Showing the last orders received.',
                   next_in=max(2, exc.retry_after or 2))
        return False
    problems, seen = [], set()
    for raw in rows:
        try:
            snapshot = validate(raw)
            seen.add(snapshot['id'])
            apply_order(con, cfg, feed, snapshot)
        except (ValueError, InventoryError, PermissionError, sqlite3.IntegrityError) as exc:
            problems.append(f"{raw.get('id') if isinstance(raw, dict) else 'order'}: {exc}")
    follow_up(con, cfg, seen=seen)
    message = '; '.join(problems)[:300] or (
        'Clover cloud delayed. Items still update from the device; payments wait.' if data.get('cloud_unavailable') else '')
    _feed_done(con, cfg, error=message, next_in=1, fetched=fetched)
    return True


def follow_up(con, cfg, *, seen=()):
    """Orders the latest-20 feed no longer shows, due write-backs and pending deletes."""
    now = time.time()
    stale = con.execute('''SELECT l.* FROM clover_orders l JOIN checkout_orders o ON o.id=l.checkout_id
        WHERE l.merchant_id=? AND o.status='open' AND l.fetch_next_at<=? ORDER BY l.fetch_next_at LIMIT 4''',
        (cfg['merchant'], now)).fetchall()
    for link in [link for link in stale if link['order_id'] not in seen][:2]:
        try:
            refresh_one(con, cfg, link)
        except (ConnectorError, ValueError, InventoryError, PermissionError, sqlite3.IntegrityError):
            with con:
                con.execute('UPDATE clover_orders SET fetch_next_at=? WHERE checkout_id=?', (now + 30, link['checkout_id']))
    for row in con.execute('''SELECT checkout_id FROM clover_orders WHERE merchant_id=?
            AND writeback_state IN ('pending','failed') AND writeback_next_at<=? ORDER BY writeback_next_at LIMIT 3''',
            (cfg['merchant'], now)).fetchall():
        write_back(con, cfg, row['checkout_id'])
    for row in con.execute('SELECT * FROM clover_orders WHERE merchant_id=? AND delete_pending=1 LIMIT 2',
                           (cfg['merchant'],)).fetchall():
        try:
            call(cfg, 'DELETE', f"/orders/{row['order_id']}", writer=True)
            with con:
                con.execute("UPDATE clover_orders SET delete_pending=0,state='DELETED' WHERE checkout_id=?",
                            (row['checkout_id'],))
        except ConnectorError as exc:
            if exc.code == 'has_payments':
                with con:
                    con.execute('UPDATE clover_orders SET delete_pending=0 WHERE checkout_id=?', (row['checkout_id'],))
                checkout = con.execute('SELECT * FROM checkout_orders WHERE id=?', (row['checkout_id'],)).fetchone()
                with con:
                    _set_review(con, checkout, row, 'clover_payment_added',
                                'The order was cancelled in POPCORE but Clover has a payment on it.')


def sync_day(con, cfg, business_date):
    """Every Clover order of one store day, for closing. Returns whether the scan finished."""
    day = datetime.fromisoformat(business_date).replace(tzinfo=TORONTO)
    since = int(day.timestamp() * 1000)
    until = int((day + timedelta(days=1)).timestamp() * 1000)
    feed = ensure_feed(con, cfg)
    offset = 0
    try:
        while True:
            data = call(cfg, 'GET', '/orders/scan', params={'since': since, 'until': until, 'offset': offset},
                        timeout=(3, 25))
            for raw in data.get('orders') or []:
                try:
                    apply_order(con, cfg, feed, validate(raw))
                except (ValueError, InventoryError, PermissionError, sqlite3.IntegrityError):
                    pass
            if not data.get('more') or offset >= 2000:
                break
            offset += 100
    except ConnectorError:
        return False
    with con:
        con.execute('UPDATE clover_feed SET day_scan_date=?,day_scan_at=? WHERE merchant_id=?',
                    (business_date, time.time(), cfg['merchant']))
    return True


def background_sync():
    """Scheduler job: finalize Clover-paid orders and retry write-backs even with no screen open."""
    try:
        cfg = config()
    except ValueError:
        return
    if cfg is None:
        return
    con = connect()
    try:
        sync(con, cfg)
    finally:
        con.close()


# ── Writing back to Clover ──────────────────────────────────────────────────

def paid_label(con, checkout_id):
    """'Paid <date> <time>' in Toronto time, from the last payment recorded in POPCORE, or None."""
    row = con.execute('''SELECT MAX(created_at) AS at FROM checkout_attempts WHERE checkout_id=?
        AND origin='popcore' AND status='completed' ''', (checkout_id,)).fetchone()
    if not row or not row['at']:
        return None
    moment = datetime.fromisoformat(row['at'].replace('Z', '+00:00')).astimezone(TORONTO)
    return f'Paid {moment:%Y-%m-%d %H:%M}'


def write_back(con, cfg, checkout_id, *, now_ok=False):
    """Mark the Clover order of a sale recorded in POPCORE: a "Paid <date> <time>" label, or (pay mode)
    POPCORE's discount, total and tenders as well."""
    now = time.time()
    with con:
        leased = con.execute('''UPDATE clover_orders SET writeback_next_at=? WHERE checkout_id=?
            AND writeback_state IN ('pending','failed') AND writeback_next_at<=?''',
            (now + 60, checkout_id, now + (10**9 if now_ok else 0))).rowcount
    if not leased:
        return None
    link = con.execute('SELECT * FROM clover_orders WHERE checkout_id=?', (checkout_id,)).fetchone()
    checkout = con.execute('SELECT * FROM checkout_orders WHERE id=?', (checkout_id,)).fetchone()
    if checkout['status'] != 'completed':
        return None
    snapshot = json.loads(link['snapshot'])
    attempts = con.execute("SELECT * FROM checkout_attempts WHERE checkout_id=? AND status='completed' ORDER BY id",
                           (checkout_id,)).fetchall()
    popcore = [a for a in attempts if a['origin'] == 'popcore']
    label = paid_label(con, checkout_id)
    if label is None:
        with con:  # nothing was recorded in POPCORE, so there is nothing to mark on Clover
            con.execute("UPDATE clover_orders SET writeback_state='none',writeback_error=NULL WHERE checkout_id=?",
                        (checkout_id,))
        return None
    if cfg['writeback'] == 'label':
        path, body = f"/orders/{link['order_id']}/label", {'label': label}
    else:
        path, body = f"/orders/{link['order_id']}/settle", {
            'target_total': link['due_cents'],
            'discount_name': pricing.discount_label(link['pricing_mode']) if link['due_cents'] < link['clover_total_cents'] else None,
            'payments': [{'key': pricing.payment_key(cfg['instance'], checkout_id, a['id']), 'tender': a['tender'],
                          'amount': a['amount_cents']} for a in popcore],
            'expected': {
                'items': [[i.get('id'), i.get('price'), i.get('unitQty')] for i in snapshot['items']],
                'discounts': [[d.get('id'), d.get('amount'), d.get('percentage')] for d in snapshot['discounts']
                              if not pricing.is_popcore_discount(d)],
                'payments': [[a['provider_payment_id'], a['provider_amount_cents']] for a in attempts
                             if a['origin'] == 'clover'],
            },
            'reference': {'title': label},
        }
    try:
        result = call(cfg, 'POST', path, body=body, writer=True, timeout=(3, 45))
    except ConnectorError as exc:
        tries = link['writeback_tries'] + 1
        permanent = exc.code in PERMANENT_WRITE_ERRORS
        delay = 300 if permanent else WRITEBACK_BACKOFF[min(tries, len(WRITEBACK_BACKOFF)) - 1]
        with con:
            con.execute('''UPDATE clover_orders SET writeback_state='failed',writeback_error=?,writeback_tries=?,
                writeback_next_at=?,fetch_next_at=0 WHERE checkout_id=?''',
                (str(exc)[:300], tries, now + max(delay, exc.retry_after or 0), checkout_id))
        return 'failed'
    with con:
        for payment in result.get('payments') or []:
            parsed = pricing.parse_payment_key(payment.get('key'))
            if parsed and parsed[1] == checkout_id and IDENTIFIER.fullmatch(str(payment.get('id') or '')):
                con.execute('''UPDATE checkout_attempts SET provider_payment_id=?,provider_amount_cents=?
                    WHERE id=? AND checkout_id=? AND origin='popcore' ''', (payment['id'], payment.get('amount'),
                                                                          parsed[2], checkout_id))
        discount = result.get('discount') or {}
        state = result.get('paymentState') if result.get('paymentState') in STATE_LABELS else link['state']
        con.execute('''UPDATE clover_orders SET writeback_state='done',writeback_error=NULL,writeback_next_at=0,
            discount_id=?,discount_cents=?,state=? WHERE checkout_id=?''',
            (discount.get('id'), abs(discount['amount']) if type(discount.get('amount')) is int else None, state,
             checkout_id))
        con.execute('UPDATE checkout_orders SET version=version+1 WHERE id=?', (checkout_id,))
    return 'done'


# ── Cashier actions ─────────────────────────────────────────────────────────

ACTIONS = ('claim', 'release', 'receive', 'undo', 'cancel', 'clover-retry', 'clover-handled')


def _link(con, checkout_id):
    link = con.execute('SELECT * FROM clover_orders WHERE checkout_id=?', (checkout_id,)).fetchone()
    if link is None:
        raise InventoryValidationError('This is not a Clover order')
    return link


def _cfg_for(row):
    cfg = store_config(row['store_id'])
    if cfg is None:
        raise InventoryConflict('Clover checkout is not connected for this store', 'clover_disconnected')
    return cfg


def change(con, checkout_id, action, data, actor, request_key):
    row = checkout_row(con, checkout_id, actor)
    link = _link(con, row['id'])
    cfg = _cfg_for(row)
    if action == 'receive':
        return receive(con, cfg, row, data, actor, request_key)
    if action == 'undo':
        return undo(con, cfg, row, data, actor, request_key)
    require_checkout(con, row, actor, write=True)
    if action == 'claim':
        _claim(con, row['id'], actor)
    elif action == 'release':
        _begin(con)
        try:
            current = con.execute('SELECT * FROM checkout_orders WHERE id=?', (row['id'],)).fetchone()
            _, popcore_cents = _received(con, row['id'])
            if current['status'] != 'open' or popcore_cents or (current['created_by'] != actor['sub'] and not manager(actor)):
                raise InventoryConflict('Only an open order with no money recorded in POPCORE can be released',
                                        'checkout_state_conflict')
            con.execute('UPDATE checkout_orders SET created_by=?,version=version+1 WHERE id=?', (REGISTER_SUB, row['id']))
            con.execute("UPDATE checkout_attempts SET assigned_to=? WHERE checkout_id=? AND origin='clover'",
                        (REGISTER_SUB, row['id']))
            con.commit()
        except Exception:
            con.rollback()
            raise
    elif action == 'cancel':
        _cancel(con, cfg, row, link, data, actor)
    elif action == 'clover-retry':
        with con:
            con.execute('UPDATE clover_orders SET writeback_next_at=0 WHERE checkout_id=?', (row['id'],))
        if link['delete_pending']:
            follow_up(con, cfg)
        write_back(con, cfg, row['id'], now_ok=True)
    elif action == 'clover-handled':
        reason = str(data.get('reason') or '').strip()
        if not manager(actor) or not reason:
            raise PermissionError('A manager must give the reason the Clover order was handled')
        with con:
            con.execute('''UPDATE clover_orders SET writeback_state='done',writeback_error=?
                WHERE checkout_id=? AND writeback_state='failed' ''', (f'Handled on Clover: {reason}'[:300], row['id']))
            con.execute('UPDATE checkout_orders SET version=version+1 WHERE id=?', (row['id'],))
    else:
        raise InventoryValidationError('Unknown Clover checkout action')
    return checkout_detail(con, row['id'], actor)


def _claim(con, checkout_id, actor, *, inside=False):
    if not inside:
        _begin(con)
    try:
        current = con.execute('SELECT * FROM checkout_orders WHERE id=?', (checkout_id,)).fetchone()
        if current['created_by'] != actor['sub']:
            if current['status'] != 'open' or current['created_by'] != REGISTER_SUB:
                raise InventoryConflict('Another cashier has this order', 'checkout_claimed')
            con.execute('UPDATE checkout_orders SET created_by=?,version=version+1 WHERE id=? AND created_by=?',
                        (actor['sub'], checkout_id, REGISTER_SUB))
            con.execute("UPDATE checkout_attempts SET assigned_to=? WHERE checkout_id=? AND origin='clover'",
                        (actor['sub'], checkout_id))
        if not inside:
            con.commit()
    except Exception:
        if not inside:
            con.rollback()
        raise


def receive(con, cfg, row, data, actor, request_key):
    """Record money the cashier received in POPCORE; finalize and complete Clover when it is all in."""
    require_checkout(con, row, actor, write=True)
    tender, plan = data.get('tender'), data.get('plan', 'pay')
    if tender == 'card':
        raise InventoryValidationError('Card is taken on Clover. Choose the tender received in POPCORE.')
    if tender not in POPCORE_TENDERS or plan not in ('pay', 'split_card'):
        raise InventoryValidationError('Choose cash, e-transfer, WeChat Pay or Alipay')
    amount = read_int(data.get('amount_cents'), 'amount_cents', minimum=1)
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    expected_due = data.get('expected_due_cents')
    fen, rate = cny_charge(tender, amount, data)
    if row['created_by'] not in (actor['sub'], REGISTER_SUB) and not manager(actor):
        raise PermissionError('Another cashier has this order')
    link = _link(con, row['id'])
    try:
        # Price from Clover's order as it is now, not as the screen last saw it.
        refresh_one(con, cfg, link)
    except (ConnectorError, ValueError, InventoryError, sqlite3.IntegrityError):
        pass
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'clover_receive', actor, {**data, 'checkout_id': row['id']})
        if prior:
            con.commit()
            return checkout_detail(con, row['id'], actor)
        current = con.execute('SELECT * FROM checkout_orders WHERE id=?', (row['id'],)).fetchone()
        if current['status'] != 'open' or current['abandoned_reason']:
            raise InventoryConflict('This order is no longer open', 'checkout_state_conflict')
        if current['version'] != expected_version:
            raise InventoryConflict('The order changed on Clover. Check the new amount.', 'checkout_state_conflict')
        link = con.execute('SELECT * FROM clover_orders WHERE checkout_id=?', (row['id'],)).fetchone()
        if link['review_reason'] in OPEN_BLOCKERS:
            raise InventoryConflict('This order needs review before more money is recorded', 'clover_review')
        if current['created_by'] == REGISTER_SUB:
            _claim(con, row['id'], actor, inside=True)
        elif current['created_by'] != actor['sub'] and not manager(actor):
            raise InventoryConflict('Another cashier has this order', 'checkout_claimed')
        snapshot = json.loads(link['snapshot'])
        if snapshot.get('total') is None:
            raise InventoryConflict('Clover has not confirmed a total yet', 'clover_total_pending')
        clover_cents, popcore_cents = _received(con, row['id'])
        due, _ = pricing.amount_due(pricing.summarize(snapshot), clover_cents=clover_cents,
                                    popcore_cents=popcore_cents + amount, plan=plan, verified=_verified(snapshot, cfg))
        if expected_due is not None and expected_due != due:
            raise InventoryConflict('The amount due changed. Check the new amount.', 'price_changed')
        if amount > due - clover_cents - popcore_cents:
            raise InventoryValidationError('That is more than the amount still to collect')
        attempt_id = con.execute('''INSERT INTO checkout_attempts(checkout_id,tender,amount_cents,assigned_to,status,
            recorded_by,origin,cny_fen,cny_per_cad) VALUES (?,?,?,?,'completed',?,'popcore',?,?)''',
            (row['id'], tender, amount, actor['sub'], actor['sub'], fen, rate)).lastrowid
        con.execute('UPDATE clover_orders SET plan=? WHERE checkout_id=?', (plan, row['id']))
        con.execute('UPDATE checkout_orders SET version=version+1 WHERE id=?', (row['id'],))
        record_late_adjustment(con, current['store_id'], current['business_date'], 'checkout', row['id'],
                               'Checkout changed after store-day close', actor['sub'])
        settled = _settle_if_paid(con, cfg, row['id'], actor=actor)
        _remember(con, key, 'clover_receive', row['id'], actor, digest, {'id': row['id'], 'attempt_id': attempt_id})
        con.commit()
    except Exception:
        con.rollback()
        raise
    if settled:
        write_back(con, cfg, row['id'])
    return checkout_detail(con, row['id'], actor)


def undo(con, cfg, row, data, actor, request_key):
    """Take back a payment recorded in POPCORE before the sale exists (money handed back or miscounted)."""
    require_checkout(con, row, actor, write=True)
    attempt_id = read_int(data.get('attempt_id'), 'attempt_id', minimum=1)
    reason = data.get('reason')
    if not isinstance(reason, str) or not reason.strip():
        raise InventoryValidationError('Enter why this payment is taken back')
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'clover_undo', actor, {**data, 'checkout_id': row['id']})
        if prior:
            con.commit()
            return checkout_detail(con, row['id'], actor)
        current = con.execute('SELECT * FROM checkout_orders WHERE id=?', (row['id'],)).fetchone()
        attempt = con.execute('SELECT * FROM checkout_attempts WHERE id=? AND checkout_id=?',
                              (attempt_id, row['id'])).fetchone()
        if (current['status'] != 'open' or current['abandoned_reason'] or attempt is None
                or attempt['origin'] != 'popcore' or attempt['status'] != 'completed'):
            raise InventoryConflict('Only a payment recorded in POPCORE on an unfinished order can be taken back',
                                    'checkout_state_conflict')
        if attempt['recorded_by'] != actor['sub'] and not manager(actor):
            raise PermissionError('Only the cashier who recorded this payment or a manager can take it back')
        con.execute("UPDATE checkout_attempts SET status='cancelled',cancel_reason=? WHERE id=?",
                    (reason.strip()[:300], attempt_id))
        if not con.execute("""SELECT 1 FROM checkout_attempts WHERE checkout_id=? AND origin='popcore'
                AND status='completed'""", (row['id'],)).fetchone():
            con.execute('UPDATE clover_orders SET plan=NULL WHERE checkout_id=?', (row['id'],))
        con.execute('UPDATE checkout_orders SET version=version+1 WHERE id=?', (row['id'],))
        record_late_adjustment(con, current['store_id'], current['business_date'], 'checkout', row['id'],
                               'Checkout changed after store-day close', actor['sub'])
        settled = _settle_if_paid(con, cfg, row['id'], actor=actor)
        _remember(con, key, 'clover_undo', row['id'], actor, digest, {'id': row['id'], 'attempt_id': attempt_id})
        con.commit()
    except Exception:
        con.rollback()
        raise
    if settled:
        write_back(con, cfg, row['id'])
    return checkout_detail(con, row['id'], actor)


def _cancel(con, cfg, row, link, data, actor):
    local = data.get('local') is True
    if local and not manager(actor):
        raise PermissionError('Only a manager can cancel without Clover')
    clover_cents, popcore_cents = _received(con, row['id'])
    if row['status'] != 'open' or clover_cents or popcore_cents:
        raise InventoryConflict('Money was recorded on this order. A manager abandons it and records the refund.',
                                'checkout_has_payments')
    if row['created_by'] not in (actor['sub'], REGISTER_SUB) and not manager(actor):
        raise PermissionError('Another cashier has this order')
    deleted = False
    if not local:
        try:
            call(cfg, 'DELETE', f"/orders/{link['order_id']}", writer=True)
            deleted = True
        except ConnectorError as exc:
            if exc.code == 'has_payments':
                try:
                    refresh_one(con, cfg, link)
                except ConnectorError:
                    pass
                raise InventoryConflict('Clover already has a payment on this order', 'checkout_has_payments') from exc
            raise InventoryConflict(f'Clover could not delete the order: {exc}', 'clover_unavailable') from exc
    _begin(con)
    try:
        current = con.execute('SELECT * FROM checkout_orders WHERE id=?', (row['id'],)).fetchone()
        link = con.execute('SELECT * FROM clover_orders WHERE checkout_id=?', (row['id'],)).fetchone()
        clover_cents, popcore_cents = _received(con, row['id'])
        if current['status'] == 'open' and not clover_cents + popcore_cents:
            if current['created_by'] == REGISTER_SUB:
                _claim(con, row['id'], actor, inside=True)  # the cancelled order is the canceller's
            con.execute("UPDATE checkout_orders SET status='cancelled',version=version+1 WHERE id=?", (row['id'],))
            record_late_adjustment(con, current['store_id'], current['business_date'], 'checkout', row['id'],
                                   'Checkout changed after store-day close', actor['sub'])
            con.execute('UPDATE clover_orders SET state=?,delete_pending=? WHERE checkout_id=?',
                        ('DELETED' if deleted else link['state'], 0 if deleted else 1, row['id']))
        elif deleted:
            # Money was recorded while Clover deleted the order: keep it and have a manager review.
            con.execute("UPDATE clover_orders SET state='DELETED',delete_pending=0 WHERE checkout_id=?", (row['id'],))
            _set_review(con, current, link, 'clover_deleted', 'The order was deleted on Clover after money was recorded.')
        else:
            raise InventoryConflict('Money was recorded on this order. A manager abandons it and records the refund.',
                                    'checkout_has_payments')
        con.commit()
    except Exception:
        con.rollback()
        raise


# ── What the screen shows ───────────────────────────────────────────────────

STATE_LABELS = {'OPEN': 'Open', 'PARTIALLY_PAID': 'Partly paid', 'PAID': 'Paid', 'DEVICE': 'Open on device',
                'DELETED': 'Deleted on Clover', 'REFUNDED': 'Refunded', 'PARTIALLY_REFUNDED': 'Partly refunded',
                'CREDITED': 'Credited'}


def clover_block(con, row, actor, *, writable):
    link = con.execute('SELECT * FROM clover_orders WHERE checkout_id=?', (row['id'],)).fetchone()
    snapshot = json.loads(link['snapshot'])
    clover_cents = sum(a['amount_cents'] for a in row['attempts'] if a['status'] == 'completed' and a.get('origin') == 'clover')
    popcore_cents = sum(a['amount_cents'] for a in row['attempts'] if a['status'] == 'completed' and a.get('origin') != 'clover')
    summary = pricing.summarize(snapshot)
    cfg = store_config(row['store_id'])
    verified = _verified(snapshot, cfg)
    is_open = row['status'] == 'open' and not row['abandoned_reason']
    due, mode = (link['due_cents'], link['pricing_mode']) if row['status'] == 'completed' else (summary['total'], None)
    if is_open and summary['total'] is not None and (clover_cents or popcore_cents or link['plan']):
        due, mode = pricing.amount_due(summary, clover_cents=clover_cents, popcore_cents=popcore_cents,
                                       plan=link['plan'], verified=verified)
    register = row['created_by'] == REGISTER_SUB
    owner = row['created_by'] == actor['sub']
    is_manager = manager(actor)
    feed = con.execute('SELECT ok_at FROM clover_feed WHERE merchant_id=?', (link['merchant_id'],)).fetchone()
    fresh = time.time() - link['seen_at'] <= FRESH_SECONDS and bool(feed) and time.time() - feed['ok_at'] <= FRESH_SECONDS
    lines = []
    for item in snapshot['items']:
        whole = item.get('unitQty') in (None, 0, 1000)
        product = product_for_item(con, item.get('itemId'), item.get('itemCode')) if whole else None
        lines.append({'name': str(item.get('name') or 'Clover item')[:240], 'quantity': 1 if whole else item['unitQty'] / 1000,
                      'unit': item.get('unitName') or 'each', 'unit_price_cents': item.get('price'),
                      'item_code': item.get('itemCode'), 'product_id': product['id'] if product else None,
                      'product_name': product['name'] if product else None, 'mapped': product is not None})
    discounts = [{'name': str(d.get('name') or 'Discount')[:80], 'amount_cents': d.get('amount'),
                  'percentage': d.get('percentage'), 'popcore': pricing.is_popcore_discount(d)}
                 for d in snapshot['discounts']]
    pending_label = pricing.discount_label(mode) if mode and due is not None and summary['total'] is not None and due < summary['total'] else None
    remaining = max(0, due - clover_cents - popcore_cents) if is_open and due is not None else 0
    has_money = bool(clover_cents or popcore_cents)
    state = link['state'] or 'DEVICE'  # kept current by every Clover read and write
    blocked = link['review_reason'] in OPEN_BLOCKERS
    collect = pricing.collect(summary, clover_cents=clover_cents, popcore_cents=popcore_cents, verified=verified) \
        if is_open and summary['total'] is not None else None
    may_undo = writable and is_open
    undo_ids = [a['id'] for a in row['attempts'] if may_undo and a['status'] == 'completed' and a.get('origin') == 'popcore'
                and (a['recorded_by'] == actor['sub'] or is_manager)]
    return {
        'order_id': link['order_id'], 'merchant_id': link['merchant_id'], 'created_ms': link['created_ms'],
        'state': state, 'state_label': STATE_LABELS.get(state, state.replace('_', ' ').title()),
        'source': link['source'], 'seen_at': link['seen_at'], 'fresh': fresh,
        'employee_name': snapshot.get('employeeName'), 'register': register, 'register_name': REGISTER_NAME,
        'popcore_reference': f"POPCORE-{row['id']}", 'total_cents': summary['total'], 'lines': lines,
        'discounts': discounts, 'popcore_discount_label': pending_label,
        'prices': pricing.price_options(summary, verified=verified) if is_open else None,
        'pricing_verified': verified, 'plan': link['plan'], 'pricing_mode': mode, 'due_cents': due,
        'clover_cents': clover_cents, 'popcore_cents': popcore_cents, 'remaining_cents': remaining,
        'collect': collect, 'undo_attempt_ids': undo_ids, 'card_blocked': summary['cash_priced'],
        'writeback': {'state': link['writeback_state'], 'error': link['writeback_error'],
                      'mode': cfg['writeback'] if cfg else 'label', 'label': paid_label(con, row['id'])},
        'review': {'reason': link['review_reason'], 'detail': link['review_detail']} if link['review_reason'] else None,
        'delete_pending': bool(link['delete_pending']),
        'can_claim': writable and is_open and register,
        'can_release': writable and is_open and owner and not popcore_cents,
        'can_receive': writable and is_open and not blocked and (owner or register or is_manager) and summary['total'] is not None,
        'can_cancel': writable and is_open and not has_money and (owner or register or is_manager),
        'can_cancel_local': writable and is_open and not has_money and is_manager,
        'can_retry': writable and (link['writeback_state'] in ('pending', 'failed') or bool(link['delete_pending'])),
        'can_mark_handled': writable and is_manager and link['writeback_state'] == 'failed',
    }


def feed_status(con, cfg):
    feed = ensure_feed(con, cfg)
    connected = time.time() - feed['ok_at'] <= FRESH_SECONDS
    return {'enabled': True, 'environment': cfg['environment'], 'connected': connected,
            'writes': len(cfg['writer_key']) >= 32, 'fetched_at': feed['fetched_at'],
            'message': feed['error'] or ('' if connected else 'Waiting for Clover'),
            'server_ms': int(time.time() * 1000)}


def find(con, store_id, order_id):
    row = con.execute('''SELECT o.id FROM clover_orders l JOIN checkout_orders o ON o.id=l.checkout_id
        WHERE o.store_id=? AND l.order_id=?''', (store_id, order_id.strip().upper())).fetchone()
    return row['id'] if row else None
