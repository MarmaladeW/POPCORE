"""记一笔 Quick sale: a whole sale recorded in one tap, in place of 随手记 (owner, 2026-11-21).

"right now we use something called 随手记, which is where we record all of the cash/emt/wechat/alipay sales, and at the
end of the day we record all of the POS sales on 随手记 too … popcore.store is capable of replacing that part, especially
we have existing product so we dont have to type everything and remember all of the jizhangming verbatim."

A quick sale is an ordinary manual checkout, created and paid in one request by the same code as 买单: the same prices
(products.price, a design without one takes its series' price), 13% HST, the cash price for every tender but card, cash
rounded to 5¢, WeChat Pay and Alipay in yuan at the checkout rate. E-transfer, WeChat Pay and Alipay ask for a photo of
the payment; a sale saved without one says so (`needs_photo`) until a photo is added (owner: "they can save the order
without image, but that order should be flagged and saying there isnt a picture").

It moves no stock when it is saved (owner, same day: "the stock change only at the end of the day at once after the
report has been reviewed"): checkout_orders.stock_by_report marks it and its sale document is allocated with no
inventory document. So it asks no blind-box question and never waits for stock.

审核 Review the day (owner, 2026-11-24: "once the 记一笔 is fully built, we dont need the 日报 pasting anymore"): at
the end of the day a manager reviews the day's quick sales in the app and confirms; that saves them as the day's sales
(daily_sales, by product id, no text and no name matching) and takes them off stock once, through the same code a
pasted 日报 used (`report_body` feeds `sales.submit_daily_report`): a sealed set opens when the floor runs out, a display
piece leaves display, what the floor does not have waits as a notice. `quick_sale_reviews` keeps who reviewed which
day. A day is reviewed here or pasted, never both. Sales recorded or undone after the review show the day as changed;
reviewing again moves only the difference. Until a day is reviewed, the 晚盘 takes its quick sales off what POPCORE
expects on the floor (`unreviewed_units`).

员工折扣 (owner, 2026-11-24: in the app, not the 日报): a sale ticked 员工折扣 names the staff member who bought. It is
an ordinary sale (it comes off stock and counts in the day's sales, which a 日报 员工折扣 line never did); the person's
monthly uses are counted from it (staff_discounts.sync_month). 现金: the manager's review may record the cash counted
that day, kept where a pasted 日报's cash line was (daily_report_metadata.cash_actual_cents).

撤销: the cashier (their own sale, that day) or a manager takes back a sale recorded by mistake. Nothing is deleted: each
payment gets a correction to zero with the reason, the sale is marked undone and leaves 今天's total, the 日报 summary
and the sales report.
"""
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal

import clover_pricing as pricing
import exchange_rates
import checkout_access as access_rules
from checkout_access import checkout_access, require_checkout
from checkout_operations import change_checkout, checkout_row, create_checkout, manager, manual_due, receive_manual
from goods_operations import _begin, _remember, _replay
from inventory_commands import InventoryConflict, InventoryError, InventoryValidationError
from sales_operations import record_late_adjustment
from validation import read_date, read_int
import people
import staff_discounts

TENDERS = ('cash', 'e_transfer', 'wechat', 'alipay', 'card')
PHOTO_TENDERS = ('e_transfer', 'wechat', 'alipay')
CNY_TENDERS = ('wechat', 'alipay')
TENDER_NAMES = {'cash': '现金 Cash', 'e_transfer': 'E-transfer', 'wechat': '微信 WeChat Pay',
                'alipay': '支付宝 Alipay', 'card': '刷卡 Card'}
HST_PERCENT = 13
MAX_LINES = 40
UNDO_REASON = '记错了 Recorded by mistake'
SHIFT_MESSAGE = ('你今天在这家店没有排班，不能记账。请店长在排班里加上你的班。'
                 'You have no shift at this store today, so you cannot record sales. Ask a manager to add your shift.')
ACCESS_MESSAGE = ('你还没有这家店的门店操作权限，请店长在 员工 里打开。'
                  'You do not have store access here yet. Ask a manager to turn it on in Users.')


def hst(subtotal):
    """13% HST, rounded half up, as the checkout screen and Clover round it."""
    return (subtotal * HST_PERCENT + 50) // 100


def _cents(price):
    if price is None:
        return None
    cents = int((Decimal(str(price)) * 100).to_integral_value())
    return cents if cents > 0 else None


