import assert from 'node:assert/strict'
import test from 'node:test'
import { EXTRA_FIELDS, extrasChange, fromInput, kindText, toInput } from './dexFields.ts'

const field = (kind: keyof typeof EXTRA_FIELDS, key: string) => EXTRA_FIELDS[kind].find(entry => entry.key === key)!

test('the editor never offers market prices, and every kind has its contract keys', () => {
  assert.deepEqual(EXTRA_FIELDS.design.map(entry => entry.key), ['weight', 'weight_range', 'shake_level', 'shake_note', 'flavour_text'])
  assert.deepEqual(EXTRA_FIELDS.company, [])
  assert.equal(EXTRA_FIELDS.product.length, 9)
})

test('lists split on commas, blanks are null, numbers stay numbers', () => {
  assert.deepEqual(fromInput(field('ip', 'aliases'), ' 拉布布, Labubu ，'), ['拉布布', 'Labubu'])
  assert.equal(fromInput(field('ip', 'aliases'), ' , '), null)
  assert.equal(fromInput(field('ip', 'description'), '   '), null)
  assert.equal(fromInput(field('design', 'weight'), 112), 112)
  assert.equal(fromInput(field('design', 'weight'), ''), null)
  assert.equal(toInput(field('ip', 'aliases'), ['a', 'b']), 'a, b')
  assert.equal(toInput(field('ip', 'is_original'), null), undefined)
})

test('only changed keys are sent, and a cleared one is sent as null', () => {
  const before = { weight: 112, shake_note: 'rattles', market_prices: [{ price: 1 }] }
  assert.deepEqual(extrasChange('design', before, { weight: 112, shake_note: 'rattles', weight_range: '' }), {})
  assert.deepEqual(extrasChange('design', before, { weight: 113, shake_note: '' }), { weight: 113, shake_note: null })
  assert.deepEqual(extrasChange('ip', {}, { aliases: 'x, y', is_original: true }), { aliases: ['x', 'y'], is_original: true })
})

test('levels read Chinese first, then English, without saying IP twice', () => {
  assert.equal(kindText('design'), '款 Design')
  assert.equal(kindText('ip'), 'IP')
})
