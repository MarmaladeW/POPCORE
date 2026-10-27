from checkout_access import require_checkout, require_checkout_history_store, require_linked_sale
"""Versioned store-day closing and exact cash arithmetic."""
import hashlib
import json
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from checkout_operations import checkout_funds
from auth import ROLE_CLAIM, ROLE_HIERARCHY
from goods_operations import _begin, _remember, _replay
from inventory_commands import InventoryConflict, InventoryValidationError, require_inventory_access
from validation import read_date, read_int


OPENING_BILLS_CENTS = 65_000
DENOMINATIONS = {
    '10000': 10000, '5000': 5000, '2000': 2000, '1000': 1000,
    '500': 500, '200': 200, '100': 100, '25': 25, '10': 10, '5': 5,
}


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _closing(con, closing_id):
    closing_id = read_int(closing_id, 'closing_id', minimum=1)
    row = con.execute('SELECT * FROM closing_sessions WHERE id=?', (closing_id,)).fetchone()
    if row is None:
        raise InventoryValidationError('closing session does not exist')
    return dict(row)


def _is_manager(actor):
    return ROLE_HIERARCHY.get(actor.get(ROLE_CLAIM, 'viewer'), 0) >= ROLE_HIERARCHY['manager']


def _require_closing_access(con, store_id, business_date, actor, minimum='staff'):
    """Staff close a store day they had a shift on, whoever took its orders (owner, 2026-10-27); they
    get the staff view of `closing_detail`. Managers and admins keep the checkout history scope."""
    require_inventory_access(con, actor, (store_id,), minimum)
    if _is_manager(actor):
        require_checkout_history_store(con, store_id, actor, business_date, business_date)
    elif not con.execute(
        """SELECT 1 FROM shifts s JOIN employees e ON e.id=s.employee_id
           WHERE e.auth0_id=? AND e.is_active=1 AND s.store_id=? AND s.date=?""",
        (actor.get('sub'), store_id, business_date),
    ).fetchone():
        raise PermissionError('Closing needs a shift at this store on that day')


def _payment_rows(con, store_id, business_date):
    """The money received for the day's sales, as corrected. A rejected payment was not received and
    stays out; refunds are money out on the day they are recorded (`_refund_rows`)."""
    rows = []
    for row in con.execute(
        """SELECT p.* FROM sale_payments p
           JOIN sale_documents s ON s.id=p.sale_id
           WHERE s.store_id=? AND s.business_date=? AND s.status='posted'
                 AND p.state!='rejected'
           ORDER BY p.id""", (store_id, business_date),
    ):
        item = dict(row)
        effective = item['amount_cents']
        if effective is not None:
            for event in con.execute(
                """SELECT direction, amount_cents FROM payment_events
                   WHERE payment_id=? AND event_type!='refund' ORDER BY id""", (item['id'],),
            ):
                if event['direction'] == 'increase':
                    effective += event['amount_cents']
                elif event['direction'] == 'decrease':
                    effective -= event['amount_cents']
        item['effective_amount_cents'] = effective
        rows.append(item)
    return rows


# A refund recorded on a sale counts on its own business date; one recorded before refunds had a date
# counts on its sale's day, as it always did.
REFUND_DATE = 'COALESCE(e.business_date, s.business_date)'


def _refund_rows(con, store_id, business_date):
    """Refunds recorded on this store's sales on this business date, whatever day the sale was."""
    return [dict(row) for row in con.execute(
        f"""SELECT e.id, e.payment_id, p.sale_id, p.tender, e.amount_cents
            FROM payment_events e JOIN sale_payments p ON p.id=e.payment_id
            JOIN sale_documents s ON s.id=p.sale_id
            WHERE e.event_type='refund' AND s.store_id=? AND s.status='posted'
                  AND {REFUND_DATE}=? ORDER BY e.id""", (store_id, business_date),
    )]


def cash_summary(con, store_id, business_date, opening_coin_cents):
    payments = _payment_rows(con, store_id, business_date)
    verified_cash = [
        item for item in payments if item['tender'] == 'cash'
        and item['state'] == 'verified' and item['effective_amount_cents'] is not None
    ]
    unknown_cash = [
        item['id'] for item in payments if item['tender'] == 'cash'
        and (item['state'] != 'verified' or item['effective_amount_cents'] is None)
    ]
    cash_receipts = sum(item['effective_amount_cents'] for item in verified_cash)
    event_totals = {name: 0 for name in ('paid_in', 'refund', 'payout', 'removal')}
    for event in con.execute(
        """SELECT event_type, amount_cents FROM cash_events
           WHERE store_id=? AND business_date=? ORDER BY id""",
        (store_id, business_date),
    ):
        event_totals[event['event_type']] += event['amount_cents']
    funds = checkout_funds(con,store_id,business_date)
    cash_receipts += sum(r['amount_cents'] for r in funds['receipts'] if r['tender']=='cash' and r['verified'])
    event_totals['refund'] += sum(r['amount_cents'] for r in funds['refunds'] if r['tender']=='cash')
    # Cash refunded on a sale leaves the drawer today, once, whichever day the sale was.
    event_totals['refund'] += sum(r['amount_cents'] for r in _refund_rows(con, store_id, business_date)
                                  if r['tender'] == 'cash')
    opening = OPENING_BILLS_CENTS + opening_coin_cents
    expected = (
        opening + cash_receipts + event_totals['paid_in']
        - event_totals['refund'] - event_totals['payout'] - event_totals['removal']
    )
    return {
        'opening_cash_cents': opening,
        'verified_cash_receipts_cents': cash_receipts,
        'unknown_cash_payment_ids': unknown_cash,
        'unknown_checkout_cash_attempt_ids': [r['id'] for r in funds['receipts'] if r['tender']=='cash' and not r['verified']],
        'event_totals_cents': event_totals,
        'expected_drawer_cents': expected,
    }