def _rate(con):
    """(rate, where it came from) for WeChat Pay and Alipay: the Settings rate, otherwise today's reference rate."""
    row = con.execute("SELECT value FROM app_settings WHERE key='checkout_cny_per_cad'").fetchone()
    if row and row['value']:
        return row['value'], 'Settings'
    live = exchange_rates.cny_per_cad()
    if live:
        return live['rate'], f"{live['source']} {live['date']}"
    return None, None


def _location(con, store_id, code):
    row = con.execute('SELECT id FROM inventory_locations WHERE store_id=? AND code=? AND is_active=1',
                      (store_id, code)).fetchone()
    return row['id'] if row else None


def _product(con, product_id):
    return con.execute(
        """SELECT p.id, p.sku, p.jizhanming, p.name_cn_en, p.price, p.stock_form, p.stock_unit, p.identity_status,
                  p.design_name, ps.name AS series_name, head.price AS series_price
           FROM products p LEFT JOIN product_series ps ON ps.id=p.series_id
           LEFT JOIN products head ON head.id=ps.product_id AND head.id<>p.id
           WHERE p.id=?""", (product_id,)).fetchone()


def name_of(product):
    """The 记账名 a 日报 line uses, else the official name."""
    return (product['jizhanming'] or '').strip() or (product['name_cn_en'] or '').strip() or product['sku']


def _require_store(con, actor, store_id):
    try:
        return require_checkout(con, {'store_id': store_id}, actor, write=True)
    except PermissionError as exc:
        if str(exc) == 'Checkout shift access denied':
            raise InventoryError(SHIFT_MESSAGE, 'quick_sale_no_shift', 403) from exc
        if str(exc) == 'Inventory access denied':
            raise InventoryError(ACCESS_MESSAGE, 'quick_sale_no_access', 403) from exc
        raise


def _buyer(con, data, actor):
    """员工折扣: the staff member who bought, or None. The buyer is eligible by the 员工折扣 page's rule, and below
    manager must still have a use left this month (owner, 2026-11-26); a manager may record one over the limit."""
    buyer = data.get('staff_discount_employee_id')
    if buyer is None:
        return None
    buyer = read_int(buyer, 'staff_discount_employee_id', minimum=1)
    eligible, left = staff_discounts.uses_left(con, buyer, access_rules.business_date()[:7])
    if not eligible:
        raise InventoryValidationError('员工折扣要选一位正式员工或以上的在职员工。'
                                       'Choose an active staff member of 正式员工 rank or above for the staff discount.')
    if not left and not manager(actor):
        raise InventoryConflict('这位员工本月的员工折扣已经用完了，要店长来记。'
                                'This person has no staff discount left this month; ask a manager to record it.',
                                'staff_discount_none_left')
    return buyer


