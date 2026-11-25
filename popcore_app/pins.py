"""Name + PIN on the shared store computer (owner, 2026-11-20: every action leaves a mark of who did it).

The store computer signs in as one 查看者 Viewer counter account with 门店操作权限. Before it changes anything, the
person at the computer taps their name and types their own 4-digit PIN: the request carries
`X-Popcore-Person: <employee id>:<pin>`, and the change is marked with that person, by way of the counter
account, in 动态 and wherever the app names who did it (the website order note too). Permissions stay the counter
account's own; the PIN only says who. Looking around needs no PIN.

Staff set their own PIN from their phone (我的 PIN); a manager can clear a forgotten one. Five wrong PINs in a row
lock that person's PIN for five minutes. PINs are kept salted and hashed, never as typed.
"""
import hashlib
import hmac
import os
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, request

import db
from auth import ROLE_CLAIM, login_required, role_required

bp = Blueprint('pins', __name__)
HEADER = 'X-Popcore-Person'
ROUNDS = 60_000
TRIES, LOCK_MINUTES = 5, 5
PIN = re.compile(r'\d{4}')
MUTATING = {'POST', 'PUT', 'PATCH', 'DELETE'}
# What the store computer may do without a name: setting up its own notifications, looking up a pickup code.
FREE = {'website.web_orders_code', 'website.web_orders_notify_me', 'website.web_orders_notify_off',
        'website.web_orders_notify_test', 'pins.check_pin'}


class PinError(Exception):
    def __init__(self, message, code, status=403):
        super().__init__(message)
        self.code, self.status = code, status


def migrate(cur):
    columns = {row[1] for row in cur.execute('PRAGMA table_info(employees)')}
    for column, kind in (('pin_hash', 'TEXT'), ('pin_salt', 'TEXT'), ('pin_set_at', 'TEXT'),
                         ('pin_failures', 'INTEGER NOT NULL DEFAULT 0'), ('pin_locked_until', 'TEXT')):
        if column not in columns:
            cur.execute(f'ALTER TABLE employees ADD COLUMN {column} {kind}')


def _hash(pin, salt):
    return hashlib.pbkdf2_hmac('sha256', pin.encode(), bytes.fromhex(salt), ROUNDS).hex()


def _now():
    return datetime.now(timezone.utc)


def verify(con, employee_id, pin):
    """The employee whose PIN this is, or PinError. Counts wrong tries and locks after five."""
    if not isinstance(employee_id, int) or not isinstance(pin, str) or not PIN.fullmatch(pin):
        raise PinError('Tap your name and type your 4-digit PIN.', 'pin_needed', 428)
    row = con.execute('SELECT * FROM employees WHERE id=? AND is_active=1', (employee_id,)).fetchone()
    if row is None or not row['pin_hash']:
        raise PinError('This person has no PIN yet: set one from 我的 PIN on your phone.', 'pin_not_set')
    if row['pin_locked_until'] and row['pin_locked_until'] > _now().isoformat():
        raise PinError('Too many wrong PINs: try again in a few minutes, or ask a manager to reset it.', 'pin_locked')
    if not hmac.compare_digest(_hash(pin, row['pin_salt']), row['pin_hash']):
        failures = (row['pin_failures'] or 0) + 1
        locked = (_now() + timedelta(minutes=LOCK_MINUTES)).isoformat() if failures >= TRIES else None
        con.execute('UPDATE employees SET pin_failures=?, pin_locked_until=? WHERE id=?',
                    (0 if locked else failures, locked, employee_id))
        con.commit()
        raise PinError('Wrong PIN.', 'pin_wrong')
    if row['pin_failures'] or row['pin_locked_until']:
        con.execute('UPDATE employees SET pin_failures=0, pin_locked_until=NULL WHERE id=?', (employee_id,))
        con.commit()
    return row


def _is_counter(con, actor):
    """A 查看者 Viewer with 门店操作权限: the store computer's shared account."""
    if not actor or actor.get(ROLE_CLAIM, 'viewer') != 'viewer':
        return False
    return con.execute('SELECT 1 FROM inventory_access WHERE auth0_sub=? LIMIT 1', (actor.get('sub'),)).fetchone() is not None


def acting_person(con, actor):
    """Read the name + PIN header: the person doing this request, or None. The counter account must give one for
    every change; anyone else may (a PIN only ever says who)."""
    raw = request.headers.get(HEADER, '').strip()
    endpoint = request.endpoint or ''
    if not raw:
        if request.method in MUTATING and endpoint not in FREE and _is_counter(con, actor):
            raise PinError('Tap your name and type your PIN first.', 'person_needed', 428)
        return None
    employee, _, pin = raw.partition(':')
    row = verify(con, int(employee) if employee.isdigit() else None, pin)
    return {'sub': row['auth0_id'], 'name': row['name'], 'employee_id': row['id']}


