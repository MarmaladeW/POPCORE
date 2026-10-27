"""Pending checkouts. Money receipt is manual; provider verification is separate."""
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import clover_pricing as pricing
import exchange_rates
from auth import ROLE_CLAIM, ROLE_HIERARCHY
from checkout_access import REGISTER_SUB, require_checkout
from goods_operations import _begin, _remember, _replay
from inventory_commands import InventoryConflict, InventoryValidationError
from sales_operations import (
    TENDERS, _create_sale_in_transaction, _optional_cents, _post_sale_in_transaction,
    _sale_input, _sale_row, box_choice_fields, box_options, needs_box_choice, record_late_adjustment, set_size,
)
from validation import read_date, read_int


def manager(actor):
    return ROLE_HIERARCHY.get(actor.get(ROLE_CLAIM), 0) >= ROLE_HIERARCHY['manager']


CNY_TENDERS = ('wechat', 'alipay')


def cny_charge(tender, amount_cents, data):
    """(fen, rate) a WeChat Pay or Alipay customer was charged, or (None, None) for other tenders.

    The rate is the one the screen showed at that moment. The yuan are worked out again here,
    so the payment keeps exactly the amount the cashier asked for."""
    if tender not in CNY_TENDERS:
        return None, None
    rate = data.get('cny_per_cad')
    fen = exchange_rates.cad_to_fen(amount_cents, rate)
    if fen is None:
        raise InventoryValidationError('WeChat Pay and Alipay need a CNY rate. Ask an admin to set one in Settings.')
    if type(data.get('cny_fen')) is not int or data['cny_fen'] != fen:
        raise InventoryValidationError('The CNY amount does not match its rate. Check the amount and try again.')
    return fen, rate


def read_order_note(data):
    note, expected = data.get('note'), data.get('expected_note')
    if not isinstance(note, str) or not isinstance(expected, str) or len(note) > 2000 or len(expected) > 2000:
        raise InventoryValidationError('Order note must be text up to 2000 characters')
    return note.strip(), expected


def checkout_row(con, checkout_id, actor, *, cashier=False):
    checkout_id = read_int(checkout_id, 'checkout_id', minimum=1)
    row = con.execute('SELECT * FROM checkout_orders WHERE id=?', (checkout_id,)).fetchone()
    if row is None:
        raise InventoryValidationError('Checkout does not exist')
    row = dict(row)
    require_checkout(con, row, actor, write=cashier)
    if cashier and row['created_by'] != actor['sub'] and not manager(actor):
        raise PermissionError('Checkout cashier access denied')
    return row


