"""Confirmed designs' prices, set in bulk by a manager (owner, 2026-11-12).

A confirmed design is created with its box's price (inventory_catalog.add_designs), and that copy is no
price of its own. Asked where the daily design prices should be set, the owner answered "POPCORE might be
better"; the designs are priced "based on the market price of the day". So:

- The 确认款价格 Design prices screen sets many designs' prices at once. Each saved price is POPCORE's
  price for the design (products.price, also what the shop charges) and is kept in design_prices with who
  set it, when, and the price before.
- A price a manager sets for one design in its editor (inventory_catalog.edit_design) counts the same way.
- Only such a price goes to the website (website_sync): a design's latest design_prices row, while
  products.price still holds it. A design whose price was never set, or was changed some other way since
  (a script, an import, the copy when it was added), keeps the website's price.
- Such a price never waits for a manager's OK because of its size (owner, 2026-11-14: "for these confirmed
  option because i update it every week, so we dont approve these"); only a change that would end or restart a
  website sale waits, and a Daily Shop product waits for the hour after its reset.
"""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from goods_operations import _begin, _remember, _replay
from inventory_commands import InventoryConflict, InventoryValidationError

TORONTO = ZoneInfo('America/Toronto')
# Design names that mark a secret (隐藏款) or chase design, as the daily report's names do (blueprints/sales.py).
SECRET = re.compile(r'secret|hidden|chase|隐藏|隱藏|秘密', re.I)
MAX_CENTS = 100_000_000  # $1,000,000, as the design editor allows
MAX_CHANGES = 2000


def cents(price):
    """A stored price in cents, or None when it has none."""
    try:
        value = round(float(price) * 100) if price is not None and price != '' else None
    except (TypeError, ValueError):
        return None
    return value if value is not None and value > 0 else None


def confirmed(con):
    """{design product id: {cents, set_at, set_by, source}} for every design whose latest set price is still
    its POPCORE price: the only design prices that may go to the website."""
    found = {}
    for row in con.execute(
            """SELECT d.product_id, d.cents, d.set_at, d.set_by, d.source, p.price FROM design_prices d
               JOIN products p ON p.id=d.product_id
               WHERE p.stock_form='confirmed_design'
                 AND d.id=(SELECT MAX(id) FROM design_prices WHERE product_id=d.product_id)"""):
        if cents(row['price']) == row['cents']:
            found[row['product_id']] = {'cents': row['cents'], 'set_at': row['set_at'], 'set_by': row['set_by'],
                                        'source': row['source']}
    return found


def record(con, product_id, new_cents, previous_cents, source, actor_sub):
    """Keep a price a manager set for a design, inside the caller's transaction."""
    con.execute("""INSERT INTO design_prices(product_id, cents, previous_cents, source, set_by)
                   VALUES (?, ?, ?, ?, ?)""", (product_id, new_cents, previous_cents, source, actor_sub))


def _today():
    return datetime.now(TORONTO).date().isoformat()


def _local_day(utc):
    """The Toronto date of a UTC time saved by SQLite."""
    try:
        return datetime.fromisoformat(utc.replace(' ', 'T')).replace(tzinfo=timezone.utc).astimezone(TORONTO).date().isoformat()
    except (AttributeError, ValueError):
        return None


def listing(con):
    """Every confirmed design with its box, its price, whether a manager set it, and its website items."""
    import website_sync
    today = _today()
    set_prices = confirmed(con)
    latest = {row[0]: row[1] for row in con.execute(
        """SELECT product_id, set_at FROM design_prices d WHERE id=(SELECT MAX(id) FROM design_prices
           WHERE product_id=d.product_id)""")}
    web = website_sync.design_items(con)
    boxes, designs = {}, []
    for row in con.execute(
            """SELECT d.id, d.sku, d.design_name, d.price, d.identity_status, s.product_id AS box_id,
                      b.sku AS box_sku, COALESCE(NULLIF(b.jizhanming, ''), NULLIF(b.name_cn_en, ''), b.sku, s.name) AS box_name,
                      b.name_cn_en AS box_full_name, b.price AS box_price, b.ip_series, b.brand, s.name AS series_name
               FROM products d LEFT JOIN product_series s ON s.id=d.series_id LEFT JOIN products b ON b.id=s.product_id
               WHERE d.stock_form='confirmed_design' ORDER BY d.sku, d.id"""):
        box_id = row['box_id']
        if box_id not in boxes:
            boxes[box_id] = {'id': box_id, 'sku': row['box_sku'], 'name': row['box_name'] or row['series_name'] or '其他 Other',
                             'full_name': row['box_full_name'], 'price_cents': cents(row['box_price']),
                             'ip': (row['ip_series'] or '').strip() or None, 'brand': (row['brand'] or '').strip() or None}
        mark = set_prices.get(row['id'])
        changed = latest.get(row['id'])
        designs.append({
            'id': row['id'], 'sku': row['sku'], 'name': row['design_name'] or row['sku'], 'box_id': box_id,
            'price_cents': cents(row['price']),
            # set: a manager set this price; elsewhere: one was set, and the price changed some other way since.
            'state': 'set' if mark else 'elsewhere' if changed else 'unset',
            'set_at': mark['set_at'] if mark else None, 'set_by': mark['set_by'] if mark else None,
            'changed_today': bool(changed and _local_day(changed) == today),
            'secret': bool(SECRET.search(row['design_name'] or '')),
            'web': web.get(row['id'], []),
        })
    return {'boxes': sorted(boxes.values(), key=lambda box: ((box['sku'] or '~'), box['name'])), 'designs': designs,
            'today': today, 'website': website_sync.price_link(con)}


