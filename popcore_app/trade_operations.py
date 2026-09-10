"""Inspected physical trade slots composed with authoritative inventory."""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from goods_operations import _begin, _remember, _replay
from inventory_commands import (
    InventoryConflict, InventoryValidationError,
    _post_inventory_in_transaction, require_inventory_access,
)
from validation import read_int


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise InventoryValidationError(f'{field} is required')
    return value.strip()


def _slot(con, slot_id):
    slot_id = read_int(slot_id, 'slot_id', minimum=1)
    row = con.execute(
        """SELECT ts.*, s.code AS store_code, ps.name AS series_name
           FROM trade_slots ts JOIN stores s ON s.id=ts.store_id
           JOIN product_series ps ON ps.id=ts.series_id
           WHERE ts.id=? AND ts.is_active=1""", (slot_id,),
    ).fetchone()
    if row is None:
        raise InventoryValidationError('slot_id is invalid')
    return dict(row)


def _product(con, product_id, form, series_id):
    product_id = read_int(product_id, 'product_id', minimum=1)
    row = con.execute(
        """SELECT id, series_id, stock_form, stock_unit, identity_status,
                  COALESCE(design_name, name_cn_en, jizhanming, sku) AS name
           FROM products WHERE id=?""", (product_id,),
    ).fetchone()
    if (row is None or row['identity_status'] != 'verified'
            or row['stock_form'] != form or row['series_id'] != series_id):
        raise InventoryConflict(
            'Product does not match the verified trade series',
            'trade_product_mismatch',
        )
    return dict(row)


def _result_slot(con, slot_id):
    row = _slot(con, slot_id)
    return {
        'slot_id': row['id'], 'store_id': row['store_id'],
        'store_code': row['store_code'], 'series_id': row['series_id'],
        'series_name': row['series_name'], 'location_id': row['location_id'],
        'occupant_unit_id': row['occupant_unit_id'], 'version': row['version'],
    }