def quote(con, data, actor):
    """What a cart costs, paid each way, before anything is saved. The screen shows only these numbers."""
    if not isinstance(data, dict):
        raise InventoryValidationError('body must be an object')
    store_id = read_int(data.get('store_id'), 'store_id', minimum=1)
    access = _require_store(con, actor, store_id)
    buyer = _buyer(con, data, actor)
    display = _location(con, store_id, 'display')
    raw_lines = data.get('lines')
    if not isinstance(raw_lines, list) or not raw_lines or len(raw_lines) > MAX_LINES:
        raise InventoryValidationError(f'Add 1 to {MAX_LINES} items')
    lines, seen = [], set()
    for raw in raw_lines:
        if not isinstance(raw, dict):
            raise InventoryValidationError('Each item must be an object')
        product_id = read_int(raw.get('product_id'), 'product_id', minimum=1)
        quantity = read_int(raw.get('quantity'), 'quantity', minimum=1)
        if quantity > 999:
            raise InventoryValidationError('Quantity must be at most 999')
        from_display = raw.get('from_display', False)
        if not isinstance(from_display, bool):
            raise InventoryValidationError('from_display must be true or false')
        if from_display and display is None:
            raise InventoryConflict('这家店没有 display。This store has no display location.', 'display_location_missing')
        if (product_id, from_display) in seen:
            raise InventoryValidationError('The same item is in the cart twice: change its quantity instead')
        seen.add((product_id, from_display))
        product = _product(con, product_id)
        if product is None:
            raise InventoryValidationError('product_id is invalid')
        if product['identity_status'] != 'verified' or not product['stock_form']:
            raise InventoryConflict(f'「{name_of(product)}」还没核对好，不能在这里卖，请告诉店长。'
                                    'This product is not reviewed yet; tell a manager.', 'identity_unverified')
        typed = raw.get('unit_price_cents')
        if typed is not None:
            typed = read_int(typed, 'unit_price_cents', minimum=1)
            if typed > 10_000_00:
                raise InventoryValidationError('Price must be at most $10,000')
        own, series = _cents(product['price']), _cents(product['series_price'])
        if typed and (own or series) and not manager(actor):
            # Owner, 2026-11-26: below manager a price is typed only for a product that has none.
            raise InventoryConflict(f'「{name_of(product)}」已经有价格，改价要店长来记。'
                                    'This item has a price; only a manager can change it.', 'price_needs_manager')
        price = typed or own or series
        lines.append({'product_id': product_id, 'sku': product['sku'], 'name': name_of(product),
                      'unit': product['stock_unit'], 'quantity': quantity, 'from_display': from_display,
                      'unit_price_cents': price, 'list_price_cents': own or series,
                      'price_from': 'typed' if typed else 'product' if own else 'series' if series else None,
                      'line_cents': price * quantity if price else None})
    missing = [line['name'] for line in lines if line['unit_price_cents'] is None]
    discount = read_int(data.get('discount_cents') or 0, 'discount_cents', minimum=0)
    if discount and buyer is None and not manager(actor):
        # Owner, 2026-11-26: the server works out no discount of its own, so below manager the only discount is a
        # 员工折扣, which names who bought and counts against their uses.
        raise InventoryConflict('优惠要店长来记；员工折扣请先选谁买的。'
                                'A discount needs a manager; for a staff discount, pick who bought first.',
                                'discount_needs_manager')
    result = {'store_id': store_id, 'business_date': access['business_date'], 'lines': lines,
              'missing_prices': missing, 'discount_cents': discount, 'staff_discount_employee_id': buyer,
              'tenders': None}
    if missing:
        return result
    subtotal = sum(line['line_cents'] for line in lines)
    tax = hst(subtotal)
    if discount >= subtotal + tax:
        raise InventoryValidationError('The discount must be less than the total')
    total = subtotal + tax - discount
    summary = {'total': total, 'simple': True, 'subtotal': subtotal, 'external_discount_cents': discount,
               'ext_discount': discount > 0, 'cash_priced': False, 'popcore_discount': None}
    other, mode = manual_due(summary, card_cents=0, other_cents=1, plan='pay')
    rate, source = _rate(con)
    fen = exchange_rates.cad_to_fen(other, rate) if rate else None
    result.update(subtotal_cents=subtotal, tax_cents=tax, gross_cents=subtotal + tax, total_cents=total,
                  price_label=pricing.discount_label(mode) if other < total else None,
                  tenders={'card': total, 'cash': pricing.cash_limit(other, cash_cents=0, other_cents=0),
                           'e_transfer': other, 'wechat': other, 'alipay': other},
                  other_due_cents=other,
                  cny={'rate': rate, 'source': source, 'fen': fen} if fen is not None else None)
    return result


