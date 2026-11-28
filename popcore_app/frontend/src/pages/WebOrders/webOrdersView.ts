import type { WebOrderCard, WebOrderCheck, WebOrderCodeResult, WebOrderColumn, WebOrderLine, WebOrdersBoard } from '../../api/webOrders.ts'
import { whereText } from '../../lib/stockPlaces.ts'

// The five columns, left to right (website session's brief, 2026-11-19). Deliveries go by packing (owner, 2026-11-27):
// 待打包 until 打包好了 Packed, 等快递 after it, whether a courier is booked yet or not.
export const COLUMNS: Array<{ key: WebOrderColumn; zh: string; en: string }> = [
  { key: 'pick', zh: '自取 · 待拣货', en: 'Pickup · to pick' },
  { key: 'ready', zh: '自取 · 等顾客', en: 'Pickup · ready, waiting for the customer' },
  { key: 'pack', zh: '配送 · 待打包', en: 'Delivery · to pack' },
  { key: 'booked', zh: '配送 · 等快递', en: 'Delivery · packed, waiting for the courier' },
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

/** A time on the store's clock: "14:05", or "—". */
export const clockText = (time: number | null) => time === null ? '—'
  : new Date(time).toLocaleTimeString('en-CA', { timeZone: 'America/Toronto', hour: '2-digit', minute: '2-digit', hour12: false })

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

/** How many pieces an order holds, all lines together: the card's 共 N 件 (owner, 2026-11-27: "the number/amount
 * for each order is not that obvious"). */
export const pieceCount = (card: WebOrderCard) => card.lines.reduce((sum, line) => sum + line.quantity, 0)

/** Where a line's stock was taken from: "DT 楼下 Floor 2 · DT 楼上 Upstairs 1". */
export const picksText = (line: WebOrderLine) =>
  line.picks.map(pick => `${whereText(pick.place, pick.store)} ${pick.quantity}`).join(' · ')

const SERVICE: Record<string, string> = { normal: '普通 Normal', door: '送到门口 Door to door' }

/** A delivery card's facts: what the customer chose, then FleetNow's once it is booked, its times before its long
 * booking number (the times are what the store works to). */
export function deliveryFacts(card: WebOrderCard): string[] {
  const delivery = card.delivery ?? {}
  const facts = [
    delivery.window && `送达 ${delivery.window}`,
    delivery.service && (SERVICE[delivery.service] ?? delivery.service),
    delivery.zone && `区域 Zone ${delivery.zone}`,
  ]
  if (delivery.fleetnow_no) facts.push(
    delivery.fleetnow_pickup && `取件 Pickup ${delivery.fleetnow_pickup}`,
    delivery.fleetnow_delivery && `派送 Delivery ${delivery.fleetnow_delivery}`,
    `FleetNow ${delivery.fleetnow_no}${delivery.fleetnow_status ? ` · ${delivery.fleetnow_status}` : ''}`)
  return facts.filter((fact): fact is string => !!fact)
}

/** Who packed a delivery and when: "已打包 Packed · 14:05 · DT 柜台", or '' while it is not packed. */
export function packedText(card: WebOrderCard): string {
  if (!card.packed_at) return ''
  return ['已打包 Packed', clockText(websiteTime(card.packed_at)), card.packed_by].filter(Boolean).join(' · ')
}

/** What a delivery's courier means for the store, only when it needs saying: packed with no courier booked, or a
 * courier booked for a parcel not packed yet. Gone once FleetNow picks it up. */
export function courierText(card: WebOrderCard): string {
  if (card.kind !== 'delivery' || card.status === 'out-for-delivery') return ''
  if (card.packed_at && !card.booked) return '快递未预约 · Courier not booked yet: an admin books it on popcore.ca.'
  if (!card.packed_at && card.booked) return '快递已预约,先打包 · A courier is booked: pack it first.'
  return ''
}

// Why an order needs the admin, in staff words (the 网站 page that settles them is the admin's since 2026-11-26).
const PROBLEM: Record<string, string> = {
  not_enough_stock: '库存不够 · POPCORE’s count does not have enough of it. Tell the admin: it needs a count or a receipt.',
  unknown_item: '产品未连接 · An item is not linked to POPCORE. An admin fixes the website item.',
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
    case 'status': return `${STATUS[result.status ?? ''] ?? `The website has this order as “${result.status}”.`} It cannot be handed over here: ask the admin.`
  }
  return ''
}