def checkout_detail(con, checkout_id, actor):
    row = checkout_row(con, checkout_id, actor)
    row['order'] = json.loads(row.pop('payload'))
    try:
        require_checkout(con, row, actor, write=True)
        writable = True
    except PermissionError:
        writable = False
    employee = con.execute('SELECT name FROM employees WHERE auth0_id=?', (row['created_by'],)).fetchone()
    row['cashier_sub'] = row['created_by']
    row['cashier_name'] = (employee['name'].strip() if employee else '') or row['created_by']
    row['can_manage'] = writable and (row['created_by'] == actor['sub'] or manager(actor))
    row['can_note'] = row['created_by'] == actor['sub'] or manager(actor)
    row['can_process'] = row['can_manage'] and row['status'] == 'open' and not row['abandoned_reason']
    row['can_refund'] = writable and manager(actor) and row['status'] == 'open' and bool(row['abandoned_reason'])
    row['refunds'] = [dict(refund) for refund in con.execute('''SELECT r.*,a.tender FROM checkout_refunds r
        JOIN checkout_attempts a ON a.id=r.attempt_id WHERE a.checkout_id=? ORDER BY r.id''',(checkout_id,))]
    row['attempts'] = []
    for attempt in con.execute('SELECT * FROM checkout_attempts WHERE checkout_id=? ORDER BY id', (checkout_id,)):
        attempt = dict(attempt)
        attempt['refunded_cents'] = sum(r['amount_cents'] for r in row['refunds'] if r['attempt_id']==attempt['id'])
        attempt['refundable_cents'] = attempt['amount_cents'] - attempt['refunded_cents'] if attempt['status']=='completed' else 0
        attempt['can_upload'] = writable and (row['can_manage'] or attempt['assigned_to'] == actor['sub']) and attempt['status'] != 'cancelled' and row['status'] != 'cancelled'
        attempt['photos'] = [dict(photo) for photo in con.execute(
            'SELECT id,uploader_sub,created_at,evidence_id FROM checkout_evidence WHERE attempt_id=? ORDER BY id', (attempt['id'],))]
        row['attempts'].append(attempt)
    row['received_cents'] = sum(a['amount_cents'] for a in row['attempts'] if a['status'] == 'completed')
    row['refunded_cents'] = sum(r['amount_cents'] for r in row['refunds'])
    row['refund_due_cents'] = row['received_cents'] - row['refunded_cents'] if row['abandoned_reason'] else 0
    row['remaining_cents'] = row['order']['collected_cents'] - row['received_cents']
    row['clover'] = None
    row['pricing'] = None if is_clover(con, checkout_id) else manual_block(row, actor)
    row['clover_send'] = None
    if not is_clover(con, checkout_id):
        from clover_checkout import send_block  # avoids a circular import
        row['clover_send'] = send_block(con, row, writable=writable)
        if row['clover_send'] and row['clover_send']['pending']:
            row['pricing']['can_receive'] = False  # paid on Clover once it has the order, or after Stop
    if is_clover(con, checkout_id):
        from clover_checkout import clover_block  # avoids a circular import
        row['clover'] = clover_block(con, row, actor, writable=writable)
        if row['clover']['register']:
            row['cashier_name'] = row['clover']['register_name']
        row['remaining_cents'] = row['clover']['remaining_cents']
    is_open = row['status'] == 'open' and not row['abandoned_reason']
    row['box_choices'] = box_choices(con, row) if is_open else []
    row['box_choice_required'] = any(choice['choice'] is None for choice in row['box_choices'])
    row['can_choose_boxes'] = writable and is_open and (
        row['created_by'] in (actor['sub'], REGISTER_SUB if row['clover'] else None) or manager(actor))
    if row['box_choice_required']:  # no money is recorded until the cashier says where the boxes came from
        for block in (row['pricing'], row['clover']):
            if block:
                block['can_receive'] = False
    return row


# ── Blind boxes over half a set ──────────────────────────────────────────────
# Owner, 2026-10-27: more than half a set of blind boxes came from a new sealed set (开新端), a set
# already opened on the floor (已开的端) or loose boxes (散盒). The cashier says which before payment;
# the sale then opens that set, takes from that opened set, or takes loose boxes only.

BOX_CHOICES = ('fresh_set', 'open_set', 'loose')


def _floor_id(con, store_id):
    row = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='floor' AND is_active=1",
                      (store_id,)).fetchone()
    if row is None:
        raise InventoryConflict('Store floor location is missing', 'inventory_store_missing')
    return row['id']


def _line_choice(line):
    return ('open_set' if line.get('open_set_id') else 'fresh_set' if line.get('fresh_set_product_id')
            else 'loose' if line.get('loose_boxes') else None)


def box_choices(con, row):
    """The order's blind-box lines over half a set, each with the cashier's answer and the options on hand."""
    floor = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='floor' AND is_active=1",
                        (row['store_id'],)).fetchone()
    floor = floor['id'] if floor else None
    result = []
    for line in row['order']['lines']:
        if line.get('stock_form_snapshot') != 'random_box' or line.get('product_id') is None:
            continue
        size = set_size(con, line['product_id'])
        if size is None or line['quantity'] <= size // 2:
            continue
        result.append({'product_id': line['product_id'], 'from_display': line['location_id'] != floor,
                       'name': line['product_name_snapshot'], 'quantity': line['quantity'], 'set_size': size,
                       'choice': _line_choice(line), 'open_set_id': line.get('open_set_id'),
                       'options': box_options(con, line['product_id'], line['location_id'])})
    return result


def missing_box_choice(con, intent):
    return any(needs_box_choice(con, line) for line in intent['lines'])


def require_box_choice(con, intent):
    if missing_box_choice(con, intent):
        raise InventoryConflict('Say where the blind boxes came from before taking payment', 'box_choice_required')