def save(con, data, actor, request_key):
    """Create and pay one quick sale; replaying the same Idempotency-Key returns the same sale."""
    if not isinstance(request_key, str) or not request_key.strip():
        raise InventoryValidationError('Idempotency-Key is required')
    key = request_key.strip()
    tender = data.get('tender') if isinstance(data, dict) else None
    if tender not in TENDERS:
        raise InventoryValidationError('Choose cash, e-transfer, WeChat Pay, Alipay or card')
    # The same key again is the same sale, whatever changed since (a price, the rate, the date at midnight).
    earlier = con.execute("""SELECT resource_id FROM operation_requests WHERE request_key=? AND actor_sub=?
                             AND operation='checkout_create'""", (f'quick-sale:{key}:create', actor.get('sub'))).fetchone()
    if earlier:
        order = con.execute('SELECT id, status, version FROM checkout_orders WHERE id=?', (earlier['resource_id'],)).fetchone()
        if order and order['status'] == 'completed':
            return sale_row(con, order['id'], actor)
        if order and order['status'] == 'open':
            # Created but never paid (the server stopped in between): cancelled, so the next tap starts clean.
            change_checkout(con, order['id'], 'cancel', {'expected_version': order['version']}, actor,
                            f'quick-sale:{key}:cancel')
        raise InventoryConflict('这笔没有存上，请再按一次保存。This sale was not saved; tap Save again.', 'quick_sale_retry')
    priced = quote(con, data, actor)
    buyer = priced['staff_discount_employee_id']
    if priced['missing_prices']:
        raise InventoryConflict(f"先填价格 Enter a price first: {'、'.join(priced['missing_prices'])}", 'price_missing')
    amount = priced['tenders'][tender]
    if data.get('expected_amount_cents') != amount:
        raise InventoryConflict('金额变了，请看一下新的金额。The amount changed. Check the new amount.', 'price_changed')
    receive = {'tender': tender, 'plan': 'pay', 'amount_cents': amount,
               'expected_due_cents': priced['total_cents'] if tender == 'card' else priced['other_due_cents']}
    if tender in CNY_TENDERS:
        if not priced['cny']:
            raise InventoryValidationError('WeChat Pay and Alipay need a CNY rate. Ask an admin to set one in Settings.')
        if data.get('cny_fen') != priced['cny']['fen']:
            raise InventoryConflict('人民币金额变了，请再看一下。The CNY amount changed. Check it again.', 'price_changed')
        receive.update(cny_per_cad=priced['cny']['rate'], cny_fen=priced['cny']['fen'])
    lines = [{'product_id': line['product_id'], 'unit': line['unit'], 'quantity': line['quantity'],
              'unit_price_cents': line['unit_price_cents'], **({'from_display': True} if line['from_display'] else {})}
             for line in priced['lines']]
    order = create_checkout(con, {
        'store_id': priced['store_id'], 'business_date': priced['business_date'], 'lines': lines,
        'subtotal_cents': priced['subtotal_cents'], 'source_tax_cents': priced['tax_cents'],
        'gross_cents': priced['gross_cents'], 'reduction_cents': priced['discount_cents'], 'rounding_cents': 0,
        'collected_cents': priced['total_cents'],
    }, actor, f'quick-sale:{key}:create', stock_by_report=True, staff_discount_for=buyer)
    if order['status'] == 'open':
        try:
            order = receive_manual(con, order['id'], {**receive, 'expected_version': order['version']}, actor,
                                   f'quick-sale:{key}:receive')
        except Exception:
            # Nothing was received: the unpaid order is cancelled, so a second tap starts clean.
            try:
                current = con.execute('SELECT version, status FROM checkout_orders WHERE id=?', (order['id'],)).fetchone()
                if current and current['status'] == 'open':
                    change_checkout(con, order['id'], 'cancel', {'expected_version': current['version']}, actor,
                                    f'quick-sale:{key}:cancel')
            except Exception:  # noqa: BLE001 - the first error is the one to show
                pass
            raise
    if order['status'] != 'completed':
        raise InventoryConflict('这笔没有存上，请再按一次保存。This sale was not saved; tap Save again.', 'quick_sale_retry')
    # For the 动态 line only (the route takes it out of the answer): prices a manager typed over the list price.
    return {**sale_row(con, order['id'], actor),
            'typed_prices': [line for line in priced['lines'] if line['price_from'] == 'typed' and line['list_price_cents']]}


def _report_saved(con, store_id, business_date):
    """Whether the day's sales already came off stock: it was reviewed here, or its 日报 was pasted."""
    return con.execute("""SELECT 1 FROM stores s WHERE s.id=? AND (
                              EXISTS (SELECT 1 FROM quick_sale_reviews r WHERE r.store_id=s.id AND r.business_date=?)
                              OR EXISTS (SELECT 1 FROM daily_sales d WHERE d.store=s.code AND d.date=?)
                              OR EXISTS (SELECT 1 FROM daily_report_metadata m WHERE m.store=s.code AND m.date=?))""",
                       (store_id, business_date, business_date, business_date)).fetchone() is not None


