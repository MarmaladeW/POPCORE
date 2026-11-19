import assert from 'node:assert/strict'
import test from 'node:test'
import type { WebOrderCard } from '../../api/webOrders.ts'
import { ageText, codeMissText, deliveryFacts, longWait, picksText, reasonText } from './webOrdersView.ts'

const now = Date.parse('2026-11-19T18:00:00Z')
const card = (fields: Partial<WebOrderCard>): WebOrderCard =>
  ({ id: 1, number: '35864', paid_at: '2026-11-19T17:55:00', kind: 'pickup', store: 'DT', status: 'processing', lines: [], ...fields })

test('a card says how long ago it was paid', () => {
  assert.equal(ageText('2026-11-19T17:55:00', now), '5 min')
  assert.equal(ageText('2026-11-19T16:00:00', now), '2 h')
  assert.equal(ageText('2026-11-18T17:00:00', now), '1 day')
  assert.equal(ageText('2026-11-16T12:00:00', now), '3 days')
  assert.equal(ageText(null, now), '')
})

test('a pickup that has waited 3 days or more stands out; deliveries never do', () => {
  assert.equal(longWait(card({ paid_at: '2026-11-16T18:30:00' }), now), 0)
  assert.equal(longWait(card({ paid_at: '2026-11-16T17:30:00' }), now), 3)
  assert.equal(longWait(card({ paid_at: '2026-11-10T17:30:00', kind: 'delivery' }), now), 0)
})

test('lines say where they were taken from', () => {
  assert.equal(picksText({ name: 'Echo', quantity: 3, popcore_id: 'PM0100',
    picks: [{ store: 'DT', place: 'floor', quantity: 2 }, { store: 'MK', place: 'warehouse', quantity: 1 }] }),
  'DT 楼下 Floor 2 · MK 仓库 Warehouse 1')
})

test('a delivery shows the customer’s choice, then FleetNow’s booking', () => {
  assert.deepEqual(deliveryFacts(card({ kind: 'delivery', delivery: { window: 'Fri Nov 21, 2–6 pm', service: 'door', zone: 'Downtown' } })),
    ['送达 Fri Nov 21, 2–6 pm', '送到门口 Door to door', '区域 Zone Downtown'])
  assert.deepEqual(deliveryFacts(card({ kind: 'delivery', delivery: { service: 'normal', fleetnow_no: 'DO202611190000000001',
    fleetnow_status: 'PICKED_UP', fleetnow_pickup: '13:00–14:00', fleetnow_delivery: '14:00–18:00' } })),
  ['普通 Normal', 'FleetNow DO202611190000000001 · PICKED_UP', '取件 Pickup 13:00–14:00', '派送 Delivery 14:00–18:00'])
})

test('reasons and code misses are in staff words', () => {
  assert.match(reasonText(card({ reason: 'not_enough_stock' })), /^库存不够/)
  assert.match(reasonText(card({ reason: 'status', status: 'awaiting-stock' })), /special order/)
  assert.match(reasonText(card({ reason: 'status', status: 'trash' })), /“trash”/)
  assert.equal(codeMissText({ found: false, why: 'other_store', store: 'MK' }), 'MK 的订单 · This order is for pickup at MK.')
  assert.match(codeMissText({ found: false, why: 'cancelled' }), /Hand nothing over/)
  assert.match(codeMissText({ found: false, why: 'status', status: 'on-hold' }), /payment is not settled.*ask a manager/)
})
