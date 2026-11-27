"""Notifications for new web orders (owner, 2026-11-19: "we need our own notification thing"): Web Push, signed with
POPCORE's own key (RFC 8292) and encrypted for each device (RFC 8291), to the devices of the store computer and the
admins (owner, 2026-11-26: the board is closed to staff) with store operations access for the order's store. Only the browsers' push services are ever posted to, nothing about the customer is in a
message, a device the push service calls gone is forgotten, and a failed notification never stops the sync."""
import base64
import json
import os
import stat
from contextlib import closing
from unittest.mock import patch

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from test_website_board import ADMIN, OTHER, PRIVATE, BoardCase
from test_website_sync import _ago

import push_notifications as push

MK_ADMIN = {'Authorization': 'Bearer admin:mkstaff'}  # auth0|mkstaff, an admin with MK store operations access only


class Device:
    """A browser's side of Web Push: its keys, its subscription, and decrypting what it receives."""

    def __init__(self, name):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.endpoint = f'https://fcm.googleapis.com/fcm/send/{name}'

    def subscription(self, endpoint=None):
        return {'endpoint': endpoint or self.endpoint,
                'keys': {'p256dh': push.b64(push._point(self.key.public_key())), 'auth': push.b64(self.auth)}}

    def read(self, body):
        salt, size, server = body[:16], body[16:20], body[21:21 + body[20]]
        self_point = push._point(self.key.public_key())
        shared = self.key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), server))
        prk = push._hmac(salt, push._hmac(push._hmac(self.auth, shared), b'WebPush: info\x00' + self_point + server + b'\x01'))
        record = AESGCM(push._hmac(prk, b'Content-Encoding: aes128gcm\x00\x01')[:16]).decrypt(
            push._hmac(prk, b'Content-Encoding: nonce\x00\x01')[:12], body[21 + body[20]:], None)
        assert int.from_bytes(size, 'big') == push.RECORD_SIZE and record.endswith(b'\x02')
        return json.loads(record[:-1])


class PushCase(BoardCase):
    """The board's orders, with every push caught instead of sent."""

    def setUp(self):
        super().setUp()
        self.sent = []  # (endpoint, body, headers)
        self.answer = 201
        poster = patch.object(push, '_post', side_effect=self.post)
        poster.start()
        self.addCleanup(poster.stop)
        with closing(self.connect()) as con:
            mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|mkstaff', ?)", (mk,))
            con.commit()

    def post(self, endpoint, body, headers):
        if isinstance(self.answer, Exception):
            raise self.answer
        self.sent.append((endpoint, body, headers))
        return self.answer

    def notify_me(self, device, headers=ADMIN):
        return self.client.post('/api/website/notifications', json={'subscription': device.subscription()},
                                headers=headers)