def undo(con, checkout_id, data, actor, request_key):
    """撤销: take back a quick sale recorded by mistake. Each payment is corrected to zero with the reason; nothing is
    deleted. The cashier who recorded it may, on its day; a manager any day."""
    data = data if isinstance(data, dict) else {}
    note = data.get('reason')
    if note is not None and (not isinstance(note, str) or len(note) > 200):
        raise InventoryValidationError('Reason must be text up to 200 characters')
    reason = ' · '.join(filter(None, (UNDO_REASON, (note or '').strip())))
    _begin(con)
    try:
        row = checkout_row(con, checkout_id, actor)
        key, digest, prior = _replay(con, request_key, 'quick_sale_undo', actor, {'checkout_id': row['id'], 'reason': reason})
        if prior:
            con.commit()
            return sale_row(con, row['id'], actor)
        require_checkout(con, row, actor, write=True)
        if not row['stock_by_report'] or row['status'] != 'completed' or not row['sale_id']:
            raise InventoryConflict('只有 记一笔 记好的销售可以撤销。Only a saved quick sale can be undone here.', 'quick_sale_undo_state')
        if row['undone_at']:
            raise InventoryConflict('这一笔已经撤销了。This sale was already undone.', 'quick_sale_undone')
        today = checkout_access(con, actor)['business_date']
        if not manager(actor) and (row['created_by'] != actor['sub'] or row['business_date'] != today):
            raise PermissionError('Only the cashier, on the day, or a manager can undo this sale')
        for payment in con.execute('SELECT id, amount_cents FROM sale_payments WHERE sale_id=?', (row['sale_id'],)).fetchall():
            change = con.execute("""SELECT COALESCE(SUM(CASE WHEN direction='increase' THEN amount_cents
                                                         WHEN direction='decrease' THEN -amount_cents ELSE 0 END), 0)
                                    FROM payment_events WHERE payment_id=?""", (payment['id'],)).fetchone()[0]
            left = (payment['amount_cents'] or 0) + change
            if left > 0:
                con.execute("""INSERT INTO payment_events(payment_id, event_type, direction, amount_cents, reason, actor_sub)
                               VALUES (?, 'correction', 'decrease', ?, ?, ?)""", (payment['id'], left, reason, actor['sub']))
        con.execute("""UPDATE checkout_orders SET undone_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), undone_by=?, undo_reason=?,
                       version=version+1 WHERE id=?""", (actor['sub'], reason, row['id']))
        con.execute('UPDATE sale_documents SET version=version+1 WHERE id=?', (row['sale_id'],))
        record_late_adjustment(con, row['store_id'], row['business_date'], 'checkout', row['id'],
                               'Quick sale undone after store-day close', actor['sub'])
        _remember(con, key, 'quick_sale_undo', row['id'], actor, digest, {'id': row['id']})
        con.commit()
    except Exception:
        con.rollback()
        raise
    return sale_row(con, row['id'], actor)


LOOK_BACK_DAYS = 14


def _quick_lines(con, store_id, business_date):
    """The day's quick sales that stand (not undone), as (tender is card, product id, quantity, from display)."""
    floor = _location(con, store_id, 'floor')
    out = []
    for row in con.execute("""SELECT o.payload, EXISTS(SELECT 1 FROM checkout_attempts a WHERE a.checkout_id=o.id
                                         AND a.status='completed' AND a.tender='card') AS card
                              FROM checkout_orders o WHERE o.store_id=? AND o.business_date=? AND o.status='completed'
                                AND o.stock_by_report=1 AND o.undone_at IS NULL ORDER BY o.id""", (store_id, business_date)):
        for line in json.loads(row['payload']).get('lines') or []:
            if line.get('product_id'):
                out.append((bool(row['card']), line['product_id'], line['quantity'], line.get('location_id') not in (None, floor)))
    return out


def unreviewed_days(con, store_id, through):
    """Days up to `through` (the last two weeks) with quick sales whose stock has not come off yet, oldest first."""
    start = (date.fromisoformat(through) - timedelta(days=LOOK_BACK_DAYS)).isoformat()
    return [row[0] for row in con.execute(
        """SELECT DISTINCT business_date FROM checkout_orders WHERE store_id=? AND business_date BETWEEN ? AND ?
           AND status='completed' AND stock_by_report=1 AND undone_at IS NULL ORDER BY business_date""",
        (store_id, start, through)) if not _report_saved(con, store_id, row[0])]


def unreviewed_units(con, store_id, business_date, product_ids):
    """{product_id: units} quick sales sold from the floor, up to this day, whose stock has not come off yet (for the
    晚盘): a day reviewed late, days later, is still off the shelf."""
    out = {}
    for day_ in unreviewed_days(con, store_id, business_date) if product_ids else []:
        for _, product_id, quantity, display in _quick_lines(con, store_id, day_):
            if product_id in product_ids and not display:
                out[product_id] = out.get(product_id, 0) + quantity
    return out


# ── 审核 Review the day: the day's quick sales become its sales and leave stock, once ─────────────────────────

