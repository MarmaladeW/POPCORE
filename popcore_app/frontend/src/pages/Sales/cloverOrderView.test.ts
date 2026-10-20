import assert from 'node:assert/strict'
import { test } from 'node:test'
import { autoOpenCandidate, cardPart, centsOrNull, groupLines, splitInstruction, stateTone, writebackStatus, type CloverBlock, type CloverLine } from './cloverOrderView.ts'

test('identical Clover units show once with their count; weighed lines stay separate', () => {
  const line = (name: string, price: number, quantity = 1): CloverLine => ({
    name, quantity, unit: 'each', unit_price_cents: price, item_code: null, product_id: null, product_name: null, mapped: false })
  const grouped = groupLines([line('Smiski', 1770), line('Labubu', 1999), line('Smiski', 1770), line('Smiski', 1500),
    line('Candy', 250, 0.5), line('Candy', 250, 0.5)])
  assert.deepEqual(grouped.map(g => [g.name, g.unit_price_cents, g.quantity]),
    [['Smiski', 1770, 2], ['Labubu', 1999, 1], ['Smiski', 1500, 1], ['Candy', 250, 0.5], ['Candy', 250, 0.5]])
  assert.equal(new Set(grouped.map(g => g.key)).size, grouped.length)
})

const block = (overrides: Partial<CloverBlock> = {}) => ({
  register: true, can_claim: true, popcore_reference: 'POPCORE-7', popcore_cents: 4200,
  writeback: { state: 'done', error: null }, ...overrides,
}) as CloverBlock

test('the split instruction names the exact steps and amount on the device', () => {
  assert.equal(splitInstruction(2200), 'Pay → ⋮ → Split Custom Amount → $22.00 → Card')
})

test('typed amounts are checked without throwing', () => {
  assert.equal(centsOrNull('22'), 2200)
  assert.equal(centsOrNull('22.5'), 2250)
  assert.equal(centsOrNull(''), null)
  assert.equal(centsOrNull('abc'), null)
  assert.equal(centsOrNull('0'), null)
  assert.equal(centsOrNull('1.234'), null)
  assert.deepEqual(cardPart('22.00', 4200), { cents: 2200, error: '' })
  assert.equal(cardPart('42.00', 4200).cents, null)
  assert.match(cardPart('42.00', 4200).error, /less than \$42\.00/)
  assert.deepEqual(cardPart('', 4200), { cents: null, error: '' })
})

test('Clover states map to one of five chip tones', () => {
  assert.deepEqual(['OPEN', 'PARTIALLY_PAID', 'PAID', 'DELETED', 'REFUNDED', 'DEVICE'].map(stateTone),
    ['open', 'partial', 'paid', 'deleted', 'refunded', 'open'])
})

test('the Clover side of a finished sale is always stated', () => {
  assert.equal(writebackStatus(block(), 'open'), null)
  assert.equal(writebackStatus(block(), 'completed')?.title, 'Paid on Clover')
  assert.equal(writebackStatus(block({ writeback: { state: 'failed', error: 'Clover is limiting requests.' } }), 'completed')?.tone, 'warning')
  assert.match(writebackStatus(block({ writeback: { state: 'failed', error: null } }), 'completed')!.detail, /cannot close/)
  assert.equal(writebackStatus(block({ writeback: { state: 'none', error: null } }), 'completed')?.title, 'Clover not updated')
  assert.equal(writebackStatus(block({ writeback: { state: 'none', error: null }, popcore_cents: 0 }), 'completed')?.title, 'Paid on Clover')
})

test('in label mode the open Clover order is described as labelled, not paid', () => {
  const labelled = (state: CloverBlock['writeback']['state'], popcore = 4200) =>
    writebackStatus(block({ writeback: { state, error: null, mode: 'label', label: 'Paid 2026-10-20 11:23' }, popcore_cents: popcore }), 'completed')
  assert.deepEqual([labelled('done')?.title, labelled('pending')?.title, labelled('failed')?.title],
    ['Labelled paid on Clover', 'Labelling the Clover order…', 'Not labelled on Clover yet'])
  assert.match(labelled('done')!.detail, /“Paid 2026-10-20 11:23” and stays open/)
  assert.equal(labelled('none', 0)?.title, 'Paid on Clover')
  assert.equal(labelled('none')?.title, 'Clover not labelled')
})

test('only an idle, visible screen picks up the oldest newly scanned register order', () => {
  const orders = [
    { id: 9, status: 'open', clover: block() },
    { id: 8, status: 'open', clover: block() },
    { id: 7, status: 'open', clover: block() },
    { id: 6, status: 'open', clover: block({ register: false, can_claim: false }) },
    { id: 5, status: 'completed', clover: block() },
    { id: 4, status: 'open', clover: null },
  ]
  const known = new Set([7])
  assert.equal(autoOpenCandidate({ enabled: true, idle: true, visible: true, known, orders }), 8)
  assert.equal(autoOpenCandidate({ enabled: false, idle: true, visible: true, known, orders }), null)
  assert.equal(autoOpenCandidate({ enabled: true, idle: false, visible: true, known, orders }), null)
  assert.equal(autoOpenCandidate({ enabled: true, idle: true, visible: false, known, orders }), null)
  assert.equal(autoOpenCandidate({ enabled: true, idle: true, visible: true, known: new Set([7, 8, 9]), orders }), null)
})
