"""Special customer requests, payments, and final handoff."""
from datetime import datetime, timezone

from auth import ROLE_CLAIM, ROLE_HIERARCHY
from checkout_access import business_date
from goods_operations import _begin, _remember, _replay
from inventory_commands import InventoryConflict, InventoryValidationError
from validation import read_int


def _text(value, field, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise InventoryValidationError(
            f'{field} must contain 1 to {maximum} characters'
        )
    return value.strip()


def _admin(actor):
    return ROLE_HIERARCHY.get(actor.get(ROLE_CLAIM), 0) >= ROLE_HIERARCHY['admin']


def _timestamp(value, field):
    if not isinstance(value, str):
        raise InventoryValidationError(f'{field} must be an ISO timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise InventoryValidationError(f'{field} must be an ISO timestamp') from exc
    if parsed.tzinfo is None:
        raise InventoryValidationError(f'{field} must include a timezone')
    return parsed.astimezone(timezone.utc).isoformat(timespec='seconds').replace(
        '+00:00', 'Z'
    )


def _employee_subject(con, value, field):
    value = _text(value, field, 200)
    if con.execute(
        'SELECT 1 FROM employees WHERE auth0_id=? AND is_active=1', (value,)
    ).fetchone() is None:
        raise InventoryValidationError(f'{field} must identify an active employee')
    return value


def can_see_phone(con, actor):
    role = actor.get(ROLE_CLAIM)
    if ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY['manager']:
        return True
    return con.execute(
        """SELECT 1 FROM employees e JOIN shifts s ON s.employee_id=e.id
           WHERE e.auth0_id=? AND e.is_active=1 AND s.date=?
           AND lower(trim(s.position))='cashier' LIMIT 1""",
        (actor.get('sub'), business_date()),
    ).fetchone() is not None


def _order_row(con, order_id):
    order_id = read_int(order_id, 'order_id', minimum=1)
    row = con.execute(
        """SELECT o.*,
                  COALESCE(NULLIF(creator.name,''),o.created_by) created_by_name,
                  CASE WHEN o.completed_by IS NULL THEN NULL
                       ELSE COALESCE(NULLIF(completer.name,''),o.completed_by) END completed_by_name
           FROM special_orders o
           LEFT JOIN employees creator ON creator.auth0_id=o.created_by
           LEFT JOIN employees completer ON completer.auth0_id=o.completed_by
           WHERE o.id=?""",
        (order_id,),
    ).fetchone()
    if row is None:
        raise InventoryValidationError('Special order does not exist')
    return dict(row)


def _order_detail(con, row, show_phone):
    row = dict(row)
    row['payments'] = [
        dict(payment) for payment in con.execute(
            """SELECT id,amount_cents,paid_at FROM special_order_payments
               WHERE special_order_id=? ORDER BY id""",
            (row['id'],),
        )
    ]
    row['paid_cents'] = sum(payment['amount_cents'] for payment in row['payments'])
    row['remaining_cents'] = row['total_cents'] - row['paid_cents']
    if not show_phone:
        row['customer_phone'] = None
    return row


def order_detail(con, order_id, actor):
    return _order_detail(con, _order_row(con, order_id), can_see_phone(con, actor))


def list_orders(con, actor, status):
    if status not in ('open', 'completed'):
        raise InventoryValidationError('status must be open or completed')
    show_phone = can_see_phone(con, actor)
    rows = con.execute(
        """SELECT o.*,
                  COALESCE(NULLIF(creator.name,''),o.created_by) created_by_name,
                  CASE WHEN o.completed_by IS NULL THEN NULL
                       ELSE COALESCE(NULLIF(completer.name,''),o.completed_by) END completed_by_name
           FROM special_orders o
           LEFT JOIN employees creator ON creator.auth0_id=o.created_by
           LEFT JOIN employees completer ON completer.auth0_id=o.completed_by
           WHERE o.status=? ORDER BY o.id DESC""",
        (status,),
    ).fetchall()
    return [_order_detail(con, row, show_phone) for row in rows]


def create_order(con, data, actor, request_key):
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'special_order_create', actor, data)
        if prior:
            con.commit()
            return order_detail(con, prior['id'], actor)
        customer_name = _text(data.get('customer_name'), 'customer_name', 120)
        customer_phone = _text(data.get('customer_phone'), 'customer_phone', 80)
        item_description = _text(
            data.get('item_description'), 'item_description', 500
        )
        total_cents = read_int(data.get('total_cents'), 'total_cents', minimum=1)
        initial_paid = read_int(
            data.get('initial_paid_cents', 0), 'initial_paid_cents', minimum=0
        )
        if initial_paid > total_cents:
            raise InventoryValidationError('initial_paid_cents cannot exceed total_cents')
        order_id = con.execute(
            """INSERT INTO special_orders
               (customer_name,customer_phone,item_description,total_cents,created_by)
               VALUES (?,?,?,?,?)""",
            (customer_name, customer_phone, item_description, total_cents, actor['sub']),
        ).lastrowid
        if initial_paid:
            con.execute(
                """INSERT INTO special_order_payments(special_order_id,amount_cents)
                   VALUES (?,?)""",
                (order_id, initial_paid),
            )
        _remember(
            con, key, 'special_order_create', order_id, actor, digest, {'id': order_id}
        )
        con.commit()
        return order_detail(con, order_id, actor)
    except Exception:
        con.rollback()
        raise


def add_payment(con, order_id, data, actor, request_key):
    _begin(con)
    try:
        row = _order_row(con, order_id)
        intent = {**data, 'order_id': row['id']}
        key, digest, prior = _replay(
            con, request_key, 'special_order_payment', actor, intent
        )
        if prior:
            con.commit()
            return order_detail(con, row['id'], actor)
        expected = read_int(data.get('expected_version'), 'expected_version', minimum=1)
        if row['version'] != expected:
            raise InventoryConflict(
                'Special order changed. Refresh before continuing.',
                'special_order_state_conflict',
            )
        if row['status'] != 'open':
            raise InventoryConflict(
                'Completed special orders cannot receive payments.',
                'special_order_completed',
            )
        amount = read_int(data.get('amount_cents'), 'amount_cents', minimum=1)
        paid = con.execute(
            'SELECT COALESCE(sum(amount_cents),0) FROM special_order_payments WHERE special_order_id=?',
            (row['id'],),
        ).fetchone()[0]
        if amount > row['total_cents'] - paid:
            raise InventoryValidationError('amount_cents exceeds the remaining balance')
        payment_id = con.execute(
            """INSERT INTO special_order_payments(special_order_id,amount_cents)
               VALUES (?,?)""",
            (row['id'], amount),
        ).lastrowid
        con.execute(
            'UPDATE special_orders SET version=version+1 WHERE id=?', (row['id'],)
        )
        _remember(
            con, key, 'special_order_payment', payment_id, actor, digest,
            {'id': row['id']},
        )
        con.commit()
        return order_detail(con, row['id'], actor)
    except Exception:
        con.rollback()
        raise


def complete_order(con, order_id, data, actor, request_key):
    _begin(con)
    try:
        row = _order_row(con, order_id)
        intent = {**data, 'order_id': row['id']}
        key, digest, prior = _replay(
            con, request_key, 'special_order_complete', actor, intent
        )
        if prior:
            con.commit()
            return order_detail(con, row['id'], actor)
        expected = read_int(data.get('expected_version'), 'expected_version', minimum=1)
        if row['version'] != expected:
            raise InventoryConflict(
                'Special order changed. Refresh before continuing.',
                'special_order_state_conflict',
            )
        if row['status'] != 'open':
            raise InventoryConflict(
                'Special order is already completed.', 'special_order_completed'
            )
        paid = con.execute(
            'SELECT COALESCE(sum(amount_cents),0) FROM special_order_payments WHERE special_order_id=?',
            (row['id'],),
        ).fetchone()[0]
        if paid != row['total_cents']:
            raise InventoryConflict(
                'Full payment is required before customer handoff.',
                'special_order_unpaid',
            )
        con.execute(
            """UPDATE special_orders SET status='completed',completed_by=?,
               completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),version=version+1
               WHERE id=?""",
            (actor['sub'], row['id']),
        )
        _remember(
            con, key, 'special_order_complete', row['id'], actor, digest,
            {'id': row['id']},
        )
        con.commit()
        return order_detail(con, row['id'], actor)
    except Exception:
        con.rollback()
        raise


def correct_order(con, order_id, data, actor, request_key):
    if not _admin(actor):
        raise PermissionError('Admin access required')
    _begin(con)
    try:
        row = _order_row(con, order_id)
        intent = {**data, 'order_id': row['id']}
        key, digest, prior = _replay(
            con, request_key, 'special_order_correct', actor, intent
        )
        if prior:
            con.commit()
            return order_detail(con, row['id'], actor)
        expected = read_int(data.get('expected_version'), 'expected_version', minimum=1)
        if row['version'] != expected:
            raise InventoryConflict(
                'Special order changed. Refresh before continuing.',
                'special_order_state_conflict',
            )
        allowed = {'expected_version', 'created_by', 'created_at',
                   'completed_by', 'completed_at'}
        if set(data) - allowed:
            raise InventoryValidationError('Only attribution and dates can be corrected')
        if not set(data) - {'expected_version'}:
            raise InventoryValidationError('Enter at least one correction')
        values = {field: row[field] for field in (
            'created_by', 'created_at', 'completed_by', 'completed_at'
        )}
        if 'created_by' in data:
            values['created_by'] = _employee_subject(
                con, data['created_by'], 'created_by'
            )
        if 'created_at' in data:
            values['created_at'] = _timestamp(data['created_at'], 'created_at')
        if 'completed_by' in data:
            values['completed_by'] = (
                None if data['completed_by'] is None else
                _employee_subject(con, data['completed_by'], 'completed_by')
            )
        if 'completed_at' in data:
            values['completed_at'] = (
                None if data['completed_at'] is None else
                _timestamp(data['completed_at'], 'completed_at')
            )
        completed_values = values['completed_by'] is not None, values['completed_at'] is not None
        if row['status'] == 'open' and any(completed_values):
            raise InventoryValidationError('Open orders cannot have completion details')
        if row['status'] == 'completed' and not all(completed_values):
            raise InventoryValidationError(
                'Completed orders require both completed_by and completed_at'
            )
        con.execute(
            """UPDATE special_orders SET created_by=?,created_at=?,completed_by=?,
               completed_at=?,version=version+1 WHERE id=?""",
            (values['created_by'], values['created_at'], values['completed_by'],
             values['completed_at'], row['id']),
        )
        _remember(
            con, key, 'special_order_correct', row['id'], actor, digest,
            {'id': row['id']},
        )
        con.commit()
        return order_detail(con, row['id'], actor)
    except Exception:
        con.rollback()
        raise