def report_items(con, store_id, business_date):
    """The day's quick sales as the items `sales.submit_daily_report` saves, by product id: card sales as 卡机 (pos),
    every other tender as 随手记 (cash), and a display piece also as 卖display, so it leaves display and not the
    floor."""
    totals, display = {}, {}
    for card, product_id, quantity, from_display in _quick_lines(con, store_id, business_date):
        key = (product_id, 'pos' if card else 'cash')
        totals[key] = totals.get(key, 0) + quantity
        if from_display:
            display[product_id] = display.get(product_id, 0) + quantity
    names = {}
    for product_id in {key[0] for key in totals}:
        names[product_id] = name_of(_product(con, product_id))
    items = [{'product_id': product_id, 'section': section, 'raw_name': names[product_id], 'notes': '',
              'source_bucket': 'confirmed', **({'qty_pos': quantity} if section == 'pos' else {'qty_cash': quantity})}
             for (product_id, section), quantity in sorted(totals.items())]
    items += [{'product_id': product_id, 'section': 'display_sold', 'raw_name': names[product_id], 'notes': '',
               'source_bucket': 'confirmed', 'qty': quantity} for product_id, quantity in sorted(display.items())]
    return items


def _digest(items):
    return hashlib.sha256(json.dumps(sorted(
        (item['product_id'], item['section'], item.get('qty_pos') or item.get('qty_cash') or item.get('qty'))
        for item in items)).encode()).hexdigest()[:16]


def _pasted(con, store_id, business_date):
    """Whether this day's sales or stock lines came from a pasted 日报 (saved, and not by a review here)."""
    if con.execute('SELECT 1 FROM quick_sale_reviews WHERE store_id=? AND business_date=?', (store_id, business_date)).fetchone():
        return False
    return con.execute("""SELECT 1 FROM stores s WHERE s.id=? AND (
                              EXISTS (SELECT 1 FROM daily_sales d WHERE d.store=s.code AND d.date=?)
                              OR EXISTS (SELECT 1 FROM report_stock_moves m WHERE m.store_id=s.id AND m.business_date=?))""",
                       (store_id, business_date, business_date)).fetchone() is not None


def report_body(con, data, actor):
    """The body `sales.submit_daily_report` saves for a review of the day's quick sales: built here from the sales
    themselves, never from what the screen sends."""
    store = con.execute('SELECT id, code FROM stores WHERE code=? AND is_active=1',
                        (str(data.get('store_code') or '').strip().upper(),)).fetchone()
    if store is None:
        raise InventoryValidationError('store_code is invalid')
    business_date = read_date(data.get('date'), 'date')
    if business_date > access_rules.business_date():
        raise InventoryValidationError('A day cannot be reviewed before it happens')
    if _pasted(con, store['id'], business_date):
        raise InventoryConflict('这天已经贴过日报，库存已经按日报扣了，不能再从记一笔扣。'
                                'This day already has a pasted daily report; its stock came off by that report.', 'day_pasted')
    items = report_items(con, store['id'], business_date)
    reviewed = con.execute('SELECT 1 FROM quick_sale_reviews WHERE store_id=? AND business_date=?',
                           (store['id'], business_date)).fetchone()
    if not items and not reviewed:
        raise InventoryConflict('这天没有记一笔的销售。No quick sales were recorded this day.', 'nothing_to_review')
    cash = data.get('cash_counted_cents')
    if cash is not None:
        cash = read_int(cash, 'cash_counted_cents', minimum=0)
        if cash > 1_000_000_00:
            raise InventoryValidationError('cash_counted_cents is too large')
    return {'date': business_date, 'store_code': store['code'], 'mode': 'replace', 'classification': 'summary_only',
            'stock_mode': 'live', 'items': items, 'source': 'quick_sale', 'store_id': store['id'],
            'dry_run': data.get('dry_run') is True, 'stock_digest': data.get('stock_digest'),
            # The cash counted, kept where a pasted 日报's cash line was.
            **({'report_metadata': {'cash_actual': f'{cash // 100}.{cash % 100:02d}'}} if cash is not None else {})}