def choose_boxes(con, checkout_id, data, actor, request_key):
    """Record where an order's blind boxes came from, for a manual or a Clover order."""
    product_id = read_int(data.get('product_id'), 'product_id', minimum=1)
    expected = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    choice, from_display = data.get('choice'), data.get('from_display', False)
    if choice not in BOX_CHOICES or not isinstance(from_display, bool):
        raise InventoryValidationError('Choose a new sealed set, an opened set or loose boxes')
    if from_display and choice != 'loose':
        raise InventoryValidationError('A display piece is sold on its own, not from a set')
    row = checkout_row(con, checkout_id, actor)
    require_checkout(con, row, actor, write=True)
    clover = is_clover(con, row['id'])
    if row['created_by'] != actor['sub'] and not manager(actor) and not (clover and row['created_by'] == REGISTER_SUB):
        raise PermissionError('Checkout cashier access denied')
    _begin(con)
    try:
        key, digest, prior = _replay(con, request_key, 'checkout_boxes', actor, {**data, 'checkout_id': row['id']})
        if prior:
            con.commit()
            return checkout_detail(con, row['id'], actor)
        current = con.execute('SELECT * FROM checkout_orders WHERE id=?', (row['id'],)).fetchone()
        if current['status'] != 'open' or current['abandoned_reason'] or current['version'] != expected:
            raise InventoryConflict('Checkout changed. Refresh before continuing.', 'checkout_state_conflict')
        if clover and current['created_by'] == REGISTER_SUB:
            from clover_checkout import _claim  # avoids a circular import
            _claim(con, row['id'], actor, inside=True)  # like taking payment, answering picks the order up
        elif current['created_by'] != actor['sub'] and not manager(actor):
            raise PermissionError('Checkout cashier access denied')
        intent = json.loads(current['payload'])
        floor = _floor_id(con, current['store_id'])
        line = next((line for line in intent['lines'] if line.get('product_id') == product_id
                     and line.get('stock_form_snapshot') == 'random_box'
                     and (line['location_id'] != floor) == from_display), None)
        options = box_options(con, product_id, floor)
        if line is None or options is None:
            raise InventoryValidationError('This order has no blind box of a sealed set like that')
        if choice == 'fresh_set' and options['fresh_set'] is None:
            raise InventoryValidationError('This blind box has no sealed set to open')
        raw = ({'loose_boxes': True} if choice == 'loose' else {'open_set_id': data.get('open_set_id')}
               if choice == 'open_set' else {'fresh_set': {name: options['fresh_set'][name] for name in
                                                            ('product_id', 'conversion_id', 'conversion_factor')}})
        if choice == 'open_set':
            read_int(raw['open_set_id'], 'open_set_id', minimum=1)
        product = con.execute('SELECT id, stock_form FROM products WHERE id=?', (product_id,)).fetchone()
        for field in ('open_set_id', 'fresh_set_product_id', 'fresh_set_conversion_id', 'fresh_set_conversion_factor',
                      'fresh_set_balance_version', 'loose_boxes'):
            line.pop(field, None)
        line.update(box_choice_fields(con, product, raw, floor))
        con.execute('UPDATE checkout_orders SET payload=?,version=version+1 WHERE id=?', (json.dumps(intent), row['id']))
        record_late_adjustment(con, current['store_id'], current['business_date'], 'checkout', row['id'],
                               'Checkout changed after store-day close', actor['sub'])
        _remember(con, key, 'checkout_boxes', row['id'], actor, digest, {'id': row['id']})
        con.commit()
    except Exception:
        con.rollback()
        raise
    return checkout_detail(con, row['id'], actor)


def checkout_funds(con, store_id, start_date, end_date=None):
    """Abandoned checkout cash flow; completed sales use sale_payments instead."""
    end_date = end_date or start_date
    receipts = [dict(row) for row in con.execute("""SELECT a.id,a.checkout_id,a.tender,
        a.amount_cents,o.business_date,EXISTS(SELECT 1 FROM checkout_refunds r
        WHERE r.attempt_id=a.id AND r.receipt_confirmed=1) verified
        FROM checkout_attempts a JOIN checkout_orders o ON o.id=a.checkout_id
        WHERE o.store_id=? AND o.abandoned_reason IS NOT NULL AND o.sale_id IS NULL
        AND a.status='completed' AND o.business_date BETWEEN ? AND ? ORDER BY a.id""",
        (store_id,start_date,end_date))]
    refunds = [dict(row) for row in con.execute("""SELECT r.*,a.checkout_id,a.tender
        FROM checkout_refunds r JOIN checkout_attempts a ON a.id=r.attempt_id
        JOIN checkout_orders o ON o.id=a.checkout_id
        WHERE o.store_id=? AND o.abandoned_reason IS NOT NULL AND o.sale_id IS NULL
        AND r.business_date BETWEEN ? AND ? ORDER BY r.id""",(store_id,start_date,end_date))]
    return {'receipts':receipts,'refunds':refunds}


