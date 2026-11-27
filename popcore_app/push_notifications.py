"""Phone and computer notifications for new web orders (owner, 2026-11-19: "the order on woocommerce does not notify me,
it only notifies the one email that is setted, so we need our own notification thing").

Standard Web Push, so no app store and no outside account: a person taps 通知 Notify me on 网站订单 Web orders, their
browser (or POPCORE added to an iPhone's Home Screen) gives POPCORE a push address, and every new paid web order of a
store they have store operations access for is pushed there. Since 2026-11-26 that is the store computer and the
admins only (the board is closed to staff); managers turn a device on from 异常提醒 for the watcher's alerts. Messages are signed with POPCORE's own key (VAPID,
RFC 8292) and encrypted for that one device (RFC 8291), so the push service in between cannot read them. They hold the
order number, the store and the lines, never anything about the customer. Admins are also told when a delivery is
packed and no courier is booked yet (owner, 2026-11-27, popcore-work#139).

The key is made on first use and kept in a file beside the database (PUSH_KEY_FILE overrides), never in the database,
so a copy of the database cannot send notifications. A new key means every device turns notifications on again.
"""
import base64
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import db

log = logging.getLogger(__name__)

# Only the browsers' own push services: POPCORE never posts to an address a page made up.
PUSH_HOSTS = ('fcm.googleapis.com', 'android.googleapis.com', 'push.services.mozilla.com', 'push.apple.com',
              'notify.windows.com')
# The VAPID contact: the site, never a person's email.
CONTACT = 'https://popcore.store'
RECORD_SIZE = 4096
# How long a push service keeps a message for a phone that is off: a web order is news for a day.
TTL = 24 * 60 * 60
# Who a new web order is pushed to: the store computer (a 查看者 Viewer) and admins, who have 网站订单 (owner, 2026-11-26).
ORDER_ROLES = ('viewer', 'admin')
# Who can turn a device on at all: they, and managers for 异常提醒 (watcher.py). Staff have nothing to be told.
DEVICE_ROLES = ('viewer', 'manager', 'admin')
# Who hears that a delivery is packed and no courier is booked: the admins, who book FleetNow on popcore.ca (owner,
# 2026-11-27, popcore-work#139).
BOOKING_ROLES = ('admin',)


class PushError(ValueError):
    pass


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


# ── POPCORE's key ───────────────────────────────────────────────────────────

def key_file():
    return Path(os.environ.get('PUSH_KEY_FILE') or Path(db.DB_PATH).with_name('push-vapid-key.pem'))


def private_key():
    """POPCORE's VAPID key, made once and kept beside the database (readable by its owner only)."""
    path = key_file()
    try:
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    except FileNotFoundError:
        pass
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # made at the same moment by another process
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(pem)
    return key


def _point(key):
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def public_key():
    """What a browser subscribes with (applicationServerKey)."""
    return b64(_point(private_key().public_key()))


def vapid_header(endpoint, key=None, now=None):
    """The Authorization header for one push service: a JWT signed with ES256 (RFC 8292)."""
    key = key or private_key()
    parts = urlsplit(endpoint)
    claims = {'aud': f'{parts.scheme}://{parts.netloc}', 'exp': int(now or time.time()) + 12 * 60 * 60, 'sub': CONTACT}
    signing = (b64(json.dumps({'typ': 'JWT', 'alg': 'ES256'}, separators=(',', ':')).encode()) + '.'
               + b64(json.dumps(claims, separators=(',', ':')).encode()))
    r, s = decode_dss_signature(key.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
    token = f'{signing}.{b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))}'
    return f'vapid t={token}, k={b64(_point(key.public_key()))}'


# ── Encryption for one device (RFC 8291, aes128gcm) ─────────────────────────

def _hmac(key, data):
    mac = hmac.HMAC(key, hashes.SHA256())
    mac.update(data)
    return mac.finalize()


def encrypt(plaintext, p256dh, auth, *, server_key=None, salt=None):
    """The message body for the device whose subscription keys these are. server_key and salt are fresh for every
    message; they are parameters only so the RFC's worked example can be checked."""
    device = unb64(p256dh)
    secret = unb64(auth)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    server = _point(server_key.public_key())
    shared = server_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), device))
    ikm = _hmac(_hmac(secret, shared), b'WebPush: info\x00' + device + server + b'\x01')
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b'Content-Encoding: aes128gcm\x00\x01')[:16]
    nonce = _hmac(prk, b'Content-Encoding: nonce\x00\x01')[:12]
    sealed = AESGCM(cek).encrypt(nonce, plaintext + b'\x02', None)
    return salt + RECORD_SIZE.to_bytes(4, 'big') + bytes([len(server)]) + server + sealed


