import assert from 'node:assert/strict'
import { test } from 'node:test'
import { savedPriceSummary, savedLineValue } from './historicalSales.ts'

test('saved-price estimates never substitute current catalog prices or hide unknowns', () => {
  const rows = [{unit_price: 12.5, price: 99, qty_sold: 2}, {unit_price: null, price: 90, qty_sold: 3}, {unit_price: 0, price: 10, qty_sold: 1}]
  assert.deepEqual(savedPriceSummary(rows), {knownValue:25, unknownRows:1, knownRows:2})
  assert.equal(savedLineValue(rows[1]), null)
  assert.equal(savedLineValue(rows[2]), 0)
  assert.equal(savedLineValue({qty_sold:4}), null)
  assert.deepEqual(savedPriceSummary([]), {knownValue:0,unknownRows:0,knownRows:0})
})