def manual_reference(con, store_id, business_date):
    """The next free reference for a manual order without one, numbered per store and day: MK-20261020-001."""
    store = con.execute('SELECT code FROM stores WHERE id=?', (store_id,)).fetchone()
    prefix = f"{store['code'] if store else store_id}-{business_date.replace('-', '')}-"
    taken = {row['reference'] for row in con.execute(
        'SELECT reference FROM checkout_orders WHERE store_id=? AND substr(reference,1,?)=?',
        (store_id, len(prefix), prefix))}
    number = len(taken) + 1
    while f'{prefix}{number:03d}' in taken:
        number += 1
    return f'{prefix}{number:03d}'


def create_checkout(con, data, actor, request_key):
    store_id = read_int(data.get('store_id'), 'store_id', minimum=1)
    require_checkout(con, {'store_id':store_id}, actor, write=True)
    _begin(con)
    try:
        require_checkout(con, {'store_id':store_id}, actor, write=True)
        key, digest, prior = _replay(con, request_key, 'checkout_create', actor, data)
        if prior:
            con.commit()
            return checkout_detail(con, prior['id'], actor)
        reference = data.get('reference')
        if reference is None or isinstance(reference, str) and not reference.strip():
            # A phone order or an item another store is sending has no Clover order to refer to.
            reference = manual_reference(con, store_id, read_date(data.get('business_date'), 'business_date'))
        if not isinstance(reference, str):
            raise InventoryValidationError('Reference must be text')
        reference = reference.strip()
        if len(reference) > 120:
            raise InventoryValidationError('Reference must contain at most 120 characters')
        intent = _sale_input(con, {**data, 'entry_mode': 'planned_entry',
            'source': {'system':'popcore_checkout','account':str(store_id),'reference':reference}}, actor)
        if any(intent[field] is None for field in ('subtotal_cents','source_tax_cents','gross_cents','reduction_cents','rounding_cents','collected_cents')) or intent['collected_cents'] <= 0:
            raise InventoryValidationError('Enter complete money totals and a positive amount due')
        if any(line['unit_price_cents'] is None for line in intent['lines']) or sum(line['unit_price_cents'] * line['quantity'] for line in intent['lines']) != intent['subtotal_cents']:
            raise InventoryValidationError('Subtotal must match the item prices and quantities')
        try:
            checkout_id = con.execute('''INSERT INTO checkout_orders(store_id,business_date,reference,payload,created_by)
                VALUES (?,?,?,?,?)''', (store_id,intent['business_date'],reference,json.dumps(intent),actor['sub'])).lastrowid
        except sqlite3.IntegrityError as exc:
            raise InventoryConflict('Checkout reference already exists', 'source_conflict') from exc
        _remember(con,key,'checkout_create',checkout_id,actor,digest,{'id':checkout_id})
        record_late_adjustment(con,store_id,intent['business_date'],'checkout',checkout_id,'Checkout created after store-day close',actor['sub'])
        con.commit()
        return checkout_detail(con,checkout_id,actor)
    except Exception:
        con.rollback()
        raise


def attach_photo(con, photo, payment_id):
    evidence_id = con.execute('''INSERT INTO payment_evidence(payment_id,object_id,mime_type,byte_size,uploader_sub)
        VALUES (?,?,?,?,?)''', (payment_id,photo['object_id'],photo['mime_type'],photo['byte_size'],photo['uploader_sub'])).lastrowid
    con.execute('UPDATE checkout_evidence SET evidence_id=? WHERE id=?', (evidence_id,photo['id']))


