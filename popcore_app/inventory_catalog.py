"""Series rosters over the existing product and inventory ledger identities.

A series normally belongs to one product (product_series.product_id), e.g. a blind box: its sealed set
and confirmed designs are that product's sub-items, added with add_designs / add_sealed_set.
"""
import re
import sqlite3
import uuid

from catalog_identity import (
    FORMS_TO_UNITS, CatalogConflict, normalize_design_name, product_alias_text_sql, update_product_identity,
)
from goods_operations import _begin, _replay, _remember
from sealed_sets import create_sealed_set, sealed_set_item
from inventory_commands import (
    INVENTORY_DISPOSITIONS, InventoryConflict, InventoryError,
    InventoryValidationError, inventory_mode_for, require_inventory_access,
)
from validation import read_int


def _locations(con, actor, store_code):
    code = (store_code or '').strip().upper()
    if code == 'ALL':
        stores = [row['id'] for row in con.execute(
            '''SELECT DISTINCT s.id FROM stores s
               JOIN inventory_access a ON a.store_id=s.id
               JOIN inventory_locations l ON l.store_id=s.id AND l.is_active=1
               WHERE s.is_active=1 AND a.auth0_sub=?''', (actor.get('sub'),))]
    else:
        store = con.execute('SELECT id FROM stores WHERE code=?', (code,)).fetchone()
        if store is None:
            raise InventoryValidationError('Valid store_code is required')
        stores = [store['id']]
    require_inventory_access(con, actor, stores, 'viewer')
    marks = ','.join('?' for _ in stores)
    return [{**dict(row), 'opening_verified': bool(row['opening_verified'])}
            for row in con.execute(
                f'''SELECT l.id,l.store_id,s.code AS store_code,l.code,l.name,
                           COALESCE(ss.opening_verified,0) AS opening_verified
                    FROM inventory_locations l JOIN stores s ON s.id=l.store_id
                    LEFT JOIN inventory_scope_state ss ON ss.location_id=l.id
                    WHERE l.is_active=1 AND l.store_id IN ({marks}) ORDER BY l.id''', stores)]


