"""Local-only checkout rehearsal. Synthetic Clover events; real disposable POPCORE ledger."""
import argparse
from contextlib import closing
from datetime import date
import json
from pathlib import Path
import re
import secrets
import sys
import threading
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, render_template, request, send_file, session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'popcore_app' / 'tests'))
from test_receiving import ReceivingFixture
from auth import ROLE_CLAIM
from inventory_commands import InventoryError, require_inventory_access
import payment_evidence

MERCHANT = 'SIMULATED-CA'
TENDERS = ('cash', 'card', 'e_transfer', 'wechat', 'alipay')
PHOTO_TENDERS = ('e_transfer', 'wechat', 'alipay')
ACTORS = ('staff', 'photo', 'manager', 'other_staff', 'other_store')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value):
        raise ValueError('A stable test identity is required (letters, digits, dash or underscore).')
    return value


def integer(value, label, minimum=0):
    if type(value) is not int or not minimum <= value <= 100_000_000:
        raise ValueError(f'{label} must be an integer between {minimum} and 100000000.')
    return value


class Rehearsal:
    def __init__(self, fixture):
        self.fixture = fixture
        # ponytail: one local process serializes simulated checkout; use transactional workers for a real connector.
        self.lock = threading.RLock()
        with closing(fixture.connect()) as con:
            exists = con.execute("SELECT 1 FROM sqlite_master WHERE name='rehearsal_orders'").fetchone()
            con.executescript('''
                CREATE TABLE IF NOT EXISTS rehearsal_orders (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, sale_id INTEGER,
                    error TEXT, FOREIGN KEY(sale_id) REFERENCES sale_documents(id));
                CREATE TABLE IF NOT EXISTS rehearsal_attempts (
                    id TEXT PRIMARY KEY, order_id TEXT NOT NULL, tender TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    FOREIGN KEY(order_id) REFERENCES rehearsal_orders(id));
                CREATE TABLE IF NOT EXISTS rehearsal_photos (
                    id INTEGER PRIMARY KEY, attempt_id TEXT NOT NULL, content_hash TEXT NOT NULL,
                    object_id TEXT NOT NULL UNIQUE, mime_type TEXT NOT NULL, byte_size INTEGER NOT NULL,
                    uploader_sub TEXT NOT NULL, evidence_id INTEGER,
                    UNIQUE(attempt_id, content_hash, uploader_sub),
                    FOREIGN KEY(attempt_id) REFERENCES rehearsal_attempts(id),
                    FOREIGN KEY(evidence_id) REFERENCES payment_evidence(id));
            ''')
            if not exists:
                con.execute('''INSERT INTO inventory_balances
                    (product_id, location_id, disposition, quantity, version)
                    VALUES (?, ?, 'saleable', 12, 1)''', (fixture.product_id, fixture.floor))
                for actor in ('photo', 'manager', 'other_staff'):
                    con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)',
                                ('auth0|' + actor, fixture.store_id))
                other = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
                con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|other_store', ?)", (other,))
                con.commit()

    def api(self, method, path, *, body=None, key=None, actor='staff'):
        response = self.fixture.client.open(path, method=method, json=body, headers={
            **self.fixture.headers(actor), **({'Idempotency-Key': key} if key else {}),
        })
        result = response.get_json()
        if response.status_code >= 400:
            raise ValueError(f"{response.status_code}: {result.get('error', 'POPCORE operation failed')}")
        return result

    def _order(self, con, order_id):
        row = con.execute('SELECT * FROM rehearsal_orders WHERE id=?', (order_id,)).fetchone()
        if row is None:
            raise ValueError('Test order does not exist.')
        return {**dict(row), 'payload': json.loads(row['payload'])}

    def _attempt(self, con, attempt_id):
        row = con.execute('SELECT * FROM rehearsal_attempts WHERE id=?', (attempt_id,)).fetchone()
        if row is None:
            raise ValueError('Payment attempt does not exist.')
        return dict(row)

    def _photo_access(self, actor):
        if actor not in ACTORS:
            raise PermissionError('Unknown simulated employee.')
        with closing(self.fixture.connect()) as con:
            require_inventory_access(con, {
                'sub': 'auth0|' + actor, ROLE_CLAIM: 'manager' if actor == 'manager' else 'staff',
            }, [self.fixture.store_id], 'staff')
        if actor not in ('staff', 'photo', 'manager'):
            raise PermissionError('This employee is not assigned to these payment tasks.')

    def create_order(self, data):
        if not isinstance(data, dict):
            raise ValueError('Test order must be an object.')
        body = {'id': identifier(data.get('id')), 'item': data.get('item')}
        if body['item'] not in ('BOX-DEMO', 'UNKNOWN'):
            raise ValueError('Choose a mapped demo product or the unmapped test item.')
        for field in ('quantity', 'original_cents', 'suggested_cents', 'subtotal_cents', 'tax_cents'):
            body[field] = integer(data.get(field), field, 1 if field == 'quantity' else 0)
        body['total_cents'] = integer(body['subtotal_cents'] + body['tax_cents'], 'Final total', 1)
        with self.lock, closing(self.fixture.connect()) as con:
            existing = con.execute('SELECT payload FROM rehearsal_orders WHERE id=?', (body['id'],)).fetchone()
            if existing:
                previous = json.loads(existing[0])
                body['business_date'] = previous['business_date']
                if previous != body:
                    raise ValueError('This order identity already has different facts. Use a new order.')
            else:
                body['business_date'] = date.today().isoformat()
                con.execute('INSERT INTO rehearsal_orders(id, payload) VALUES (?, ?)',
                            (body['id'], json.dumps(body, sort_keys=True)))
                con.commit()
        return body

    def start_attempt(self, data):
        if not isinstance(data, dict):
            raise ValueError('Payment attempt must be an object.')
        values = (identifier(data.get('id')), identifier(data.get('order_id')),
                  data.get('tender'), integer(data.get('amount_cents'), 'Payment amount', 1))
        if values[2] not in TENDERS:
            raise ValueError('Choose a supported tender.')
        with self.lock, closing(self.fixture.connect()) as con:
            old = con.execute('SELECT * FROM rehearsal_attempts WHERE id=?', (values[0],)).fetchone()
            if old:
                if tuple(old[k] for k in ('id', 'order_id', 'tender', 'amount_cents')) != values:
                    raise ValueError('This payment identity already has different facts.')
                return dict(old)
            order = self._order(con, values[1])
            paid = con.execute("SELECT COALESCE(sum(amount_cents),0) FROM rehearsal_attempts WHERE order_id=? AND status='completed'", (values[1],)).fetchone()[0]
            if values[3] > order['payload']['total_cents'] - paid:
                raise ValueError('Payment exceeds the remaining amount.')
            con.execute("UPDATE rehearsal_attempts SET status='cancelled' WHERE order_id=? AND status='pending'", (values[1],))
            con.execute('INSERT INTO rehearsal_attempts(id,order_id,tender,amount_cents) VALUES (?,?,?,?)', values)
            con.commit()
            return self._attempt(con, values[0])

    def cancel(self, attempt_id):
        with self.lock, closing(self.fixture.connect()) as con:
            attempt = self._attempt(con, attempt_id)
            if attempt['status'] == 'completed':
                raise ValueError('Completed payments need a separately recorded refund.')
            con.execute("UPDATE rehearsal_attempts SET status='cancelled' WHERE id=?", (attempt_id,))
            con.commit()

    def complete(self, attempt_id):
        with self.lock, closing(self.fixture.connect()) as con:
            attempt = self._attempt(con, attempt_id)
            if attempt['status'] == 'cancelled':
                raise ValueError('This payment was cancelled or replaced.')
            con.execute("UPDATE rehearsal_attempts SET status='completed' WHERE id=?", (attempt_id,))
            con.commit()
            self.reconcile()

    def _attach_photos(self, con, attempt_id, payment_id):
        for photo in con.execute('SELECT * FROM rehearsal_photos WHERE attempt_id=? AND evidence_id IS NULL', (attempt_id,)).fetchall():
            evidence_id = con.execute('''INSERT INTO payment_evidence
                (payment_id, object_id, mime_type, byte_size, uploader_sub)
                VALUES (?, ?, ?, ?, ?)''', (payment_id, photo['object_id'], photo['mime_type'],
                                           photo['byte_size'], photo['uploader_sub'])).lastrowid
            con.execute('UPDATE rehearsal_photos SET evidence_id=? WHERE id=?', (evidence_id, photo['id']))
        con.commit()

    def _import(self, order, attempts):
        body = order['payload']
        with closing(self.fixture.connect()) as con:
            source = con.execute('''SELECT sale_id FROM sale_sources WHERE source_system='clover_rehearsal'
                AND source_account=? AND source_reference=?''', (MERCHANT, order['id'])).fetchone()
        if source:
            sale_id = source[0]
        else:
            line = {'quantity': body['quantity']}
            if body['item'] == 'BOX-DEMO':
                line.update(product_id=self.fixture.product_id, unit='piece')
            else:
                line['raw_product_text'] = 'Unmapped synthetic Clover item: UNKNOWN'
            created = self.api('POST', '/api/sale-documents', key='rehearsal-create:' + order['id'], body={
                'store_id': self.fixture.store_id, 'business_date': body['business_date'],
                'entry_mode': 'already_paid',
                'source': {'system': 'clover_rehearsal', 'account': MERCHANT, 'reference': order['id']},
                'subtotal_cents': body['subtotal_cents'], 'source_tax_cents': body['tax_cents'],
                'gross_cents': body['total_cents'], 'reduction_cents': 0, 'rounding_cents': 0,
                'collected_cents': body['total_cents'], 'lines': [line],
            })
            sale_id = created['sale_id']
        with closing(self.fixture.connect()) as con:
            con.execute('UPDATE rehearsal_orders SET sale_id=? WHERE id=?', (sale_id, order['id']))
            con.commit()
        path = f'/api/sale-documents/{sale_id}'
        detail = self.api('GET', path)
        if detail['status'] == 'draft':
            self.api('POST', path + '/post', key='rehearsal-post:' + order['id'],
                     body={'expected_version': detail['version']})
        for attempt in attempts:
            detail = self.api('GET', path)
            payment = next((p for p in detail['payments'] if p['source_system'] == 'clover_rehearsal'
                            and p['source_account'] == MERCHANT and p['source_reference'] == attempt['id']), None)
            if payment is None:
                detail = self.api('POST', path + '/payments', key='rehearsal-payment:' + attempt['id'], body={
                    'expected_version': detail['version'], 'payments': [{
                        'tender': attempt['tender'], 'amount_cents': attempt['amount_cents'],
                        'source_system': 'clover_rehearsal', 'source_account': MERCHANT,
                        'source_reference': attempt['id'],
                    }],
                })
                payment = next(p for p in detail['payments'] if p['source_reference'] == attempt['id'])
            with closing(self.fixture.connect()) as con:
                self._attach_photos(con, attempt['id'], payment['id'])

    def reconcile(self):
        errors = []
        with self.lock, closing(self.fixture.connect()) as con:
            for row in con.execute('SELECT id FROM rehearsal_orders').fetchall():
                order = self._order(con, row['id'])
                attempts = [dict(a) for a in con.execute("SELECT * FROM rehearsal_attempts WHERE order_id=? AND status='completed' ORDER BY rowid", (order['id'],))]
                if sum(a['amount_cents'] for a in attempts) != order['payload']['total_cents']:
                    continue
                error = None
                try:
                    self._import(order, attempts)
                except (ValueError, InventoryError) as exc:
                    error = str(exc)
                    errors.append(order['id'] + ': ' + error)
                con.execute('UPDATE rehearsal_orders SET error=? WHERE id=?', (error, order['id']))
                con.commit()
        if errors:
            raise ValueError('; '.join(errors))

    def upload(self, attempt_id, upload, actor):
        self._photo_access(actor)
        with self.lock, closing(self.fixture.connect()) as con:
            attempt = self._attempt(con, attempt_id)
            if attempt['status'] == 'cancelled' or attempt['tender'] not in PHOTO_TENDERS:
                raise ValueError('This payment attempt does not accept evidence.')
            try:
                image = payment_evidence.prepare_image(upload)
            except InventoryError as exc:
                raise ValueError(str(exc)) from exc
            prior = con.execute('''SELECT id FROM rehearsal_photos
                WHERE attempt_id=? AND content_hash=? AND uploader_sub=?''',
                (attempt_id, image['content_hash'], 'auth0|' + actor)).fetchone()
            if prior:
                photo_id = prior['id']
            else:
                path = payment_evidence.publish(image)
                try:
                    photo_id = con.execute('''INSERT INTO rehearsal_photos
                        (attempt_id,content_hash,object_id,mime_type,byte_size,uploader_sub)
                        VALUES (?,?,?,?,?,?)''', (attempt_id, image['content_hash'], image['object_id'],
                        image['mime_type'], image['byte_size'], 'auth0|' + actor)).lastrowid
                    con.commit()
                except Exception:
                    con.rollback()
                    path.unlink(missing_ok=True)
                    raise
            payment = con.execute('''SELECT id FROM sale_payments WHERE source_system='clover_rehearsal'
                AND source_account=? AND source_reference=?''', (MERCHANT, attempt_id)).fetchone()
            if payment:
                self._attach_photos(con, attempt_id, payment['id'])
            return {'id': photo_id}

    def state(self, actor='staff'):
        if actor not in ACTORS:
            raise PermissionError('Unknown simulated employee.')
        try:
            self._photo_access(actor)
            allowed = True
        except PermissionError:
            allowed = False
        with self.lock, closing(self.fixture.connect()) as con:
            orders, tasks = [], []
            for row in con.execute('SELECT id FROM rehearsal_orders ORDER BY rowid DESC'):
                order = self._order(con, row['id'])
                attempts = [dict(a) for a in con.execute('SELECT * FROM rehearsal_attempts WHERE order_id=? ORDER BY rowid', (order['id'],))]
                paid = sum(a['amount_cents'] for a in attempts if a['status'] == 'completed')
                allocation = None
                if order['sale_id']:
                    allocation = con.execute('SELECT allocation_status FROM sale_documents WHERE id=?', (order['sale_id'],)).fetchone()[0]
                if actor in ('staff', 'manager'):
                    orders.append({**order['payload'], 'sale_id': order['sale_id'], 'error': order['error'],
                                   'attempts': attempts, 'paid_cents': paid, 'allocation': allocation})
                if not allowed:
                    continue
                for attempt in attempts:
                    if attempt['tender'] not in PHOTO_TENDERS or attempt['status'] == 'cancelled':
                        continue
                    photos = [dict(p) for p in con.execute('SELECT id,evidence_id,uploader_sub FROM rehearsal_photos WHERE attempt_id=?', (attempt['id'],))]
                    tasks.append({**attempt, 'photos': photos, 'quantity': order['payload']['quantity'],
                                  'item': order['payload']['item'], 'cashier': 'staff', 'assigned_to': 'photo',
                                  'evidence_status': ('attached' if any(p['evidence_id'] for p in photos)
                                                      else 'saved for this attempt' if photos else 'missing')})
            stock = con.execute('SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=? AND disposition=\'saleable\'',
                                (self.fixture.product_id, self.fixture.floor)).fetchone()[0]
        return {'environment': 'local simulation', 'orders': orders, 'tasks': tasks,
                'stock': stock if actor in ('staff', 'manager') else None}


