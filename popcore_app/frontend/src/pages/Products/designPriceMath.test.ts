import assert from 'node:assert/strict'
import test from 'node:test'

import { adjust, parseMoney, percent, placePaste, readPaste } from './designPriceMath.ts'

test('prices are read as typed or copied from a spreadsheet', () => {
  for (const [text, cents] of [['45.99', 4599], [' $45.99 ', 4599], ['CA$1,299', 129900], ['45', 4500], ['45.5', 4550],
    ['.99', 99], ['1,299.00', 129900]] as const) assert.equal(parseMoney(text), cents, text)
  for (const text of ['', '0', '0.00', '-5', 'free', '45.999', '1,29', '12abc', '1000000.01']) assert.equal(parseMoney(text), null, text)
})

test('a selection is raised or lowered by a percentage or an amount, to the cent', () => {
  assert.equal(adjust(4599, 10, '%'), 5059)
  assert.equal(adjust(4599, -25, '%'), 3449)
  assert.equal(adjust(4599, 5, '$'), 5099)
  assert.equal(adjust(4599, -45.99, '$'), null) // never $0 or less
  assert.equal(adjust(4599, -100, '%'), null)
  assert.equal(percent(10999, 15000), 36)
})

test('a pasted column fills the shown designs downward, a blank row leaving one as it is', () => {
  const order = [{ id: 1, sku: 'PM0100-01' }, { id: 2, sku: 'PM0100-02' }, { id: 3, sku: 'PM0100-03' }]
  const ids = new Set(order.map(row => row.sku))
  assert.deepEqual(placePaste(readPaste('45.99\n\n$60\n', ids), order, 0), { placed: { 1: '45.99', 3: '$60' }, missed: 0, byId: false })
  assert.deepEqual(placePaste(readPaste('12\r\n13\r\n14', ids), order, 1), { placed: { 2: '12', 3: '13' }, missed: 1, byId: false })
})

test('pasted rows that name a POPCORE ID go to that design, in any column order', () => {
  const order = [{ id: 1, sku: 'PM0100-01' }, { id: 2, sku: 'PM0100-02' }]
  const ids = new Set([...order.map(row => row.sku), 'PM0999-01'])
  const lines = readPaste('pm0100-02\tMoon\t$31.50\n45.99\tPM0100-01\nPM0999-01\t9.99\nPM0100-01\t', ids)
  assert.deepEqual(lines, [{ id: 'PM0100-02', text: '$31.50' }, { id: 'PM0100-01', text: '45.99' },
    { id: 'PM0999-01', text: '9.99' }, { id: 'PM0100-01', text: '' }])
  // PM0999-01 is not shown (filtered out), so it is counted as missed rather than set.
  assert.deepEqual(placePaste(lines, order, 1), { placed: { 2: '$31.50', 1: '45.99' }, missed: 1, byId: true })
})
