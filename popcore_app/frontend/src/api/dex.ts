import client from './client'

// 图鉴 Dex (owner, 2026-11-12: "The Dex itself lives on POPCORE"). One shape for every level; the rules
// are on the server (dex_book.py, docs/dex-book.md).
export type DexKind = 'company' | 'partner' | 'ip' | 'character' | 'series' | 'product' | 'design'
export type DexRarity = 'normal' | 'popular' | 'secret' | 'super_secret' | 'special'
export type DexExtras = Record<string, unknown>

export interface DexStep { id: number; kind: DexKind; name: string }
export interface DexEntry {
  id: number; kind: DexKind; name: string; parent_id: number | null; partner_id: number | null
  character_id: number | null; sort_order: number; blind_box: boolean | null; rarity: DexRarity | null
  product_id: number | null; product_sku: string | null; product_name: string | null
  picture_url: string | null; thumb_url: string | null; extras: DexExtras; website_id: number | null
  version: number; child_count: number; updated_at: string; updated_by: string | null
}
export interface DexSearchHit extends DexEntry { path: DexStep[] }
export interface DexDetail extends DexEntry {
  path: DexStep[]; allowed_children: DexKind[]; allowed_parents: DexKind[]
  product: { id: number; sku: string; name: string } | null
  partner: DexStep | null; character: DexStep | null; ip: DexStep | null
}
export interface DexRemoval {
  counts: Partial<Record<DexKind, number>>
  linked: { id: number; name: string; sku: string }[]
  used_by: DexStep[]
}
export type DexChange = Partial<{
  name: string; parent_id: number | null; partner_id: number | null; character_id: number | null
  blind_box: boolean | null; rarity: DexRarity | null; product_id: number | null; extras: DexExtras
}>

export const topEntries = (kind: 'company' | 'partner') =>
  client.get<DexEntry[]>('/dex/entries', { params: { kind } }).then(r => r.data)
export const childEntries = (parent: number) =>
  client.get<DexEntry[]>('/dex/entries', { params: { parent } }).then(r => r.data)
export const searchEntries = (q: string, kinds?: DexKind[]) =>
  client.get<DexSearchHit[]>('/dex/entries', { params: { q, ...(kinds?.length ? { kinds: kinds.join(',') } : {}) } }).then(r => r.data)
export const getEntry = (id: number) => client.get<DexDetail>(`/dex/entries/${id}`).then(r => r.data)
export const getRemoval = (id: number) => client.get<DexRemoval>(`/dex/entries/${id}/removal`).then(r => r.data)
export const createEntry = (body: DexChange & { kind: DexKind }) =>
  client.post<DexDetail>('/dex/entries', body).then(r => r.data)
export const updateEntry = (id: number, body: DexChange) =>
  client.patch<DexDetail>(`/dex/entries/${id}`, body).then(r => r.data)
export const deleteEntry = (id: number) =>
  client.delete<{ removed: number; kept_for_website: number }>(`/dex/entries/${id}`).then(r => r.data)
export const reorderEntries = (parent_id: number | null, ids: number[]) =>
  client.post<DexEntry[]>('/dex/reorder', { parent_id, ids }).then(r => r.data)

// POPCORE items not in the book yet (no live book entry links them); never sealed sets.
export interface DexUnlinkedItem {
  id: number; sku: string; name_cn_en: string | null; jizhanming: string | null; design_name: string | null
  stock_form: 'random_box' | 'ordinary' | 'confirmed_design' | null; ip_series: string | null
  box: { id: number; sku: string; name: string | null } | null
  box_entry: { id: number; name: string } | null
}
export interface DexUnlinkedPage { items: DexUnlinkedItem[]; total: number; page: number; per_page: number }
export const unlinkedItems = (q = '', page = 1, per_page = 50) =>
  client.get<DexUnlinkedPage>('/dex/unlinked', { params: { q, page, per_page } }).then(r => r.data)
