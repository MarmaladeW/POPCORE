"""Versioned store-day closing and exact cash arithmetic."""
import hashlib
import json
from datetime import datetime, timezone

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


def _payment_rows(con, store_id, business_date):
    rows = []
    for row in con.execute(
        """SELECT p.* FROM sale_payments p
           JOIN sale_documents s ON s.id=p.sale_id
           WHERE s.store_id=? AND s.business_date=? AND s.status='posted'
           ORDER BY p.id""", (store_id, business_date),
    ):
        item = dict(row)
        effective = item['amount_cents']
        if effective is not None:
            for event in con.execute(
                """SELECT direction, amount_cents FROM payment_events
                   WHERE payment_id=? ORDER BY id""", (item['id'],),
            ):
                if event['direction'] == 'increase':
                    effective += event['amount_cents']
                elif event['direction'] == 'decrease':
                    effective -= event['amount_cents']
        item['effective_amount_cents'] = effective
        rows.append(item)
    return rows


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
    opening = OPENING_BILLS_CENTS + opening_coin_cents
    expected = (
        opening + cash_receipts + event_totals['paid_in']
        - event_totals['refund'] - event_totals['payout'] - event_totals['removal']
    )
    return {
        'opening_cash_cents': opening,
        'verified_cash_receipts_cents': cash_receipts,
        'unknown_cash_payment_ids': unknown_cash,
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
        'payment_events': [tuple(row) for row in con.execute(
            """SELECT e.id, e.payment_id, e.event_type, e.direction, e.amount_cents,
                      e.reason, e.actor_sub, e.created_at
               FROM payment_events e JOIN sale_payments p ON p.id=e.payment_id
               JOIN sale_documents s ON s.id=p.sale_id
               WHERE s.store_id=? AND s.business_date=? ORDER BY e.id""",
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
    }


def source_token(con, session):
    sources = _source_facts(con, session)
    raw = json.dumps(sources, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _cash_source_token(con, session):
    facts = _source_facts(con, session)
    cash_sources = {
        key: facts[key] for key in ('payments', 'payment_events', 'cash_events')
    }
    raw = json.dumps(cash_sources, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def closing_detail(con, closing_id, *, actor):
    session = _closing(con, closing_id)
    require_inventory_access(con, actor, (session['store_id'],), 'staff')
    token = source_token(con, session)
    count = con.execute(
        """SELECT * FROM closing_cash_counts WHERE closing_session_id=?
           ORDER BY revision DESC LIMIT 1""", (closing_id,),
    ).fetchone()
    opening_coin = count['opening_coin_cents'] if count else 0
    result = {
        **session, 'closing_id': session['id'], 'source_token': token,
        'cash': cash_summary(con, session['store_id'], session['business_date'], opening_coin),
        'latest_cash_count': dict(count) if count else None,
        'source_documents': {
            'sales': [dict(row) for row in con.execute(
                """SELECT id, status, allocation_status, financial_status FROM sale_documents
                   WHERE store_id=? AND business_date=? ORDER BY id""",
                (session['store_id'], session['business_date']))
            ],
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
    if ROLE_HIERARCHY.get(actor.get(ROLE_CLAIM, 'viewer'), 0) >= ROLE_HIERARCHY['manager']:
        totals = {tender: 0 for tender in ('cash', 'card', 'e_transfer', 'wechat', 'alipay')}
        unknown = []
        for payment in _payment_rows(con, session['store_id'], session['business_date']):
            if payment['effective_amount_cents'] is None:
                unknown.append(payment['id'])
            else:
                totals[payment['tender']] += payment['effective_amount_cents']
        result['tender_totals_cents'] = totals
        result['unknown_payment_ids'] = unknown
        hard, exceptions = closing_issues(con, session)
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
    for payment in _payment_rows(con, session['store_id'], session['business_date']):
        if payment['tender'] != 'cash' and payment['state'] != 'verified':
            exceptions.append(f'payment_unverified:{payment["id"]}')
    evidence = con.execute(
        """SELECT e.id, e.status FROM payment_evidence e
           JOIN sale_payments p ON p.id=e.payment_id
           JOIN sale_documents s ON s.id=p.sale_id
           WHERE s.store_id=? AND s.business_date=? AND e.status!='accepted'
           ORDER BY e.id""", (session['store_id'], session['business_date']),
    )
    exceptions.extend(f'evidence_{row["status"]}:{row["id"]}' for row in evidence)
    return hard, exceptions


def create_closing(con, data, *, actor, request_key):
    store_id = read_int(data.get('store_id'), 'store_id', minimum=1)
    business_date = read_date(data.get('business_date'), 'business_date')
    require_inventory_access(con, actor, (store_id,), 'staff')
    intent = {'store_id': store_id, 'business_date': business_date}
    _begin(con)
    try:
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
    require_inventory_access(con, actor, (session['store_id'],), 'staff')
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    intake_complete = data.get('intake_complete')
    if type(intake_complete) is not bool:
        raise InventoryValidationError('intake_complete must be true or false')
    intent = {'closing_id': closing_id, 'expected_version': expected_version,
              'intake_complete': intake_complete}
    _begin(con)
    try:
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
    require_inventory_access(con, actor, (session['store_id'],), 'staff')
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    event_type = data.get('event_type')
    if event_type not in {'paid_in', 'refund', 'payout'}:
        raise InventoryValidationError('cash event type is invalid')
    amount = read_int(data.get('amount_cents'), 'amount_cents', minimum=1)
    reason = str(data.get('reason') or '').strip()
    if not reason:
        raise InventoryValidationError('cash event reason is required')
    intent = {'closing_id': closing_id, 'expected_version': expected_version,
              'event_type': event_type, 'amount_cents': amount, 'reason': reason}
    _begin(con)
    try:
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
    require_inventory_access(con, actor, (session['store_id'],), 'staff')
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
    require_inventory_access(con, actor, (session['store_id'],), 'staff')
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    supplied_token = str(data.get('source_token') or '')
    intent = {'closing_id': closing_id, 'expected_version': expected_version,
              'source_token': supplied_token}
    _begin(con)
    try:
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
            raise InventoryConflict(
                f'Closing blockers remain: {", ".join(hard)}', 'closing_blocked'
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
    require_inventory_access(con, actor, (session['store_id'],), 'manager')
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    reason = str(data.get('reason') or '').strip()
    if not reason:
        raise InventoryValidationError('return reason is required')
    intent = {'closing_id': closing_id, 'expected_version': expected_version, 'reason': reason}
    _begin(con)
    try:
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
    require_inventory_access(con, actor, (session['store_id'],), 'manager')
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
        tender_totals = {
            tender: 0 for tender in ('cash', 'card', 'e_transfer', 'wechat', 'alipay')
        }
        unknown_payment_ids = []
        for payment in payments:
            if payment['effective_amount_cents'] is None:
                unknown_payment_ids.append(payment['id'])
            else:
                tender_totals[payment['tender']] += payment['effective_amount_cents']
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
    require_inventory_access(con, actor, (session['store_id'],), 'manager')
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
