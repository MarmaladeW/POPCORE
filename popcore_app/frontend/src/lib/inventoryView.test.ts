import assert from 'node:assert/strict'
import test from 'node:test'
import { canCheckout, canSeeInventory, stockFigureValue } from './inventoryView.ts'

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

test('买单 waits for the selected store\'s Clover for staff only', () => {
  const stores = [{ code: 'DT', checkout: false }, { code: 'MK', checkout: true }, { code: 'MT' }]
  assert.equal(canCheckout(stores, { code: 'DT' }, true), true)
  assert.equal(canCheckout(stores, { code: 'DT' }, false), false)
  assert.equal(canCheckout(stores, { code: 'MK' }, false), true)
  // A store the server says nothing about (an older server) stays open.
  assert.equal(canCheckout(stores, { code: 'MT' }, false), true)
  assert.equal(canCheckout([{ code: 'DT', checkout: false }], { code: 'ALL' }, false), false)
  assert.equal(canCheckout(stores, { code: 'ALL' }, false), true)
  assert.equal(canCheckout([], null, false), true)
})