def series_inventory(con, actor, store_code, query='', series_id=None, product_id=None):
    """Series (a product we sell confirmed designs of) and plain products, each with its stock.

    Most products are plain and listed under `products`; a blind box's sealed set is its `variations`.
    `series_id` returns one series; `product_id` returns one plain product."""
    if series_id is not None:
        series_id = read_int(series_id, 'series_id', minimum=1)
    if product_id is not None:
        product_id = read_int(product_id, 'product_id', minimum=1)
    con.execute('BEGIN')
    locations = _locations(con, actor, store_code)
    mode = inventory_mode_for(con, {location['store_id'] for location in locations})
    # A series owned by a product is shown under that product's current name.
    roster = {row['id']: {**dict(row), 'products': []} for row in con.execute(
        '''SELECT ps.id,ps.product_id,
                  COALESCE((SELECT COALESCE(NULLIF(p.jizhanming,''),NULLIF(p.name_cn_en,''),p.sku)
                            FROM products p WHERE p.id=ps.product_id), ps.name) AS name
           FROM product_series ps WHERE ? IS NULL AND (? IS NULL OR ps.id=?) ORDER BY name,ps.id''',
        (product_id, series_id, series_id))}
    standalone = []
    marks = ','.join('?' for _ in locations)
    balances = {(row['product_id'], row['location_id'], row['disposition']): row
                for row in con.execute(
                    f'SELECT * FROM inventory_balances WHERE location_id IN ({marks})',
                    [location['id'] for location in locations])}
    opened_sets = {}
    if locations:
        for row in con.execute(
            f'''SELECT o.id,o.random_product_id,o.location_id,o.purpose,o.remaining_qty,o.opening_document_id
                FROM inventory_open_sets o JOIN inventory_scope_state ss ON ss.location_id=o.location_id
                WHERE o.location_id IN ({marks}) AND ss.opening_verified=1 AND o.remaining_qty>0 ORDER BY o.id''',
            [location['id'] for location in locations]):
            item = dict(row)
            opened_sets.setdefault(item.pop('random_product_id'), []).append(item)
    # A sealed set is its blind box's other variation (owner, 2026-10-26): it is shown with the box, never as its own
    # entry, whether or not the box is a series (a series means the box also sells confirmed designs).
    set_box = {row[0]: row[1] for row in con.execute(
        """SELECT c.source_product_id, c.target_product_id FROM product_conversions c
           JOIN products s ON s.id=c.source_product_id WHERE s.stock_form='sealed_set'""")}
    owner_series = {item['product_id']: item for item in roster.values() if item['product_id']}
    plain = {}
    loose_sets = []
    for row in con.execute(
        f'''SELECT id,sku,COALESCE(NULLIF(jizhanming,''),NULLIF(name_cn_en,''),sku) AS name,
                  jizhanming,name_cn_en,{product_alias_text_sql('products')} AS alias_text,
                  series_id,stock_form,stock_unit,design_name,identity_status,
                  (SELECT filename FROM hidden_images i WHERE i.product_id=products.id
                   ORDER BY CASE WHEN i.image_type='general' THEN 0 ELSE 1 END,i.id LIMIT 1) AS image_filename
           FROM products ORDER BY id'''):
        product = dict(row)
        is_loose_set = product['id'] in set_box and product['series_id'] is None
        if product['series_id'] is not None and product['series_id'] not in roster:
            continue
        if product['series_id'] is None and not is_loose_set and (series_id is not None or product_id not in (None, product['id'])):
            continue
        valid = (product['identity_status'] == 'verified'
                 and product['stock_unit'] is not None
                 and FORMS_TO_UNITS.get(product['stock_form']) == product['stock_unit']
                 and (product['stock_form'] != 'confirmed_design' or bool((product['design_name'] or '').strip())))
        product['open_sets'] = opened_sets.get(product['id'], []) if valid else []
        product['balances'] = []
        for location in locations:
            trusted = location['opening_verified'] and valid
            for disposition in sorted(INVENTORY_DISPOSITIONS):
                balance = balances.get((product['id'], location['id'], disposition))
                product['balances'].append({
                    'location_id': location['id'], 'disposition': disposition,
                    'quantity': (balance['quantity'] if balance else 0) if trusted else None,
                    'version': (balance['version'] if balance else 0) if trusted else None,
                })
        if is_loose_set:
            loose_sets.append(product)
        elif product['series_id'] is None:
            product['variations'] = []
            plain[product['id']] = product
        else:
            roster[product['series_id']]['products'].append(product)
    for item in loose_sets:
        box = set_box[item['id']]
        if box in owner_series:
            owner_series[box]['products'].append(item)
        elif box in plain:
            plain[box]['variations'].append(item)
    standalone = list(plain.values())
    needle = normalize_design_name(query)
    text = lambda products: [str(p.get(field) or '') for p in products
                             for field in ('name', 'sku', 'design_name', 'jizhanming', 'name_cn_en', 'alias_text')]
    series = [item for item in roster.values()
              if not needle or needle in normalize_design_name(' '.join([item['name'], *text(item['products'])]))]
    products = sorted((p for p in standalone if not needle or needle in normalize_design_name(' '.join(text([p, *p['variations']])))),
                      key=lambda p: (p['name'] or '').lower())
    return {'mode': mode, 'locations': locations, 'series': series, 'products': products,
            'unassigned_count': con.execute('SELECT COUNT(*) FROM products WHERE series_id IS NULL').fetchone()[0]}


def series_history(con, actor, store_code, series_id, *, product_id=None,
                   date_from=None, date_to=None, before_id=None, limit=100):
    from validation import read_date

    limit = min(read_int(limit, 'limit', minimum=1), 100)
    if product_id is not None:
        product_id = read_int(product_id, 'product_id', minimum=1)
    if before_id is not None:
        before_id = read_int(before_id, 'before_id', minimum=1)
    for field, value in (('date_from', date_from), ('date_to', date_to)):
        if value is not None and read_date(value, field) != value:
            raise ValueError(f'{field} must be an ISO date in YYYY-MM-DD format')
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError('date_from must be on or before date_to')
    con.execute('BEGIN')
    locations = _locations(con, actor, store_code)
    if not con.execute('SELECT 1 FROM product_series WHERE id=?', (series_id,)).fetchone():
        raise InventoryError('Series not found', 'not_found', 404)
    if product_id is not None and not con.execute(
            'SELECT 1 FROM products WHERE id=? AND series_id=?', (product_id, series_id)).fetchone():
        raise InventoryError('Product not found in this series', 'not_found', 404)
    marks = ','.join('?' for _ in locations)
    filters = [f"p.series_id=? AND m.location_id IN ({marks}) AND d.status='posted'"]
    params = [series_id, *[location['id'] for location in locations]]
    for expression, value in (('m.product_id=?', product_id), ('d.business_date>=?', date_from),
                              ('d.business_date<=?', date_to), ('m.id<?', before_id)):
        if value is not None:
            filters.append(expression)
            params.append(value)
    items = [dict(row) for row in con.execute(
        f'''SELECT m.id,m.document_id,d.kind,d.source_type,d.source_id,d.actor_sub,m.product_id,
                   CASE WHEN p.stock_form='confirmed_design' THEN ps.name || ' · ' || p.design_name
                        ELSE COALESCE(NULLIF(p.jizhanming,''),NULLIF(p.name_cn_en,''),p.sku) END AS product_name,
                   m.location_id,l.name AS location_name,s.code AS store_code,m.disposition,m.quantity,
                   d.business_date,d.posted_at,d.reason,line.open_set_id,
                   CASE WHEN m.product_id=line.product_id THEN line.native_unit
                        WHEN d.kind='open_set' AND m.product_id=conversion.target_product_id THEN 'box' END AS native_unit
            FROM inventory_movements m JOIN inventory_documents d ON d.id=m.document_id
            JOIN inventory_document_lines line ON line.document_id=m.document_id AND line.line_no=m.line_no
            LEFT JOIN product_conversions conversion ON conversion.id=line.conversion_id
            JOIN products p ON p.id=m.product_id JOIN product_series ps ON ps.id=p.series_id
            JOIN inventory_locations l ON l.id=m.location_id JOIN stores s ON s.id=l.store_id
            WHERE {' AND '.join(filters)} ORDER BY m.id DESC LIMIT ?''', (*params, limit + 1))]
    has_more = len(items) > limit
    items = items[:limit]
    return {'items': items, 'has_more': has_more,
            'next_before_id': items[-1]['id'] if has_more else None}


