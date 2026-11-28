import assert from 'node:assert/strict'
import test from 'node:test'
import { calendarRange, canGoBack, canGoForward, monthClosed, type ManagerWindow } from './managerWindow.ts'

const november: ManagerWindow = { first: '2026-11-01', last: '2026-11-30', months: ['2026-11'] }

test('a manager moves only inside this month', () => {
  assert.equal(canGoBack(november, '2026-11-01'), false)
  assert.equal(canGoBack(november, '2026-11-02'), true)   // a week or fortnight starting after the 1st
  assert.equal(canGoForward(november, '2026-11-30'), true)
  assert.equal(canGoForward(november, '2026-12-01'), false)
  assert.equal(monthClosed(november, '2026-10'), true)
  assert.equal(monthClosed(november, '2026-11'), false)
  assert.equal(monthClosed(november, '2026-12'), true)
  assert.deepEqual(calendarRange(november), { start: '2026-11-01', end: '2026-12-01' })
})

test('an admin moves anywhere', () => {
  assert.equal(canGoBack(null, '2020-01-01'), true)
  assert.equal(canGoForward(null, '2030-01-01'), true)
  assert.equal(monthClosed(null, '2020-01'), false)
  assert.equal(calendarRange(null), undefined)
})
