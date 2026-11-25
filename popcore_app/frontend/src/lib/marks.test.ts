import assert from 'node:assert/strict'
import test from 'node:test'
import { markText, torontoStamp } from './marks.ts'

test('a mark says who and when in Toronto time', () => {
  assert.equal(torontoStamp('2026-11-20 19:32:00'), '11/20 14:32')
  assert.equal(torontoStamp('2026-11-21T03:05:00Z'), '11/20 22:05')
  assert.equal(markText('Zimo', '2026-11-20 19:32:00'), 'Zimo · 11/20 14:32')
  assert.equal(markText(null), '没有员工资料 No staff profile')
  assert.equal(torontoStamp('not a time'), '')
})
