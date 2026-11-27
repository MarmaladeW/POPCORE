"""Who is at the shared store computer (owner, 2026-11-20: every action leaves a mark of who did it).

The store computer signs in as one 查看者 Viewer counter account with 门店操作权限. Before it changes anything, the
person at the computer taps their name: the request carries `X-Popcore-Person: <employee id>`, and the change is
marked with that person, by way of the counter account, in 动态 and wherever the app names who did it (the website
order note too). Permissions stay the counter account's own; the name only says who. Looking around needs no name.

Owner, 2026-11-26: "PIN not necessary". There is no PIN any more: a tap on the name is enough. The header is read
only for the store computer; from any other account it is ignored.
"""
from flask import Blueprint, g, jsonify, request

import db
from auth import ROLE_CLAIM, ROLE_HIERARCHY, login_required

bp = Blueprint('store_computer', __name__)
HEADER = 'X-Popcore-Person'
MUTATING = {'POST', 'PUT', 'PATCH', 'DELETE'}
# What the store computer may do without a name: setting up its own notifications, looking up a pickup code.
FREE = {'website.web_orders_code', 'website.web_orders_notify_me', 'website.web_orders_notify_off',
        'website.web_orders_notify_test'}


class PersonError(Exception):
    def __init__(self, message, code='person_needed', status=428):
        super().__init__(message)
        self.code, self.status = code, status


def migrate(cur):
    """The PIN columns stay (migration `employee_pins`), empty since `employee_pins_cleared`."""
    columns = {row[1] for row in cur.execute('PRAGMA table_info(employees)')}
    for column, kind in (('pin_hash', 'TEXT'), ('pin_salt', 'TEXT'), ('pin_set_at', 'TEXT'),
                         ('pin_failures', 'INTEGER NOT NULL DEFAULT 0'), ('pin_locked_until', 'TEXT')):
        if column not in columns:
            cur.execute(f'ALTER TABLE employees ADD COLUMN {column} {kind}')


def clear_pins(cur):
    """Owner, 2026-11-26: no PIN. What was kept of them (hash, salt, wrong tries) is wiped: four digits are found
    from a hash at once, and they open nothing now."""
    cur.execute("""UPDATE employees SET pin_hash=NULL, pin_salt=NULL, pin_set_at=NULL, pin_failures=0,
                       pin_locked_until=NULL""")


def is_counter(con, actor):
    """A 查看者 Viewer with 门店操作权限: the store computer's shared account."""
    if not actor or actor.get(ROLE_CLAIM, 'viewer') != 'viewer':
        return False
    return con.execute('SELECT 1 FROM inventory_access WHERE auth0_sub=? LIMIT 1', (actor.get('sub'),)).fetchone() is not None


def acting_person(con, actor):
    """Read the name header: the person doing this request, or None. The counter account must give one for every
    change. From any other account the header is ignored: nobody acting."""
    if not is_counter(con, actor):
        return None
    # A page loaded before the PIN went away still sends "<id>:<pin>": only the id is read.
    raw = request.headers.get(HEADER, '').strip().partition(':')[0]
    if not raw:
        if request.method in MUTATING and (request.endpoint or '') not in FREE:
            raise PersonError('先点你的名字 Tap your name first.')
        return None
    row = con.execute("SELECT id, auth0_id, name FROM employees WHERE id=? AND is_active=1 AND COALESCE(name, '') != ''",
                      (int(raw) if raw.isdigit() else None,)).fetchone()
    # The shared account is not a person, and neither is someone removed since the name was tapped: ask again.
    if row is None or row['auth0_id'] == actor.get('sub'):
        raise PersonError('先点你的名字 Tap your name first.')
    return {'sub': row['auth0_id'], 'name': row['name'], 'employee_id': row['id']}


def init_app(app):
    app.register_blueprint(bp)


def guard():
    """Called once the request's account is known (auth.py): sets g.acting_person or refuses the change."""
    actor = getattr(request, 'jwt_payload', None)
    raw = request.headers.get(HEADER, '').strip()
    # Only a viewer can be the store computer; anyone else's header is never read.
    if not actor or actor.get(ROLE_CLAIM, 'viewer') != 'viewer' or (not raw and request.method not in MUTATING):
        return None
    con = db.get_db()
    try:
        g.acting_person = acting_person(con, actor)
    except PersonError as exc:
        return jsonify(error=str(exc), code=exc.code), exc.status
    return None


@bp.get('/api/counter/people')
@login_required
def counter_people():
    """Who can be picked on the store computer: active staff of this store, today's shift first. Only the store
    computer and managers read it (owner, 2026-11-26: it says who is on shift today), and the account asking is
    never in it: the shared account is not a person."""
    con = db.get_db()
    actor = request.jwt_payload
    if not is_counter(con, actor) and ROLE_HIERARCHY.get(actor.get(ROLE_CLAIM), 0) < ROLE_HIERARCHY['manager']:
        return jsonify(error='只有店铺电脑和经理可以查看 Only the store computer and managers can see this list',
                       code='forbidden'), 403
    code = (request.args.get('store_code') or '').strip().upper()
    store = con.execute('SELECT id FROM stores WHERE code=?', (code,)).fetchone() if code else None
    from checkout_access import business_date
    today = business_date()
    rows = con.execute(
        """SELECT e.id, e.name,
                  EXISTS(SELECT 1 FROM shifts s WHERE s.employee_id=e.id AND s.date=? AND (? IS NULL OR s.store_id=?)) AS on_shift,
                  (? IS NULL OR EXISTS(SELECT 1 FROM employee_stores es WHERE es.employee_id=e.id AND es.store_id=?)
                   OR EXISTS(SELECT 1 FROM shifts s WHERE s.employee_id=e.id AND s.store_id=?)
                   OR EXISTS(SELECT 1 FROM inventory_access a WHERE a.auth0_sub=e.auth0_id AND a.store_id=?)) AS here
           FROM employees e WHERE e.is_active=1 AND COALESCE(e.name, '') != '' AND e.auth0_id != ?
           ORDER BY on_shift DESC, e.name COLLATE NOCASE""",
        (today, store and store['id'], store and store['id'], store and store['id'], store and store['id'],
         store and store['id'], store and store['id'], actor.get('sub') or '')).fetchall()
    return jsonify(people=[{'id': row['id'], 'name': row['name'], 'on_shift': bool(row['on_shift'])}
                           for row in rows if row['here']])
