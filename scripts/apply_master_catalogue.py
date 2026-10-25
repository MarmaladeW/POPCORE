"""Apply the owner-reviewed master product list in one transaction (one-time catalogue cutover).

The plan (JSON, built from the master product sheet) says, for every product in the new catalogue,
its new brand ID (`sku`), its 记账名 and extra search aliases. Applying it:

1. applies every decided, unapplied Clover match (merge or delete), exactly as the plan expects;
2. creates the Downtown-only Clover items and the new series products;
3. folds duplicates (an old product merges into its Clover item; an unreferenced duplicate
   Clover item is deleted) and deletes removed items that have no records;
4. renames SKUs and sets 记账名; the old SKU, the old 记账名 and the plan's aliases become search
   aliases, and an alias on another product that equals a product's new 记账名 is dropped;
5. attaches confirmed designs to their series product.

Any refusal rolls the whole run back. Products the plan does not list (undecided old products,
removed items with records) keep their SKU and 记账名.

    python scripts/apply_master_catalogue.py --plan plan.json [--db PATH] [--dry-run]
"""
import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'popcore_app'))
sys.path.insert(0, str(ROOT / 'scripts'))
import merge_clover_products as merger  # noqa: E402
from matcher import clean_name, normalize  # noqa: E402

ACTOR = 'catalogue-cutover'
REMOVED_NOTE = 'Removed from the catalogue at the cutover; kept only for its records.'


class Refused(Exception):
    pass


def _norm(text):
    return normalize(clean_name(text or ''))


def _search_blob(con, product_id):
    row = con.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
    return ' '.join((row[f] or '').lower() for f in ('sku', 'jizhanming', 'name_cn_en', 'brand', 'product_type', 'ip_series'))


def _add_alias(con, product_id, text, counts):
    norm = _norm(text)
    if not norm:
        return
    owner = con.execute('SELECT product_id FROM product_aliases WHERE alias_norm=?', (norm,)).fetchone()
    if owner is None:
        con.execute('INSERT INTO product_aliases (product_id, alias, alias_norm, created_by) VALUES (?,?,?,?)',
                    (product_id, text.strip(), norm, ACTOR))
        counts['aliases added'] += 1
    elif owner[0] != product_id:
        counts['aliases skipped (another product has it)'] += 1


