import assert from 'node:assert/strict'
import test from 'node:test'
import { asksSets, asksSetSize, landing, moveLines, waitingFor, type MoveItem } from './moveStock.ts'

const item = (change: Partial<MoveItem> = {}): MoveItem => ({ productId: 7, name: 'SA Santa', sku: 'DR0200', unit: 'box',
  perSet: 12, canHaveSet: false, sets: '', units: '', setSize: '', ...change })

test('whole sets are asked only out of a back room or for new goods', () => {
  assert.equal(asksSets('upstairs', item()), true)
  assert.equal(asksSets('warehouse', item()), true)
  assert.equal(asksSets('floor', item()), false)
  assert.equal(asksSets('upstairs', item({ perSet: null })), false)
  assert.equal(asksSets('new', item({ perSet: null, canHaveSet: true })), true)
})

test('the lines sent keep only what has a quantity, and sets only where they are asked', () => {
  const items = [item({ sets: '1', units: '4' }), item({ productId: 9, perSet: null, units: '' }), item({ productId: 3, sets: '2' })]
  assert.deepEqual(moveLines(items, 'upstairs', 'floor'), [{ product_id: 7, sets: 1, units: 4 }, { product_id: 3, sets: 2, units: 0 }])
  // Typed sets do not ride along once the source is the floor.
  assert.deepEqual(moveLines(items, 'floor', 'display'), [{ product_id: 7, sets: 0, units: 4 }])
  assert.equal(landing(items[0], 'upstairs'), '1 端 = 12 盒')
  assert.equal(landing(items[0], 'floor'), '')
})

test('new goods give a blind box its first whole sets once their size is typed', () => {
  const fresh = item({ perSet: null, canHaveSet: true, sets: '3' })
  assert.equal(asksSetSize('new', 'upstairs', fresh), true)
  assert.equal(asksSetSize('new', 'floor', fresh), false)
  assert.equal(waitingFor('new', 'upstairs', '', '', [fresh]), '填每端几盒')
  assert.deepEqual(moveLines([{ ...fresh, setSize: '8' }], 'new', 'upstairs'), [{ product_id: 7, sets: 3, units: 0, set_size: 8 }])
})

test('save names the one thing still missing', () => {
  assert.equal(waitingFor('', '', '', '', []), '选从哪里')
  assert.equal(waitingFor('upstairs', '', '', '', []), '选到哪里')
  assert.equal(waitingFor('upstairs', 'floor', '', '', []), '加要移的货')
  assert.equal(waitingFor('upstairs', 'floor', '', '', [item()]), '填数量')
  assert.equal(waitingFor('floor', 'gone', '', '', [item({ units: '1' })]), '选原因')
  assert.equal(waitingFor('floor', 'gone', 'other', ' ', [item({ units: '1' })]), '写一下原因')
  assert.equal(waitingFor('floor', 'gone', 'broken', '', [item({ units: '1' })]), '')
  assert.equal(waitingFor('upstairs', 'floor', '', '', [item({ sets: '1' })]), '')
})
