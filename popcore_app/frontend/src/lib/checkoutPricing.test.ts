import assert from 'node:assert/strict'
import test from 'node:test'

import { cashRest, convertCadCentsToCnyFen, discountQuote, hstCents, nickel, suggestPayable, suggestPaymentTarget } from './checkoutPricing.ts'

test('CNY quote uses the configured rate with exact cent rounding', () => {
  assert.equal(convertCadCentsToCnyFen(3200, '5.20'), 16640)
  assert.equal(convertCadCentsToCnyFen(1, '5.5'), 6)
  assert.equal(convertCadCentsToCnyFen(1, '0.5'), 1)
  for (const rate of ['', '0', '5.12345', 'bad']) {
    assert.equal(convertCadCentsToCnyFen(3200, rate), null)
  }
  assert.equal(convertCadCentsToCnyFen(-1, '5.2'), null)
})

test('suggestPayable rounds a 2.5% reduction to whole dollars, with ties down', () => {
  assert.equal(suggestPayable(4134), 4000)
  assert.equal(suggestPayable(712), 700)
  assert.equal(suggestPayable(2000), 1900)
  assert.equal(suggestPayable(1500), 1400)
})

test('above $15 suggestPayable takes at least $1 off, or 2.5% when more, rounded down', () => {
  assert.equal(suggestPayable(2259), 2100)
  assert.equal(suggestPayable(9914), 9600)
  assert.equal(suggestPayable(6777), 6600)
  assert.equal(suggestPayable(1501), 1400)
  assert.equal(suggestPayable(1599), 1400)
})

// The same vectors are in popcore_app/tests/test_clover_pricing.py: both sides must agree.
test('under $10 the cash price is the previous whole dollar, and a whole dollar keeps its price', () => {
  for (const [total, expected] of [[510, 500], [999, 900], [712, 700], [101, 100], [199, 100], [950, 900],
    [100, 100], [500, 500], [900, 900], [1000, 900], [1099, 1000]]) {
    assert.equal(suggestPayable(total), expected, String(total))
  }
})

test('cash is rounded to 5 cents the Canadian way', () => {
  for (const [cents, expected] of [[2160, 2160], [2161, 2160], [2162, 2160], [2163, 2165], [2164, 2165], [2165, 2165],
    [2166, 2165], [2167, 2165], [2168, 2170], [2169, 2170], [747, 745], [0, 0]]) {
    assert.equal(nickel(cents), expected, String(cents))
  }
  for (const [rest, expected] of [[747, 745], [2163, 2165], [19, 20], [97, 95], [3, 5], [2, 2], [1, 1], [0, 0]]) {
    assert.equal(cashRest(rest), expected, String(rest))
  }
  assert.throws(() => nickel(1.5), RangeError)
})

test('cash alone pays the cash price rounded to 5 cents; other tenders pay it exactly', () => {
  assert.equal(suggestPaymentTarget(99, 'cash', false), 95)
  assert.equal(suggestPaymentTarget(99, 'e_transfer', false), 97)
  assert.equal(suggestPaymentTarget(712, 'cash', false), 700)
  assert.equal(suggestPaymentTarget(747, 'card', false), 747)
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

test('suggestPaymentTarget applies the tender pricing matrix', () => {
  assert.equal(suggestPaymentTarget(2831, 'card', false), 2831)
  for (const tender of ['cash', 'e_transfer', 'wechat', 'alipay']) {
    assert.equal(suggestPaymentTarget(2000, tender, false), 1900)
  }

  assert.equal(suggestPaymentTarget(2831, 'cash', true), 2800)
  assert.equal(suggestPaymentTarget(3191, 'alipay', true), 3100)
})

test('suggestPaymentTarget keeps small Card-inclusive splits exact', () => {
  assert.equal(suggestPaymentTarget(999, 'cash', true), 999)
  assert.equal(suggestPaymentTarget(1000, 'card', true), 1000)
  assert.equal(suggestPaymentTarget(1099, 'wechat', true), 1000)
})

test('suggestPaymentTarget rejects malformed cent values for every pricing path', () => {
  for (const value of [-1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => suggestPaymentTarget(value, 'card', false), RangeError)
    assert.throws(() => suggestPaymentTarget(value, 'cash', true), RangeError)
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

test('HST is 13% of the item prices, rounded half up like Clover', () => {
  assert.equal(hstCents(4000), 520)
  assert.equal(hstCents(1770 + 1999), 490)
  assert.equal(hstCents(1999), 260)
  assert.equal(hstCents(5), 1)
  assert.equal(hstCents(0), 0)
  assert.throws(() => hstCents(-1), RangeError)
  assert.throws(() => hstCents(1.5), RangeError)
})
