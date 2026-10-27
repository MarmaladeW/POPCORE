"""Store scope for the legacy routes: legacy stock, daily reports, restock sheets and 晚盘.

The ledger checks inventory_access grants only. Legacy writes also accept the people who
already work at the store, so the routes stop trusting the store_code a client sends
without locking out the staff who run that store.
"""
from flask import jsonify

from auth import ROLE_CLAIM, ROLE_HIERARCHY
from checkout_access import business_date


def can_write_legacy_store(con, actor, store_id):
    """Staff or above, and admin, an inventory_access grant, a schedule store membership,
    or a shift at that store on today's Toronto business date."""
    role = actor.get(ROLE_CLAIM) if isinstance(actor, dict) else None
    if ROLE_HIERARCHY.get(role, -1) < ROLE_HIERARCHY['staff']:
        return False
    if role == 'admin':
        return True
    subject = actor.get('sub')
    if not isinstance(subject, str) or not subject or store_id is None:
        return False
    return con.execute(
        """SELECT 1 FROM inventory_access WHERE auth0_sub=? AND store_id=?
           UNION ALL
           SELECT 1 FROM employees e JOIN employee_stores es ON es.employee_id=e.id
            WHERE e.auth0_id=? AND e.is_active=1 AND es.store_id=?
           UNION ALL
           SELECT 1 FROM employees e JOIN shifts s ON s.employee_id=e.id
            WHERE e.auth0_id=? AND e.is_active=1 AND s.store_id=? AND s.date=?
           LIMIT 1""",
        (subject, store_id, subject, store_id, subject, store_id, business_date()),
    ).fetchone() is not None


def legacy_store_denied(con, actor, store_id):
    """None when actor may write store_id's legacy records, else the ledger's 403 response."""
    if can_write_legacy_store(con, actor, store_id):
        return None
    return jsonify({'error': 'Inventory access denied', 'code': 'inventory_forbidden'}), 403
