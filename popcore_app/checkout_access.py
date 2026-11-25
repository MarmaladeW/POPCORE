"""Shared checkout scope, including sale and evidence views of the same order."""
from datetime import datetime
from zoneinfo import ZoneInfo

from auth import ROLE_CLAIM, ROLE_HIERARCHY
from inventory_commands import InventoryError, require_inventory_access

# Owner of Clover orders nobody has picked up yet, and of sales paid entirely on Clover.
# It never comes from a login: Auth0 subjects always contain '|'.
REGISTER_SUB = 'popcore:clover-register'
REGISTER_NAME = 'Clover register'


def register_actor():
    return {'sub': REGISTER_SUB, ROLE_CLAIM: 'manager'}


def business_date():
    return datetime.now(ZoneInfo('America/Toronto')).date().isoformat()


# Until Clover is connected at a store, its sales leave stock through the pasted 日报, so a staff checkout or
# 补录 there would take the same sale off twice (owner, 2026-11-20). app_settings staff_checkout: 'clover' (staff
# take checkouts only where Clover is connected; set on a live database) or 'all' (every store, the default).
CLOSED_MESSAGE = ('买单要等这家店接上 Clover 才开，今天的销售照常写日报。'
                  'Checkout opens here once Clover is connected; keep writing sales in the daily report.')


def staff_checkout_mode(con):
    row = con.execute("SELECT value FROM app_settings WHERE key='staff_checkout'").fetchone()
    return 'clover' if row and row[0] == 'clover' else 'all'


def checkout_open(con, role, store_id):
    """Whether this role may take checkouts and record sales (补录) at this store. Managers and admins always may."""
    if role != 'staff' or staff_checkout_mode(con) == 'all':
        return True
    import clover_checkout  # it imports this module
    return clover_checkout.store_config(store_id) is not None


def require_checkout_open(con, actor, store_id):
    if not checkout_open(con, actor.get(ROLE_CLAIM, 'viewer'), store_id):
        raise InventoryError(CLOSED_MESSAGE, 'checkout_needs_clover', 403)


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
    rate = con.execute("SELECT value FROM app_settings WHERE key='checkout_cny_per_cad'").fetchone()
    # live_stores also lets staff record claw and display events, so checkout keeps its own list.
    checkout = [s for s in live if checkout_open(con, role, s['id'])]
    return dict(business_date=today, role=role, live_stores=live, checkout_stores=checkout, history_stores=history,
                cny_per_cad=rate['value'] if rate else '')


def can_read(row, actor, access):
    if access['role'] == 'admin':
        return row['store_id'] in {s['id'] for s in access['live_stores']}
    if access['role'] == 'staff' and row['created_by'] == actor['sub']:
        return True
    # Today's Clover register sales belong to whoever is on shift at that store.
    register_today = row['created_by'] == REGISTER_SUB and row['business_date'] == access['business_date']
    return row['store_id'] in {s['id'] for s in access['live_stores']} and (
        access['role'] == 'manager' or row['status'] == 'open' or register_today)


def require_checkout(con, row, actor, *, write=False):
    access = checkout_access(con, actor)
    if write:
        if row['store_id'] not in {s['id'] for s in access['live_stores']}:
            raise PermissionError('Checkout shift access denied')
        require_checkout_open(con, actor, row['store_id'])
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
            # Register-owned Clover sales belong to whoever is on shift at that store.
            on_shift = store_id in {s['id'] for s in access['live_stores']}
            if any(row['created_by'] != actor['sub'] and not (on_shift and row['created_by'] == REGISTER_SUB)
                   for row in rows):
                raise PermissionError('Checkout history store access denied')
        elif store_id not in {s['id'] for s in access['history_stores']}:
            raise PermissionError('Checkout history store access denied')
