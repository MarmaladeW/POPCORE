import assert from 'node:assert/strict'
import test from 'node:test'
import { POSITION_OPTIONS, positionBody, positionLabel, positionOf, rankIndex, regularRankInfo, withPosition } from './ranks.ts'
import { positionEmoji, positionText } from '../pages/Schedule/openHours.ts'

test('one picker: a rank sets the rights, admin and viewer have none (owner, 2026-11-19)', () => {
  assert.deepEqual(POSITION_OPTIONS.map(option => option.value),
    ['admin', 'store_manager', 'assistant_manager', 'senior', 'regular', 'trainee', 'viewer'])
  assert.deepEqual(positionBody('assistant_manager'), { rank: 'assistant_manager' })
  assert.deepEqual(positionBody('viewer'), { role: 'viewer' })
  assert.deepEqual(withPosition({ role: 'staff', rank: 'trainee' }, 'store_manager'), { role: 'manager', rank: 'store_manager' })
  assert.deepEqual(withPosition({ role: 'staff', rank: 'trainee' }, 'viewer'), { role: 'viewer', rank: null })
  assert.equal(positionOf({ role: 'staff', rank: null }), undefined)
  assert.equal(positionOf({ role: 'viewer', rank: 'trainee' }), 'viewer')  // a viewer is never a 培训员工
  assert.equal(positionLabel({ role: 'manager', rank: 'assistant_manager' }), '副店长')
  assert.equal(positionLabel({ role: 'manager', rank: null }), '经理')
})

test('people sort 店长 first, people without a rank last', () => {
  const names = [['YC', 'trainee'], ['Amy', null], ['Xiao Na', 'store_manager'], ['Zimo', 'senior'], ['Kitty', 'assistant_manager']]
    .sort((a, b) => rankIndex(a[1]) - rankIndex(b[1])).map(([name]) => name)
  assert.deepEqual(names, ['Xiao Na', 'Kitty', 'Zimo', 'YC', 'Amy'])
})

test('the schedule marks someone 转正 with their rank, 正 without a higher one (owner, 2026-11-22)', () => {
  assert.equal(regularRankInfo('store_manager').glyph, '长')
  assert.equal(regularRankInfo('assistant_manager').glyph, '副')
  assert.equal(regularRankInfo('senior').glyph, '资')
  assert.equal(regularRankInfo('regular').glyph, '正')
  assert.equal(regularRankInfo(null).glyph, '正')
  assert.equal(regularRankInfo('trainee').glyph, '正')
})

test('each shift position has its emoji, End reads as Back', () => {
  assert.equal(positionText('Back'), '📦 Back')
  assert.equal(positionEmoji('End'), '📦')
  assert.equal(positionText('Cashier'), '💰 Cashier')
  assert.equal(positionText('Sales'), '🛍️ Sales')
  assert.equal(positionText('Front'), '👋 Front')
  assert.equal(positionText('Stock room'), '📍 Stock room')
})
