"""Receiving, delivery, and count workflows backed by the inventory ledger."""
import hashlib
import json
import sqlite3

from inventory_commands import (
    INVENTORY_BUSY_TIMEOUT_MS,
    InventoryBusy,
    InventoryConflict,
    InventoryValidationError,
    _post_inventory_in_transaction,
    require_inventory_access,
)
from validation import read_date, read_int


def _hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _begin(con):
    con.execute(f'PRAGMA busy_timeout={INVENTORY_BUSY_TIMEOUT_MS}')
    try:
        con.execute('BEGIN IMMEDIATE')
    except sqlite3.OperationalError as exc:
        if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
            raise InventoryBusy() from exc
        raise


def _replay(con, request_key, operation, actor, intent):
    if not isinstance(request_key, str) or not request_key.strip():
        raise InventoryValidationError('Idempotency-Key is required')
    request_key = request_key.strip()
    digest = _hash(intent)
    row = con.execute(
        """SELECT operation, actor_sub, payload_hash, stored_result
           FROM operation_requests WHERE request_key=?""", (request_key,)
    ).fetchone()
    if row:
        if (row['operation'] != operation or row['actor_sub'] != actor.get('sub')
                or row['payload_hash'] != digest):
            raise InventoryConflict(
                'Request key was already used for different intent',
                'idempotency_conflict',
            )
        return request_key, digest, json.loads(row['stored_result'])
    return request_key, digest, None


