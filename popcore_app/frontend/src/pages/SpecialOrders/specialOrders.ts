export type SpecialOrderPayment = {
  id: number
  amount_cents: number
  paid_at: string
}

export type SpecialOrder = {
  id: number
  customer_name: string
  customer_phone: string | null
  item_description: string
  total_cents: number
  status: 'open' | 'completed'
  created_by: string
  created_by_name: string
  created_at: string
  completed_by: string | null
  completed_by_name: string | null
  completed_at: string | null
  version: number
  paid_cents: number
  remaining_cents: number
  payments: SpecialOrderPayment[]
}

function cents(value: number): number {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new RangeError('Money must be nonnegative safe integer cents.')
  }
  return value
}

export function paidCents(order: Pick<SpecialOrder, 'payments'>): number {
  return order.payments.reduce((total, payment) => {
    const next = total + cents(payment.amount_cents)
    if (!Number.isSafeInteger(next)) throw new RangeError('Payment total is too large.')
    return next
  }, 0)
}

export function remainingCents(
  order: Pick<SpecialOrder, 'total_cents' | 'payments'>,
): number {
  const remaining = cents(order.total_cents) - paidCents(order)
  if (remaining < 0) throw new RangeError('Payments exceed the order total.')
  return remaining
}

export function paymentState(
  order: Pick<SpecialOrder, 'total_cents' | 'payments'>,
): 'remaining' | 'paid' {
  return remainingCents(order) === 0 ? 'paid' : 'remaining'
}
