"""Import the Clover item list as the POPCORE product master.

Idempotent: a row is matched to an existing product by Clover item ID when both sides have
one, otherwise by the exact Clover item name. New products get a CL-prefixed SKU, Clover's
official name, price and category (as brand); product codes become manufacturer barcodes.
Existing products keep their jizhanming, aliases, identity and stock untouched.

    .local/frontend-venv/bin/python scripts/import_clover_items.py --csv items.csv [--dry-run]

CSV headers: clover_item_name, price, category; optional clover_item_id, product_code.
"""
import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'popcore_app'))

REQUIRED = ('clover_item_name', 'price', 'category')
OPTIONAL = ('clover_item_id', 'product_code')
IDENTIFIER = re.compile(r'^[A-Za-z0-9]{13}$')
SKU_PREFIX = 'CL'
# PDF text extraction turns "ff"/"fi"/"fl" into single ligature characters; Clover's names use plain letters.
LIGATURES = str.maketrans({'\ufb00': 'ff', '\ufb01': 'fi', '\ufb02': 'fl', '\ufb03': 'ffi', '\ufb04': 'ffl'})


def clean_name(name):
    return ' '.join(name.translate(LIGATURES).split())


def read_rows(path):
    with open(path, newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        missing = set(REQUIRED) - set(reader.fieldnames or ())
        if missing:
            raise ValueError('CSV is missing columns: ' + ', '.join(sorted(missing)))
        rows, names = [], set()
        for line, raw in enumerate(reader, start=2):
            name = clean_name(raw.get('clover_item_name') or '')
            if not name:
                raise ValueError(f'Row {line}: clover_item_name is empty')
            if name in names:
                raise ValueError(f'Row {line}: duplicate Clover item name {name!r}')
            names.add(name)
            price_text = (raw.get('price') or '').strip().replace(',', '')
            if price_text in ('', 'Variable'):
                price = None
            else:
                try:
                    price = float(price_text)
                except ValueError:
                    raise ValueError(f'Row {line}: price {price_text!r} is not a number') from None
                if price < 0:
                    raise ValueError(f'Row {line}: price must not be negative')
            item_id = (raw.get('clover_item_id') or '').strip() or None
            if item_id and not IDENTIFIER.fullmatch(item_id):
                raise ValueError(f'Row {line}: clover_item_id must be 13 letters or digits')
            rows.append({
                'clover_item_name': name, 'price': price,
                'category': (raw.get('category') or '').strip(),
                'clover_item_id': item_id,
                'product_code': (raw.get('product_code') or '').strip() or None,
            })
    if not rows:
        raise ValueError('CSV has no item rows')
    return rows


def _search_blob(product):
    return ' '.join((product.get(field) or '').lower() for field in
                    ('sku', 'jizhanming', 'name_cn_en', 'brand', 'product_type', 'ip_series'))


def _next_sku(con):
    row = con.execute("SELECT sku FROM products WHERE sku LIKE ? ORDER BY sku DESC LIMIT 1",
                      (SKU_PREFIX + '%',)).fetchone()
    last = int(row[0][len(SKU_PREFIX):]) if row and row[0][len(SKU_PREFIX):].isdigit() else 0
    return f'{SKU_PREFIX}{last + 1:05d}'


def _existing(con, row):
    if row['clover_item_id']:
        found = con.execute('SELECT * FROM products WHERE clover_item_id=?', (row['clover_item_id'],)).fetchone()
        if found:
            return found
    found = con.execute('SELECT * FROM products WHERE clover_item_name=?', (row['clover_item_name'],)).fetchone()
    if found:
        return found
    # A name imported before ligature cleaning matches by its cleaned form and is renamed in place.
    for candidate in con.execute('SELECT * FROM products WHERE clover_item_name IS NOT NULL'):
        if clean_name(candidate['clover_item_name']) == row['clover_item_name']:
            return candidate
    return None


def import_items(con, rows):
    """Apply the rows inside one transaction; returns counts. Never touches stock or identity."""
    con.row_factory = sqlite3.Row
    counts = {'created': 0, 'updated': 0, 'unchanged': 0, 'barcodes': 0}
    con.execute('BEGIN IMMEDIATE')
    try:
        for row in rows:
            current = _existing(con, row)
            if current is None:
                product = {
                    'sku': _next_sku(con), 'name_cn_en': row['clover_item_name'], 'jizhanming': '',
                    'price': row['price'], 'ip_series': '', 'product_type': '', 'brand': row['category'],
                    'notes': '', 'clover_item_name': row['clover_item_name'],
                    'clover_item_id': row['clover_item_id'],
                }
                product['search_blob'] = _search_blob(product)
                cur = con.execute(
                    'INSERT INTO products (' + ', '.join(product) + ') VALUES ('
                    + ', '.join('?' for _ in product) + ')', list(product.values()))
                product_id = cur.lastrowid
                counts['created'] += 1
            else:
                product_id = current['id']
                updates = {
                    'name_cn_en': row['clover_item_name'], 'price': row['price'],
                    'brand': row['category'], 'clover_item_name': row['clover_item_name'],
                    'clover_item_id': row['clover_item_id'] or current['clover_item_id'],
                }
                merged = {**dict(current), **updates}
                updates['search_blob'] = _search_blob(merged)
                if all(current[key] == value for key, value in updates.items()):
                    counts['unchanged'] += 1
                else:
                    con.execute('UPDATE products SET ' + ', '.join(f'{k}=?' for k in updates)
                                + ' WHERE id=?', [*updates.values(), product_id])
                    counts['updated'] += 1
            if row['product_code']:
                inserted = con.execute(
                    """INSERT OR IGNORE INTO product_barcodes
                       (code, product_id, code_kind, input_unit, quantity_per_scan)
                       VALUES (?, ?, 'manufacturer', 'piece', 1)""",
                    (row['product_code'], product_id)).rowcount
                counts['barcodes'] += inserted
        con.commit()
    except Exception:
        con.rollback()
        raise
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--csv', required=True, help='Clover item list CSV')
    parser.add_argument('--db', help='SQLite path (default: the application database)')
    parser.add_argument('--dry-run', action='store_true', help='Validate and report without writing')
    args = parser.parse_args(argv)
    import db  # noqa: E402  (applies the schema migrations to the chosen database)
    if args.db:
        db.DB_PATH = str(Path(args.db).resolve())
    db.migrate_db()
    rows = read_rows(args.csv)
    con = sqlite3.connect(db.DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        if args.dry_run:
            existing = sum(1 for row in rows if _existing(con, row) is not None)
            print(f'{len(rows)} rows: {len(rows) - existing} would be created, {existing} matched')
            return 0
        counts = import_items(con, rows)
    finally:
        con.close()
    print(', '.join(f'{key} {value}' for key, value in counts.items()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