def finalize_sale(con, row, intent, *, actor, key):
    """Inside the caller's transaction: create, post and pay the sale of a fully paid checkout.

    The original cashier owns the sale even when a manager or the Clover register finalizes it.
    Card payments Clover confirmed are recorded as verified; everything else stays recorded."""
    intent = {**intent, 'entry_mode': 'already_paid'}
    owner = {**actor, 'sub': row['created_by']}
    sale_id = _create_sale_in_transaction(con, intent, owner)
    _post_sale_in_transaction(con, _sale_row(con, sale_id), 1, actor, key)
    attempts = con.execute("SELECT * FROM checkout_attempts WHERE checkout_id=? AND status='completed' ORDER BY id",
                           (row['id'],)).fetchall()
    for attempt in attempts:
        confirmed = attempt['origin'] == 'clover' and attempt['tender'] == 'card'
        payment_id = con.execute('''INSERT INTO sale_payments(sale_id,tender,amount_cents,source_system,source_account,source_reference,recorded_by,state)
            VALUES (?,?,?,'popcore_checkout',?,?,?,?)''',(sale_id,attempt['tender'],attempt['amount_cents'],str(row['store_id']),
            str(attempt['id']),attempt['recorded_by'],'verified' if confirmed else 'recorded')).lastrowid
        if confirmed:
            con.execute('''INSERT INTO payment_events(payment_id,event_type,direction,amount_cents,reason,actor_sub)
                VALUES (?,'verify','none',NULL,?,?)''',(payment_id,f"Confirmed by Clover payment {attempt['provider_payment_id']}",actor['sub']))
        con.execute('UPDATE checkout_attempts SET payment_id=? WHERE id=?',(payment_id,attempt['id']))
        for photo in con.execute('SELECT * FROM checkout_evidence WHERE attempt_id=?',(attempt['id'],)).fetchall():
            attach_photo(con,photo,payment_id)
    con.execute('UPDATE sale_documents SET version=version+1 WHERE id=?',(sale_id,))
    con.execute("UPDATE checkout_orders SET status='completed',sale_id=?,payload=? WHERE id=?",
                (sale_id,json.dumps(intent),row['id']))
    return sale_id


# ── Manual orders paid like Clover orders ────────────────────────────────────
# The same prices as at the Clover counter: card pays the order total, other tenders alone get the
# cash discount, and a split with card rounds down to the dollar. Card is recorded in POPCORE here,
# because a manual order has no Clover order behind it.

def manual_summary(intent):
    """A manual order's pricing facts; an agreed discount entered on the order is kept, like a Clover discount."""
    return {'total': intent['collected_cents'], 'simple': True, 'subtotal': intent['subtotal_cents'],
            'external_discount_cents': intent['reduction_cents'], 'ext_discount': intent['reduction_cents'] > 0,
            'cash_priced': False, 'popcore_discount': None}


def manual_due(summary, *, card_cents, other_cents, plan):
    if other_cents == 0 and plan != 'split_card':
        return summary['total'], None  # card alone
    return pricing.amount_due(summary, clover_cents=card_cents, popcore_cents=other_cents, plan=plan, verified=True)


def _manual_money(con, checkout_id):
    """(card, every other tender, of which cash) received on a manual order."""
    card = other = cash = 0
    for attempt in con.execute("SELECT tender,amount_cents FROM checkout_attempts WHERE checkout_id=? AND status='completed'",
                               (checkout_id,)):
        if attempt['tender'] == 'card':
            card += attempt['amount_cents']
        else:
            other += attempt['amount_cents']
            cash += attempt['amount_cents'] if attempt['tender'] == 'cash' else 0
    return card, other, cash


def cash_amount_limit(tender, amount, due, *, cash_cents, received_cents):
    """The most this payment may be. Cash pays what the other tenders leave rounded to 5¢ (Canada has
    no pennies) and is counted in 5¢ steps; every other tender is exact."""
    if tender != 'cash':
        return due - received_cents
    limit = pricing.cash_limit(due, cash_cents=cash_cents, other_cents=received_cents - cash_cents)
    if amount % 5 and amount != limit:
        raise InventoryValidationError(f'Cash is rounded to 5¢: collect ${limit / 100:.2f}, or an amount in 5¢ steps')
    return limit


def manual_block(row, actor):
    summary = manual_summary(row['order'])
    paid = [a for a in row['attempts'] if a['status'] == 'completed']
    card = sum(a['amount_cents'] for a in paid if a['tender'] == 'card')
    other = sum(a['amount_cents'] for a in paid if a['tender'] != 'card')
    cash = sum(a['amount_cents'] for a in paid if a['tender'] == 'cash')
    is_open = row['status'] == 'open' and not row['abandoned_reason']
    return {'total_cents': summary['total'], 'card_cents': card, 'other_cents': other,
            'collect': pricing.collect(summary, clover_cents=card, popcore_cents=other, cash_cents=cash,
                                       verified=True) if is_open else None,
            'can_receive': bool(row['can_process']),
            'undo_attempt_ids': [a['id'] for a in paid if is_open and row['can_process']
                                 and (a['recorded_by'] == actor['sub'] or manager(actor))]}


