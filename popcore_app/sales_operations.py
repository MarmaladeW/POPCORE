"""Immutable individual sale documents and inventory allocation."""
from checkout_access import require_linked_sale
from datetime import datetime, timezone

from auth import ROLE_CLAIM, ROLE_HIERARCHY
from goods_operations import _balance, _begin, _location, _remember, _replay
from inventory_commands import (
    InventoryConflict, InventoryValidationError,
    _post_inventory_in_transaction, require_inventory_access,
)
from validation import SQLITE_INTEGER_MAX, read_date, read_int


PENDING_ALLOCATION_CODES = {
    'insufficient_stock', 'stale_version', 'identity_unverified',
    'opening_unverified', 'inventory_legacy_mode',
    'fresh_set_selection_required', 'provenance_reconciliation_required',
}
TENDERS = {'cash', 'card', 'e_transfer', 'wechat', 'alipay'}


def record_late_adjustment(con, store_id, business_date, source_type, source_id,
                           reason, actor_sub):
    closing = con.execute(
        """SELECT id FROM closing_sessions
           WHERE store_id=? AND business_date=? AND status='closed'""",
        (store_id, business_date),
    ).fetchone()
    if closing:
        con.execute(
            """INSERT OR IGNORE INTO closing_adjustments
               (closing_session_id, source_type, source_id, reason, actor_sub)
               VALUES (?, ?, ?, ?, ?)""",
            (closing['id'], source_type, str(source_id), reason, actor_sub),
        )


def _optional_cents(value, field, *, nonnegative=False):
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.isascii() and (
            candidate.isdecimal()
            or (candidate.startswith('-') and candidate[1:].isdecimal())
        ):
            value = int(candidate)
    if (
        type(value) is not int
        or value < -SQLITE_INTEGER_MAX
        or value > SQLITE_INTEGER_MAX
        or (nonnegative and value < 0)
    ):
        requirement = 'a nonnegative integer' if nonnegative else 'an integer'
        raise InventoryValidationError(f'{field} must be {requirement} cents')
    return value


def _sale_input(con, data, actor):
    if not isinstance(data, dict):
        raise InventoryValidationError('body must be an object')
    store_id = read_int(data.get('store_id'), 'store_id', minimum=1)
    require_inventory_access(con, actor, (store_id,), 'staff')
    business_date = read_date(data.get('business_date'), 'business_date')
    entry_mode = data.get('entry_mode')
    if entry_mode not in {'planned_entry', 'already_paid'}:
        raise InventoryValidationError('entry_mode is invalid')
    source = data.get('source')
    if not isinstance(source, dict):
        raise InventoryValidationError('source is required')
    source_values = {
        key: str(source.get(key) or '').strip()
        for key in ('system', 'account', 'reference')
    }
    if not all(source_values.values()):
        raise InventoryValidationError('source system, account, and reference are required')
    money = {
        'subtotal_cents': _optional_cents(
            data.get('subtotal_cents'), 'subtotal_cents', nonnegative=True
        ),
        'source_tax_cents': _optional_cents(
            data.get('source_tax_cents'), 'source_tax_cents', nonnegative=True
        ),
        'gross_cents': _optional_cents(
            data.get('gross_cents'), 'gross_cents', nonnegative=True
        ),
        'reduction_cents': _optional_cents(
            data.get('reduction_cents'), 'reduction_cents', nonnegative=True
        ),
        'rounding_cents': _optional_cents(data.get('rounding_cents'), 'rounding_cents'),
        'collected_cents': _optional_cents(
            data.get('collected_cents'), 'collected_cents', nonnegative=True
        ),
    }
    if money['subtotal_cents'] is not None and money['source_tax_cents'] is not None:
        expected_gross = money['subtotal_cents'] + money['source_tax_cents']
        if money['gross_cents'] is not None and money['gross_cents'] != expected_gross:
            raise InventoryValidationError('gross_cents does not match supplied components')
    if all(money[key] is not None for key in (
        'gross_cents', 'reduction_cents', 'rounding_cents', 'collected_cents'
    )):
        expected_collected = (money['gross_cents'] - money['reduction_cents']
                              + money['rounding_cents'])
        if money['collected_cents'] != expected_collected:
            raise InventoryValidationError('collected_cents does not match supplied components')
    floor = con.execute(
        """SELECT id FROM inventory_locations
           WHERE store_id=? AND code='floor' AND is_active=1""", (store_id,)
    ).fetchone()
    if floor is None:
        raise InventoryConflict('Store floor location is missing', 'inventory_store_missing')
    _location(con, floor['id'])
    raw_lines = data.get('lines')
    if not isinstance(raw_lines, list) or not raw_lines:
        raise InventoryValidationError('lines must be a non-empty list')
    lines = []
    product_ids = set()
    for index, raw in enumerate(raw_lines, 1):
        if not isinstance(raw, dict):
            raise InventoryValidationError(f'line {index} must be an object')
        quantity = read_int(raw.get('quantity'), 'quantity', minimum=1)
        product_id = raw.get('product_id')
        raw_text = str(raw.get('raw_product_text') or '').strip() or None
        if product_id is None:
            if entry_mode != 'already_paid' or not raw_text:
                raise InventoryValidationError('planned sale lines require a product')
            lines.append({
                'product_id': None, 'raw_product_text': raw_text,
                'product_name_snapshot': raw_text, 'stock_form_snapshot': None,
                'unit': None, 'quantity': quantity,
                'unit_price_cents': _optional_cents(
                    raw.get('unit_price_cents'), 'unit_price_cents', nonnegative=True
                ),
                'source_tax_cents': _optional_cents(
                    raw.get('source_tax_cents'), 'source_tax_cents', nonnegative=True
                ),
                'location_id': floor['id'], 'captured_balance_version': None,
                'open_set_id': None, 'fresh_set_product_id': None,
                'fresh_set_conversion_id': None, 'fresh_set_conversion_factor': None,
                'fresh_set_balance_version': None,
            })
            continue
        product_id = read_int(product_id, 'product_id', minimum=1)
        if product_id in product_ids:
            raise InventoryValidationError('sale lines must not repeat a product')
        product_ids.add(product_id)
        product = con.execute(
            """SELECT id, jizhanming, name_cn_en, stock_form, stock_unit,
                      identity_status FROM products WHERE id=?""", (product_id,)
        ).fetchone()
        if product is None:
            raise InventoryValidationError('product_id is invalid')
        if product['identity_status'] != 'verified':
            if entry_mode == 'planned_entry':
                raise InventoryConflict('Product identity is unverified', 'identity_unverified')
        unit = raw.get('unit')
        if unit != product['stock_unit']:
            raise InventoryValidationError('unit does not match product unit')
        balance = _balance(con, product_id, floor['id'], 'saleable')
        open_set_id = raw.get('open_set_id')
        open_set_id = None if open_set_id is None else read_int(
            open_set_id, 'open_set_id', minimum=1
        )
        if open_set_id is not None:
            opened = con.execute(
                'SELECT random_product_id, location_id FROM inventory_open_sets WHERE id=?',
                (open_set_id,),
            ).fetchone()
            if (product['stock_form'] != 'random_box' or opened is None
                    or opened['random_product_id'] != product_id
                    or opened['location_id'] != floor['id']):
                raise InventoryValidationError('Opened set does not match the product and store')
            if raw.get('fresh_set') is not None:
                raise InventoryValidationError('Choose an opened set or a fresh set, not both')
        fresh_set = raw.get('fresh_set')
        fresh_values = {
            'fresh_set_product_id': None, 'fresh_set_conversion_id': None,
            'fresh_set_conversion_factor': None, 'fresh_set_balance_version': None,
        }
        if fresh_set is not None:
            if product['stock_form'] != 'random_box' or not isinstance(fresh_set, dict):
                raise InventoryValidationError('fresh_set is invalid for this product')
            set_product_id = read_int(
                fresh_set.get('product_id'), 'fresh_set.product_id', minimum=1
            )
            conversion_id = read_int(
                fresh_set.get('conversion_id'), 'fresh_set.conversion_id', minimum=1
            )
            conversion_factor = read_int(
                fresh_set.get('conversion_factor'),
                'fresh_set.conversion_factor', minimum=1,
            )
            conversion = con.execute(
                """SELECT source_product_id, target_product_id, output_per_input
                   FROM product_conversions WHERE id=?""", (conversion_id,),
            ).fetchone()
            set_product = con.execute(
                """SELECT stock_form, stock_unit, identity_status FROM products WHERE id=?""",
                (set_product_id,),
            ).fetchone()
            if (
                conversion is None or set_product is None
                or conversion['source_product_id'] != set_product_id
                or conversion['target_product_id'] != product_id
                or conversion['output_per_input'] != conversion_factor
                or set_product['stock_form'] != 'sealed_set'
                or set_product['stock_unit'] != 'set'
                or set_product['identity_status'] != 'verified'
            ):
                raise InventoryConflict(
                    'Fresh-set selection does not match the product',
                    'conversion_product_mismatch',
                )
            fresh_values = {
                'fresh_set_product_id': set_product_id,
                'fresh_set_conversion_id': conversion_id,
                'fresh_set_conversion_factor': conversion_factor,
                'fresh_set_balance_version': _balance(
                    con, set_product_id, floor['id'], 'saleable'
                )['version'],
            }
        lines.append({
            'product_id': product_id, 'raw_product_text': raw_text,
            'product_name_snapshot': product['jizhanming'] or product['name_cn_en'],
            'stock_form_snapshot': product['stock_form'], 'unit': unit,
            'quantity': quantity,
            'unit_price_cents': _optional_cents(
                raw.get('unit_price_cents'), 'unit_price_cents', nonnegative=True
            ),
            'source_tax_cents': _optional_cents(
                raw.get('source_tax_cents'), 'source_tax_cents', nonnegative=True
            ),
            'location_id': floor['id'],
            'captured_balance_version': balance['version'],
            'open_set_id': open_set_id,
            **fresh_values,
        })
    return {
        'store_id': store_id, 'business_date': business_date,
        'entry_mode': entry_mode, 'source': source_values,
        **money, 'lines': lines,
    }


