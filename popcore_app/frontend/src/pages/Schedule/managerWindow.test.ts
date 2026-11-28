import assert from 'node:assert/strict'
import test from 'node:test'
import { calendarRange, canGoBack, canGoForward, monthClosed, monthLimitHint, type ManagerWindow } from './managerWindow.ts'

const november: ManagerWindow = { first: '2026-11-04', last: '2026-12-03', months: ['2026-11'] }

test('a manager moves only inside this wage month', () => {
  assert.equal(canGoBack(november, '2026-11-01'), false)  // the November month grid
  assert.equal(canGoBack(november, '2026-11-05'), true)   // a week or fortnight starting after the 4th
  assert.equal(canGoForward(november, '2026-12-01'), true) // December's grid still holds Dec 1–3
  assert.equal(canGoForward(november, '2026-12-03'), true)
  assert.equal(canGoForward(november, '2026-12-04'), false)
  assert.equal(monthClosed(november, '2026-10'), true)
  assert.equal(monthClosed(november, '2026-11'), false)
  assert.equal(monthClosed(november, '2026-12'), true)
  assert.deepEqual(calendarRange(november), { start: '2026-11-04', end: '2026-12-04' })
  assert.equal(monthLimitHint(november), '店长只能看 2026-11-04 到 2026-12-03 的排班 · Managers see 2026-11-04 – 2026-12-03 only')
})

test('an admin moves anywhere', () => {
  assert.equal(canGoBack(null, '2020-01-01'), true)
  assert.equal(canGoForward(null, '2030-01-01'), true)
  assert.equal(monthClosed(null, '2020-01'), false)
  assert.equal(calendarRange(null), undefined)
  assert.equal(monthLimitHint(null), undefined)
})
