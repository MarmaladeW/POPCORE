"""Sealed sets (整端) for blind boxes.

A sealed set holds one of every design in its series; loose boxes could be anything. So a sealed set is
its own product (stock_form sealed_set, counted in sets), linked to its blind box by a reviewed
conversion (1 set = N boxes, N from the catalogue's boxes-per-set). Opening a set keeps that provenance
through inventory_open_sets. ``plan`` reports what would be created; ``apply`` creates every pair in one
transaction behind a checked backup. See docs/inventory-core.md (Sealed sets).
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

from catalog_identity import add_conversion


class SealedSetError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


SET_SUFFIX = '-SET'


def _display_name(row):
    return (row['jizhanming'] or row['name_cn_en'] or row['sku'] or '').strip()


def sealed_set_item(box, boxes):
    """The sealed set a blind box gets: SKU, names and price for N boxes per set."""
    name = _display_name(box)
    return {
        'box_product_id': box['id'], 'box_sku': box['sku'], 'name': name,
        'boxes_per_set': boxes, 'set_sku': f"{box['sku']}{SET_SUFFIX}" if box['sku'] else None,
        'set_jizhanming': f'{name} 整端',
        'set_name': f"{(box['name_cn_en'] or name).strip()} · 整端 Sealed set",
        'set_price': round(box['price'] * boxes, 2) if box['price'] is not None else None,
    }


def plan_sealed_sets(con):
    """Every verified blind box with a boxes-per-set count and no sealed set yet, and why others wait."""
    items, waiting, errors = [], [], []
    rows = con.execute(
        """SELECT p.id, p.sku, p.jizhanming, p.name_cn_en, p.price, p.ip_series, p.product_type, p.brand,
                  p.boxes_per_dan, p.series_id, ps.name AS series_name, p.stock_form, p.stock_unit,
                  p.identity_status,
                  EXISTS (SELECT 1 FROM product_conversions c WHERE c.target_product_id=p.id) AS linked
           FROM products p LEFT JOIN product_series ps ON ps.id=p.series_id
           WHERE CAST(COALESCE(p.boxes_per_dan, 0) AS INTEGER) > 0
           ORDER BY p.sku, p.id""").fetchall()
    taken = {row['sku'] for row in con.execute('SELECT sku FROM products WHERE sku IS NOT NULL')}
    series_by_name = {row['name']: row['id'] for row in con.execute('SELECT id, name FROM product_series')}
    planned_names = set()
    for row in rows:
        if row['linked']:
            continue
        if (row['stock_form'], row['stock_unit'], row['identity_status']) != ('random_box', 'box', 'verified'):
            waiting.append({'product_id': row['id'], 'sku': row['sku'], 'name': _display_name(row),
                            'message': 'verify it as a blind box (random_box, box) first'})
            continue
        item = sealed_set_item(row, int(row['boxes_per_dan']))
        set_sku = item['set_sku']
        if not set_sku or set_sku in taken:
            errors.append({'product_id': row['id'], 'sku': row['sku'], 'code': 'sku_taken',
                           'message': f'{set_sku or "a SKU"} is not available for its sealed set'})
            continue
        series_name = row['series_name'] or item['name']
        if not row['series_id'] and series_name in planned_names:
            errors.append({'product_id': row['id'], 'sku': row['sku'], 'code': 'series_name_repeated',
                           'message': f'two blind boxes would share the new series "{series_name}"; '
                                      'give them their series in the identity sheet first'})
            continue
        if not row['series_id']:
            planned_names.add(series_name)
        items.append({
            **item,
            'series_id': row['series_id'] or series_by_name.get(series_name),
            'series_name': series_name,
            'new_series': not row['series_id'] and series_name not in series_by_name,
        })
    return {'items': items, 'waiting': waiting, 'errors': errors,
            'ready_to_apply': bool(items) and not errors}


def create_sealed_set(con, item):
    """Create the sealed set product and its conversion; the blind box owns the series."""
    box = con.execute('SELECT * FROM products WHERE id=?', (item['box_product_id'],)).fetchone()
    series_id = item['series_id']
    if series_id is None:
        series_id = con.execute('INSERT INTO product_series(name, product_id) VALUES (?, ?)',
                                (item['series_name'], box['id'])).lastrowid
    else:
        con.execute('UPDATE product_series SET product_id=? WHERE id=? AND product_id IS NULL',
                    (box['id'], series_id))
    if box['series_id'] != series_id:
        con.execute('UPDATE products SET series_id=? WHERE id=?', (series_id, box['id']))
    search = ' '.join(value for value in (item['set_sku'], item['set_jizhanming'], item['set_name'],
                                          box['brand'], box['ip_series'], '整端 sealed set') if value).lower()
    set_id = con.execute(
        """INSERT INTO products (sku, name_cn_en, jizhanming, price, ip_series, product_type, brand,
                                 boxes_per_dan, search_blob, series_id, stock_form, stock_unit,
                                 identity_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sealed_set', 'set', 'unverified')""",
        (item['set_sku'], item['set_name'], item['set_jizhanming'], item['set_price'], box['ip_series'],
         box['product_type'], box['brand'], item['boxes_per_set'], search, series_id),
    ).lastrowid
    add_conversion(con, set_id, box['id'], item['boxes_per_set'])
    con.execute("UPDATE products SET identity_status='verified' WHERE id=?", (set_id,))
    return {**item, 'set_product_id': set_id, 'series_id': series_id}


def apply_sealed_sets(db_path, *, backup_path):
    """Create every planned sealed set and its conversion in one transaction after a checked backup."""
    if backup_path is None:
        raise SealedSetError('apply needs a new backup path', 'backup_required')
    with closing(sqlite3.connect(db_path, isolation_level=None)) as con:
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('BEGIN IMMEDIATE')
        try:
            report = plan_sealed_sets(con)
            if not report['ready_to_apply']:
                con.execute('ROLLBACK')
                return {**report, 'applied': False}
            with closing(sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)) as source, \
                    closing(sqlite3.connect(backup_path)) as target:
                source.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise SealedSetError('backup failed integrity_check', 'backup_failed')
            created = [create_sealed_set(con, item) for item in report['items']]
            if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise SealedSetError('integrity_check failed after creating sealed sets', 'integrity_failed')
            con.execute('COMMIT')
        except Exception:
            con.execute('ROLLBACK')
            raise
    return {**report, 'items': created, 'applied': True, 'backup': str(backup_path)}
