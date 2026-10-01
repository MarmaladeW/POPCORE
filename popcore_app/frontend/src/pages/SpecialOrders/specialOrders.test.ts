import assert from 'node:assert/strict'
import test from 'node:test'

import { paidCents, paymentState, remainingCents } from './specialOrders.ts'


const order = {
  id: 1,
  total_cents: 10000,
  payments: [{ id: 1, amount_cents: 2000, paid_at: '2026-09-21T10:00:00Z' }],
} as any


test('payment totals are derived from separate entries', () => {
  assert.equal(paidCents(order), 2000)
  assert.equal(remainingCents(order), 8000)
  assert.equal(paymentState(order), 'remaining')
  assert.equal(paymentState({
    ...order,
    payments: [{ ...order.payments[0], amount_cents: 10000 }],
  }), 'paid')
})


test('unsafe or overpaid data is rejected instead of displayed', () => {
  assert.throws(() => remainingCents({ ...order, total_cents: 1.5 }), RangeError)
  assert.throws(() => remainingCents({
    ...order,
    payments: [{ ...order.payments[0], amount_cents: 10001 }],
  }), RangeError)
  assert.throws(() => paidCents({
    ...order,
    payments: [{ ...order.payments[0], amount_cents: -1 }],
  }), RangeError)
})
