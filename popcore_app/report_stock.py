"""A live store's daily report takes its sales off the floor (owner, 2026-10-29).

Clover is not connected yet, so the pasted daily report is how a live store's sales leave
stock. Whenever a day's sales change (a report is pasted, replaced or edited), each product's
sales that day are compared with what that day already took off, and only the difference moves:
more sold comes off the floor's saleable stock, less sold goes back. Stock never goes below
zero: what the floor does not have is not taken off, and the next save of that day tries again.
Only days from the store's go-live date on count, because the opening counts were taken before
them. A sealed set sold takes its set size in boxes, since the floor keeps blind boxes as boxes
only; a set with no recorded set size takes nothing, because the size is never assumed.

Every mismatch is kept as a notice for managers and admins (owner, 2026-10-29): sales that could
not come off (not counted here, not enough on the floor, set size unknown) and sales taken off a
product the floor counted the same day, which may already have left them out. A notice closes by
itself once a later save takes the rest off, or when a manager checks it: checking a shortfall
means the floor count already reflects those sales, so they are never taken off later.
"""
import uuid

from goods_operations import _balance
from inventory_commands import (InventoryConflict, InventoryError, _post_inventory_in_transaction,
                                require_inventory_access, store_is_live)

REPORT_REASON = '日报 Daily report'
RETURN_REASON = '日报更正 Daily report corrected'
# Sales the floor could not take off; a check waives them. counted_same_day only warns.
SHORT_REASONS = ('not_counted', 'not_enough_on_floor', 'set_size_unknown')


def live_since(con, store_id):
    """The business date a store went live, or None while it is not live."""
    row = con.execute(
        """SELECT MIN(d.business_date) FROM inventory_scope_state s
           JOIN inventory_documents d ON d.id=s.opening_document_id
           WHERE s.store_id=? AND s.opening_verified=1""", (store_id,)).fetchone()
    return row[0] if row else None


def _stock_product(con, product):
    """(product that holds the stock, units per one sold). A sealed set sells as its boxes, by the
    set size recorded for it; with no set size recorded the units are unknown (None)."""
    if product['stock_form'] != 'sealed_set':
        return product, 1
    box = con.execute(
        """SELECT p.*, c.output_per_input AS boxes_per_set FROM product_conversions c
           JOIN products p ON p.id=c.target_product_id
           WHERE c.source_product_id=? ORDER BY c.version DESC LIMIT 1""", (product['id'],)).fetchone()
    if box is None or not box['boxes_per_set'] or box['boxes_per_set'] < 1:
        return product, None
    return box, box['boxes_per_set']


def _loose(con, product_id, location_id):
    """Saleable boxes not kept in an opened set: what a sale can take without asking."""
    kept = con.execute(
        """SELECT COALESCE(SUM(remaining_qty), 0) FROM inventory_open_sets
           WHERE random_product_id=? AND location_id=? AND remaining_qty>0""",
        (product_id, location_id)).fetchone()[0]
    return max(0, _balance(con, product_id, location_id, 'saleable')['quantity'] - kept)


def _counted_since(con, location_id, day, product_id, since):
    """When the floor last counted this product on `day` after `since` (None: any time that day)."""
    row = con.execute(
        """SELECT MAX(c.created_at) FROM inventory_counts c JOIN inventory_count_lines l ON l.count_id=c.id
           WHERE c.location_id=? AND c.business_date=? AND c.status='approved' AND l.product_id=?
             AND (? IS NULL OR c.created_at > ?)""", (location_id, day, product_id, since, since)).fetchone()
    return row[0]


def _floor(con, store_id):
    return con.execute(
        "SELECT id FROM inventory_locations WHERE store_id=? AND code='floor' AND is_active=1", (store_id,),
    ).fetchone()


def _require_manager(con, actor, store_id):
    """Only managers and admins with this store's inventory access paste reports (owner, 2026-10-29)."""
    try:
        require_inventory_access(con, actor, [store_id], 'manager')
    except PermissionError as exc:
        raise InventoryError('Only a manager or admin with access to this store can change its daily report',
                             'inventory_forbidden', 403) from exc


def _name(product):
    return product['jizhanming'] or product['name_cn_en'] or product['sku']


