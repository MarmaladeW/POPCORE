"""盘点 Count: count one product at one location from a single screen.

A blind box is counted together with its sealed sets and confirmed designs; anything else is
one number. Before a store goes live a saved count only records what was found
(opening_counts) and leaves stock alone: on the store's count day
``scripts/inventory_opening.py intake --from-app --store DT`` reviews and applies those
counts exactly like count sheets, so going live stays one deliberate, reviewed step
(owner, 2026-10-27). Once a store is live, saving a count corrects its stock at once
(owner, 2026-10-27). Each such count is kept as an approved inventory count, so a manager
can look over the differences afterwards.
"""
from catalog_identity import (
    FORMS_TO_UNITS, normalize_design_name, product_alias_text_sql, update_product_identity,
)
from checkout_access import business_date
from goods_operations import _balance, _begin, _remember, _replay
from identity_sheet import SUGGESTED_IDENTITY
from inventory_commands import (
    InventoryConflict, InventoryError, InventoryValidationError,
    _post_inventory_in_transaction, require_inventory_access, store_is_live,
)
from inventory_opening import COUNT_VERIFIABLE_FORMS, legacy_column, trusts_legacy
from store_access import can_write_legacy_store
from validation import read_int

SEARCH_LIMIT = 20
MISSING_LIMIT = 200
MAX_ENTRIES = 300
MAX_QUANTITY = 100000
DIRECT_COUNT_REASON = '盘点 Count'
LABEL_SQL = "COALESCE(NULLIF(jizhanming,''), NULLIF(name_cn_en,''), sku)"


def _scope(con, actor, store_code, location_code=None):
    """The store (and location) to count, if actor may count there."""
    store = con.execute('SELECT id, code, name FROM stores WHERE code=? AND is_active=1',
                        ((store_code or '').strip().upper(),)).fetchone()
    if store is None:
        raise InventoryValidationError('Choose one store to count')
    live = store_is_live(con, store['id'])
    # A live store's stock is the ledger, which needs a store grant. Before that, the people
    # who work at the store count it, as on the legacy stock pages.
    if live:
        require_inventory_access(con, actor, (store['id'],), 'staff')
    elif not can_write_legacy_store(con, actor, store['id']):
        raise PermissionError('Inventory access denied')
    locations = [dict(row) for row in con.execute(
        'SELECT id, code, name FROM inventory_locations WHERE store_id=? AND is_active=1 ORDER BY id',
        (store['id'],))]
    location = None
    if location_code is not None:
        code = (location_code or '').strip().lower()
        location = next((item for item in locations if item['code'] == code), None)
        if location is None:
            raise InventoryValidationError('Choose a location of this store')
    return {'store': dict(store), 'live': live, 'locations': locations, 'location': location}


def _product(con, product_id):
    row = con.execute(f'SELECT *, {LABEL_SQL} AS label FROM products WHERE id=?', (product_id,)).fetchone()
    if row is None:
        raise InventoryError('Product not found', 'not_found', 404)
    return row


def _family(con, product_id):
    """What is counted together with product_id: (head product or None, series id or None).

    A sealed set is counted with its blind box and a confirmed design with the product that
    owns its series. A series no product owns is its designs alone."""
    product = _product(con, read_int(product_id, 'product_id', minimum=1))
    if product['stock_form'] == 'sealed_set':
        box = con.execute('SELECT target_product_id FROM product_conversions WHERE source_product_id=? '
                          'ORDER BY version DESC LIMIT 1', (product['id'],)).fetchone()
        if box:
            product = _product(con, box[0])
    if product['series_id'] is None:
        owned = con.execute('SELECT id FROM product_series WHERE product_id=?', (product['id'],)).fetchone()
        return product, owned['id'] if owned else None
    owner = con.execute('SELECT product_id FROM product_series WHERE id=?', (product['series_id'],)).fetchone()
    if owner is None or owner['product_id'] is None:
        return None, product['series_id']
    if owner['product_id'] != product['id']:
        product = _product(con, owner['product_id'])
    return product, product['series_id']