def mark_reviewed(con, store_id, business_date, items, actor, cash_counted_cents=None):
    """Inside the report save's transaction: this day's stock came off by a review of its quick sales."""
    con.execute("""INSERT INTO quick_sale_reviews(store_id, business_date, reviewed_by, digest, units, cash_counted_cents)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(store_id, business_date) DO UPDATE SET reviewed_by=excluded.reviewed_by,
                       reviewed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), digest=excluded.digest, units=excluded.units,
                       cash_counted_cents=excluded.cash_counted_cents""",
                (store_id, business_date, actor['sub'], _digest(items),
                 sum(item.get('qty_pos') or item.get('qty_cash') or 0 for item in items), cash_counted_cents))


def reviewed_in_app(con, store_id, business_date):
    return con.execute('SELECT 1 FROM quick_sale_reviews WHERE store_id=? AND business_date=?',
                       (store_id, business_date)).fetchone() is not None


def review_state(con, store_id, store_code, business_date):
    """Where the day stands: `none` (stock not off yet), `reviewed`, `changed` (sales recorded or undone since the
    review: review again), or `pasted` (a 日报 was pasted for it; `differs` lists where it and 记一笔 disagree)."""
    items = report_items(con, store_id, business_date)
    row = con.execute('SELECT * FROM quick_sale_reviews WHERE store_id=? AND business_date=?', (store_id, business_date)).fetchone()
    if row:
        return {'state': 'reviewed' if row['digest'] == _digest(items) else 'changed', 'by': people.name(con, row['reviewed_by']),
                'at': row['reviewed_at'], 'differs': [], 'cash_counted_cents': row['cash_counted_cents']}
    if not _pasted(con, store_id, business_date):
        return {'state': 'none', 'by': None, 'at': None, 'differs': [], 'cash_counted_cents': None}
    here = {}
    for item in items:
        if item['section'] != 'display_sold':
            here[item['product_id']] = here.get(item['product_id'], 0) + (item.get('qty_pos') or item.get('qty_cash') or 0)
    there = {r['product_id']: r['sold'] for r in con.execute(
        'SELECT product_id, SUM(qty_sold) AS sold FROM daily_sales WHERE date=? AND store=? GROUP BY product_id',
        (business_date, store_code))}
    differs = [{'product_id': product_id, 'name': name_of(_product(con, product_id)), 'quick': here.get(product_id, 0),
                'report': there.get(product_id, 0)}
               for product_id in sorted(set(here) | set(there)) if here.get(product_id, 0) != there.get(product_id, 0)]
    return {'state': 'pasted', 'by': None, 'at': None, 'differs': differs if items else [], 'cash_counted_cents': None}


def people_name(con, employee_id):
    row = con.execute('SELECT name FROM employees WHERE id=?', (employee_id,)).fetchone() if employee_id else None
    return row['name'] if row else None


# ── Today's sales and the 日报 summary ────────────────────────────────────────

def _lines(con, payload, floor):
    out = []
    for line in payload.get('lines') or []:
        product = _product(con, line['product_id']) if line.get('product_id') else None
        out.append({'product_id': line.get('product_id'), 'name': name_of(product) if product
                    else line.get('product_name_snapshot') or line.get('raw_product_text') or '',
                    'quantity': line['quantity'], 'from_display': line.get('location_id') not in (None, floor),
                    'unit_price_cents': line.get('unit_price_cents')})
    return out


def sale_row(con, checkout_id, actor=None, today=None):
    order = con.execute('SELECT * FROM checkout_orders WHERE id=?', (checkout_id,)).fetchone()
    floor = _location(con, order['store_id'], 'floor')
    payload = json.loads(order['payload'])
    payments = []
    for attempt in con.execute("""SELECT id, tender, amount_cents, cny_fen, recorded_by,
            (SELECT COUNT(*) FROM checkout_evidence e WHERE e.attempt_id=checkout_attempts.id) AS photos
            FROM checkout_attempts WHERE checkout_id=? AND status='completed' ORDER BY id""", (order['id'],)):
        payments.append({**dict(attempt), 'needs_photo': not order['undone_at'] and attempt['tender'] in PHOTO_TENDERS
                         and not attempt['photos']})
    mine = bool(actor) and actor.get('sub') == order['created_by']
    today = today or (checkout_access(con, actor)['business_date'] if actor else None)
    return {'id': order['id'], 'reference': order['reference'], 'store_id': order['store_id'],
            'business_date': order['business_date'], 'created_at': order['created_at'], 'status': order['status'],
            'sale_id': order['sale_id'], 'cashier': people.name(con, order['created_by']),
            'lines': _lines(con, payload, floor), 'total_cents': payload.get('collected_cents'),
            'discount_cents': payload.get('reduction_cents') or 0, 'payments': payments,
            'card': any(payment['tender'] == 'card' for payment in payments),
            'needs_photo': any(payment['needs_photo'] for payment in payments),
            'can_add_photo': bool(actor) and (mine or manager(actor)) and not order['undone_at'],
            'quick': bool(order['stock_by_report']),
            # 员工折扣: the staff member who bought it.
            'staff_discount': people_name(con, order['staff_discount_employee_id']),
            'undone': {'at': order['undone_at'], 'by': people.name(con, order['undone_by']), 'reason': order['undo_reason']}
            if order['undone_at'] else None,
            'can_undo': bool(actor) and bool(order['stock_by_report']) and order['status'] == 'completed'
            and not order['undone_at'] and (manager(actor) or (mine and order['business_date'] == today)),
            # The day's stock already came off: a sale undone now needs the day reviewed again.
            'report_saved': _report_saved(con, order['store_id'], order['business_date'])}


