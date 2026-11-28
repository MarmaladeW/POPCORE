import client from './client'

// 动态 Activity (owner, 2026-11-20): every change anyone made, newest first, like the store's group chat.
export interface ActivityEntry {
  id: number; at: string; business_date: string; store_id: number | null; store_code: string | null
  actor_sub: string; actor_name: string | null; via_name: string | null
  kind: string; summary: string; detail: string; link: string | null
}
export interface ActivityPage {
  date: string; stores: Array<{ id: number; code: string; name: string }>; entries: ActivityEntry[]
  next_before_id: number | null; people: Array<{ sub: string; name: string | null }>; kinds: Array<[string, string]>
}
export interface ActivityQuery { store_code: string; date?: string; person?: string; kind?: string; before_id?: number; limit?: number }

export const getActivity = (params: ActivityQuery, signal?: AbortSignal) =>
  client.get<ActivityPage>('/activity', { params, signal }).then(response => response.data)

// What one line changed (owner, 2026-11-28: "admin should be able to click into there and see exactly what changed").
// Values are the words people read, kept when the change was made; null is empty. Admin only.
export interface ChangedField { label: string; before: string | null; after: string | null }
export interface ChangedRecord { type: string; label: string; link: string | null; op: 'add' | 'edit' | 'remove'; fields: ChangedField[] }
export interface StockEnd { place: string; before?: number; after?: number }
export interface StockLine { product: string; qty: number; from: StockEnd; to: StockEnd; into?: string; why?: string }
export interface ActivityChanges { v: number; records?: ChangedRecord[]; records_more?: number; stock?: StockLine[]; stock_more?: number }
export interface ActivityDetail extends ActivityEntry { store_name: string | null; kind_name: string; changes: ActivityChanges | null }

export const getActivityEntry = (id: number, signal?: AbortSignal) =>
  client.get<ActivityDetail>(`/activity/${id}`, { signal }).then(response => response.data)