def _suggested_form(product):
    if product['stock_form']:
        return product['stock_form']
    return (SUGGESTED_IDENTITY.get((product['product_type'] or '').strip()) or (None, None))[0]


def _members(con, head, series_id):
    """(role, product, boxes per set) for everything counted on one card, in display order."""
    members = []
    if head is not None:
        members.append(('box' if _suggested_form(head) == 'random_box' else 'item', head, None))
        members.extend(('set', row, row['boxes_per_set']) for row in con.execute(
            f"""SELECT p.*, {LABEL_SQL} AS label, c.output_per_input AS boxes_per_set
                FROM products p JOIN product_conversions c ON c.source_product_id=p.id
                WHERE c.target_product_id=? AND p.stock_form='sealed_set'
                  AND c.version=(SELECT MAX(version) FROM product_conversions WHERE source_product_id=p.id)
                ORDER BY p.id""", (head['id'],)))
    if series_id is not None:
        members.extend(('design', row, None) for row in con.execute(
            f"""SELECT *, {LABEL_SQL} AS label FROM products
                WHERE series_id=? AND stock_form='confirmed_design' AND id<>?
                ORDER BY design_name COLLATE NOCASE, id""", (series_id, head['id'] if head else 0)))
    return members


def _names(con, subjects):
    subjects = sorted({subject for subject in subjects if subject})
    if not subjects:
        return {}
    marks = ','.join('?' for _ in subjects)
    return {row['auth0_id']: row['name'] for row in con.execute(
        f'SELECT auth0_id, name FROM employees WHERE auth0_id IN ({marks})', subjects)}


def _image(con, product_id):
    row = con.execute("""SELECT filename FROM hidden_images WHERE product_id=?
                         ORDER BY CASE WHEN image_type='general' THEN 0 ELSE 1 END, id LIMIT 1""",
                      (product_id,)).fetchone()
    return row['filename'] if row else None


def _saved_counts(con, location_id, product_ids):
    if not product_ids:
        return {}
    marks = ','.join('?' for _ in product_ids)
    return {row['product_id']: dict(row) for row in con.execute(
        f'SELECT * FROM opening_counts WHERE location_id=? AND product_id IN ({marks})',
        (location_id, *product_ids))}


def _read(con, work):
    """Run a read with one consistent snapshot."""
    con.execute('BEGIN')
    try:
        return work()
    finally:
        if con.in_transaction:
            con.rollback()


def count_scope(con, actor, store_code):
    """The store's locations, how many products each has counted, and what the actor may do."""
    def work():
        scope = _scope(con, actor, store_code)
        counted = {} if scope['live'] else {row[0]: row[1] for row in con.execute(
            'SELECT location_id, COUNT(*) FROM opening_counts oc JOIN inventory_locations l ON l.id=oc.location_id '
            'WHERE l.store_id=? GROUP BY location_id', (scope['store']['id'],))}
        return {'store': scope['store'], 'live': scope['live'],
                'locations': [{**location, 'counted': counted.get(location['id'], 0)}
                              for location in scope['locations']]}
    return _read(con, work)