def setup_series(con, data, *, actor, request_key):
    if not isinstance(data, dict) or set(data) not in ({'name', 'design_names'}, {'series_id', 'design_names'}):
        raise InventoryValidationError('Supply name or series_id and design_names only')
    names = data['design_names']
    if (not isinstance(names, list) or not 1 <= len(names) <= 100
            or any(not isinstance(name, str) or not name.strip() or len(name.strip()) > 200 for name in names)):
        raise InventoryValidationError('Supply 1 to 100 nonempty design names, up to 200 characters each')
    names = [name.strip() for name in names]
    normalized = {normalize_design_name(name) for name in names}
    if len(normalized) != len(names):
        raise InventoryValidationError('Design names must be unique within a series')
    series_id = read_int(data['series_id'], 'series_id', minimum=1) if 'series_id' in data else None
    name = data.get('name')
    if series_id is None and (not isinstance(name, str) or not name.strip() or len(name.strip()) > 200):
        raise InventoryValidationError('Supply a series name, up to 200 characters')
    intent = {'design_names': names, **({'series_id': series_id} if series_id else {'name': name.strip()})}
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'series_setup', actor, intent)
        if prior is not None:
            con.commit()
            return prior
        if series_id is None:
            name = intent['name']
            if any(normalize_design_name(row['name']) == normalize_design_name(name)
                   for row in con.execute('SELECT name FROM product_series')):
                raise InventoryConflict('Series already exists; select it to add designs', 'series_conflict')
            series_id = con.execute('INSERT INTO product_series(name) VALUES (?)', (name,)).lastrowid
        else:
            series = con.execute('SELECT name FROM product_series WHERE id=?', (series_id,)).fetchone()
            if series is None:
                raise InventoryValidationError('series_id does not identify a series')
            name = series['name']
        for row in con.execute("SELECT design_name FROM products WHERE series_id=? AND stock_form='confirmed_design'", (series_id,)):
            if row['design_name'] and normalize_design_name(row['design_name']) in normalized:
                raise InventoryConflict('This series already contains that named design', 'design_conflict')
        product_ids = []
        for design in names:
            label = f'{name} · {design}'
            sku = f'DESIGN-{uuid.uuid4().hex}'
            product_ids.append(con.execute(
                '''INSERT INTO products(sku,jizhanming,name_cn_en,series_id,stock_form,stock_unit,
                                        design_name,identity_status,search_blob)
                   VALUES (?,?,?,?,'confirmed_design','piece',?,'verified',?)''',
                (sku, label, label, series_id, design, f'{label} {sku}'.lower())).lastrowid)
        result = {'id': series_id, 'name': name, 'product_ids': product_ids}
        _remember(con, key, 'series_setup', series_id, actor, digest, result)
        con.commit()
        return result
    except sqlite3.IntegrityError as exc:
        con.rollback()
        raise InventoryConflict('Could not save the roster; no designs were created', 'series_conflict') from exc
    except Exception:
        con.rollback()
        raise


SUB_ITEM_FORMS = ('confirmed_design', 'sealed_set')


def _display_name(product):
    return (product['jizhanming'] or product['name_cn_en'] or product['sku'] or '').strip()