def sync_report_stock(con, actor, store_id, store_code, day):
    """Inside the caller's write transaction: move a live store's floor stock to match its daily
    sales on `day`. Returns what moved and what could not, or None when the day does not count."""
    since = live_since(con, store_id)
    if not store_is_live(con, store_id) or since is None or day < since:
        return None
    floor = _floor(con, store_id)
    if floor is None:
        return None
    _require_manager(con, actor, store_id)
    wanted, products, unknown_size = {}, {}, set()
    for row in con.execute(
            """SELECT p.*, SUM(s.qty_sold) AS sold
               FROM daily_sales s JOIN products p ON p.id=s.product_id
               WHERE s.date=? AND s.store=? GROUP BY p.id HAVING SUM(s.qty_sold) > 0""", (day, store_code)):
        product, per = _stock_product(con, row)
        if per is None:
            unknown_size.add(product['id'])
            per = 1
        wanted[product['id']] = wanted.get(product['id'], 0) + row['sold'] * per
        products[product['id']] = product
    kept = {row['product_id']: row for row in con.execute(
        'SELECT * FROM report_stock_takes WHERE store_id=? AND business_date=?', (store_id, day))}
    for product_id in set(kept) - set(products):
        products[product_id] = con.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
    take_lines, return_lines, short, counted, changes = [], [], {}, {}, {}
    for product_id in sorted(set(wanted) | set(kept)):
        product = products[product_id]
        taken = kept[product_id]['taken'] if product_id in kept else 0
        checked = kept[product_id]['checked'] if product_id in kept else 0
        delta = wanted.get(product_id, 0) - taken - checked
        if delta < 0:
            # Fewer sold: sales a manager already checked off go first, since they never moved.
            unchecked = min(checked, -delta)
            give = -delta - unchecked
            if give:
                balance = _balance(con, product_id, floor['id'], 'saleable')
                return_lines.append({'product_id': product_id, 'unit': product['stock_unit'], 'quantity': give,
                                     'to_location_id': floor['id'], 'to_disposition': 'saleable',
                                     'expected_versions': {'to': balance['version']}})
            changes[product_id] = (-give, -unchecked)
            continue
        if delta == 0:
            continue
        if product_id in unknown_size:
            short[product_id] = (product, delta, 'set_size_unknown')
            continue
        if product['identity_status'] != 'verified':
            short[product_id] = (product, delta, 'not_counted')
            continue
        balance = _balance(con, product_id, floor['id'], 'saleable')
        available = (_loose(con, product_id, floor['id']) if product['stock_form'] == 'random_box'
                     else balance['quantity'])
        take = min(delta, available)
        if take:
            take_lines.append({'product_id': product_id, 'unit': product['stock_unit'], 'quantity': take,
                               'from_location_id': floor['id'], 'from_disposition': 'saleable',
                               'expected_versions': {'from': balance['version']}})
            changes[product_id] = (take, 0)
            last = kept[product_id]['updated_at'] if product_id in kept and taken else None
            when = _counted_since(con, floor['id'], day, product_id, last)
            if when:
                counted[product_id] = (product, take, when)
        if take < delta:
            short[product_id] = (product, delta - take, 'not_enough_on_floor')
    documents = []
    for kind, lines, reason in (('consume', take_lines, REPORT_REASON), ('correction', return_lines, RETURN_REASON)):
        if not lines:
            continue
        # A fresh key each time: what moves is always the difference from what this day already took.
        key = uuid.uuid4().hex
        document = _post_inventory_in_transaction(con, {
            'kind': kind, 'business_date': day, 'source_type': 'daily_report',
            'source_id': f'{store_code}:{day}:{key[:12]}', 'reason': reason, 'lines': lines,
        }, actor=actor, request_key=f'inventory:daily-report:{key}', minimum_role='manager')
        documents.append(document['document_id'])
    for product_id, (taken, checked) in changes.items():
        if product_id in kept:
            # updated_at marks the last time this day took stock, for the same-day count check.
            con.execute("""UPDATE report_stock_takes SET taken=taken+?, checked=checked+?,
                               updated_at=CASE WHEN ? THEN datetime('now') ELSE updated_at END
                           WHERE store_id=? AND business_date=? AND product_id=?""",
                        (taken, checked, taken != 0, store_id, day, product_id))
        else:
            con.execute('INSERT INTO report_stock_takes(store_id, business_date, product_id, taken) VALUES (?, ?, ?, ?)',
                        (store_id, day, product_id, taken))
    _keep_notices(con, store_id, day, short, counted)
    return {
        'business_date': day, 'documents': documents,
        'taken': sum(taken for taken, _ in changes.values() if taken > 0),
        'returned': -sum(taken for taken, _ in changes.values() if taken < 0),
        'short': [{'product_id': product['id'], 'sku': product['sku'], 'name': _name(product),
                   'quantity': quantity, 'reason': reason} for product, quantity, reason in short.values()],
        'counted_same_day': [{'product_id': product['id'], 'sku': product['sku'], 'name': _name(product),
                              'quantity': quantity, 'counted_at': when}
                             for product, quantity, when in counted.values()],
    }


