"""Bulk inventory-identity review sheet: export, preview and atomic confirm.

Every row is applied through catalog_identity.update_product_identity, so the
single-product validation and lock rules apply unchanged. Nothing here writes
stock, balances or stock history.
"""
import csv
import io
import sqlite3

from catalog_identity import (
    FORMS_TO_UNITS, CatalogConflict, normalize_design_name, update_product_identity,
)
from db import esc_csv
from goods_operations import _begin, _remember, _replay
from inventory_commands import InventoryConflict, InventoryValidationError
from matcher import normalize_sku

SHEET_COLUMNS = ('product_id', 'sku', 'name', 'jizhanming', 'legacy_series', 'series_id',
                 'series', 'stock_form', 'stock_unit', 'design_name', 'identity_status')
SHEET_HEADERS = ('product_id', 'SKU', 'name', '记账名', 'legacy_series', 'series_id',
                 'series', 'stock_form', 'stock_unit', 'design_name', 'identity_status')
# Reference-only columns: exported for review, ignored on import.
READ_ONLY = {'name', 'jizhanming', 'legacy_series', 'identity_status'}
IDENTITY_INPUTS = ('series_id', 'series', 'stock_form', 'stock_unit', 'design_name')
HEADER_ALIASES = {**dict(zip((h.lower() for h in SHEET_HEADERS), SHEET_COLUMNS)),
                  '产品名称': 'name', 'name_cn_en': 'name', 'jizhanming': 'jizhanming', 'id': 'product_id'}
SNAPSHOT = ('series_id', 'stock_form', 'stock_unit', 'design_name', 'identity_status')
MAX_SERIES_NAME = 200


def export_csv(con, store_code=None, unverified_only=False):
    filters, params = [], []
    if store_code:
        store = con.execute('SELECT id FROM stores WHERE code=?', (store_code,)).fetchone()
        if store is None:
            raise ValueError('store_code does not identify a store')
        filters.append('EXISTS (SELECT 1 FROM stock s WHERE s.product_id=p.id AND s.store_id=? '
                       'AND (s.instore_qty > 0 OR s.upstairs_qty > 0))')
        params.append(store['id'])
    if unverified_only:
        filters.append("p.identity_status != 'verified'")
    where = ('WHERE ' + ' AND '.join(filters)) if filters else ''
    rows = con.execute(f'''
        SELECT p.id AS product_id, p.sku, p.name_cn_en AS name, p.jizhanming,
               p.ip_series AS legacy_series, p.series_id, ps.name AS series,
               p.stock_form, p.stock_unit, p.design_name, p.identity_status
        FROM products p LEFT JOIN product_series ps ON ps.id=p.series_id
        {where}
        ORDER BY p.ip_series, p.sku, p.id''', params).fetchall()
    lines = ['﻿' + ','.join(SHEET_HEADERS)]
    lines += [','.join(esc_csv(row[column]) for column in SHEET_COLUMNS) for row in rows]
    return '\n'.join(lines), len(rows)


def _parse(text):
    if not isinstance(text, str) or not text.lstrip('﻿').strip():
        raise ValueError('Nonempty CSV or TSV text is required')
    text = text.lstrip('﻿')
    reader = csv.reader(io.StringIO(text, newline=''),
                        delimiter='\t' if '\t' in text.splitlines()[0] else ',', strict=True)
    try:
        raw = next(reader)
    except StopIteration:
        raise ValueError('A header row is required') from None
    headers = [HEADER_ALIASES.get(h.strip().lower(), h.strip()) for h in raw]
    unknown = [h for h in headers if h not in SHEET_COLUMNS]
    if unknown:
        raise ValueError('Unsupported identity sheet columns: ' + ', '.join(unknown))
    if len(set(headers)) != len(headers):
        raise ValueError('Identity sheet headers must not repeat')
    if 'product_id' not in headers and 'sku' not in headers:
        raise ValueError('Identity sheet needs a product_id or SKU column')
    if not set(headers) & set(IDENTITY_INPUTS):
        raise ValueError('Identity sheet needs at least one identity column: ' + ', '.join(IDENTITY_INPUTS))
    rows = []
    for line, cells in enumerate(reader, start=2):
        if not any(cell.strip() for cell in cells):
            continue
        if len(cells) != len(headers):
            raise ValueError(f'Row {line} must have one cell per header')
        rows.append((line, {h: c.strip() for h, c in zip(headers, cells)}))
    if not rows:
        raise ValueError('At least one product row is required')
    return headers, rows


def _snapshot(product, series_names):
    result = {key: product[key] for key in SNAPSHOT}
    result['series_name'] = series_names.get(product['series_id'])
    return result


def _series_lookup(con):
    by_id = {row['id']: row['name'] for row in con.execute('SELECT id, name FROM product_series')}
    return by_id, {name: sid for sid, name in by_id.items()}


def _resolve_series(cells, headers, current, by_id, by_name, new_names):
    """Return (series_id, series_name, is_new) for one row; raise ValueError when unclear."""
    if 'series_id' not in headers and 'series' not in headers:
        return current, by_id.get(current), False
    raw_id, name = cells.get('series_id', ''), cells.get('series', '')
    if raw_id:
        try:
            series_id = int(raw_id)
        except ValueError:
            raise ValueError('series_id must be a whole number') from None
        if series_id not in by_id:
            raise ValueError('series_id does not identify a series')
        if name and name != by_id[series_id]:
            raise ValueError(f'series_id {series_id} is named "{by_id[series_id]}", not "{name}"')
        return series_id, by_id[series_id], False
    if not name:
        return None, None, False
    if name in by_name:
        return by_name[name], name, False
    if len(name) > MAX_SERIES_NAME:
        raise ValueError(f'Series names are limited to {MAX_SERIES_NAME} characters')
    folded = normalize_design_name(name)
    for existing in list(by_name) + list(new_names):
        if existing != name and normalize_design_name(existing) == folded:
            raise CatalogConflict(f'Series "{name}" differs from "{existing}" only by case or spacing; '
                                  'use the exact existing name', 'series_conflict')
    return None, name, True


def _target(cells, headers, product, series):
    series_id, series_name, is_new = series
    form = (cells['stock_form'].lower() or None) if 'stock_form' in headers else product['stock_form']
    unit = cells.get('stock_unit', '').lower() or None
    if unit is None and form in FORMS_TO_UNITS:
        unit = FORMS_TO_UNITS[form]  # The unit is fixed by the form; a blank cell takes it.
    if 'design_name' in headers:
        design = cells['design_name'] or None
    else:
        design = product['design_name']
    return {'series_id': series_id, 'series_name': series_name, 'new_series': is_new,
            'stock_form': form, 'stock_unit': unit, 'design_name': design,
            'identity_status': 'verified'}


def _apply(con, product_id, values, created):
    """Apply one row's target through update_product_identity inside the open transaction."""
    series_id = values['series_id']
    if values['new_series']:
        name = values['series_name']
        series_id = created.get(name)
        if series_id is None:
            row = con.execute('SELECT id FROM product_series WHERE name=?', (name,)).fetchone()
            if row is None:
                folded = normalize_design_name(name)
                if any(normalize_design_name(r['name']) == folded
                       for r in con.execute('SELECT name FROM product_series')):
                    raise CatalogConflict('Series already exists under a different spelling; '
                                          'preview again', 'series_conflict')
                series_id = con.execute('INSERT INTO product_series(name) VALUES (?)', (name,)).lastrowid
            else:
                series_id = row['id']
            created[name] = series_id
    update_product_identity(con, product_id, {
        'series_id': series_id, 'stock_form': values['stock_form'],
        'stock_unit': values['stock_unit'], 'design_name': values['design_name'],
        'identity_status': 'verified',
    })


def _same(before, values):
    return (not values['new_series']
            and all(before[key] == values[key] for key in SNAPSHOT))


def preview(con, text):
    """Validate every row against current values; the transaction is always rolled back."""
    headers, parsed = _parse(text)
    by_id, by_name = _series_lookup(con)
    products_by_sku = {}
    _begin(con)
    try:
        products = {row['id']: dict(row) for row in con.execute(
            'SELECT id, sku, name_cn_en, jizhanming, ' + ', '.join(SNAPSHOT) + ' FROM products')}
        for product in products.values():
            products_by_sku.setdefault(normalize_sku(product['sku']), []).append(product)
        rows, seen, new_names, created = [], set(), [], {}
        for line, cells in parsed:
            result = {'line': line, 'product_id': None, 'sku': cells.get('sku', ''), 'name': None,
                      'status': 'rejected', 'reason': None, 'code': None, 'before': None, 'values': None}
            rows.append(result)
            try:
                product = _find_product(cells, products, products_by_sku)
                result.update(product_id=product['id'], sku=product['sku'],
                              name=product['jizhanming'] or product['name_cn_en'])
                if product['id'] in seen:
                    raise ValueError('Product appears more than once in this sheet')
                seen.add(product['id'])
                result['before'] = _snapshot(product, by_id)
                if not any(cells.get(key) for key in IDENTITY_INPUTS):
                    result.update(status='skipped', reason='No identity entered')
                    continue
                series = _resolve_series(cells, headers, product['series_id'], by_id, by_name, new_names)
                values = _target(cells, headers, product, series)
                result['values'] = values
                if _same(result['before'], values):
                    result['status'] = 'unchanged'
                    continue
                # Accepted rows stay applied until the final rollback, so later rows
                # validate against them exactly as confirm will.
                con.execute('SAVEPOINT identity_row')
                known = dict(created)
                try:
                    _apply(con, product['id'], values, created)
                except Exception:
                    con.execute('ROLLBACK TO identity_row')
                    created.clear()
                    created.update(known)
                    raise
                finally:
                    con.execute('RELEASE identity_row')
                if values['new_series'] and values['series_name'] not in new_names:
                    new_names.append(values['series_name'])
                result['status'] = 'update'
            except CatalogConflict as exc:
                result.update(reason=str(exc), code=exc.code)
            except ValueError as exc:
                result.update(reason=str(exc), code='invalid_input')
    finally:
        con.rollback()
    counts = {status: sum(r['status'] == status for r in rows)
              for status in ('update', 'unchanged', 'skipped', 'rejected')}
    return {'rows': rows, **counts, 'new_series': new_names}


