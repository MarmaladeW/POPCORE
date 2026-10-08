"""Match legacy products to the Clover product master, then merge them on approval.

review:  scores every legacy product (one without a Clover item name) against the Clover
         products and writes a review CSV. Fill the `decision` column with the Clover product id
         to merge into, `keep`, or `delete` (only unreferenced products can be deleted).
apply:   for each decided row, in its own transaction: re-point every table that references the
         legacy product to the Clover product, record the legacy 记账名 and name as aliases, carry
         identity fields the Clover product lacks, then delete the legacy row. A row whose
         references collide (both products hold stock at the same location, for example) is
         refused and listed; nothing is forced.

    python scripts/merge_clover_products.py review --out review.csv [--db PATH]
    python scripts/merge_clover_products.py apply --csv review.csv [--db PATH] [--dry-run]
"""
import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'popcore_app'))
from matcher import _score_pair_jzm, _variant_tokens, clean_name, identity_conflicts, normalize, normalize_spaced  # noqa: E402

CONFIDENT, CHECK = 88, 55
# Carried to the Clover product when its own value is empty; never overwrites.
CARRY_FIELDS = ('jizhanming', 'ip_series', 'product_type', 'series_id', 'stock_form', 'stock_unit',
                'design_name', 'identity_status', 'boxes_per_dan', 'hidden_count', 'hidden_has_small',
                'hidden_has_large', 'hidden_prob_small', 'hidden_prob_large', 'is_bestseller', 'notes',
                'style_notes', 'release_date', 'edition_size', 'channel', 'hidden', 'sheet_ref')
# Leftover rows in these tables after a re-point are exact duplicates of what the Clover
# product already has, so they can go. Everything else that is left over is a collision.
DUPLICATE_OK = {'product_aliases', 'report_match_choices', 'product_barcodes'}
REVIEW_COLUMNS = ('legacy_id', 'legacy_sku', 'jizhanming', 'legacy_name', 'legacy_price', 'references',
                  'suggested_clover_id', 'suggested_name', 'suggested_price', 'score',
                  'runner_up_id', 'runner_up_score', 'verdict', 'decision')


def connect(path):
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con


def references(con):
    """(table, column) pairs that reference products(id), from the live schema."""
    pairs = []
    for table in [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]:
        for fk in con.execute(f'PRAGMA foreign_key_list("{table}")'):
            if fk['table'] == 'products':
                pairs.append((table, fk['from']))
    return pairs


def reference_count(con, product_id, pairs):
    return sum(con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}"=?',
                           (product_id,)).fetchone()[0] for table, column in pairs)


FILLER = {'series', 'figure', 'figures', 'the', 'a', 'of', 'and', 'blind', 'box', 'x', 'pop', 'mart'}


def _tokens(text):
    return set(re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]', normalize_spaced(text)))


def _latin_tokens(text):
    """Latin/digit words of a name, minus filler; legacy names mix Chinese and English."""
    return {w for w in re.findall(r'[a-z0-9%.]+', normalize_spaced(text)) if w not in FILLER}


def score(legacy, clover):
    """0-100 similarity between a legacy product and a Clover product."""
    cn = normalize(clover['name_cn_en'])
    candidate_latin = _latin_tokens(clover['name_cn_en'])
    best = 0
    for field in ('name_cn_en', 'jizhanming'):
        value = legacy[field] or ''
        if not value:
            continue
        best = max(best, _score_pair_jzm(normalize(value), cn))
        tokens, candidate = _tokens(value), _tokens(clover['name_cn_en'])
        if tokens and candidate:
            best = max(best, int(100 * len(tokens & candidate) / len(tokens | candidate)))
        # The English part of a mixed legacy name, contained in the Clover name.
        latin = _latin_tokens(value)
        if latin and candidate_latin:
            contained = int(100 * len(latin & candidate_latin) / len(latin))
            if len(latin) == 1:
                contained = min(contained, 70)
            if _variant_tokens(normalize(value)) != _variant_tokens(cn):
                contained = min(contained, 75)
            if identity_conflicts(value, {'name_cn_en': clover['name_cn_en']}):
                contained = min(contained, 75)
            best = max(best, contained)
    if best and legacy['price'] is not None and clover['price'] is not None:
        if abs(legacy['price'] - clover['price']) < 0.005:
            best = min(99, best + 5)
        elif legacy['price'] and abs(legacy['price'] - clover['price']) / legacy['price'] > 0.3:
            best = max(0, best - 10)
    return best


def review(con):
    pairs = references(con)
    clover = [dict(r) for r in con.execute(
        'SELECT id, name_cn_en, price FROM products WHERE clover_item_name IS NOT NULL ORDER BY id')]
    rows = []
    for legacy in con.execute('SELECT * FROM products WHERE clover_item_name IS NULL ORDER BY id'):
        ranked = sorted(((score(legacy, c), c) for c in clover), key=lambda x: (-x[0], x[1]['id']))
        top, second = (ranked[0] if ranked else (0, None)), (ranked[1] if len(ranked) > 1 else (0, None))
        verdict = ('confident' if top[0] >= CONFIDENT and top[0] - second[0] >= 8
                   else 'check' if top[0] >= CHECK else 'none')
        rows.append({
            'legacy_id': legacy['id'], 'legacy_sku': legacy['sku'], 'jizhanming': legacy['jizhanming'],
            'legacy_name': legacy['name_cn_en'], 'legacy_price': legacy['price'],
            'references': reference_count(con, legacy['id'], pairs),
            'suggested_clover_id': top[1]['id'] if top[1] and top[0] >= CHECK else '',
            'suggested_name': top[1]['name_cn_en'] if top[1] and top[0] >= CHECK else '',
            'suggested_price': top[1]['price'] if top[1] and top[0] >= CHECK else '',
            'score': top[0], 'runner_up_id': second[1]['id'] if second[1] else '',
            'runner_up_score': second[0], 'verdict': verdict,
            'decision': top[1]['id'] if verdict == 'confident' else '',
        })
    return rows


