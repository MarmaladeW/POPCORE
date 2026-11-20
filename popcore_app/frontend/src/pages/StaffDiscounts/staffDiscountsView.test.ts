import assert from 'node:assert/strict'
import test from 'node:test'
import { distinctProducts, itemsText, monthTitle, paymentLabel, shiftMonth, shortDate, standing } from './staffDiscountsView.ts'

test('months step across the year and read in Chinese', () => {
  assert.equal(shiftMonth('2026-11', 1), '2026-12')
  assert.equal(shiftMonth('2026-12', 1), '2027-01')
  assert.equal(shiftMonth('2026-01', -1), '2025-12')
  assert.equal(monthTitle('2026-11'), '2026年11月')
  assert.equal(shortDate('2026-11-05'), '11-5')
})

test('payments read in both languages, a split one with both', () => {
  assert.equal(paymentLabel('emt'), 'EMT')
  assert.equal(paymentLabel('card'), '卡机 Card')
  assert.equal(paymentLabel('cash+card'), '现金 Cash + 卡机 Card')
  assert.equal(paymentLabel(null), '')
})

test('products show with their quantities; ten of one product is one use', () => {
  assert.equal(itemsText([{ name:'Angry Molly 迪士尼公主毛绒', qty:2 }, { name:'SA Animal ver2', qty:1 }]),
    'Angry Molly 迪士尼公主毛绒 ×2 · SA Animal ver2 ×1')
  assert.equal(distinctProducts([{ name:'Smiski Hipper', qty:10 }]), 1)
  assert.equal(distinctProducts([{ name:'Smiski Hipper', qty:4 }, { name:'smiski  hipper ', qty:6 }, { name:'', qty:1 }]), 1)
  assert.equal(distinctProducts([{ name:'Labubu', qty:1 }, { name:'Crybaby', qty:1 }]), 2)
})

test('a person stands out at 4 and in red over 4; 培训员工 are not eligible', () => {
  assert.deepEqual(standing({ eligible:true, left:3, over:0 }), { tone:'ok', zh:'剩 3 次', en:'3 left' })
  assert.deepEqual(standing({ eligible:true, left:0, over:0 }), { tone:'warn', zh:'用完了', en:'All used' })
  assert.deepEqual(standing({ eligible:true, left:0, over:2 }), { tone:'out', zh:'超出 2 次', en:'Over by 2' })
  assert.deepEqual(standing({ eligible:false, left:null, over:0 }), { tone:'none', zh:'不适用', en:'Not eligible' })
})
