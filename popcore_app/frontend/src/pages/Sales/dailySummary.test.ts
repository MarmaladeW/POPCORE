import assert from 'node:assert/strict'
import { test } from 'node:test'
import { allCheckouts, itemsText, methodGroups, splitNote, torontoTime, type SummaryCheckout } from './dailySummary.ts'

const order = (id: number, at: string, payments: Array<[string, number]>, items = [{ name: `Toy ${id}`, quantity: 1 }]): SummaryCheckout =>
  ({ id, reference: `R-${id}`, actor_name: '王', created_at: `2026-10-22T${at}:00Z`, items, payments: payments.map(([tender, amount_cents]) => ({ tender, amount_cents })) })

const checkouts = {
  pos: [order(2, '16:00', [['card', 4259]]), order(1, '15:02', [['cash', 2800]])],
  non_pos: [order(3, '18:15', [['e_transfer', 7400]])],
  split: [order(4, '19:20', [['cash', 1800], ['card', 2200]], [{ name: 'SA Dream', quantity: 2 }])],
}

test('every method has its own group, in the fixed order, even when empty', () => {
  const groups = methodGroups(checkouts)
  assert.deepEqual(groups.map(group => [group.zh, group.count, group.cents]),
    [['现金', 2, 4600], ['刷卡', 2, 6459], ['转账 EMT', 1, 7400], ['微信', 0, 0], ['支付宝', 0, 0]])
  assert.deepEqual(groups[0].parts.map(part => part.order.reference), ['R-1', 'R-4'])
})

test('a split order shows its part under each method, with the other part named', () => {
  const [cash, card] = methodGroups(checkouts)
  const cashPart = cash.parts.find(part => part.order.id === 4)!
  const cardPart = card.parts.find(part => part.order.id === 4)!
  assert.equal(cashPart.cents, 1800)
  assert.equal(cardPart.cents, 2200)
  assert.equal(splitNote(cashPart), '拆单，另刷卡 $22.00')
  assert.equal(splitNote(cardPart, ' · '), '拆单 · 另现金 $18.00')
  assert.equal(splitNote(cash.parts[0]), '')
})

test('parts of one method within an order are added together, and unknown methods still show', () => {
  const groups = methodGroups({ split: [order(5, '12:00', [['cash', 500], ['cash', 700]]), order(6, '13:00', [['gift_card', 900]])] })
  assert.equal(groups[0].parts[0].cents, 1200)
  assert.deepEqual(groups[0].parts[0].others, [])
  assert.deepEqual(groups.at(-1)!.parts.map(part => part.cents), [900])
  assert.equal(groups.at(-1)!.zh, 'gift_card')
})

test('missing groups, item text and Toronto time', () => {
  assert.deepEqual(methodGroups(undefined).map(group => group.count), [0, 0, 0, 0, 0])
  assert.deepEqual(allCheckouts(checkouts).map(item => item.id), [1, 2, 3, 4])
  assert.equal(itemsText(order(7, '10:00', [], [{ name: 'A', quantity: 1 }, { name: 'B', quantity: 2 }])), 'A × 1 · B × 2')
  assert.equal(torontoTime('2026-10-22T15:02:00Z'), '11:02')
  assert.equal(torontoTime('2026-10-22 15:02:00'), '11:02')
  assert.equal(torontoTime('nonsense'), '')
})