def search_targets(con, actor, store_code, location_code, query):
    """Products to count matching a name, 记账名, SKU, design name, alias or an exact barcode,
    one result per card (a design or sealed set finds its blind box)."""
    text = (query or '').strip()

    def work():
        scope = _scope(con, actor, store_code, location_code)
        if not text:
            return {'results': []}
        needle = normalize_design_name(text)
        scanned = {row[0] for row in con.execute('SELECT product_id FROM product_barcodes WHERE code=?', (text,))}
        owners = {row['id']: row['product_id'] for row in con.execute('SELECT id, product_id FROM product_series')}
        owned = {product_id: series_id for series_id, product_id in owners.items() if product_id}
        set_box = {row[0]: row[1] for row in con.execute(
            """SELECT c.source_product_id, c.target_product_id FROM product_conversions c
               JOIN products s ON s.id=c.source_product_id WHERE s.stock_form='sealed_set'
               ORDER BY c.version""")}
        products = {}
        hits = []
        for row in con.execute(
                f"""SELECT p.id, p.sku, p.jizhanming, p.name_cn_en, p.design_name, p.series_id, p.stock_form,
                           {product_alias_text_sql('p')} AS alias_text, ps.name AS series_name
                    FROM products p LEFT JOIN product_series ps ON ps.id=p.series_id"""):
            products[row['id']] = row
            exact = row['id'] in scanned or (row['sku'] or '').casefold() == text.casefold()
            haystack = normalize_design_name(' '.join(str(row[field] or '') for field in (
                'sku', 'jizhanming', 'name_cn_en', 'design_name', 'alias_text', 'series_name')))
            if exact or needle in haystack:
                hits.append((0 if exact else 1 if haystack.startswith(needle) else 2, row['id']))

        def card_of(product_id):
            product = products[product_id]
            if product['stock_form'] == 'sealed_set' and product_id in set_box and set_box[product_id] in products:
                product_id = set_box[product_id]
                product = products[product_id]
            series_id = product['series_id'] or owned.get(product_id)
            if series_id is not None and owners.get(series_id) is None:
                return ('series', series_id), product_id
            if series_id is not None and owners[series_id] != product_id:
                return ('product', owners[series_id]), owners[series_id]
            return ('product', product_id), product_id

        cards = {}
        for _, product_id in sorted(hits):
            key, anchor = card_of(product_id)
            cards.setdefault(key, anchor)
            if len(cards) >= SEARCH_LIMIT:
                break
        results = []
        for anchor in cards.values():
            head, series_id = _family(con, anchor)
            members = _members(con, head, series_id)
            if not members:
                continue
            ids = [product['id'] for _, product, _ in members]
            counted = 0 if scope['live'] else len(_saved_counts(con, scope['location']['id'], ids))
            series_name = con.execute('SELECT name FROM product_series WHERE id=?', (series_id,)).fetchone() \
                if series_id is not None and head is None else None
            results.append({
                'product_id': anchor,
                'title': head['label'] if head is not None else (series_name['name'] if series_name else ''),
                'sku': head['sku'] if head is not None else None,
                'kind': members[0][0] if head is not None else 'series',
                'sets': sum(1 for role, *_ in members if role == 'set'),
                'designs': sum(1 for role, *_ in members if role == 'design'),
                'members': len(members), 'counted': counted,
                'image': _image(con, head['id']) if head is not None else None,
            })
        return {'results': results}
    return _read(con, work)