def build_app(demo):
    app = Flask(__name__, template_folder=str(Path(__file__).parent))
    app.config.update(SECRET_KEY=secrets.token_hex(32), MAX_CONTENT_LENGTH=11 * 1024 * 1024,
                      SESSION_COOKIE_NAME='clover_rehearsal', SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict')

    @app.before_request
    def local_only():
        if request.remote_addr not in ('127.0.0.1', '::1') or urlsplit('http://' + request.host).hostname not in ('localhost', '127.0.0.1', '::1'):
            abort(403)
        if request.method != 'GET':
            expected = session.get('csrf', '')
            if not expected or not secrets.compare_digest(expected, request.headers.get('X-Rehearsal-CSRF', '')):
                abort(403)

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
                                 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
                                 'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; frame-ancestors 'none'; form-action 'self'"})
        return response

    @app.errorhandler(ValueError)
    @app.errorhandler(PermissionError)
    def error(exc):
        return jsonify(error=str(exc)), 403 if isinstance(exc, PermissionError) else 409

    @app.get('/')
    def home():
        session.setdefault('csrf', secrets.token_urlsafe(32))
        return render_template('clover_rehearsal.html', csrf=session['csrf'])

    @app.get('/state')
    def state():
        return jsonify(demo.state(request.args.get('actor', 'staff')))

    @app.get('/photo/<int:photo_id>')
    def photo(photo_id):
        actor = request.args.get('actor', 'photo')
        demo._photo_access(actor)
        with closing(demo.fixture.connect()) as con:
            row = con.execute('SELECT * FROM rehearsal_photos WHERE id=?', (photo_id,)).fetchone()
            if row is None:
                abort(404)
            # Preserve the product's uploader/scoped-manager byte access rule.
            if actor != 'manager' and row['uploader_sub'] != 'auth0|' + actor:
                raise PermissionError('Only the uploader or scoped manager can read this photo.')
        return send_file(payment_evidence.evidence_path(row['object_id']), mimetype=row['mime_type'])

    @app.post('/action/<action>')
    def act(action):
        data = request.get_json(silent=True) if request.is_json else request.form
        if data is None or not hasattr(data, 'get'):
            raise ValueError('Action data must be an object.')
        if action == 'order': result = demo.create_order(data)
        elif action == 'start': result = demo.start_attempt(data)
        elif action == 'complete': result = demo.complete(identifier(data.get('id')))
        elif action == 'cancel': result = demo.cancel(identifier(data.get('id')))
        elif action == 'reconcile': result = demo.reconcile()
        elif action == 'upload': result = demo.upload(identifier(data.get('id')), request.files.get('image'), data.get('actor', 'photo'))
        else: abort(404)
        return jsonify(result or {'ok': True})

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5056)
    args = parser.parse_args()
    fixture = ReceivingFixture()
    fixture.setUp()
    try:
        demo = Rehearsal(fixture)
        print(f'Local simulation: http://127.0.0.1:{args.port}/ — no Clover connection; data deleted on exit.', flush=True)
        build_app(demo).run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False, threaded=False)
    finally:
        fixture.tearDown()


if __name__ == '__main__':
    main()
