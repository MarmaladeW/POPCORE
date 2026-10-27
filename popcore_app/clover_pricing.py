"""What a Clover order costs, how its sale records the money, and the keys tying both systems.

Pure functions only. The cash prices mirror popcore_app/frontend/src/lib/checkoutPricing.ts:
non-card tenders get the cash price ("Cash discount", see suggest_payable); a split that includes
card, or an order that already carries another discount (RewardUp loyalty), is only floored to
the whole dollar ("Rounding"); card alone pays Clover's total. Physical cash is then rounded to
5¢, because Canada has no pennies (cash_rest); e-transfer, WeChat Pay, Alipay and card stay exact.
"""
import hashlib
import json
import re

MAX_CENTS = 2**53 - 1
IDENTIFIER = re.compile(r'[A-Za-z0-9]{13}\Z')
INSTANCE = re.compile(r'[A-Za-z0-9]{1,12}\Z')
CASH_DISCOUNT = 'Cash discount'
ROUNDING = 'Rounding'
POPCORE_DISCOUNTS = (CASH_DISCOUNT, ROUNDING)
# Before POPCORE applied the cash price, staff typed it on Clover as e.g. "CA$0.89 Off".
_LEGACY_CASH_PRICE = re.compile(r'CA\$\d+\.\d{2} Off\Z')
_PAYMENT_KEY = re.compile(r'popcore-([A-Za-z0-9]{1,12})-(\d{1,15})-(\d{1,15})\Z')
_CARD_KEYS = {'com.clover.tender.credit_card', 'com.clover.tender.debit_card'}
_TENDER_NAMES = {
    'card': 'card', 'creditcard': 'card', 'debitcard': 'card', 'cash': 'cash',
    'etransfer': 'e_transfer', 'interacetransfer': 'e_transfer',
    'wechat': 'wechat', 'wechatpay': 'wechat', 'alipay': 'alipay',
}


def safe_cents(value):
    return type(value) is int and 0 <= value <= MAX_CENTS


