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
from inventory_catalog import sealed_set_for
from inventory_commands import (
    InventoryConflict, InventoryError, InventoryValidationError,
    _post_inventory_in_transaction, require_inventory_access, store_is_live,
)
from inventory_opening import COUNT_VERIFIABLE_FORMS, legacy_column, trusts_legacy
from sealed_sets import SET_SUFFIX
from store_access import can_write_legacy_store
from validation import read_int

SEARCH_LIMIT = 20
MISSING_LIMIT = 200
MAX_ENTRIES = 300
MAX_QUANTITY = 100000
DIRECT_COUNT_REASON = '盘点 Count'
LABEL_SQL = "COALESCE(NULLIF(jizhanming,''), NULLIF(name_cn_en,''), sku)"
FORM_NAMES = {'random_box': '盲盒 blind box', 'ordinary': '单品 item'}


class CountStale(InventoryConflict):
    """What a card showed changed while it was open; names the products to count again."""
    def __init__(self, message, product_ids):
        super().__init__(message, 'count_stale')
        self.details = {'product_ids': sorted(product_ids)}


def _listed(members, product_ids):
    names = [members[product_id][1]['label'] for product_id in product_ids]
    return ', '.join(names[:3]) + (f' and {len(names) - 3} more' if len(names) > 3 else '')


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
        try:
            require_inventory_access(con, actor, (store['id'],), 'staff')
        except PermissionError:
            raise PermissionError(f"{store['code']} is live: counting there needs 门店操作权限 Store operations "
                                  f"access for {store['code']}. Ask an admin to turn it on in Users.") from None
    elif not can_write_legacy_store(con, actor, store['id']):
        raise PermissionError(f"To count at {store['code']} you need a shift there today, {store['code']} on your "
                              'staff profile, or 门店操作权限 Store operations access. Ask a manager or an admin.')
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
    owns its series; a series no product owns is its designs alone. Anything else heads its
    own card, also inside another product's series, so every product is on a card."""
    product = _product(con, read_int(product_id, 'product_id', minimum=1))
    if product['stock_form'] == 'sealed_set':
        box = con.execute('SELECT target_product_id FROM product_conversions WHERE source_product_id=? '
                          'ORDER BY version DESC LIMIT 1', (product['id'],)).fetchone()
        if box:
            product = _product(con, box[0])
    if product['stock_form'] == 'confirmed_design' and product['series_id'] is not None:
        owner = con.execute('SELECT product_id FROM product_series WHERE id=?', (product['series_id'],)).fetchone()
        if owner is None or owner['product_id'] is None:
            return None, product['series_id']
        if owner['product_id'] != product['id']:
            return _product(con, owner['product_id']), product['series_id']
    owned = con.execute('SELECT id FROM product_series WHERE product_id=?', (product['id'],)).fetchone()
    return product, owned['id'] if owned else None


def _type_form(product):
    """The form a product's type names (盲盒 → blind box, 吊卡 → item ...), or None."""
    return (SUGGESTED_IDENTITY.get((product['product_type'] or '').strip()) or (None, None))[0]


