import client from './client'

// 图鉴 Dex (owner, 2026-11-12: "The Dex itself lives on POPCORE"). One shape for every level; the rules
// are on the server (dex_book.py, docs/dex-book.md).
export type DexKind = 'company' | 'partner' | 'ip' | 'character' | 'series' | 'product' | 'design'
export type DexRarity = 'normal' | 'popular' | 'secret' | 'super_secret' | 'special'
export type DexExtras = Record<string, unknown>

export interface DexStep { id: number; kind: DexKind; name: string }
// The IP tag (owner, 2026-11-14): the IP above an entry, else the IP it names, else the partner it sits under.
export interface DexIpTag { id: number; kind: 'ip' | 'partner'; name: string }
export interface DexEntry {
  id: number; kind: DexKind; name: string; parent_id: number | null; partner_id: number | null
  character_id: number | null; sort_order: number; blind_box: boolean | null; rarity: DexRarity | null
  product_id: number | null; product_sku: string | null; product_name: string | null
  // The linked POPCORE item's price now; null when nothing is linked or it has no price.
  price_cents: number | null
  picture_url: string | null; thumb_url: string | null; extras: DexExtras; website_id: number | null
  version: number; child_count: number; updated_at: string; updated_by: string | null
  // Contract 1.3: a product or design with no IP above names its own IP (a series with several IPs).
  ip_id: number | null; named_ip: { id: number; name: string } | null
  // Naming standard rule 7: the full name, put together on the server from the entry's place (never typed).
  full_name: string; ip: DexIpTag | null
}
export interface DexSearchHit extends DexEntry { path: DexStep[] }
export interface DexDetail extends DexEntry {
  path: DexStep[]; allowed_children: DexKind[]; allowed_parents: DexKind[]
  product: { id: number; sku: string; name: string } | null
  // ip_above: the IP the entry sits under, or is (its characters are the ones the editor offers).
  partner: DexStep | null; character: DexStep | null; ip_above: DexStep | null
  // On an IP: the products and designs elsewhere in the book that name it.
  named_by: DexSearchHit[]
}
export interface DexRemoval {
  counts: Partial<Record<DexKind, number>>
  linked: { id: number; name: string; sku: string }[]
  used_by: DexStep[]
}
export type DexChange = Partial<{
  name: string; parent_id: number | null; partner_id: number | null; character_id: number | null; ip_id: number | null
  blind_box: boolean | null; rarity: DexRarity | null; product_id: number | null; extras: DexExtras
}>

export const topEntries = (kind: 'company' | 'partner') =>
  client.get<DexEntry[]>('/dex/entries', { params: { kind } }).then(r => r.data)
export const childEntries = (parent: number) =>
  client.get<DexEntry[]>('/dex/entries', { params: { parent } }).then(r => r.data)
// Every live IP with its company, for the IP picker.
export const ipEntries = () => client.get<DexSearchHit[]>('/dex/entries', { params: { kind: 'ip' } }).then(r => r.data)
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
  // The closest book entry by name (score 0-100), for a manager to link; never linked by itself.
  suggestion: { id: number; path: string; score: number } | null
  // The suggested entry and the box's book product with their full names and IP tags.
  suggested_entry: DexNamed | null; box_entry_named: DexNamed | null
}
export interface DexNamed { id: number; kind: DexKind; name: string; full_name: string; ip: DexIpTag | null }
export interface DexUnlinkedPage { items: DexUnlinkedItem[]; total: number; page: number; per_page: number }
export const unlinkedItems = (q = '', page = 1, per_page = 50) =>
  client.get<DexUnlinkedPage>('/dex/unlinked', { params: { q, page, per_page } }).then(r => r.data)

// A design's price trend: the prices managers set for its POPCORE item (design_prices, newest first; set_at UTC),
// and the website's market prices kept in its extras. confirmed_design: the item is a confirmed design, whose price
// is set as on 确认款价格 Design prices; any other item's is its own price, as the Products edit sets it.
export interface DexPricePoint {
  cents: number; previous_cents: number | null; source: string; set_at: string; set_by: string; set_by_name: string | null
}
export interface DexWebsitePrice { price: number; recorded_at: string | null; source: string | null }
export interface DexPrices {
  linked: boolean; confirmed_design: boolean; current_cents: number | null; points: DexPricePoint[]; website: DexWebsitePrice[]
}
export const entryPrices = (id: number) => client.get<DexPrices>(`/dex/entries/${id}/prices`).then(r => r.data)