def suggest_payable(total):
    """The cash price, for cash, e-transfer, WeChat Pay and Alipay (before cash is rounded to 5¢).

    Under $1: about 2.5% off, at least 1¢. From $1 to $9.99: down to the previous whole dollar, so
    $5.10 is $5, $9.99 is $9 and $7.12 is $7 (owner, 2026-10-27); a whole-dollar total is
    whole_dollar_under_ten. From $10 to $15: the nearest whole dollar to 2.5% off, ties down and
    always below the total. Above $15: at least $1 off, or 2.5% when that is more, rounded down:
    $22.59 to $21, $41.34 to $40, $99.14 to $96."""
    if not safe_cents(total):
        raise ValueError('Total cents must be a nonnegative safe integer')
    if total == 0:
        return 0
    if total < 100:
        return max(1, (total * 390 + 199) // 400)
    if total < 1000:
        if total % 100 == 0:
            return whole_dollar_under_ten(total)
        return total - total % 100
    if total > 1500:
        return min(total * 975 // 100_000, (total - 100) // 100) * 100
    suggestion = ((total * 1950 + 99_999) // 200_000) * 100
    if suggestion >= total:
        suggestion -= 100
    return max(1, suggestion)


def whole_dollar_under_ten(total):
    """The cash price of a whole-dollar total from $1 to $9: the total itself, $5.00 stays $5.00.

    Owner, 2026-10-27. The owner may switch this to one dollar less; change it here and in
    wholeDollarUnderTen in checkoutPricing.ts."""
    return total


def nickel(cents):
    """Canadian cash rounding to 5¢: ending in 1-2 goes down to 0, 3-4 up to 5, 6-7 down to 5, 8-9 up to 10."""
    return (cents + 2) // 5 * 5


def cash_rest(rest):
    """What the customer hands over in cash for `rest` cents still owed, rounded to 5¢.

    A rest of 1-2¢ would round to nothing, so it stays exact."""
    return nickel(rest) or rest


def cash_limit(due, *, cash_cents, other_cents):
    """The most cash that may still be received: the rest after every non-cash payment, rounded to 5¢,
    less the cash already in. Paying exactly this settles the order (see settled_due)."""
    return cash_rest(max(0, due - other_cents)) - cash_cents


def settled_due(due, *, cash_cents, other_cents):
    """What the customer owes once cash has paid the rest rounded to 5¢: what was received, within 2¢ of
    `due`. Otherwise `due`. The difference is rounding, recorded in the sale's reduction (or, above the
    order's total, its rounding)."""
    rest = due - other_cents
    if cash_cents and rest > 0 and cash_cents == cash_rest(rest):
        return other_cents + cash_cents
    return due


def whole_dollar(total):
    """Floor to the whole dollar from $10 up; smaller totals stay exact."""
    if not safe_cents(total):
        raise ValueError('Total cents must be a nonnegative safe integer')
    return total if total < 1000 else total - total % 100


def is_popcore_discount(discount):
    return isinstance(discount, dict) and discount.get('name') in POPCORE_DISCOUNTS


def is_popcore_payment(payment):
    return isinstance(payment, dict) and str(payment.get('externalPaymentId') or '').startswith('popcore-')


def _half_up(numerator, denominator):
    return (2 * numerator + denominator) // (2 * denominator)


def summarize(snapshot):
    """Pricing facts from a validated connector order row (cloud or device)."""
    total = snapshot.get('total')
    items = snapshot.get('items') or []
    simple = bool(items) and safe_cents(total)
    subtotal = 0
    for item in items:
        price = item.get('price')
        if (not safe_cents(price) or item.get('refunded')
                or item.get('unitQty') not in (None, 0, 1000)):
            simple = False
            continue
        subtotal += price
    discounts = [d for d in snapshot.get('discounts') or [] if isinstance(d, dict)]
    foreign = [d for d in discounts if not is_popcore_discount(d)]
    line_discount = any(item.get('discountAmount') for item in items)
    # Pre-tax effect of the discounts already on the order: percentages per line, then amounts.
    external = 0
    if simple:
        for discount in foreign:
            percentage, amount = discount.get('percentage'), discount.get('amount')
            if type(percentage) is int and percentage > 0:
                external += sum(_half_up(item['price'] * percentage, 100) for item in items)
            elif type(amount) is int:
                external += abs(amount)
        external += sum(abs(item['discountAmount']) for item in items
                        if type(item.get('discountAmount')) is int)
        external = min(external, subtotal)
    return {
        'total': total if safe_cents(total) else None,
        'simple': simple,
        'subtotal': subtotal if simple else None,
        'external_discount_cents': external if simple else None,
        'ext_discount': bool(foreign) or line_discount,
        'cash_priced': any(isinstance(d.get('name'), str) and _LEGACY_CASH_PRICE.fullmatch(d['name'])
                           and type(d.get('amount')) is int and d['amount'] < 0
                           and d.get('percentage') is None for d in foreign),
        'popcore_discount': next((d for d in discounts if is_popcore_discount(d)), None),
    }


def amount_due(summary, *, clover_cents, popcore_cents, plan, verified):
    """(amount the customer owes, pricing mode) from the payments actually made.

    Only Clover payments: Clover's total. POPCORE tenders only: the cash price. Both, or a
    cashier-declared card split: floored to the dollar, unless the card alone reaches that.
    No automatic price when Clover's total can't be reproduced exactly (`verified`)."""
    total = summary['total']
    if total is None:
        raise ValueError('Clover has not confirmed a total for this order')
    if popcore_cents == 0 and plan is None:
        return total, 'clover'
    automatic = (verified and summary['simple'] and not summary['cash_priced']
                 and summary['popcore_discount'] is None)
    if not automatic:
        return total, 'exact'
    if clover_cents > 0 or plan == 'split_card':
        floor = whole_dollar(total)
        if floor == total or clover_cents >= floor:
            return total, 'exact'
        return floor, 'split_card'
    if summary['ext_discount']:
        floor = whole_dollar(total)
        return (floor, 'rounding') if floor < total else (total, 'exact')
    price = suggest_payable(total)
    return (price, 'cash_discount') if price < total else (total, 'exact')


def price_options(summary, *, verified):
    """What each way of paying costs before any money is in (for the tender buttons)."""
    if summary['total'] is None:
        return None
    pay, pay_mode = amount_due(summary, clover_cents=0, popcore_cents=1, plan='pay', verified=verified)
    split, split_mode = amount_due(summary, clover_cents=0, popcore_cents=1, plan='split_card',
                                   verified=verified)
    return {
        'card_cents': summary['total'], 'card_blocked': summary['cash_priced'],
        'cash_cents': pay, 'cash_mode': pay_mode, 'cash_label': discount_label(pay_mode),
        'split_cents': split, 'split_mode': split_mode, 'split_label': discount_label(split_mode),
    }


def collect(summary, *, clover_cents, popcore_cents, verified, cash_cents=0):
    """What is still to collect for each way of paying the rest, after the money already in.

    `clover_cents` is card money (taken on Clover, or recorded by card in a manual order);
    `popcore_cents` is every other tender, of which `cash_cents` is cash. `cash_rest_cents` is what
    cash collects for the rest, rounded to 5¢; other tenders collect `rest_cents` exactly.
    The server prices; screens only show these."""
    result = {}
    for plan in ('pay', 'split_card'):
        due, mode = amount_due(summary, clover_cents=clover_cents, popcore_cents=popcore_cents, plan=plan,
                               verified=verified)
        rest = max(0, due - clover_cents - popcore_cents)
        result[plan] = {'due_cents': due, 'mode': mode,
                        'label': discount_label(mode) if due < summary['total'] else None,
                        'rest_cents': rest,
                        'cash_rest_cents': cash_limit(due, cash_cents=cash_cents,
                                                      other_cents=clover_cents + popcore_cents - cash_cents)
                        if rest else 0}
    card_due = result['split_card']['due_cents'] if popcore_cents else summary['total']
    result['card'] = {'due_cents': card_due, 'rest_cents': max(0, card_due - clover_cents - popcore_cents)}
    return result


def discount_label(mode):
    return CASH_DISCOUNT if mode == 'cash_discount' else ROUNDING if mode in ('rounding', 'split_card') else None


def collected_money(gross, collected):
    """The reduction and rounding that take a sale's gross to the money collected.

    Everything below the gross is the reduction (cash discount, agreed discount, rounding). Cash
    rounded to 5¢ can end 1-2¢ above an order with no discount; only that goes in rounding."""
    if collected > gross + 2:
        raise ValueError('Amount due is more than the order')
    return {'reduction_cents': max(0, gross - collected), 'rounding_cents': max(0, collected - gross),
            'collected_cents': collected}


def sale_money(summary, due):
    """Sale totals that keep `_sale_input`'s invariants: gross less reduction, plus rounding, is collected."""
    total = summary['total']
    if summary['simple']:
        subtotal = summary['subtotal']
        tax = max(0, total - (subtotal - summary['external_discount_cents']))
        gross = subtotal + tax
    else:
        subtotal = tax = None
        gross = total
    return {'subtotal_cents': subtotal, 'source_tax_cents': tax, 'gross_cents': gross,
            **collected_money(gross, due)}


def _items_key(snapshot):
    # The device and Clover's cloud spell a plain single unit differently (None, 0 or 1000).
    return sorted([str(i.get('id') or ''), str(i.get('itemId') or ''), str(i.get('itemCode') or ''),
                   i.get('price'), None if i.get('unitQty') in (None, 0, 1000) else i.get('unitQty'),
                   str(i.get('name') or '')]
                  for i in snapshot.get('items') or [])


def fingerprint(snapshot, *, payments=True):
    """Changes made outside POPCORE: items, foreign discounts, Clover payments, refunds.

    POPCORE's own discount, the total it sets and its tenders are excluded, so its write-back
    never looks like somebody else's edit. Callers compare the total separately while open."""
    foreign_discounts = sorted([str(d.get('id') or ''), str(d.get('name') or ''), d.get('amount'),
                                d.get('percentage')] for d in snapshot.get('discounts') or []
                               if isinstance(d, dict) and not is_popcore_discount(d))
    facts = {'items': _items_key(snapshot), 'discounts': foreign_discounts}
    if payments:
        facts['payments'] = sorted([p.get('id'), p.get('amount'), p.get('result')]
                                   for p in snapshot.get('payments') or []
                                   if isinstance(p, dict) and not is_popcore_payment(p))
        facts['refunds'] = sorted([r.get('id'), r.get('amount')] for r in snapshot.get('refunds') or []
                                  if isinstance(r, dict))
    raw = json.dumps(facts, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def payment_key(instance, checkout_id, attempt_id):
    if not INSTANCE.fullmatch(instance):
        raise ValueError('Clover instance tag must be 1-12 letters or digits')
    return f'popcore-{instance}-{checkout_id}-{attempt_id}'


def parse_payment_key(key):
    match = _PAYMENT_KEY.fullmatch(str(key or ''))
    return (match.group(1), int(match.group(2)), int(match.group(3))) if match else None


def tender_for(label, label_key=None):
    """POPCORE tender for a Clover tender, or None when it can't be mapped."""
    if label_key in _CARD_KEYS:
        return 'card'
    if label_key == 'com.clover.tender.cash':
        return 'cash'
    return _TENDER_NAMES.get(re.sub(r'[\s_-]', '', str(label or '').casefold()))
