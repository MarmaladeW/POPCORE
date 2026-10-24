import assert from 'node:assert/strict'
import { test } from 'node:test'
import { finishDigits, parseFinishDigits } from './finishTime.ts'

test('actual finish digits read as a time of day', () => {
  assert.equal(parseFinishDigits('2134'), '21:34')
  assert.equal(parseFinishDigits('934'), '09:34')
  assert.equal(parseFinishDigits('21'), '21:00')
  assert.equal(parseFinishDigits('9'), '09:00')
  assert.equal(parseFinishDigits('045'), '00:45')
  assert.equal(parseFinishDigits('0'), '00:00')
  assert.equal(parseFinishDigits('100'), '01:00')
  assert.equal(parseFinishDigits('21:34'), '21:34')
  assert.equal(parseFinishDigits(' 2 1 3 4 '), '21:34')
  assert.equal(parseFinishDigits(''), '')
})

test('digits that are not a time are refused', () => {
  for (const input of ['2460', '2400', '25', '960', '99', '12345', '1260']) assert.equal(parseFinishDigits(input), null, input)
})

test('a saved finish shows as the digits that produce it', () => {
  for (const value of ['21:34', '09:34', '21:00', '00:45', '01:00', '00:00']) {
    assert.equal(parseFinishDigits(finishDigits(value)), value, value)
  }
  assert.equal(finishDigits('21:34'), '2134')
  assert.equal(finishDigits('09:34'), '934')
  assert.equal(finishDigits('00:45'), '045')
  assert.equal(finishDigits(''), '')
  assert.equal(finishDigits(null), '')
})