def _create_sale_in_transaction(con, intent, actor):
    try:
        sale_id = con.execute(
            """INSERT INTO sale_documents
               (store_id, business_date, entry_mode, subtotal_cents,
                source_tax_cents, gross_cents, reduction_cents,
                rounding_cents, collected_cents, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (intent['store_id'], intent['business_date'], intent['entry_mode'],
             intent['subtotal_cents'], intent['source_tax_cents'],
             intent['gross_cents'], intent['reduction_cents'],
             intent['rounding_cents'], intent['collected_cents'], actor['sub']),
        ).lastrowid
        source = intent['source']
        con.execute(
            """INSERT INTO sale_sources
               (sale_id, source_system, source_account, source_reference)
               VALUES (?, ?, ?, ?)""",
            (sale_id, source['system'], source['account'], source['reference']),
        )
    except Exception as exc:
        if 'UNIQUE constraint failed: sale_sources' in str(exc):
            raise InventoryConflict('Sale source already exists', 'source_conflict') from exc
        raise
    for line_no, line in enumerate(intent['lines'], 1):
        con.execute(
            """INSERT INTO sale_lines
               (sale_id, line_no, product_id, raw_product_text,
                product_name_snapshot, stock_form_snapshot, native_unit,
                quantity, unit_price_cents, source_tax_cents, location_id,
                captured_balance_version, open_set_id, fresh_set_product_id,
                fresh_set_conversion_id, fresh_set_conversion_factor,
                fresh_set_balance_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sale_id, line_no, line['product_id'], line['raw_product_text'],
             line['product_name_snapshot'], line['stock_form_snapshot'], line['unit'],
             line['quantity'], line['unit_price_cents'], line['source_tax_cents'],
             line['location_id'], line['captured_balance_version'], line['open_set_id'],
             line['fresh_set_product_id'], line['fresh_set_conversion_id'],
             line['fresh_set_conversion_factor'], line['fresh_set_balance_version']),
        )
    return sale_id


def _reject_checkout_source(system):
    if system == 'popcore_checkout':
        raise InventoryValidationError('Checkout source identities are assigned by the checkout workflow')


