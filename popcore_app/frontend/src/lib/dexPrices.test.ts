import assert from 'node:assert/strict'
import test from 'node:test'

import { timeOf, torontoDay, trend } from './dexPrices.ts'

test('stored UTC times are read as UTC and shown as the Toronto date', () => {
  assert.equal(timeOf('2026-11-13 03:30:00'), Date.UTC(2026, 10, 13, 3, 30))
  assert.equal(torontoDay('2026-11-13 03:30:00'), torontoDay('2026-11-12 12:00:00'))  // 22:30 the evening before
  assert.equal(torontoDay('2026-11-02'), torontoDay('2026-11-02 17:00:00'))
  assert.ok(Number.isNaN(timeOf('soon')))
  assert.equal(torontoDay('soon'), 'soon')
})

test('a trend needs two set prices and runs oldest to newest, high prices at the top', () => {
  assert.equal(trend([{ at: '2026-11-10 15:00:00', cents: 2500 }], [], 300, 80), null)
  const drawn = trend([
    { at: '2026-11-13 18:00:00', cents: 3200 },
    { at: '2026-11-10 15:00:00', cents: 2500 },
    { at: '2026-11-12 03:30:00', cents: 2800 },
  ], [], 300, 80, 6)!
  assert.deepEqual(drawn.dots.map(d => d.cents), [2500, 2800, 3200])
  assert.equal(drawn.dots[0].x, 6)
  assert.equal(drawn.dots[2].x, 294)
  assert.equal(drawn.dots[0].y, 74)
  assert.equal(drawn.dots[2].y, 6)
  assert.ok(drawn.line.startsWith('M6 74 L'))
  assert.deepEqual([drawn.low, drawn.high], [2500, 3200])
})

test("the website's prices share the scale but not the line", () => {
  const drawn = trend([{ at: '2026-11-10 15:00:00', cents: 2500 }, { at: '2026-11-13 18:00:00', cents: 3200 }],
    [{ at: '2026-11-01', cents: 4000 }, { at: 'unknown', cents: 1 }], 300, 80, 6)!
  assert.equal(drawn.website.length, 1)
  assert.deepEqual([drawn.website[0].x, drawn.website[0].y], [6, 6])
  assert.equal(drawn.line.split('L').length, 2)
  // Same price every time: a flat line in the middle.
  const flat = trend([{ at: '2026-11-10', cents: 2500 }, { at: '2026-11-11', cents: 2500 }], [], 100, 40)!
  assert.deepEqual(flat.dots.map(d => d.y), [20, 20])
})
