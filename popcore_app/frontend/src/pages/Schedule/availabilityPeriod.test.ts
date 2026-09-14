import assert from 'node:assert/strict'
import test from 'node:test'
import { cycleStart, cycleDates, repeatFirstWeek, emptyDays, availabilityIssue } from './availabilityPeriod.ts'

test('shared 14-day cycles stay aligned across DST, years and earlier dates', () => {
  assert.equal(cycleStart('2026-09-14'), '2026-09-14')
  assert.equal(cycleStart('2026-09-27'), '2026-09-14')
  assert.equal(cycleStart('2026-09-28'), '2026-09-28')
  assert.equal(cycleStart('2026-09-13'), '2026-08-31')
  for (const date of ['2026-11-01', '2027-03-14', '2027-01-01']) {
    const days = cycleDates(cycleStart(date))
    assert.equal(days.length, 14)
    assert.equal(new Set(days).size, 14)
    assert.ok(days.includes(date))
    assert.equal(new Date(days[0] + 'T00:00:00Z').getUTCDay(), 1)
  }
})

test('repeat copies availability without changing target dates or treating blanks as off', () => {
  const days = emptyDays('2026-09-21')
  days[0] = { ...days[0], status: 'available', start_time: '12:00', end_time: '17:00' }
  days[1] = { ...days[1], status: 'unavailable' }
  const repeated = repeatFirstWeek(days)
  assert.deepEqual(repeated[7], { ...days[0], date: '2026-09-28' })
  assert.equal(repeated[8].status, 'unavailable')
  assert.equal(repeated[9].status, 'unset')
  assert.equal(days[7].status, 'unset')
})

test('assignment distinguishes unavailable, unsubmitted, wrong store and out-of-hours', () => {
  const available = { status: 'available' as const, store_code: 'DT', start_time: '12:00', end_time: '17:00', submitted_at: '2026-09-18' }
  assert.equal(availabilityIssue(available, 'DT', '12:00', '17:00'), null)
  assert.match(availabilityIssue(available, 'MK', '12:00', '17:00')!, /not submitted/i)
  assert.match(availabilityIssue({ ...available, submitted_at: null }, 'DT', '12:00', '17:00')!, /not submitted/i)
  assert.match(availabilityIssue({ ...available, status: 'unavailable' }, 'DT', '12:00', '17:00')!, /unavailable/i)
  assert.match(availabilityIssue(available, 'DT', '12:00', '22:00')!, /outside/i)
  assert.match(availabilityIssue(available, 'DT', '17:00', '12:00')!, /after/i)
})

test('late submission preserves newer edits and cannot restore an identity-cleared draft', async () => {
  const { finishSubmission } = await import('./availabilityPeriod.ts')
  const sent = { days: emptyDays('2026-09-21'), version: 1, dirty: true, submittedAt: null as string | null }
  const newer = { ...sent, days: repeatFirstWeek(sent.days) }
  const saved = { version: 2, submitted_at: '2026-09-18T12:00:00Z' }
  assert.equal(finishSubmission(sent, sent, saved)?.dirty, false)
  assert.equal(finishSubmission(newer, sent, saved)?.days, newer.days)
  assert.equal(finishSubmission(newer, sent, saved)?.dirty, true)
  assert.equal(finishSubmission(undefined, sent, saved), undefined)
  const newest = { ...newer, version: 3 }
  assert.equal(finishSubmission(newest, sent, saved), newest)
})
