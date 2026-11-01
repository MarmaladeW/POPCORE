import assert from 'node:assert/strict'
import test from 'node:test'
import { canSeeInventory, stockFigureValue } from './inventoryView.ts'

const stores = [{ code: 'DT', inventory_visible: true }, { code: 'MK', inventory_visible: false }]

test('managers and admins always see stock', () => {
  assert.equal(canSeeInventory([], null, true), true)
  assert.equal(canSeeInventory(stores, { code: 'MK' }, true), true)
})

test('other accounts see only the stores an admin ticked', () => {
  assert.equal(canSeeInventory(stores, { code: 'DT' }, false), true)
  assert.equal(canSeeInventory(stores, { code: 'MK' }, false), false)
  assert.equal(canSeeInventory(stores, { code: 'XX' }, false), false)
  assert.equal(canSeeInventory(stores, null, false), false)
})

test('All stores needs every store ticked', () => {
  assert.equal(canSeeInventory(stores, { code: 'ALL' }, false), false)
  assert.equal(canSeeInventory(stores.map(store => ({ ...store, inventory_visible: true })), { code: 'ALL' }, false), true)
  assert.equal(canSeeInventory([], { code: 'ALL' }, false), false)
})

test('a hidden store shows only in or out of stock', () => {
  assert.equal(stockFigureValue({}, 7), 7)
  assert.equal(stockFigureValue({ stock_hidden: true, out_of_stock: false }, 0), 'in')
  assert.equal(stockFigureValue({ stock_hidden: true, out_of_stock: true }, 0), 'out')
})
