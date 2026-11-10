import client from './client'

// popcore.ca takes its stock from POPCORE; paid web orders take their items out of stock
// (owner, 2026-11-01). Website items link by their POPCORE ID field, never by SKU (owner, 2026-11-07).
// See docs/website-sync.md.
export interface WebsiteStatus {
  configured: boolean; settings_problem: string | null; url: string | null; keep_back: number | null
  delivery_store: string | null; shop_opens: string | null
  // Each live store's last reported business day and the shop days since that are not reported yet.
  report: Array<{ store: string; reported_through: string | null; days_behind: number }>
  enabled: boolean; since: string | null; switched_at: string | null
  refreshed_at: string | null; refresh_requested: boolean; last_run: string | null; last_push_at: string | null
  last_push_count: number; last_error: { code: string; message: string; at: string } | null
  live_stores: string[]; push_errors: number
  orders: { recorded: number; needs_review: number; cancelled: number; dismissed: number }
}
export interface WebsiteChange {
  wc_id: number; parent_id: number | null; popcore_id: string; name: string; product_id: number | null
  manage_stock: number; stock_quantity: number | null; stock_status: string | null; quantity: number
  // A Whole Set only (null otherwise): its sealed sets, the whole sets its floor boxes are sure to have (at least)
  // (owner, 2026-11-10), and its boxes per set (null when unknown: the floor then adds none).
  sealed_sets: number | null; floor_sets: number | null; set_size: number | null
}
export interface WebsitePreview {
  // items: linked; unknown_id: a POPCORE ID POPCORE does not have; shared_id: an ID on more than one
  // website item; no_id: no POPCORE ID, left as they are.
  items: number; unknown_id: number; shared_id: number; no_id: number; stock_changes: number; back_in_stock: number
  sold_out: number; low_stock: number; pickup_changes: number; changes: WebsiteChange[]
  shop_hold: { items: number; units: number }
  floor_sets: { items: number; sets: number; unknown_size: number }
  pickup: Array<{ wc_id: number; name: string; popcore_id: string; pickup: string[]; wanted: string[] }>
  unknown_ids: Array<{ wc_id: number; parent_id: number | null; popcore_id: string; name: string; shared: boolean }>
  // The website's SKU here is a barcode (or empty), shown only to find the item.
  no_ids: Array<{ wc_id: number; parent_id: number | null; sku: string; name: string }>
}
export type WebsiteOrderState = 'new' | 'recorded' | 'needs_review' | 'cancelled' | 'cancel_failed' | 'dismissed'
export interface WebsiteOrder {
  wc_order_id: number; number: string; wc_status: string; paid_at: string | null; total_cents: number | null
  payment: string | null; fulfilment: string | null; pickup_store: string | null; store_code: string | null
  state: WebsiteOrderState; problem: string | null; refunded: number; dismiss_reason: string | null
  lines: Array<{ popcore_id?: string; name: string; quantity: number; product_id: number | null }>
}

export const problemText: Record<string, string> = {
  not_enough_stock: '库存不够 · The store does not have enough. Count or receive it, then try again.',
  unknown_item: '网站产品未连接 · An item has no POPCORE ID on the website, or one POPCORE does not have.',
  no_live_store: '没有上线的门店 · No live store can fill it.',
  no_items: '没有产品 · The order has no items.',
  no_store_access: '没有门店权限 · The website has no stock access at this store (Users → Store operations access).',
  reopened: '取消后又付款 · Paid again after it was cancelled.',
}

export const getWebsiteStatus = () => client.get<WebsiteStatus>('/website/status').then(r => r.data)
export const getWebsitePreview = () => client.get<WebsitePreview>('/website/preview').then(r => r.data)
export const refreshWebsite = () => client.post<WebsiteStatus>('/website/refresh').then(r => r.data)
export const switchWebsite = (on: boolean) => client.post<WebsiteStatus>('/website/switch', { on }).then(r => r.data)
export const getWebsiteOrders = () => client.get<{ orders: WebsiteOrder[] }>('/website/orders').then(r => r.data.orders)
export const retryWebsiteOrder = (id: number) =>
  client.post<{ state: WebsiteOrderState }>(`/website/orders/${id}/retry`).then(r => r.data.state)
export const dismissWebsiteOrder = (id: number, reason: string) =>
  client.post<{ state: WebsiteOrderState }>(`/website/orders/${id}/dismiss`, { reason }).then(r => r.data.state)
