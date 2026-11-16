import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  cashDifference, formatCheckWhere, hasReportNotes, isReportRowReady, matchFeedback, PACKED_SECTIONS, REPORT_NOTE_SECTIONS,
  reportPayloadQuantities, STOCK_LINE_SECTIONS, stockLineEffect, applyRepeatChoice, dropRepeats,
} from './dailyReportReview.ts'
import { whereText } from '../../lib/stockPlaces.ts'
import { quantityText, SECTION_TEXT } from './namePicksView.ts'

test('report review never silently submits unresolved rows or invents packs and match scores', () => {
  const row = { section: 'cash', qty: 2, box_size: null, flagged: false, product: { id: 1 } }
  assert.equal(isReportRowReady(row), true)
  for (const qty of [0, -1, 1.5, NaN]) assert.equal(isReportRowReady({ ...row, qty }), false)
  assert.equal(isReportRowReady({ ...row, flagged: true }), false)
  assert.equal(isReportRowReady({ ...row, accepted: false }), false)
  assert.equal(isReportRowReady({ ...row, product: undefined }), false)
  assert.equal(isReportRowReady({ ...row, unknown_header: 'other' }), false)
  for (const section of ['unknown', 'employee_discount', 'claw', 'sell_display']) {
    assert.equal(isReportRowReady({ ...row, section }), false)
  }
  assert.equal(isReportRowReady({ ...row, section: 'stock_in' }), false)
  assert.equal(isReportRowReady({ ...row, section: 'stock_in', box_size: 1.5 }), false)
  assert.equal(isReportRowReady({ ...row, section: 'stock_in', box_size: 6 }), true)
  const candidates = [{ id: 1, score: 78 }, { id: 2, score: 61 }]
  assert.deepEqual(matchFeedback(candidates, 2, 1, 78), { fuzzy_score: 61, top_score: 78, was_top: false })
  assert.deepEqual(matchFeedback(candidates, 1, 1, 78), { fuzzy_score: 78, top_score: 78, was_top: true })
  assert.deepEqual(matchFeedback(candidates, 9, 1, 78), { fuzzy_score: 0, top_score: 78, was_top: false })
  assert.equal(cashDifference(595, 601.5), -6.5)
  assert.equal(cashDifference(595, null), null)
  assert.equal(cashDifference(0, 0), 0)
})


test('receipt loose units are explicit non-negative integers and pack totals stay exact', () => {
  const row = { section: 'stock_in', qty: 2, box_size: 6, flagged: false, product: { id: 1 } }
  assert.equal(isReportRowReady(row), true)
  assert.equal(isReportRowReady({ ...row, loose_qty: 3 }), true)
  for (const loose_qty of [-1, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER]) {
    assert.equal(isReportRowReady({ ...row, loose_qty }), false)
  }
  assert.equal(isReportRowReady({ ...row, qty: 1, box_size: 1, loose_qty: 0 }), true)
  assert.equal(isReportRowReady({ ...row, box_size: null, loose_qty: 3 }), false)
  assert.equal(isReportRowReady({ ...row, qty: 0, loose_qty: 3 }), false)
})

test('each literal annotation can form a notes-only report without inferring money', () => {
  assert.equal(hasReportNotes({}), false)
  for (const { key } of REPORT_NOTE_SECTIONS) {
    assert.equal(hasReportNotes({ [key]: [] }), false)
    assert.equal(hasReportNotes({ [key]: ['原文*2 (17:48)'] }), true)
  }
  assert.equal(cashDifference(null, null), null)
})

test('a live store reviews its stock lines and sends what each one moves', () => {
  const row = { section: 'refill', qty: 2, box_size: 12, loose_qty: 0, flagged: false, product: { id: 1 } }
  // 出库入店 and the other stock lines are only for a store that is live; a legacy store keeps its old sections.
  assert.equal(isReportRowReady(row), false)
  assert.equal(isReportRowReady(row, true), true)
  assert.equal(isReportRowReady({ ...row, box_size: null }, true), false)
  for (const section of STOCK_LINE_SECTIONS) {
    assert.equal(isReportRowReady({ ...row, section, box_size: PACKED_SECTIONS.includes(section) ? 6 : null }, true), true)
    assert.ok(stockLineEffect({ section }))
    assert.ok(SECTION_TEXT[section])
  }
  assert.equal(stockLineEffect({ section: 'pos' }), null)
  assert.equal(stockLineEffect({ section: 'refill', to_display: true }), '楼上/仓库 → Display')
  assert.equal(stockLineEffect({ section: 'stock_out', from_display: true }), 'Display → 出店 Out')
  assert.deepEqual(reportPayloadQuantities({ ...row, loose_qty: 3 }), { box_size: 12, num_boxes: 2, loose_qty: 3, to_display: false })
  assert.deepEqual(reportPayloadQuantities({ section: 'display_sold', qty: 2 }), { qty: 2 })
  assert.deepEqual(reportPayloadQuantities({ section: 'stock_out', qty: 1, from_display: true }), { qty: 1, from_display: true })
  assert.deepEqual(reportPayloadQuantities({ section: 'cash', qty: 4 }), { qty_cash: 4 })
  assert.deepEqual(reportPayloadQuantities({ section: 'pos', qty: 4 }), { qty_pos: 4 })
  // 出库 stays a note that points to 调拨 Transfer only while the other store is not live.
  assert.ok(REPORT_NOTE_SECTIONS.some(({ key, description }) => key === 'store_transfers' && description.includes('调拨')))
})

