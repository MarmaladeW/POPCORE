import assert from 'node:assert/strict'
import test from 'node:test'
import type { WebOrderCard, WebOrdersBoard } from '../../api/webOrders.ts'
import { ageText, announceText, codeMissText, deliveryFacts, keyBytes, keyText, longWait, nameMatches, newCards, picksText, plainName, reasonText } from './webOrdersView.ts'

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

test('the board announces only open orders it did not have before', () => {
  const board = (ids: number[], waiting: number[] = []): WebOrdersBoard => ({
    configured: true, enabled: true, last_run: null, stores: ['DT'], deliveries: true, names_read: true, done_today: [card({ id: 99 })],
    columns: { pick: ids.map(id => card({ id })), ready: [], pack: [], booked: [], out: [] },
    needs_manager: waiting.map(id => card({ id, reason: 'not_enough_stock' })),
  })
  assert.deepEqual(newCards(board([1, 2], [3]), new Set([1])).map(found => found.id), [2, 3])
  assert.deepEqual(newCards(board([1]), new Set([1, 2])), [])
  assert.equal(announceText(card({ lines: [{ name: 'Echo', quantity: 2, popcore_id: 'PM0100', picks: [] }] })), '自取 DT pickup · Echo × 2')
  assert.equal(announceText(card({ kind: 'delivery', lines: [] })), '配送 Delivery · ')
})

test('the name box finds deliveries by part of a name, whatever the case and accents (owner, 2026-11-25)', () => {
  assert.equal(plainName('  Zoë   NG '), 'zoe ng')
  assert.equal(plainName('Søren Łukasz Strauß'), 'soren lukasz strauss')
  const delivery = (id: number, name: string | null) => card({ id, number: String(id), kind: 'delivery', name })
  const board: WebOrdersBoard = {
    configured: true, enabled: true, last_run: null, stores: ['DT'], deliveries: true, names_read: true,
    columns: { pick: [card({ id: 1 })], ready: [], pack: [delivery(2, 'Zoë Ng')], booked: [delivery(3, 'Ana Lima'), delivery(4, null)],
      out: [] },
    needs_manager: [delivery(5, 'Émile Roy')], done_today: [delivery(6, 'Zoe Ngo')],
  }
  const found = (typed: string) => nameMatches(board, typed).map(match => `${match.card.id} ${match.where}`)
  assert.deepEqual(found('zoe'), ['2 配送 · 待打包 · Delivery · to pack', '6 今天完成 · Done today'])
  assert.deepEqual(found('ZO NG'), ['2 配送 · 待打包 · Delivery · to pack', '6 今天完成 · Done today'])
  assert.deepEqual(found('ngo'), ['6 今天完成 · Done today'])
  assert.deepEqual(found('lim'), ['3 配送 · 等快递 · Delivery · booked, waiting for the courier'])
  assert.deepEqual(found('emile'), ['5 需要经理 · Needs a manager'])
  assert.deepEqual(found('   '), [])
  assert.deepEqual(found('Ana Roy'), [])
  // Pickups are found by their code only; the board gives them no name.
  assert.deepEqual(nameMatches({ ...board, columns: { ...board.columns, pick: [{ ...card({ id: 1 }), name: 'Zoë' }] } }, 'zoe').map(match => match.card.id), [2, 6])
})

test('POPCORE’s push key goes to the browser as bytes and back', () => {
  const key = 'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8'
  const bytes = keyBytes(key)
  assert.equal(bytes.length, 65)
  assert.equal(bytes[0], 4)
  assert.equal(keyText(bytes.buffer), key)
  assert.equal(keyText(null), '')
})
