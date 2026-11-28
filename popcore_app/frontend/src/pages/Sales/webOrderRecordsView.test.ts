import assert from 'node:assert/strict'
import { test } from 'node:test'
import { byDay, countText, kindText, monthTitle, shiftMonth, statusText, takenText, type WebOrderRecord } from './webOrderRecordsView.ts'

const order = (number: string, day: string, extra: Partial<WebOrderRecord> = {}): WebOrderRecord => ({
  id: Number(number), number, paid_at: `${day}T18:00:00`, day, kind: 'pickup', stores: ['DT'], taken_from: ['DT'],
  status: 'open', waiting: false, settled: null, total_cents: 4520, tax_cents: 520, payment: 'Card', counted: true,
  pieces: 1, lines: [], closed_at: null, ...extra,
})

test('an open order reads as the board does: a pickup to pick, a delivery to pack', () => {
  assert.equal(statusText(order('1', '2026-11-27')), '待拣货')
  assert.equal(statusText(order('2', '2026-11-27', { kind: 'delivery' })), '待打包')
  assert.deepEqual((['packed', 'ready', 'out', 'done', 'cancelled', 'refunded'] as const).map(status => statusText({ status, kind: 'delivery' })),
    ['已打包', '待取货', '派送中', '已完成', '已取消', '已退款'])
})

test('kind and store, and where the stock came from only when it was elsewhere', () => {
  assert.equal(kindText(order('1', '2026-11-27')), '自取 DT')
  assert.equal(kindText(order('2', '2026-11-27', { kind: 'delivery', stores: ['DT', 'MK'] })), '配送 DT + MK')
  assert.equal(takenText(order('1', '2026-11-27')), '')
  assert.equal(takenText(order('3', '2026-11-27', { taken_from: ['DT', 'MK'] })), '货从 MK')
})

test('counts name only the kinds there were, and the cancelled ones apart', () => {
  assert.equal(countText({ orders: 3, cents: 1, pieces: 4, not_counted: 0, pickup: 3, delivery: 0 }), '3 单 · 自取 3')
  assert.equal(countText({ orders: 2, cents: 1, pieces: 2, not_counted: 1, pickup: 1, delivery: 1 }), '2 单 · 自取 1 · 配送 1 · 1 单取消不计')
  assert.equal(countText({ orders: 0, cents: 0, pieces: 0, not_counted: 0, pickup: 0, delivery: 0 }), '0 单')
})

test('days come newest first with their own totals, cancelled orders listed but not added', () => {
  const days = byDay([order('5', '2026-11-27'), order('4', '2026-11-27', { status: 'cancelled', counted: false }),
    order('3', '2026-11-25', { total_cents: 1000 })])
  assert.deepEqual(days.map(day => [day.day, day.orders.length, day.count, day.cents]),
    [['2026-11-27', 2, 1, 4520], ['2026-11-25', 1, 1, 1000]])
})

test('months step across the year', () => {
  assert.equal(monthTitle('2026-11'), '2026年11月')
  assert.equal(shiftMonth('2026-12', 1), '2027-01')
  assert.equal(shiftMonth('2026-01', -1), '2025-12')
})