test('入库, 出库, 出店入库 and 出display入店 are stock lines written as sets, boxes or pieces', () => {
  // Owner, 2026-11-13: 入库 is new goods into upstairs; 出库 comes off this store and goes onto the other at once.
  const row = { section: 'back_in', qty: 8, box_size: 12, loose_qty: 0, flagged: false, product: { id: 1 } }
  assert.equal(isReportRowReady(row), false)
  assert.equal(isReportRowReady(row, true), true)
  assert.equal(isReportRowReady({ ...row, box_size: null }, true), false)
  assert.deepEqual(reportPayloadQuantities(row), { box_size: 12, num_boxes: 8, loose_qty: 0 })
  assert.equal(stockLineEffect(row), '新货 New goods → 楼上/仓库')
  // 出库 lands in the other store's upstairs or warehouse; its floor only when the manager ticks it.
  const sent = { section: 'transfer_out', qty: 30, box_size: 1, loose_qty: 0 }
  assert.deepEqual(reportPayloadQuantities(sent), { box_size: 1, num_boxes: 30, loose_qty: 0, to_floor: false })
  assert.deepEqual(reportPayloadQuantities({ ...sent, to_floor: true }), { box_size: 1, num_boxes: 30, loose_qty: 0, to_floor: true })
  assert.equal(stockLineEffect(sent), '楼上/仓库 → 另一家店楼上/仓库 Other store')
  assert.equal(stockLineEffect({ ...sent, to_floor: true }), '楼上/仓库 → 另一家店楼下 Other store floor')
  assert.equal(stockLineEffect({ section: 'floor_to_back' }), '楼下 Floor → 楼上/仓库')
  assert.equal(stockLineEffect({ section: 'display_to_floor' }), 'Display → 楼下 Floor')
  assert.deepEqual(reportPayloadQuantities({ section: 'display_to_floor', qty: 1, box_size: 12 }), { box_size: 12, num_boxes: 1, loose_qty: 0 })
  // The other store's side of a line is named with its store; a notice names it "MK:warehouse".
  assert.equal(whereText('warehouse', 'MK'), 'MK 仓库 Warehouse')
  assert.equal(whereText('MK:warehouse'), 'MK 仓库 Warehouse')
  assert.equal(whereText('upstairs'), '楼上 Upstairs')
  assert.equal(whereText('upstairs', null), '楼上 Upstairs')
  assert.equal(quantityText({ section: 'transfer_out', qty: 3, box_size: 9, loose_qty: 0 }), '9 × 3')
  assert.equal(quantityText({ section: 'back_in', qty: 2, box_size: 1, loose_qty: 0 }), '× 2')
})

test('a format check names the lines of the paste it points at, and its section', () => {
  assert.equal(formatCheckWhere({ lines: [82, 86], section: '出库入店' }), '第 82、86 行 Line 82, 86 · 出库入店')
  assert.equal(formatCheckWhere({ lines: [1], section: null }), '第 1 行 Line 1')
  assert.equal(formatCheckWhere({ lines: [], section: null }), '整份日报 Whole report')
})

test('a repeated stock block is left out until the manager counts both', () => {
  const rows: { raw_name: string; line: number; repeat_id?: string; removed?: boolean }[] = [
    { raw_name: 'a', line: 3 }, { raw_name: 'a', line: 8, repeat_id: 'block:8-9' },
    { raw_name: 'b', line: 9, repeat_id: 'block:8-9' }, { raw_name: 'c', line: 12, repeat_id: 'section:11' }]
  const first = dropRepeats(rows)
  assert.deepEqual(first.map(row => !!row.removed), [false, true, true, true])
  const both = applyRepeatChoice(first, 'block:8-9', 'both')
  assert.deepEqual(both.map(row => !!row.removed), [false, false, false, true])
  assert.deepEqual(applyRepeatChoice(both, 'block:8-9', 'drop').map(row => !!row.removed), [false, true, true, true])
})
