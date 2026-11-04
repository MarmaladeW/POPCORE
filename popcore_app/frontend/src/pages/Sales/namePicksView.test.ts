import assert from 'node:assert/strict'
import test from 'node:test'

import type { PastedDay, PastedName } from '../../api/namePicks.ts'
import { bySection, dayCounts, firstDay, needsAnswer, placeText, quantityText, STATUS_TEXT, writtenName } from './namePicksView.ts'

const counts = (open: number, remembered = 0) => ({ open, disputed: 0, stale: 0, confirm: 0, remembered, ignored: 0 })
const day = (date: string, open: number, remembered = 0): PastedDay =>
  ({ store_code: 'DT', date, parsed_at: `${date} 23:00:00`, saved: false, counts: counts(open, remembered) })
const name = (raw_name: string, section: string, extra: Partial<PastedName> = {}): PastedName => ({
  name_key: raw_name.toLowerCase(), note_key: '', raw_name, note: '', section, display: false, qty: 1, box_size: null,
  loose_qty: 0, status: 'open', guess: null, pick: null, ...extra,
})

test('status labels line up: every one is three characters', () => {
  assert.deepEqual(new Set(Object.values(STATUS_TEXT).map(text => [...text.zh].length)), new Set([3]))
})

test('only names a next paste would ask about need an answer', () => {
  assert.deepEqual(Object.keys(STATUS_TEXT).filter(status => needsAnswer(status as keyof typeof STATUS_TEXT)),
    ['open', 'disputed', 'stale', 'confirm'])
  assert.deepEqual(dayCounts({ ...day('2026-11-02', 2, 9), counts: { ...counts(2, 9), confirm: 1, ignored: 1 } }),
    { toAnswer: 3, remembered: 9, ignored: 1 })
})

test('the page opens the newest paste that still has names to answer', () => {
  assert.equal(firstDay([day('2026-11-02', 0, 5), day('2026-11-01', 3), day('2026-10-31', 1)])?.date, '2026-11-01')
  assert.equal(firstDay([day('2026-11-02', 0, 5), day('2026-11-01', 0)])?.date, '2026-11-02')
  assert.equal(firstDay([]), undefined)
})

test('a name reads as written, with its note and its quantity form', () => {
  assert.equal(writtenName({ raw_name: 'SA kiss', note: '' }), 'SA kiss')
  assert.equal(writtenName({ raw_name: '小野哈利波特figure', note: '斯内普' }), '小野哈利波特figure（斯内普）')
  assert.equal(quantityText(name('smiski s1', 'pos', { qty: 2 })), '× 2')
  assert.equal(quantityText(name('SA dream', 'refill', { qty: 2, box_size: 12 })), '12 × 2')
  assert.equal(quantityText(name('芭比', 'stock_in', { qty: 1, box_size: 20, loose_qty: 19 })), '20 × 1 + 19')
  assert.equal(quantityText(name('星星人', 'stock_in', { qty: 3, box_size: 1 })), '× 3')
  assert.equal(placeText({ store_code: 'DT', section: 'refill', display: true }), 'DT 出库入店 · Display')
})

test("a day's names keep the report's own sections in paste order", () => {
  const groups = bySection([name('a', 'pos'), name('b', 'refill'), name('c', 'pos'), name('d', 'stock_out')])
  assert.deepEqual(groups.map(group => [group.section, group.names.map(entry => entry.raw_name)]),
    [['pos', ['a', 'c']], ['refill', ['b']], ['stock_out', ['d']]])
})
