"""Editable legacy → Clover product decisions, applied one product at a time.

A review sheet (scripts/load_clover_match_review.py) seeds one row per legacy product. Managers
change decisions in the app until they apply them; applying runs the same merge as
scripts/merge_clover_products.py in its own transaction and records the outcome. A refused merge
or delete changes nothing and leaves the row open.
"""
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import merge_clover_products as merger  # noqa: E402

SOURCES = {'auto': 'auto', 'you': 'owner', 'owner': 'owner', 'claude': 'claude', 'claude (fixed auto)': 'claude'}
CONFIDENCE = ('high', 'medium', 'low')


def _text(value):
    return ' '.join(str(value).split()) if value not in (None, '') else ''


def reference_counts(con):
    """Rows referencing each product id, across every table with a products(id) foreign key."""
    counts = Counter()
    for table, column in merger.references(con):
        for product_id, count in con.execute(
                f'SELECT "{column}", COUNT(*) FROM "{table}" WHERE "{column}" IS NOT NULL GROUP BY 1'):
            counts[product_id] += count
    return counts


def _status(row):
    if row['applied_at']:
        return 'applied'
    if not row['legacy_exists']:
        return 'missing'
    if row['decision'] is None:
        return 'undecided'
    return 'keep' if row['decision'] == 'keep' else 'ready'


def list_reviews(con):
    counts = reference_counts(con)
    rows = con.execute(
        '''SELECT r.*, lp.id IS NOT NULL AS legacy_exists, lp.price AS legacy_price,
                  tp.clover_item_name AS target_name, tp.price AS target_price
             FROM clover_match_reviews r
             LEFT JOIN products lp ON lp.id = r.legacy_product_id AND lp.clover_item_name IS NULL
             LEFT JOIN products tp ON tp.id = r.target_product_id
            ORDER BY r.legacy_product_id''').fetchall()
    items = [{
        'legacy_product_id': row['legacy_product_id'], 'legacy_sku': row['legacy_sku'],
        'legacy_jizhanming': row['legacy_jizhanming'], 'legacy_name': row['legacy_name'],
        'legacy_price': row['legacy_price'],
        'references': counts.get(row['legacy_product_id'], 0) if row['legacy_exists'] else None,
        'decision': row['decision'], 'target_product_id': row['target_product_id'],
        'target_name': row['target_name'], 'target_price': row['target_price'],
        'source': row['source'], 'confidence': row['confidence'], 'note': row['note'],
        'updated_by': row['updated_by'], 'updated_at': row['updated_at'],
        'applied_at': row['applied_at'], 'applied_by': row['applied_by'], 'applied_outcome': row['applied_outcome'],
        'status': _status(row),
    } for row in rows]
    unlisted = con.execute(
        '''SELECT COUNT(*) FROM products p WHERE p.clover_item_name IS NULL
              AND NOT EXISTS (SELECT 1 FROM clover_match_reviews r WHERE r.legacy_product_id = p.id)''').fetchone()[0]
    clover = [dict(row) for row in con.execute(
        '''SELECT id, clover_item_name AS name, price, brand FROM products
            WHERE clover_item_name IS NOT NULL ORDER BY clover_item_name COLLATE NOCASE''')]
    return {'items': items, 'clover_products': clover, 'unlisted_legacy': unlisted}


def update_review(con, legacy_id, payload, actor):
    """Change one open decision. Raises LookupError or ValueError."""
    row = con.execute('SELECT * FROM clover_match_reviews WHERE legacy_product_id=?', (legacy_id,)).fetchone()
    if row is None:
        raise LookupError('This product is not in the Clover match review.')
    if row['applied_at']:
        raise ValueError('This decision was already applied and can no longer change.')
    decision = payload.get('decision') or None
    if decision not in (None, 'merge', 'keep', 'delete'):
        raise ValueError('Decision must be merge, keep, delete or empty.')
    target = None
    if decision == 'merge':
        try:
            target = int(payload.get('target_product_id'))
        except (TypeError, ValueError):
            raise ValueError('Choose the Clover product to merge into.') from None
        if con.execute('SELECT 1 FROM products WHERE id=? AND clover_item_name IS NOT NULL', (target,)).fetchone() is None:
            raise ValueError('The chosen product is not a Clover product.')
    note = _text(payload['note'])[:500] if 'note' in payload else row['note']
    con.execute('''UPDATE clover_match_reviews
                      SET decision=?, target_product_id=?, note=?, source='edited',
                          updated_by=?, updated_at=datetime('now')
                    WHERE legacy_product_id=?''', (decision, target, note or None, actor, legacy_id))


def _decision_from_sheet(con, value):
    value = _text(value)
    if not value:
        return None, None, None
    if value.lower() in ('keep', 'delete'):
        return value.lower(), None, None
    try:
        return 'merge', merger.resolve_target(con, value)['id'], None
    except ValueError:
        return None, None, f'Sheet decision not found on Clover: {value}'


