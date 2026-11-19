import type { WebOrderCard, WebOrderCodeResult, WebOrderColumn, WebOrderLine, WebOrdersBoard } from '../../api/webOrders.ts'
import { whereText } from '../../lib/stockPlaces.ts'

// The five columns, left to right (website session's brief, 2026-11-19).
export const COLUMNS: Array<{ key: WebOrderColumn; zh: string; en: string }> = [
  { key: 'pick', zh: '自取 · 待拣货', en: 'Pickup · to pick' },
  { key: 'ready', zh: '自取 · 等顾客', en: 'Pickup · ready, waiting for the customer' },
  { key: 'pack', zh: '配送 · 待打包', en: 'Delivery · to pack' },
  { key: 'booked', zh: '配送 · 等快递', en: 'Delivery · booked, waiting for the courier' },
  { key: 'out', zh: '配送 · 派送中', en: 'Delivery · out for delivery' },
]

// The website never cancels an uncollected pickup: one waiting this long stands out.
export const LONG_WAIT_DAYS = 3
const DAY = 24 * 60 * 60 * 1000

// The website's times are GMT without a zone.
export function websiteTime(value: string | null | undefined): number | null {
  if (!value) return null
  const time = Date.parse(`${value.replace(' ', 'T')}Z`)
  return Number.isNaN(time) ? null : time
}

/** How long ago an order was paid: "5 min", "2 h", "3 days". */
export function ageText(paidAt: string | null, now: number): string {
  const paid = websiteTime(paidAt)
  if (paid === null) return ''
  const minutes = Math.max(0, Math.floor((now - paid) / 60000))
  if (minutes < 60) return `${minutes} min`
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)} h`
  const days = Math.floor(minutes / (24 * 60))
  return `${days} day${days === 1 ? '' : 's'}`
}

/** Whole days a pickup has waited when that is LONG_WAIT_DAYS or more, else 0. */
export function longWait(card: WebOrderCard, now: number): number {
  const paid = websiteTime(card.paid_at)
  if (card.kind !== 'pickup' || paid === null) return 0
  const days = Math.floor((now - paid) / DAY)
  return days >= LONG_WAIT_DAYS ? days : 0
}

/** Where a line's stock was taken from: "DT 楼下 Floor 2 · DT 楼上 Upstairs 1". */
export const picksText = (line: WebOrderLine) =>
  line.picks.map(pick => `${whereText(pick.place, pick.store)} ${pick.quantity}`).join(' · ')

const SERVICE: Record<string, string> = { normal: '普通 Normal', door: '送到门口 Door to door' }

/** A delivery card's facts: what the customer chose, then FleetNow's once it is booked. */
export function deliveryFacts(card: WebOrderCard): string[] {
  const delivery = card.delivery ?? {}
  const facts = [
    delivery.window && `送达 ${delivery.window}`,
    delivery.service && (SERVICE[delivery.service] ?? delivery.service),
    delivery.zone && `区域 Zone ${delivery.zone}`,
  ]
  if (delivery.fleetnow_no) facts.push(
    `FleetNow ${delivery.fleetnow_no}${delivery.fleetnow_status ? ` · ${delivery.fleetnow_status}` : ''}`,
    delivery.fleetnow_pickup && `取件 Pickup ${delivery.fleetnow_pickup}`,
    delivery.fleetnow_delivery && `派送 Delivery ${delivery.fleetnow_delivery}`)
  return facts.filter((fact): fact is string => !!fact)
}

// Why an order needs a manager, in staff words.
const PROBLEM: Record<string, string> = {
  not_enough_stock: '库存不够 · POPCORE’s count does not have enough of it. A manager counts or receives it.',
  unknown_item: '产品未连接 · An item is not linked to POPCORE. A manager fixes the website item.',
  no_live_store: '没有上线的门店 · No live store can fill it.',
  no_items: '没有产品 · The order has no items.',
  no_store_access: '没有门店权限 · The website has no stock access at this store.',
  reopened: '取消后又付款 · Paid again after it was cancelled.',
  cancel_failed: '取消未退回 · Cancelled on the website; POPCORE could not put its stock back.',
}
const STATUS: Record<string, string> = {
  'on-hold': '等付款 · On hold: the payment is not settled.',
  'awaiting-stock': '等货 · A special order waiting for stock.',
  pending: '未付款 · Waiting for payment.',
  failed: '付款失败 · The payment failed.',
}

export function reasonText(card: WebOrderCard): string {
  if (card.reason === 'status') return STATUS[card.status] ?? `网站状态 · The website has it as “${card.status}”.`
  return PROBLEM[card.reason ?? ''] ?? card.reason ?? ''
}

/** What the code box says when it found no order to hand over. */
export function codeMissText(result: Exclude<WebOrderCodeResult, { found: true }>): string {
  switch (result.why) {
    case 'not_found': return '没有这个取货码 · No pickup order has this code. Check it with the customer.'
    case 'not_pickup': return '不是自取订单 · This code is not for a pickup order.'
    case 'completed': return '已经取走 · This order was already handed over.'
    case 'cancelled': return '已取消 · This order was cancelled on the website. Hand nothing over.'
    case 'refunded': return '已退款 · This order was refunded on the website. Hand nothing over.'
    case 'other_store': return `${result.store} 的订单 · This order is for pickup at ${result.store}.`
    case 'status': return `${STATUS[result.status ?? ''] ?? `The website has this order as “${result.status}”.`} It cannot be handed over here: ask a manager.`
  }
  return ''
}

/** Every order on the board that is still open: the five columns and Needs a manager. */
export const openCards = (board: WebOrdersBoard): WebOrderCard[] =>
  [...Object.values(board.columns).flat(), ...board.needs_manager]

/** Open orders the board did not have before: the ones to announce. */
export const newCards = (board: WebOrdersBoard, seen: Set<number>): WebOrderCard[] =>
  openCards(board).filter(card => !seen.has(card.id))

/** A new order as the board's pop-up says it. */
export const announceText = (card: WebOrderCard) =>
  `${card.kind === 'pickup' ? `自取 ${card.store} pickup` : '配送 Delivery'} · ${card.lines.map(line => `${line.name} × ${line.quantity}`).join(', ')}`

// POPCORE's push key travels as base64url; browsers take and give it as bytes.
export function keyBytes(text: string): Uint8Array<ArrayBuffer> {
  const plain = atob(text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - text.length % 4) % 4))
  return Uint8Array.from(plain, ch => ch.charCodeAt(0))
}
export function keyText(bytes: ArrayBuffer | Uint8Array | null | undefined): string {
  if (!bytes) return ''
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}
