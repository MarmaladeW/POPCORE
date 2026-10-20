import assert from 'node:assert/strict'
import { test } from 'node:test'
import { cnyCharged, historyQuery, missingPhotos, paymentSummary, TENDER_FILTERS } from './checkoutHistory.ts'

test('a card shows the money actually received and every method used, once each', () => {
  assert.deepEqual(paymentSummary([
    { tender: 'card', amount_cents: 2200, status: 'completed' },
    { tender: 'cash', amount_cents: 2000, status: 'cancelled' },
    { tender: 'cash', amount_cents: 1900, status: 'completed' },
    { tender: 'cash', amount_cents: 100, status: 'completed' },
  ]), { cents: 4200, methods: ['Card', 'Cash'] })
  assert.deepEqual(paymentSummary([]), { cents: 0, methods: [] })
  assert.deepEqual(paymentSummary([{ tender: 'wechat', amount_cents: 500, status: 'pending' }]), { cents: 0, methods: [] })
})

test('WeChat Pay and Alipay show the yuan they charged, at the rate kept with each payment', () => {
  const wechat = { tender: 'wechat', amount_cents: 2400, status: 'completed', cny_fen: 11407, cny_per_cad: '4.7528' }
  assert.equal(cnyCharged(wechat), '¥114.07 charged at 1 CAD = 4.7528 CNY')
  assert.equal(cnyCharged({ cny_fen: null, cny_per_cad: null }), '')
  assert.equal(cnyCharged({}), '')
  assert.deepEqual(paymentSummary([
    { tender: 'cash', amount_cents: 1000, status: 'completed' },
    wechat,
    { tender: 'wechat', amount_cents: 1000, status: 'completed', cny_fen: 4760, cny_per_cad: '4.7600' },
    { tender: 'wechat', amount_cents: 900, status: 'cancelled', cny_fen: 4284, cny_per_cad: '4.7600' },
  ]), { cents: 4400, methods: ['Cash', 'WeChat Pay ¥161.67'] })
  // A payment recorded before POPCORE kept the yuan shows the method alone.
  assert.deepEqual(paymentSummary([{ tender: 'alipay', amount_cents: 500, status: 'completed' }]),
    { cents: 500, methods: ['Alipay'] })
})

test('the payment filter offers every tender and the search is trimmed and bounded', () => {
  assert.deepEqual(TENDER_FILTERS.map(option => option.value), ['cash', 'card', 'e_transfer', 'wechat', 'alipay'])
  assert.equal(historyQuery('  Taxable Item #2  '), 'Taxable Item #2')
  assert.equal(historyQuery('   '), '')
  assert.equal(historyQuery('x'.repeat(100)).length, 80)
})

test('only received e-transfer, WeChat Pay and Alipay payments without a photo count as missing', () => {
  assert.equal(missingPhotos([
    { tender: 'e_transfer', amount_cents: 8300, status: 'completed', photos: [] },
    { tender: 'wechat', amount_cents: 100, status: 'completed', photos: [{ id: 1 }] },
    { tender: 'alipay', amount_cents: 100, status: 'cancelled', photos: [] },
    { tender: 'cash', amount_cents: 100, status: 'completed', photos: [] },
    { tender: 'alipay', amount_cents: 100, status: 'completed' },
  ]), 2)
})
