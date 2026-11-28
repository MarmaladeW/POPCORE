"""网站订单记录 Web order records (owner, 2026-11-28, popcore-work#157: "网站订单 also need to be recorded, their order
details"). Every paid web order the sync reads stays in `website_orders` (website_sync.py) and is never deleted; this
reads them back with their details: number, when it was paid, the items as the customer bought them (the website's
name, which carries the design) with quantities, the amount, pickup or delivery, the store it is handed over at or
leaves from, the stores its stock came from, and where it stands (packed, done, cancelled).

Admin only (owner, 2026-11-28; docs/access-rules.md: sales and the website section are the admin's). They show on
销售 → 每日汇总 (that day's orders of the store, as their own line beside the store's own sales: a web order never
adds to the store's total) and on 销售 → 网站订单 (any order by month, number or item).

Nothing personal: POPCORE keeps no name, email, phone or address of a customer, and the pickup code never leaves the
board's code check, so none of them is here.
"""
import json
import os
from datetime import date, datetime, time, timedelta, timezone

from website_board import delivery_stores, kind
from website_sync import TORONTO, taken_stores

# Where an order stands, from its website status and POPCORE's state.
STATUSES = ('open', 'packed', 'ready', 'out', 'done', 'cancelled', 'refunded')
# Cancelled and refunded orders stay on record but count in no total.
NOT_COUNTED = ('cancelled', 'refunded')
SEARCH_LIMIT = 200
GMT = '%Y-%m-%dT%H:%M:%S'  # how the website's GMT times are kept (paid_at, modified_at)


def delivery_store():
    """The store deliveries leave from when nothing else says (as on the board)."""
    return os.environ.get('WEBSITE_DELIVERY_STORE', 'DT').strip().upper() or 'DT'


def toronto_day(gmt):
    """The Toronto date of a website GMT time, as YYYY-MM-DD."""
    return datetime.fromisoformat(gmt).replace(tzinfo=timezone.utc).astimezone(TORONTO).date().isoformat()


def gmt_bounds(start, end):
    """[from, to) in website GMT time for the Toronto days start..end, both included."""
    low = datetime.combine(start, time(), TORONTO).astimezone(timezone.utc)
    high = datetime.combine(end + timedelta(days=1), time(), TORONTO).astimezone(timezone.utc)
    return low.strftime(GMT), high.strftime(GMT)


def status_of(row):
    """open, packed (a delivery), ready (a pickup waiting at the counter), out (with the courier), done, cancelled or
    refunded."""
    status = row['wc_status']
    if status == 'refunded' or row['refunded']:
        return 'refunded'
    if status == 'cancelled' or row['state'] in ('cancelled', 'cancel_failed'):
        return 'cancelled'
    if status == 'completed':
        return 'done'
    if status == 'out-for-delivery':
        return 'out'
    if status == 'ready-pickup':
        return 'ready'
    if kind(row) == 'delivery' and row['packed_at']:
        return 'packed'
    return 'open'


def stores_of(con, row, cfg):
    """The stores an order belongs to: a pickup's pickup store; the store a delivery leaves from (both, while its
    stock is still in two)."""
    if kind(row) == 'pickup':
        return [row['pickup_store'] or cfg['delivery_store']]
    return delivery_stores(con, row, cfg)[0]


def _names(con, rows):
    """{product id: the name staff know it by}: the 记账名, else the official name."""
    ids = sorted({line['product_id'] for row in rows for line in json.loads(row['lines'] or '[]') if line.get('product_id')})
    names = {}
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        for product in con.execute(f"SELECT id, jizhanming, name_cn_en FROM products WHERE id IN ({','.join('?' * len(part))})",
                                   part):
            names[product['id']] = (product['jizhanming'] or '').strip() or product['name_cn_en']
    return names


