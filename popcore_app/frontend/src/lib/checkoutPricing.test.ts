import assert from 'node:assert/strict'
import test from 'node:test'

import { discountQuote, suggestPayable } from './checkoutPricing.ts'

test('suggestPayable rounds a 2.5% reduction to whole dollars, with ties down', () => {
  assert.equal(suggestPayable(4134), 4000)
  assert.equal(suggestPayable(712), 700)
  assert.equal(suggestPayable(9914), 9700)
  assert.equal(suggestPayable(2000), 1900)
})

test('suggestPayable keeps tiny positive totals positive', () => {
  assert.equal(suggestPayable(1), 1)
  assert.equal(suggestPayable(20), 19)
  assert.equal(suggestPayable(51), 50)
  assert.equal(suggestPayable(99), 97)
  assert.equal(suggestPayable(0), 0)
})

test('suggestPayable rejects malformed cent values', () => {
  for (const value of [-1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => suggestPayable(value), RangeError)
  }
})

test('discountQuote estimates the pre-tax discount using the supplied effective tax', () => {
  assert.deepEqual(discountQuote(1000, 130, 1000), {
    discountCents: 115,
    predictedTotalCents: 1000,
    savingsCents: 130,
    warning: false,
  })
  assert.deepEqual(discountQuote(333, 43, 300), {
    discountCents: 67,
    predictedTotalCents: 300,
    savingsCents: 76,
    warning: true,
  })
})

test('discountQuote handles zero tax and an unchanged target', () => {
  assert.deepEqual(discountQuote(1000, 0, 800), {
    discountCents: 200,
    predictedTotalCents: 800,
    savingsCents: 200,
    warning: false,
  })
  assert.deepEqual(discountQuote(1000, 130, 1130), {
    discountCents: 0,
    predictedTotalCents: 1130,
    savingsCents: 0,
    warning: false,
  })
})

test('discountQuote warns only above twenty percent of the original total', () => {
  assert.equal(discountQuote(1000, 0, 800)?.warning, false)
  assert.equal(discountQuote(1000, 0, 799)?.warning, true)
  assert.deepEqual(discountQuote(1020, 133, 923), {
    discountCents: 204,
    predictedTotalCents: 922,
    savingsCents: 230,
    warning: false,
  })
})

test('discountQuote rejects invalid and unsafe inputs without mutating arguments', () => {
  const invalidInputs: ReadonlyArray<readonly [number, number, number]> = [
    [-1, 0, 1],
    [1.5, 0, 1],
    [100, -1, 50],
    [100, 0, 0],
    [100, 0, 101],
    [Number.MAX_SAFE_INTEGER, 1, 1],
    [Number.MAX_SAFE_INTEGER + 1, 0, 1],
    [100, Number.NaN, 50],
  ]
  for (const args of invalidInputs) {
    assert.equal(discountQuote(...args), null)
  }

  const inputs = [1000, 130, 1000] as const
  discountQuote(...inputs)
  assert.deepEqual(inputs, [1000, 130, 1000])
})