def receive_manual(con, checkout_id, data, actor, request_key):
    """Record money received for a manual order at its automatic price; record the sale once it is all in."""
    tender, plan = data.get('tender'), data.get('plan', 'pay')
    if tender not in TENDERS or plan not in ('pay', 'split_card'):
        raise InventoryValidationError('Choose card, cash, e-transfer, WeChat Pay or Alipay')
    amount = read_int(data.get('amount_cents'), 'amount_cents', minimum=1)
    expected = read_int(data.get('expected_version'), 'expected_version', minimum=1)
    fen, rate = cny_charge(tender, amount, data)
    _begin(con)
    try:
        row = checkout_row(con, checkout_id, actor, cashier=True)
        key, digest, prior = _replay(con, request_key, 'checkout_receive', actor, {**data, 'checkout_id': row['id']})
        if prior:
            con.commit()
            return checkout_detail(con, row['id'], actor)
        if is_clover(con, row['id']):
            raise InventoryConflict('Clover orders are paid from their Clover screen', 'clover_order')
        if con.execute('SELECT 1 FROM clover_sends WHERE checkout_id=?', (row['id'],)).fetchone():
            raise InventoryConflict('This order is being sent to Clover. Retry or stop sending first.', 'clover_sending')
        if row['status'] != 'open' or row['version'] != expected:
            raise InventoryConflict('Checkout changed. Refresh before continuing.', 'checkout_state_conflict')
        if row['abandoned_reason']:
            raise InventoryConflict('Abandoned checkouts can only be refunded', 'checkout_abandoned')
        intent = json.loads(row['payload'])
        require_box_choice(con, intent)
        summary = manual_summary(intent)
        card, other, cash = _manual_money(con, row['id'])
        due, _ = manual_due(summary, card_cents=card + (amount if tender == 'card' else 0),
                            other_cents=other + (0 if tender == 'card' else amount), plan=plan)
        if data.get('expected_due_cents') is not None and data['expected_due_cents'] != due:
            raise InventoryConflict('The amount due changed. Check the new amount.', 'price_changed')
        if amount > cash_amount_limit(tender, amount, due, cash_cents=cash, received_cents=card + other):
            raise InventoryValidationError('That is more than the amount still to collect')
        # A payment started on the old screen and never received is replaced by this one.
        con.execute("UPDATE checkout_attempts SET status='cancelled' WHERE checkout_id=? AND status='pending'", (row['id'],))
        con.execute('''INSERT INTO checkout_attempts(checkout_id,tender,amount_cents,assigned_to,status,recorded_by,
            cny_fen,cny_per_cad) VALUES (?,?,?,?,'completed',?,?,?)''',
            (row['id'], tender, amount, actor['sub'], actor['sub'], fen, rate))
        cash += amount if tender == 'cash' else 0
        received = card + other + amount
        due = pricing.settled_due(due, cash_cents=cash, other_cents=received - cash)
        if received == due:
            # Cash rounded to 5¢ is part of the reduction (or, above the order's total, its rounding).
            final = {**intent, **pricing.collected_money(intent['gross_cents'], due)}
            finalize_sale(con, row, final, actor=actor, key=key)
        con.execute('UPDATE checkout_orders SET version=version+1 WHERE id=?', (row['id'],))
        record_late_adjustment(con, row['store_id'], row['business_date'], 'checkout', row['id'],
                               'Checkout changed after store-day close', actor['sub'])
        _remember(con, key, 'checkout_receive', row['id'], actor, digest, {'id': row['id']})
        con.commit()
        return checkout_detail(con, row['id'], actor)
    except Exception:
        con.rollback()
        raise


