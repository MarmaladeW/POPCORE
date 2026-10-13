import assert from 'node:assert/strict'
import test from 'node:test'
import { DEFAULT_STORE_HOURS, presetTimes, shiftSpan } from './openHours.ts'

const DT = DEFAULT_STORE_HOURS.DT

test('full and half day hours follow the date: weekend halves split at 16:30', () => {
  assert.deepEqual(presetTimes('2026-10-23', DT), {
    full: { start: '12:00', end: '22:00' }, first: { start: '12:00', end: '17:00' }, second: { start: '17:00', end: '22:00' },
  })
  assert.deepEqual(presetTimes('2026-10-25', DT), {
    full: { start: '11:00', end: '22:00' }, first: { start: '11:00', end: '16:30' }, second: { start: '16:30', end: '22:00' },
  })
})

test('calendar bars span the cell for a full day and one half for a half day', () => {
  assert.deepEqual(shiftSpan('2026-10-23', DT, '12:00', '22:00'), { left: 0, width: 1 })
  assert.deepEqual(shiftSpan('2026-10-23', DT, '12:00', '17:00'), { left: 0, width: 0.5 })
  assert.deepEqual(shiftSpan('2026-10-25', DT, '16:30', '22:00'), { left: 0.5, width: 0.5 })
  // Short or out-of-hours shifts stay readable and inside the day.
  assert.deepEqual(shiftSpan('2026-10-23', DT, '20:00', '23:00'), { left: 0.6, width: 0.4 })
  assert.deepEqual(shiftSpan('2026-10-23', DT, '09:00', '13:00'), { left: 0, width: 0.4 })
})