def _keep_notices(con, store_id, day, short, counted):
    """Keep the day's notices in step with this save: open what it found, close shortfalls it settled."""
    for product_id, (_, quantity, reason) in short.items():
        con.execute(
            """INSERT INTO report_stock_issues(store_id, business_date, product_id, reason, quantity)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(store_id, business_date, product_id, reason) DO UPDATE
               SET quantity=excluded.quantity, status='open', updated_at=datetime('now'),
                   checked_by=NULL, checked_at=NULL""", (store_id, day, product_id, reason, quantity))
    marks = ','.join('?' * len(SHORT_REASONS))
    for row in con.execute(
            f"""SELECT id, product_id, reason FROM report_stock_issues
                WHERE store_id=? AND business_date=? AND status='open' AND reason IN ({marks})""",
            (store_id, day, *SHORT_REASONS)).fetchall():
        if short.get(row['product_id'], (None, 0, None))[2] != row['reason']:
            con.execute("UPDATE report_stock_issues SET status='resolved', updated_at=datetime('now') WHERE id=?",
                        (row['id'],))
    for product_id, (_, quantity, when) in counted.items():
        con.execute(
            """INSERT INTO report_stock_issues(store_id, business_date, product_id, reason, quantity, detail)
               VALUES (?, ?, ?, 'counted_same_day', ?, ?)
               ON CONFLICT(store_id, business_date, product_id, reason) DO UPDATE
               SET quantity=CASE WHEN status='open' THEN quantity+excluded.quantity ELSE excluded.quantity END,
                   detail=excluded.detail, status='open', updated_at=datetime('now'),
                   checked_by=NULL, checked_at=NULL""", (store_id, day, product_id, quantity, when))


def open_notices(con, store_ids):
    """Open notices at these stores, oldest day first, with each product's floor and upstairs stock now."""
    if not store_ids:
        return []
    marks = ','.join('?' * len(store_ids))
    notices = []
    for row in con.execute(
            f"""SELECT i.*, s.code AS store_code, p.sku, p.jizhanming, p.name_cn_en, p.stock_unit
                FROM report_stock_issues i JOIN stores s ON s.id=i.store_id JOIN products p ON p.id=i.product_id
                WHERE i.status='open' AND i.store_id IN ({marks}) ORDER BY i.business_date, i.store_id, i.id""",
            tuple(store_ids)):
        stock = {code: con.execute(
            """SELECT COALESCE(SUM(b.quantity), 0) FROM inventory_balances b JOIN inventory_locations l ON l.id=b.location_id
               WHERE l.store_id=? AND l.code=? AND b.product_id=? AND b.disposition='saleable'""",
            (row['store_id'], code, row['product_id'])).fetchone()[0] for code in ('floor', 'upstairs')}
        notices.append({'id': row['id'], 'store_id': row['store_id'], 'store_code': row['store_code'],
                        'business_date': row['business_date'], 'product_id': row['product_id'],
                        'sku': row['sku'], 'name': _name(row), 'unit': row['stock_unit'],
                        'reason': row['reason'], 'quantity': row['quantity'], 'counted_at': row['detail'],
                        'floor': stock['floor'], 'upstairs': stock['upstairs'], 'updated_at': row['updated_at']})
    return notices


def check_notice(con, actor, notice_id):
    """Inside the caller's write transaction: a manager checked this notice. A checked shortfall
    is taken as already reflected in the floor count, so it is never taken off later."""
    row = con.execute('SELECT * FROM report_stock_issues WHERE id=?', (notice_id,)).fetchone()
    if row is None:
        raise InventoryError('Notice not found', 'not_found', 404)
    _require_manager(con, actor, row['store_id'])
    if row['status'] != 'open':
        raise InventoryConflict('Someone already checked or settled this notice', 'notice_closed')
    if row['reason'] in SHORT_REASONS:
        con.execute(
            """INSERT INTO report_stock_takes(store_id, business_date, product_id, taken, checked) VALUES (?, ?, ?, 0, ?)
               ON CONFLICT(store_id, business_date, product_id) DO UPDATE SET checked=checked+excluded.checked""",
            (row['store_id'], row['business_date'], row['product_id'], row['quantity']))
    con.execute("""UPDATE report_stock_issues SET status='checked', checked_by=?, checked_at=datetime('now'),
                   updated_at=datetime('now') WHERE id=?""", (actor['sub'], notice_id))
    return {'id': notice_id, 'status': 'checked'}