class WebOrderNotificationTests(PushCase):
    def test_encryption_matches_the_rfc_example_and_the_key_signs_for_each_push_service(self):
        # RFC 8291 section 5.
        server = ec.derive_private_key(int.from_bytes(push.unb64('yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw'), 'big'),
                                       ec.SECP256R1())
        body = push.encrypt(push.unb64('V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24'),
                            'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4',
                            'BTBZMqHH6r4Tts7J_aSIgg', server_key=server, salt=push.unb64('DGv6ra1nlYgDCS1FRnbzlw'))
        self.assertEqual(push.b64(body),
                         'DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzA'
                         'C8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN')
        # RFC 8292: an ES256 JWT for the push service's origin, with POPCORE's public key beside it.
        header = push.vapid_header('https://fcm.googleapis.com/fcm/send/abc')
        token, key = header.removeprefix('vapid t=').split(', k=')
        self.assertEqual(key, push.public_key())
        head, claims, signature = token.split('.')
        self.assertEqual(json.loads(push.unb64(head)), {'typ': 'JWT', 'alg': 'ES256'})
        claims_data = json.loads(push.unb64(claims))
        self.assertEqual((claims_data['aud'], claims_data['sub']), ('https://fcm.googleapis.com', 'https://popcore.store'))
        raw = push.unb64(signature)
        public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.unb64(key))
        public.verify(encode_dss_signature(int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big')),
                      f'{head}.{claims}'.encode(), ec.ECDSA(hashes.SHA256()))
        with self.assertRaises(InvalidSignature):
            public.verify(encode_dss_signature(int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big')),
                          f'{head}.{claims}x'.encode(), ec.ECDSA(hashes.SHA256()))

    def test_the_key_is_made_once_beside_the_database_and_only_push_services_are_accepted(self):
        answer = self.client.get('/api/website/notifications', headers=ADMIN).get_json()
        self.assertEqual(answer['devices'], 0)
        self.assertEqual(push.unb64(answer['key'])[:1], b'\x04')
        path = push.key_file()
        self.assertEqual(path.parent, push.Path(push.db.DB_PATH).parent)
        if os.name == 'posix':  # Windows has no owner-only file modes; the server is Linux
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(self.client.get('/api/website/notifications', headers=ADMIN).get_json()['key'], answer['key'])
        with closing(self.connect()) as con:  # never in the database
            self.assertNotIn('PRIVATE KEY', '\n'.join(con.iterdump()))
        device = Device('a')
        for endpoint in ('http://fcm.googleapis.com/fcm/send/a', 'https://127.0.0.1/fcm/send/a', 'https://localhost:5000/x',
                         'https://evil.example/fcm.googleapis.com', 'https://fcm.googleapis.com.evil.example/a',
                         'https://fcm.googleapis.com:8443/a', 'https://user@fcm.googleapis.com/a'):
            response = self.client.post('/api/website/notifications', json={'subscription': device.subscription(endpoint)},
                                        headers=ADMIN)
            self.assertEqual(response.status_code, 400, endpoint)
        broken = device.subscription()
        broken['keys']['p256dh'] = push.b64(b'\x04' + b'\x01' * 64)
        self.assertEqual(self.client.post('/api/website/notifications', json={'subscription': broken},
                                          headers=ADMIN).status_code, 400)
        self.assertEqual(self.notify_me(device).get_json(), {'devices': 1})
        for host in ('web.push.apple.com', 'updates.push.services.mozilla.com', 'wns2-by3p.notify.windows.com'):
            self.assertEqual(self.client.post('/api/website/notifications', headers=OTHER, json={
                'subscription': Device('y').subscription(f'https://{host}/push/y')}).status_code, 200, host)
        # A device signed in as someone else since is theirs; only its owner turns it off.
        self.notify_me(device, headers=MK_ADMIN)
        self.assertEqual(self.client.get('/api/website/notifications', headers=ADMIN).get_json()['devices'], 0)
        self.client.post('/api/website/notifications/off', json={'endpoint': device.endpoint}, headers=ADMIN)
        self.assertEqual(self.client.get('/api/website/notifications', headers=MK_ADMIN).get_json()['devices'], 1)
        self.assertEqual(self.client.post('/api/website/notifications/off', json={'endpoint': device.endpoint},
                                          headers=MK_ADMIN).get_json(), {'devices': 0})
        # A viewer counter account turns them on too (owner, 2026-11-19); an account with no role cannot.
        self.assertEqual(self.client.get('/api/website/notifications', headers=self.headers('viewer')).status_code, 200)
        self.assertEqual(self.client.get('/api/website/notifications', headers=self.headers('norole')).status_code, 403)

    def test_a_new_paid_order_reaches_the_devices_of_its_store_only(self):
        dt, mk, nobody = Device('dt'), Device('mk'), Device('nobody')
        self.notify_me(dt)
        self.notify_me(mk, headers=MK_ADMIN)
        self.notify_me(nobody, headers=OTHER)
        self.woo.order(801, [(11, 2)], pickup='dt', meta=[('_popcore_pickup_code', 'PUSH01')])
        self.woo.order(802, [(11, 1)], pickup='mk', modified=_ago(0))
        self.woo.order(803, [(11, 1)], modified=_ago(0))  # a delivery: DT's
        self.run_sync()
        self.assertEqual([endpoint for endpoint, _, _ in self.sent], [dt.endpoint, mk.endpoint, dt.endpoint])
        endpoint, body, headers = self.sent[0]
        self.assertEqual((headers['Content-Encoding'], headers['TTL'], headers['Urgency']), ('aes128gcm', '86400', 'high'))
        self.assertTrue(headers['Authorization'].startswith('vapid t='))
        self.assertEqual(dt.read(body), {'title': '网站订单 #801 · 自取 DT pickup', 'body': 'Echo × 2',
                                         'url': '/web-orders', 'tag': 'web-order-801'})
        self.assertEqual(mk.read(self.sent[1][1])['title'], '网站订单 #802 · 自取 MK pickup')
        self.assertEqual(dt.read(self.sent[2][1])['title'], '网站订单 #803 · 配送 delivery')
        for _, body, _ in self.sent:
            for value in (*PRIVATE, 'PUSH01'):
                self.assertNotIn(value.encode(), body)
        # Read again, changed, or handed over: never announced twice.
        self.woo.orders[801]['date_modified_gmt'] = _ago(0)
        self.run_sync()
        self.assertEqual(self.step(801, 'ready').get_json()['result'], 'done')
        self.run_sync()
        self.assertEqual(len(self.sent), 3)
        # One POPCORE cannot fill says so.
        self.woo.order(804, [(12, 5)], pickup='dt', modified=_ago(0))
        self.run_sync()
        self.assertEqual(dt.read(self.sent[-1][1])['body'], '需要经理 Needs a manager · Echo 整端 × 5')

    def test_gone_devices_are_forgotten_and_a_failed_notification_never_stops_the_sync(self):
        device = Device('gone')
        self.notify_me(device)
        self.answer = 410
        self.woo.order(811, [(11, 1)], pickup='dt')
        self.run_sync()
        self.assertEqual(self.order_row(811)['state'], 'recorded')
        self.assertEqual(self.client.get('/api/website/notifications', headers=ADMIN).get_json()['devices'], 0)
        for problem, order, failures in ((requests.ConnectionError('down'), 812, 1), (RuntimeError('unexpected'), 813, 0)):
            self.notify_me(device)
            self.answer = problem
            self.woo.order(order, [(11, 1)], pickup='dt', modified=_ago(0))
            self.run_sync()
            self.assertEqual(self.order_row(order)['state'], 'recorded')
            self.assertIsNone(self.state('last_error'))
            with closing(self.connect()) as con:  # an unexpected error leaves the device as it was
                self.assertEqual(con.execute('SELECT failures FROM push_subscriptions').fetchone()[0], failures)
        # The test button reaches every device of this person.
        self.answer = 201
        self.notify_me(Device('second'))
        result = self.client.post('/api/website/notifications/test', headers=ADMIN).get_json()
        self.assertEqual((result['sent'], result['failed'], result['devices']), (2, 0, 2))
        body = next(body for endpoint, body, _ in reversed(self.sent) if endpoint == device.endpoint)
        self.assertEqual(device.read(body)['body'], '通知已开启 Notifications are on.')


class WhoIsNotifiedTests(PushCase):
    """Owner, 2026-11-26: 网站订单 is the store computer's and the admin's, so only their devices hear of a new web
    order. Managers keep a device for 异常提醒; staff have nothing to be told."""

    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            con.executemany('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)',
                            [(f'auth0|{name}', self.dt) for name in ('counter', 'manager', 'amy')])
            con.commit()

    def roles(self):
        with closing(self.connect()) as con:
            return dict(con.execute('SELECT auth0_sub, role FROM push_subscriptions'))

    def test_staff_cannot_turn_a_device_on_but_anyone_turns_their_own_off(self):
        device = Device('staff')
        staff = self.headers('staff:amy')
        for response in (self.client.get('/api/website/notifications', headers=staff), self.notify_me(device, headers=staff),
                         self.client.post('/api/website/notifications/test', headers=staff)):
            self.assertEqual((response.status_code, response.get_json()['code']), (403, 'notifications_not_for_staff'))
        self.assertEqual(self.roles(), {})
        # A device a staff member turned on before this still turns off.
        with closing(self.connect()) as con:
            push.subscribe(con, 'auth0|amy', device.subscription())
        self.assertEqual(self.client.post('/api/website/notifications/off', json={'endpoint': device.endpoint},
                                          headers=staff).get_json(), {'devices': 0})

    def test_a_new_order_reaches_the_store_computer_and_the_admin_only(self):
        admin, counter, manager = Device('admin'), Device('counter'), Device('manager')
        self.notify_me(admin)
        self.notify_me(counter, headers=self.headers('viewer:counter'))
        self.assertEqual(self.notify_me(manager, headers=self.headers('manager')).status_code, 200)   # for 异常提醒
        self.assertEqual(self.roles(), {'auth0|staff': 'admin', 'auth0|counter': 'viewer', 'auth0|manager': 'manager'})
        self.woo.order(821, [(11, 1)], pickup='dt')
        self.run_sync()
        self.assertEqual(sorted(endpoint for endpoint, _, _ in self.sent), sorted([admin.endpoint, counter.endpoint]))

    def test_a_device_from_before_follows_its_owners_role_once_they_open_the_app(self):
        old_staff, old_admin = Device('old-staff'), Device('old-admin')
        with closing(self.connect()) as con:   # turned on before roles were kept: no role
            push.subscribe(con, 'auth0|amy', old_staff.subscription())
            push.subscribe(con, 'auth0|staff', old_admin.subscription())
        self.assertEqual(self.roles(), {'auth0|amy': None, 'auth0|staff': None})
        self.woo.order(831, [(11, 1)], pickup='dt')
        self.run_sync()   # nobody goes quiet on the day this goes live
        self.assertEqual(sorted(endpoint for endpoint, _, _ in self.sent), sorted([old_staff.endpoint, old_admin.endpoint]))
        # The app opens with GET /api/stores: each account's devices take its role.
        for headers in (self.headers('staff:amy'), ADMIN):
            self.assertEqual(self.client.get('/api/stores', headers=headers).status_code, 200)
        self.assertEqual(self.roles(), {'auth0|amy': 'staff', 'auth0|staff': 'admin'})
        self.sent.clear()
        self.woo.order(832, [(11, 1)], pickup='dt', modified=_ago(0))
        self.run_sync()
        self.assertEqual([endpoint for endpoint, _, _ in self.sent], [old_admin.endpoint])