def create_sale(con, data, *, actor, request_key):
    intent = _sale_input(con, data, actor)
    _reject_checkout_source(intent['source']['system'])
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'sale_create', actor, intent)
        if prior:
            con.commit()
            return prior
        sale_id = _create_sale_in_transaction(con, intent, actor)
        result = {'sale_id': sale_id, 'version': 1, 'status': 'draft'}
        _remember(con, key, 'sale_create', sale_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def update_sale(con, sale_id, data, *, actor, request_key):
    sale = _sale_row(con, sale_id)
    require_linked_sale(con, sale_id, actor, write=True)
    require_inventory_access(con, actor, (sale['store_id'],), 'staff')
    _require_sale_owner(sale, actor)
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    intent = _sale_input(con, data, actor)
    _reject_checkout_source(intent['source']['system'])
    intent = {'sale_id': sale['id'], 'expected_version': expected_version, **intent}
    _begin(con)
    try:
        require_linked_sale(con, sale_id, actor, write=True)
        sale = _sale_row(con, sale_id)
        _require_sale_owner(sale, actor)
        key, digest, prior = _replay(con, request_key, 'sale_update', actor, intent)
        if prior:
            con.commit()
            return prior
        if sale['status'] != 'draft' or sale['version'] != expected_version:
            raise InventoryConflict('Sale changed before save', 'sale_state_conflict')
        source = intent['source']
        try:
            con.execute(
                """UPDATE sale_sources SET source_system=?, source_account=?,
                          source_reference=? WHERE sale_id=?""",
                (source['system'], source['account'], source['reference'], sale_id),
            )
        except Exception as exc:
            if 'UNIQUE constraint failed: sale_sources' in str(exc):
                raise InventoryConflict('Sale source already exists', 'source_conflict') from exc
            raise
        changed = con.execute(
            """UPDATE sale_documents SET store_id=?, business_date=?, entry_mode=?,
                      subtotal_cents=?, source_tax_cents=?, gross_cents=?,
                      reduction_cents=?, rounding_cents=?, collected_cents=?,
                      version=version+1
               WHERE id=? AND status='draft' AND version=?""",
            (intent['store_id'], intent['business_date'], intent['entry_mode'],
             intent['subtotal_cents'], intent['source_tax_cents'], intent['gross_cents'],
             intent['reduction_cents'], intent['rounding_cents'], intent['collected_cents'],
             sale_id, expected_version),
        )
        if changed.rowcount != 1:
            raise InventoryConflict('Sale changed before save', 'sale_state_conflict')
        con.execute('DELETE FROM sale_lines WHERE sale_id=?', (sale_id,))
        for line_no, line in enumerate(intent['lines'], 1):
            con.execute(
                """INSERT INTO sale_lines
                   (sale_id, line_no, product_id, raw_product_text,
                    product_name_snapshot, stock_form_snapshot, native_unit,
                    quantity, unit_price_cents, source_tax_cents, location_id,
                    captured_balance_version, open_set_id, fresh_set_product_id,
                    fresh_set_conversion_id, fresh_set_conversion_factor,
                    fresh_set_balance_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sale_id, line_no, line['product_id'], line['raw_product_text'],
                 line['product_name_snapshot'], line['stock_form_snapshot'], line['unit'],
                 line['quantity'], line['unit_price_cents'], line['source_tax_cents'],
                 line['location_id'], line['captured_balance_version'], line['open_set_id'],
                 line['fresh_set_product_id'], line['fresh_set_conversion_id'],
                 line['fresh_set_conversion_factor'], line['fresh_set_balance_version']),
            )
        result = {'sale_id': sale_id, 'version': expected_version + 1, 'status': 'draft'}
        _remember(con, key, 'sale_update', sale_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def _sale_row(con, sale_id):
    sale_id = read_int(sale_id, 'sale_id', minimum=1)
    row = con.execute('SELECT * FROM sale_documents WHERE id=?', (sale_id,)).fetchone()
    if row is None:
        raise InventoryValidationError('sale does not exist')
    return dict(row)


def _require_sale_owner(sale, actor):
    if sale['created_by'] != actor.get('sub') and ROLE_HIERARCHY.get(
        actor.get(ROLE_CLAIM, 'viewer'), 0
    ) < ROLE_HIERARCHY['manager']:
        raise PermissionError('Sale access denied')


def sale_detail(con, sale_id, *, actor):
    sale = _sale_row(con, sale_id)
    if not require_linked_sale(con, sale_id, actor):
        require_inventory_access(con, actor, (sale['store_id'],), 'staff')
        _require_sale_owner(sale, actor)
    sale['sale_id'] = sale.pop('id')
    sale['lines'] = [dict(row) for row in con.execute(
        'SELECT * FROM sale_lines WHERE sale_id=? ORDER BY line_no', (sale_id,)
    )]
    sale['sources'] = [dict(row) for row in con.execute(
        'SELECT * FROM sale_sources WHERE sale_id=? ORDER BY id', (sale_id,)
    )]
    sale['unresolved_reasons'] = (
        [sale['allocation_reason']] if sale['allocation_reason'] else []
    )
    payments = []
    for row in con.execute(
        'SELECT * FROM sale_payments WHERE sale_id=? ORDER BY id', (sale_id,)
    ):
        payment = dict(row)
        events = [dict(event) for event in con.execute(
            'SELECT * FROM payment_events WHERE payment_id=? ORDER BY id', (row['id'],)
        )]
        effective = payment['amount_cents']
        if effective is not None:
            for event in events:
                if event['direction'] == 'increase':
                    effective += event['amount_cents']
                elif event['direction'] == 'decrease':
                    effective -= event['amount_cents']
        payment['events'] = events
        payment['evidence'] = [dict(evidence) for evidence in con.execute(
            """SELECT id, status, mime_type, byte_size, created_at
               FROM payment_evidence WHERE payment_id=? ORDER BY id""", (row['id'],)
        )]
        payment['effective_amount_cents'] = effective
        payments.append(payment)
    sale['payments'] = payments
    known = [p['effective_amount_cents'] for p in payments
             if p['effective_amount_cents'] is not None]
    sale['payment_total_cents'] = sum(known) if len(known) == len(payments) else None
    sale['payment_difference_cents'] = (
        sale['payment_total_cents'] - sale['collected_cents']
        if sale['payment_total_cents'] is not None and sale['collected_cents'] is not None
        else None
    )
    sale['returns'] = [dict(row) for row in con.execute(
        'SELECT * FROM sale_returns WHERE sale_id=? ORDER BY id', (sale_id,)
    )]
    return sale


def _allocation_payload(con, sale, *, current_versions=False):
    lines = []
    unresolved = []
    for row in con.execute(
        'SELECT * FROM sale_lines WHERE sale_id=? ORDER BY line_no', (sale['id'],)
    ):
        if row['product_id'] is None or row['native_unit'] is None:
            unresolved.append('product_mapping_required')
            continue
        product = con.execute(
            'SELECT stock_form, stock_unit FROM products WHERE id=?', (row['product_id'],),
        ).fetchone()
        if (product is None or product['stock_unit'] != row['native_unit']
                or product['stock_form'] != row['stock_form_snapshot']):
            unresolved.append('product_mapping_required')
            continue
        if row['fresh_set_product_id'] is not None and row['open_set_id'] is None:
            source = con.execute(
                'SELECT stock_form, stock_unit FROM products WHERE id=?',
                (row['fresh_set_product_id'],),
            ).fetchone()
            conversion = con.execute(
                'SELECT source_product_id, target_product_id, output_per_input FROM product_conversions WHERE id=?',
                (row['fresh_set_conversion_id'],),
            ).fetchone()
            if (source is None or source['stock_form'] != 'sealed_set' or source['stock_unit'] != 'set'
                    or conversion is None or conversion['source_product_id'] != row['fresh_set_product_id']
                    or conversion['target_product_id'] != row['product_id']
                    or conversion['output_per_input'] != row['fresh_set_conversion_factor']):
                unresolved.append('product_mapping_required')
                continue
        if row['stock_form_snapshot'] == 'random_box' and row['open_set_id'] is None:
            conversion = con.execute(
                """SELECT output_per_input FROM product_conversions
                   WHERE target_product_id=? ORDER BY version DESC LIMIT 1""",
                (row['product_id'],),
            ).fetchone()
            if (
                conversion is not None
                and row['quantity'] > conversion['output_per_input'] // 2
                and row['fresh_set_product_id'] is None
            ):
                unresolved.append('fresh_set_selection_required')
                continue
        version = row['captured_balance_version']
        if current_versions:
            version = _balance(
                con, row['product_id'], row['location_id'], 'saleable'
            )['version']
        lines.append({
            'product_id': row['product_id'], 'unit': row['native_unit'],
            'quantity': row['quantity'], 'from_location_id': row['location_id'],
            'from_disposition': 'saleable',
            'expected_versions': {'from': version},
            **({'open_set_id': row['open_set_id']} if row['open_set_id'] else {}),
        })
    return lines, sorted(set(unresolved))


def _open_selected_fresh_sets(con, sale, actor, request_key):
    selected = con.execute(
        """SELECT * FROM sale_lines
           WHERE sale_id=? AND fresh_set_product_id IS NOT NULL
                 AND open_set_id IS NULL ORDER BY line_no""",
        (sale['id'],),
    ).fetchall()
    if not selected:
        return
    opening_lines = []
    for row in selected:
        set_version = row['fresh_set_balance_version']
        box_version = row['captured_balance_version']
        opening_lines.append({
            'product_id': row['fresh_set_product_id'], 'quantity': 1, 'unit': 'set',
            'conversion_id': row['fresh_set_conversion_id'],
            'conversion_factor': row['fresh_set_conversion_factor'],
            'purpose': 'customer_tray',
            'from_location_id': row['location_id'],
            'from_disposition': 'saleable',
            'to_location_id': row['location_id'],
            'to_disposition': 'saleable',
            'expected_versions': {'from': set_version, 'to': box_version},
        })
    opened = _post_inventory_in_transaction(con, {
        'kind': 'open_set', 'business_date': sale['business_date'],
        'source_type': 'sale_document_open', 'source_id': str(sale['id']),
        'reason': 'Fresh set selected for sale', 'lines': opening_lines,
    }, actor=actor, request_key=f'inventory:{request_key}:open')
    for row in selected:
        opened_set = con.execute(
            """SELECT id FROM inventory_open_sets
               WHERE opening_document_id=? AND random_product_id=?
                     AND location_id=? AND purpose='customer_tray'""",
            (opened['document_id'], row['product_id'], row['location_id']),
        ).fetchone()
        if opened_set is None:
            raise InventoryConflict('Fresh set was not retained', 'fresh_set_selection_required')
        con.execute(
            'UPDATE sale_lines SET open_set_id=? WHERE sale_id=? AND line_no=?',
            (opened_set['id'], sale['id'], row['line_no']),
        )


def _post_sale_in_transaction(con, sale, expected_version, actor, key,
                              *, allocation_lines=None, allow_trade=False):
    """Post a reviewed sale inside an existing transaction."""
    _require_sale_owner(sale, actor)
    if sale['status'] != 'draft' or sale['version'] != expected_version:
        raise InventoryConflict('Sale changed before posting', 'sale_state_conflict')
    if allocation_lines is None:
        lines, unresolved = _allocation_payload(con, sale)
        if unresolved and sale['entry_mode'] == 'planned_entry':
            raise InventoryConflict('Sale product mapping is unresolved', unresolved[0])
    else:
        lines, unresolved = allocation_lines, []
    inventory_result = None
    if not unresolved:
        con.execute('SAVEPOINT sale_allocation')
        try:
            if allocation_lines is None:
                _open_selected_fresh_sets(con, sale, actor, key)
                lines, unresolved = _allocation_payload(con, sale, current_versions=True)
            inventory_result = _post_inventory_in_transaction(con, {
                'kind': 'consume', 'business_date': sale['business_date'],
                'source_type': 'sale_document', 'source_id': str(sale['id']),
                'reason': 'Posted sale', 'lines': lines,
            }, actor=actor, request_key=f'inventory:{key}:consume',
                allow_trade=allow_trade)
            con.execute('RELEASE sale_allocation')
        except InventoryConflict as exc:
            con.execute('ROLLBACK TO sale_allocation')
            con.execute('RELEASE sale_allocation')
            if (allocation_lines is not None or sale['entry_mode'] != 'already_paid'
                    or exc.code not in PENDING_ALLOCATION_CODES):
                raise
            unresolved = [exc.code]
    allocation_status = 'allocated' if inventory_result else 'pending'
    for row in con.execute(
        'SELECT line_no FROM sale_lines WHERE sale_id=? ORDER BY line_no',
        (sale['id'],),
    ):
        con.execute(
            """INSERT INTO sale_allocations
               (sale_id, line_no, status, reason, inventory_document_id)
               VALUES (?, ?, ?, ?, ?)""",
            (sale['id'], row['line_no'], allocation_status,
             unresolved[0] if unresolved else None,
             inventory_result['document_id'] if inventory_result else None),
        )
    posted_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    con.execute(
        """UPDATE sale_documents SET status='posted', version=version+1,
                  financial_status='recorded', allocation_status=?,
                  allocation_reason=?, inventory_document_id=?, posted_at=?
           WHERE id=? AND status='draft' AND version=?""",
        (allocation_status, unresolved[0] if unresolved else None,
         inventory_result['document_id'] if inventory_result else None,
         posted_at, sale['id'], expected_version),
    )
    record_late_adjustment(
        con, sale['store_id'], sale['business_date'], 'sale', sale['id'],
        'Sale posted after store-day close', actor['sub'],
    )
    return {
        'sale_id': sale['id'], 'version': expected_version + 1,
        'status': 'posted', 'financial_status': 'recorded',
        'allocation_status': allocation_status,
        'inventory_document_id': (
            inventory_result['document_id'] if inventory_result else None
        ),
        'unresolved_reasons': unresolved,
    }


def post_sale(con, sale_id, data, *, actor, request_key):
    sale = _sale_row(con, sale_id)
    require_linked_sale(con, sale_id, actor, write=True)
    require_inventory_access(con, actor, (sale['store_id'],), 'staff')
    _require_sale_owner(sale, actor)
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    intent = {'sale_id': sale['id'], 'expected_version': expected_version}
    _begin(con)
    try:
        require_linked_sale(con, sale_id, actor, write=True)
        sale = _sale_row(con, sale_id)
        _require_sale_owner(sale, actor)
        key, digest, prior = _replay(con, request_key, 'sale_post', actor, intent)
        if prior:
            current = sale_detail(con, sale_id, actor=actor)
            con.commit()
            return {key: current.get(key) for key in (
                'sale_id', 'version', 'status', 'financial_status',
                'allocation_status', 'inventory_document_id', 'unresolved_reasons'
            )}
        result = _post_sale_in_transaction(
            con, sale, expected_version, actor, key,
        )
        _remember(con, key, 'sale_post', sale_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def allocate_sale(con, sale_id, data, *, actor, request_key):
    sale = _sale_row(con, sale_id)
    require_linked_sale(con, sale_id, actor, write=True)
    require_inventory_access(con, actor, (sale['store_id'],), 'manager')
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    reason = str(data.get('reason') or '').strip()
    if not reason:
        raise InventoryValidationError('allocation reason is required')
    mappings = data.get('mappings', [])
    if not isinstance(mappings, list):
        raise InventoryValidationError('mappings must be a list')
    intent = {
        'sale_id': sale_id, 'expected_version': expected_version,
        'reason': reason, 'mappings': mappings,
    }
    _begin(con)
    try:
        require_linked_sale(con, sale_id, actor, write=True)
        sale = _sale_row(con, sale_id)
        key, digest, prior = _replay(con, request_key, 'sale_allocate', actor, intent)
        if prior:
            con.commit()
            return prior
        if (sale['status'] != 'posted' or sale['allocation_status'] != 'pending'
                or sale['version'] != expected_version):
            raise InventoryConflict('Sale allocation changed', 'sale_state_conflict')
        floor = con.execute(
            """SELECT id FROM inventory_locations
               WHERE store_id=? AND code='floor' AND is_active=1""",
            (sale['store_id'],),
        ).fetchone()
        for mapping in mappings:
            if not isinstance(mapping, dict):
                raise InventoryValidationError('each mapping must be an object')
            line_no = read_int(mapping.get('line_no'), 'line_no', minimum=1)
            product_id = read_int(mapping.get('product_id'), 'product_id', minimum=1)
            product = con.execute(
                """SELECT jizhanming, name_cn_en, stock_form, stock_unit, identity_status
                   FROM products WHERE id=?""", (product_id,),
            ).fetchone()
            if product is None or product['identity_status'] != 'verified':
                raise InventoryConflict('Product identity is unverified', 'identity_unverified')
            open_set_id = mapping.get('open_set_id')
            if open_set_id is not None:
                open_set_id = read_int(open_set_id, 'open_set_id', minimum=1)
            fresh_set = mapping.get('fresh_set')
            fresh_product_id = fresh_conversion_id = fresh_factor = fresh_version = None
            if fresh_set is not None:
                if product['stock_form'] != 'random_box' or not isinstance(fresh_set, dict):
                    raise InventoryValidationError('fresh_set is invalid for this product')
                fresh_product_id = read_int(
                    fresh_set.get('product_id'), 'fresh_set.product_id', minimum=1
                )
                fresh_conversion_id = read_int(
                    fresh_set.get('conversion_id'), 'fresh_set.conversion_id', minimum=1
                )
                fresh_factor = read_int(
                    fresh_set.get('conversion_factor'),
                    'fresh_set.conversion_factor', minimum=1,
                )
                conversion = con.execute(
                    """SELECT source_product_id, target_product_id, output_per_input
                       FROM product_conversions WHERE id=?""", (fresh_conversion_id,),
                ).fetchone()
                if (
                    conversion is None
                    or conversion['source_product_id'] != fresh_product_id
                    or conversion['target_product_id'] != product_id
                    or conversion['output_per_input'] != fresh_factor
                ):
                    raise InventoryConflict(
                        'Fresh-set selection does not match the product',
                        'conversion_product_mismatch',
                    )
                fresh_version = _balance(
                    con, fresh_product_id, floor['id'], 'saleable'
                )['version']
            changed = con.execute(
                """UPDATE sale_lines SET product_id=?, stock_form_snapshot=?, native_unit=?,
                          location_id=?, captured_balance_version=?, open_set_id=?,
                          fresh_set_product_id=?, fresh_set_conversion_id=?,
                          fresh_set_conversion_factor=?, fresh_set_balance_version=?
                   WHERE sale_id=? AND line_no=?""",
                (product_id, product['stock_form'], product['stock_unit'], floor['id'],
                 _balance(con, product_id, floor['id'], 'saleable')['version'], open_set_id,
                 fresh_product_id, fresh_conversion_id, fresh_factor, fresh_version,
                 sale_id, line_no),
            )
            if changed.rowcount != 1:
                raise InventoryValidationError('line_no is invalid')
        duplicate = con.execute(
            """SELECT product_id FROM sale_lines
               WHERE sale_id=? AND product_id IS NOT NULL
               GROUP BY product_id HAVING COUNT(*)>1 LIMIT 1""",
            (sale_id,),
        ).fetchone()
        if duplicate:
            raise InventoryValidationError('sale lines must not repeat a product')
        lines, unresolved = _allocation_payload(con, sale, current_versions=True)
        if unresolved:
            raise InventoryConflict('Sale product mapping is unresolved', unresolved[0])
        _open_selected_fresh_sets(con, sale, actor, key)
        lines, unresolved = _allocation_payload(con, sale, current_versions=True)
        inventory_result = _post_inventory_in_transaction(con, {
            'kind': 'consume', 'business_date': sale['business_date'],
            'source_type': 'sale_document', 'source_id': str(sale_id),
            'reason': reason, 'lines': lines,
        }, actor=actor, request_key=f'inventory:{key}:consume')
        con.execute(
            """UPDATE sale_documents SET allocation_status='allocated',
                      allocation_reason=NULL, inventory_document_id=?, version=version+1
               WHERE id=? AND version=?""",
            (inventory_result['document_id'], sale_id, expected_version),
        )
        con.execute(
            """UPDATE sale_allocations SET status='allocated', reason=NULL,
                      inventory_document_id=? WHERE sale_id=?""",
            (inventory_result['document_id'], sale_id),
        )
        result = {
            'sale_id': sale_id, 'version': expected_version + 1,
            'status': 'posted', 'financial_status': 'recorded',
            'allocation_status': 'allocated',
            'inventory_document_id': inventory_result['document_id'],
            'unresolved_reasons': [],
        }
        _remember(con, key, 'sale_allocate', sale_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def add_sale_payments(con, sale_id, data, *, actor, request_key):
    sale = _sale_row(con, sale_id)
    require_linked_sale(con, sale_id, actor, write=True)
    require_inventory_access(con, actor, (sale['store_id'],), 'staff')
    if sale['created_by'] != actor.get('sub') and ROLE_HIERARCHY.get(
        actor.get(ROLE_CLAIM, 'viewer'), 0
    ) < ROLE_HIERARCHY['manager']:
        raise PermissionError('Sale access denied')
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    raw_payments = data.get('payments')
    if not isinstance(raw_payments, list) or not raw_payments:
        raise InventoryValidationError('payments must be a non-empty list')
    payments = []
    for raw in raw_payments:
        if not isinstance(raw, dict) or not isinstance(raw.get('tender'), str) or raw['tender'] not in TENDERS:
            raise InventoryValidationError('payment tender is invalid')
        amount = _optional_cents(raw.get('amount_cents'), 'amount_cents', nonnegative=True)
        source = {
            key: str(raw.get(key) or '').strip() or None
            for key in ('source_system', 'source_account', 'source_reference')
        }
        _reject_checkout_source(source['source_system'])
        supplied = [value is not None for value in source.values()]
        if any(supplied) and not all(supplied):
            raise InventoryValidationError('payment source identity must be complete')
        payments.append({'tender': raw['tender'], 'amount_cents': amount, **source})
    intent = {'sale_id': sale_id, 'expected_version': expected_version, 'payments': payments}
    _begin(con)
    try:
        require_linked_sale(con, sale_id, actor, write=True)
        sale = _sale_row(con, sale_id)
        key, digest, prior = _replay(con, request_key, 'sale_payments_add', actor, intent)
        if prior:
            current = sale_detail(con, sale_id, actor=actor)
            con.commit()
            return current
        if sale['status'] != 'posted' or sale['version'] != expected_version:
            raise InventoryConflict('Sale changed before payment save', 'sale_state_conflict')
        try:
            for payment in payments:
                con.execute(
                    """INSERT INTO sale_payments
                       (sale_id, tender, amount_cents, source_system, source_account,
                        source_reference, recorded_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (sale_id, payment['tender'], payment['amount_cents'],
                     payment['source_system'], payment['source_account'],
                     payment['source_reference'], actor['sub']),
                )
        except Exception as exc:
            if 'UNIQUE constraint failed: sale_payments' in str(exc):
                raise InventoryConflict('Payment source already exists', 'source_conflict') from exc
            raise
        con.execute('UPDATE sale_documents SET version=version+1 WHERE id=?', (sale_id,))
        record_late_adjustment(
            con, sale['store_id'], sale['business_date'], 'sale_payment', sale_id,
            'Payment recorded after store-day close', actor['sub'],
        )
        result = sale_detail(con, sale_id, actor=actor)
        _remember(con, key, 'sale_payments_add', sale_id, actor, digest, {
            'sale_id': sale_id, 'version': expected_version + 1,
        })
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def _payment_sale(con, payment_id):
    payment_id = read_int(payment_id, 'payment_id', minimum=1)
    row = con.execute(
        """SELECT p.*, s.store_id, s.version AS sale_version
           FROM sale_payments p JOIN sale_documents s ON s.id=p.sale_id
           WHERE p.id=?""", (payment_id,),
    ).fetchone()
    if row is None:
        raise InventoryValidationError('payment does not exist')
    return dict(row)


def record_payment_event(con, payment_id, event_type, data, *, actor, request_key):
    payment = _payment_sale(con, payment_id)
    require_linked_sale(con, payment['sale_id'], actor, write=True)
    require_inventory_access(con, actor, (payment['store_id'],), 'manager')
    if event_type not in {'verify', 'reject', 'correction', 'refund'}:
        raise InventoryValidationError('payment event is invalid')
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    reason = str(data.get('reason') or '').strip()
    if not reason:
        raise InventoryValidationError('reason is required')
    direction = 'none'
    amount = None
    if event_type == 'correction':
        direction = data.get('direction')
        if direction not in {'increase', 'decrease'}:
            raise InventoryValidationError('correction direction is invalid')
        amount = _optional_cents(data.get('amount_cents'), 'amount_cents', nonnegative=True)
        if amount is None or amount == 0:
            raise InventoryValidationError('amount_cents must be positive')
    elif event_type == 'refund':
        direction = 'decrease'
        amount = _optional_cents(data.get('amount_cents'), 'amount_cents', nonnegative=True)
        if amount is None or amount == 0:
            raise InventoryValidationError('amount_cents must be positive')
    intent = {
        'payment_id': payment_id, 'event_type': event_type,
        'expected_version': expected_version, 'reason': reason,
        'direction': direction, 'amount_cents': amount,
    }
    _begin(con)
    try:
        require_linked_sale(con, payment['sale_id'], actor, write=True)
        payment = _payment_sale(con, payment_id)
        key, digest, prior = _replay(con, request_key, 'payment_event', actor, intent)
        if prior:
            con.commit()
            return prior
        if payment['sale_version'] != expected_version:
            raise InventoryConflict('Sale changed before payment review', 'sale_state_conflict')
        if event_type in {'refund', 'correction'} and direction == 'decrease':
            if payment['amount_cents'] is None:
                raise InventoryConflict('Unknown payment amount requires review', 'amount_unknown')
            decreases = con.execute(
                """SELECT COALESCE(SUM(amount_cents), 0) FROM payment_events
                   WHERE payment_id=? AND direction='decrease'""", (payment_id,),
            ).fetchone()[0]
            increases = con.execute(
                """SELECT COALESCE(SUM(amount_cents), 0) FROM payment_events
                   WHERE payment_id=? AND direction='increase'""", (payment_id,),
            ).fetchone()[0]
            if amount > payment['amount_cents'] + increases - decreases:
                raise InventoryConflict('Refund exceeds recorded payment', 'refund_exceeds_payment')
        con.execute(
            """INSERT INTO payment_events
               (payment_id, event_type, direction, amount_cents, reason, actor_sub)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (payment_id, event_type, direction, amount, reason, actor['sub']),
        )
        if event_type in {'verify', 'reject'}:
            con.execute(
                'UPDATE sale_payments SET state=? WHERE id=?',
                ('verified' if event_type == 'verify' else 'rejected', payment_id),
            )
        con.execute(
            'UPDATE sale_documents SET version=version+1 WHERE id=?',
            (payment['sale_id'],),
        )
        sale = _sale_row(con, payment['sale_id'])
        record_late_adjustment(
            con, sale['store_id'], sale['business_date'], 'payment_event', payment_id,
            'Payment event recorded after store-day close', actor['sub'],
        )
        result = {
            'payment_id': payment_id, 'sale_id': payment['sale_id'],
            'version': expected_version + 1, 'event_type': event_type,
        }
        _remember(con, key, 'payment_event', payment_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def add_sale_source(con, sale_id, data, *, actor, request_key):
    sale = _sale_row(con, sale_id)
    require_linked_sale(con, sale_id, actor, write=True)
    require_inventory_access(con, actor, (sale['store_id'],), 'manager')
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    source = {
        key: str(data.get(key) or '').strip()
        for key in ('source_system', 'source_account', 'source_reference')
    }
    _reject_checkout_source(source['source_system'])
    reason = str(data.get('reason') or '').strip()
    if not all(source.values()) or not reason:
        raise InventoryValidationError('complete source identity and reason are required')
    intent = {'sale_id': sale_id, 'expected_version': expected_version, **source, 'reason': reason}
    _begin(con)
    try:
        require_linked_sale(con, sale_id, actor, write=True)
        sale = _sale_row(con, sale_id)
        key, digest, prior = _replay(con, request_key, 'sale_source_add', actor, intent)
        if prior:
            con.commit()
            return prior
        if sale['version'] != expected_version:
            raise InventoryConflict('Sale changed before source link', 'sale_state_conflict')
        try:
            con.execute(
                """INSERT INTO sale_sources
                   (sale_id, source_system, source_account, source_reference)
                   VALUES (?, ?, ?, ?)""",
                (sale_id, source['source_system'], source['source_account'],
                 source['source_reference']),
            )
        except Exception as exc:
            if 'UNIQUE constraint failed: sale_sources' in str(exc):
                raise InventoryConflict('Sale source already exists', 'source_conflict') from exc
            raise
        con.execute('UPDATE sale_documents SET version=version+1 WHERE id=?', (sale_id,))
        result = {'sale_id': sale_id, 'version': expected_version + 1}
        _remember(con, key, 'sale_source_add', sale_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def record_sale_return(con, sale_id, data, *, actor, request_key):
    sale = _sale_row(con, sale_id)
    require_linked_sale(con, sale_id, actor, write=True)
    require_inventory_access(con, actor, (sale['store_id'],), 'manager')
    expected_version = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    line_no = read_int(data.get('line_no'), 'line_no', minimum=1)
    quantity = read_int(data.get('quantity'), 'quantity', minimum=1)
    disposition = data.get('disposition')
    if disposition not in {'saleable', 'damaged', 'hold'}:
        raise InventoryValidationError('return disposition is invalid')
    reason = str(data.get('reason') or '').strip()
    if not reason:
        raise InventoryValidationError('return reason is required')
    intent = {
        'sale_id': sale_id, 'expected_version': expected_version,
        'line_no': line_no, 'quantity': quantity,
        'disposition': disposition, 'reason': reason,
    }
    _begin(con)
    try:
        require_linked_sale(con, sale_id, actor, write=True)
        sale = _sale_row(con, sale_id)
        key, digest, prior = _replay(con, request_key, 'sale_return', actor, intent)
        if prior:
            con.commit()
            return prior
        if sale['version'] != expected_version or sale['allocation_status'] != 'allocated':
            raise InventoryConflict('Sale changed before return', 'sale_state_conflict')
        line = con.execute(
            'SELECT * FROM sale_lines WHERE sale_id=? AND line_no=?', (sale_id, line_no),
        ).fetchone()
        if line is None or line['product_id'] is None:
            raise InventoryValidationError('sale line is invalid')
        returned = con.execute(
            'SELECT COALESCE(SUM(quantity), 0) FROM sale_returns WHERE sale_id=? AND sale_line_no=?',
            (sale_id, line_no),
        ).fetchone()[0]
        if quantity > line['quantity'] - returned:
            raise InventoryConflict('Return exceeds allocated quantity', 'return_exceeds_sale')
        return_id = con.execute(
            """INSERT INTO sale_returns
               (sale_id, sale_line_no, quantity, disposition, reason, reviewed_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sale_id, line_no, quantity, disposition, reason, actor['sub']),
        ).lastrowid
        current = _balance(con, line['product_id'], line['location_id'], disposition)
        inventory = _post_inventory_in_transaction(con, {
            'kind': 'receipt', 'business_date': sale['business_date'],
            'source_type': 'sale_return', 'source_id': str(return_id),
            'reason': reason, 'lines': [{
                'product_id': line['product_id'], 'unit': line['native_unit'],
                'quantity': quantity, 'to_location_id': line['location_id'],
                'to_disposition': disposition,
                'expected_versions': {'to': current['version']},
            }],
        }, actor=actor, request_key=f'inventory:{key}:return')
        con.execute(
            'UPDATE sale_returns SET inventory_document_id=? WHERE id=?',
            (inventory['document_id'], return_id),
        )
        con.execute('UPDATE sale_documents SET version=version+1 WHERE id=?', (sale_id,))
        result = {
            'return_id': return_id, 'sale_id': sale_id,
            'version': expected_version + 1,
            'inventory_document_id': inventory['document_id'],
        }
        _remember(con, key, 'sale_return', return_id, actor, digest, result)
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def create_sale_reconciliation(con, data, *, actor, request_key):
    if not isinstance(data, dict):
        raise InventoryValidationError('body must be an object')
    store_id = read_int(data.get('store_id'), 'store_id', minimum=1)
    require_inventory_access(con, actor, (store_id,), 'manager')
    business_date = read_date(data.get('business_date'), 'business_date')
    intent_kind = data.get('intent')
    if intent_kind not in {'missing_transactions', 'summary_only', 'stock_already_posted'}:
        raise InventoryValidationError('reconciliation intent is invalid')
    source = {
        key: str(data.get(key) or '').strip()
        for key in ('source_system', 'source_account', 'source_reference')
    }
    notes = str(data.get('notes') or '').strip()
    if not all(source.values()) or not notes:
        raise InventoryValidationError('source identity and review notes are required')
    sale_id = data.get('sale_id')
    inventory_document_id = data.get('inventory_document_id')
    sale_id = None if sale_id is None else read_int(sale_id, 'sale_id', minimum=1)
    inventory_document_id = (
        None if inventory_document_id is None
        else read_int(inventory_document_id, 'inventory_document_id', minimum=1)
    )
    if intent_kind == 'missing_transactions' and sale_id is None:
        raise InventoryValidationError('missing transactions require a stable sale document')
    if intent_kind == 'stock_already_posted' and (
        inventory_document_id is None or sale_id is None
    ):
        raise InventoryValidationError(
            'stock already posted requires a sale and inventory document'
        )
    linked_sale = None
    if sale_id is not None:
        require_linked_sale(con, sale_id, actor, write=True)
        linked_sale = _sale_row(con, sale_id)
        if linked_sale['store_id'] != store_id:
            raise InventoryValidationError('sale belongs to another store')
    if inventory_document_id is not None:
        inventory = con.execute(
            """SELECT id, kind, business_date FROM inventory_documents
               WHERE id=? AND status='posted'""",
            (inventory_document_id,),
        ).fetchone()
        if inventory is None or inventory['business_date'] != business_date:
            raise InventoryValidationError('inventory document does not cover this store day')
        if intent_kind == 'stock_already_posted':
            if linked_sale['status'] != 'posted' or linked_sale['business_date'] != business_date:
                raise InventoryValidationError('sale does not cover this store day')
            if inventory['kind'] != 'consume':
                raise InventoryValidationError('inventory document is not a sale consumption')
            expected = sorted(
                (row['product_id'], row['native_unit'], row['quantity'],
                 row['location_id'], 'saleable', None, None)
                for row in con.execute(
                    """SELECT product_id, native_unit, quantity, location_id
                       FROM sale_lines WHERE sale_id=? ORDER BY line_no""",
                    (sale_id,),
                )
            )
            actual = sorted(
                (row['product_id'], row['native_unit'], row['quantity'],
                 row['from_location_id'], row['from_disposition'],
                 row['to_location_id'], row['to_disposition'])
                for row in con.execute(
                    """SELECT product_id, native_unit, quantity, from_location_id,
                              from_disposition, to_location_id, to_disposition
                       FROM inventory_document_lines WHERE document_id=? ORDER BY line_no""",
                    (inventory_document_id,),
                )
            )
            if expected != actual:
                raise InventoryValidationError(
                    'inventory document does not exactly cover the sale lines'
                )
    normalized = {
        'store_id': store_id, 'business_date': business_date, 'intent': intent_kind,
        **source, 'notes': notes, 'sale_id': sale_id,
        'inventory_document_id': inventory_document_id,
    }
    _begin(con)
    try:
        if sale_id is not None:
            require_linked_sale(con, sale_id, actor, write=True)
        key, digest, prior = _replay(
            con, request_key, 'sale_reconciliation', actor, normalized
        )
        if prior:
            con.commit()
            return prior
        if inventory_document_id is not None and con.execute(
            'SELECT 1 FROM sale_reconciliations WHERE inventory_document_id=?',
            (inventory_document_id,),
        ).fetchone():
            raise InventoryConflict(
                'Inventory document is already reconciled',
                'inventory_document_already_reconciled',
            )
        try:
            reconciliation_id = con.execute(
                """INSERT INTO sale_reconciliations
                   (store_id, business_date, intent, source_system, source_account,
                    source_reference, sale_id, inventory_document_id, notes, reviewed_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (store_id, business_date, intent_kind, source['source_system'],
                 source['source_account'], source['source_reference'], sale_id,
                 inventory_document_id, notes, actor['sub']),
            ).lastrowid
        except Exception as exc:
            if 'UNIQUE constraint failed: sale_reconciliations' in str(exc):
                code = (
                    'inventory_document_already_reconciled'
                    if 'inventory_document_id' in str(exc) else 'source_conflict'
                )
                raise InventoryConflict('Reconciliation already exists', code) from exc
            raise
        result = {'reconciliation_id': reconciliation_id, **normalized}
        _remember(
            con, key, 'sale_reconciliation', reconciliation_id, actor, digest, result
        )
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
