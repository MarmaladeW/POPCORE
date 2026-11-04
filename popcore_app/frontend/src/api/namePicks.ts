import client from './client'

// 名称记忆 Name picks (owner, 2026-11-03): which product a name written in a daily report means.
// A pick confirms a line by itself only at the same store and section; elsewhere the paste asks again.
export type NameStatus = 'open' | 'disputed' | 'stale' | 'confirm' | 'remembered' | 'ignored'

export interface PickProduct {
  id: number
  sku: string | null
  jizhanming: string | null
  name_cn_en: string | null
  series_name?: string | null
  ip_series?: string | null
  design_name?: string | null
  stock_form?: string | null
  stock_unit?: string | null
}

export interface PickPlace { store_code: string; section: string; display: boolean }

export interface PickView {
  state: 'ok' | 'stale' | 'disputed'
  raw_name: string
  note: string
  products: PickProduct[]
  places: PickPlace[]
  picked_at: string | null
}

export interface RememberedPick extends PickView { name_key: string; note_key: string }

export interface PastedDay {
  store_code: string
  date: string
  parsed_at: string
  saved: boolean
  counts: Record<NameStatus, number>
}

export interface PastedName {
  name_key: string
  note_key: string
  raw_name: string
  note: string
  section: string
  display: boolean
  qty: number
  box_size: number | null
  loose_qty: number
  status: NameStatus
  guess: PickProduct | null
  pick: PickView | null
}

export interface PickBody {
  raw_name: string
  note: string
  product_id: number
  replaces: number[]
  name_key?: string
  note_key?: string
  store_code?: string
  section?: string
  display?: boolean
}

export const getNamePicks = (store_code: string, signal?: AbortSignal) =>
  client.get<{ picks: RememberedPick[]; days: PastedDay[] }>('/sales/name-picks', { params: { store_code }, signal }).then(r => r.data)
export const getPastedDay = (store_code: string, date: string, signal?: AbortSignal) =>
  client.get<{ names: PastedName[] }>('/sales/name-picks/day', { params: { store_code, date }, signal }).then(r => r.data.names)
export const savePick = (body: PickBody) => client.post('/sales/name-picks', body).then(r => r.data)
export const forgetPick = (body: { name_key: string; note_key: string; replaces: number[] }) =>
  client.delete('/sales/name-picks', { data: body }).then(r => r.data)
export const ignoreName = (body: { name_key: string; note_key: string; ignored: boolean }) =>
  client.post('/sales/name-picks/ignore', body).then(r => r.data)