def _source_facts(con, session):
    store_id = session['store_id']
    business_date = session['business_date']
    return {
        'session': [session['id'], session['version'], session['status'],
                    session['intake_complete']],
        'sales': [tuple(row) for row in con.execute(
            """SELECT id, version, status, allocation_status, financial_status,
                      inventory_document_id
               FROM sale_documents WHERE store_id=? AND business_date=? ORDER BY id""",
            (store_id, business_date),
        )],
        'checkouts': [tuple(row) for row in con.execute(
            """SELECT o.id,o.version,o.status,o.sale_id FROM checkout_orders o WHERE o.store_id=?
            AND (o.business_date=? OR EXISTS(SELECT 1 FROM checkout_refunds r
            JOIN checkout_attempts a ON a.id=r.attempt_id WHERE a.checkout_id=o.id AND r.business_date=?)) ORDER BY o.id""",
            (store_id,business_date,business_date),
        )],
        'checkout_funds': checkout_funds(con,store_id,business_date),
        'clover': [tuple(row) for row in con.execute(
            """SELECT l.checkout_id, l.state, l.writeback_state, l.review_reason
               FROM clover_orders l JOIN checkout_orders o ON o.id=l.checkout_id
               WHERE o.store_id=? AND o.business_date=? ORDER BY l.checkout_id""",
            (store_id, business_date),
        )],
        'sale_sources': [tuple(row) for row in con.execute(
            """SELECT source.id, source.sale_id, source.source_system,
                      source.source_account, source.source_reference
               FROM sale_sources source
               JOIN sale_documents sale ON sale.id=source.sale_id
               WHERE sale.store_id=? AND sale.business_date=? ORDER BY source.id""",
            (store_id, business_date),
        )],
        'sale_reconciliations': [tuple(row) for row in con.execute(
            """SELECT id, intent, source_system, source_account, source_reference,
                      sale_id, inventory_document_id, notes, reviewed_by, created_at
               FROM sale_reconciliations
               WHERE store_id=? AND business_date=? ORDER BY id""",
            (store_id, business_date),
        )],
        'payments': [tuple(row) for row in con.execute(
            """SELECT p.id, p.sale_id, p.tender, p.state, p.amount_cents,
                      p.source_system, p.source_account, p.source_reference
               FROM sale_payments p JOIN sale_documents s ON s.id=p.sale_id
               WHERE s.store_id=? AND s.business_date=? ORDER BY p.id""",
            (store_id, business_date),
        )],
        # A refund belongs to the day it was recorded (the refund day's cash), not to its sale's day.
        'payment_events': [tuple(row) for row in con.execute(
            f"""SELECT e.id, e.payment_id, e.event_type, e.direction, e.amount_cents,
                      e.reason, e.actor_sub, e.created_at
               FROM payment_events e JOIN sale_payments p ON p.id=e.payment_id
               JOIN sale_documents s ON s.id=p.sale_id
               WHERE s.store_id=? AND CASE WHEN e.event_type='refund' THEN {REFUND_DATE}
                                           ELSE s.business_date END=? ORDER BY e.id""",
            (store_id, business_date),
        )],
        'evidence': [tuple(row) for row in con.execute(
            """SELECT e.id, e.payment_id, e.status FROM payment_evidence e
               JOIN sale_payments p ON p.id=e.payment_id
               JOIN sale_documents s ON s.id=p.sale_id
               WHERE s.store_id=? AND s.business_date=? ORDER BY e.id""",
            (store_id, business_date),
        )],
        'cash_events': [tuple(row) for row in con.execute(
            """SELECT id, closing_session_id, event_type, amount_cents, reason,
                      actor_sub, created_at FROM cash_events
               WHERE store_id=? AND business_date=? ORDER BY id""",
            (store_id, business_date),
        )],
        'cash_counts': [tuple(row) for row in con.execute(
            """SELECT id, revision, denomination_counts_json, opening_coin_cents,
                      retained_coin_cents, counted_cents, expected_cents,
                      variance_cents, retained_cents, removal_cents, source_token,
                      counted_by, created_at
               FROM closing_cash_counts WHERE closing_session_id=? ORDER BY id""",
            (session['id'],),
        )],
        'receipts': [tuple(row) for row in con.execute(
            """SELECT id, version, status FROM goods_receipts
               WHERE store_id=? AND business_date=? ORDER BY id""",
            (store_id, business_date),
        )],
        'counts': [tuple(row) for row in con.execute(
            """SELECT id, version, status FROM inventory_counts
               WHERE store_id=? AND business_date=? ORDER BY id""",
            (store_id, business_date),
        )],
        'deliveries': [tuple(row) for row in con.execute(
            """SELECT d.id, d.version, d.status
               FROM inventory_deliveries d
               JOIN inventory_locations source ON source.id=d.source_location_id
               JOIN inventory_locations destination ON destination.id=d.destination_location_id
               WHERE (source.store_id=? OR destination.store_id=?)
                     AND d.business_date=? ORDER BY d.id""",
            (store_id, store_id, business_date),
        )],
        'restocks': [tuple(row) for row in con.execute(
            """SELECT id, status FROM restock_sessions
               WHERE store_id=? AND date=? ORDER BY id""",
            (store_id, business_date),
        )],
        'trades': [tuple(row) for row in con.execute(
            """SELECT e.id, e.event_type, e.slot_id, e.slot_version_after,
                      e.incoming_unit_id, e.outgoing_unit_id,
                      e.consume_document_id, e.receipt_document_id, e.sale_id
               FROM trade_events e JOIN trade_slots s ON s.id=e.slot_id
               WHERE s.store_id=? AND e.business_date=? ORDER BY e.id""",
            (store_id, business_date),
        )],
        'balances': [tuple(row) for row in con.execute(
            """SELECT b.product_id, b.location_id, b.disposition, b.version
               FROM inventory_balances b JOIN inventory_locations l ON l.id=b.location_id
               WHERE l.store_id=? ORDER BY b.product_id, b.location_id, b.disposition""",
            (store_id,),
        )],
        # Posted documents are immutable, so their ids cover every stock change of this store day.
        'inventory_documents': [tuple(row) for row in con.execute(
            """SELECT DISTINCT d.id, d.kind, d.status
               FROM inventory_documents d
               JOIN inventory_movements m ON m.document_id=d.id
               JOIN inventory_locations l ON l.id=m.location_id
               WHERE l.store_id=? AND d.business_date=? ORDER BY d.id""",
            (store_id, business_date),
        )],
    }