def undo_manual(con, checkout_id, data, actor, request_key):
    """Take back a payment recorded on a manual order before its sale exists (money handed back, mistyped)."""
    attempt_id = read_int(data.get('attempt_id'), 'attempt_id', minimum=1)
    reason = data.get('reason')
    if not isinstance(reason, str) or not reason.strip():
        raise InventoryValidationError('Enter why this payment is taken back')
    _begin(con)
    try:
        row = checkout_row(con, checkout_id, actor, cashier=True)
        key, digest, prior = _replay(con, request_key, 'checkout_undo', actor, {**data, 'checkout_id': row['id']})
        if prior:
            con.commit()
            return checkout_detail(con, row['id'], actor)
        attempt = con.execute('SELECT * FROM checkout_attempts WHERE id=? AND checkout_id=?', (attempt_id, row['id'])).fetchone()
        if (is_clover(con, row['id']) or row['status'] != 'open' or row['abandoned_reason'] or attempt is None
                or attempt['status'] != 'completed'):
            raise InventoryConflict('Only a payment on an unfinished manual order can be taken back', 'checkout_state_conflict')
        if attempt['recorded_by'] != actor['sub'] and not manager(actor):
            raise PermissionError('Only the cashier who recorded this payment or a manager can take it back')
        con.execute("UPDATE checkout_attempts SET status='cancelled',cancel_reason=? WHERE id=?", (reason.strip()[:300], attempt_id))
        con.execute('UPDATE checkout_orders SET version=version+1 WHERE id=?', (row['id'],))
        record_late_adjustment(con, row['store_id'], row['business_date'], 'checkout', row['id'],
                               'Checkout changed after store-day close', actor['sub'])
        _remember(con, key, 'checkout_undo', row['id'], actor, digest, {'id': row['id']})
        con.commit()
        return checkout_detail(con, row['id'], actor)
    except Exception:
        con.rollback()
        raise


def is_clover(con, checkout_id):
    return con.execute('SELECT 1 FROM clover_orders WHERE checkout_id=?', (checkout_id,)).fetchone() is not None


