"""Pending checkouts. Money receipt is manual; provider verification is separate."""
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from auth import ROLE_CLAIM, ROLE_HIERARCHY
from checkout_access import require_checkout
from goods_operations import _begin, _remember, _replay
from inventory_commands import InventoryConflict, InventoryValidationError
from sales_operations import (
    TENDERS, _create_sale_in_transaction, _optional_cents, _post_sale_in_transaction,
    _sale_input, _sale_row, record_late_adjustment,
)
from validation import read_date, read_int


def manager(actor):
    return ROLE_HIERARCHY.get(actor.get(ROLE_CLAIM), 0) >= ROLE_HIERARCHY['manager']


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
    return row


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
        if not isinstance(reference, str):
            raise InventoryValidationError('Reference must be text')
        reference = reference.strip()
        if not reference or len(reference) > 120:
            raise InventoryValidationError('Reference must contain 1 to 120 characters')
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
            intent = detail['order']
            intent['entry_mode'] = 'already_paid'
            # Keep the original cashier as the owner, even when a scoped manager resolves it.
            owner = {**actor, 'sub':row['created_by']}
            sale_id = _create_sale_in_transaction(con,intent,owner)
            _post_sale_in_transaction(con,_sale_row(con,sale_id),1,actor,key)
            for attempt in detail['attempts']:
                if attempt['status'] != 'completed':
                    continue
                payment_id = con.execute('''INSERT INTO sale_payments(sale_id,tender,amount_cents,source_system,source_account,source_reference,recorded_by)
                    VALUES (?,?,?,'popcore_checkout',?,?,?)''',(sale_id,attempt['tender'],attempt['amount_cents'],str(row['store_id']),str(attempt['id']),attempt['recorded_by'])).lastrowid
                con.execute('UPDATE checkout_attempts SET payment_id=? WHERE id=?',(payment_id,attempt['id']))
                for photo in con.execute('SELECT * FROM checkout_evidence WHERE attempt_id=?',(attempt['id'],)).fetchall():
                    attach_photo(con,photo,payment_id)
            con.execute('UPDATE sale_documents SET version=version+1 WHERE id=?',(sale_id,))
            con.execute("UPDATE checkout_orders SET status='completed',sale_id=? WHERE id=?",(sale_id,checkout_id))
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