def _read_changes(data):
    changes = data.get('changes') if isinstance(data, dict) and set(data) == {'changes'} else None
    if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_CHANGES:
        raise InventoryValidationError(f'Supply changes: 1 to {MAX_CHANGES} designs and their new prices')
    read = []
    for change in changes:
        if not isinstance(change, dict) or set(change) != {'product_id', 'cents', 'expected_cents'}:
            raise InventoryValidationError('Each change is product_id, cents and expected_cents')
        product_id, new, expected = change['product_id'], change['cents'], change['expected_cents']
        if isinstance(product_id, bool) or not isinstance(product_id, int) or product_id < 1:
            raise InventoryValidationError('product_id must be a design')
        if isinstance(new, bool) or not isinstance(new, int) or not 0 < new <= MAX_CENTS:
            raise InventoryValidationError('A price is more than $0 and at most $1,000,000')
        if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int)):
            raise InventoryValidationError('expected_cents is the price the page showed, in cents, or null')
        read.append({'product_id': product_id, 'cents': new, 'expected_cents': expected})
    if len({change['product_id'] for change in read}) != len(read):
        raise InventoryValidationError('Each design can appear only once')
    return sorted(read, key=lambda change: change['product_id'])


def _apply(con, changes, actor_sub):
    """Set the prices inside the caller's transaction; returns what changed, for the summary."""
    set_prices = confirmed(con)
    ever = {row[0] for row in con.execute('SELECT DISTINCT product_id FROM design_prices')}
    applied, moved = [], []
    for change in changes:
        row = con.execute("""SELECT d.id, d.sku, d.design_name, d.price, d.stock_form, b.price AS box_price,
                                    COALESCE(NULLIF(b.jizhanming, ''), NULLIF(b.name_cn_en, ''), b.sku) AS box_name
                             FROM products d LEFT JOIN product_series s ON s.id=d.series_id
                             LEFT JOIN products b ON b.id=s.product_id WHERE d.id=?""", (change['product_id'],)).fetchone()
        if row is None or row['stock_form'] != 'confirmed_design':
            raise InventoryValidationError(f"Product {change['product_id']} is not a confirmed design")
        old = cents(row['price'])
        if old != change['expected_cents']:
            moved.append(row['sku'])
            continue
        mark = set_prices.get(row['id'])
        if mark and mark['cents'] == change['cents']:
            continue  # already this design's set price: nothing to change
        con.execute('UPDATE products SET price=? WHERE id=?', (change['cents'] / 100, row['id']))
        record(con, row['id'], change['cents'], old, 'design_prices', actor_sub)
        applied.append({'product_id': row['id'], 'sku': row['sku'], 'name': row['design_name'] or row['sku'],
                        'box_name': row['box_name'], 'old_cents': old, 'new_cents': change['cents'],
                        # Never set before: it carried what it was created with (its box's price), not one of its own.
                        'first': row['id'] not in ever, 'box_cents': cents(row['box_price'])})
    if moved:
        raise InventoryConflict(f"These prices changed since the page was loaded: {', '.join(moved[:10])}"
                                f"{' and more' if len(moved) > 10 else ''}. Load the page again.", 'prices_changed')
    return applied


def _summary(con, applied):
    """What the change does in POPCORE and on the website, judged as the website sync will judge it."""
    import website_sync
    effects = website_sync.design_price_effects(con, [change['product_id'] for change in applied])
    for change in applied:
        change['website'] = effects.get(change['product_id'], [])
    states = [item['state'] for change in applied for item in change['website']]
    return {'changes': applied, 'counts': {
        'changes': len(applied),
        'first': sum(1 for change in applied if change['first']),
        'website_first': sum(1 for change in applied for item in change['website']
                             if item['state'] in ('send', 'daily') and item['first']),
        'send': states.count('send'), 'daily': states.count('daily'), 'held': states.count('held'),
        'same': states.count('same'), 'not_linked': sum(1 for change in applied if not change['website'])},
        'website': website_sync.price_link(con)}


def check(con, data, *, actor):
    """The summary of a change before it is saved: the change is applied, judged and rolled back."""
    changes = _read_changes(data)
    _begin(con)
    try:
        return _summary(con, _apply(con, changes, actor['sub']))
    finally:
        con.rollback()


def save(con, data, *, actor, request_key):
    """Set the prices in one transaction; a page loaded before another change is refused as a whole."""
    changes = _read_changes(data)
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'design_prices', actor, {'changes': changes})
        if prior is not None:
            con.commit()
            return prior
        result = _summary(con, _apply(con, changes, actor['sub']))
        result['saved'] = len(result['changes'])
        _remember(con, key, 'design_prices', None, actor, digest, result)
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise
