"""Shared by the one-off catalog fixes scripts/normalize_brands.py and scripts/fill_ip_series.py.

- Catalog: every product, the 盘点 card it is counted on (as stock_counting._family) and the IP the Dex gives it.
- run(): plan the fix, write the plan to a CSV for the owner, and with --apply write it after an online backup.
  A dry run only reads. --apply plans again inside one write transaction and writes only the rows marked change.
- Each written row gets its search text rebuilt as the product save builds it (blueprints/products.py), and a
  remembered 日报 name pick that matched the product before still matches it after (the signature includes brand
  and 系列, so without this a spelling fix would send good picks back for review)."""
import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'popcore_app'))  # first, ahead of scripts/ (it has its own inventory_opening.py)
os.environ.setdefault('AUTH0_DOMAIN', 'catalog-fix.invalid')
os.environ.setdefault('DISABLE_SCHEDULER', '1')

import db  # noqa: E402
import dex_book  # noqa: E402
from matcher import report_identity_signature  # noqa: E402

SEARCH_FIELDS = ('sku', 'jizhanming', 'name_cn_en', 'brand', 'product_type', 'ip_series')
COLUMNS = ('action', 'why', 'product_id', 'sku', 'name', 'before', 'after')


class Catalog:
    def __init__(self, con):
        self.products = {row['id']: dict(row) for row in con.execute('SELECT * FROM products ORDER BY id')}
        owner = {row['id']: row['product_id'] for row in con.execute('SELECT id, product_id FROM product_series')}
        self.series_name = {row['id']: row['name'] for row in con.execute('SELECT id, name FROM product_series')}
        set_box = {}
        for source, target in con.execute("""SELECT c.source_product_id, c.target_product_id FROM product_conversions c
                                             JOIN products s ON s.id=c.source_product_id
                                             WHERE s.stock_form='sealed_set' ORDER BY c.version"""):
            set_box[source] = target  # the latest conversion wins
        self.card, self.members = {}, defaultdict(list)
        for product_id, product in self.products.items():
            if product['stock_form'] == 'sealed_set' and set_box.get(product_id) in self.products:
                product = self.products[set_box[product_id]]
            card = ('product', product['id'])
            if product['stock_form'] == 'confirmed_design' and product['series_id'] is not None:
                head = owner.get(product['series_id'])
                card = ('product', head) if head in self.products else ('series', product['series_id'])
            self.card[product_id] = card
            self.members[card].append(product_id)
        entries = {row['product_id']: row['id'] for row in con.execute(
            'SELECT id, product_id FROM dex_entries WHERE product_id IS NOT NULL AND deleted_at IS NULL ORDER BY id')}
        named = dex_book.names(con, list(entries.values())) if entries else {}
        self.dex_ip = {product_id: named[entry]['ip'] for product_id, entry in entries.items()
                       if named.get(entry) and named[entry].get('ip')}

    def head(self, product_id):
        """The product a card opens on: its head, else (a series no product owns) its first design."""
        kind, ident = self.card[product_id]
        return ident if kind == 'product' else min(self.members[(kind, ident)])

    def card_ip(self, product_id):
        """The Dex IP (or partner) of the product's card: its head's, else the one most of its members have."""
        card = self.card[product_id]
        if card[0] == 'product' and card[1] in self.dex_ip:
            return self.dex_ip[card[1]]
        tags = Counter((tag['id'], tag['kind'], tag['name'])
                       for tag in (self.dex_ip.get(member) for member in self.members[card]) if tag)
        if not tags:
            return None
        ident, kind, name = tags.most_common(1)[0][0]
        return {'id': ident, 'kind': kind, 'name': name}

    def name(self, product_id):
        product = self.products[product_id]
        return (product['jizhanming'] or product['name_cn_en'] or '').strip()


def write(con, product_id, field, value):
    """Set one product's brand or ip_series, rebuild its search text and keep its good name picks good."""
    row = dict(con.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone())
    signature = report_identity_signature(row)
    old_words, gone = (row['search_blob'] or '').split(), set(str(row[field] or '').lower().split())
    row[field] = value
    blob = ' '.join((row[name] or '').lower() for name in SEARCH_FIELDS)
    kept = set(blob.split())
    extra = [word for word in dict.fromkeys(old_words) if word not in kept and word not in gone]
    if extra:  # never drop a search word the six fields do not hold (none on 2026-11-20, but keep it safe)
        blob = f"{blob} {' '.join(extra)}"
    con.execute(f'UPDATE products SET {field}=?, search_blob=? WHERE id=?', (value, blob, product_id))
    con.execute('UPDATE report_match_choices SET identity_signature=? WHERE product_id=? AND identity_signature=?',
                (report_identity_signature(row), product_id, signature))


def write_csv(path, rows, columns):
    with open(path, 'w', newline='', encoding='utf-8-sig') as handle:  # the BOM lets Excel read the Chinese names
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def connect(path):
    con = sqlite3.connect(str(path), timeout=db.SQLITE_BUSY_TIMEOUT)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    return con


def run(con, field, plan, apply=False):
    """The plan's rows; with apply they are written in one transaction, planned again inside it."""
    if not apply:
        return plan(con, Catalog(con))
    con.execute('BEGIN IMMEDIATE')
    try:
        rows = plan(con, Catalog(con))
        for row in rows:
            if row['action'] == 'change':
                write(con, row['product_id'], field, row['after'])
        con.commit()
    except Exception:
        con.rollback()
        raise
    return rows


def main(argv, *, doc, field, plan, out, prefix, columns=COLUMNS):
    parser = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', default=str(ROOT / '.local' / out), help=f'folder for the CSV (default .local/{out})')
    parser.add_argument('--apply', action='store_true', help='write the changes (after the backup)')
    parser.add_argument('--backup', help='with --apply: where the online backup goes; must not exist yet')
    args = parser.parse_args(argv)
    if args.apply and (not args.backup or Path(args.backup).exists()):
        parser.error('--apply needs --backup PATH, a file that does not exist yet')
    database = Path(db.DB_PATH)
    if not database.is_file():
        parser.error(f'No database at {database} (set POPCORE_DB_PATH)')
    folder = Path(args.out)
    folder.mkdir(parents=True, exist_ok=True)
    con = connect(database)
    try:
        if args.apply:
            backup = sqlite3.connect(args.backup)
            try:
                con.backup(backup)
            finally:
                backup.close()
        rows = run(con, field, plan, apply=args.apply)
    finally:
        con.close()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    report = folder / f"{prefix}-{'applied' if args.apply else 'dry-run'}-{stamp}.csv"
    write_csv(report, rows, columns)
    print(json.dumps({'database': str(database), 'applied': args.apply, 'backup': args.backup if args.apply else None,
                      'changed': sum(row['action'] == 'change' for row in rows),
                      'listed': sum(row['action'] == 'list' for row in rows),
                      'by_why': dict(Counter(f"{row['action']}: {row['why']}" for row in rows).most_common()),
                      'csv': str(report)}, ensure_ascii=False, indent=1))
    return 0