def _suggested_form(product):
    return product['stock_form'] or _type_form(product)


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
    """Each product's count here, versioned by the history line that saved it."""
    if not product_ids:
        return {}
    marks = ','.join('?' for _ in product_ids)
    return {row['product_id']: dict(row) for row in con.execute(
        f"""SELECT oc.*, (SELECT MAX(e.id) FROM opening_count_events e
                          WHERE e.location_id=oc.location_id AND e.product_id=oc.product_id) AS version
            FROM opening_counts oc WHERE oc.location_id=? AND oc.product_id IN ({marks})""",
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
            # As _family: a sealed set finds its blind box and a confirmed design its series' owner.
            product = products[product_id]
            if product['stock_form'] == 'sealed_set' and product_id in set_box and set_box[product_id] in products:
                product_id = set_box[product_id]
                product = products[product_id]
            series_id = product['series_id']
            if product['stock_form'] == 'confirmed_design' and series_id is not None:
                owner = owners.get(series_id)
                if owner is None:
                    return ('series', series_id), product_id
                return ('product', owner), owner
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
            counted = 0 if scope['live'] else con.execute(
                f"SELECT COUNT(*) FROM opening_counts WHERE location_id=? AND product_id IN ({','.join('?' * len(ids))})",
                (scope['location']['id'], *ids)).fetchone()[0]
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
                # Going live verifies one form per product, so a recount keeps the one already counted.
                recorded = None if scope['live'] else con.execute(
                    'SELECT stock_form FROM opening_counts WHERE product_id=? ORDER BY location_id=? DESC LIMIT 1',
                    (product['id'], location['id'])).fetchone()
                if recorded:
                    item['counted_form'] = recorded[0]
            if scope['live']:
                # Blind: the version only lets the save notice stock moving during the count.
                item['balance_version'] = _balance(con, product['id'], location['id'], 'saleable')['version']
                other = [dict(row) for row in con.execute(
                    """SELECT disposition, quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                       AND disposition<>'saleable' AND quantity<>0 ORDER BY disposition""",
                    (product['id'], location['id']))]
                if other:
                    # Held or damaged units are not counted here: the count replaces sellable stock only.
                    item['other_stock'] = other
            elif product['id'] in saved:
                count = saved[product['id']]
                item['count'] = {'quantity': count['quantity'], 'counted_at': count['counted_at'],
                                 'counted_by': names.get(count['counted_by']) or 'someone',
                                 'version': count['version']}
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
            'new_sealed_set': _whole_set_offer(con, head, members),
        }
    return _read(con, work)


def _whole_set_offer(con, head, members):
    """A blind box always counts whole sets and single boxes (owner, 2026-10-27). One with no
    sealed set yet offers it here, with boxes per set from the product, and the save creates it.
    An untyped product offers it too, for when the counter says it is a blind box. The set's SKU
    is the box's plus -SET, so a box without a SKU, or whose -SET SKU is taken, has none."""
    if head is None or any(role == 'set' for role, _, _ in members):
        return None
    if head['stock_form'] not in (None, 'random_box') or _suggested_form(head) not in (None, 'random_box'):
        return None
    if not head['sku'] or con.execute('SELECT 1 FROM products WHERE sku=?', (f"{head['sku']}{SET_SUFFIX}",)).fetchone():
        return None
    return {'boxes_per_set': head['boxes_per_dan'] if (head['boxes_per_dan'] or 0) > 1 else None}


def _read_new_set(data):
    new_set = data.get('new_sealed_set')
    if new_set is None:
        return None
    if not isinstance(new_set, dict) or set(new_set) != {'quantity', 'boxes_per_set'}:
        raise InventoryValidationError('new_sealed_set needs quantity and boxes_per_set')
    boxes = read_int(new_set['boxes_per_set'], 'boxes_per_set', minimum=2)
    if boxes > 100:
        raise InventoryValidationError('A whole set cannot hold more than 100 boxes')
    quantity = read_int(new_set['quantity'], 'quantity', minimum=1)
    if quantity > MAX_QUANTITY:
        raise InventoryValidationError(f'A count cannot be more than {MAX_QUANTITY}')
    return {'quantity': quantity, 'boxes_per_set': boxes}


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


def _read_entries(data, allow_empty=False):
    raw = data.get('entries')
    if raw is None and allow_empty:
        raw = []
    if (not isinstance(raw, list) or (not raw and not allow_empty) or len(raw) > MAX_ENTRIES
            or any(not isinstance(e, dict) for e in raw)):
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
        shown = item.get('count_version')
        entries.append({'product_id': product_id, 'quantity': quantity, 'stock_form': form,
                        'balance_version': None if version is None else read_int(version, 'balance_version', minimum=0),
                        # The saved count the card showed (null: none); a newer one is someone else's.
                        'checks_count': 'count_version' in item,
                        'count_version': None if shown is None else read_int(shown, 'count_version', minimum=1)})
    return entries


def _same_form(con, product, location, form):
    """Going live verifies one form per product, so an unverified product counted elsewhere
    as another form would stop the opening. The product type decides (owner, 2026-10-27): a count
    in the form its type names goes through, and counts elsewhere in another form are counted
    again (重点 Recount)."""
    if form == _type_form(product):
        return
    other = con.execute(
        """SELECT oc.stock_form, s.code || ' ' || l.name AS place FROM opening_counts oc
           JOIN inventory_locations l ON l.id=oc.location_id JOIN stores s ON s.id=l.store_id
           WHERE oc.product_id=? AND oc.location_id<>? AND oc.stock_form<>? LIMIT 1""",
        (product['id'], location['id'], form)).fetchone()
    if other:
        raise InventoryConflict(
            f"{product['label']} was counted as {FORM_NAMES.get(other['stock_form'], other['stock_form'])} "
            f"at {other['place']}. Count it the same way, or ask a manager to set it up in Products.",
            'identity_conflict')


def _record_opening(con, actor, store_id, location, members, entries):
    """Before go-live: keep the latest count of each product here, and a line of history."""
    saved = _saved_counts(con, location['id'], [entry['product_id'] for entry in entries])
    stale = [entry['product_id'] for entry in entries if entry['checks_count']
             and (saved.get(entry['product_id']) or {}).get('version') != entry['count_version']]
    if stale:
        raise CountStale(f'Someone else saved a count of {_listed(members, stale)} while this card was open. '
                         'Check it and save again.', stale)
    column = legacy_column(location['code']) if trusts_legacy(location['code']) else None
    boxed = any(role == 'set' for role, _, _ in members.values())
    results = []
    for entry in entries:
        role, product, _ = members[entry['product_id']]
        form = _count_form(product, entry)
        if product['identity_status'] != 'verified':
            _same_form(con, product, location, form)
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
        # POPCORE kept a blind box's whole sets as boxes, so with whole sets only the box total compares.
        compared = column and role != 'set' and not (role == 'box' and boxed)
        legacy = con.execute(f'SELECT {column} FROM stock WHERE product_id=? AND store_id=?',
                             (product['id'], store_id)).fetchone() if compared else None
        results.append({'product_id': product['id'], 'quantity': entry['quantity'],
                        'previous_count': previous['quantity'] if previous else None,
                        'popcore_quantity': (legacy[0] if legacy else 0) if compared else None})
    outcome = {'results': results}
    head = next((product for role, product, _ in members.values() if role == 'box'), None)
    if column and boxed and head is not None:
        per_set = {product_id: boxes for product_id, (role, _, boxes) in members.items() if role == 'set'}
        ids = [head['id'], *per_set]
        marks = ','.join('?' * len(ids))
        counts = {row[0]: row[1] for row in con.execute(
            f'SELECT product_id, quantity FROM opening_counts WHERE location_id=? AND product_id IN ({marks})',
            (location['id'], *ids))}
        legacy = {row[0]: row[1] for row in con.execute(
            f'SELECT product_id, {column} FROM stock WHERE store_id=? AND product_id IN ({marks})', (store_id, *ids))}
        if head['id'] in counts:
            outcome['boxes'] = {
                'popcore': legacy.get(head['id'], 0) + sum(legacy.get(i, 0) * boxes for i, boxes in per_set.items()),
                'counted': counts[head['id']] + sum(counts.get(i, 0) * boxes for i, boxes in per_set.items()),
            }
    return outcome


def _correct_stock(con, actor, store_id, location, members, entries, request_key):
    """A live store: post the differences as one correction, kept as an approved count."""
    balances = {entry['product_id']: _balance(con, entry['product_id'], location['id'], 'saleable')
                for entry in entries}
    if any(entry['balance_version'] is None for entry in entries):
        # A card opened before the store went live has no balances to count against.
        raise CountStale('This store went live while the card was open. Count it again.',
                         [entry['product_id'] for entry in entries])
    stale = [entry['product_id'] for entry in entries
             if entry['balance_version'] != balances[entry['product_id']]['version']]
    if stale:
        raise CountStale(f'Stock of {_listed(members, stale)} changed while you were counting (a sale or a move). '
                         'Count it again.', stale)
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
        current = balances[product['id']]
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
    return {'results': results}


def save_counts(con, actor, store_code, location_code, data, *, request_key):
    """Save one card. Saving again replaces the count; a design count needs the series'
    designs checked first, in this save or an earlier one."""
    if not isinstance(data, dict):
        raise InventoryValidationError('Send the counts as JSON')
    product_id = read_int(data.get('product_id'), 'product_id', minimum=1)
    new_set = _read_new_set(data)
    entries = _read_entries(data, allow_empty=new_set is not None)
    designs_checked = data.get('designs_checked')
    if designs_checked not in (None, True, False):
        raise InventoryValidationError('designs_checked must be true or false')
    intent = {'store_code': (store_code or '').strip().upper(), 'location': (location_code or '').strip().lower(),
              'product_id': product_id, 'entries': entries, 'designs_checked': designs_checked,
              **({'new_sealed_set': new_set} if new_set else {})}
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'count_save', actor, intent)
        if prior is not None:
            con.commit()
            return prior
        scope = _scope(con, actor, store_code, location_code)
        location = scope['location']
        head, series_id = _family(con, product_id)
        if new_set:
            current = _members(con, head, series_id)
            existing = [(product['id'], boxes) for role, product, boxes in current if role == 'set']
            created = not existing
            if existing:
                # Two people counted the same blind box and the other save added its whole set a
                # moment ago: before go-live count against that set when it holds the same number
                # of boxes; once live its balance may have moved since, so it is counted again.
                set_id, boxes = existing[0]
                if boxes != new_set['boxes_per_set']:
                    raise InventoryConflict(f"This box's whole set was just added with {boxes} boxes per set, "
                                            f"not {new_set['boxes_per_set']}; check it and count again",
                                            'whole_set_changed')
                if scope['live']:
                    raise CountStale(f"Someone just added the whole set of {head['label']}. "
                                     'Count the whole sets again.', [set_id])
            elif _whole_set_offer(con, head, current) is None:
                raise InventoryConflict('Whole sets are counted for a blind box; this product is not one',
                                        'whole_set_unavailable')
            else:
                if not scope['live'] and head['identity_status'] != 'verified':
                    _same_form(con, head, location, 'random_box')  # the set makes it a verified blind box
                # Counting the first whole sets of a blind box gives it its sealed set (owner, 2026-10-27).
                set_id = sealed_set_for(con, head['id'], new_set['boxes_per_set'])
                head = _product(con, head['id'])
            if all(entry['product_id'] != set_id for entry in entries):
                # The card showed no set: someone else's count of it here since then is theirs to
                # keep, and once live only a set this save added has a balance known to be untouched.
                version = _balance(con, set_id, location['id'], 'saleable')['version'] if created else None
                entries = [*entries, {'product_id': set_id, 'quantity': new_set['quantity'], 'stock_form': None,
                                      'balance_version': version, 'checks_count': True, 'count_version': None}]
        members = {product['id']: (role, product, boxes) for role, product, boxes in _members(con, head, series_id)}
        if any(entry['product_id'] not in members for entry in entries):
            raise InventoryConflict('This card changed while you were counting (a design was added or removed). '
                                    'Check it and save again.', 'card_changed')
        if (not scope['live'] and head is not None and all(entry['product_id'] != head['id'] for entry in entries)
                and not con.execute('SELECT 1 FROM opening_counts WHERE location_id=? AND product_id=?',
                                    (location['id'], head['id'])).fetchone()):
            # A blank box count next to whole sets or designs would leave POPCORE's boxes here uncounted.
            raise InventoryValidationError(
                '散盒 Enter the single boxes too, 0 if there are none.' if members[head['id']][0] == 'box'
                else f"Enter the count of {head['label']} too, 0 if there are none.", 'head_uncounted')
        if series_id is not None and designs_checked:
            con.execute("UPDATE product_series SET designs_checked_at=datetime('now'), designs_checked_by=? WHERE id=?",
                        (actor['sub'], series_id))
        if any(members[entry['product_id']][0] == 'design' for entry in entries) and not con.execute(
                'SELECT designs_checked_at FROM product_series WHERE id=?', (series_id,)).fetchone()[0]:
            raise InventoryValidationError('Check the designs are right before counting them', 'designs_unchecked')
        record = _correct_stock if scope['live'] else _record_opening
        kwargs = {'request_key': key} if scope['live'] else {}
        outcome = record(con, actor, scope['store']['id'], location, members, entries, **kwargs)
        response = {'live': scope['live'], **outcome}
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
        # Counted as a form its product no longer has (set up or typed otherwise in Products since):
        # going live stops on these until they are counted again.
        missing = [{'product_id': row['id'], 'name': row['label'], 'sku': row['sku'], 'blocks_opening': True,
                    'recount': True}
                   for row in con.execute(
                       f"""SELECT p.id, p.sku, p.stock_form, p.product_type, oc.stock_form AS counted_form,
                                  {LABEL_SQL} AS label FROM opening_counts oc
                           JOIN products p ON p.id=oc.product_id
                           WHERE oc.location_id=? ORDER BY label COLLATE NOCASE, p.id""", (location['id'],))
                   if (row['stock_form'] or _type_form(row) or row['counted_form']) != row['counted_form']]
        if trusts_legacy(location['code']):
            column = legacy_column(location['code'])
            # As going live checks: POPCORE stock here of a verified product, or of one counted
            # anywhere in this store (going live verifies it), has to be counted here too.
            missing += [{'product_id': row['id'], 'name': row['label'], 'sku': row['sku'],
                         'blocks_opening': bool(row['blocks'])}
                        for row in con.execute(
                            f"""SELECT p.id, p.sku, {LABEL_SQL} AS label,
                                       p.identity_status='verified' OR EXISTS (
                                           SELECT 1 FROM opening_counts oc
                                           JOIN inventory_locations l ON l.id=oc.location_id
                                           WHERE l.store_id=st.store_id AND oc.product_id=p.id) AS blocks
                                FROM stock st JOIN products p ON p.id=st.product_id
                                WHERE st.store_id=? AND st.{column} != 0
                                  AND NOT EXISTS (SELECT 1 FROM opening_counts oc
                                                  WHERE oc.location_id=? AND oc.product_id=p.id)
                                ORDER BY label COLLATE NOCASE, p.id""",
                            (scope['store']['id'], location['id']))]
        return {'live': False, 'counted': counted, 'missing': missing[:MISSING_LIMIT],
                'missing_total': len(missing)}
    return _read(con, work)
