import assert from 'node:assert/strict'
import test from 'node:test'

import { boxAnswerFields, boxAnswerReady, boxChoiceNote, needsBoxAnswer, openSetLabel, type BoxOptions } from './boxChoice.ts'

const options: BoxOptions = {
  set_size: 12,
  fresh_set: { product_id: 8, conversion_id: 3, conversion_factor: 12, name: 'Series Set', sets_on_hand: 2 },
  open_sets: [{ id: 41, remaining_qty: 5 }, { id: 42, remaining_qty: 11 }],
  loose_units: 4,
}

test('more than half a set of a blind box with a sealed set needs an answer', () => {
  assert.equal(needsBoxAnswer(options, 7), true)
  assert.equal(needsBoxAnswer(options, 6), false)
  assert.equal(needsBoxAnswer({ ...options, set_size: 9 }, 5), true)
  assert.equal(needsBoxAnswer(null, 12), false)
  assert.equal(needsBoxAnswer(options, Number.NaN), false)
})

test('each answer becomes exactly one sale line field', () => {
  assert.deepEqual(boxAnswerFields({ choice: 'loose' }, options), { open_set_id: null, fresh_set: null, loose_boxes: true })
  assert.deepEqual(boxAnswerFields({ choice: 'open_set', openSetId: 42 }, options), { open_set_id: 42, fresh_set: null, loose_boxes: false })
  assert.deepEqual(boxAnswerFields({ choice: 'fresh_set' }, options),
    { open_set_id: null, fresh_set: { product_id: 8, conversion_id: 3, conversion_factor: 12 }, loose_boxes: false })
})

test('an answer is ready only with something to take from', () => {
  assert.equal(boxAnswerReady(undefined, options), false)
  assert.equal(boxAnswerReady({ choice: 'open_set' }, options), false)
  assert.equal(boxAnswerReady({ choice: 'open_set', openSetId: 99 }, options), false)
  assert.equal(boxAnswerReady({ choice: 'open_set', openSetId: 41 }, options), true)
  assert.equal(boxAnswerReady({ choice: 'fresh_set' }, { ...options, fresh_set: null }), false)
  assert.equal(boxAnswerReady({ choice: 'loose' }, { ...options, loose_units: 0 }), true) // short stock waits on the sale page
  assert.equal(boxChoiceNote('open_set', options), '16 left in 2 opened')
  assert.equal(boxChoiceNote('fresh_set', { ...options, fresh_set: null }), 'No sealed set')
  assert.equal(boxChoiceNote('loose', options), '4 loose')
})

test('without 库存查看 the answers say only in or out of stock', () => {
  const hidden: BoxOptions = { set_size: 12, stock_hidden: true, loose_units: null, loose_out_of_stock: true,
    fresh_set: { ...options.fresh_set!, sets_on_hand: null, out_of_stock: false },
    open_sets: options.open_sets.map(set => ({ ...set, remaining_qty: null })) }
  assert.equal(boxChoiceNote('fresh_set', hidden), '有货 In stock')
  assert.equal(boxChoiceNote('fresh_set', { ...hidden, fresh_set: { ...hidden.fresh_set!, out_of_stock: true } }), '缺货 Out of stock')
  assert.equal(boxChoiceNote('open_set', hidden), '2 opened')
  assert.equal(boxChoiceNote('loose', hidden), '缺货 Out of stock')
  assert.equal(openSetLabel(hidden.open_sets[0]), 'Opened set #41')
  assert.equal(openSetLabel(options.open_sets[0]), `Opened set #41 · ${options.open_sets[0].remaining_qty} left`)
})
