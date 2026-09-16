import assert from 'node:assert/strict'
import { test } from 'node:test'
import { cashDifference, hasReportNotes, isReportRowReady, matchFeedback, REPORT_NOTE_SECTIONS } from './dailyReportReview.ts'

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
