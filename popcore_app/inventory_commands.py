"""Atomic inventory posting and explicit operations access."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from auth import ROLE_CLAIM, ROLE_HIERARCHY
from catalog_identity import receipt_units
from validation import SQLITE_INTEGER_MAX, read_date, read_int


INVENTORY_DISPOSITIONS = {
    'saleable', 'trade', 'display', 'hold', 'damaged', 'transit'
}
INVENTORY_KINDS = {
    'opening', 'receipt', 'move', 'consume', 'open_set', 'correction',
    'restock_complete',
}
INVENTORY_BUSY_TIMEOUT_MS = 5000


class InventoryError(Exception):
    def __init__(self, message, code, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


class InventoryValidationError(InventoryError):
    def __init__(self, message, code='invalid_input'):
        super().__init__(message, code, 400)


class InventoryConflict(InventoryError):
    def __init__(self, message, code):
        super().__init__(message, code, 409)


class InventoryBusy(InventoryError):
    def __init__(self):
        super().__init__(
            'Inventory is busy; retry the same request key',
            'inventory_busy', 503,
        )


def require_inventory_access(con, jwt_payload, store_ids, minimum_role):
    if minimum_role not in ROLE_HIERARCHY:
        raise ValueError('Unknown minimum inventory role')
    if not isinstance(jwt_payload, dict):
        raise PermissionError('Inventory access denied')
    role = jwt_payload.get(ROLE_CLAIM)
    if role not in ROLE_HIERARCHY:
        raise PermissionError('Inventory access denied')
    if ROLE_HIERARCHY[role] < ROLE_HIERARCHY[minimum_role]:
        raise PermissionError('Inventory access denied')
    subject = jwt_payload.get('sub')
    if not isinstance(subject, str) or not subject:
        raise PermissionError('Inventory access denied')

    unique_store_ids = []
    for value in store_ids:
        store_id = read_int(value, 'store_id', minimum=1)
        if store_id not in unique_store_ids:
            unique_store_ids.append(store_id)
    if not unique_store_ids:
        raise PermissionError('Inventory access denied')

    for store_id in unique_store_ids:
        has_location = con.execute(
            """SELECT 1 FROM inventory_locations l
               JOIN stores s ON s.id=l.store_id
               WHERE l.store_id=? AND l.is_active=1 AND s.is_active=1
               LIMIT 1""",
            (store_id,),
        ).fetchone()
        granted = con.execute(
            'SELECT 1 FROM inventory_access WHERE auth0_sub=? AND store_id=?',
            (subject, store_id),
        ).fetchone()
        if not has_location or not granted:
            raise PermissionError('Inventory access denied')


def _optional_text(value, field):
    if value is None:
        return None
    if not isinstance(value, str):
        raise InventoryValidationError(f'{field} must be text')
    value = value.strip()
    return value or None


def _normalize_endpoint(line, prefix, required):
    location_key = f'{prefix}_location_id'
    disposition_key = f'{prefix}_disposition'
    location = line.get(location_key)
    disposition = line.get(disposition_key)
    if location is None and disposition is None and not required:
        return None, None
    if location is None or disposition is None:
        raise InventoryValidationError(
            f'{location_key} and {disposition_key} must be supplied together'
        )
    location = read_int(location, location_key, minimum=1)
    if disposition not in INVENTORY_DISPOSITIONS:
        raise InventoryValidationError(f'{disposition_key} is invalid')
    return location, disposition


def _normalize_payload(payload, *, allow_delivery_provenance=False):
    if not isinstance(payload, dict):
        raise InventoryValidationError('payload must be an object')
    kind = payload.get('kind')
    if kind not in INVENTORY_KINDS:
        raise InventoryValidationError('kind is invalid')
    business_date = read_date(payload.get('business_date'), 'business_date')
    reason = _optional_text(payload.get('reason'), 'reason')
    source_type = _optional_text(payload.get('source_type'), 'source_type')
    source_id = _optional_text(payload.get('source_id'), 'source_id')
    if (source_type is None) != (source_id is None):
        raise InventoryValidationError(
            'source_type and source_id must be supplied together'
        )
    correction_of = payload.get('correction_of')
    if correction_of is not None:
        correction_of = read_int(correction_of, 'correction_of', minimum=1)
    if kind == 'correction' and not reason:
        raise InventoryValidationError('correction requires a reason')
    lines = payload.get('lines')
    if not isinstance(lines, list) or not lines:
        raise InventoryValidationError('lines must be a non-empty list')

    normalized_lines = []
    for index, raw in enumerate(lines, start=1):
        if not isinstance(raw, dict):
            raise InventoryValidationError(f'line {index} must be an object')
        product_id = read_int(raw.get('product_id'), 'product_id', minimum=1)
        quantity = read_int(raw.get('quantity'), 'quantity', minimum=1)
        unit = raw.get('unit')
        if unit not in {'box', 'set', 'piece'}:
            raise InventoryValidationError('unit is invalid')
        needs_from = kind in {'move', 'consume', 'open_set', 'restock_complete'}
        needs_to = kind in {'opening', 'receipt', 'move', 'open_set', 'restock_complete'}
        from_location, from_disposition = _normalize_endpoint(
            raw, 'from', needs_from
        )
        to_location, to_disposition = _normalize_endpoint(raw, 'to', needs_to)
        if kind in {'opening', 'receipt'} and from_location is not None:
            raise InventoryValidationError(f'{kind} cannot have a source balance')
        if kind == 'consume' and to_location is not None:
            raise InventoryValidationError('consume cannot have a destination balance')
        if kind == 'correction' and from_location is None and to_location is None:
            raise InventoryValidationError(
                'correction requires a source or destination balance'
            )
        if kind != 'open_set' and (from_location, from_disposition) == \
                (to_location, to_disposition) and from_location is not None:
            raise InventoryValidationError('source and destination must differ')
        expected = raw.get('expected_versions')
        if not isinstance(expected, dict):
            raise InventoryValidationError('expected_versions must be an object')
        from_version = None
        to_version = None
        if from_location is not None:
            from_version = read_int(
                expected.get('from'), 'expected_versions.from', minimum=0
            )
        if to_location is not None:
            to_version = read_int(
                expected.get('to'), 'expected_versions.to', minimum=0
            )
        conversion_id = conversion_factor = purpose = open_set_id = None
        if kind == 'open_set':
            conversion_id = read_int(
                raw.get('conversion_id'), 'conversion_id', minimum=1
            )
            conversion_factor = read_int(
                raw.get('conversion_factor'), 'conversion_factor', minimum=1
            )
            purpose = raw.get('purpose')
            if purpose not in {'customer_tray', 'replenishment'}:
                raise InventoryValidationError('purpose is invalid')
        elif 'open_set_id' in raw:
            open_set_id = read_int(raw.get('open_set_id'), 'open_set_id', minimum=1)
            if kind != 'consume' and not (allow_delivery_provenance and kind == 'move'):
                raise InventoryValidationError(
                    'open_set_id is only supported for explicit consumption'
                )
        normalized_lines.append({
            'product_id': product_id,
            'quantity': quantity,
            'unit': unit,
            'conversion_id': conversion_id,
            'conversion_factor': conversion_factor,
            'purpose': purpose,
            'open_set_id': open_set_id,
            'from_location_id': from_location,
            'from_disposition': from_disposition,
            'to_location_id': to_location,
            'to_disposition': to_disposition,
            'expected_versions': {
                **({'from': from_version} if from_location is not None else {}),
                **({'to': to_version} if to_location is not None else {}),
            },
        })
    return {
        'kind': kind,
        'business_date': business_date,
        'source_type': source_type,
        'source_id': source_id,
        'reason': reason,
        'correction_of': correction_of,
        'lines': normalized_lines,
    }


def _payload_hash(payload):
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    )
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _validate_catalog_and_locations(con, payload):
    locations = {}
    store_ids = []
    for line in payload['lines']:
        product = con.execute(
            """SELECT id, stock_unit, stock_form, identity_status
               FROM products WHERE id=?""", (line['product_id'],)
        ).fetchone()
        if product is None:
            raise InventoryValidationError('product_id does not identify a product')
        if product['identity_status'] != 'verified':
            raise InventoryConflict('Product identity is unverified',
                                    'identity_unverified')
        if product['stock_unit'] != line['unit']:
            raise InventoryValidationError('line unit does not match product unit')
        if payload['kind'] == 'open_set':
            conversion = con.execute(
                """SELECT c.source_product_id, c.target_product_id,
                          c.output_per_input, c.version,
                          p.stock_form AS target_form,
                          p.stock_unit AS target_unit,
                          p.identity_status AS target_status
                   FROM product_conversions c
                   JOIN products p ON p.id=c.target_product_id
                   WHERE c.id=?""",
                (line['conversion_id'],),
            ).fetchone()
            if conversion is None or conversion['source_product_id'] != line['product_id']:
                raise InventoryConflict(
                    'Conversion does not match the selected set product',
                    'conversion_product_mismatch',
                )
            if (product['stock_form'] != 'sealed_set'
                    or conversion['target_form'] != 'random_box'
                    or conversion['target_unit'] != 'box'
                    or conversion['target_status'] != 'verified'):
                raise InventoryConflict(
                    'Conversion products are not verified set and random-box identities',
                    'conversion_product_mismatch',
                )
            if conversion['output_per_input'] != line['conversion_factor']:
                raise InventoryConflict(
                    'Conversion factor changed; review the action again',
                    'conversion_changed',
                )
            line['target_product_id'] = conversion['target_product_id']
            line['target_unit'] = conversion['target_unit']
        for key in ('from_location_id', 'to_location_id'):
            location_id = line[key]
            if location_id is None or location_id in locations:
                continue
            location = con.execute(
                """SELECT l.id, l.store_id, ss.opening_verified
                   FROM inventory_locations l
                   JOIN stores s ON s.id=l.store_id
                   LEFT JOIN inventory_scope_state ss
                     ON ss.store_id=l.store_id AND ss.location_id=l.id
                   WHERE l.id=? AND l.is_active=1 AND s.is_active=1""",
                (location_id,),
            ).fetchone()
            if location is None:
                raise InventoryValidationError(
                    'location_id does not identify an active inventory location'
                )
            locations[location_id] = dict(location)
            if location['store_id'] not in store_ids:
                store_ids.append(location['store_id'])
    return locations, tuple(store_ids)


def _apply_negative(con, product_id, location_id, disposition,
                    quantity, expected_version):
    changed = con.execute(
        """UPDATE inventory_balances
           SET quantity=quantity-?, version=version+1
           WHERE product_id=? AND location_id=? AND disposition=?
             AND version=? AND quantity>=?""",
        (quantity, product_id, location_id, disposition,
         expected_version, quantity),
    ).rowcount
    if changed == 1:
        return
    current = con.execute(
        """SELECT quantity, version FROM inventory_balances
           WHERE product_id=? AND location_id=? AND disposition=?""",
        (product_id, location_id, disposition),
    ).fetchone()
    if current is None or current['quantity'] < quantity:
        raise InventoryConflict('Insufficient inventory', 'insufficient_stock')
    raise InventoryConflict('Inventory balance version changed', 'stale_version')


def _apply_positive(con, product_id, location_id, disposition,
                    quantity, expected_version):
    if expected_version == 0:
        try:
            con.execute(
                """INSERT INTO inventory_balances
                   (product_id, location_id, disposition, quantity, version)
                   VALUES (?, ?, ?, ?, 1)""",
                (product_id, location_id, disposition, quantity),
            )
            return
        except sqlite3.IntegrityError as exc:
            raise InventoryConflict(
                'Inventory balance version changed', 'stale_version'
            ) from exc
    changed = con.execute(
        """UPDATE inventory_balances
           SET quantity=quantity+?, version=version+1
           WHERE product_id=? AND location_id=? AND disposition=?
             AND version=? AND quantity<=?""",
        (quantity, product_id, location_id, disposition,
         expected_version, SQLITE_INTEGER_MAX - quantity),
    ).rowcount
    if changed == 1:
        return
    current = con.execute(
        """SELECT quantity, version FROM inventory_balances
           WHERE product_id=? AND location_id=? AND disposition=?""",
        (product_id, location_id, disposition),
    ).fetchone()
    if current is None or current['version'] != expected_version:
        raise InventoryConflict('Inventory balance version changed', 'stale_version')
    raise InventoryValidationError('Inventory quantity exceeds SQLite integer range')


def _consume_provenance(con, line, kind):
    if line['from_disposition'] != 'saleable':
        if line.get('open_set_id') is not None:
            raise InventoryValidationError(
                'open_set_id requires a saleable source balance'
            )
        return
    product = con.execute(
        'SELECT stock_form FROM products WHERE id=?', (line['product_id'],)
    ).fetchone()
    if product is None or product['stock_form'] != 'random_box':
        if line.get('open_set_id') is not None:
            raise InventoryValidationError(
                'open_set_id requires a random_box product'
            )
        return
    protected_rows = con.execute(
        """SELECT id, remaining_qty FROM inventory_open_sets
           WHERE random_product_id=? AND location_id=? AND remaining_qty>0
           ORDER BY id""",
        (line['product_id'], line['from_location_id']),
    ).fetchall()
    selected_id = line.get('open_set_id')
    if selected_id is not None:
        selected = next((row for row in protected_rows if row['id'] == selected_id), None)
        if selected is None or selected['remaining_qty'] < line['quantity']:
            raise InventoryConflict(
                'Selected opened set has insufficient retained quantity',
                'insufficient_stock',
            )
        changed = con.execute(
            """UPDATE inventory_open_sets
               SET remaining_qty=remaining_qty-?
               WHERE id=? AND random_product_id=? AND location_id=?
                 AND remaining_qty>=?""",
            (line['quantity'], selected_id, line['product_id'],
             line['from_location_id'], line['quantity']),
        ).rowcount
        if changed != 1:
            raise InventoryConflict('Selected opened set changed', 'stale_version')
        return

    protected = sum(row['remaining_qty'] for row in protected_rows)
    balance = con.execute(
        """SELECT quantity FROM inventory_balances
           WHERE product_id=? AND location_id=? AND disposition=?""",
        (line['product_id'], line['from_location_id'],
         line['from_disposition']),
    ).fetchone()
    physical = balance['quantity'] if balance else 0
    mixed = max(0, physical - protected)
    if kind == 'consume' and protected and line['quantity'] > mixed:
        raise InventoryConflict(
            'Select a retained opened set before consuming protected units',
            'fresh_set_selection_required',
        )
    if kind in {'move', 'correction'} and line['quantity'] > mixed:
        to_drop = line['quantity'] - mixed
        for row in protected_rows:
            drop = min(to_drop, row['remaining_qty'])
            if drop:
                con.execute(
                    'UPDATE inventory_open_sets SET remaining_qty=remaining_qty-? WHERE id=?',
                    (drop, row['id']),
                )
                to_drop -= drop
            if to_drop == 0:
                break


def _requested_effects(payload):
    effects = []
    for line in payload['lines']:
        if line['from_location_id'] is not None:
            effects.append((
                line['product_id'], line['from_location_id'],
                line['from_disposition'], -line['quantity'],
            ))
        if line['to_location_id'] is not None:
            effects.append((
                line['product_id'], line['to_location_id'],
                line['to_disposition'], line['quantity'],
            ))
    return sorted(effects)


def _validate_exact_correction(con, payload):
    original_id = payload['correction_of']
    if original_id is None:
        return
    original = con.execute(
        "SELECT kind FROM inventory_documents WHERE id=? AND status='posted'",
        (original_id,),
    ).fetchone()
    if original is None:
        raise InventoryValidationError(
            'correction_of does not identify a posted document'
        )
    if original['kind'] == 'opening':
        raise InventoryConflict(
            'Inventory activation cannot be reversed casually',
            'opening_not_reversible',
        )
    existing = con.execute(
        """SELECT 1 FROM inventory_documents
           WHERE kind='correction' AND correction_of=?""", (original_id,)
    ).fetchone()
    if existing:
        raise InventoryConflict(
            'Document already has a full reversal', 'already_reversed'
        )
    inverse = sorted(
        (row['product_id'], row['location_id'], row['disposition'],
         -row['quantity'])
        for row in con.execute(
            """SELECT product_id, location_id, disposition, quantity
               FROM inventory_movements WHERE document_id=?""",
            (original_id,),
        )
    )
    if _requested_effects(payload) != inverse:
        raise InventoryConflict(
            'Correction must exactly reverse the original movements',
            'correction_mismatch',
        )


def _validate_restock_complete(con, payload):
    if payload['kind'] != 'restock_complete':
        return None
    if payload['source_type'] != 'restock_session' or payload['source_id'] is None:
        raise InventoryValidationError(
            'restock_complete requires a restock_session source'
        )
    try:
        session_id = read_int(payload['source_id'], 'source_id', minimum=1)
    except ValueError as exc:
        raise InventoryValidationError(str(exc)) from exc
    session = con.execute(
        "SELECT status FROM restock_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if session is None or session['status'] not in {'submitted', 'picking'}:
        raise InventoryConflict('Restock session cannot be completed',
                                'restock_state_conflict')
    pending = con.execute(
        """SELECT 1 FROM restock_items
           WHERE session_id=? AND pick_status='pending' LIMIT 1""", (session_id,)
    ).fetchone()
    if pending:
        raise InventoryConflict('Restock session still has pending items',
                                'restock_pending')
    expected = sorted(
        (row['product_id'], row['found_qty']) for row in con.execute(
            """SELECT product_id, SUM(found_qty) AS found_qty
               FROM restock_items
               WHERE session_id=? AND pick_status='found' AND found_qty>0
               GROUP BY product_id""", (session_id,)
        )
    )
    actual = sorted((line['product_id'], line['quantity'])
                    for line in payload['lines'])
    if actual != expected:
        raise InventoryConflict('Restock lines changed before completion',
                                'restock_state_conflict')
    return session_id


def _project_compatibility(con, document_id, kind, business_date, affected,
                           restock_session_id=None):
    """Keep existing stock/history screens aligned inside the posting transaction."""
    for product_id, location_id, disposition in affected:
        if disposition != 'saleable':
            continue
        location = con.execute(
            'SELECT store_id, code FROM inventory_locations WHERE id=?',
            (location_id,),
        ).fetchone()
        if location is None or location['code'] not in {'floor', 'upstairs', 'warehouse'}:
            continue
        balance = con.execute(
            """SELECT quantity FROM inventory_balances
               WHERE product_id=? AND location_id=? AND disposition=?""",
            (product_id, location_id, disposition),
        ).fetchone()
        con.execute(
            """INSERT OR IGNORE INTO stock
               (product_id, store_id, upstairs_qty, instore_qty, claw_qty)
               VALUES (?, ?, 0, 0, 0)""",
            (product_id, location['store_id']),
        )
        column = 'instore_qty' if location['code'] == 'floor' else 'upstairs_qty'
        con.execute(
            f"""UPDATE stock SET {column}=?, last_updated=?
                WHERE product_id=? AND store_id=?""",
            (balance['quantity'] if balance else 0, business_date,
             product_id, location['store_id']),
        )

    for movement in con.execute(
        """SELECT m.product_id, m.quantity, l.store_id, l.code
           FROM inventory_movements m
           JOIN inventory_locations l ON l.id=m.location_id
           WHERE m.document_id=? ORDER BY m.id""", (document_id,)
    ):
        con.execute(
            """INSERT INTO stock_transactions
               (product_id, txn_type, qty, location, date, notes, store_id,
                inventory_document_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (movement['product_id'], f'inventory_{kind}', movement['quantity'],
             movement['code'], business_date,
             f'Inventory document {document_id}', movement['store_id'], document_id),
        )
        con.execute(
            """INSERT INTO stock_movements
               (product_id, session_id, movement_type, qty_change, location,
                store_id, inventory_document_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (movement['product_id'], restock_session_id,
             f'inventory_{kind}', movement['quantity'], movement['code'],
             movement['store_id'], document_id),
        )

def _post_inventory(con, payload, *, actor, request_key, owns_transaction,
                    access_store_ids=None, allow_transit=False,
                    allow_delivery_provenance=False, allow_trade=False):
    if owns_transaction and con.in_transaction:
        raise InventoryValidationError(
            'post_inventory must own the write transaction',
            'caller_transaction_active',
        )
    if not owns_transaction and not con.in_transaction:
        raise InventoryValidationError(
            '_post_inventory_in_transaction requires an active transaction',
            'caller_transaction_required',
        )
    if not isinstance(request_key, str) or not request_key.strip():
        raise InventoryValidationError('request_key is required')
    request_key = request_key.strip()
    if owns_transaction:
        con.execute(f'PRAGMA busy_timeout={INVENTORY_BUSY_TIMEOUT_MS}')
        try:
            con.execute('BEGIN IMMEDIATE')
        except sqlite3.OperationalError as exc:
            if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
                raise InventoryBusy() from exc
            raise

    try:
        normalized = _normalize_payload(
            payload, allow_delivery_provenance=allow_delivery_provenance
        )
        if not allow_trade and any(
            line.get('from_disposition') == 'trade'
            or line.get('to_disposition') == 'trade'
            for line in normalized['lines']
        ):
            raise InventoryConflict(
                'Trade inventory requires a trade operation',
                'trade_context_required',
            )
        if not allow_transit and any(
            line.get('from_disposition') == 'transit'
            or line.get('to_disposition') == 'transit'
            for line in normalized['lines']
        ):
            raise InventoryConflict(
                'Transit inventory requires a delivery operation',
                'delivery_context_required',
            )
        locations, store_ids = _validate_catalog_and_locations(con, normalized)
        minimum_role = {
            'opening': 'admin',
            'correction': 'manager',
        }.get(normalized['kind'], 'staff')
        required_stores = tuple(access_store_ids) if access_store_ids is not None else store_ids
        if not set(required_stores).issubset(store_ids):
            raise InventoryValidationError('access_store_ids is outside this command')
        require_inventory_access(con, actor, required_stores, minimum_role)
        digest = _payload_hash(normalized)

        prior = con.execute(
            """SELECT payload_hash, stored_result
               FROM inventory_documents WHERE request_key=?""",
            (request_key,),
        ).fetchone()
        if prior is not None:
            if prior['payload_hash'] != digest:
                raise InventoryConflict(
                    'Request key was already used for different intent',
                    'idempotency_conflict',
                )
            if prior['stored_result'] is None:
                raise InventoryConflict(
                    'Prior request has no completed result', 'inventory_incomplete'
                )
            result = json.loads(prior['stored_result'])
            if owns_transaction:
                con.commit()
            return result

        if normalized['source_type'] is not None:
            duplicate = con.execute(
                """SELECT id FROM inventory_documents
                   WHERE source_type=? AND source_id=?""",
                (normalized['source_type'], normalized['source_id']),
            ).fetchone()
            if duplicate:
                raise InventoryConflict(
                    'Source was already posted by another request',
                    'source_conflict',
                )

        mode = con.execute(
            'SELECT mode FROM inventory_mode WHERE id=1'
        ).fetchone()['mode']
        if normalized['kind'] == 'opening':
            if mode != 'legacy':
                raise InventoryConflict(
                    'Opening is only allowed during controlled activation',
                    'opening_closed',
                )
        elif mode != 'authoritative':
            raise InventoryConflict(
                'Authoritative inventory is not active',
                'inventory_legacy_mode',
            )

        if normalized['kind'] != 'opening':
            for location in locations.values():
                if not location['opening_verified']:
                    raise InventoryConflict(
                        'Opening balance is not verified',
                        'opening_unverified',
                    )

        if normalized['kind'] == 'correction':
            _validate_exact_correction(con, normalized)
        restock_session_id = _validate_restock_complete(con, normalized)


        actor_sub = actor['sub']
        cur = con.execute(
            """INSERT INTO inventory_documents
               (kind, request_key, payload_hash, source_type, source_id,
                actor_sub, business_date, correction_of, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'building')""",
            (normalized['kind'], request_key, digest,
             normalized['source_type'], normalized['source_id'], actor_sub,
             normalized['business_date'], normalized['correction_of']),
        )
        document_id = cur.lastrowid
        affected = set()

        for line_no, line in enumerate(normalized['lines'], start=1):
            expected = line['expected_versions']
            target_product_id = line.get('target_product_id', line['product_id'])
            target_quantity = (
                receipt_units(line['conversion_factor'], line['quantity'])
                if normalized['kind'] == 'open_set' else line['quantity']
            )
            con.execute(
                """INSERT INTO inventory_document_lines
                   (document_id, line_no, product_id, native_unit, quantity,
                    from_location_id, from_disposition, to_location_id,
                    to_disposition, from_version, to_version,
                    conversion_id, conversion_factor)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (document_id, line_no, line['product_id'], line['unit'],
                 line['quantity'], line['from_location_id'],
                 line['from_disposition'], line['to_location_id'],
                 line['to_disposition'], expected.get('from'),
                 expected.get('to'), line.get('conversion_id'),
                 line.get('conversion_factor')),
            )
            if line['from_location_id'] is not None:
                if normalized['kind'] != 'open_set':
                    _consume_provenance(con, line, normalized['kind'])
                _apply_negative(
                    con, line['product_id'], line['from_location_id'],
                    line['from_disposition'], line['quantity'], expected['from'],
                )
                con.execute(
                    """INSERT INTO inventory_movements
                       (document_id, line_no, product_id, location_id,
                        disposition, quantity)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (document_id, line_no, line['product_id'],
                     line['from_location_id'], line['from_disposition'],
                     -line['quantity']),
                )
                affected.add((line['product_id'], line['from_location_id'],
                              line['from_disposition']))
            if line['to_location_id'] is not None:
                _apply_positive(
                    con, target_product_id, line['to_location_id'],
                    line['to_disposition'], target_quantity, expected['to'],
                )
                con.execute(
                    """INSERT INTO inventory_movements
                       (document_id, line_no, product_id, location_id,
                        disposition, quantity)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (document_id, line_no, target_product_id,
                     line['to_location_id'], line['to_disposition'],
                     target_quantity),
                )
                affected.add((target_product_id, line['to_location_id'],
                              line['to_disposition']))
            if normalized['kind'] == 'open_set':
                con.execute(
                    """INSERT INTO inventory_open_sets
                       (opening_document_id, random_product_id, location_id,
                        purpose, remaining_qty)
                       VALUES (?, ?, ?, ?, ?)""",
                    (document_id, target_product_id, line['to_location_id'],
                     line['purpose'], target_quantity),
                )
        if restock_session_id is not None:
            updated = con.execute(
                """UPDATE restock_sessions
                   SET status='completed', completed_at=datetime('now')
                   WHERE id=? AND status IN ('submitted','picking')""",
                (restock_session_id,),
            )
            if updated.rowcount != 1:
                raise InventoryConflict('Restock session changed before completion',
                                        'restock_state_conflict')
        _project_compatibility(
            con, document_id, normalized['kind'], normalized['business_date'],
            affected, restock_session_id,
        )
        posted_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        balances = []
        for product_id, location_id, disposition in sorted(affected):
            row = con.execute(
                """SELECT quantity, version FROM inventory_balances
                   WHERE product_id=? AND location_id=? AND disposition=?""",
                (product_id, location_id, disposition),
            ).fetchone()
            balances.append({
                'product_id': product_id,
                'location_id': location_id,
                'disposition': disposition,
                'quantity': row['quantity'],
                'version': row['version'],
            })
        result = {
            'document_id': document_id,
            'posted_at': posted_at,
            'balances': balances,
        }
        stored = json.dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        )
        con.execute(
            """UPDATE inventory_documents
               SET status='posted', posted_at=?, stored_result=?
               WHERE id=? AND status='building'""",
            (posted_at, stored, document_id),
        )
        if normalized['kind'] == 'opening':
            for location_id in locations:
                con.execute(
                    """UPDATE inventory_scope_state
                       SET opening_verified=1, opening_document_id=?
                       WHERE location_id=?""",
                    (document_id, location_id),
                )
            con.execute(
                """UPDATE inventory_mode
                   SET mode='authoritative', cutover_identifier=?, cutover_at=?
                   WHERE id=1 AND mode='legacy'""",
                (request_key, posted_at),
            )
        if owns_transaction:
            con.commit()
        return result
    except Exception:
        if owns_transaction and con.in_transaction:
            con.rollback()
        raise


def _post_inventory_in_transaction(con, payload, *, actor, request_key,
                                   access_store_ids=None, allow_transit=False,
                                   allow_delivery_provenance=False,
                                   allow_trade=False):
    """Post inventory inside a caller-owned transaction without committing it."""
    return _post_inventory(
        con, payload, actor=actor, request_key=request_key,
        owns_transaction=False, access_store_ids=access_store_ids,
        allow_transit=allow_transit,
        allow_delivery_provenance=allow_delivery_provenance,
        allow_trade=allow_trade,
    )


def post_inventory(con, payload, *, actor, request_key):
    """Post inventory as one complete transaction."""
    return _post_inventory(
        con, payload, actor=actor, request_key=request_key,
        owns_transaction=True,
    )
