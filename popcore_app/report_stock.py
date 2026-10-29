"""A live store's daily report takes its sales off the floor (owner, 2026-10-29).

Clover is not connected yet, so the pasted daily report is how a live store's sales leave
stock. Whenever a day's sales change (a report is pasted, replaced or edited), each product's
sales that day are compared with what that day already took off, and only the difference moves:
more sold comes off the floor's saleable stock, less sold goes back. Stock never goes below
zero: what the floor does not have is listed as not taken off, for a recount in 盘点, and the
next save of that day tries again. Only days from the store's go-live date on count, because the
opening counts were taken before them. A sealed set sold takes its boxes, since the floor keeps
blind boxes as boxes only.
"""
import uuid

from goods_operations import _balance
from inventory_commands import _post_inventory_in_transaction, store_is_live

REPORT_REASON = '日报 Daily report'
RETURN_REASON = '日报更正 Daily report corrected'


def live_since(con, store_id):
    """The business date a store went live, or None while it is not live."""
    row = con.execute(
        """SELECT MIN(d.business_date) FROM inventory_scope_state s
           JOIN inventory_documents d ON d.id=s.opening_document_id
           WHERE s.store_id=? AND s.opening_verified=1""", (store_id,)).fetchone()
    return row[0] if row else None


def _stock_product(con, product):
    """(product that holds the stock, units per one sold): a sealed set sells as its boxes."""
    if product['stock_form'] != 'sealed_set':
        return product, 1
    box = con.execute(
        """SELECT p.*, c.output_per_input AS boxes_per_set FROM product_conversions c
           JOIN products p ON p.id=c.target_product_id
           WHERE c.source_product_id=? ORDER BY c.version DESC LIMIT 1""", (product['id'],)).fetchone()
    return (box, box['boxes_per_set']) if box else (product, 1)


def _loose(con, product_id, location_id):
    """Saleable boxes not kept in an opened set: what a sale can take without asking."""
    kept = con.execute(
        """SELECT COALESCE(SUM(remaining_qty), 0) FROM inventory_open_sets
           WHERE random_product_id=? AND location_id=? AND remaining_qty>0""",
        (product_id, location_id)).fetchone()[0]
    return max(0, _balance(con, product_id, location_id, 'saleable')['quantity'] - kept)


def sync_report_stock(con, actor, store_id, store_code, day):
    """Inside the caller's write transaction: move a live store's floor stock to match its daily
    sales on `day`. Returns what moved and what could not, or None when the day does not count."""
    since = live_since(con, store_id)
    if not store_is_live(con, store_id) or since is None or day < since:
        return None
    floor = con.execute(
        "SELECT id FROM inventory_locations WHERE store_id=? AND code='floor' AND is_active=1", (store_id,),
    ).fetchone()
    if floor is None:
        return None
    wanted, products = {}, {}
    for row in con.execute(
            """SELECT p.*, SUM(s.qty_sold) AS sold
               FROM daily_sales s JOIN products p ON p.id=s.product_id
               WHERE s.date=? AND s.store=? GROUP BY p.id HAVING SUM(s.qty_sold) > 0""", (day, store_code)):
        product, per = _stock_product(con, row)
        wanted[product['id']] = wanted.get(product['id'], 0) + row['sold'] * per
        products[product['id']] = product
    taken = {row['product_id']: row['taken'] for row in con.execute(
        'SELECT product_id, taken FROM report_stock_takes WHERE store_id=? AND business_date=?', (store_id, day))}
    for product_id in set(taken) - set(products):
        products[product_id] = con.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
    take_lines, return_lines, short, moved = [], [], [], {}
    for product_id in sorted(set(wanted) | set(taken)):
        product = products[product_id]
        delta = wanted.get(product_id, 0) - taken.get(product_id, 0)
        if delta < 0:
            balance = _balance(con, product_id, floor['id'], 'saleable')
            return_lines.append({'product_id': product_id, 'unit': product['stock_unit'], 'quantity': -delta,
                                 'to_location_id': floor['id'], 'to_disposition': 'saleable',
                                 'expected_versions': {'to': balance['version']}})
            moved[product_id] = delta
            continue
        if delta == 0:
            continue
        if product['identity_status'] != 'verified':
            short.append((product, delta, 'not_counted'))
            continue
        balance = _balance(con, product_id, floor['id'], 'saleable')
        available = (_loose(con, product_id, floor['id']) if product['stock_form'] == 'random_box'
                     else balance['quantity'])
        take = min(delta, available)
        if take:
            take_lines.append({'product_id': product_id, 'unit': product['stock_unit'], 'quantity': take,
                               'from_location_id': floor['id'], 'from_disposition': 'saleable',
                               'expected_versions': {'from': balance['version']}})
            moved[product_id] = take
        if take < delta:
            short.append((product, delta - take, 'not_enough_on_floor'))
    documents = []
    for kind, lines, reason in (('consume', take_lines, REPORT_REASON), ('correction', return_lines, RETURN_REASON)):
        if not lines:
            continue
        # A fresh key each time: what moves is always the difference from what this day already took.
        key = uuid.uuid4().hex
        document = _post_inventory_in_transaction(con, {
            'kind': kind, 'business_date': day, 'source_type': 'daily_report',
            'source_id': f'{store_code}:{day}:{key[:12]}', 'reason': reason, 'lines': lines,
        }, actor=actor, request_key=f'inventory:daily-report:{key}', minimum_role='staff')
        documents.append(document['document_id'])
    for product_id, quantity in moved.items():
        if quantity < 0:
            # Put back: only a day that took this product off has anything to give back.
            con.execute("""UPDATE report_stock_takes SET taken=taken+?, updated_at=datetime('now')
                           WHERE store_id=? AND business_date=? AND product_id=?""",
                        (quantity, store_id, day, product_id))
            continue
        con.execute(
            """INSERT INTO report_stock_takes(store_id, business_date, product_id, taken) VALUES (?, ?, ?, ?)
               ON CONFLICT(store_id, business_date, product_id) DO UPDATE
               SET taken=taken+excluded.taken, updated_at=datetime('now')""",
            (store_id, day, product_id, quantity))
    return {
        'business_date': day, 'documents': documents,
        'taken': sum(q for q in moved.values() if q > 0),
        'returned': -sum(q for q in moved.values() if q < 0),
        'short': [{'product_id': product['id'], 'sku': product['sku'],
                   'name': product['jizhanming'] or product['name_cn_en'] or product['sku'],
                   'quantity': quantity, 'reason': reason} for product, quantity, reason in short],
    }