def load_rows(con, rows, actor, replace=False):
    """Seed review rows from a review sheet. Rows edited or applied in the app are left alone
    unless replace=True (applied rows are never replaced). Returns outcome counts."""
    outcomes = Counter()
    for raw in rows:
        try:
            legacy_id = int(raw.get('legacy_id'))
        except (TypeError, ValueError):
            outcomes['no legacy id'] += 1
            continue
        legacy = con.execute('SELECT * FROM products WHERE id=?', (legacy_id,)).fetchone()
        existing = con.execute('SELECT * FROM clover_match_reviews WHERE legacy_product_id=?', (legacy_id,)).fetchone()
        if existing and (existing['applied_at'] or (existing['source'] == 'edited' and not replace)):
            outcomes['kept app edit' if not existing['applied_at'] else 'already applied'] += 1
            continue
        if legacy is None or legacy['clover_item_name'] is not None:
            outcomes['legacy product not found'] += 1
            continue
        # Sheet ids come from whichever database produced the sheet; only trust an id whose
        # 记账名 and name still match.
        if (_text(raw.get('jizhanming')), _text(raw.get('legacy_name'))) != (_text(legacy['jizhanming']), _text(legacy['name_cn_en'])):
            outcomes['legacy name differs'] += 1
            continue
        decision, target, problem = _decision_from_sheet(con, raw.get('decision'))
        confidence = _text(raw.get('confidence')).lower()
        note = '; '.join(filter(None, (_text(raw.get('why')), problem))) or None
        con.execute(
            '''INSERT INTO clover_match_reviews
                   (legacy_product_id, legacy_sku, legacy_jizhanming, legacy_name, decision, target_product_id,
                    source, confidence, note, updated_by, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))
               ON CONFLICT(legacy_product_id) DO UPDATE SET
                   legacy_sku=excluded.legacy_sku, legacy_jizhanming=excluded.legacy_jizhanming,
                   legacy_name=excluded.legacy_name, decision=excluded.decision,
                   target_product_id=excluded.target_product_id, source=excluded.source,
                   confidence=excluded.confidence, note=excluded.note,
                   updated_by=excluded.updated_by, updated_at=excluded.updated_at''',
            (legacy_id, legacy['sku'], legacy['jizhanming'], legacy['name_cn_en'], decision, target,
             SOURCES.get(_text(raw.get('filled_by')).lower(), 'sheet'),
             confidence if confidence in CONFIDENCE else None, note, actor))
        outcomes['problem' if problem else 'loaded'] += 1
    return outcomes


def apply_reviews(path, legacy_ids, actor):
    """Apply decided rows, each in its own transaction. Returns one result per requested id."""
    con = merger.connect(path)
    results = []
    try:
        pairs = merger.references(con)
        for legacy_id in legacy_ids:
            con.execute('BEGIN IMMEDIATE')
            try:
                row = con.execute('SELECT * FROM clover_match_reviews WHERE legacy_product_id=?', (legacy_id,)).fetchone()
                if row is None:
                    raise ValueError('not in the Clover match review')
                if row['applied_at']:
                    raise ValueError('already applied')
                if row['decision'] in (None, 'keep'):
                    raise ValueError('nothing to apply' if row['decision'] is None else 'kept; nothing to apply')
                legacy = con.execute('SELECT * FROM products WHERE id=? AND clover_item_name IS NULL', (legacy_id,)).fetchone()
                if legacy is None:
                    raise ValueError('the old product no longer exists')
                if (_text(legacy['jizhanming']), _text(legacy['name_cn_en'])) != (_text(row['legacy_jizhanming']), _text(row['legacy_name'])):
                    raise ValueError('the old product changed since it was reviewed; reload the review')
                if row['decision'] == 'delete':
                    if merger.reference_count(con, legacy_id, pairs):
                        raise ValueError('it still has sales or stock records; merge or keep it instead')
                    con.execute('DELETE FROM products WHERE id=?', (legacy_id,))
                    outcome = 'deleted'
                else:
                    merged = merger.merge(con, legacy_id, row['target_product_id'])
                    target = con.execute('SELECT clover_item_name FROM products WHERE id=?', (row['target_product_id'],)).fetchone()
                    outcome = f"merged into {target['clover_item_name']}"
                    if merged['carried']:
                        outcome += f" (carried {', '.join(merged['carried'])})"
                con.execute('''UPDATE clover_match_reviews SET applied_at=datetime('now'), applied_by=?, applied_outcome=?
                                WHERE legacy_product_id=?''', (actor, outcome, legacy_id))
                con.commit()
                results.append({'legacy_product_id': legacy_id, 'status': 'applied', 'message': outcome})
            except (ValueError, sqlite3.IntegrityError) as exc:
                con.rollback()
                results.append({'legacy_product_id': legacy_id, 'status': 'refused', 'message': str(exc)})
    finally:
        con.close()
    return results