/** Every order on the board that is still open: the five columns and Needs an admin. */
export const openCards = (board: WebOrdersBoard): WebOrderCard[] =>
  [...Object.values(board.columns).flat(), ...board.needs_manager]

/** Open orders the board did not have before: the ones to announce. */
export const newCards = (board: WebOrdersBoard, seen: Set<number>): WebOrderCard[] =>
  openCards(board).filter(card => !seen.has(card.id))

// Letters NFD does not take apart.
const BASE_LETTERS: Record<string, string> = { ø: 'o', ł: 'l', đ: 'd', ð: 'd', þ: 'th', ß: 'ss', æ: 'ae', œ: 'oe', ı: 'i' }

/** A name as the name box compares it: no accents, no case, single spaces ("  Zoë  NG" → "zoe ng"). */
export const plainName = (text: string) => text.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase()
  .replace(/[øłđðþßæœı]/g, letter => BASE_LETTERS[letter]).replace(/\s+/g, ' ').trim()

export type NameMatch = { card: WebOrderCard; where: string }

/** The delivery orders on the board whose name holds every word typed, anywhere in the first or last name
 * ("zoe", "ng", "zo n" all find Zoë Ng), with where each one is on the board. Owner, 2026-11-25. */
export function nameMatches(board: WebOrdersBoard, typed: string): NameMatch[] {
  const words = plainName(typed).split(' ').filter(Boolean)
  if (!words.length) return []
  const places: Array<[WebOrderCard[], string]> = [
    ...COLUMNS.map(column => [board.columns[column.key], `${column.zh} · ${column.en}`] as [WebOrderCard[], string]),
    [board.needs_manager, '需要管理员 · Needs an admin'],
    [board.done_today, '今天完成 · Done today'],
  ]
  return places.flatMap(([cards, where]) => cards
    .filter(card => card.kind === 'delivery' && card.name && words.every(word => plainName(card.name!).includes(word)))
    .map(card => ({ card, where })))
}

/** The stores an order leaves from: one, or two while a delivery's stock is in both. */
export const cardStores = (card: WebOrderCard) => card.stores?.length ? card.stores : [card.store]

/** A card's kind and store: "自取 DT", "配送 MK", "配送 DT + MK". */
export const kindText = (card: WebOrderCard) => `${card.kind === 'pickup' ? '自取' : '配送'} ${cardStores(card).join(' + ')}`

/** What staff must move before the order can leave, or '' when it is all in its store. */
export function moveText(card: WebOrderCard): string {
  const elsewhere = card.taken_elsewhere ?? []
  if (elsewhere.length) return `从 ${elsewhere.join(' + ')} 拿 · Taken from ${elsewhere.join(' + ')}: bring it to ${card.store}`
  const stores = cardStores(card)
  // The website books no courier for a delivery taken from two stores (owner, 2026-11-26).
  if (card.kind === 'delivery' && stores.length > 1) return `货在 ${stores.join(' + ')},先集中到一家店 · Taken from ${stores.join(' and ')}: bring it to one store. No courier is booked yet.`
  return ''
}

/** 请核对 Check (popcore-work#149): what staff should check on the floor before they pack it. */
export function checkText(check: WebOrderCheck): string {
  if (check.why === 'set_opened') return `请核对 ${check.store} 楼下：${check.name} 没有散盒，系统拿下 ${check.units} 端开到楼下 · Check ${check.store} floor: no loose ${check.name}, POPCORE brought ${check.units} set${check.units === 1 ? '' : 's'} down and opened ${check.units === 1 ? 'it' : 'them'}`
  return `请核对 ${check.store} 楼下：${check.name} ${check.units} 盒，日报没到，可能已卖完 · Check ${check.store} floor: ${check.name} ×${check.units} may have sold in store (report not in)`
}

/** A new order as the board's pop-up says it. */
export const announceText = (card: WebOrderCard) =>
  `${kindText(card)} ${card.kind === 'pickup' ? 'pickup' : 'delivery'} · ${card.lines.map(line => `${line.name} × ${line.quantity}`).join(', ')}`

// POPCORE's push key travels as base64url; browsers take and give it as bytes.
export function keyBytes(text: string): Uint8Array<ArrayBuffer> {
  const plain = atob(text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - text.length % 4) % 4))
  return Uint8Array.from(plain, ch => ch.charCodeAt(0))
}
export function keyText(bytes: ArrayBuffer | Uint8Array | null | undefined): string {
  if (!bytes) return ''
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}