def day(con, store_id, business_date, actor):
    """A store's sales recorded in the app on one day, oldest first, and their 日报 summary (undone sales left out)."""
    store_id = read_int(store_id, 'store_id', minimum=1)
    try:
        access = checkout_access(con, actor)
    except PermissionError:
        if not manager(actor):
            raise
        # A manager without a staff profile still reviews the day, as they could paste its 日报.
        access = {'business_date': access_rules.business_date(), 'live_stores': []}
    today = access['business_date']
    business_date = read_date(business_date or today, 'business_date')
    on_shift = store_id in {store['id'] for store in access['live_stores']}
    if not manager(actor) and not (on_shift and business_date == today):
        raise PermissionError('Only today’s sales at a store you are working at')
    store = con.execute('SELECT code FROM stores WHERE id=?', (store_id,)).fetchone()
    # Owner, 2026-11-26: below manager a person sees only the sales they recorded themselves, and the day's count,
    # totals and summary are of those; managers and admins see the whole day, which they review.
    everyone = manager(actor)
    rows = [sale_row(con, row['id'], actor, today) for row in con.execute(
        """SELECT id FROM checkout_orders WHERE store_id=? AND business_date=? AND status='completed'
           AND (?=1 OR created_by=?) ORDER BY id""", (store_id, business_date, int(everyone), actor.get('sub')))]
    kept = [row for row in rows if not row['undone']]
    code = store['code'] if store else str(store_id)
    review = review_state(con, store_id, code, business_date)
    if not everyone:  # the cash a manager counted and where a pasted 日报 differs are the store's figures
        review = {**review, 'cash_counted_cents': None, 'differs': []}
    return {'store_id': store_id, 'business_date': business_date, 'today': today, 'sales': rows,
            'summary_text': summary_text(code, business_date, kept),
            'needs_photo': sum(1 for row in kept if row['needs_photo']),
            'total_cents': sum(row['total_cents'] or 0 for row in kept),
            # Cash taken in the day's sales: what the manager sees beside the cash they count.
            'cash_cents': sum(payment['amount_cents'] or 0 for row in kept for payment in row['payments']
                              if payment['tender'] == 'cash'),
            'count': len(kept), 'review': review,
            'can_review': manager(actor),
            # Earlier days whose stock has not come off yet: a manager reviews them too.
            'unreviewed_days': [day_ for day_ in unreviewed_days(con, store_id, today) if day_ != business_date]}


def _item(line):
    return f"{line['name']}*{line['quantity']}" + ('（display）' if line['from_display'] else '')


def summary_text(store_code, business_date, rows):
    """The day's sales written as the 日报 writes them (DT 2026-11-18): 卡机汇总 one line per item over every card
    sale, 随手记汇总 one line per other sale, items joined by "，", a display piece marked （display）."""
    card, merged = [], {}
    for row in rows:
        if not row['card']:
            continue
        for line in row['lines']:
            key = (line['name'], line['from_display'])
            if key not in merged:
                merged[key] = {**line, 'quantity': 0}
                card.append(merged[key])
            merged[key]['quantity'] += line['quantity']
    other = ['，'.join(_item(line) for line in row['lines']) for row in rows if not row['card']]
    parts = [f"{business_date.replace('-', '.')} {store_code}", '', '卡机汇总：', *map(_item, card), '',
             '随手记汇总：', *other]
    return '\n'.join(parts).rstrip() + '\n'
