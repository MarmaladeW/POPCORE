"""Shared checkout scope, including sale and evidence views of the same order."""
from datetime import datetime
from zoneinfo import ZoneInfo

from auth import ROLE_CLAIM, ROLE_HIERARCHY
from inventory_commands import require_inventory_access


def business_date():
    return datetime.now(ZoneInfo('America/Toronto')).date().isoformat()


def checkout_access(con, actor):
    role = actor.get(ROLE_CLAIM, 'viewer')
    employee = con.execute('SELECT id FROM employees WHERE auth0_id=? AND is_active=1', (actor.get('sub'),)).fetchone()
    if not isinstance(actor.get('sub'), str) or not actor['sub'].strip() or ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY['staff'] or (role != 'admin' and not employee):
        raise PermissionError('Active checkout employee required')
    today = business_date()
    stores = [dict(r) for r in con.execute('SELECT id,code,name FROM stores WHERE is_active=1 ORDER BY id')]
    assigned = {r[0] for r in con.execute('SELECT store_id FROM shifts WHERE employee_id=? AND date=?', (employee['id'], today))} if employee else set()
    live = stores if role == 'admin' else [s for s in stores if s['id'] in assigned]
    own = {r[0] for r in con.execute('SELECT DISTINCT store_id FROM checkout_orders WHERE created_by=?', (actor['sub'],))}
    history = [s for s in stores if s['id'] in own] if role == 'staff' else live
    return dict(business_date=today, role=role, live_stores=live, history_stores=history)


def can_read(row, actor, access):
    if access['role'] == 'admin':
        return row['store_id'] in {s['id'] for s in access['live_stores']}
    if access['role'] == 'staff' and row['created_by'] == actor['sub']:
        return True
    return row['store_id'] in {s['id'] for s in access['live_stores']} and (access['role'] == 'manager' or row['status'] == 'open')


def require_checkout(con, row, actor, *, write=False):
    access = checkout_access(con, actor)
    if write:
        if row['store_id'] not in {s['id'] for s in access['live_stores']}:
            raise PermissionError('Checkout shift access denied')
        require_inventory_access(con, actor, (row['store_id'],), 'staff')
    elif not can_read(row, actor, access):
        raise PermissionError('Checkout history access denied')
    return access


def require_linked_sale(con, sale_id, actor, *, write=False):
    row = con.execute('SELECT * FROM checkout_orders WHERE sale_id=?', (sale_id,)).fetchone()
    if row:
        require_checkout(con, row, actor)
        if write:
            require_checkout(con, row, actor, write=True)
    return row is not None


def require_checkout_history_store(con, store_id, actor, start_date='0001-01-01', end_date='9999-12-31'):
    """Reject mixed summaries containing checkout history outside the actor's scope."""
    rows = con.execute("""SELECT o.* FROM checkout_orders o WHERE o.store_id=?
        AND (o.business_date BETWEEN ? AND ? OR EXISTS(SELECT 1 FROM checkout_refunds r
        JOIN checkout_attempts a ON a.id=r.attempt_id WHERE a.checkout_id=o.id
        AND r.business_date BETWEEN ? AND ?))""", (store_id,start_date,end_date,start_date,end_date)).fetchall()
    if rows:
        access = checkout_access(con, actor)
        if access['role'] == 'staff':
            if any(row['created_by'] != actor['sub'] for row in rows):
                raise PermissionError('Checkout history store access denied')
        elif store_id not in {s['id'] for s in access['history_stores']}:
            raise PermissionError('Checkout history store access denied')
