"""Rehearse inventory activation against disposable synthetic data only."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / 'popcore_app'
sys.path.insert(0, str(APP_DIR))
os.environ.setdefault('AUTH0_DOMAIN', 'rehearsal.invalid')
os.environ.setdefault('DISABLE_SCHEDULER', '1')

import db  # noqa: E402
from catalog_identity import add_conversion  # noqa: E402
from inventory_commands import post_inventory  # noqa: E402


def connect(path: Path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con


def add_product(con, sku, name, series_id, form, unit):
    return con.execute(
        """INSERT INTO products
           (sku, name_cn_en, jizhanming, ip_series, product_type, search_blob,
            series_id, stock_form, stock_unit, identity_status)
           VALUES (?, ?, ?, 'Rehearsal', '盲盒', ?, ?, ?, ?, 'verified')""",
        (sku, name, name, name.lower(), series_id, form, unit),
    ).lastrowid


def actor(role):
    return {'sub': f'rehearsal|{role}', 'https://popcore/role': role}


def verify(con):
    integrity = con.execute('PRAGMA integrity_check').fetchone()[0]
    foreign_keys = [tuple(row) for row in con.execute('PRAGMA foreign_key_check')]
    movements = {(r['product_id'], r['location_id'], r['disposition']): r['quantity']
                 for r in con.execute(
        """SELECT product_id, location_id, disposition, SUM(quantity) AS quantity
           FROM inventory_movements GROUP BY product_id, location_id, disposition""")}
    balances = {(r['product_id'], r['location_id'], r['disposition']): r['quantity']
                for r in con.execute(
        'SELECT product_id, location_id, disposition, quantity FROM inventory_balances')}
    return integrity, foreign_keys, movements == balances


def rehearse(fixture: str, output_dir: Path):
    output_dir = output_dir.resolve()
    local_root = (ROOT / '.local').resolve()
    if local_root not in output_dir.parents:
        raise SystemExit(f'output-dir must be inside {local_root}')
    output_dir.mkdir(parents=True, exist_ok=True)
    database = output_dir / 'rehearsal.db'
    snapshot = output_dir / 'pre-cutover-snapshot.db'
    report_path = output_dir / 'report.json'
    for path in (database, snapshot, report_path):
        if path.exists():
            raise SystemExit(f'refusing existing output: {path}')

    original_path = db.DB_PATH
    db.DB_PATH = str(database)
    try:
        db.migrate_db()
        db.migrate_db()
    finally:
        db.DB_PATH = original_path

    report = {'fixture': fixture, 'database': str(database),
              'snapshot': str(snapshot), 'activated': False, 'unresolved': []}
    with closing(connect(database)) as con:
        if fixture == 'empty':
            integrity, foreign_keys, reconciled = verify(con)
            report.update(integrity=integrity, foreign_key_errors=foreign_keys,
                          ledger_reconciled=reconciled)
        else:
            series_id = con.execute(
                "INSERT INTO product_series(name) VALUES ('Rehearsal')"
            ).lastrowid
            random_id = add_product(con, 'REH-RANDOM', 'Rehearsal Random',
                                    series_id, 'random_box', 'box')
            set_id = add_product(con, 'REH-SET', 'Rehearsal Set',
                                 series_id, 'sealed_set', 'set')
            conversion = add_conversion(con, set_id, random_id, 12, 1)
            con.execute(
                "INSERT INTO product_aliases(product_id, alias, alias_norm) VALUES (?, 'reh random', 'reh random')",
                (random_id,),
            )
            con.execute("UPDATE products SET sheet_ref='REH-001' WHERE id=?", (random_id,))
            if fixture == 'ambiguous':
                con.execute(
                    """INSERT INTO stock(product_id, store_id, upstairs_qty, instore_qty, claw_qty)
                       VALUES (?, (SELECT id FROM stores WHERE code='DT'), 1, 1, 1)""",
                    (random_id,),
                )
                report['unresolved'] = [{'product_id': random_id,
                                         'reason': 'claw_qty overlaps physical stock meaning'}]
            con.commit()
            with closing(connect(database)) as source, closing(connect(snapshot)) as target:
                source.backup(target)

            if fixture == 'representative':
                stores = {r['code']: r['id'] for r in con.execute(
                    "SELECT id, code FROM stores WHERE code IN ('DT','MK')")}
                locations = {(r['store_code'], r['code']): r['id'] for r in con.execute(
                    """SELECT s.code AS store_code, l.code, l.id
                       FROM inventory_locations l JOIN stores s ON s.id=l.store_id
                       WHERE s.code IN ('DT','MK')""")}
                for role in ('admin', 'manager', 'staff'):
                    for store_id in stores.values():
                        con.execute(
                            'INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)',
                            (f'rehearsal|{role}', store_id),
                        )
                con.commit()
                opening = post_inventory(con, {
                    'kind': 'opening', 'business_date': '2026-09-08',
                    'reason': 'reviewed synthetic opening', 'lines': [
                        {'product_id': set_id, 'quantity': 1, 'unit': 'set',
                         'to_location_id': locations['DT', 'upstairs'],
                         'to_disposition': 'saleable', 'expected_versions': {'to': 0}},
                        {'product_id': random_id, 'quantity': 5, 'unit': 'box',
                         'to_location_id': locations['DT', 'floor'],
                         'to_disposition': 'saleable', 'expected_versions': {'to': 0}},
                        {'product_id': random_id, 'quantity': 3, 'unit': 'box',
                         'to_location_id': locations['MK', 'floor'],
                         'to_disposition': 'saleable', 'expected_versions': {'to': 0}},
                        {'product_id': random_id, 'quantity': 1, 'unit': 'box',
                         'to_location_id': locations['MK', 'warehouse'],
                         'to_disposition': 'saleable', 'expected_versions': {'to': 0}},
                    ]}, actor=actor('admin'), request_key='rehearsal-opening')
                opened = post_inventory(con, {
                    'kind': 'open_set', 'business_date': '2026-09-08',
                    'reason': 'customer tray', 'lines': [{
                        'product_id': set_id, 'quantity': 1, 'unit': 'set',
                        'conversion_id': conversion['id'], 'conversion_factor': 12,
                        'purpose': 'customer_tray',
                        'from_location_id': locations['DT', 'upstairs'],
                        'from_disposition': 'saleable',
                        'to_location_id': locations['DT', 'upstairs'],
                        'to_disposition': 'saleable',
                        'expected_versions': {'from': 1, 'to': 0},
                    }]}, actor=actor('staff'), request_key='rehearsal-open-set')
                moved = post_inventory(con, {
                    'kind': 'move', 'business_date': '2026-09-08',
                    'reason': 'floor restock', 'lines': [{
                        'product_id': random_id, 'quantity': 5, 'unit': 'box',
                        'from_location_id': locations['DT', 'upstairs'],
                        'from_disposition': 'saleable',
                        'to_location_id': locations['DT', 'floor'],
                        'to_disposition': 'saleable',
                        'expected_versions': {'from': 1, 'to': 1},
                    }]}, actor=actor('staff'), request_key='rehearsal-move')
                consumed = post_inventory(con, {
                    'kind': 'consume', 'business_date': '2026-09-08',
                    'reason': 'synthetic sale', 'lines': [{
                        'product_id': random_id, 'quantity': 2, 'unit': 'box',
                        'from_location_id': locations['DT', 'floor'],
                        'from_disposition': 'saleable',
                        'expected_versions': {'from': 2},
                    }]}, actor=actor('staff'), request_key='rehearsal-consume')
                correction_payload = {
                    'kind': 'correction', 'business_date': '2026-09-08',
                    'reason': 'reverse synthetic sale',
                    'correction_of': consumed['document_id'], 'lines': [{
                        'product_id': random_id, 'quantity': 2, 'unit': 'box',
                        'to_location_id': locations['DT', 'floor'],
                        'to_disposition': 'saleable', 'expected_versions': {'to': 3},
                    }]}
                correction = post_inventory(
                    con, correction_payload, actor=actor('manager'),
                    request_key='rehearsal-correction')
                replay = post_inventory(
                    con, correction_payload, actor=actor('manager'),
                    request_key='rehearsal-correction')
                integrity, foreign_keys, reconciled = verify(con)
                report.update(
                    activated=True, opening_document=opening['document_id'],
                    workflow_documents=[opened['document_id'], moved['document_id'],
                                        consumed['document_id'], correction['document_id']],
                    retry_identical=replay == correction, integrity=integrity,
                    foreign_key_errors=foreign_keys, ledger_reconciled=reconciled,
                    migration_count=con.execute('SELECT COUNT(*) FROM _migrations').fetchone()[0],
                )
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', choices=('empty', 'representative', 'ambiguous'),
                        required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    rehearse(args.fixture, args.output_dir)


if __name__ == '__main__':
    main()