# ── Devices ─────────────────────────────────────────────────────────────────

def check_subscription(data):
    """(endpoint, p256dh, auth) from a browser's PushSubscription JSON, or PushError."""
    if not isinstance(data, dict):
        raise PushError('Send the browser’s push subscription.')
    endpoint, keys = data.get('endpoint'), data.get('keys')
    if not isinstance(endpoint, str) or not isinstance(keys, dict):
        raise PushError('Send the browser’s push subscription.')
    parts = urlsplit(endpoint)
    host = (parts.hostname or '').lower()
    if (parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443)
            or not any(host == known or host.endswith('.' + known) for known in PUSH_HOSTS) or len(endpoint) > 1000):
        raise PushError('That is not a browser push address.')
    p256dh, auth = keys.get('p256dh'), keys.get('auth')
    try:
        device, secret = unb64(p256dh), unb64(auth)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), device)
    except (TypeError, ValueError, AttributeError):
        raise PushError('The browser’s push keys are not valid.') from None
    if len(secret) != 16:
        raise PushError('The browser’s push keys are not valid.')
    return endpoint, p256dh, auth


def subscribe(con, sub, data, role=None):
    """Keep a device for this person, with the role they are signed in with; a device signed in as someone else
    since moves to them."""
    endpoint, p256dh, auth = check_subscription(data)
    con.execute("""INSERT INTO push_subscriptions(endpoint, auth0_sub, p256dh, auth, role) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(endpoint) DO UPDATE SET auth0_sub=excluded.auth0_sub, p256dh=excluded.p256dh,
                       auth=excluded.auth, role=excluded.role, failures=0""", (endpoint, sub, p256dh, auth, role))
    con.commit()


def remember_role(con, sub, role):
    """Keep this person's devices on the role they are signed in with now. Called when the app opens (GET
    /api/stores), so a device turned on before roles were kept, or by someone whose role changed since, is right
    after their next visit."""
    if con.execute('SELECT 1 FROM push_subscriptions WHERE auth0_sub=? AND role IS NOT ? LIMIT 1', (sub, role)).fetchone():
        con.execute('UPDATE push_subscriptions SET role=? WHERE auth0_sub=?', (role, sub))
        con.commit()


def unsubscribe(con, sub, endpoint):
    con.execute('DELETE FROM push_subscriptions WHERE endpoint=? AND auth0_sub=?', (endpoint, sub))
    con.commit()


def devices(con, sub):
    return con.execute('SELECT COUNT(*) FROM push_subscriptions WHERE auth0_sub=?', (sub,)).fetchone()[0]


# ── Sending ─────────────────────────────────────────────────────────────────

def _post(endpoint, body, headers):
    """One push; the status code. Kept apart so tests can stand in for the push services."""
    return requests.request('POST', endpoint, data=body, headers=headers, timeout=(3, 5), allow_redirects=False).status_code


