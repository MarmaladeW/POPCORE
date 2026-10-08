"""Step 3 of the Clover rehearsal: post one settled Clover-sourced order as a real sale.

Off unless CLOVER_SANDBOX_POST_SALES=1 and CLOVER_SANDBOX_MERCHANT_ID are set. The sandbox
adapter never calls this; it only reads the isolated sandbox database. Everything written
here goes to the real POPCORE database through the existing sale, payment and evidence paths,
in one transaction, keyed by the Clover order so a retry can never post twice.
"""
import io
import os
import re
from types import SimpleNamespace

from goods_operations import _begin
from inventory_commands import InventoryConflict, require_inventory_access
from payment_evidence import evidence_path, prepare_image, publish, remove_unreferenced
from sales_operations import (
    TENDERS, _create_sale_in_transaction, _post_sale_in_transaction, _sale_input, _sale_row,
    record_late_adjustment, sale_detail,
)

IDENTIFIER = re.compile(r'^[A-Za-z0-9]{13}$')
SOURCE_SYSTEM = 'clover'


def enabled():
    """Explicit opt-in, separate from the sandbox adapter's own enable flag."""
    return (os.environ.get('CLOVER_SANDBOX_POST_SALES') == '1'
            and bool(IDENTIFIER.fullmatch(os.environ.get('CLOVER_SANDBOX_MERCHANT_ID', ''))))


def merchant():
    return os.environ['CLOVER_SANDBOX_MERCHANT_ID']


def _lines(con, value, payload):
    """Map sandbox lines to sale lines. Repeated scans of one product merge; the rest stay raw."""
    mapped, raw = {}, []
    for line, item in zip(value['order']['lines'], payload['items']):
        quantity = line['quantity']
        whole = float(quantity).is_integer() and quantity >= 1
        price = item.get('price') if type(item.get('price')) is int and item['price'] >= 0 else None
        if line['mapped'] and whole:
            entry = mapped.setdefault(line['product_id'], {'quantity': 0, 'prices': set()})
            entry['quantity'] += int(quantity)
            entry['prices'].add(price)
            continue
        text = f"Clover item: {line['product_name_snapshot']}"
        if line['item_code']:
            text += f" [{line['item_code']}]"
        if not whole:
            text += f" x{quantity:g} {line['unit']}"
        raw.append({'quantity': int(quantity) if whole else 1, 'raw_product_text': text[:240],
                    'unit_price_cents': price if whole else None})
    lines = []
    for product_id, entry in mapped.items():
        unit = con.execute('SELECT stock_unit FROM products WHERE id=?', (product_id,)).fetchone()
        lines.append({'product_id': product_id, 'unit': unit['stock_unit'] if unit else None,
                      'quantity': entry['quantity'],
                      'unit_price_cents': next(iter(entry['prices'])) if len(entry['prices']) == 1 else None})
    return lines + raw


def _payments(sandbox, value, reference):
    """One payment fact per completed attempt: Clover card facts and POPCORE-recorded tenders."""
    clover = {row['id']: row for row in sandbox.execute(
        'SELECT id, source_id FROM payments WHERE order_id=?', (value['id'],))}
    manual = {row['id']: row for row in sandbox.execute(
        'SELECT id, recorded_by FROM manual_payments WHERE order_id=?', (value['id'],))}
    payments = []
    for attempt in value['attempts']:
        if attempt['status'] != 'completed':
            continue
        if attempt['tender'] not in TENDERS:
            raise InventoryConflict(f"Clover tender '{attempt['tender']}' is not a POPCORE tender",
                                    'clover_tender_unknown')
        if attempt['source'] == 'clover':
            table, source_reference = 'evidence', clover[attempt['id']]['source_id']
            recorded_by = value['cashier_sub']
        else:
            table, source_reference = 'manual_evidence', f"{reference}/popcore/{attempt['id']}"
            recorded_by = manual[attempt['id']]['recorded_by']
        photos = [dict(row) for row in sandbox.execute(
            f'SELECT id, content_hash, mime_type, content, uploader_sub FROM {table} '
            'WHERE payment_id=? ORDER BY id', (attempt['id'],))]
        payments.append({'tender': attempt['tender'], 'amount_cents': attempt['amount_cents'],
                         'source_reference': source_reference, 'recorded_by': recorded_by,
                         'kind': attempt['source'], 'attempt_id': attempt['id'], 'photos': photos})
    return payments


def _object_id(account, reference, payment, photo):
    """Deterministic so a retry recognises an already copied photo without a second file."""
    extension = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}[photo['mime_type']]
    return (f"clover-{account}-{reference}-{payment['kind']}{payment['attempt_id']}-"
            f"{photo['id']}-{photo['content_hash'][:16]}{extension}")