def _remember(con, request_key, operation, resource_id, actor, digest, result):
    con.execute(
        """INSERT INTO operation_requests
           (request_key, operation, resource_id, actor_sub, payload_hash, stored_result)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (request_key, operation, resource_id, actor['sub'], digest,
         json.dumps(result, sort_keys=True, separators=(',', ':'))),
    )


def _location(con, location_id):
    location_id = read_int(location_id, 'location_id', minimum=1)
    row = con.execute(
        """SELECT l.id, l.store_id, l.code, ss.opening_verified
           FROM inventory_locations l
           JOIN stores s ON s.id=l.store_id
           LEFT JOIN inventory_scope_state ss ON ss.location_id=l.id
           WHERE l.id=? AND l.is_active=1 AND s.is_active=1""", (location_id,)
    ).fetchone()
    if row is None:
        raise InventoryValidationError('location_id is invalid')
    if not row['opening_verified']:
        raise InventoryConflict('Opening balance is not verified', 'opening_unverified')
    return dict(row)


def _product(con, product_id, unit):
    product_id = read_int(product_id, 'product_id', minimum=1)
    row = con.execute(
        """SELECT id, stock_unit, identity_status FROM products WHERE id=?""",
        (product_id,),
    ).fetchone()
    if row is None or row['identity_status'] != 'verified':
        raise InventoryConflict('Product identity is unverified', 'identity_unverified')
    if unit != row['stock_unit']:
        raise InventoryValidationError('unit does not match product unit')
    return product_id


def _quantity(value, field, *, allow_zero=False):
    return read_int(value, field, minimum=0 if allow_zero else 1)


def _balance(con, product_id, location_id, disposition):
    row = con.execute(
        """SELECT quantity, version FROM inventory_balances
           WHERE product_id=? AND location_id=? AND disposition=?""",
        (product_id, location_id, disposition),
    ).fetchone()
    return {'quantity': row['quantity'], 'version': row['version']} if row else {
        'quantity': 0, 'version': 0,
    }


def _selected_open_set(con, open_set_id, product_id, location_id):
    if open_set_id is None:
        return None
    open_set_id = read_int(open_set_id, 'open_set_id', minimum=1)
    row = con.execute(
        """SELECT id, opening_document_id, random_product_id, location_id,
                  purpose, remaining_qty
           FROM inventory_open_sets WHERE id=?""",
        (open_set_id,),
    ).fetchone()
    if (row is None or row['random_product_id'] != product_id
            or row['location_id'] != location_id):
        raise InventoryValidationError('open_set_id does not match the delivery source')
    return dict(row)


def _restore_open_set(con, line, location_id, quantity):
    if line['open_set_id'] is None:
        return
    con.execute(
        """INSERT INTO inventory_open_sets
           (opening_document_id, random_product_id, location_id, purpose, remaining_qty)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(opening_document_id, random_product_id, location_id, purpose)
           DO UPDATE SET remaining_qty=remaining_qty+excluded.remaining_qty""",
        (line['open_set_opening_document_id'], line['product_id'], location_id,
         line['open_set_purpose'], quantity),
    )


def _receipt_intent(con, data, actor):
    if not isinstance(data, dict):
        raise InventoryValidationError('body must be an object')
    store_id = read_int(data.get('store_id'), 'store_id', minimum=1)
    destination = _location(con, data.get('destination_location_id'))
    if destination['store_id'] != store_id:
        raise InventoryValidationError('destination does not belong to store')
    require_inventory_access(con, actor, (store_id,), 'staff')
    business_date = read_date(data.get('business_date'), 'business_date')
    raw_lines = data.get('lines')
    if not isinstance(raw_lines, list) or not raw_lines:
        raise InventoryValidationError('lines must be a non-empty list')
    lines = []
    for index, raw in enumerate(raw_lines, 1):
        if not isinstance(raw, dict):
            raise InventoryValidationError(f'line {index} must be an object')
        unit = raw.get('unit')
        product_id = _product(con, raw.get('product_id'), unit)
        expected = raw.get('expected_quantity')
        expected = None if expected is None else _quantity(
            expected, 'expected_quantity', allow_zero=True
        )
        values = {
            key: _quantity(raw.get(key, 0), key, allow_zero=True)
            for key in ('saleable_quantity', 'damaged_quantity', 'hold_quantity')
        }
        actual = sum(values.values())
        if actual <= 0:
            raise InventoryValidationError('receipt line actual quantity must be positive')
        note = (raw.get('discrepancy_note') or '').strip() or None
        if expected is not None and expected != actual and not note:
            raise InventoryValidationError('discrepancy_note is required')
        lines.append({
            'product_id': product_id, 'unit': unit,
            'expected_quantity': expected, **values,
            'discrepancy_note': note,
        })
    return {
        'store_id': store_id, 'destination_location_id': destination['id'],
        'business_date': business_date,
        'shipment_reference': (data.get('shipment_reference') or '').strip() or None,
        'supplier': (data.get('supplier') or '').strip() or None,
        'lines': lines,
    }


def _replace_receipt_lines(con, receipt_id, lines):
    con.execute('DELETE FROM goods_receipt_lines WHERE receipt_id=?', (receipt_id,))
    for line_no, line in enumerate(lines, 1):
        con.execute(
            """INSERT INTO goods_receipt_lines
               (receipt_id, line_no, product_id, native_unit,
                expected_quantity, saleable_quantity, damaged_quantity,
                hold_quantity, discrepancy_note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (receipt_id, line_no, line['product_id'], line['unit'],
             line['expected_quantity'], line['saleable_quantity'],
             line['damaged_quantity'], line['hold_quantity'],
             line['discrepancy_note']),
        )


def create_receipt(con, data, *, actor, request_key):
    intent = _receipt_intent(con, data, actor)
    store_id = intent['store_id']
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'receipt_create', actor, intent)
        if prior:
            con.commit()
            return prior
        cur = con.execute(
            """INSERT INTO goods_receipts
               (store_id, destination_location_id, business_date,
                shipment_reference, supplier, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (store_id, intent['destination_location_id'], intent['business_date'],
             intent['shipment_reference'], intent['supplier'], actor['sub']),
        )
        receipt_id = cur.lastrowid
        _replace_receipt_lines(con, receipt_id, intent['lines'])
        result = {'id': receipt_id, 'version': 1, 'status': 'draft'}
        _remember(con, key, 'receipt_create', receipt_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def _receipt(con, receipt_id):
    receipt_id = read_int(receipt_id, 'receipt_id', minimum=1)
    row = con.execute('SELECT * FROM goods_receipts WHERE id=?', (receipt_id,)).fetchone()
    if row is None:
        raise InventoryValidationError('receipt does not exist')
    return dict(row)


def update_receipt(con, receipt_id, data, *, actor, request_key):
    receipt = _receipt(con, receipt_id)
    require_inventory_access(con, actor, (receipt['store_id'],), 'staff')
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    intent = _receipt_intent(con, data, actor)
    intent = {'receipt_id': receipt['id'], 'expected_version': expected_version, **intent}
    _begin(con)
    try:
        receipt = _receipt(con, receipt_id)
        key, digest, prior = _replay(con, request_key, 'receipt_update', actor, intent)
        if prior:
            con.commit()
            return prior
        if receipt['status'] != 'draft' or receipt['version'] != expected_version:
            raise InventoryConflict('Receipt changed before save', 'receipt_state_conflict')
        changed = con.execute(
            """UPDATE goods_receipts SET store_id=?, destination_location_id=?,
                      business_date=?, shipment_reference=?, supplier=?, version=version+1
               WHERE id=? AND status='draft' AND version=?""",
            (intent['store_id'], intent['destination_location_id'],
             intent['business_date'], intent['shipment_reference'], intent['supplier'],
             receipt['id'], expected_version),
        )
        if changed.rowcount != 1:
            raise InventoryConflict('Receipt changed before save', 'receipt_state_conflict')
        _replace_receipt_lines(con, receipt['id'], intent['lines'])
        response = {
            'id': receipt['id'], 'version': expected_version + 1,
            'status': 'draft', 'inventory_document_id': None,
        }
        _remember(con, key, 'receipt_update', receipt['id'], actor, digest, response)
        con.commit()
        return response
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def post_receipt(con, receipt_id, data, *, actor, request_key):
    receipt = _receipt(con, receipt_id)
    require_inventory_access(con, actor, (receipt['store_id'],), 'staff')
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    intent = {'receipt_id': receipt['id'], 'expected_version': expected_version}
    _begin(con)
    try:
        receipt = _receipt(con, receipt_id)
        require_inventory_access(con, actor, (receipt['store_id'],), 'staff')
        key, digest, prior = _replay(con, request_key, 'receipt_post', actor, intent)
        if prior:
            con.commit()
            return prior
        if receipt['status'] != 'draft' or receipt['version'] != expected_version:
            raise InventoryConflict('Receipt changed before posting', 'receipt_state_conflict')
        grouped = {}
        for row in con.execute(
            'SELECT * FROM goods_receipt_lines WHERE receipt_id=? ORDER BY line_no',
            (receipt['id'],),
        ):
            for disposition, column in (
                ('saleable', 'saleable_quantity'), ('damaged', 'damaged_quantity'),
                ('hold', 'hold_quantity'),
            ):
                if row[column]:
                    group = (row['product_id'], row['native_unit'], disposition)
                    grouped[group] = grouped.get(group, 0) + row[column]
        lines = []
        for (product_id, unit, disposition), quantity in grouped.items():
            current = _balance(
                con, product_id, receipt['destination_location_id'], disposition
            )
            lines.append({
                'product_id': product_id, 'unit': unit, 'quantity': quantity,
                'to_location_id': receipt['destination_location_id'],
                'to_disposition': disposition,
                'expected_versions': {'to': current['version']},
            })
        result = _post_inventory_in_transaction(con, {
            'kind': 'receipt', 'business_date': receipt['business_date'],
            'source_type': 'goods_receipt', 'source_id': str(receipt['id']),
            'reason': receipt['shipment_reference'] or 'Goods receipt',
            'lines': lines,
        }, actor=actor, request_key=f'inventory:{key}')
        changed = con.execute(
            """UPDATE goods_receipts
               SET status='posted', version=version+1, inventory_document_id=?
               WHERE id=? AND status='draft' AND version=?""",
            (result['document_id'], receipt['id'], expected_version),
        )
        if changed.rowcount != 1:
            raise InventoryConflict('Receipt changed before posting', 'receipt_state_conflict')
        response = {
            'id': receipt['id'], 'version': expected_version + 1,
            'status': 'posted', 'inventory_document_id': result['document_id'],
            'balances': result['balances'],
        }
        _remember(con, key, 'receipt_post', receipt['id'], actor, digest, response)
        con.commit()
        return response
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def cancel_receipt(con, receipt_id, data, *, actor, request_key):
    receipt = _receipt(con, receipt_id)
    require_inventory_access(con, actor, (receipt['store_id'],), 'staff')
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    intent = {'receipt_id': receipt['id'], 'expected_version': expected_version}
    _begin(con)
    try:
        receipt = _receipt(con, receipt_id)
        require_inventory_access(con, actor, (receipt['store_id'],), 'staff')
        key, digest, prior = _replay(con, request_key, 'receipt_cancel', actor, intent)
        if prior:
            con.commit()
            return prior
        changed = con.execute(
            """UPDATE goods_receipts SET status='cancelled', version=version+1
               WHERE id=? AND status='draft' AND version=?""",
            (receipt['id'], expected_version),
        )
        if changed.rowcount != 1:
            raise InventoryConflict('Receipt changed before cancellation',
                                    'receipt_state_conflict')
        response = {
            'id': receipt['id'], 'version': expected_version + 1,
            'status': 'cancelled', 'inventory_document_id': None,
        }
        _remember(con, key, 'receipt_cancel', receipt['id'], actor, digest, response)
        con.commit()
        return response
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def create_delivery(con, data, *, actor, request_key, kind='transfer'):
    if kind not in {'transfer', 'restock'}:
        raise InventoryValidationError('delivery kind is invalid')
    source = _location(con, data.get('source_location_id'))
    destination = _location(con, data.get('destination_location_id'))
    if source['id'] == destination['id']:
        raise InventoryValidationError('source and destination must differ')
    minimum = 'manager' if kind == 'transfer' else 'staff'
    require_inventory_access(
        con, actor, (source['store_id'], destination['store_id']), minimum
    )
    business_date = read_date(data.get('business_date'), 'business_date')
    raw_lines = data.get('lines')
    if not isinstance(raw_lines, list) or not raw_lines:
        raise InventoryValidationError('lines must be a non-empty list')
    grouped = {}
    for raw in raw_lines:
        unit = raw.get('unit')
        product_id = _product(con, raw.get('product_id'), unit)
        selected = _selected_open_set(
            con, raw.get('open_set_id'), product_id, source['id']
        )
        group = (product_id, unit, selected['id'] if selected else None)
        quantity = _quantity(raw.get('requested_quantity'), 'requested_quantity')
        if group not in grouped:
            grouped[group] = {
                'product_id': product_id, 'unit': unit,
                'requested_quantity': 0,
                'open_set_id': selected['id'] if selected else None,
                'open_set_opening_document_id': (
                    selected['opening_document_id'] if selected else None
                ),
                'open_set_purpose': selected['purpose'] if selected else None,
            }
        grouped[group]['requested_quantity'] += quantity
    lines = list(grouped.values())
    for line in lines:
        if line['open_set_id'] is not None:
            selected = _selected_open_set(
                con, line['open_set_id'], line['product_id'], source['id']
            )
            if line['requested_quantity'] > selected['remaining_qty']:
                raise InventoryConflict(
                    'Selected opened set has insufficient retained quantity',
                    'insufficient_stock',
                )
    intent = {
        'kind': kind, 'source_location_id': source['id'],
        'destination_location_id': destination['id'],
        'business_date': business_date, 'lines': lines,
        'restock_session_id': data.get('restock_session_id'),
    }
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'delivery_create', actor, intent)
        if prior:
            con.commit()
            return prior
        cur = con.execute(
            """INSERT INTO inventory_deliveries
               (kind, restock_session_id, source_location_id,
                destination_location_id, business_date, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (kind, intent['restock_session_id'], source['id'], destination['id'],
             business_date, actor['sub']),
        )
        delivery_id = cur.lastrowid
        for line_no, line in enumerate(lines, 1):
            con.execute(
                """INSERT INTO inventory_delivery_lines
                   (delivery_id, line_no, product_id, native_unit, requested_quantity,
                    open_set_id, open_set_opening_document_id, open_set_purpose)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (delivery_id, line_no, line['product_id'], line['unit'],
                 line['requested_quantity'], line['open_set_id'],
                 line['open_set_opening_document_id'], line['open_set_purpose']),
            )
        response = {'id': delivery_id, 'version': 1, 'status': 'planned'}
        _remember(con, key, 'delivery_create', delivery_id, actor, digest, response)
        con.commit()
        return response
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def delivery_detail(con, delivery_id, *, actor):
    delivery_id = read_int(delivery_id, 'delivery_id', minimum=1)
    row = con.execute(
        """SELECT d.*, sl.store_id AS source_store_id,
                  dl.store_id AS destination_store_id
           FROM inventory_deliveries d
           JOIN inventory_locations sl ON sl.id=d.source_location_id
           JOIN inventory_locations dl ON dl.id=d.destination_location_id
           WHERE d.id=?""", (delivery_id,)
    ).fetchone()
    if row is None:
        raise InventoryValidationError('delivery does not exist')
    source_ok = con.execute(
        'SELECT 1 FROM inventory_access WHERE auth0_sub=? AND store_id=?',
        (actor.get('sub'), row['source_store_id']),
    ).fetchone()
    destination_ok = con.execute(
        'SELECT 1 FROM inventory_access WHERE auth0_sub=? AND store_id=?',
        (actor.get('sub'), row['destination_store_id']),
    ).fetchone()
    if not (source_ok or destination_ok):
        raise PermissionError('Inventory access denied')
    result = {key: row[key] for key in (
        'id', 'kind', 'source_location_id', 'destination_location_id',
        'business_date', 'status', 'version', 'restock_session_id',
    )}
    result['lines'] = []
    for line in con.execute(
        'SELECT * FROM inventory_delivery_lines WHERE delivery_id=? ORDER BY line_no',
        (delivery_id,),
    ):
        item = dict(line)
        item['outstanding_transit'] = (
            line['dispatched_quantity'] - line['received_quantity']
            - line['returned_quantity'] - line['loss_quantity']
        )
        result['lines'].append(item)
    return result


def act_on_delivery(con, delivery_id, action, data, *, actor, request_key):
    if action not in {'dispatch', 'receive', 'return', 'resolve_loss', 'short_close'}:
        raise InventoryValidationError('delivery action is invalid')
    delivery = delivery_detail(con, delivery_id, actor=actor)
    source = _location(con, delivery['source_location_id'])
    destination = _location(con, delivery['destination_location_id'])
    required_store = destination['store_id'] if action == 'receive' else source['store_id']
    require_inventory_access(
        con, actor, (required_store,), 'manager' if action == 'resolve_loss' else 'staff'
    )
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    raw_lines = data.get('lines')
    if not isinstance(raw_lines, list) or not raw_lines:
        raise InventoryValidationError('lines must be a non-empty list')
    requested = []
    seen_lines = set()
    for raw in raw_lines:
        line_no = read_int(raw.get('line_no'), 'line_no', minimum=1)
        if line_no in seen_lines:
            raise InventoryValidationError('each delivery line may appear only once per action')
        seen_lines.add(line_no)
        requested.append({
            'line_no': line_no,
            'quantity': _quantity(raw.get('quantity'), 'quantity'),
            'disposition': raw.get('disposition', 'saleable'),
        })
    intent = {
        'delivery_id': delivery['id'], 'action': action,
        'expected_version': expected_version, 'lines': requested,
        'reason': (data.get('reason') or '').strip() or None,
    }
    _begin(con)
    try:
        current = delivery_detail(con, delivery_id, actor=actor)
        key, digest, prior = _replay(
            con, request_key, f'delivery_{action}', actor, intent
        )
        if prior:
            con.commit()
            return prior
        if current['version'] != expected_version or current['status'] in {'completed', 'cancelled'}:
            raise InventoryConflict('Delivery changed before action', 'delivery_state_conflict')
        rows = {row['line_no']: row for row in con.execute(
            'SELECT * FROM inventory_delivery_lines WHERE delivery_id=?',
            (delivery_id,),
        )}
        ledger_lines = []
        updates = []
        for item in requested:
            row = rows.get(item['line_no'])
            if row is None:
                raise InventoryValidationError('delivery line does not exist')
            quantity = item['quantity']
            outstanding = (row['dispatched_quantity'] - row['received_quantity']
                           - row['returned_quantity'] - row['loss_quantity'])
            if action == 'dispatch':
                if quantity > row['requested_quantity'] - row['dispatched_quantity']:
                    raise InventoryConflict('Dispatch exceeds requested quantity', 'delivery_quantity_conflict')
                source_balance = _balance(con, row['product_id'], source['id'], 'saleable')
                transit_balance = _balance(con, row['product_id'], source['id'], 'transit')
                ledger_lines.append({
                    'product_id': row['product_id'], 'unit': row['native_unit'],
                    'quantity': quantity, 'from_location_id': source['id'],
                    'from_disposition': 'saleable', 'to_location_id': source['id'],
                    'to_disposition': 'transit', 'expected_versions': {
                        'from': source_balance['version'], 'to': transit_balance['version'],
                    },
                    **({'open_set_id': row['open_set_id']}
                       if row['open_set_id'] is not None else {}),
                })
                updates.append(('dispatched_quantity', quantity, row['line_no']))
            elif action == 'receive':
                if quantity > outstanding:
                    raise InventoryConflict('Receipt exceeds outstanding transit', 'delivery_quantity_conflict')
                if item['disposition'] not in {'saleable', 'hold', 'damaged'}:
                    raise InventoryValidationError('destination disposition is invalid')
                if row['open_set_id'] is not None and item['disposition'] != 'saleable':
                    raise InventoryValidationError(
                        'opened-set delivery stock must be received as saleable'
                    )
                transit_balance = _balance(con, row['product_id'], source['id'], 'transit')
                target = _balance(con, row['product_id'], destination['id'], item['disposition'])
                ledger_lines.append({
                    'product_id': row['product_id'], 'unit': row['native_unit'],
                    'quantity': quantity, 'from_location_id': source['id'],
                    'from_disposition': 'transit', 'to_location_id': destination['id'],
                    'to_disposition': item['disposition'], 'expected_versions': {
                        'from': transit_balance['version'], 'to': target['version'],
                    },
                })
                updates.append(('received_quantity', quantity, row['line_no']))
            elif action == 'return':
                if quantity > outstanding:
                    raise InventoryConflict('Return exceeds outstanding transit', 'delivery_quantity_conflict')
                transit_balance = _balance(con, row['product_id'], source['id'], 'transit')
                target = _balance(con, row['product_id'], source['id'], 'saleable')
                ledger_lines.append({
                    'product_id': row['product_id'], 'unit': row['native_unit'],
                    'quantity': quantity, 'from_location_id': source['id'],
                    'from_disposition': 'transit', 'to_location_id': source['id'],
                    'to_disposition': 'saleable', 'expected_versions': {
                        'from': transit_balance['version'], 'to': target['version'],
                    },
                })
                updates.append(('returned_quantity', quantity, row['line_no']))
            elif action == 'resolve_loss':
                if not intent['reason'] or quantity > outstanding:
                    raise InventoryValidationError('Loss requires a reason and outstanding quantity')
                transit_balance = _balance(con, row['product_id'], source['id'], 'transit')
                ledger_lines.append({
                    'product_id': row['product_id'], 'unit': row['native_unit'],
                    'quantity': quantity, 'from_location_id': source['id'],
                    'from_disposition': 'transit',
                    'expected_versions': {'from': transit_balance['version']},
                })
                updates.append(('loss_quantity', quantity, row['line_no']))
            else:
                unfilled = row['requested_quantity'] - row['dispatched_quantity'] - row['short_quantity']
                if quantity > unfilled or outstanding:
                    raise InventoryConflict('Short close is not ready', 'delivery_quantity_conflict')
                if not intent['reason']:
                    raise InventoryValidationError('Short close requires a reason')
                updates.append(('short_quantity', quantity, row['line_no']))
        inventory_result = None
        if ledger_lines:
            kind = 'consume' if action == 'resolve_loss' else 'move'
            inventory_result = _post_inventory_in_transaction(con, {
                'kind': kind, 'business_date': current['business_date'],
                'source_type': 'delivery_event', 'source_id': key,
                'reason': intent['reason'] or f'Delivery {action}',
                'lines': ledger_lines,
            }, actor=actor, request_key=f'inventory:{key}',
                access_store_ids=(required_store,), allow_transit=True,
                allow_delivery_provenance=(action == 'dispatch'))
        if action in {'receive', 'return'}:
            restore_location = destination['id'] if action == 'receive' else source['id']
            for item in requested:
                _restore_open_set(con, rows[item['line_no']], restore_location,
                                  item['quantity'])
        event = con.execute(
            """INSERT INTO inventory_delivery_events
               (delivery_id, action, actor_sub, reason, inventory_document_id)
               VALUES (?, ?, ?, ?, ?)""",
            (delivery_id, action, actor['sub'], intent['reason'],
             inventory_result['document_id'] if inventory_result else None),
        ).lastrowid
        for index, (column, quantity, line_no) in enumerate(updates, 1):
            con.execute(
                f'UPDATE inventory_delivery_lines SET {column}={column}+? WHERE delivery_id=? AND line_no=?',
                (quantity, delivery_id, line_no),
            )
            con.execute(
                """INSERT INTO inventory_delivery_event_lines
                   (event_id, line_no, delivery_line_no, quantity, disposition)
                   VALUES (?, ?, ?, ?, ?)""",
                (event, index, line_no, quantity,
                 next(item['disposition'] for item in requested if item['line_no'] == line_no)),
            )
        remaining = con.execute(
            """SELECT COUNT(*) FROM inventory_delivery_lines
               WHERE delivery_id=? AND (
                 dispatched_quantity-received_quantity-returned_quantity-loss_quantity>0
                 OR dispatched_quantity+short_quantity<requested_quantity
               )""", (delivery_id,)
        ).fetchone()[0]
        status = 'completed' if remaining == 0 else 'active'
        con.execute(
            "UPDATE inventory_deliveries SET status=?, version=version+1 WHERE id=? AND version=?",
            (status, delivery_id, expected_version),
        )
        if current['restock_session_id'] is not None:
            con.execute(
                """UPDATE restock_sessions
                   SET status=?, completed_at=CASE WHEN ?='completed'
                       THEN datetime('now') ELSE completed_at END
                   WHERE id=? AND status IN ('submitted','picking')""",
                ('completed' if status == 'completed' else 'picking', status,
                 current['restock_session_id']),
            )
        response = {
            'id': delivery_id, 'version': expected_version + 1, 'status': status,
            'inventory_document_id': (
                inventory_result['document_id'] if inventory_result else None
            ),
        }
        _remember(con, key, f'delivery_{action}', delivery_id, actor, digest, response)
        con.commit()
        return response
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def create_count(con, data, *, actor, request_key):
    location = _location(con, data.get('location_id'))
    require_inventory_access(con, actor, (location['store_id'],), 'staff')
    business_date = read_date(data.get('business_date'), 'business_date')
    disposition = data.get('disposition', 'saleable')
    if disposition not in {'saleable', 'trade', 'display', 'hold', 'damaged'}:
        raise InventoryValidationError('disposition is invalid')
    raw_lines = data.get('lines')
    if not isinstance(raw_lines, list) or not raw_lines:
        raise InventoryValidationError('lines must be a non-empty list')
    lines = []
    seen_products = set()
    for raw in raw_lines:
        unit = raw.get('unit')
        product_id = _product(con, raw.get('product_id'), unit)
        if product_id in seen_products:
            raise InventoryValidationError('each product may appear only once in a count')
        seen_products.add(product_id)
        balance = _balance(con, product_id, location['id'], disposition)
        lines.append({
            'product_id': product_id, 'unit': unit,
            'observed_quantity': _quantity(
                raw.get('observed_quantity'), 'observed_quantity', allow_zero=True
            ),
            'expected_quantity': balance['quantity'],
            'captured_balance_version': balance['version'],
        })
    intent = {
        'location_id': location['id'], 'business_date': business_date,
        'disposition': disposition, 'lines': lines,
    }
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'count_create', actor, intent)
        if prior:
            con.commit()
            return prior
        count_id = con.execute(
            """INSERT INTO inventory_counts
               (store_id, location_id, disposition, business_date, created_by)
               VALUES (?, ?, ?, ?, ?)""",
            (location['store_id'], location['id'], disposition,
             business_date, actor['sub']),
        ).lastrowid
        for line_no, line in enumerate(lines, 1):
            con.execute(
                """INSERT INTO inventory_count_lines
                   (count_id, line_no, product_id, native_unit,
                    expected_quantity, observed_quantity, captured_balance_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (count_id, line_no, line['product_id'], line['unit'],
                 line['expected_quantity'], line['observed_quantity'],
                 line['captured_balance_version']),
            )
        response = {'id': count_id, 'version': 1, 'status': 'draft'}
        _remember(con, key, 'count_create', count_id, actor, digest, response)
        con.commit()
        return response
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def transition_count(con, count_id, action, data, *, actor, request_key):
    count_id = read_int(count_id, 'count_id', minimum=1)
    row = con.execute('SELECT * FROM inventory_counts WHERE id=?', (count_id,)).fetchone()
    if row is None:
        raise InventoryValidationError('count does not exist')
    minimum = 'manager' if action in {'approve', 'return'} else 'staff'
    require_inventory_access(con, actor, (row['store_id'],), minimum)
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    reason = (data.get('reason') or '').strip() or None
    intent = {
        'count_id': count_id, 'action': action,
        'expected_version': expected_version, 'reason': reason,
    }
    _begin(con)
    try:
        row = con.execute('SELECT * FROM inventory_counts WHERE id=?', (count_id,)).fetchone()
        key, digest, prior = _replay(con, request_key, f'count_{action}', actor, intent)
        if prior:
            con.commit()
            return prior
        expected_status = {'submit': 'draft', 'approve': 'submitted', 'return': 'submitted'}[action]
        if row['status'] != expected_status or row['version'] != expected_version:
            raise InventoryConflict('Count changed before action', 'count_state_conflict')
        inventory_result = None
        if action == 'approve':
            if not reason:
                raise InventoryValidationError('Count approval requires a reason')
            correction_lines = []
            for line in con.execute(
                'SELECT * FROM inventory_count_lines WHERE count_id=? ORDER BY line_no',
                (count_id,),
            ):
                current = _balance(
                    con, line['product_id'], row['location_id'], row['disposition']
                )
                if current['version'] != line['captured_balance_version']:
                    raise InventoryConflict('Counted balance changed; recount required', 'count_stale')
                difference = line['observed_quantity'] - line['expected_quantity']
                if difference:
                    if difference < 0 and row['disposition'] == 'saleable':
                        protected = con.execute(
                            """SELECT COALESCE(SUM(remaining_qty), 0)
                               FROM inventory_open_sets
                               WHERE random_product_id=? AND location_id=?
                                 AND remaining_qty>0""",
                            (line['product_id'], row['location_id']),
                        ).fetchone()[0]
                        mixed = max(0, current['quantity'] - protected)
                        if abs(difference) > mixed:
                            raise InventoryConflict(
                                'Count cannot reconcile protected opened-set units',
                                'provenance_reconciliation_required',
                            )
                    endpoint = 'to' if difference > 0 else 'from'
                    correction_lines.append({
                        'product_id': line['product_id'], 'unit': line['native_unit'],
                        'quantity': abs(difference),
                        f'{endpoint}_location_id': row['location_id'],
                        f'{endpoint}_disposition': row['disposition'],
                        'expected_versions': {endpoint: current['version']},
                    })
            if correction_lines:
                inventory_result = _post_inventory_in_transaction(con, {
                    'kind': 'correction', 'business_date': row['business_date'],
                    'source_type': 'inventory_count', 'source_id': str(count_id),
                    'reason': reason, 'lines': correction_lines,
                }, actor=actor, request_key=f'inventory:{key}')
        new_status = {'submit': 'submitted', 'approve': 'approved', 'return': 'returned'}[action]
        con.execute(
            """UPDATE inventory_counts SET status=?, version=version+1,
                      reviewed_by=?, review_reason=?, inventory_document_id=?
               WHERE id=? AND version=?""",
            (new_status, actor['sub'] if action != 'submit' else None,
             reason, inventory_result['document_id'] if inventory_result else None,
             count_id, expected_version),
        )
        response = {
            'id': count_id, 'version': expected_version + 1,
            'status': new_status,
            'inventory_document_id': (
                inventory_result['document_id'] if inventory_result else None
            ),
        }
        if action == 'return':
            recount_id = con.execute(
                """INSERT INTO inventory_counts
                   (store_id, location_id, disposition, business_date, created_by)
                   VALUES (?, ?, ?, ?, ?)""",
                (row['store_id'], row['location_id'], row['disposition'],
                 row['business_date'], actor['sub']),
            ).lastrowid
            for line_no, line in enumerate(con.execute(
                'SELECT * FROM inventory_count_lines WHERE count_id=? ORDER BY line_no',
                (count_id,),
            ), 1):
                balance = _balance(
                    con, line['product_id'], row['location_id'], row['disposition']
                )
                con.execute(
                    """INSERT INTO inventory_count_lines
                       (count_id, line_no, product_id, native_unit,
                        expected_quantity, observed_quantity, captured_balance_version)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (recount_id, line_no, line['product_id'], line['native_unit'],
                     balance['quantity'], line['observed_quantity'], balance['version']),
                )
            response['recount_id'] = recount_id
        _remember(con, key, f'count_{action}', count_id, actor, digest, response)
        con.commit()
        return response
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def save_target(con, data, *, actor, request_key):
    location = _location(con, data.get('location_id'))
    if location['code'] != 'floor':
        raise InventoryValidationError('targets require a floor location')
    require_inventory_access(con, actor, (location['store_id'],), 'manager')
    product_id = read_int(data.get('product_id'), 'product_id', minimum=1)
    product = con.execute(
        'SELECT stock_unit FROM products WHERE id=?', (product_id,)
    ).fetchone()
    if product is None:
        raise InventoryValidationError('product_id is invalid')
    minimum = _quantity(data.get('min_quantity'), 'min_quantity', allow_zero=True)
    maximum = _quantity(data.get('max_quantity'), 'max_quantity', allow_zero=True)
    if maximum < minimum:
        raise InventoryValidationError('max_quantity must be at least min_quantity')
    expected_version = read_int(
        data.get('expected_version'), 'expected_version', minimum=0
    )
    intent = {
        'location_id': location['id'], 'product_id': product_id,
        'min_quantity': minimum, 'max_quantity': maximum,
        'expected_version': expected_version,
    }
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'target_save', actor, intent)
        if prior:
            con.commit()
            return prior
        row = con.execute(
            """SELECT version FROM inventory_floor_targets
               WHERE location_id=? AND product_id=?""",
            (location['id'], product_id),
        ).fetchone()
        current_version = row['version'] if row else 0
        if current_version != expected_version:
            raise InventoryConflict('Target changed before save', 'target_state_conflict')
        if row:
            con.execute(
                """UPDATE inventory_floor_targets
                   SET min_quantity=?, max_quantity=?, version=version+1,
                       updated_by=? WHERE location_id=? AND product_id=?""",
                (minimum, maximum, actor['sub'], location['id'], product_id),
            )
        else:
            con.execute(
                """INSERT INTO inventory_floor_targets
                   (location_id, product_id, min_quantity, max_quantity, updated_by)
                   VALUES (?, ?, ?, ?, ?)""",
                (location['id'], product_id, minimum, maximum, actor['sub']),
            )
        response = {
            **intent, 'version': current_version + 1,
            'unit': product['stock_unit'],
        }
        _remember(con, key, 'target_save', product_id, actor, digest, response)
        con.commit()
        return response
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def restock_suggestions(con, location_id, *, actor):
    floor = _location(con, location_id)
    if floor['code'] != 'floor':
        raise InventoryValidationError('suggestions require a floor location')
    require_inventory_access(con, actor, (floor['store_id'],), 'staff')
    back = con.execute(
        """SELECT id FROM inventory_locations
           WHERE store_id=? AND code IN ('upstairs','warehouse') AND is_active=1
           ORDER BY CASE code WHEN 'upstairs' THEN 0 ELSE 1 END LIMIT 1""",
        (floor['store_id'],),
    ).fetchone()
    if back is None:
        raise InventoryConflict('Back-stock location is missing', 'inventory_store_missing')
    items = []
    targets = con.execute(
        """SELECT t.*, p.stock_unit, p.sku, p.jizhanming
           FROM inventory_floor_targets t JOIN products p ON p.id=t.product_id
           WHERE t.location_id=? ORDER BY p.jizhanming, p.id""", (floor['id'],)
    ).fetchall()
    for target in targets:
        floor_qty = _balance(
            con, target['product_id'], floor['id'], 'saleable'
        )['quantity']
        back_qty = _balance(
            con, target['product_id'], back['id'], 'saleable'
        )['quantity']
        protected = con.execute(
            """SELECT COALESCE(SUM(remaining_qty), 0) FROM inventory_open_sets
               WHERE random_product_id=? AND location_id=? AND remaining_qty>0""",
            (target['product_id'], back['id']),
        ).fetchone()[0]
        available_back = max(0, back_qty - protected)
        inbound = con.execute(
            """SELECT COALESCE(SUM(l.dispatched_quantity-l.received_quantity-
                                      l.returned_quantity-l.loss_quantity), 0)
               FROM inventory_delivery_lines l
               JOIN inventory_deliveries d ON d.id=l.delivery_id
               WHERE d.destination_location_id=? AND d.status='active'
                 AND l.product_id=?""",
            (floor['id'], target['product_id']),
        ).fetchone()[0]
        suggested = 0
        if floor_qty < target['min_quantity']:
            suggested = max(0, min(
                target['max_quantity'] - floor_qty - inbound,
                available_back,
            ))
        items.append({
            'product_id': target['product_id'], 'sku': target['sku'],
            'name': target['jizhanming'], 'unit': target['stock_unit'],
            'floor_quantity': floor_qty, 'available_back_quantity': available_back,
            'outstanding_inbound': inbound,
            'min_quantity': target['min_quantity'],
            'max_quantity': target['max_quantity'],
            'suggested_quantity': suggested,
        })
    return {'location_id': floor['id'], 'items': items}