def _parent_product(con, product_id):
    product = con.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
    if product is None:
        raise InventoryError('Product not found', 'not_found', 404)
    if product['stock_form'] in SUB_ITEM_FORMS:
        raise InventoryConflict('This is already a sub-item; add to its product instead', 'sub_item')
    return product


def _owned_series(con, product):
    """The series this product owns, created (named after it) on first use."""
    if product['series_id'] is not None:
        series = con.execute('SELECT id,product_id FROM product_series WHERE id=?', (product['series_id'],)).fetchone()
        if series['product_id'] not in (None, product['id']):
            raise InventoryConflict('This product belongs to another product; add to that product instead', 'sub_item')
        con.execute('UPDATE product_series SET product_id=? WHERE id=?', (product['id'], series['id']))
        return series['id']
    name = _display_name(product)
    if con.execute('SELECT 1 FROM product_series WHERE name=?', (name,)).fetchone():
        name = f"{name} ({product['sku']})"
    series_id = con.execute('INSERT INTO product_series(name, product_id) VALUES (?, ?)',
                            (name, product['id'])).lastrowid
    con.execute('UPDATE products SET series_id=? WHERE id=?', (series_id, product['id']))
    return series_id


def add_designs(con, product_id, data, *, actor, request_key):
    """Add confirmed designs under a product: each is its own piece product, SKU <product SKU>-01, -02..."""
    product_id = read_int(product_id, 'product_id', minimum=1)
    names = data.get('design_names') if isinstance(data, dict) and set(data) == {'design_names'} else None
    if (not isinstance(names, list) or not 1 <= len(names) <= 100
            or any(not isinstance(name, str) or not name.strip() or len(name.strip()) > 200 for name in names)):
        raise InventoryValidationError('Supply 1 to 100 nonempty design names, up to 200 characters each')
    names = [name.strip() for name in names]
    normalized = {normalize_design_name(name) for name in names}
    if len(normalized) != len(names):
        raise InventoryValidationError('Design names must be unique')
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'product_designs', actor,
                                     {'product_id': product_id, 'design_names': names})
        if prior is not None:
            con.commit()
            return prior
        product = _parent_product(con, product_id)
        series_id = _owned_series(con, product)
        for row in con.execute("SELECT design_name FROM products WHERE series_id=? AND stock_form='confirmed_design'",
                               (series_id,)):
            if row['design_name'] and normalize_design_name(row['design_name']) in normalized:
                raise InventoryConflict(f"{product['sku']} already has the design {row['design_name']}", 'design_conflict')
        sku = product['sku'] or f'P{product_id}'
        pattern = re.compile(re.escape(sku) + r'-(\d{2,})$')
        used = [int(match.group(1)) for (other,) in con.execute('SELECT sku FROM products WHERE sku LIKE ?', (f'{sku}-%',))
                if (match := pattern.match(other or ''))]
        number = max(used, default=0)
        parent_name, full_name = _display_name(product), (product['name_cn_en'] or _display_name(product)).strip()
        product_ids = []
        for design in names:
            number += 1
            design_sku, jizhanming, name = f'{sku}-{number:02d}', f'{parent_name} {design}', f'{full_name} · {design}'
            search = ' '.join(value for value in (design_sku, jizhanming, name, product['brand'], product['ip_series'])
                              if value).lower()
            product_ids.append(con.execute(
                '''INSERT INTO products(sku,jizhanming,name_cn_en,price,ip_series,product_type,brand,series_id,
                                        stock_form,stock_unit,design_name,identity_status,search_blob)
                   VALUES (?,?,?,?,?,?,?,?,'confirmed_design','piece',?,'verified',?)''',
                (design_sku, jizhanming, name, product['price'], product['ip_series'], product['product_type'],
                 product['brand'], series_id, design, search)).lastrowid)
        result = {'product_id': product_id, 'series_id': series_id, 'product_ids': product_ids}
        _remember(con, key, 'product_designs', product_id, actor, digest, result)
        con.commit()
        return result
    except sqlite3.IntegrityError as exc:
        con.rollback()
        raise InventoryConflict('Could not save the designs; nothing was created', 'design_conflict') from exc
    except Exception:
        con.rollback()
        raise