def count_card(con, actor, store_code, location_code, product_id):
    """Everything counted on one card, with what was already counted here before go-live."""
    def work():
        scope = _scope(con, actor, store_code, location_code)
        head, series_id = _family(con, product_id)
        members = _members(con, head, series_id)
        if not members:
            raise InventoryError('This series has nothing to count yet; add its designs first', 'not_found', 404)
        location = scope['location']
        saved = {} if scope['live'] else _saved_counts(con, location['id'], [p['id'] for _, p, _ in members])
        series = con.execute('SELECT id, name, designs_checked_at, designs_checked_by FROM product_series WHERE id=?',
                             (series_id,)).fetchone() if series_id is not None else None
        names = _names(con, [count['counted_by'] for count in saved.values()]
                       + [series['designs_checked_by'] if series else None])
        items = []
        for role, product, boxes_per_set in members:
            item = {
                'product_id': product['id'], 'role': role, 'sku': product['sku'], 'name': product['label'],
                'design_name': product['design_name'], 'stock_form': product['stock_form'],
                'stock_unit': product['stock_unit'], 'identity_status': product['identity_status'],
                'image': _image(con, product['id']),
            }
            if boxes_per_set is not None:
                item['boxes_per_set'] = boxes_per_set
            if product['identity_status'] != 'verified':
                item['suggested_form'] = _suggested_form(product)
            if scope['live']:
                # Blind: the version only lets the save notice stock moving during the count.
                item['balance_version'] = _balance(con, product['id'], location['id'], 'saleable')['version']
            elif product['id'] in saved:
                count = saved[product['id']]
                item['count'] = {'quantity': count['quantity'], 'counted_at': count['counted_at'],
                                 'counted_by': names.get(count['counted_by']) or 'someone'}
            items.append(item)
        designs_checked = None
        if series is not None and series['designs_checked_at']:
            designs_checked = {'at': series['designs_checked_at'],
                               'by': names.get(series['designs_checked_by']) or 'someone'}
        return {
            'live': scope['live'], 'store': scope['store'], 'location': location,
            'product_id': head['id'] if head is not None else members[0][1]['id'],
            'title': head['label'] if head is not None else series['name'],
            'series_id': series_id, 'designs_checked': designs_checked, 'items': items,
        }
    return _read(con, work)


def _count_form(product, entry):
    """The stock form a count records: a verified product's own, or the form an unverified
    product is counted as (blind box or item), which going live then verifies."""
    if product['identity_status'] == 'verified':
        if entry['stock_form'] and entry['stock_form'] != product['stock_form']:
            raise InventoryConflict(f"{product['label']} is set up as {product['stock_form']}; "
                                    'change it in Products', 'identity_changed')
        return product['stock_form']
    form = entry['stock_form'] or _suggested_form(product)
    if form not in COUNT_VERIFIABLE_FORMS:
        raise InventoryValidationError(f"Choose blind box or item for {product['label']}", 'identity_unverified')
    if product['stock_form'] and form != product['stock_form']:
        raise InventoryConflict(f"{product['label']} is set up as {product['stock_form']}; "
                                'change it in Products', 'identity_changed')
    return form


def _read_entries(data):
    raw = data.get('entries')
    if not isinstance(raw, list) or not raw or len(raw) > MAX_ENTRIES or any(not isinstance(e, dict) for e in raw):
        raise InventoryValidationError('Enter at least one count')
    entries, seen = [], set()
    for item in raw:
        product_id = read_int(item.get('product_id'), 'product_id', minimum=1)
        if product_id in seen:
            raise InventoryValidationError('Count each product once per save')
        seen.add(product_id)
        quantity = read_int(item.get('quantity'), 'quantity', minimum=0)
        if quantity > MAX_QUANTITY:
            raise InventoryValidationError(f'A count cannot be more than {MAX_QUANTITY}')
        form = item.get('stock_form')
        if form is not None and form not in FORMS_TO_UNITS:
            raise InventoryValidationError('stock_form is invalid')
        version = item.get('balance_version')
        entries.append({'product_id': product_id, 'quantity': quantity, 'stock_form': form,
                        'balance_version': None if version is None else read_int(version, 'balance_version', minimum=0)})
    return entries


