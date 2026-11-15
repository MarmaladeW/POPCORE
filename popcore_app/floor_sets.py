"""Sealed sets (整端) sitting on a floor, opened into boxes.

Whole sets are kept upstairs or in the warehouse; a floor holds blind boxes as boxes only (owner, 2026-10-29),
and a set is no longer tracked as a set once it is downstairs (owner, 2026-11-06). 补货 used to carry a picked
whole set down as it was, so floors hold sealed sets that sales cannot take. ``plan_floor_sets`` lists them with
the boxes each becomes; ``apply_floor_sets`` turns them into loose boxes on the same floor, one inventory
document per store, behind a checked backup. Nothing else moves. See docs/goods-handling.md.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from auth import ROLE_CLAIM
from checkout_access import business_date as today
from inventory_commands import InventoryError, _post_inventory_in_transaction, boxes_of_set, expected_versions

REASON = '楼下整端拆成散盒 Sealed sets on the floor opened into boxes'


class FloorSetError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def _name(row):
    return (row['jizhanming'] or row['name_cn_en'] or row['sku'] or '').strip()


def _quantity(con, product_id, location_id):
    row = con.execute(
        """SELECT quantity FROM inventory_balances
           WHERE product_id=? AND location_id=? AND disposition='saleable'""", (product_id, location_id)).fetchone()
    return row['quantity'] if row else 0


def plan_floor_sets(con, stores=None):
    """Every sealed set with saleable stock on a live floor, and what it becomes: `items` can be opened,
    `blocked` are left as they are and say why. `stores` (codes, any case) limits it to those stores."""
    wanted = None if stores is None else {code.strip().upper() for code in stores if code and code.strip()}
    known = {row['code'] for row in con.execute('SELECT code FROM stores')}
    if wanted is not None and wanted - known:
        raise FloorSetError(f"no store has the code {', '.join(sorted(wanted - known))}", 'invalid_input')
    items, blocked = [], []
    for row in con.execute(
            """SELECT s.code AS store, s.id AS store_id, l.id AS location_id, b.quantity AS sets, b.version,
                      p.id AS product_id, p.sku, p.jizhanming, p.name_cn_en, p.identity_status
               FROM inventory_balances b
               JOIN inventory_locations l ON l.id=b.location_id AND l.code='floor' AND l.is_active=1
               JOIN inventory_scope_state ss ON ss.location_id=l.id AND ss.opening_verified=1
               JOIN stores s ON s.id=l.store_id AND s.is_active=1
               JOIN products p ON p.id=b.product_id AND p.stock_form='sealed_set'
               WHERE b.disposition='saleable' AND b.quantity > 0
               ORDER BY s.code, p.sku, p.id""").fetchall():
        if wanted is not None and row['store'] not in wanted:
            continue
        entry = {'store': row['store'], 'store_id': row['store_id'], 'location_id': row['location_id'],
                 'set_product_id': row['product_id'], 'set_sku': row['sku'], 'name': _name(row),
                 'sets': row['sets'], 'set_version': row['version']}
        try:
            if row['identity_status'] != 'verified':
                raise InventoryError('the sealed set is not a verified product', 'identity_unverified')
            box = boxes_of_set(con, row['product_id'])
        except InventoryError as exc:
            blocked.append({**entry, 'code': exc.code, 'message': str(exc)})
            continue
        product = con.execute('SELECT sku, jizhanming, name_cn_en FROM products WHERE id=?',
                              (box['product_id'],)).fetchone()
        items.append({**entry, 'boxes_per_set': box['per_set'], 'boxes': row['sets'] * box['per_set'],
                      'box_product_id': box['product_id'], 'box_sku': product['sku'], 'box_name': _name(product),
                      'floor_boxes_before': _quantity(con, box['product_id'], row['location_id'])})
    added = {}
    for item in items:
        key = (item['location_id'], item['box_product_id'])
        added[key] = added.get(key, 0) + item['boxes']
    for item in items:
        # Two sets of one blind box on one floor end in the same pile of boxes.
        item['floor_boxes_after'] = item['floor_boxes_before'] + added[(item['location_id'], item['box_product_id'])]
    return {
        'items': items, 'blocked': blocked,
        'totals': {'set_products': len(items), 'sets': sum(item['sets'] for item in items),
                   'boxes': sum(item['boxes'] for item in items),
                   'stores': sorted({item['store'] for item in items})},
        'ready_to_apply': bool(items),
    }


def _drift(con):
    """Balances that are not the sum of their movements (there should be none)."""
    return con.execute(
        """SELECT COUNT(*) FROM inventory_balances b
           WHERE b.quantity != (SELECT COALESCE(SUM(m.quantity), 0) FROM inventory_movements m
                                WHERE m.product_id=b.product_id AND m.location_id=b.location_id
                                  AND m.disposition=b.disposition)""").fetchone()[0]


def _post_store(con, store, items, *, actor, day, reason):
    """One document for one store's floor: each set comes off, its boxes go on."""
    expect = expected_versions(con)
    floor = items[0]['location_id']
    lines, boxes = [], {}
    for item in items:
        lines.append({'product_id': item['set_product_id'], 'unit': 'set', 'quantity': item['sets'],
                      'from_location_id': floor, 'from_disposition': 'saleable',
                      'expected_versions': {'from': expect(item['set_product_id'], floor, 'saleable')}})
        boxes[item['box_product_id']] = boxes.get(item['box_product_id'], 0) + item['boxes']
    for product_id, quantity in boxes.items():
        lines.append({'product_id': product_id, 'unit': 'box', 'quantity': quantity,
                      'to_location_id': floor, 'to_disposition': 'saleable',
                      'expected_versions': {'to': expect(product_id, floor, 'saleable')}})
    # Balance versions only ever rise, so the same sets found again later are a new request, never a replay.
    digest = hashlib.sha256(json.dumps(
        [[item['set_product_id'], item['sets'], item['set_version']] for item in items]).encode()).hexdigest()[:16]
    return _post_inventory_in_transaction(con, {
        'kind': 'correction', 'business_date': day, 'source_type': 'floor_sets_opened',
        'source_id': f'{store}:{digest}', 'reason': reason, 'lines': lines,
    }, actor=actor, request_key=f'floor-sets-opened:{store}:{digest}')['document_id']


