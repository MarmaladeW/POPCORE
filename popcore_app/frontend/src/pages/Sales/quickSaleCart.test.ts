import assert from 'node:assert/strict'
import test from 'node:test'
import { addToCart, canSave, changeItem, itemsText, removeItem, requestLines, SaveKey, tenderName, waitingFor, type Quote } from './quickSaleCart.ts'

const santa = { id: 7, name: 'SA Santa', sku: 'DR0200' }
const bear = { id: 9, name: '蒙奇奇', sku: 'PM0001' }

test('adding the same product counts it up; display pieces are a line of their own', () => {
  let cart = addToCart(addToCart([], santa), santa)
  assert.deepEqual(cart.map(item => [item.productId, item.quantity, item.fromDisplay]), [[7, 2, false]])
  cart = addToCart(cart, bear)
  cart = changeItem(cart, 1, { fromDisplay: true })
  cart = addToCart(cart, bear)
  assert.deepEqual(cart.map(item => [item.productId, item.quantity, item.fromDisplay]), [[7, 2, false], [9, 1, true], [9, 1, false]])
  // Taking the display mark off merges it back into the floor line.
  cart = changeItem(cart, 1, { fromDisplay: false })
  assert.deepEqual(cart.map(item => [item.productId, item.quantity, item.fromDisplay]), [[7, 2, false], [9, 2, false]])
  assert.deepEqual(removeItem(cart, 0).map(item => item.productId), [9])
})

test('the request keeps only what was set', () => {
  let cart = changeItem(addToCart([], santa), 0, { quantity: 7 })
  assert.deepEqual(requestLines(cart), [{ product_id: 7, quantity: 7 }])
  cart = changeItem(cart, 0, { fromDisplay: true, priceCents: 1200 })
  assert.deepEqual(requestLines(cart), [{ product_id: 7, quantity: 7, from_display: true, unit_price_cents: 1200 }])
})

test('saving waits for prices and a CNY rate for WeChat and Alipay', () => {
  const quote: Quote = { store_id: 1, business_date: '2026-11-21', lines: [], missing_prices: [],
    discount_cents: 0, tenders: { cash: 1500, e_transfer: 1500, wechat: 1500, alipay: 1500, card: 1694 }, cny: null }
  assert.equal(canSave(quote, 'cash'), true)
  assert.equal(canSave(quote, ''), false)
  assert.equal(canSave(quote, 'wechat'), false)
  assert.equal(canSave({ ...quote, cny: { rate: '5.0000', source: 'Settings', fen: 7500 } }, 'alipay'), true)
  assert.equal(canSave({ ...quote, missing_prices: ['没价钱'] }, 'cash'), false)
  assert.equal(canSave(undefined, 'cash'), false)
  // The disabled Save names the one thing still missing.
  assert.deepEqual([waitingFor(undefined, 'cash'), waitingFor({ ...quote, missing_prices: ['x'] }, 'cash'),
    waitingFor(quote, ''), waitingFor(quote, 'wechat'), waitingFor(quote, 'cash')],
  ['算价中…', '先填价格', '选付款方式', '没有汇率', ''])
  assert.deepEqual(['cash', 'e_transfer', 'card'].map(tenderName), ['现金', 'E-transfer', '刷卡'])
})

test('a sale reads like a 随手记 line', () => {
  assert.equal(itemsText([{ product_id: 7, name: 'SA Santa', quantity: 2, from_display: false },
    { product_id: 9, name: '小王子', quantity: 1, from_display: true }]), 'SA Santa*2，小王子*1（display）')
})

test('one key per save of the same cart; a new cart or a reset makes a new one', () => {
  let n = 0
  const keys = new SaveKey(() => `k${++n}`)
  assert.equal(keys.for('a'), 'k1')
  assert.equal(keys.for('a'), 'k1')
  assert.equal(keys.for('b'), 'k2')
  keys.reset()
  assert.equal(keys.for('b'), 'k3')
})