def init_app(app):
    app.register_blueprint(bp)


def guard():
    """Called once the request's account is known (auth.py): sets g.acting_person or refuses the change."""
    actor = getattr(request, 'jwt_payload', None)
    raw = request.headers.get(HEADER, '').strip()
    if not actor or (not raw and (request.method not in MUTATING or actor.get(ROLE_CLAIM, 'viewer') != 'viewer')):
        return None
    con = db.get_db()
    try:
        g.acting_person = acting_person(con, actor)
    except PinError as exc:
        return jsonify(error=str(exc), code=exc.code), exc.status
    return None


# ── Routes ────────────────────────────────────────────────────────────────────

def _me(con):
    return con.execute('SELECT * FROM employees WHERE auth0_id=? AND is_active=1',
                       (request.jwt_payload.get('sub'),)).fetchone()


@bp.get('/api/me/pin')
@login_required
def my_pin():
    row = _me(db.get_db())
    return jsonify(has_profile=row is not None, has_pin=bool(row and row['pin_hash']), set_at=row and row['pin_set_at'])


@bp.post('/api/me/pin')
@login_required
def set_my_pin():
    """Set or change one's own PIN (from one's own phone, signed in as oneself)."""
    data = request.get_json(silent=True) or {}
    pin = data.get('pin')
    if not isinstance(pin, str) or not PIN.fullmatch(pin) or len(set(pin)) == 1 or pin in ('1234', '4321', '0123'):
        return jsonify(error='Choose 4 digits that are not all the same or in a row.', code='pin_weak'), 400
    con = db.get_db()
    row = _me(con)
    if row is None:
        return jsonify(error='Only staff with a staff profile have a PIN.', code='no_profile'), 403
    salt = os.urandom(16).hex()
    con.execute("""UPDATE employees SET pin_hash=?, pin_salt=?, pin_set_at=?, pin_failures=0, pin_locked_until=NULL
                   WHERE id=?""", (_hash(pin, salt), salt, _now().isoformat(), row['id']))
    con.commit()
    return jsonify(ok=True)


@bp.delete('/api/employees/<int:employee_id>/pin')
@role_required('manager')
def clear_pin(employee_id):
    """A manager clears a forgotten PIN; the person sets a new one from their phone."""
    con = db.get_db()
    updated = con.execute("""UPDATE employees SET pin_hash=NULL, pin_salt=NULL, pin_set_at=NULL, pin_failures=0,
                                 pin_locked_until=NULL WHERE id=?""", (employee_id,))
    con.commit()
    if not updated.rowcount:
        return jsonify(error='Employee not found'), 404
    return jsonify(ok=True)


@bp.get('/api/counter/people')
@login_required
def counter_people():
    """Who can be picked on the store computer: active staff of this store with a PIN, today's shift first."""
    con = db.get_db()
    code = (request.args.get('store_code') or '').strip().upper()
    store = con.execute('SELECT id FROM stores WHERE code=?', (code,)).fetchone() if code else None
    from checkout_access import business_date
    today = business_date()
    rows = con.execute(
        """SELECT e.id, e.name, e.pin_hash IS NOT NULL AS has_pin,
                  EXISTS(SELECT 1 FROM shifts s WHERE s.employee_id=e.id AND s.date=? AND (? IS NULL OR s.store_id=?)) AS on_shift,
                  (? IS NULL OR EXISTS(SELECT 1 FROM employee_stores es WHERE es.employee_id=e.id AND es.store_id=?)
                   OR EXISTS(SELECT 1 FROM shifts s WHERE s.employee_id=e.id AND s.store_id=?)
                   OR EXISTS(SELECT 1 FROM inventory_access a WHERE a.auth0_sub=e.auth0_id AND a.store_id=?)) AS here
           FROM employees e WHERE e.is_active=1 AND COALESCE(e.name, '') != ''
           ORDER BY on_shift DESC, e.name COLLATE NOCASE""",
        (today, store and store['id'], store and store['id'], store and store['id'], store and store['id'],
         store and store['id'], store and store['id'])).fetchall()
    return jsonify(people=[{'id': row['id'], 'name': row['name'], 'has_pin': bool(row['has_pin']),
                            'on_shift': bool(row['on_shift'])} for row in rows if row['here']])


@bp.post('/api/counter/check')
@login_required
def check_pin():
    """Check a name + PIN before the computer goes on with a change."""
    data = request.get_json(silent=True) or {}
    con = db.get_db()
    try:
        row = verify(con, data.get('employee_id'), data.get('pin'))
    except PinError as exc:
        return jsonify(error=str(exc), code=exc.code), exc.status
    return jsonify(ok=True, name=row['name'], employee_id=row['id'])