def apply_floor_sets(db_path, *, backup_path, actor_sub, stores=None, business_date=None, reason=REASON,
                     busy_timeout_ms=5000):
    """Back up, then open every planned sealed set into boxes on its floor, and check the result before
    committing. The write lock is held from the plan to the commit, so the app cannot change a floor in
    between; any failed check rolls everything back and the backup stays as the snapshot from before."""
    if not actor_sub:
        raise FloorSetError('apply needs --actor-sub: the manager or admin this is recorded under', 'invalid_input')
    db_path, backup_path = Path(db_path).resolve(), Path(backup_path).resolve()
    if not db_path.is_file():
        raise FloorSetError(f'database does not exist: {db_path}', 'invalid_input')
    if backup_path.exists() or backup_path == db_path:
        raise FloorSetError(f'refusing existing backup path: {backup_path}', 'invalid_input')
    day = business_date or today()
    with closing(sqlite3.connect(db_path, isolation_level=None)) as con:
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.execute(f'PRAGMA busy_timeout={int(busy_timeout_ms)}')
        con.execute('BEGIN IMMEDIATE')
        try:
            plan = plan_floor_sets(con, stores)
            if not plan['ready_to_apply']:
                con.execute('ROLLBACK')
                return {**plan, 'applied': False}
            for store_id, store in sorted({(item['store_id'], item['store']) for item in plan['items']}):
                if not con.execute('SELECT 1 FROM inventory_access WHERE auth0_sub=? AND store_id=?',
                                   (actor_sub, store_id)).fetchone():
                    raise FloorSetError(f'{actor_sub} has no inventory access for {store}', 'actor_access_missing')
            if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise FloorSetError('integrity_check failed before the change', 'integrity_failed')
            foreign_keys = {tuple(row) for row in con.execute('PRAGMA foreign_key_check')}
            drift = _drift(con)
            # A second reader sees exactly the committed state this lock protects.
            with closing(sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)) as source, \
                    closing(sqlite3.connect(backup_path)) as target:
                source.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise FloorSetError('backup failed integrity_check', 'backup_failed')
            actor = {'sub': actor_sub, ROLE_CLAIM: 'manager'}
            documents = {}
            for store in plan['totals']['stores']:
                try:
                    documents[store] = _post_store(
                        con, store, [item for item in plan['items'] if item['store'] == store],
                        actor=actor, day=day, reason=reason)
                except (InventoryError, PermissionError) as exc:
                    raise FloorSetError(f'{store}: {exc}', getattr(exc, 'code', 'inventory_forbidden')) from exc
            for item in plan['items']:
                item['sets_after'] = _quantity(con, item['set_product_id'], item['location_id'])
                item['floor_boxes_now'] = _quantity(con, item['box_product_id'], item['location_id'])
                if item['sets_after'] != 0 or item['floor_boxes_now'] != item['floor_boxes_after']:
                    raise FloorSetError(f"{item['store']} {item['set_sku']}: the floor is not what the plan said",
                                        'result_mismatch')
            if _drift(con) != drift:
                raise FloorSetError('a balance no longer matches its movements', 'ledger_mismatch')
            if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or (
                    {tuple(row) for row in con.execute('PRAGMA foreign_key_check')} - foreign_keys):
                raise FloorSetError('integrity or foreign key check failed after the change', 'integrity_failed')
            con.execute('COMMIT')
        except Exception:
            if con.in_transaction:
                con.execute('ROLLBACK')
            raise
    return {**plan, 'applied': True, 'backup': str(backup_path), 'business_date': day, 'documents': documents}
