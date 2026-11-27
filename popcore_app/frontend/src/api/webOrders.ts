import client from './client'

// 网站订单 Web orders: the shop computer's order fulfilment screen (owner, 2026-11-19), built from the popcore.ca
// website session's brief of the same day. The board's data never holds the pickup code; the only personal thing in
// it is the name each delivery goes to (owner, 2026-11-25), read live from the website. See docs/web-orders-board.md.
export type WebOrderPick = { store: string; place: string; quantity: number }
export interface WebOrderLine {
  // The 记账名, else the official name, else the website's name.
  name: string; quantity: number; popcore_id: string | null
  // Where the stock was taken from, when the order's consume document records it.
  picks: WebOrderPick[]
}
export interface WebOrderDelivery {
  window?: string; service?: string; zone?: string
  fleetnow_no?: string; fleetnow_pickup?: string; fleetnow_delivery?: string; fleetnow_status?: string
  // The store the website booked FleetNow from ("dt", "mk"), once it has chosen.
  collect?: string
}
export interface WebOrderCard {
  id: number; number: string; paid_at: string | null; kind: 'pickup' | 'delivery'; store: string; status: string
  lines: WebOrderLine[]
  // The stores it leaves from. A pickup: its pickup store. A delivery (owner, 2026-11-26): the store the courier
  // collects from once the website booked it, else the store POPCORE took it from; two stores while its stock is in
  // both and nobody has brought it together. `store` is the first of them.
  stores?: string[]
  // The other stores its stock came from, to be brought over to `store`.
  taken_elsewhere?: string[]
  delivery?: WebOrderDelivery
  // Delivery: who the parcel goes to (shipping name, else billing), read live; null when the website did not answer.
  name?: string | null
  // Needs a manager: a POPCORE problem code, 'cancel_failed', or 'status' (a website status the columns lack).
  reason?: string | null
  done_at?: string | null
}
export type WebOrderColumn = 'pick' | 'ready' | 'pack' | 'booked' | 'out'
export interface WebOrdersBoard {
  configured: boolean; enabled: boolean; last_run: string | null
  // The stores this board shows: those the user has store operations access for.
  stores: string[]
  // Whether the board has deliveries, or is the delivery store's: only then is there a name box.
  deliveries: boolean
  // False when the website did not answer the names read: the delivery cards then have no names.
  names_read: boolean
  columns: Record<WebOrderColumn, WebOrderCard[]>
  needs_manager: WebOrderCard[]
  done_today: WebOrderCard[]
}
// The code box's answer. Only a found order carries the customer's first name (and the collector's), read live.
// why: not_found, not_pickup, completed, cancelled, refunded, other_store (with store) or status (with status).
export type WebOrderCodeResult =
  | { found: true; order: WebOrderCard; first_name: string; collector: string | null; needs_manager: boolean }
  | { found: false; why: string; store?: string; status?: string }
export type WebOrderStep = { result: 'done' | 'already'; noted: boolean | null }

export const getWebOrdersBoard = (store?: string) =>
  client.get<WebOrdersBoard>('/website/board', { params: store ? { store } : undefined }).then(r => r.data)
export const findWebOrderCode = (code: string, store?: string) =>
  client.post<WebOrderCodeResult>('/website/board/code', { code, store }).then(r => r.data)
export const markWebOrderReady = (id: number) =>
  client.post<WebOrderStep>(`/website/board/orders/${id}/ready`).then(r => r.data)
export const handWebOrderOver = (id: number, code: string) =>
  client.post<WebOrderStep>(`/website/board/orders/${id}/handed-over`, { code }).then(r => r.data)

// Notifications for new web orders (owner, 2026-11-19): Web Push to the devices a person turns on.
export type WebOrderDevices = { devices: number }
export const getWebOrderNotifications = () =>
  client.get<WebOrderDevices & { key: string }>('/website/notifications').then(r => r.data)
export const notifyThisDevice = (subscription: PushSubscriptionJSON) =>
  client.post<WebOrderDevices>('/website/notifications', { subscription }).then(r => r.data)
export const stopNotifyingThisDevice = (endpoint: string) =>
  client.post<WebOrderDevices>('/website/notifications/off', { endpoint }).then(r => r.data)
export const testWebOrderNotification = () =>
  client.post<WebOrderDevices & { sent: number; failed: number }>('/website/notifications/test').then(r => r.data)