// 图鉴同步 Dex sync, step 3: POPCORE sends the book to popcore.ca behind its own switch (dex_sync.py). Times are UTC.
export interface DexSyncProblem { code: string; message: string; at: string }
export interface DexSyncRefused {
  id: number; kind: DexKind; name: string; deleted: boolean; place: string[]; code: string; message: string; at: string | null
}
export interface DexSyncStatus {
  configured: boolean; settings_problem: string | null; url: string | null; enabled: boolean
  switched_at: string | null; switched_by: string | null; last_run: string | null
  last_sent_at: string | null; last_sent_count: number; first_send_done: boolean
  last_error: DexSyncProblem | null
  // The last time an admin's turn-on asked the website's book route.
  website_check: ({ ok: true; at: string } | ({ ok: false } & DexSyncProblem)) | null
  counts: { waiting: number; sent: number; refused: number; held: number; behind: number; deletes: number }
  deletes_held: boolean
  refused: DexSyncRefused[]
  // Products POPCORE does not send yet: no format from the website's list (contract 1.9 §4).
  held: { id: number; kind: DexKind; name: string; place: string[] }[]
}
export interface DexSyncPreview {
  create: number; update: number; delete: number; removed_quietly: number; held: number
  deletes: { id: number; kind: DexKind; name: string; website_id: number | null; place: string[] }[]
}
export interface DexSyncEntryState {
  enabled: boolean; state: 'sent' | 'waiting' | 'refused' | 'held' | 'behind'; version: number; sent_version: number
  website_id: number | null; code: string | null; message: string | null
}
export const dexSyncStatus = () => client.get<DexSyncStatus>('/dex/sync').then(r => r.data)
export const dexSyncPreview = () => client.get<DexSyncPreview>('/dex/sync/preview').then(r => r.data)
export const dexSyncEntry = (id: number) => client.get<DexSyncEntryState>(`/dex/sync/entries/${id}`).then(r => r.data)
export const switchDexSync = (on: boolean, confirm_deletes?: number) =>
  client.post<DexSyncStatus>('/dex/sync/switch', { on, ...(confirm_deletes === undefined ? {} : { confirm_deletes }) }).then(r => r.data)
export const retryDexSync = (target: number | 'all') =>
  client.post<{ retried: number }>('/dex/sync/retry', target === 'all' ? { all: true } : { id: target }).then(r => r.data)
export const uploadDexPicture = (id: number, file: File) => {
  const form = new FormData()
  form.append('file', file)
  return client.post<DexDetail>(`/dex/entries/${id}/picture`, form).then(r => r.data)
}

// 图鉴 Dex step 4: listings on popcore.ca for stocked items with none (contract §7). A manager approves them from the
// preview; they go as drafts on the Dex sync's pass once it is on and the item's book entry is on the website.
export type DexListingKind = 'blind_box' | 'ordinary' | 'confirmed_design'
export type DexListingState = 'approved' | 'created' | 'completed' | 'exists' | 'refused' | 'error'
export interface DexListingCandidate {
  product_id: number; popcore_id: string; kind: DexListingKind; name: string; book_ref: number | null
  popcore_type: string | null; price_cents: number | null
  set_id?: string | null; boxes_per_set?: number | null; set_price_cents?: number | null
  // Units a web order may take; a blind box's sealed sets beside (null with no Whole Set).
  stock: number; set_stock: number | null
  missing: string[]; action: 'new' | 'complete'
  state: 'ready' | 'book_waiting' | 'not_in_book'
  entry: { id: number; kind: DexKind; name: string } | null
  // Made before, but a newer catalogue read still lacks it.
  again: boolean
}
export interface DexListingRow {
  product_id: number; popcore_id: string; name: string; state: DexListingState
  approved_by: string; approved_at: string; sent_at: string | null; answered_at: string | null
  website_product_id: number | null; code: string | null; message: string | null
  // Approved rows only: what it waits for (null: it goes with the next pass while the switch is on).
  waits?: 'book' | 'not_in_book' | null
}
export interface DexListings {
  enabled: boolean; catalogue_read_at: string | null
  counts: {
    candidates: number; ready: number; book_waiting: number; not_in_book: number; approvable: number
    new: number; complete: number; approved: number; refused: number; done: number
    created: number; completed: number; exists: number
  }
  candidates: DexListingCandidate[]; approved: DexListingRow[]; refused: DexListingRow[]; done: DexListingRow[]
  last_error: DexSyncProblem | null
}
export const dexListings = () => client.get<DexListings>('/dex/listings').then(r => r.data)
export const approveDexListings = (target: number[] | { all: true; count: number }) =>
  client.post<{ approved: number }>('/dex/listings/approve', Array.isArray(target) ? { product_ids: target } : target).then(r => r.data)
export const withdrawDexListing = (product_id: number) =>
  client.post<{ withdrawn: number }>('/dex/listings/withdraw', { product_id }).then(r => r.data)
export const retryDexListings = (target: number | 'all') =>
  client.post<{ retried: number }>('/dex/listings/retry', target === 'all' ? { all: true } : { product_id: target }).then(r => r.data)
