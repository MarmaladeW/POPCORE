import assert from 'node:assert/strict'
import test from 'node:test'
import { activityTime, activityWho, shiftDay } from './activityView.ts'

test('a 动态 line names who did it and the store computer it was done on', () => {
  assert.equal(activityWho({ actor_name: 'Zimo', via_name: null }), 'Zimo')
  assert.equal(activityWho({ actor_name: 'Zimo', via_name: 'DT 前台' }), 'Zimo（DT 前台）')
  assert.equal(activityWho({ actor_name: null, via_name: null }), '没有员工资料 No staff profile')
  assert.equal(activityTime({ at: '2026-11-20 19:32:05' }), '14:32')
  assert.equal(shiftDay('2026-11-01', -1), '2026-10-31')
  assert.equal(shiftDay('2026-12-31', 1), '2027-01-01')
})