def send(con, rows, message):
    """Push one message to these devices: (sent, failed). A device the push service says is gone is forgotten."""
    key = private_key()
    plaintext = json.dumps(message, ensure_ascii=False).encode()
    sent = failed = 0
    for row in rows:
        try:
            status = _post(row['endpoint'], encrypt(plaintext, row['p256dh'], row['auth']), {
                'Authorization': vapid_header(row['endpoint'], key), 'Content-Encoding': 'aes128gcm',
                'Content-Type': 'application/octet-stream', 'TTL': str(TTL), 'Urgency': 'high'})
        except (requests.RequestException, ValueError) as exc:
            status = None
            log.warning('Push to %s failed: %s', urlsplit(row['endpoint']).hostname, type(exc).__name__)
        if status in (404, 410):
            con.execute('DELETE FROM push_subscriptions WHERE endpoint=?', (row['endpoint'],))
            failed += 1
        elif status is not None and 200 <= status < 300:
            con.execute("UPDATE push_subscriptions SET failures=0, last_sent_at=datetime('now') WHERE endpoint=?",
                        (row['endpoint'],))
            sent += 1
        else:
            con.execute('UPDATE push_subscriptions SET failures=failures+1 WHERE endpoint=?', (row['endpoint'],))
            failed += 1
    con.commit()
    return sent, failed


def order_message(con, cfg, row):
    """What a phone shows for a new web order: its number, pickup or delivery and store, and the lines."""
    import website_board  # imported here: website_board imports website_sync, which calls this module
    card = website_board.card(con, row, cfg)
    lines = ', '.join(f"{line['name']} × {line['quantity']}" for line in card['lines'])
    where = f"自取 {card['store']} pickup" if card['kind'] == 'pickup' else f"配送 {' + '.join(card['stores'])} delivery"
    waiting = '需要管理员 Needs an admin · ' if row['state'] in website_board.WAITING else ''
    return {'title': f"网站订单 #{card['number']} · {where}", 'body': (waiting + lines)[:240],
            'url': '/web-orders', 'tag': f"web-order-{card['id']}"}


def _devices_at(con, roles, stores):
    """The devices of people signed in with one of `roles` who have store operations access at one of `stores`."""
    return con.execute(
        f"""SELECT p.* FROM push_subscriptions p
            WHERE (p.role IS NULL OR p.role IN ({','.join('?' * len(roles))}))
              AND EXISTS (SELECT 1 FROM inventory_access a JOIN stores s ON s.id=a.store_id
                          WHERE a.auth0_sub=p.auth0_sub AND s.code IN ({','.join('?' * len(stores))})
                            AND s.is_active=1)""", (*roles, *stores)).fetchall()


def packed_not_booked(con, cfg, wc_order_id):
    """Tell the admins at the store a delivery leaves from that it is packed and no courier is booked yet, so one of
    them books FleetNow on popcore.ca (owner, 2026-11-27, popcore-work#139). The order number and store only."""
    import website_board
    row = con.execute('SELECT * FROM website_orders WHERE wc_order_id=?', (wc_order_id,)).fetchone()
    if row is None or not row['packed_at']:
        return 0, 0
    card = website_board.card(con, row, cfg)
    if card['kind'] != 'delivery' or card['booked']:
        return 0, 0
    # Admins only: a device kept before roles were (role empty) may be the store computer that just pressed Packed.
    rows = [device for device in _devices_at(con, BOOKING_ROLES, card['stores']) if device['role'] in BOOKING_ROLES]
    stores = ' + '.join(card['stores'])
    return send(con, rows, {
        'title': f"网站订单 #{card['number']} · 已打包 Packed",
        'body': f'配送 {stores} · 快递未预约 Courier not booked yet: book it on popcore.ca (Dispatch via Fleetnow).',
        'url': '/web-orders', 'tag': f"web-order-packed-{card['id']}"}) if rows else (0, 0)


def new_order(con, cfg, wc_order_id):
    """Tell the store computer and the admins who asked, at the order's store, about a new paid web order (owner,
    2026-11-26: 网站订单 is closed to staff). A device turned on before roles were kept (`role` empty) still gets it
    until its owner next opens POPCORE, so no admin phone goes quiet on the day this went live."""
    import website_board
    row = con.execute('SELECT * FROM website_orders WHERE wc_order_id=?', (wc_order_id,)).fetchone()
    if row is None:
        return 0, 0
    # A delivery tells the store it leaves from (the store POPCORE took it from), a pickup its pickup store.
    stores = sorted(website_board.board_stores(website_board.card(con, row, cfg)))
    rows = _devices_at(con, ORDER_ROLES, stores)
    return send(con, rows, order_message(con, cfg, row)) if rows else (0, 0)