def source_token(con, session):
    # Balances stay in the snapshot for the record; stock moved on a later day must not
    # make this day stale, so the token covers the day's inventory documents instead.
    sources = {key: value for key, value in _source_facts(con, session).items()
               if key != 'balances'}
    raw = json.dumps(sources, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _cash_source_token(con, session):
    facts = _source_facts(con, session)
    cash_sources = {
        key: facts[key] for key in ('payments', 'payment_events', 'cash_events', 'checkout_funds')
    }
    raw = json.dumps(cash_sources, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _tender_summary(payments, funds, refunds):
    """Each tender's money for the day: receipts of the day's sales, less refunds recorded that day."""
    totals = {tender: 0 for tender in ('cash', 'card', 'e_transfer', 'wechat', 'alipay')}
    unknown = []
    for payment in payments:
        if payment['effective_amount_cents'] is None:
            unknown.append(payment['id'])
        else:
            totals[payment['tender']] += payment['effective_amount_cents']
    for receipt in funds['receipts']:
        totals[receipt['tender']] += receipt['amount_cents']
    for refund in [*funds['refunds'], *refunds]:
        totals[refund['tender']] -= refund['amount_cents']
    return totals,unknown


def _issue_targets(con, session, codes, actor):
    targets = {}
    for code in codes:
        kind, _, value = code.partition(':')
        if kind in {'sales_intake_incomplete', 'cash_count_missing', 'retained_float_shortfall', 'clover_sync_stale'}:
            targets[code] = {'href': '#closing-cash-count' if kind in {'cash_count_missing', 'retained_float_shortfall'}
                             else '#closing-sources'}
            continue
        if kind == 'clover_import_failed':  # a Clover order ID; listed on the Checkout page for a manager
            targets[code] = {'href': '/checkout', 'label': f'Clover order {value}'}
            continue
        item_id = int(value)
        row = None
        if kind in {'clover_writeback_pending', 'clover_review'}:
            row = con.execute('SELECT o.* FROM checkout_orders o WHERE o.id=?', (item_id,)).fetchone()
            if row:
                try:
                    require_checkout(con, row, actor)
                except PermissionError:
                    continue
                targets[code] = {'href': f"/checkout/{row['id']}", 'label': f"Clover order {row['reference']}"}
        elif kind in {'checkout_unresolved', 'checkout_cash_unresolved'}:
            query = 'SELECT o.* FROM checkout_orders o WHERE o.id=?'
            if kind == 'checkout_cash_unresolved':
                query = 'SELECT o.* FROM checkout_attempts a JOIN checkout_orders o ON o.id=a.checkout_id WHERE a.id=?'
            row = con.execute(query, (item_id,)).fetchone()
            if row:
                try:
                    require_checkout(con, row, actor)
                except PermissionError:
                    continue
                targets[code] = {'href': f"/checkout/{row['id']}", 'label': row['reference'] or f"Checkout #{row['id']}"}
        elif kind in {'pending_allocation', 'payment_mismatch', 'cash_payment_unresolved', 'payment_unverified', 'evidence_pending', 'evidence_rejected'}:
            sale_id = item_id
            if kind not in {'pending_allocation', 'payment_mismatch'}:
                query = 'SELECT sale_id FROM sale_payments WHERE id=?'
                if kind.startswith('evidence_'):
                    query = 'SELECT p.sale_id FROM payment_evidence e JOIN sale_payments p ON p.id=e.payment_id WHERE e.id=?'
                row = con.execute(query, (item_id,)).fetchone()
                if row is None:
                    continue
                sale_id = row['sale_id']
            row = con.execute('''SELECT s.id, (SELECT source_reference FROM sale_sources
                WHERE sale_id=s.id ORDER BY id LIMIT 1) AS reference FROM sale_documents s
                WHERE s.id=? AND s.store_id=? AND s.business_date=?''',
                (sale_id, session['store_id'], session['business_date'])).fetchone()
            if row:
                try:
                    require_linked_sale(con, sale_id, actor)
                except PermissionError:
                    continue
                label = row['reference'] or f'Sale #{sale_id}'
                if kind not in {'pending_allocation', 'payment_mismatch'}:
                    label = f"{'Evidence' if kind.startswith('evidence_') else 'Payment'} #{item_id} · {label}"
                targets[code] = {'href': f'/sales/documents/{sale_id}', 'label': label}
        elif kind == 'delivery_unresolved':
            row = con.execute('''SELECT d.id, d.kind, d.restock_session_id, ss.code AS source_store, ds.code AS destination_store,
                sl.name AS source_name, dl.name AS destination_name FROM inventory_deliveries d
                JOIN inventory_locations sl ON sl.id=d.source_location_id JOIN stores ss ON ss.id=sl.store_id
                JOIN inventory_locations dl ON dl.id=d.destination_location_id JOIN stores ds ON ds.id=dl.store_id
                WHERE d.id=? AND (sl.store_id=? OR dl.store_id=?)''',
                (item_id, session['store_id'], session['store_id'])).fetchone()
            if row:
                targets[code] = {'href': f"/restock?session_id={row['restock_session_id']}" if row['kind'] == 'restock' and row['restock_session_id'] else f'/goods/transfers?transfer_id={item_id}',
                    'label': f"Delivery #{item_id} · {row['source_store']} {row['source_name']} → {row['destination_store']} {row['destination_name']}"}
        elif kind == 'restock_unresolved':
            row = con.execute('SELECT id FROM restock_sessions WHERE id=? AND store_id=?', (item_id, session['store_id'])).fetchone()
            if row:
                targets[code] = {'href': f'/restock?session_id={item_id}', 'label': f'Restock #{item_id}'}
        elif kind == 'hot_item_count_missing':
            row = con.execute('SELECT sku, name_cn_en, jizhanming FROM products WHERE id=?', (item_id,)).fetchone()
            if row:
                count = con.execute('''SELECT c.id FROM inventory_counts c JOIN inventory_count_lines l ON l.count_id=c.id
                    WHERE c.store_id=? AND c.business_date=? AND l.product_id=? AND c.status IN ('draft','submitted')
                    ORDER BY c.id DESC LIMIT 1''', (session['store_id'], session['business_date'], item_id)).fetchone()
                href = f"/goods/counts?count_id={count['id']}" if count else f"/goods/counts?product_id={item_id}&business_date={session['business_date']}"
                targets[code] = {'href': href, 'label': row['name_cn_en'] or row['jizhanming'] or row['sku'] or f'Product #{item_id}'}
    return targets


def _openable_sale(con, sale, actor):
    """Whether this staff member may open the sale page: their own sale, or a checkout sale they can read."""
    try:
        return require_linked_sale(con, sale['id'], actor) or sale['created_by'] == actor.get('sub')
    except PermissionError:
        return False


def closing_detail(con, closing_id, *, actor):
    session = _closing(con, closing_id)
    _require_closing_access(con, session['store_id'], session['business_date'], actor)
    is_manager = _is_manager(actor)
    token = source_token(con, session)
    count = con.execute(
        """SELECT * FROM closing_cash_counts WHERE closing_session_id=?
           ORDER BY revision DESC LIMIT 1""", (closing_id,),
    ).fetchone()
    opening_coin = count['opening_coin_cents'] if count else 0
    sales = [dict(row) for row in con.execute(
        """SELECT id, status, allocation_status, financial_status, created_by FROM sale_documents
           WHERE store_id=? AND business_date=? ORDER BY id""",
        (session['store_id'], session['business_date']))]
    if not is_manager:  # staff get no link to a sale they cannot open
        sales = [sale for sale in sales if _openable_sale(con, sale, actor)]
    for sale in sales:
        sale.pop('created_by')
    result = {
        **session, 'closing_id': session['id'], 'source_token': token,
        'store_name': con.execute('SELECT name FROM stores WHERE id=?', (session['store_id'],)).fetchone()['name'],
        'cash': cash_summary(con, session['store_id'], session['business_date'], opening_coin),
        'latest_cash_count': dict(count) if count else None,
        'source_documents': {
            'sales': sales,
            'receipts': [dict(row) for row in con.execute(
                """SELECT id, status FROM goods_receipts WHERE store_id=? AND business_date=?
                   ORDER BY id""", (session['store_id'], session['business_date']))
            ],
            'counts': [dict(row) for row in con.execute(
                """SELECT id, status FROM inventory_counts WHERE store_id=? AND business_date=?
                   ORDER BY id""", (session['store_id'], session['business_date']))
            ],
        },
    }
    result.pop('id')
    if result['latest_cash_count']:
        result['latest_cash_count']['denomination_counts'] = json.loads(
            result['latest_cash_count'].pop('denomination_counts_json')
        )
    hard, exceptions = closing_issues(con, session)
    actionable = [code for code in hard if code.split(':')[0] in {
        'sales_intake_incomplete', 'cash_count_missing', 'retained_float_shortfall',
        'checkout_unresolved', 'delivery_unresolved', 'restock_unresolved', 'hot_item_count_missing',
        'clover_writeback_pending', 'clover_sync_stale', 'clover_import_failed',
    }]
    targets = _issue_targets(con, session, hard + exceptions if is_manager else actionable, actor)
    result['issue_targets'] = targets
    result['actionable_blockers'] = [code for code in actionable if code in targets]
    funds = checkout_funds(con,session['store_id'],session['business_date'])
    totals,unknown = _tender_summary(_payment_rows(con,session['store_id'],session['business_date']),funds,
                                     _refund_rows(con,session['store_id'],session['business_date']))
    result['tender_totals_cents'] = totals  # the day's totals per tender; staff see these too
    if not is_manager:
        # Staff see what blocks the day and what a manager will review as labels: only what they can act
        # on and open keeps its id and link. No other cashier's order, amount or reference.
        result['hard_blockers'] = [code if code in targets else code.split(':')[0] for code in hard]
        result['review_exceptions'] = [code.split(':')[0] for code in exceptions]
        for key in ('unknown_cash_payment_ids', 'unknown_checkout_cash_attempt_ids'):
            result['cash'].pop(key)
    else:
        result['checkout_funds'] = funds
        result['unknown_payment_ids'] = unknown
        result['hard_blockers'] = hard
        result['review_exceptions'] = exceptions
        snapshot = con.execute(
            'SELECT id, snapshot_json FROM closing_snapshots WHERE closing_session_id=?',
            (closing_id,),
        ).fetchone()
        if snapshot:
            result['snapshot'] = {'snapshot_id': snapshot['id'],
                                  **json.loads(snapshot['snapshot_json'])}
        result['late_adjustments'] = [dict(row) for row in con.execute(
            'SELECT * FROM closing_adjustments WHERE closing_session_id=? ORDER BY id',
            (closing_id,),
        )]
    return result


def closing_issues(con, session):
    hard = []
    exceptions = []
    if not session['intake_complete']:
        hard.append('sales_intake_incomplete')
    hard.extend(f'checkout_unresolved:{row[0]}' for row in con.execute(
        """SELECT o.id,o.version,o.status,o.sale_id FROM checkout_orders o WHERE o.store_id=?
            AND (o.business_date=? OR EXISTS(SELECT 1 FROM checkout_refunds r
            JOIN checkout_attempts a ON a.id=r.attempt_id WHERE a.checkout_id=o.id AND r.business_date=?)) AND o.status='open' ORDER BY o.id""",
        (session['store_id'],session['business_date'],session['business_date']),
    ))
    pending_sales = [row['id'] for row in con.execute(
        """SELECT id FROM sale_documents WHERE store_id=? AND business_date=?
           AND status='posted' AND allocation_status!='allocated' ORDER BY id""",
        (session['store_id'], session['business_date']),
    )]
    hard.extend(f'pending_allocation:{value}' for value in pending_sales)
    latest_count = con.execute(
        """SELECT id, removal_cents FROM closing_cash_counts
           WHERE closing_session_id=? ORDER BY revision DESC LIMIT 1""",
        (session['id'],),
    ).fetchone()
    if latest_count is None:
        hard.append('cash_count_missing')
    elif latest_count['removal_cents'] is None:
        hard.append('retained_float_shortfall')
    unknown_cash = cash_summary(con, session['store_id'], session['business_date'], 0)[
        'unknown_cash_payment_ids'
    ]
    hard.extend(f'cash_payment_unresolved:{value}' for value in unknown_cash)
    hard.extend(f'checkout_cash_unresolved:{value}' for value in cash_summary(
        con,session['store_id'],session['business_date'],0)['unknown_checkout_cash_attempt_ids'])
    open_deliveries = [row['id'] for row in con.execute(
        """SELECT d.id FROM inventory_deliveries d
           JOIN inventory_locations source ON source.id=d.source_location_id
           JOIN inventory_locations destination ON destination.id=d.destination_location_id
           WHERE (source.store_id=? OR destination.store_id=?)
                 AND d.business_date=? AND d.status NOT IN ('completed','cancelled')
           ORDER BY d.id""",
        (session['store_id'], session['store_id'], session['business_date']),
    )]
    hard.extend(f'delivery_unresolved:{value}' for value in open_deliveries)
    open_restocks = [row['id'] for row in con.execute(
        """SELECT id FROM restock_sessions
           WHERE store_id=? AND date=? AND status IN ('submitted','picking') ORDER BY id""",
        (session['store_id'], session['business_date']),
    )]
    hard.extend(f'restock_unresolved:{value}' for value in open_restocks)
    missing_counts = [row['id'] for row in con.execute(
        """SELECT p.id FROM products p WHERE p.is_bestseller=1 AND NOT EXISTS (
               SELECT 1 FROM inventory_counts c JOIN inventory_count_lines l ON l.count_id=c.id
               WHERE c.store_id=? AND c.business_date=? AND c.status='approved'
                     AND l.product_id=p.id
           ) ORDER BY p.id""", (session['store_id'], session['business_date']),
    )]
    hard.extend(f'hot_item_count_missing:{value}' for value in missing_counts)
    paid = {}
    for payment in _payment_rows(con, session['store_id'], session['business_date']):
        if payment['tender'] != 'cash' and payment['state'] != 'verified':
            exceptions.append(f'payment_unverified:{payment["id"]}')
        paid.setdefault(payment['sale_id'], []).append(payment['effective_amount_cents'])
    # As on the sale page, known tenders that do not add up to the collected total need a reason.
    for sale in con.execute(
        """SELECT id, collected_cents FROM sale_documents
           WHERE store_id=? AND business_date=? AND status='posted' ORDER BY id""",
        (session['store_id'], session['business_date']),
    ):
        amounts = paid.get(sale['id'], [])
        if (sale['collected_cents'] is not None and None not in amounts
                and sum(amounts) != sale['collected_cents']):
            exceptions.append(f'payment_mismatch:{sale["id"]}')
    evidence = con.execute(
        """SELECT e.id, e.status FROM payment_evidence e
           JOIN sale_payments p ON p.id=e.payment_id
           JOIN sale_documents s ON s.id=p.sale_id
           WHERE s.store_id=? AND s.business_date=? AND e.status!='accepted'
           ORDER BY e.id""", (session['store_id'], session['business_date']),
    )
    exceptions.extend(f'evidence_{row["status"]}:{row["id"]}' for row in evidence)
    store_id, business_date = session['store_id'], session['business_date']
    feed = con.execute('SELECT * FROM clover_feed WHERE store_id=?', (store_id,)).fetchone()
    if feed:
        since = datetime.fromtimestamp(feed['import_since_ms'] / 1000, ZoneInfo('America/Toronto')).date().isoformat()
        # Every Clover order of the day must be in POPCORE before the day can close.
        if business_date >= since and (feed['day_scan_date'] != business_date
                                       or time.time() - feed['day_scan_at'] > 600):
            hard.append('clover_sync_stale')
    # A Clover order of the day that could not be imported, until it imports or a manager resolves it.
    hard.extend(f'clover_import_failed:{row[0]}' for row in con.execute(
        """SELECT order_id FROM clover_import_failures WHERE store_id=? AND business_date=?
           AND resolved_at IS NULL ORDER BY order_id""", (store_id, business_date)))
    hard.extend(f'clover_writeback_pending:{row[0]}' for row in con.execute(
        """SELECT l.checkout_id FROM clover_orders l JOIN checkout_orders o ON o.id=l.checkout_id
           WHERE o.store_id=? AND o.business_date=? AND o.status='completed'
           AND l.writeback_state IN ('pending','failed') AND EXISTS(SELECT 1 FROM checkout_attempts a
           WHERE a.checkout_id=o.id AND a.origin='popcore' AND a.status='completed') ORDER BY l.checkout_id""",
        (store_id, business_date)))
    exceptions.extend(f'clover_review:{row[0]}' for row in con.execute(
        """SELECT l.checkout_id FROM clover_orders l JOIN checkout_orders o ON o.id=l.checkout_id
           WHERE o.store_id=? AND o.business_date=? AND l.review_reason IS NOT NULL ORDER BY l.checkout_id""",
        (store_id, business_date)))
    return hard, exceptions


def create_closing(con, data, *, actor, request_key):
    store_id = read_int(data.get('store_id'), 'store_id', minimum=1)
    business_date = read_date(data.get('business_date'), 'business_date')
    _require_closing_access(con, store_id, business_date, actor)
    intent = {'store_id': store_id, 'business_date': business_date}
    _begin(con)
    try:
        _require_closing_access(con, store_id, business_date, actor)
        key, digest, prior = _replay(con, request_key, 'closing_create', actor, intent)
        if prior:
            result = closing_detail(con, prior['closing_id'], actor=actor)
            con.commit()
            return result
        row = con.execute(
            'SELECT id FROM closing_sessions WHERE store_id=? AND business_date=?',
            (store_id, business_date),
        ).fetchone()
        if row:
            closing_id = row['id']
        else:
            closing_id = con.execute(
                """INSERT INTO closing_sessions
                   (store_id, business_date, created_by) VALUES (?, ?, ?)""",
                (store_id, business_date, actor['sub']),
            ).lastrowid
        result = closing_detail(con, closing_id, actor=actor)
        _remember(con, key, 'closing_create', closing_id, actor, digest,
                  {'closing_id': closing_id})
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def update_closing(con, closing_id, data, *, actor, request_key):
    session = _closing(con, closing_id)
    _require_closing_access(con, session['store_id'], session['business_date'], actor)
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    intake_complete = data.get('intake_complete')
    if type(intake_complete) is not bool:
        raise InventoryValidationError('intake_complete must be true or false')
    intent = {'closing_id': closing_id, 'expected_version': expected_version,
              'intake_complete': intake_complete}
    _begin(con)
    try:
        _require_closing_access(con, session['store_id'], session['business_date'], actor)
        session = _closing(con, closing_id)
        key, digest, prior = _replay(con, request_key, 'closing_update', actor, intent)
        if prior:
            result = closing_detail(con, closing_id, actor=actor)
            con.commit()
            return result
        if session['status'] != 'draft' or session['version'] != expected_version:
            raise InventoryConflict('Closing changed before save', 'closing_state_conflict')
        con.execute(
            'UPDATE closing_sessions SET intake_complete=?, version=version+1 WHERE id=?',
            (intake_complete, closing_id),
        )
        result = closing_detail(con, closing_id, actor=actor)
        _remember(con, key, 'closing_update', closing_id, actor, digest,
                  {'closing_id': closing_id})
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def add_cash_event(con, closing_id, data, *, actor, request_key):
    session = _closing(con, closing_id)
    _require_closing_access(con, session['store_id'], session['business_date'], actor)
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    event_type = data.get('event_type')
    if event_type not in {'paid_in', 'refund', 'payout'}:
        raise InventoryValidationError('cash event type is invalid')
    amount = read_int(data.get('amount_cents'), 'amount_cents', minimum=1)
    reason = str(data.get('reason') or '').strip()
    if not reason and event_type == 'refund':
        # Only for money returned with no sale in POPCORE: a sale's refund is recorded on that sale
        # and already counts on the day it is recorded (owner, 2026-10-27).
        raise InventoryValidationError('A cash refund with no sale in POPCORE needs a note: who was refunded and why')
    if not reason:
        raise InventoryValidationError('cash event reason is required')
    intent = {'closing_id': closing_id, 'expected_version': expected_version,
              'event_type': event_type, 'amount_cents': amount, 'reason': reason}
    _begin(con)
    try:
        _require_closing_access(con, session['store_id'], session['business_date'], actor)
        session = _closing(con, closing_id)
        key, digest, prior = _replay(con, request_key, 'cash_event', actor, intent)
        if prior:
            result = closing_detail(con, closing_id, actor=actor)
            con.commit()
            return result
        if session['status'] != 'draft' or session['version'] != expected_version:
            raise InventoryConflict('Closing changed before cash event', 'closing_state_conflict')
        con.execute(
            """INSERT INTO cash_events
               (store_id, business_date, closing_session_id, event_type,
                amount_cents, reason, actor_sub)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session['store_id'], session['business_date'], closing_id,
             event_type, amount, reason, actor['sub']),
        )
        con.execute('UPDATE closing_sessions SET version=version+1 WHERE id=?', (closing_id,))
        result = closing_detail(con, closing_id, actor=actor)
        _remember(con, key, 'cash_event', closing_id, actor, digest,
                  {'closing_id': closing_id})
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def add_cash_count(con, closing_id, data, *, actor, request_key):
    session = _closing(con, closing_id)
    _require_closing_access(con, session['store_id'], session['business_date'], actor)
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    supplied_token = str(data.get('source_token') or '')
    opening_coins = read_int(data.get('opening_coin_cents'), 'opening_coin_cents')
    retained_coins = read_int(data.get('retained_coin_cents'), 'retained_coin_cents')
    raw_counts = data.get('denomination_counts')
    if not isinstance(raw_counts, dict) or set(raw_counts) != set(DENOMINATIONS):
        raise InventoryValidationError('all denomination counts are required')
    counts = {key: read_int(raw_counts[key], f'denomination_counts.{key}')
              for key in DENOMINATIONS}
    intent = {'closing_id': closing_id, 'expected_version': expected_version,
              'source_token': supplied_token, 'opening_coin_cents': opening_coins,
              'retained_coin_cents': retained_coins, 'denomination_counts': counts}
    _begin(con)
    try:
        _require_closing_access(con, session['store_id'], session['business_date'], actor)
        session = _closing(con, closing_id)
        key, digest, prior = _replay(con, request_key, 'cash_count', actor, intent)
        if prior:
            result = closing_detail(con, closing_id, actor=actor)
            con.commit()
            return result
        current_token = source_token(con, session)
        if supplied_token != current_token:
            raise InventoryConflict('Closing sources changed; refresh first', 'closing_stale')
        if session['status'] != 'draft' or session['version'] != expected_version:
            raise InventoryConflict('Closing changed before cash count', 'closing_state_conflict')
        summary = cash_summary(con, session['store_id'], session['business_date'], opening_coins)
        cash_token = _cash_source_token(con, session)
        counted = sum(DENOMINATIONS[key] * count for key, count in counts.items())
        retained = OPENING_BILLS_CENTS + retained_coins
        removal = counted - retained if counted >= retained else None
        revision = con.execute(
            """SELECT COALESCE(MAX(revision), 0) + 1 FROM closing_cash_counts
               WHERE closing_session_id=?""", (closing_id,),
        ).fetchone()[0]
        con.execute(
            """INSERT INTO closing_cash_counts
               (closing_session_id, revision, denomination_counts_json,
                opening_coin_cents, retained_coin_cents, counted_cents,
                expected_cents, variance_cents, retained_cents, removal_cents,
                source_token, counted_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (closing_id, revision, json.dumps(counts, sort_keys=True), opening_coins,
             retained_coins, counted, summary['expected_drawer_cents'],
             counted - summary['expected_drawer_cents'], retained, removal,
             cash_token, actor['sub']),
        )
        con.execute('UPDATE closing_sessions SET version=version+1 WHERE id=?', (closing_id,))
        result = closing_detail(con, closing_id, actor=actor)
        _remember(con, key, 'cash_count', closing_id, actor, digest,
                  {'closing_id': closing_id})
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def submit_closing(con, closing_id, data, *, actor, request_key):
    session = _closing(con, closing_id)
    _require_closing_access(con, session['store_id'], session['business_date'], actor)
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    supplied_token = str(data.get('source_token') or '')
    intent = {'closing_id': closing_id, 'expected_version': expected_version,
              'source_token': supplied_token}
    _begin(con)
    try:
        _require_closing_access(con, session['store_id'], session['business_date'], actor)
        session = _closing(con, closing_id)
        key, digest, prior = _replay(con, request_key, 'closing_submit', actor, intent)
        if prior:
            result = closing_detail(con, closing_id, actor=actor)
            con.commit()
            return result
        if session['status'] != 'draft' or session['version'] != expected_version:
            raise InventoryConflict('Closing changed before submission', 'closing_state_conflict')
        if supplied_token != source_token(con, session):
            raise InventoryConflict('Closing sources changed; refresh first', 'closing_stale')
        count = con.execute(
            """SELECT source_token FROM closing_cash_counts
               WHERE closing_session_id=? ORDER BY revision DESC LIMIT 1""",
            (closing_id,),
        ).fetchone()
        if count is not None and count['source_token'] != _cash_source_token(con, session):
            raise InventoryConflict('Cash sources changed; recount first', 'closing_stale')
        hard, _ = closing_issues(con, session)
        if hard:
            # Staff are told what kind of blocker remains, not whose order it is.
            named = hard if _is_manager(actor) else sorted({code.split(':')[0] for code in hard})
            raise InventoryConflict(
                f'Closing blockers remain: {", ".join(named)}', 'closing_blocked'
            )
        con.execute(
            """UPDATE closing_sessions SET status='submitted', version=version+1,
                      submitted_by=?, submitted_at=? WHERE id=?""",
            (actor['sub'], _utc_now(), closing_id),
        )
        updated = _closing(con, closing_id)
        token = source_token(con, updated)
        con.execute('UPDATE closing_sessions SET source_token=? WHERE id=?', (token, closing_id))
        result = closing_detail(con, closing_id, actor=actor)
        _remember(con, key, 'closing_submit', closing_id, actor, digest,
                  {'closing_id': closing_id})
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def return_closing(con, closing_id, data, *, actor, request_key):
    session = _closing(con, closing_id)
    _require_closing_access(con, session['store_id'], session['business_date'], actor, 'manager')
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    reason = str(data.get('reason') or '').strip()
    if not reason:
        raise InventoryValidationError('return reason is required')
    intent = {'closing_id': closing_id, 'expected_version': expected_version, 'reason': reason}
    _begin(con)
    try:
        _require_closing_access(con, session['store_id'], session['business_date'], actor, 'manager')
        session = _closing(con, closing_id)
        key, digest, prior = _replay(con, request_key, 'closing_return', actor, intent)
        if prior:
            result = closing_detail(con, closing_id, actor=actor)
            con.commit()
            return result
        if session['status'] != 'submitted' or session['version'] != expected_version:
            raise InventoryConflict('Closing cannot be returned', 'closing_state_conflict')
        con.execute(
            """UPDATE closing_sessions SET status='draft', version=version+1,
                      source_token=NULL WHERE id=?""", (closing_id,),
        )
        con.execute(
            """INSERT INTO closing_adjustments
               (closing_session_id, source_type, source_id, reason, actor_sub)
               VALUES (?, 'review_return', ?, ?, ?)""",
            (closing_id, key, reason, actor['sub']),
        )
        result = closing_detail(con, closing_id, actor=actor)
        _remember(con, key, 'closing_return', closing_id, actor, digest,
                  {'closing_id': closing_id})
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def close_closing(con, closing_id, data, *, actor, request_key):
    session = _closing(con, closing_id)
    _require_closing_access(con, session['store_id'], session['business_date'], actor, 'manager')
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    supplied_token = str(data.get('source_token') or '')
    raw_exceptions = data.get('accepted_exceptions', [])
    if not isinstance(raw_exceptions, list):
        raise InventoryValidationError('accepted_exceptions must be a list')
    accepted = {}
    for item in raw_exceptions:
        if not isinstance(item, dict):
            raise InventoryValidationError('each accepted exception must be an object')
        code = str(item.get('code') or '').strip()
        reason = str(item.get('reason') or '').strip()
        if not code or not reason or code in accepted:
            raise InventoryValidationError('each exception needs a unique code and reason')
        accepted[code] = reason
    intent = {'closing_id': closing_id, 'expected_version': expected_version,
              'source_token': supplied_token, 'accepted_exceptions': accepted}
    _begin(con)
    try:
        _require_closing_access(con, session['store_id'], session['business_date'], actor, 'manager')
        session = _closing(con, closing_id)
        key, digest, prior = _replay(con, request_key, 'closing_close', actor, intent)
        if prior:
            con.commit()
            return prior
        if session['status'] != 'submitted' or session['version'] != expected_version:
            raise InventoryConflict('Closing cannot be signed off', 'closing_state_conflict')
        current_token = source_token(con, session)
        if supplied_token != current_token or session['source_token'] != current_token:
            raise InventoryConflict('Closing sources changed; refresh and review', 'closing_stale')
        hard, exceptions = closing_issues(con, session)
        if hard:
            raise InventoryConflict(
                f'Closing blockers remain: {", ".join(hard)}', 'closing_blocked'
            )
        if set(accepted) != set(exceptions):
            raise InventoryConflict('Every current exception requires a named reason',
                                    'closing_exception_review_required')
        count = con.execute(
            """SELECT * FROM closing_cash_counts WHERE closing_session_id=?
               ORDER BY revision DESC LIMIT 1""", (closing_id,),
        ).fetchone()
        source_facts = _source_facts(con, session)
        payments = _payment_rows(con, session['store_id'], session['business_date'])
        tender_totals,unknown_payment_ids = _tender_summary(
            payments, source_facts['checkout_funds'],
            _refund_rows(con, session['store_id'], session['business_date']))
        cash = cash_summary(
            con, session['store_id'], session['business_date'],
            count['opening_coin_cents'],
        )
        removal_id = None
        if count['removal_cents'] > 0:
            removal_id = con.execute(
                """INSERT INTO cash_events
                   (store_id, business_date, closing_session_id, event_type,
                    amount_cents, reason, actor_sub)
                   VALUES (?, ?, ?, 'removal', ?, 'Final counted cash removal', ?)""",
                (session['store_id'], session['business_date'], closing_id,
                 count['removal_cents'], actor['sub']),
            ).lastrowid
        snapshot = {
            'business_date': session['business_date'],
            'store_id': session['store_id'],
            'cash_count_id': count['id'],
            'counted_cents': count['counted_cents'],
            'expected_cents': count['expected_cents'],
            'variance_cents': count['variance_cents'],
            'retained_cents': count['retained_cents'],
            'removal_cents': count['removal_cents'],
            'accepted_exceptions': accepted,
            'tender_totals_cents': tender_totals,
            'unknown_payment_ids': unknown_payment_ids,
            'cash': cash,
            'sources': source_facts,
            'sale_ids': [row['id'] for row in con.execute(
                """SELECT id FROM sale_documents WHERE store_id=? AND business_date=?
                   AND status='posted' ORDER BY id""",
                (session['store_id'], session['business_date']),
            )],
            'payment_ids': [row['id'] for row in con.execute(
                """SELECT p.id FROM sale_payments p JOIN sale_documents s ON s.id=p.sale_id
                   WHERE s.store_id=? AND s.business_date=? ORDER BY p.id""",
                (session['store_id'], session['business_date']),
            )],
        }
        snapshot_id = con.execute(
            """INSERT INTO closing_snapshots
               (closing_session_id, source_token, snapshot_json,
                cash_removal_event_id, reviewed_by)
               VALUES (?, ?, ?, ?, ?)""",
            (closing_id, current_token, json.dumps(snapshot, sort_keys=True),
             removal_id, actor['sub']),
        ).lastrowid
        con.execute(
            """UPDATE closing_sessions SET status='closed', version=version+1,
                      reviewed_by=?, closed_at=? WHERE id=?""",
            (actor['sub'], _utc_now(), closing_id),
        )
        result = {'closing_id': closing_id, 'snapshot_id': snapshot_id,
                  'version': expected_version + 1, 'status': 'closed',
                  'cash_removal_event_id': removal_id}
        _remember(con, key, 'closing_close', closing_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def add_closing_adjustment(con, closing_id, data, *, actor, request_key):
    session = _closing(con, closing_id)
    _require_closing_access(con, session['store_id'], session['business_date'], actor, 'manager')
    if session['status'] != 'closed':
        raise InventoryConflict('Adjustments require a closed session', 'closing_state_conflict')
    source_type = str(data.get('source_type') or '').strip()
    source_id = str(data.get('source_id') or '').strip()
    reason = str(data.get('reason') or '').strip()
    if not source_type or not source_id or not reason:
        raise InventoryValidationError('adjustment source and reason are required')
    intent = {'closing_id': closing_id, 'source_type': source_type,
              'source_id': source_id, 'reason': reason}
    _begin(con)
    try:
        _require_closing_access(con, session['store_id'], session['business_date'], actor, 'manager')
        key, digest, prior = _replay(con, request_key, 'closing_adjustment', actor, intent)
        if prior:
            con.commit()
            return prior
        adjustment_id = con.execute(
            """INSERT INTO closing_adjustments
               (closing_session_id, source_type, source_id, reason, actor_sub)
               VALUES (?, ?, ?, ?, ?)""",
            (closing_id, source_type, source_id, reason, actor['sub']),
        ).lastrowid
        result = {'closing_id': closing_id, 'adjustment_id': adjustment_id}
        _remember(con, key, 'closing_adjustment', adjustment_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
