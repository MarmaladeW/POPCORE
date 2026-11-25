import assert from 'node:assert/strict'
import test from 'node:test'
import { eveningAmount, eveningText } from './eveningText.ts'

test('晚盘 copies as staff write it in WeChat', () => {
  assert.equal(eveningAmount({ per_set: 8, sets: 2, loose: 7 }), '8*2➕7')
  assert.equal(eveningAmount({ per_set: 8, sets: 3, loose: 0 }), '8*3')
  assert.equal(eveningAmount({ per_set: null, sets: 0, loose: 5 }), '5')
  assert.equal(eveningAmount({ per_set: 6, sets: 0, loose: 22 }), '22')
  assert.equal(eveningText('2026-11-20', 'DT', [
    { name: 'Angry Molly', per_set: 8, sets: 2, loose: 7, matches: true },
    { name: '坐坐', per_set: 6, sets: 1, loose: 0, matches: false },
    { name: 'ETB-PE', per_set: null, sets: 0, loose: 4 },
  ]), '2026.11.20 DT 晚盘\nAngry Molly 8*2➕7 ✅\n坐坐 6*1 🥲\nETB-PE 4')
})