def add_sealed_set(con, product_id, data, *, actor, request_key):
    """Give a blind box its 整端 sealed set (SKU <product SKU>-SET), 1 set = N boxes.

    An untyped product becomes a verified blind box here, since only blind boxes come in sealed sets."""
    product_id = read_int(product_id, 'product_id', minimum=1)
    if not isinstance(data, dict) or set(data) != {'boxes_per_set'}:
        raise InventoryValidationError('Supply boxes_per_set only')
    boxes = read_int(data['boxes_per_set'], 'boxes_per_set', minimum=1)
    if boxes > 100:
        raise InventoryValidationError('boxes_per_set must be 100 or fewer')
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'product_sealed_set', actor,
                                     {'product_id': product_id, 'boxes_per_set': boxes})
        if prior is not None:
            con.commit()
            return prior
        product = _parent_product(con, product_id)
        if con.execute('SELECT 1 FROM product_conversions WHERE target_product_id=?', (product_id,)).fetchone():
            raise InventoryConflict('This product already has a sealed set', 'sealed_set_exists')
        if product['stock_form'] not in (None, 'random_box'):
            raise InventoryConflict('Only a blind box can have a sealed set', 'not_blind_box')
        series_id = product['series_id']  # a sealed set is a variation, not a reason to become a series
        if (product['stock_form'], product['identity_status']) != ('random_box', 'verified'):
            update_product_identity(con, product_id, {'stock_form': 'random_box', 'stock_unit': 'box',
                                                      'identity_status': 'verified'})
        con.execute('UPDATE products SET boxes_per_dan=? WHERE id=?', (boxes, product_id))
        item = sealed_set_item(product, boxes)
        if not item['set_sku'] or con.execute('SELECT 1 FROM products WHERE sku=?', (item['set_sku'],)).fetchone():
            raise InventoryConflict(f"{item['set_sku'] or 'A SKU'} is already taken", 'sku_taken')
        created = create_sealed_set(con, {**item, 'series_id': series_id})
        result = {'product_id': product_id, 'series_id': series_id, 'set_product_id': created['set_product_id']}
        _remember(con, key, 'product_sealed_set', product_id, actor, digest, result)
        con.commit()
        return result
    except CatalogConflict as exc:
        con.rollback()
        raise InventoryConflict(str(exc), exc.code) from exc
    except Exception:
        con.rollback()
        raise


def _product_references(con, product_id):
    """Rows in other tables that point at a product (its own aliases and counts saved before go-live
    don't count: they go with it)."""
    count = 0
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        if table in ('product_aliases', 'opening_counts'):
            continue
        for fk in con.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
            if fk['table'] == 'products':
                count += con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{fk["from"]}"=?', (product_id,)).fetchone()[0]
    return count


def remove_design(con, design_id, *, actor):
    """Remove one confirmed design added by mistake (a typo, or a design the series does not have),
    refused once it has stock, sales or other records. A count saved before go-live goes with it."""
    design_id = read_int(design_id, 'design_id', minimum=1)
    _begin(con)
    try:
        design = con.execute('SELECT id, sku, stock_form FROM products WHERE id=?', (design_id,)).fetchone()
        if design is None or design['stock_form'] != 'confirmed_design':
            raise InventoryError('Design not found', 'not_found', 404)
        if _product_references(con, design_id):
            raise InventoryConflict(f"{design['sku']} already has stock, sales or other records", 'design_in_use')
        con.execute('DELETE FROM products WHERE id=?', (design_id,))
        con.commit()
        return {'removed_design': design_id}
    except Exception:
        con.rollback()
        raise


def remove_designs(con, product_id, *, actor):
    """Owner, 2026-10-26: only products we sell confirmed designs of are a series. Turn a series back into a plain
    product: delete its confirmed designs (refused if any has stock, sales or other records) and the series.
    Its sealed set, a variation, stays."""
    product_id = read_int(product_id, 'product_id', minimum=1)
    _begin(con)
    try:
        product = _parent_product(con, product_id)
        series = con.execute('SELECT id FROM product_series WHERE product_id=?', (product_id,)).fetchone() \
            if product['series_id'] else None
        if series is None or series['id'] != product['series_id']:
            raise InventoryConflict('This product has no confirmed designs', 'no_designs')
        members = con.execute('SELECT id, sku, stock_form FROM products WHERE series_id=? AND id<>?',
                              (series['id'], product_id)).fetchall()
        designs = [m for m in members if m['stock_form'] == 'confirmed_design']
        used = [m['sku'] for m in designs if _product_references(con, m['id'])]
        if used:
            raise InventoryConflict(f"These designs already have stock or sales records: {', '.join(used[:10])}", 'designs_in_use')
        for m in designs:
            con.execute('DELETE FROM products WHERE id=?', (m['id'],))
        con.execute('UPDATE products SET series_id=NULL WHERE series_id=?', (series['id'],))
        con.execute('DELETE FROM product_series WHERE id=?', (series['id'],))
        con.commit()
        return {'product_id': product_id, 'removed_designs': len(designs)}
    except Exception:
        con.rollback()
        raise