def _record_opening(con, actor, store_id, location, members, entries):
    """Before go-live: keep the latest count of each product here, and a line of history."""
    column = legacy_column(location['code']) if trusts_legacy(location['code']) else None
    results = []
    for entry in entries:
        product = members[entry['product_id']][1]
        form = _count_form(product, entry)
        previous = con.execute('SELECT quantity FROM opening_counts WHERE location_id=? AND product_id=?',
                               (location['id'], product['id'])).fetchone()
        con.execute(
            """INSERT INTO opening_counts(location_id, product_id, stock_form, quantity, counted_by, counted_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(location_id, product_id) DO UPDATE SET stock_form=excluded.stock_form,
                   quantity=excluded.quantity, counted_by=excluded.counted_by, counted_at=excluded.counted_at""",
            (location['id'], product['id'], form, entry['quantity'], actor['sub']))
        con.execute(
            """INSERT INTO opening_count_events(location_id, product_id, quantity, previous_quantity, counted_by)
               VALUES (?, ?, ?, ?, ?)""",
            (location['id'], product['id'], entry['quantity'], previous['quantity'] if previous else None, actor['sub']))
        legacy = con.execute(f'SELECT {column} FROM stock WHERE product_id=? AND store_id=?',
                             (product['id'], store_id)).fetchone() if column else None
        results.append({'product_id': product['id'], 'quantity': entry['quantity'],
                        'previous_count': previous['quantity'] if previous else None,
                        'popcore_quantity': (legacy[0] if legacy else 0) if column else None})
    return results


