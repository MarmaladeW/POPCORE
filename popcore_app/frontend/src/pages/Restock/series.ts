import client from '../../api/client'
import type { RestockItem } from './index'

// 补货 asks by series (owner, 2026-11-03): one card per blind box with its 整端 whole sets, 散盒 single
// boxes and 确认款 designs, as on 盘点. A product outside any series is a card of its own.
export type SeriesRole = 'set' | 'box' | 'item' | 'design'

export interface SeriesResult {
  product_id: number
  title: string
  sku: string | null
  ip_series: string | null
  kind: 'box' | 'item' | 'series'
  sets: number
  designs: number
  image: string | null
  on_sheet: number
  back: Record<string, number> | null   // upstairs by unit; null without 库存查看
  stock_hidden?: boolean
  out_of_stock?: boolean
}

export interface SeriesItem {
  product_id: number
  role: SeriesRole
  sku: string | null
  name: string
  design_name: string | null
  stock_unit: 'box' | 'set' | 'piece' | null
  boxes_per_set: number | null
  requested_qty: number
  upstairs_qty: number | null
  instore_qty: number | null
  stock_hidden?: boolean
  out_of_stock?: boolean
}

export interface SeriesCardData {
  product_id: number
  title: string
  sku: string | null
  ip_series: string | null
  image: string | null
  no_whole_set: boolean
  items: SeriesItem[]
}

export const searchSeries = (sessionId: number, q: string, signal: AbortSignal) =>
  client.get<{ results: SeriesResult[] }>(`/restock/session/${sessionId}/series-search`, { params: { q }, signal })
    .then(response => response.data.results)

export const getSeries = (sessionId: number, productId: number, signal: AbortSignal) =>
  client.get<SeriesCardData>(`/restock/session/${sessionId}/series`, { params: { product_id: productId }, signal })
    .then(response => response.data)

export const saveSeries = (sessionId: number, productId: number, entries: { product_id: number; requested_qty: number }[]) =>
  client.put(`/restock/session/${sessionId}/series`, { product_id: productId, entries })

const ROLE_ORDER: Record<string, number> = { set: 0, box: 1, item: 1, design: 2 }

export function partLabel(role: string | undefined, designName?: string | null, name?: string) {
  if (role === 'set') return '整端 Whole sets'
  if (role === 'box') return '散盒 Single boxes'
  if (role === 'design') return designName || name || '确认款'
  return '数量 Quantity'
}

export interface FamilyGroup<T> { id: number; title: string; sku: string | null; items: T[] }

// The sheet's lines grouped by series card, in the order each series was first asked for.
export function familyGroups<T extends RestockItem>(items: T[]): FamilyGroup<T>[] {
  const groups = new Map<number, FamilyGroup<T>>()
  for (const item of items) {
    const id = item.family_id ?? item.product_id
    const group = groups.get(id) ?? { id, title: item.family_title || item.jizhanming || item.name_cn_en, sku: null, items: [] }
    if (item.role === 'box' || item.role === 'item') group.sku = item.sku
    group.items.push(item)
    groups.set(id, group)
  }
  // Designs in the card's order (the server sorts them by name, case aside).
  const named = (item: T) => (item.design_name || '').toLowerCase()
  for (const group of groups.values()) {
    group.items.sort((a, b) => (ROLE_ORDER[a.role ?? 'item'] - ROLE_ORDER[b.role ?? 'item'])
      || (named(a) < named(b) ? -1 : named(a) > named(b) ? 1 : 0))
  }
  return [...groups.values()]
}
