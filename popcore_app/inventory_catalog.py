"""Series rosters over the existing product and inventory ledger identities."""
import sqlite3
import uuid

from catalog_identity import FORMS_TO_UNITS, normalize_design_name
from goods_operations import _begin, _replay, _remember
from inventory_commands import (
    INVENTORY_DISPOSITIONS, InventoryConflict, InventoryError,
    InventoryValidationError, require_inventory_access,
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


def series_inventory(con, actor, store_code, query='', series_id=None):
    if series_id is not None:
        series_id = read_int(series_id, 'series_id', minimum=1)
    con.execute('BEGIN')
    locations = _locations(con, actor, store_code)
    mode = con.execute('SELECT mode FROM inventory_mode WHERE id=1').fetchone()['mode']
    roster = {row['id']: {**dict(row), 'products': []} for row in con.execute(
        'SELECT id,name FROM product_series WHERE (? IS NULL OR id=?) ORDER BY name,id',
        (series_id, series_id))}
    marks = ','.join('?' for _ in locations)
    balances = {(row['product_id'], row['location_id'], row['disposition']): row
                for row in con.execute(
                    f'SELECT * FROM inventory_balances WHERE location_id IN ({marks})',
                    [location['id'] for location in locations])}
    opened_sets = {}
    if mode == 'authoritative':
        for row in con.execute(
            f'''SELECT o.id,o.random_product_id,o.location_id,o.purpose,o.remaining_qty,o.opening_document_id
                FROM inventory_open_sets o JOIN inventory_scope_state ss ON ss.location_id=o.location_id
                WHERE o.location_id IN ({marks}) AND ss.opening_verified=1 AND o.remaining_qty>0 ORDER BY o.id''',
            [location['id'] for location in locations]):
            item = dict(row)
            opened_sets.setdefault(item.pop('random_product_id'), []).append(item)
    for row in con.execute(
        '''SELECT id,sku,COALESCE(NULLIF(jizhanming,''),NULLIF(name_cn_en,''),sku) AS name,
                  series_id,stock_form,stock_unit,design_name,identity_status,
                  (SELECT filename FROM hidden_images i WHERE i.product_id=products.id
                   ORDER BY CASE WHEN i.image_type='general' THEN 0 ELSE 1 END,i.id LIMIT 1) AS image_filename
           FROM products WHERE series_id IS NOT NULL AND (? IS NULL OR series_id=?) ORDER BY id''',
        (series_id, series_id)):
        product = dict(row)
        if product['series_id'] not in roster:
            continue
        valid = (product['identity_status'] == 'verified'
                 and product['stock_unit'] is not None
                 and FORMS_TO_UNITS.get(product['stock_form']) == product['stock_unit']
                 and (product['stock_form'] != 'confirmed_design' or bool((product['design_name'] or '').strip())))
        product['open_sets'] = opened_sets.get(product['id'], []) if valid else []
        product['balances'] = []
        for location in locations:
            trusted = mode == 'authoritative' and location['opening_verified'] and valid
            for disposition in sorted(INVENTORY_DISPOSITIONS):
                balance = balances.get((product['id'], location['id'], disposition))
                product['balances'].append({
                    'location_id': location['id'], 'disposition': disposition,
                    'quantity': (balance['quantity'] if balance else 0) if trusted else None,
                    'version': (balance['version'] if balance else 0) if trusted else None,
                })
        roster[product['series_id']]['products'].append(product)
    needle = normalize_design_name(query)
    series = [item for item in roster.values() if not needle or needle in normalize_design_name(
        ' '.join([item['name'], *[str(p.get(field) or '') for p in item['products']
                                for field in ('name', 'sku', 'design_name')]]))]
    return {'mode': mode, 'locations': locations, 'series': series,
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