def apply_plan(con, plan):
    """Run the whole cutover on an open connection inside one transaction; the caller commits."""
    counts = Counter()
    pairs = merger.references(con)
    broken_before = len(con.execute('PRAGMA foreign_key_check').fetchall())
    if con.execute('SELECT COUNT(*) FROM products').fetchone()[0] != plan['snapshot_products']:
        raise Refused('the product count differs from the snapshot the plan was built on; rebuild the plan')

    # 1. decided Clover matches (every refusal is collected, then the run is refused as a whole)
    refused = []
    for legacy_id, decision, target_id in plan['reviews']:
        row = con.execute('SELECT * FROM clover_match_reviews WHERE legacy_product_id=?', (legacy_id,)).fetchone()
        if row is None or row['applied_at'] or row['decision'] != decision or (decision == 'merge' and row['target_product_id'] != target_id):
            raise Refused(f'review for product {legacy_id} changed since the plan was built')
        try:
            if decision == 'delete':
                if merger.reference_count(con, legacy_id, pairs):
                    raise ValueError('it still has records')
                con.execute('DELETE FROM products WHERE id=? AND clover_item_name IS NULL', (legacy_id,))
                outcome = 'deleted'
            else:
                merger.merge(con, legacy_id, target_id)
                outcome = f'merged into {target_id}'
        except (ValueError, sqlite3.DatabaseError) as exc:
            refused.append(f'product {legacy_id} ({decision} into {target_id}): {exc}')
            continue
        con.execute('''UPDATE clover_match_reviews SET applied_at=datetime('now'), applied_by=?, applied_outcome=?
                        WHERE legacy_product_id=?''', (ACTOR, outcome, legacy_id))
        counts[f'reviews {decision}d' if decision == 'delete' else 'reviews merged'] += 1
    if refused:
        raise Refused(f'{len(refused)} Clover matches cannot be applied:\n  ' + '\n  '.join(refused))

    # 2. new products (temporary SKUs until step 4)
    ids = {}
    for item in plan['products']:
        kind, value = item['ref'].split(':', 1)
        if kind == 'id':
            ids[item['ref']] = int(value)
            continue
        clover_name = value if kind == 'dt' else None
        cur = con.execute('''INSERT INTO products (sku, name_cn_en, jizhanming, price, brand, notes, clover_item_name)
                             VALUES (?,?,?,?,?,?,?)''',
                          (f'TMP-NEW-{len(ids)}', item['name'], '', item['price'], item['category'], '', clover_name))
        ids[item['ref']] = cur.lastrowid
        counts['created (Downtown items)' if kind == 'dt' else 'created (series products)'] += 1

    def resolve(reference):
        if reference in ids:
            return ids[reference]
        kind, value = reference.split(':', 1)
        return int(value) if kind == 'id' else None

    # 3. folds and removals
    for fold in plan['folds']:
        source, target = resolve(fold['from']), resolve(fold['to'])
        if source is None:
            continue  # a Downtown item that is the same as a Markham one: never created
        product = con.execute('SELECT * FROM products WHERE id=?', (source,)).fetchone()
        try:
            if product['clover_item_name'] is None:
                merger.merge(con, source, target)
            elif merger.reference_count(con, source, pairs):
                raise ValueError('both are Clover items and the duplicate has records')
            else:
                con.execute('DELETE FROM products WHERE id=?', (source,))
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise Refused(f"fold {fold['from']} -> {fold['to']}: {exc}") from None
        counts['folded'] += 1
    for item in plan['removed']:
        product_id = resolve(item['ref'])
        if product_id is None:
            continue
        if merger.reference_count(con, product_id, pairs):
            con.execute("UPDATE products SET notes=TRIM(COALESCE(notes,'') || ' ' || ?) WHERE id=?", (REMOVED_NOTE, product_id))
            counts['removed but kept for records'] += 1
        else:
            con.execute('DELETE FROM products WHERE id=?', (product_id,))
            counts['removed'] += 1

    # 4. SKUs, 记账名 and aliases
    old = {}
    for item in plan['products']:
        product_id = ids[item['ref']]
        row = con.execute('SELECT sku, jizhanming FROM products WHERE id=?', (product_id,)).fetchone()
        if row is None:
            raise Refused(f"{item['ref']} ({item['name']}) no longer exists")
        old[product_id] = (row['sku'], row['jizhanming'])
        con.execute('UPDATE products SET sku=? WHERE id=?', (f'TMP-{product_id}', product_id))
    for item in plan['products']:
        product_id = ids[item['ref']]
        old_sku, old_jzm = old[product_id]
        con.execute('UPDATE products SET sku=?, jizhanming=? WHERE id=?', (item['sku'], item['jizhanming'], product_id))
        con.execute('UPDATE products SET search_blob=? WHERE id=?', (_search_blob(con, product_id), product_id))
        counts['renamed'] += 1
        extras = [old_jzm] if old_jzm and old_jzm.strip() != item['jizhanming'] else []
        if old_sku and not old_sku.startswith('TMP-'):
            extras.append(old_sku)
        for text in [*extras, *item['aliases']]:
            if text and text.strip() != item['jizhanming']:
                _add_alias(con, product_id, text, counts)
    for item in plan['products']:
        product_id = ids[item['ref']]
        dropped = con.execute('DELETE FROM product_aliases WHERE alias_norm=? AND product_id<>?',
                              (_norm(item['jizhanming']), product_id)).rowcount
        counts['aliases dropped (now another product\'s 记账名)'] += dropped

    # 5. confirmed designs under their series product
    series = {}
    for item in plan['products']:
        if not item['parent']:
            continue
        parent_id = ids[item['parent']]
        if parent_id not in series:
            parent = con.execute('SELECT * FROM products WHERE id=?', (parent_id,)).fetchone()
            name = parent['jizhanming'] or parent['name_cn_en']
            if con.execute('SELECT 1 FROM product_series WHERE name=?', (name,)).fetchone():
                name = f"{name} ({parent['sku']})"
            series[parent_id] = con.execute('INSERT INTO product_series(name, product_id) VALUES (?,?)',
                                            (name, parent_id)).lastrowid
            con.execute('UPDATE products SET series_id=? WHERE id=?', (series[parent_id], parent_id))
        con.execute('''UPDATE products SET series_id=?, stock_form='confirmed_design', stock_unit='piece',
                              design_name=?, identity_status='verified' WHERE id=?''',
                    (series[parent_id], item['design_name'], ids[item['ref']]))
        counts['designs attached'] += 1

    leftovers = con.execute("SELECT COUNT(*) FROM products WHERE sku LIKE 'TMP-%'").fetchone()[0]
    broken = len(con.execute('PRAGMA foreign_key_check').fetchall())
    if leftovers or broken > broken_before:
        raise Refused(f'{leftovers} temporary SKUs left, {broken - broken_before} new broken references')
    counts['products after'] = con.execute('SELECT COUNT(*) FROM products').fetchone()[0]
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--plan', required=True)
    parser.add_argument('--db')
    parser.add_argument('--dry-run', action='store_true', help='run everything, then roll back')
    args = parser.parse_args(argv)
    import db  # noqa: E402
    if args.db:
        db.DB_PATH = str(Path(args.db).resolve())
    db.migrate_db()
    plan = json.loads(Path(args.plan).read_text(encoding='utf-8'))
    con = merger.connect(db.DB_PATH)
    con.isolation_level = None
    con.execute('BEGIN IMMEDIATE')
    try:
        counts = apply_plan(con, plan)
    except Refused as exc:
        con.execute('ROLLBACK')
        print(f'refused, nothing changed: {exc}')
        return 1
    except Exception:
        con.execute('ROLLBACK')
        raise
    con.execute('ROLLBACK' if args.dry_run else 'COMMIT')
    con.close()
    for key, value in counts.items():
        print(f'{key}: {value}')
    print('dry run: rolled back' if args.dry_run else 'committed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
