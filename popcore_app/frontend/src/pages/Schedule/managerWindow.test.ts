import assert from 'node:assert/strict'
import test from 'node:test'
import { calendarRange, canGoBack, canGoForward, monthLimitHint, type ManagerWindow } from './managerWindow.ts'

const twoWeeks: ManagerWindow = { first: '2026-11-28', last: '2026-12-11' }

test('a manager moves only inside the next two weeks', () => {
  assert.equal(canGoBack(twoWeeks, '2026-11-23'), false)  // the fortnight holding today
  assert.equal(canGoBack(twoWeeks, '2026-12-07'), true)   // the next one, with today before it
  assert.equal(canGoForward(twoWeeks, '2026-12-07'), true) // Dec 7–11 are still ahead
  assert.equal(canGoForward(twoWeeks, '2026-12-11'), true)
  assert.equal(canGoForward(twoWeeks, '2026-12-12'), false)
  assert.deepEqual(calendarRange(twoWeeks), { start: '2026-11-28', end: '2026-12-12' })
  assert.equal(monthLimitHint(twoWeeks), '店长只能看 2026-11-28 到 2026-12-11 的排班 · Managers see 2026-11-28 – 2026-12-11 only')
})

test('an admin moves anywhere', () => {
  assert.equal(canGoBack(null, '2020-01-01'), true)
  assert.equal(canGoForward(null, '2030-01-01'), true)
  assert.equal(calendarRange(null), undefined)
  assert.equal(monthLimitHint(null), undefined)
})