def _find_product(cells, products, products_by_sku):
    raw_id, sku = cells.get('product_id', ''), cells.get('sku', '')
    product = None
    if raw_id:
        try:
            product = products.get(int(raw_id))
        except ValueError:
            raise ValueError('product_id must be a whole number') from None
        if product is None:
            raise ValueError('product_id does not identify a product')
        if sku and normalize_sku(sku) != normalize_sku(product['sku']):
            raise ValueError(f'product_id {product["id"]} has SKU {product["sku"]}, not {sku}')
        return product
    if not sku:
        raise ValueError('Each row needs a product_id or SKU')
    matches = products_by_sku.get(normalize_sku(sku), [])
    if len(matches) > 1:
        raise CatalogConflict('Multiple products share this SKU; use product_id', 'sku_conflict')
    if not matches:
        raise ValueError('No product has this exact SKU')
    return matches[0]


def _confirm_rows(rows):
    if not isinstance(rows, list) or not rows:
        raise InventoryValidationError('A nonempty list of accepted preview rows is required')
    clean = []
    for row in rows:
        if not isinstance(row, dict) or row.get('status') != 'update':
            raise InventoryValidationError('Only accepted preview rows can be confirmed')
        product_id, before, values = row.get('product_id'), row.get('before'), row.get('values')
        if type(product_id) is not int or product_id <= 0:
            raise InventoryValidationError('product_id must be a positive integer')
        if not isinstance(before, dict) or not set(SNAPSHOT) <= set(before):
            raise InventoryValidationError('Each row needs its preview snapshot')
        if not isinstance(values, dict) or not set(SNAPSHOT) | {'series_name', 'new_series'} <= set(values):
            raise InventoryValidationError('Each row needs its previewed identity')
        if values['identity_status'] != 'verified' or type(values['new_series']) is not bool:
            raise InventoryValidationError('Previewed identity rows must verify the product')
        if values['new_series']:
            name = values['series_name']
            if (values['series_id'] is not None or not isinstance(name, str) or not name.strip()
                    or len(name) > MAX_SERIES_NAME or name != name.strip()):
                raise InventoryValidationError('A new series needs a trimmed name and no series_id')
        clean.append({'product_id': product_id, 'sku': row.get('sku'),
                      'before': {key: before[key] for key in SNAPSHOT},
                      'values': {key: values[key] for key in (*SNAPSHOT, 'series_name', 'new_series')}})
    if len({row['product_id'] for row in clean}) != len(clean):
        raise InventoryValidationError('Product appears more than once in this confirmation')
    return clean


def confirm(con, rows, *, actor, request_key):
    rows = _confirm_rows(rows)
    intent = {'rows': [{'product_id': r['product_id'], 'before': r['before'], 'values': r['values']}
                       for r in rows]}
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'identity_import', actor, intent)
        if prior is not None:
            con.commit()
            return prior
        updated = unchanged = 0
        created = {}
        for row in rows:
            label = row['sku'] or f'product {row["product_id"]}'
            current = con.execute('SELECT ' + ', '.join(SNAPSHOT) + ' FROM products WHERE id=?',
                                  (row['product_id'],)).fetchone()
            if current is None:
                raise InventoryConflict(f'{label} no longer exists; preview again', 'stale_preview')
            current = dict(current)
            target = row['values']
            series_id = target['series_id']
            if target['new_series']:
                # Another confirmation may already have created the series and applied this row.
                existing = con.execute('SELECT id FROM product_series WHERE name=?',
                                       (target['series_name'],)).fetchone()
                series_id = existing['id'] if existing else None
            if ((not target['new_series'] or series_id is not None)
                    and current == {**{key: target[key] for key in SNAPSHOT}, 'series_id': series_id}):
                unchanged += 1
                continue
            if current != row['before']:
                raise InventoryConflict(f'{label} changed since preview; preview again', 'stale_preview')
            try:
                _apply(con, row['product_id'], target, created)
            except CatalogConflict as exc:
                raise InventoryConflict(f'{label}: {exc}', exc.code) from exc
            except ValueError as exc:
                raise InventoryValidationError(f'{label}: {exc}') from exc
            updated += 1
        result = {'ok': True, 'updated': updated, 'unchanged': unchanged,
                  'series_created': sorted(created)}
        _remember(con, key, 'identity_import', None, actor, digest, result)
        con.commit()
        return result
    except sqlite3.IntegrityError as exc:
        con.rollback()
        raise InventoryConflict('Identity import could not be saved; nothing was changed',
                                'identity_conflict') from exc
    except Exception:
        con.rollback()
        raise
