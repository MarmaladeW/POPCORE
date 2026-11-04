import assert from 'node:assert/strict'
import test from 'node:test'

import {
  countedRange, formatHours, isRegularOn, regularSplit, shortDay, sumHours, weeksOfPeriod, type DetailShift,
} from './monthlyDetail.ts'

const shift = (date: string, hours: number, extra: Partial<DetailShift> = {}): DetailShift => ({
  id: Number(date.replaceAll('-', '')), date, store_code: 'DT', start_time: '12:00', end_time: '20:00',
  actual_end_time: null, position: '', hours, ...extra,
})

test('shifts from the 转正 date on are marked', () => {
  assert.equal(isRegularOn(null, '2026-10-15'), false)
  assert.equal(isRegularOn('2026-10-15', '2026-10-14'), false)
  assert.equal(isRegularOn('2026-10-15', '2026-10-15'), true)
  assert.equal(isRegularOn('2026-10-15', '2026-11-02'), true)
})

test('weeks run Monday to Sunday, cut to the wage period, and keep empty weeks', () => {
  // Oct 4 2026 is a Sunday; the period ends Tuesday Nov 3.
  const weeks = weeksOfPeriod('2026-10-04', '2026-11-03', [
    shift('2026-10-04', 8), shift('2026-10-06', 8.33), shift('2026-10-11', 4), shift('2026-11-03', 6.5),
  ])
  assert.deepEqual(weeks.map(week => [week.start, week.end, week.shifts.length, week.hours]), [
    ['2026-10-04', '2026-10-04', 1, 8],
    ['2026-10-05', '2026-10-11', 2, 12.33],
    ['2026-10-12', '2026-10-18', 0, 0],
    ['2026-10-19', '2026-10-25', 0, 0],
    ['2026-10-26', '2026-11-01', 0, 0],
    ['2026-11-02', '2026-11-03', 1, 6.5],
  ])
})

test('hours add up as shown and print without trailing zeros', () => {
  assert.equal(sumHours([8.33, 8.33, 8.34]), 25)
  assert.equal(sumHours([0.1, 0.2]), 0.3)
  assert.equal(formatHours(8), '8')
  assert.equal(formatHours(7.5), '7.5')
  assert.equal(formatHours(8.333), '8.33')
})

test('a 转正 date inside the period splits the hours', () => {
  const shifts = [shift('2026-10-05', 8), shift('2026-10-14', 4.5), shift('2026-10-15', 6), shift('2026-10-20', 2.25)]
  assert.deepEqual(regularSplit('2026-10-15', '2026-10-04', '2026-11-03', shifts), { before: 12.5, after: 8.25 })
  // Before the period, on its first day or after it: nothing to split.
  assert.equal(regularSplit('2026-09-01', '2026-10-04', '2026-11-03', shifts), null)
  assert.equal(regularSplit('2026-10-04', '2026-10-04', '2026-11-03', shifts), null)
  assert.equal(regularSplit('2026-11-04', '2026-10-04', '2026-11-03', shifts), null)
  assert.equal(regularSplit(null, '2026-10-04', '2026-11-03', shifts), null)
})

test('rows show the weekday and the time the hours count to', () => {
  assert.equal(shortDay('2026-10-05'), 'Mon 10/05')
  assert.equal(shortDay('2026-10-04'), 'Sun 10/04')
  assert.equal(countedRange({ start_time: '11:00', end_time: '19:00', actual_end_time: null }), '11:00–19:00')
  assert.equal(countedRange({ start_time: '11:00', end_time: '19:00', actual_end_time: '19:20' }), '11:00–19:20')
  assert.equal(countedRange({ start_time: '12:00', end_time: '22:00', actual_end_time: '00:30' }), '12:00–00:30 +1')
})
