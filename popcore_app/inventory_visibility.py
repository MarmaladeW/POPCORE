"""Who sees stock (owner, 2026-11-01): managers and admins at every store; anyone else only at
the stores an admin ticked for them in Users → 库存查看 Inventory view (inventory_viewers).

The 库存 tab and the tools reached from it (盘点, 入库, 调拨, 娃娃机, the legacy stock pages and
the inventory reports) refuse everyone else with `inventory_hidden`. 晚盘 stays open to the people
who already run it. Products and 补货 keep working but never send a hidden store's quantities:
each number is replaced by `out_of_stock`, true when the quantity it hides is zero or less.

This is separate from 门店操作权限 Store operations access (inventory_access), which decides who may
change a live store's stock; seeing it needs both.
"""
from flask import jsonify

from auth import ROLE_CLAIM, ROLE_HIERARCHY

HIDDEN_MESSAGE = ('库存查看 Inventory view is off for this account{where}. '
                  'Ask an admin to turn it on in Users.')


class InventoryHidden(PermissionError):
    code = 'inventory_hidden'


def sees_every_store(actor):
    role = actor.get(ROLE_CLAIM) if isinstance(actor, dict) else None
    return ROLE_HIERARCHY.get(role, -1) >= ROLE_HIERARCHY['manager']


def visible_store_ids(con, actor):
    """The active stores whose stock actor may see."""
    if sees_every_store(actor):
        return {row[0] for row in con.execute('SELECT id FROM stores WHERE is_active=1')}
    subject = actor.get('sub') if isinstance(actor, dict) else None
    if not isinstance(subject, str) or not subject:
        return set()
    return {row[0] for row in con.execute(
        """SELECT v.store_id FROM inventory_viewers v JOIN stores s ON s.id=v.store_id
           WHERE v.auth0_sub=? AND s.is_active=1""", (subject,))}


def can_see_inventory(con, actor, store_ids):
    """Whether actor may see stock at every store in store_ids; None means every active store (ALL)."""
    if sees_every_store(actor):
        return True
    visible = visible_store_ids(con, actor)
    if store_ids is None:
        wanted = {row[0] for row in con.execute('SELECT id FROM stores WHERE is_active=1')}
    else:
        wanted = set(store_ids)
    return bool(wanted) and None not in wanted and wanted <= visible


def require_inventory_visible(con, actor, store_ids, store_code=None):
    if not can_see_inventory(con, actor, store_ids):
        raise InventoryHidden(HIDDEN_MESSAGE.format(where=f' at {store_code}' if store_code else ''))


def hidden_response(exc):
    return jsonify({'error': str(exc), 'code': InventoryHidden.code}), 403


def inventory_hidden(con, actor, store_ids, store_code=None):
    """None when actor may see stock at store_ids, else the 403 response."""
    try:
        require_inventory_visible(con, actor, store_ids, store_code)
    except InventoryHidden as exc:
        return hidden_response(exc)
    return None


def hide_quantities(row, *fields):
    """Replace row's quantity fields with out_of_stock (their sum is zero or less)."""
    row['out_of_stock'] = sum(row.get(field) or 0 for field in fields) <= 0
    for field in fields:
        row[field] = None
    row['stock_hidden'] = True
    return row


def hide_balances(result):
    """Drop the quantities from a ledger result's balances; versions stay for the next write."""
    for balance in result.get('balances') or ():
        balance['quantity'] = None
    return result


def hide_box_options(options):
    """A blind box's checkout answers (sealed set, opened sets, loose boxes) without their counts:
    only whether there is a sealed set and loose boxes to take (owner, 2026-11-01)."""
    if options is None:
        return None
    if options['fresh_set']:
        hide_quantities(options['fresh_set'], 'sets_on_hand')
    for open_set in options['open_sets']:
        open_set['remaining_qty'] = None
    options['loose_out_of_stock'] = (options['loose_units'] or 0) <= 0
    options['loose_units'] = None
    options['stock_hidden'] = True
    return options