def record(con, row, cfg, names):
    """One order as the records show it."""
    status = status_of(row)
    lines = [{'name': line.get('name') or line.get('popcore_id') or '', 'quantity': line['quantity'],
              'total_cents': line.get('total_cents'), 'popcore_id': line.get('popcore_id') or None,
              'popcore_name': names.get(line.get('product_id'))}
             for line in json.loads(row['lines'] or '[]')]
    item = {'id': row['wc_order_id'], 'number': row['number'], 'paid_at': row['paid_at'],
            'day': toronto_day(row['paid_at']) if row['paid_at'] else None, 'kind': kind(row),
            'stores': stores_of(con, row, cfg), 'taken_from': taken_stores(con, row), 'status': status,
            # Waiting for an admin on the 网站 page: POPCORE could not take it out of stock, or put it back.
            'waiting': row['state'] in ('needs_review', 'cancel_failed'),
            'settled': row['dismiss_reason'] if row['state'] == 'dismissed' else None,
            'total_cents': row['total_cents'], 'tax_cents': row['tax_cents'], 'payment': row['payment'] or None,
            'counted': status not in NOT_COUNTED, 'pieces': sum(line['quantity'] for line in lines), 'lines': lines,
            # When the website last changed it: for a done or cancelled order, when that happened.
            'closed_at': row['modified_at'] if status in ('done', *NOT_COUNTED) else None}
    if item['kind'] == 'delivery':
        item.update(packed_at=row['packed_at'], packed_by=row['packed_by'])
    return item


def totals(orders):
    counted = [order for order in orders if order['counted']]
    return {'orders': len(counted), 'cents': sum(order['total_cents'] or 0 for order in counted),
            'pieces': sum(order['pieces'] for order in counted), 'not_counted': len(orders) - len(counted),
            'pickup': sum(order['kind'] == 'pickup' for order in counted),
            'delivery': sum(order['kind'] == 'delivery' for order in counted)}


def _matches(order, words):
    """An order number (with or without #), or words found in its items' names or POPCORE IDs."""
    number = words.lstrip('#').strip()
    if number and number == order['number']:
        return True
    text = ' '.join(filter(None, (value for line in order['lines']
                                  for value in (line['name'], line['popcore_name'], line['popcore_id'])))).casefold()
    return all(word in text for word in words.casefold().split())


def records(con, *, start=None, end=None, store=None, query=None, cfg=None):
    """The orders paid in the Toronto days start..end (both dates, or neither for every day), newest first, of one
    store (its code) or all, matching `query` (an order number, or words of an item). Without dates the search stops
    at SEARCH_LIMIT orders."""
    cfg = cfg or {'delivery_store': delivery_store()}
    sql, params = 'SELECT * FROM website_orders', []
    if start and end:
        sql += ' WHERE paid_at >= ? AND paid_at < ?'
        params += gmt_bounds(start, end)
    rows = con.execute(sql + ' ORDER BY paid_at DESC, wc_order_id DESC', params).fetchall()
    names = _names(con, rows)
    query = (query or '').strip()
    found = []
    for row in rows:
        order = record(con, row, cfg, names)
        if store and store.upper() not in order['stores']:
            continue
        if query and not _matches(order, query):
            continue
        found.append(order)
        if query and not start and len(found) >= SEARCH_LIMIT:
            break
    return {'orders': found, 'totals': totals(found)}


def day_text(day_records):
    """The web orders' lines of the copied 汇总 text: their own line, never in the store's total."""
    orders, sums = day_records['orders'], day_records['totals']
    lines = [f"网站订单（不计入门店）: {sums['orders']} 单，${sums['cents'] / 100:.2f}"]
    words = {'open': '待处理', 'packed': '已打包', 'ready': '待取货', 'out': '派送中', 'done': '已完成',
             'cancelled': '已取消', 'refunded': '已退款'}
    for order in sorted(orders, key=lambda item: item['paid_at'] or ''):
        when = datetime.fromisoformat(order['paid_at']).replace(tzinfo=timezone.utc).astimezone(TORONTO).strftime('%H:%M')
        amount = f"${(order['total_cents'] or 0) / 100:.2f}"
        lines.append(f"  {when} #{order['number']} {'自取' if order['kind'] == 'pickup' else '配送'} · "
                     f"{amount if order['counted'] else f'（{amount} 不计）'} · {words[order['status']]}")
        lines.extend(f"    {line['name']} × {line['quantity']}" for line in order['lines'])
    return lines


def month_range(month):
    """The first and last day of a YYYY-MM month."""
    first = date.fromisoformat(f'{month}-01')
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return first, last