def write_review(rows, path):
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def merge(con, legacy_id, clover_id):
    """Fold one legacy product into a Clover product. Raises ValueError when it cannot be done safely."""
    legacy = con.execute('SELECT * FROM products WHERE id=?', (legacy_id,)).fetchone()
    clover = con.execute('SELECT * FROM products WHERE id=?', (clover_id,)).fetchone()
    if legacy is None:
        raise ValueError('legacy product no longer exists (already merged?)')
    if clover is None or clover['clover_item_name'] is None:
        raise ValueError('target is not a Clover product')
    if legacy['clover_item_name'] is not None:
        raise ValueError('source is itself a Clover product')
    con.execute('SAVEPOINT merge_product')
    try:
        for table, column in references(con):
            con.execute(f'UPDATE OR IGNORE "{table}" SET "{column}"=? WHERE "{column}"=?', (clover_id, legacy_id))
            leftover = con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}"=?', (legacy_id,)).fetchone()[0]
            if leftover and table in DUPLICATE_OK:
                con.execute(f'DELETE FROM "{table}" WHERE "{column}"=?', (legacy_id,))
            elif leftover:
                raise ValueError(f'{table}: both products already have rows that would collide; '
                                 'resolve that stock or record first')
        for text in (legacy['jizhanming'], legacy['name_cn_en']):
            norm = normalize(clean_name(text or ''))
            if norm:
                con.execute('INSERT OR IGNORE INTO product_aliases (product_id, alias, alias_norm) VALUES (?, ?, ?)',
                            (clover_id, text.strip(), norm))
        updates = {field: legacy[field] for field in CARRY_FIELDS
                   if clover[field] in (None, '', 0, '0') and legacy[field] not in (None, '', 0, '0')}
        merged = {**dict(clover), **updates}
        updates['search_blob'] = ' '.join((merged.get(f) or '').lower() for f in
                                          ('sku', 'jizhanming', 'name_cn_en', 'brand', 'product_type', 'ip_series'))
        con.execute('UPDATE products SET ' + ', '.join(f'"{k}"=?' for k in updates) + ' WHERE id=?',
                    [*updates.values(), clover_id])
        con.execute('DELETE FROM products WHERE id=?', (legacy_id,))
        con.execute('RELEASE merge_product')
    except Exception:
        con.execute('ROLLBACK TO merge_product')
        con.execute('RELEASE merge_product')
        raise
    return {'legacy_id': legacy_id, 'clover_id': clover_id, 'carried': sorted(k for k in updates if k != 'search_blob')}


def apply(con, rows, dry_run=False):
    pairs = references(con)
    results = []
    for row in rows:
        decision = (row.get('decision') or '').strip()
        legacy_id = int(row['legacy_id'])
        if not decision:
            continue
        con.execute('BEGIN IMMEDIATE')
        try:
            if decision == 'keep':
                outcome = 'kept'
            elif decision == 'delete':
                if reference_count(con, legacy_id, pairs):
                    raise ValueError('still referenced; merge it instead of deleting')
                deleted = con.execute('DELETE FROM products WHERE id=? AND clover_item_name IS NULL', (legacy_id,)).rowcount
                outcome = 'deleted' if deleted else 'already gone'
            elif decision.isdigit():
                outcome = 'merged into %d (carried: %s)' % (int(decision), ', '.join(merge(con, legacy_id, int(decision))['carried']) or 'nothing')
            else:
                raise ValueError('decision must be a Clover product id, keep or delete')
            if dry_run:
                con.rollback()
            else:
                con.commit()
            results.append((legacy_id, 'ok', outcome))
        except (ValueError, sqlite3.IntegrityError) as exc:
            con.rollback()
            results.append((legacy_id, 'refused', str(exc)))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    rev = sub.add_parser('review'); rev.add_argument('--out', required=True); rev.add_argument('--db')
    app = sub.add_parser('apply'); app.add_argument('--csv', required=True); app.add_argument('--db')
    app.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    import db  # noqa: E402
    if args.db:
        db.DB_PATH = str(Path(args.db).resolve())
    db.migrate_db()
    con = connect(db.DB_PATH)
    try:
        if args.command == 'review':
            rows = review(con)
            write_review(rows, args.out)
            counts = {v: sum(r['verdict'] == v for r in rows) for v in ('confident', 'check', 'none')}
            print(f"{len(rows)} legacy products reviewed: {counts}; written to {args.out}")
        else:
            with open(args.csv, newline='', encoding='utf-8-sig') as handle:
                rows = list(csv.DictReader(handle))
            for legacy_id, status, outcome in apply(con, rows, dry_run=args.dry_run):
                print(f'{legacy_id}: {status}: {outcome}')
    finally:
        con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
