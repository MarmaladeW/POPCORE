"""库存 overview (owner, 2026-11-01): what the floor needs, what is selling and what changed lately.

Read-only, from the live ledger (reviewed locations only) and the pasted 日报 (daily_sales). Money appears
only as the stock's value at retail price, and only for managers and admins (owner, 2026-11-03; the
approved Figma strip's 库存价值 cell). It shares the series inventory's access check, so whoever may not
see a store's stock gets nothing here either.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from inventory_catalog import _locations
from inventory_visibility import sees_every_store
from operational_reports import _inventory_identity_valid, _product_name
from stock_losses import cents

BACK = ('upstairs', 'warehouse')
ROLES = ('floor', 'back', 'display', 'claw')
# Sealed sets stay upstairs and confirmed designs live upstairs or on display, so only loose blind boxes
# and ordinary products are refilled on the floor.
REFILL_FORMS = ('random_box', 'ordinary')
PACE_DAYS = 7       # sales pace and best sellers: the 日报 of the last 7 days
SOLD_OUT_DAYS = 14  # sold out: sold in the last 14 days, none left
LOW_DAYS = 3        # running low: the floor lasts fewer days than this at the current pace
LIST_LIMIT = 100
CHANGE_SOURCES = {'daily_report': 'report', 'sale_document': 'checkout', 'checkout_refund': 'checkout',
                  'design_identification': 'identify', 'direct_count': 'count', 'inventory_count': 'count',
                  'goods_receipt': 'receipt', 'delivery_event': 'delivery', 'restock_session': 'restock',
                  'stock_loss': 'loss', 'stock_loss_undo': 'loss', 'damaged_pile': 'damaged'}


def needs_refill(form, floor, back):
    """The floor has none while the same store holds some upstairs (a blind box counts its whole sets there)."""
    return form in REFILL_FORMS and floor == 0 and back > 0


def sealed_set_of_box(con):
    return {row[1]: row[0] for row in con.execute(
        """SELECT c.source_product_id, c.target_product_id FROM product_conversions c
           JOIN products s ON s.id=c.source_product_id WHERE s.stock_form='sealed_set'""")}


def _role(code):
    return 'back' if code in BACK else code


def inventory_overview(con, actor, store_code, today=None):
    con.execute('BEGIN')
    locations = [location for location in _locations(con, actor, store_code) if location['opening_verified']]
    if not locations:
        return {'live': False}
    today = today or datetime.now(ZoneInfo('America/Toronto')).date()
    by_id = {location['id']: location for location in locations}
    stores = sorted({location['store_code'] for location in locations})
    roles = defaultdict(set)
    for location in locations:
        roles[location['store_code']].add(_role(location['code']))
    products = {row['id']: dict(row) for row in con.execute(
        '''SELECT p.id,p.sku,p.jizhanming,p.name_cn_en,p.price,p.stock_form,p.stock_unit,p.identity_status,p.series_id,
                  p.design_name,p.boxes_per_dan,ps.name AS series_name
           FROM products p LEFT JOIN product_series ps ON ps.id=p.series_id''')}
    valid = {product_id for product_id, product in products.items() if _inventory_identity_valid(product)}
    set_of = sealed_set_of_box(con)
    box_of = {set_id: box_id for box_id, set_id in set_of.items()}

    # Saleable stock per store and product, by role: floor, back (upstairs or warehouse), display, claw.
    stock = defaultdict(Counter)
    figures = {}
    priced = sees_every_store(actor)
    value = {'value_cents': 0, 'unpriced_units': 0, 'unpriced_products': set()}
    for location in locations:
        cell = figures.setdefault(_role(location['code']), {'key': _role(location['code']), 'codes': [], 'units': 0,
                                                             'products': set(), 'by_unit': Counter(),
                                                             'value_cents': 0, 'unpriced_units': 0})
        if location['code'] not in cell['codes']:
            cell['codes'].append(location['code'])
    marks = ','.join('?' for _ in by_id)
    for row in con.execute(
            f'''SELECT product_id,location_id,quantity FROM inventory_balances
                WHERE disposition='saleable' AND quantity>0 AND location_id IN ({marks})''', list(by_id)):
        if row['product_id'] not in valid:
            continue
        location = by_id[row['location_id']]
        role = _role(location['code'])
        stock[location['store_code'], row['product_id']][role] += row['quantity']
        cell = figures[role]
        cell['units'] += row['quantity']
        cell['products'].add(row['product_id'])
        cell['by_unit'][products[row['product_id']]['stock_unit']] += row['quantity']
        if priced:
            price = cents(products[row['product_id']]['price'])
            # Each place's worth as well as the total (owner, 2026-11-03: "how much each section is worth").
            if price is None:
                value['unpriced_units'] += row['quantity']
                value['unpriced_products'].add(row['product_id'])
                cell['unpriced_units'] += row['quantity']
            else:
                value['value_cents'] += price * row['quantity']
                cell['value_cents'] += price * row['quantity']

    # 日报 sales: the pace window is the last PACE_DAYS calendar days, divided by the days that have a report.
    store_marks = ','.join('?' for _ in stores)
    pace_from = (today - timedelta(days=PACE_DAYS - 1)).isoformat()
    sold_out_from = (today - timedelta(days=SOLD_OUT_DAYS - 1)).isoformat()
    week, fortnight, total, last_sold = Counter(), Counter(), Counter(), {}
    report_days = defaultdict(set)
    for row in con.execute(
            f'''SELECT store,product_id,date,qty_sold FROM daily_sales
                WHERE store IN ({store_marks}) AND qty_sold>0 AND date<=?''', (*stores, today.isoformat())):
        key = row['store'], row['product_id']
        total[key] += row['qty_sold']
        last_sold[key] = max(last_sold.get(key, ''), row['date'])
        if row['date'] >= sold_out_from:
            fortnight[key] += row['qty_sold']
        if row['date'] >= pace_from:
            week[key] += row['qty_sold']
            report_days[row['store']].add(row['date'])

    def item(key):
        store, product_id = key
        product = products[product_id]
        held = stock[key]
        pair = set_of.get(product_id)
        if product['series_id'] is not None:
            link = {'series_id': product['series_id']}
        else:
            link = {'product_id': box_of.get(product_id, product_id)}
        pace = week[key] / len(report_days[store]) if report_days[store] else 0
        return {'product_id': product_id, 'store_code': store, 'name': _product_name(product), 'sku': product['sku'],
                'unit': product['stock_unit'], 'link': link, 'counted': product_id in valid,
                'floor': held['floor'], 'back': held['back'],
                'back_sets': stock[store, pair]['back'] if pair else 0, 'set_size': product['boxes_per_dan'],
                'sold_week': week[key], 'sold_recent': fortnight[key], 'last_sold': last_sold.get(key),
                'pace': round(pace, 2), 'days_left': round(held['floor'] / pace, 1) if pace else None}

    refill, low, sold_out = [], [], []
    for store in stores:
        floor_and_back = {'floor', 'back'} <= roles[store]
        for product_id in valid:
            product = products[product_id]
            key = store, product_id
            held = stock[key]
            pair = set_of.get(product_id)
            back = held['back'] + (stock[store, pair]['back'] if pair else 0)
            if floor_and_back and needs_refill(product['stock_form'], held['floor'], back):
                refill.append(item(key))
            if week[key] and held['floor'] and held['floor'] * len(report_days[store]) < week[key] * LOW_DAYS:
                low.append(item(key))
            if fortnight[key] and not sum(held.values()) and not (pair and sum(stock[store, pair].values())):
                sold_out.append(item(key))
    refill.sort(key=lambda row: (-row['sold_recent'], -total[row['store_code'], row['product_id']], row['name']))
    low.sort(key=lambda row: (row['days_left'], -row['pace'], row['name']))
    sold_out.sort(key=lambda row: (-row['sold_recent'], row['name']))
    best = [item(key) for key, _ in sorted(week.items(), key=lambda pair: (-pair[1], products[pair[0][1]]['sku'] or ''))
            if key[1] in products][:8]

    # Recent changes: posted ledger documents of the last PACE_DAYS business days, per day and kind.
    changes = {}
    for row in con.execute(
            f'''SELECT d.id,d.kind,d.source_type,d.business_date,m.location_id,
                       SUM(CASE WHEN m.quantity>0 THEN m.quantity ELSE 0 END) AS added,
                       SUM(CASE WHEN m.quantity<0 THEN -m.quantity ELSE 0 END) AS removed,
                SUM(CASE WHEN m.disposition='saleable' THEN m.quantity ELSE 0 END) AS saleable
                FROM inventory_documents d JOIN inventory_movements m ON m.document_id=d.id
                WHERE d.status='posted' AND d.business_date>=? AND m.location_id IN ({marks})
                GROUP BY d.id,m.location_id''', (pace_from, *by_id)):
        kind = CHANGE_SOURCES.get(row['source_type']) or ('restock' if row['kind'] == 'restock_complete' else row['kind'])
        store = by_id[row['location_id']]['store_code']
        change = changes.setdefault((row['business_date'], kind, store), {
            'business_date': row['business_date'], 'kind': kind, 'store_code': store,
            'added': 0, 'removed': 0, 'saleable': 0, 'documents': set(), 'latest': 0})
        change['added'] += row['added']
        change['removed'] += row['removed']
        change['saleable'] += row['saleable']
        change['documents'].add(row['id'])
        change['latest'] = max(change['latest'], row['id'])
    recent = sorted(changes.values(), key=lambda row: (row['business_date'], row['latest']), reverse=True)[:8]

    return {
        'live': True, 'stores': stores, 'today': today.isoformat(),
        'figures': [{**cell, 'products': len(cell['products']), 'by_unit': dict(cell['by_unit']),
                     'value_cents': cell['value_cents'] if priced else None,
                     'unpriced_units': cell['unpriced_units'] if priced else None}
                    for role in (*ROLES, *sorted(set(figures) - set(ROLES))) if (cell := figures.get(role))],
        'in_stock_products': len({product_id for (_, product_id), held in stock.items() if sum(held.values())}),
        'report_days': len(set().union(*report_days.values())) if report_days else 0,
        'last_report': max(last_sold.values(), default=None),
        'refill': refill[:LIST_LIMIT], 'refill_count': len(refill),
        'running_low': low[:LIST_LIMIT], 'sold_out': sold_out[:LIST_LIMIT], 'best_sellers': best,
        'recent_changes': [{key: value for key, value in row.items() if key != 'latest'} | {'documents': len(row['documents'])}
                           for row in recent],
        'not_set_up': len(products) - len(valid),
        # Retail value of the saleable stock above (and of each place in figures), for managers and admins only.
        'value': {**value, 'unpriced_products': len(value['unpriced_products'])} if priced else None,
    }