def create_slot(con, data, *, actor, request_key):
    if not isinstance(data, dict):
        raise InventoryValidationError('payload must be an object')
    store_id = read_int(data.get('store_id'), 'store_id', minimum=1)
    series_id = read_int(data.get('series_id'), 'series_id', minimum=1)
    location_id = read_int(data.get('location_id'), 'location_id', minimum=1)
    intent = {'store_id': store_id, 'series_id': series_id, 'location_id': location_id}
    _begin(con)
    try:
        require_inventory_access(con, actor, (store_id,), 'manager')
        request_key, digest, replay = _replay(
            con, request_key, 'trade_slot_create', actor, intent,
        )
        if replay is not None:
            con.commit()
            return replay
        location = con.execute(
            """SELECT code FROM inventory_locations
               WHERE id=? AND store_id=? AND is_active=1""",
            (location_id, store_id),
        ).fetchone()
        if location is None or location['code'] != 'floor':
            raise InventoryValidationError('Trade slot requires the store floor location')
        if con.execute('SELECT 1 FROM product_series WHERE id=?', (series_id,)).fetchone() is None:
            raise InventoryValidationError('series_id is invalid')
        try:
            slot_id = con.execute(
                """INSERT INTO trade_slots
                   (store_id, series_id, location_id, created_by)
                   VALUES (?, ?, ?, ?)""",
                (store_id, series_id, location_id, actor['sub']),
            ).lastrowid
        except sqlite3.IntegrityError as exc:
            raise InventoryConflict(
                'A trade slot already exists for this series and store',
                'trade_slot_exists',
            ) from exc
        result = _result_slot(con, slot_id)
        _remember(con, request_key, 'trade_slot_create', slot_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise


def open_slot(con, slot_id, data, *, actor, request_key):
    if not isinstance(data, dict):
        raise InventoryValidationError('payload must be an object')
    slot_id = read_int(slot_id, 'slot_id', minimum=1)
    intent = {'slot_id': slot_id, **data}
    _begin(con)
    try:
        slot = _slot(con, slot_id)
        require_inventory_access(con, actor, (slot['store_id'],), 'staff')
        request_key, digest, replay = _replay(
            con, request_key, 'trade_slot_open', actor, intent,
        )
        if replay is not None:
            con.commit()
            return replay
        expected = read_int(data.get('expected_version'), 'expected_version', minimum=1)
        if expected != slot['version']:
            raise InventoryConflict('Trade slot version changed', 'trade_slot_stale')
        if slot['occupant_unit_id'] is not None:
            raise InventoryConflict('Trade slot is occupied', 'trade_slot_occupied')
        random_product = _product(
            con, data.get('random_product_id'), 'random_box', slot['series_id'],
        )
        design_product = _product(
            con, data.get('design_product_id'), 'confirmed_design', slot['series_id'],
        )
        disclosure = _text(data.get('condition_disclosure'), 'condition_disclosure')
        random_version = read_int(
            data.get('random_balance_version'), 'random_balance_version', minimum=1,
        )
        trade_version = read_int(
            data.get('trade_balance_version'), 'trade_balance_version', minimum=0,
        )
        common = {
            'business_date': data.get('business_date') or datetime.now(
                ZoneInfo('America/Toronto')
            ).date().isoformat(),
        }
        consumed = _post_inventory_in_transaction(con, {
            **common, 'kind': 'consume', 'source_type': 'trade_opening',
            'source_id': f'{slot_id}:consume:{request_key}',
            'reason': 'Open random box for trade slot', 'lines': [{
                'product_id': random_product['id'], 'quantity': 1, 'unit': 'box',
                'from_location_id': slot['location_id'],
                'from_disposition': 'saleable',
                'expected_versions': {'from': random_version},
            }],
        }, actor=actor, request_key=f'{request_key}:consume',
            access_store_ids=(slot['store_id'],))
        received = _post_inventory_in_transaction(con, {
            **common, 'kind': 'receipt', 'source_type': 'trade_opening',
            'source_id': f'{slot_id}:receipt:{request_key}',
            'reason': 'Place inspected design in trade slot', 'lines': [{
                'product_id': design_product['id'], 'quantity': 1, 'unit': 'piece',
                'to_location_id': slot['location_id'], 'to_disposition': 'trade',
                'expected_versions': {'to': trade_version},
            }],
        }, actor=actor, request_key=f'{request_key}:receipt',
            access_store_ids=(slot['store_id'],), allow_trade=True)
        unit_id = con.execute(
            """INSERT INTO trade_units
               (design_product_id, series_id, origin, eligibility, location_id,
                custody, condition_disclosure, source_document_id, created_by)
               VALUES (?, ?, 'random_opening', 'eligible', ?, 'slot', ?, ?, ?)""",
            (design_product['id'], slot['series_id'], slot['location_id'],
             disclosure, received['document_id'], actor['sub']),
        ).lastrowid
        changed = con.execute(
            """UPDATE trade_slots SET occupant_unit_id=?, version=version+1
               WHERE id=? AND version=? AND occupant_unit_id IS NULL""",
            (unit_id, slot_id, expected),
        ).rowcount
        if changed != 1:
            raise InventoryConflict('Trade slot version changed', 'trade_slot_stale')
        event_id = con.execute(
            """INSERT INTO trade_events
               (slot_id, event_type, slot_version_before, slot_version_after,
                incoming_unit_id, consume_document_id, receipt_document_id,
                actor_sub, reason, business_date)
               VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (slot_id, expected, expected + 1, unit_id, consumed['document_id'],
             received['document_id'], actor['sub'], disclosure,
             common['business_date']),
        ).lastrowid
        result = {
            **_result_slot(con, slot_id), 'event_id': event_id, 'unit_id': unit_id,
            'inventory_document_ids': [consumed['document_id'], received['document_id']],
        }
        _remember(con, request_key, 'trade_slot_open', slot_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise


def record_inspection(con, slot_id, data, *, actor, request_key):
    if not isinstance(data, dict):
        raise InventoryValidationError('payload must be an object')
    slot_id = read_int(slot_id, 'slot_id', minimum=1)
    intent = {'slot_id': slot_id, **data}
    _begin(con)
    try:
        slot = _slot(con, slot_id)
        require_inventory_access(con, actor, (slot['store_id'],), 'staff')
        request_key, digest, replay = _replay(
            con, request_key, 'trade_inspection', actor, intent,
        )
        if replay is not None:
            con.commit()
            return replay
        expected = read_int(data.get('expected_version'), 'expected_version', minimum=1)
        if expected != slot['version']:
            raise InventoryConflict('Trade slot version changed', 'trade_slot_stale')
        origin = data.get('origin')
        proof_kind = _text(data.get('proof_kind'), 'proof_kind')
        if origin == 'confirmed_purchase' or proof_kind == 'original_receipt':
            raise InventoryConflict(
                'Confirmed-design purchases are not eligible for trade',
                'trade_ineligible',
            )
        if origin != 'inspected_trade':
            raise InventoryValidationError('origin must be inspected_trade')
        design = _product(
            con, data.get('design_product_id'), 'confirmed_design', slot['series_id'],
        )
        if proof_kind not in {'legacy_sticker', 'prior_trade'}:
            raise InventoryValidationError('proof_kind is invalid')
        proof_reference = _text(data.get('proof_reference'), 'proof_reference')
        if not isinstance(data.get('box_checked'), bool):
            raise InventoryValidationError('box_checked must be true or false')
        if not isinstance(data.get('accessories_checked'), bool):
            raise InventoryValidationError('accessories_checked must be true or false')
        condition = _text(data.get('condition'), 'condition')
        disclosure = _text(data.get('disclosure'), 'disclosure')
        decision = data.get('decision', 'accepted')
        if decision not in {'accepted', 'rejected'}:
            raise InventoryValidationError('decision must be accepted or rejected')
        if decision == 'accepted' and not (
                data['box_checked'] and data['accessories_checked']):
            raise InventoryConflict(
                'Accepted trades require the box and accessories checks',
                'trade_inspection_rejected',
            )
        eligibility = 'eligible' if decision == 'accepted' else 'ineligible'
        if proof_kind == 'prior_trade':
            unit_id = read_int(data.get('incoming_unit_id'), 'incoming_unit_id', minimum=1)
            prior = con.execute(
                """SELECT id FROM trade_units WHERE id=? AND design_product_id=?
                   AND series_id=? AND custody='customer' AND eligibility='eligible'""",
                (unit_id, design['id'], slot['series_id']),
            ).fetchone()
            if prior is None:
                raise InventoryConflict('Prior trade unit is not eligible', 'trade_ineligible')
        else:
            unit_id = con.execute(
                """INSERT INTO trade_units
                   (design_product_id, series_id, origin, eligibility, custody,
                    condition_disclosure, created_by)
                   VALUES (?, ?, 'inspected_trade', ?, 'customer', ?, ?)""",
                (design['id'], slot['series_id'], eligibility, disclosure, actor['sub']),
            ).lastrowid
        inspection_id = con.execute(
            """INSERT INTO trade_inspections
               (slot_id, incoming_unit_id, outgoing_unit_id, expected_slot_version,
                proof_kind, proof_reference, check_notes, box_checked,
                accessories_checked, condition_observed, disclosure, decision,
                actor_sub)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (slot_id, unit_id, slot['occupant_unit_id'], expected, proof_kind,
             proof_reference, data.get('check_notes'), int(data['box_checked']),
             int(data['accessories_checked']), condition, disclosure, decision,
             actor['sub']),
        ).lastrowid
        result = {
            'slot_id': slot_id, 'version': slot['version'],
            'inspection_id': inspection_id, 'incoming_unit_id': unit_id,
            'decision': decision,
        }
        _remember(con, request_key, 'trade_inspection', inspection_id,
                  actor, digest, result)
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise


def swap_slot(con, slot_id, data, *, actor, request_key):
    if not isinstance(data, dict):
        raise InventoryValidationError('payload must be an object')
    slot_id = read_int(slot_id, 'slot_id', minimum=1)
    intent = {'slot_id': slot_id, **data}
    _begin(con)
    try:
        slot = _slot(con, slot_id)
        require_inventory_access(con, actor, (slot['store_id'],), 'staff')
        request_key, digest, replay = _replay(
            con, request_key, 'trade_slot_swap', actor, intent,
        )
        if replay is not None:
            con.commit()
            return replay
        expected = read_int(data.get('expected_version'), 'expected_version', minimum=1)
        if expected != slot['version'] or slot['occupant_unit_id'] is None:
            raise InventoryConflict('Trade slot version changed', 'trade_slot_stale')
        inspection_id = read_int(data.get('inspection_id'), 'inspection_id', minimum=1)
        inspection = con.execute(
            """SELECT i.*, u.design_product_id, u.series_id, u.eligibility,
                      u.custody, u.version AS unit_version
               FROM trade_inspections i JOIN trade_units u ON u.id=i.incoming_unit_id
               WHERE i.id=? AND i.slot_id=?""", (inspection_id, slot_id),
        ).fetchone()
        if (inspection is None or inspection['decision'] != 'accepted'
                or inspection['expected_slot_version'] != expected
                or inspection['outgoing_unit_id'] != slot['occupant_unit_id']
                or inspection['series_id'] != slot['series_id']
                or inspection['eligibility'] != 'eligible'
                or inspection['custody'] != 'customer'):
            raise InventoryConflict(
                'Inspection no longer authorizes this swap', 'trade_inspection_stale'
            )
        outgoing = con.execute(
            """SELECT * FROM trade_units
               WHERE id=? AND custody='slot' AND location_id=?""",
            (slot['occupant_unit_id'], slot['location_id']),
        ).fetchone()
        if outgoing is None:
            raise InventoryConflict('Trade occupant changed', 'trade_slot_stale')
        outgoing_version = read_int(
            data.get('outgoing_balance_version'), 'outgoing_balance_version', minimum=1,
        )
        incoming_version = (
            outgoing_version + 1
            if outgoing['design_product_id'] == inspection['design_product_id']
            else read_int(data.get('incoming_balance_version'),
                          'incoming_balance_version', minimum=0)
        )
        business_date = data.get('business_date') or datetime.now(
            ZoneInfo('America/Toronto')
        ).date().isoformat()
        consumed = _post_inventory_in_transaction(con, {
            'kind': 'consume', 'business_date': business_date,
            'source_type': 'trade_swap', 'source_id': f'{slot_id}:out:{request_key}',
            'reason': 'Trade slot outgoing unit', 'lines': [{
                'product_id': outgoing['design_product_id'], 'quantity': 1,
                'unit': 'piece', 'from_location_id': slot['location_id'],
                'from_disposition': 'trade',
                'expected_versions': {'from': outgoing_version},
            }],
        }, actor=actor, request_key=f'{request_key}:consume',
            access_store_ids=(slot['store_id'],), allow_trade=True)
        received = _post_inventory_in_transaction(con, {
            'kind': 'receipt', 'business_date': business_date,
            'source_type': 'trade_swap', 'source_id': f'{slot_id}:in:{request_key}',
            'reason': 'Trade slot incoming unit', 'lines': [{
                'product_id': inspection['design_product_id'], 'quantity': 1,
                'unit': 'piece', 'to_location_id': slot['location_id'],
                'to_disposition': 'trade',
                'expected_versions': {'to': incoming_version},
            }],
        }, actor=actor, request_key=f'{request_key}:receipt',
            access_store_ids=(slot['store_id'],), allow_trade=True)
        outgoing_changed = con.execute(
            """UPDATE trade_units SET custody='customer', location_id=NULL,
                      version=version+1 WHERE id=? AND custody='slot'""",
            (outgoing['id'],),
        ).rowcount
        incoming_changed = con.execute(
            """UPDATE trade_units SET custody='slot', location_id=?, version=version+1
               WHERE id=? AND custody='customer' AND eligibility='eligible'
                 AND version=?""",
            (slot['location_id'], inspection['incoming_unit_id'],
             inspection['unit_version']),
        ).rowcount
        slot_changed = con.execute(
            """UPDATE trade_slots SET occupant_unit_id=?, version=version+1
               WHERE id=? AND version=? AND occupant_unit_id=?""",
            (inspection['incoming_unit_id'], slot_id, expected, outgoing['id']),
        ).rowcount
        if (outgoing_changed, incoming_changed, slot_changed) != (1, 1, 1):
            raise InventoryConflict('Trade slot version changed', 'trade_slot_stale')
        event_id = con.execute(
            """INSERT INTO trade_events
               (slot_id, event_type, slot_version_before, slot_version_after,
                incoming_unit_id, outgoing_unit_id, inspection_id,
                consume_document_id, receipt_document_id, actor_sub, reason,
                business_date)
               VALUES (?, 'swap', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (slot_id, expected, expected + 1, inspection['incoming_unit_id'],
             outgoing['id'], inspection_id, consumed['document_id'],
             received['document_id'], actor['sub'], 'Same-series trade', business_date),
        ).lastrowid
        result = {
            **_result_slot(con, slot_id), 'event_id': event_id,
            'incoming_unit_id': inspection['incoming_unit_id'],
            'outgoing_unit_id': outgoing['id'],
            'inventory_document_ids': [consumed['document_id'], received['document_id']],
        }
        _remember(con, request_key, 'trade_slot_swap', slot_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise


def sell_slot(con, slot_id, data, *, actor, request_key):
    if not isinstance(data, dict):
        raise InventoryValidationError('payload must be an object')
    from sales_operations import _post_sale_in_transaction, _sale_row

    slot_id = read_int(slot_id, 'slot_id', minimum=1)
    intent = {'slot_id': slot_id, **data}
    _begin(con)
    try:
        slot = _slot(con, slot_id)
        require_inventory_access(con, actor, (slot['store_id'],), 'staff')
        request_key, digest, replay = _replay(
            con, request_key, 'trade_slot_sale', actor, intent,
        )
        if replay is not None:
            con.commit()
            return replay
        expected = read_int(data.get('expected_version'), 'expected_version', minimum=1)
        if expected != slot['version'] or slot['occupant_unit_id'] is None:
            raise InventoryConflict('Trade slot version changed', 'trade_slot_stale')
        unit = con.execute(
            """SELECT * FROM trade_units
               WHERE id=? AND custody='slot' AND location_id=?""",
            (slot['occupant_unit_id'], slot['location_id']),
        ).fetchone()
        if unit is None:
            raise InventoryConflict('Trade occupant changed', 'trade_slot_stale')
        sale_id = read_int(data.get('sale_id'), 'sale_id', minimum=1)
        sale_version = read_int(
            data.get('sale_expected_version'), 'sale_expected_version', minimum=1,
        )
        sale = _sale_row(con, sale_id)
        lines = con.execute(
            'SELECT * FROM sale_lines WHERE sale_id=? ORDER BY line_no', (sale_id,),
        ).fetchall()
        if (sale['store_id'] != slot['store_id'] or len(lines) != 1
                or lines[0]['product_id'] != unit['design_product_id']
                or lines[0]['quantity'] != 1 or lines[0]['native_unit'] != 'piece'):
            raise InventoryConflict(
                'Sale must contain exactly the current trade unit', 'trade_sale_mismatch'
            )
        balance_version = read_int(
            data.get('trade_balance_version'), 'trade_balance_version', minimum=1,
        )
        con.execute(
            """UPDATE sale_lines SET condition_disclosure=?
               WHERE sale_id=? AND line_no=? AND condition_disclosure IS NULL""",
            (unit['condition_disclosure'], sale_id, lines[0]['line_no']),
        )
        sale_result = _post_sale_in_transaction(
            con, sale, sale_version, actor, request_key,
            allocation_lines=[{
                'product_id': unit['design_product_id'], 'quantity': 1,
                'unit': 'piece', 'from_location_id': slot['location_id'],
                'from_disposition': 'trade',
                'expected_versions': {'from': balance_version},
            }], allow_trade=True,
        )
        slot_changed = con.execute(
            """UPDATE trade_slots SET occupant_unit_id=NULL, version=version+1
               WHERE id=? AND version=? AND occupant_unit_id=?""",
            (slot_id, expected, unit['id']),
        ).rowcount
        unit_changed = con.execute(
            """UPDATE trade_units SET custody='sold', location_id=NULL,
                      eligibility='ineligible', version=version+1
               WHERE id=? AND custody='slot' AND version=?""",
            (unit['id'], unit['version']),
        ).rowcount
        if (slot_changed, unit_changed) != (1, 1):
            raise InventoryConflict('Trade slot version changed', 'trade_slot_stale')
        event_id = con.execute(
            """INSERT INTO trade_events
               (slot_id, event_type, slot_version_before, slot_version_after,
                outgoing_unit_id, consume_document_id, sale_id, actor_sub,
                reason, business_date)
               VALUES (?, 'sale', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (slot_id, expected, expected + 1, unit['id'],
             sale_result['inventory_document_id'], sale_id, actor['sub'],
             unit['condition_disclosure'], sale['business_date']),
        ).lastrowid
        result = {
            **sale_result, **_result_slot(con, slot_id), 'event_id': event_id,
            'outgoing_unit_id': unit['id'], 'replacement_needed': True,
        }
        _remember(con, request_key, 'trade_slot_sale', slot_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise


def _case(con, case_id):
    case_id = read_int(case_id, 'case_id', minimum=1)
    row = con.execute('SELECT * FROM condition_cases WHERE id=?', (case_id,)).fetchone()
    if row is None:
        raise InventoryConflict('Condition case was not found', 'condition_case_not_found')
    return dict(row)


def require_case_access(con, case, actor, minimum='staff'):
    from auth import ROLE_CLAIM, ROLE_HIERARCHY

    require_inventory_access(con, actor, (case['store_id'],), minimum)
    if (ROLE_HIERARCHY.get(actor.get(ROLE_CLAIM, 'viewer'), 0)
            < ROLE_HIERARCHY['manager']
            and case['created_by'] != actor.get('sub')):
        raise PermissionError('Condition evidence access denied')


def case_detail(con, case_id, *, actor):
    case = _case(con, case_id)
    require_case_access(con, case, actor)
    case['case_id'] = case.pop('id')
    case['events'] = [dict(row) for row in con.execute(
        'SELECT * FROM condition_events WHERE case_id=? ORDER BY id', (case_id,),
    )]
    case['evidence'] = [
        {'evidence_id': row['id'], 'mime_type': row['mime_type'],
         'byte_size': row['byte_size'], 'created_at': row['created_at']}
        for row in con.execute(
            'SELECT * FROM condition_evidence WHERE case_id=? ORDER BY id', (case_id,),
        )
    ]
    return case


def create_condition_case(con, data, *, actor, request_key):
    if not isinstance(data, dict):
        raise InventoryValidationError('payload must be an object')
    unit_id = data.get('unit_id')
    sale_id = data.get('sale_id')
    sale_line_no = data.get('sale_line_no')
    if unit_id is not None and (sale_id is not None or sale_line_no is not None):
        raise InventoryValidationError('Provide one condition source only')
    if unit_id is not None:
        unit_id = read_int(unit_id, 'unit_id', minimum=1)
        row = con.execute(
            """SELECT s.store_id FROM trade_events e
               JOIN trade_slots s ON s.id=e.slot_id
               WHERE e.incoming_unit_id=? OR e.outgoing_unit_id=?
               ORDER BY e.id DESC LIMIT 1""", (unit_id, unit_id),
        ).fetchone()
    elif sale_id is not None and sale_line_no is not None:
        sale_id = read_int(sale_id, 'sale_id', minimum=1)
        sale_line_no = read_int(sale_line_no, 'sale_line_no', minimum=1)
        row = con.execute(
            """SELECT s.store_id FROM sale_lines l
               JOIN sale_documents s ON s.id=l.sale_id
               WHERE l.sale_id=? AND l.line_no=?""", (sale_id, sale_line_no),
        ).fetchone()
    else:
        raise InventoryValidationError('unit_id or sale line is required')
    if row is None:
        raise InventoryValidationError('Condition source was not found')
    store_id = row['store_id']
    observed = _text(data.get('observed_condition'), 'observed_condition')
    disclosed = data.get('disclosed_condition')
    if disclosed is not None:
        disclosed = _text(disclosed, 'disclosed_condition')
    reason = _text(data.get('reason'), 'reason')
    intent = {
        'unit_id': unit_id, 'sale_id': sale_id, 'sale_line_no': sale_line_no,
        'observed_condition': observed, 'disclosed_condition': disclosed,
        'reason': reason,
    }
    _begin(con)
    try:
        require_inventory_access(con, actor, (store_id,), 'staff')
        request_key, digest, replay = _replay(
            con, request_key, 'condition_case_create', actor, intent,
        )
        if replay is not None:
            con.commit()
            return replay
        case_id = con.execute(
            """INSERT INTO condition_cases
               (store_id, unit_id, sale_id, sale_line_no, observed_condition,
                disclosed_condition, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (store_id, unit_id, sale_id, sale_line_no, observed, disclosed,
             actor['sub']),
        ).lastrowid
        event_id = con.execute(
            """INSERT INTO condition_events
               (case_id, event_type, reason, actor_sub)
               VALUES (?, 'note', ?, ?)""",
            (case_id, reason, actor['sub']),
        ).lastrowid
        result = {'case_id': case_id, 'version': 1, 'status': 'open',
                  'event_id': event_id}
        _remember(con, request_key, 'condition_case_create', case_id,
                  actor, digest, result)
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise


def decide_condition_case(con, case_id, data, *, actor, request_key):
    if not isinstance(data, dict):
        raise InventoryValidationError('payload must be an object')
    case_id = read_int(case_id, 'case_id', minimum=1)
    expected = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    disposition = data.get('disposition')
    if disposition not in {'declined', 'referred_for_separate_review', 'other'}:
        raise InventoryValidationError('disposition is invalid')
    reason = _text(data.get('reason'), 'reason')
    intent = {'case_id': case_id, 'expected_version': expected,
              'disposition': disposition, 'reason': reason}
    _begin(con)
    try:
        case = _case(con, case_id)
        require_case_access(con, case, actor, 'manager')
        request_key, digest, replay = _replay(
            con, request_key, 'condition_case_decision', actor, intent,
        )
        if replay is not None:
            con.commit()
            return replay
        changed = con.execute(
            """UPDATE condition_cases SET status='resolved', version=version+1
               WHERE id=? AND status='open' AND version=?""",
            (case_id, expected),
        ).rowcount
        if changed != 1:
            raise InventoryConflict('Condition case changed', 'condition_case_stale')
        event_id = con.execute(
            """INSERT INTO condition_events
               (case_id, event_type, disposition, reason, actor_sub)
               VALUES (?, 'decision', ?, ?, ?)""",
            (case_id, disposition, reason, actor['sub']),
        ).lastrowid
        result = {'case_id': case_id, 'version': expected + 1,
                  'status': 'resolved', 'event_id': event_id,
                  'disposition': disposition}
        _remember(con, request_key, 'condition_case_decision', case_id,
                  actor, digest, result)
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise


def list_slots(con, *, actor, store_id=None):
    if store_id is not None:
        store_ids = (read_int(store_id, 'store_id', minimum=1),)
    else:
        store_ids = tuple(row['store_id'] for row in con.execute(
            'SELECT store_id FROM inventory_access WHERE auth0_sub=? ORDER BY store_id',
            (actor.get('sub'),),
        ))
    if not store_ids:
        return []
    require_inventory_access(con, actor, store_ids, 'staff')
    marks = ','.join('?' for _ in store_ids)
    return [dict(row) for row in con.execute(
        f"""SELECT id AS slot_id, store_id, series_id, location_id,
                   occupant_unit_id, version
            FROM trade_slots WHERE is_active=1 AND store_id IN ({marks})
            ORDER BY store_id, series_id""", store_ids,
    )]


def trade_setup(con, *, actor, store_id):
    store_id = read_int(store_id, 'store_id', minimum=1)
    require_inventory_access(con, actor, (store_id,), 'staff')
    floor = con.execute(
        """SELECT id, name FROM inventory_locations
           WHERE store_id=? AND code='floor' AND is_active=1""", (store_id,),
    ).fetchone()
    if floor is None:
        raise InventoryValidationError('Store has no active floor location')
    series = [dict(row) for row in con.execute(
        """SELECT ps.id AS series_id, ps.name
           FROM product_series ps
           WHERE EXISTS (SELECT 1 FROM products p WHERE p.series_id=ps.id
                         AND p.stock_form='random_box' AND p.identity_status='verified')
             AND EXISTS (SELECT 1 FROM products p WHERE p.series_id=ps.id
                         AND p.stock_form='confirmed_design' AND p.identity_status='verified')
           ORDER BY ps.name, ps.id""",
    )]
    return {'store_id': store_id, 'floor_location_id': floor['id'],
            'floor_name': floor['name'], 'series': series}


def slot_detail(con, slot_id, *, actor):
    slot = _slot(con, slot_id)
    require_inventory_access(con, actor, (slot['store_id'],), 'staff')
    result = _result_slot(con, slot_id)
    occupant = None
    if slot['occupant_unit_id'] is not None:
        row = con.execute(
            """SELECT u.id AS unit_id, u.design_product_id, u.origin,
                      u.eligibility, u.custody, u.condition_disclosure,
                      u.version, COALESCE(p.design_name, p.name_cn_en,
                      p.jizhanming, p.sku) AS design_name
               FROM trade_units u JOIN products p ON p.id=u.design_product_id
               WHERE u.id=?""", (slot['occupant_unit_id'],),
        ).fetchone()
        occupant = dict(row) if row else None
    result['occupant'] = occupant
    result['products'] = [dict(row) for row in con.execute(
        """SELECT p.id AS product_id,
                  COALESCE(p.design_name, p.name_cn_en, p.jizhanming, p.sku) AS name,
                  p.stock_form, p.stock_unit,
                  COALESCE(b.quantity, 0) AS quantity,
                  COALESCE(b.version, 0) AS balance_version
           FROM products p
           LEFT JOIN inventory_balances b
             ON b.product_id=p.id AND b.location_id=?
            AND b.disposition=CASE WHEN p.stock_form='random_box'
                                   THEN 'saleable' ELSE 'trade' END
           WHERE p.series_id=? AND p.identity_status='verified'
             AND p.stock_form IN ('random_box','confirmed_design')
           ORDER BY p.stock_form DESC, name, p.id""",
        (slot['location_id'], slot['series_id']),
    )]
    return result
