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