def change_checkout(con, checkout_id, action, data, actor, request_key):
    _begin(con)
    try:
        row = checkout_row(con,checkout_id,actor,cashier=True)
        if action in ('abandon','refund') and not manager(actor):
            raise PermissionError('Manager access required')
        key,digest,prior = _replay(con,request_key,'checkout_'+action,actor,{**data,'checkout_id':checkout_id})
        if prior:
            con.commit()
            return checkout_detail(con,checkout_id,actor)
        expected = read_int(data.get('expected_version'),'expected_version',minimum=1)
        if row['status'] != 'open' or row['version'] != expected:
            raise InventoryConflict('Checkout changed. Refresh before continuing.', 'checkout_state_conflict')
        detail = checkout_detail(con,checkout_id,actor)
        if action in ('attempts','complete','finalize','cancel') and is_clover(con,checkout_id):
            raise InventoryConflict('Clover orders are paid with Received, Card on Clover or Cancel order','clover_order')
        if row['abandoned_reason'] and action in ('attempts','complete','finalize'):
            raise InventoryConflict('Abandoned checkouts can only be refunded','checkout_abandoned')
        if action == 'abandon':
            reason = data.get('reason')
            if not isinstance(reason,str) or not reason.strip():
                raise InventoryValidationError('Enter the reason for abandoning this checkout')
            if row['abandoned_reason'] or not detail['received_cents']:
                raise InventoryConflict('Only an active paid checkout can be abandoned','checkout_state_conflict')
            con.execute("""UPDATE checkout_orders SET abandoned_reason=?,abandoned_by=?,
                abandoned_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
                (reason.strip(),actor['sub'],checkout_id))
            con.execute("UPDATE checkout_attempts SET status='cancelled' WHERE checkout_id=? AND status='pending'",(checkout_id,))
        elif action == 'refund':
            if not row['abandoned_reason']:
                raise InventoryConflict('Abandon the checkout before recording refunds','checkout_state_conflict')
            attempt_id = read_int(data.get('attempt_id'),'attempt_id',minimum=1)
            attempt = next((a for a in detail['attempts'] if a['id']==attempt_id and a['status']=='completed'),None)
            amount = _optional_cents(data.get('amount_cents'),'amount_cents',nonnegative=True)
            if attempt is None or amount is None or amount <= 0 or amount > attempt['refundable_cents']:
                raise InventoryValidationError('Refund must be within the original payment remaining refundable amount')
            refund_date = read_date(data.get('business_date'),'business_date')
            if not row['business_date'] <= refund_date <= datetime.now(ZoneInfo('America/Toronto')).date().isoformat():
                raise InventoryValidationError('Refund date must be between the receipt date and today')
            reference,reason = data.get('reference'),data.get('reason')
            if not isinstance(reference,str) or not 1 <= len(reference.strip()) <= 120:
                raise InventoryValidationError('Refund reference must contain 1 to 120 characters')
            if not isinstance(reason,str) or not reason.strip() or data.get('confirmed_received_and_refunded') is not True:
                raise InventoryValidationError('Confirm the original receipt and actual refund, and enter a reason')
            if 'tender' in data and data['tender'] != attempt['tender']:
                raise InventoryValidationError('Refund must use the original payment tender')
            try:
                refund_id = con.execute("""INSERT INTO checkout_refunds
                    (attempt_id,store_id,amount_cents,business_date,reference,reason,recorded_by,receipt_confirmed)
                    VALUES (?,?,?,?,?,?,?,1)""",(attempt_id,row['store_id'],amount,refund_date,
                    reference.strip(),reason.strip(),actor['sub'])).lastrowid
            except sqlite3.IntegrityError as exc:
                raise InventoryConflict('Refund reference already exists','refund_reference_conflict') from exc
            if detail['refunded_cents'] + amount == detail['received_cents']:
                con.execute("UPDATE checkout_orders SET status='cancelled' WHERE id=?",(checkout_id,))
            for business_date in {row['business_date'],refund_date}:
                record_late_adjustment(con,row['store_id'],business_date,'checkout_refund',refund_id,
                    'Checkout refund recorded after store-day close',actor['sub'])
        elif action == 'attempts':

            amount = _optional_cents(data.get('amount_cents'),'amount_cents',nonnegative=True)
            if amount is None or amount <= 0 or amount > detail['remaining_cents'] or not isinstance(data.get('tender'), str) or data['tender'] not in TENDERS:
                raise InventoryValidationError('Choose a tender and an amount within the remaining balance')
            assigned = data.get('assigned_to') or actor['sub']
            if not isinstance(assigned,str):
                raise InventoryValidationError('Assigned staff is invalid')
            if not con.execute('SELECT 1 FROM inventory_access WHERE store_id=? AND auth0_sub=?',(row['store_id'],assigned)).fetchone():
                raise InventoryValidationError('Assigned staff must have access to this store')
            con.execute("UPDATE checkout_attempts SET status='cancelled' WHERE checkout_id=? AND status='pending'",(checkout_id,))
            con.execute('INSERT INTO checkout_attempts(checkout_id,tender,amount_cents,assigned_to) VALUES (?,?,?,?)',(checkout_id,data['tender'],amount,assigned))
        elif action == 'complete':
            attempt_id = read_int(data.get('attempt_id'),'attempt_id',minimum=1)
            changed = con.execute("UPDATE checkout_attempts SET status='completed',recorded_by=? WHERE id=? AND checkout_id=? AND status='pending'",(actor['sub'],attempt_id,checkout_id))
            if changed.rowcount != 1:
                raise InventoryConflict('This payment attempt is no longer pending','attempt_state_conflict')
        elif action == 'cancel':
            if detail['received_cents']:
                raise InventoryConflict('Received money must remain recorded. Ask a manager to abandon the checkout and record actual refunds.','checkout_has_payments')
            con.execute("UPDATE checkout_orders SET status='cancelled' WHERE id=?",(checkout_id,))
            con.execute("UPDATE checkout_attempts SET status='cancelled' WHERE checkout_id=? AND status='pending'",(checkout_id,))
        elif action == 'finalize':
            if detail['remaining_cents'] != 0:
                raise InventoryConflict('Record all received payments before recording the sale','checkout_unpaid')
            finalize_sale(con,row,detail['order'],actor=actor,key=key)
        else:
            raise InventoryValidationError('Unknown checkout action')
        con.execute('UPDATE checkout_orders SET version=version+1 WHERE id=?',(checkout_id,))
        record_late_adjustment(con,row['store_id'],row['business_date'],'checkout',checkout_id,'Checkout changed after store-day close',actor['sub'])
        _remember(con,key,'checkout_'+action,checkout_id,actor,digest,{'id':checkout_id})
        con.commit()
        return checkout_detail(con,checkout_id,actor)
    except Exception:
        con.rollback()
        raise