def _correct_stock(con, actor, store_id, location, members, entries, request_key):
    """A live store: post the differences as one correction, kept as an approved count."""
    today = business_date()
    count_id = con.execute(
        """INSERT INTO inventory_counts
           (store_id, location_id, disposition, business_date, status, created_by, reviewed_by, review_reason)
           VALUES (?, ?, 'saleable', ?, 'approved', ?, ?, ?)""",
        (store_id, location['id'], today, actor['sub'], actor['sub'], DIRECT_COUNT_REASON)).lastrowid
    lines, results = [], []
    for line_no, entry in enumerate(entries, 1):
        product = members[entry['product_id']][1]
        form = _count_form(product, entry)
        if product['identity_status'] != 'verified':
            # Counting verifies, as it does on count day.
            update_product_identity(con, product['id'], {
                'stock_form': form, 'stock_unit': FORMS_TO_UNITS[form], 'identity_status': 'verified'})
            product = _product(con, product['id'])
        current = _balance(con, product['id'], location['id'], 'saleable')
        if entry['balance_version'] is not None and entry['balance_version'] != current['version']:
            raise InventoryConflict('Stock changed while you were counting (a sale or a move); count it again',
                                    'count_stale')
        difference = entry['quantity'] - current['quantity']
        if difference < 0:
            protected = con.execute(
                """SELECT COALESCE(SUM(remaining_qty), 0) FROM inventory_open_sets
                   WHERE random_product_id=? AND location_id=? AND remaining_qty>0""",
                (product['id'], location['id'])).fetchone()[0]
            if -difference > max(0, current['quantity'] - protected):
                raise InventoryConflict('This count would take boxes kept for an opened set; '
                                        'a manager needs to settle the opened set first',
                                        'provenance_reconciliation_required')
        if difference:
            endpoint = 'to' if difference > 0 else 'from'
            lines.append({'product_id': product['id'], 'unit': product['stock_unit'], 'quantity': abs(difference),
                          f'{endpoint}_location_id': location['id'], f'{endpoint}_disposition': 'saleable',
                          'expected_versions': {endpoint: current['version']}})
        con.execute(
            """INSERT INTO inventory_count_lines
               (count_id, line_no, product_id, native_unit, expected_quantity, observed_quantity,
                captured_balance_version) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (count_id, line_no, product['id'], product['stock_unit'], current['quantity'],
             entry['quantity'], current['version']))
        results.append({'product_id': product['id'], 'quantity': entry['quantity'],
                        'previous_count': None, 'popcore_quantity': current['quantity']})
    if lines:
        # Anyone who may count at a live store corrects it at once; managers look over the
        # differences afterwards instead of approving each count first (owner, 2026-10-27).
        document = _post_inventory_in_transaction(con, {
            'kind': 'correction', 'business_date': today, 'source_type': 'direct_count',
            'source_id': str(count_id), 'reason': DIRECT_COUNT_REASON, 'lines': lines,
        }, actor=actor, request_key=f'inventory:{request_key}', minimum_role='staff')
        con.execute('UPDATE inventory_counts SET inventory_document_id=? WHERE id=?',
                    (document['document_id'], count_id))
    return results


def save_counts(con, actor, store_code, location_code, data, *, request_key):
    """Save one card. Saving again replaces the count; a design count needs the series'
    designs checked first, in this save or an earlier one."""
    if not isinstance(data, dict):
        raise InventoryValidationError('Send the counts as JSON')
    product_id = read_int(data.get('product_id'), 'product_id', minimum=1)
    entries = _read_entries(data)
    designs_checked = data.get('designs_checked')
    if designs_checked not in (None, True, False):
        raise InventoryValidationError('designs_checked must be true or false')
    intent = {'store_code': (store_code or '').strip().upper(), 'location': (location_code or '').strip().lower(),
              'product_id': product_id, 'entries': entries, 'designs_checked': designs_checked}
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'count_save', actor, intent)
        if prior is not None:
            con.commit()
            return prior
        scope = _scope(con, actor, store_code, location_code)
        head, series_id = _family(con, product_id)
        members = {product['id']: (role, product) for role, product, _ in _members(con, head, series_id)}
        if any(entry['product_id'] not in members for entry in entries):
            raise InventoryValidationError('Count only this product, its sealed sets and its designs')
        if series_id is not None and designs_checked:
            con.execute("UPDATE product_series SET designs_checked_at=datetime('now'), designs_checked_by=? WHERE id=?",
                        (actor['sub'], series_id))
        if any(members[entry['product_id']][0] == 'design' for entry in entries) and not con.execute(
                'SELECT designs_checked_at FROM product_series WHERE id=?', (series_id,)).fetchone()[0]:
            raise InventoryValidationError('Check the designs are right before counting them', 'designs_unchecked')
        record = _correct_stock if scope['live'] else _record_opening
        kwargs = {'request_key': key} if scope['live'] else {}
        results = record(con, actor, scope['store']['id'], scope['location'], members, entries, **kwargs)
        response = {'live': scope['live'], 'results': results}
        _remember(con, key, 'count_save', product_id, actor, digest, response)
        con.commit()
        return response
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def count_progress(con, actor, store_code, location_code):
    """How much of a location is counted, and before go-live what still has POPCORE stock here
    but no count. Uncounted verified products would stop the store going live."""
    def work():
        scope = _scope(con, actor, store_code, location_code)
        location = scope['location']
        if scope['live']:
            counted = con.execute(
                """SELECT COUNT(DISTINCT line.product_id) FROM inventory_counts c
                   JOIN inventory_count_lines line ON line.count_id=c.id
                   WHERE c.location_id=? AND c.business_date=? AND c.review_reason=?""",
                (location['id'], business_date(), DIRECT_COUNT_REASON)).fetchone()[0]
            return {'live': True, 'counted': counted, 'missing': [], 'missing_total': 0}
        counted = con.execute('SELECT COUNT(*) FROM opening_counts WHERE location_id=?', (location['id'],)).fetchone()[0]
        missing = []
        if trusts_legacy(location['code']):
            column = legacy_column(location['code'])
            missing = [{'product_id': row['id'], 'name': row['label'], 'sku': row['sku'],
                        'blocks_opening': row['identity_status'] == 'verified'}
                       for row in con.execute(
                           f"""SELECT p.id, p.sku, p.identity_status, {LABEL_SQL} AS label
                               FROM stock st JOIN products p ON p.id=st.product_id
                               WHERE st.store_id=? AND st.{column} != 0
                                 AND NOT EXISTS (SELECT 1 FROM opening_counts oc
                                                 WHERE oc.location_id=? AND oc.product_id=p.id)
                               ORDER BY label COLLATE NOCASE, p.id""",
                           (scope['store']['id'], location['id']))]
        return {'live': False, 'counted': counted, 'missing': missing[:MISSING_LIMIT],
                'missing_total': len(missing)}
    return _read(con, work)
