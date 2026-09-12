import assert from 'node:assert/strict'
import test from 'node:test'

import { formatCents, parseMoneyToCents } from './money.ts'

test('money preserves unknown, zero and exact cents', () => {
  assert.equal(parseMoneyToCents(''), null)
  assert.equal(parseMoneyToCents('0.00'), 0)
  assert.equal(parseMoneyToCents('12.30'), 1230)
  assert.equal(parseMoneyToCents(' 12.3 '), 1230)
  for (const value of ['-1', '1.001', '1e2', 'NaN', '1,200', '90071992547409.92']) {
    assert.throws(() => parseMoneyToCents(value), RangeError)
  }
  assert.equal(formatCents(null), 'Unknown')
  assert.equal(formatCents(0), '$0.00')
  assert.equal(formatCents(-125), '-$1.25')
  assert.equal(formatCents(Number.MAX_SAFE_INTEGER), '$90,071,992,547,409.91')
})