def _copy_photo(con, payment_id, object_id, photo, published):
    prepared = prepare_image(SimpleNamespace(stream=io.BytesIO(photo['content'])))
    prepared['object_id'] = object_id
    path = evidence_path(object_id)
    if path.exists():
        remove_unreferenced(path, con)  # leftover from an interrupted earlier attempt
    published.append(publish(prepared))
    con.execute(
        """INSERT INTO payment_evidence (payment_id, object_id, mime_type, byte_size, uploader_sub)
           VALUES (?, ?, ?, ?, ?)""",
        (payment_id, object_id, prepared['mime_type'], prepared['byte_size'], photo['uploader_sub']),
    )


def post_settled_order(con, sandbox, value, payload, actor, *, account):
    """Create, post and pay the real sale for a settled order; replay returns the same sale.

    `value` is the sandbox adapter's detail() for the order, `payload` its Clover snapshot.
    """
    if not value['settled_in_popcore']:
        raise InventoryConflict('Order is not settled in POPCORE', 'clover_not_settled')
    if value['changed_after_settlement']:
        raise InventoryConflict('Clover changed this order after settlement. Review it before posting.',
                                'clover_changed_after_settlement')
    if value['remaining_cents'] or not value['cashier_sub']:
        raise InventoryConflict('Order is not fully recorded', 'clover_not_settled')
    require_inventory_access(con, actor, (value['store_id'],), 'staff')
    reference = value['reference']
    payments = _payments(sandbox, value, reference)
    if sum(p['amount_cents'] for p in payments) != value['order']['collected_cents']:
        raise InventoryConflict('Recorded payments do not equal the settled amount', 'clover_not_settled')
    owner = {**actor, 'sub': value['cashier_sub']}
    published = []
    _begin(con)
    try:
        existing = con.execute(
            """SELECT sale_id FROM sale_sources
               WHERE source_system=? AND source_account=? AND source_reference=?""",
            (SOURCE_SYSTEM, account, reference)).fetchone()
        created = existing is None
        if created:
            money = value['order']
            intent = _sale_input(con, {
                'store_id': value['store_id'], 'business_date': value['business_date'],
                'entry_mode': 'already_paid',
                'source': {'system': SOURCE_SYSTEM, 'account': account, 'reference': reference},
                'subtotal_cents': money['subtotal_cents'] if value['totals_known'] else None,
                'source_tax_cents': money['source_tax_cents'] if value['totals_known'] else None,
                'gross_cents': money['gross_cents'], 'reduction_cents': money['reduction_cents'],
                'rounding_cents': 0, 'collected_cents': money['collected_cents'],
                'lines': _lines(con, value, payload),
            }, actor)
            sale_id = _create_sale_in_transaction(con, intent, owner)
            _post_sale_in_transaction(con, _sale_row(con, sale_id), 1, actor,
                                      f'clover-post:{account}:{reference}')
        else:
            sale_id = existing['sale_id']
        sale = _sale_row(con, sale_id)
        changed = False
        for payment in payments:
            row = con.execute(
                """SELECT id, sale_id FROM sale_payments
                   WHERE source_system=? AND source_account=? AND source_reference=?""",
                (SOURCE_SYSTEM, account, payment['source_reference'])).fetchone()
            if row is None:
                payment_id = con.execute(
                    """INSERT INTO sale_payments
                       (sale_id, tender, amount_cents, source_system, source_account,
                        source_reference, recorded_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (sale_id, payment['tender'], payment['amount_cents'], SOURCE_SYSTEM, account,
                     payment['source_reference'], payment['recorded_by'])).lastrowid
                changed = True
            elif row['sale_id'] != sale_id:
                raise InventoryConflict('Payment source belongs to another sale', 'source_conflict')
            else:
                payment_id = row['id']
            for photo in payment['photos']:
                object_id = _object_id(account, reference, payment, photo)
                if con.execute('SELECT 1 FROM payment_evidence WHERE object_id=?', (object_id,)).fetchone():
                    continue
                _copy_photo(con, payment_id, object_id, photo, published)
                changed = True
        if changed:
            con.execute('UPDATE sale_documents SET version=version+1 WHERE id=?', (sale_id,))
            record_late_adjustment(con, sale['store_id'], sale['business_date'], 'sale_payment', sale_id,
                                   'Payment recorded after store-day close', actor['sub'])
        detail = sale_detail(con, sale_id, actor=actor)
        con.commit()
    except Exception:
        if con.in_transaction:
            con.rollback()
        for path in published:
            remove_unreferenced(path, con)
        raise
    return {
        'created': created,
        **{key: detail[key] for key in ('sale_id', 'version', 'status', 'financial_status',
                                        'allocation_status', 'inventory_document_id',
                                        'unresolved_reasons', 'collected_cents', 'payment_total_cents')},
        'payments': [{'id': p['id'], 'tender': p['tender'], 'amount_cents': p['amount_cents'],
                      'source_reference': p['source_reference'], 'evidence': len(p['evidence'])}
                     for p in detail['payments']],
    }
