import client from './client'

// 盘点 Count: one card per product at one location (popcore_app/stock_counting.py).
export type CountRole = 'box' | 'item' | 'set' | 'design'
export type CountForm = 'random_box' | 'ordinary' | 'sealed_set' | 'confirmed_design'

export interface CountLocation { id: number; code: string; name: string; counted: number }
export interface CountScope { store: { id: number; code: string; name: string }; live: boolean; locations: CountLocation[] }

export interface CountResult {
  product_id: number
  title: string
  sku: string | null
  kind: CountRole | 'series'
  sets: number
  designs: number
  members: number
  counted: number
  image: string | null
}

export interface CountItem {
  product_id: number
  role: CountRole
  sku: string
  name: string
  design_name: string | null
  stock_form: CountForm | null
  stock_unit: 'box' | 'set' | 'piece' | null
  identity_status: 'verified' | 'unverified'
  image: string | null
  boxes_per_set?: number
  suggested_form?: CountForm | null
  // An unverified product already counted in this store keeps that form (going live verifies one).
  counted_form?: CountForm
  balance_version?: number
  // A live store's held or damaged units here, which a count of sellable stock leaves out.
  other_stock?: Array<{ disposition: string; quantity: number }>
  // version: the saved count this card shows, so a save notices someone else's newer count.
  count?: { quantity: number; counted_at: string; counted_by: string; version: number }
}

export interface CountCard {
  live: boolean
  location: { id: number; code: string; name: string }
  product_id: number
  title: string
  series_id: number | null
  designs_checked: { at: string; by: string } | null
  items: CountItem[]
  // A blind box with no whole set yet: saving a whole-set count creates it; 0 whole sets keeps only boxes_per_set.
  new_sealed_set: { boxes_per_set: number | null } | null
}

export interface CountEntry {
  product_id: number; quantity: number; stock_form?: CountForm; balance_version?: number; count_version?: number | null
}
export interface SavedCount { product_id: number; quantity: number; previous_count: number | null; popcore_quantity: number | null }
// boxes: a blind box with whole sets, compared as all its boxes here (POPCORE kept whole sets as boxes).
export interface SavedCard { live: boolean; results: SavedCount[]; boxes?: { popcore: number; counted: number } }
export interface CountProgress {
  live: boolean
  counted: number
  missing: Array<{ product_id: number; name: string; sku: string; blocks_opening: boolean; recount?: boolean }>
  missing_total: number
}

export const countRequestKey = () => crypto.randomUUID()

export const getCountScope = (store: string, signal?: AbortSignal) =>
  client.get<CountScope>('/counting/scope', { params: { store_code: store }, signal }).then(response => response.data)

export const searchCount = (store: string, location: string, q: string, signal?: AbortSignal) =>
  client.get<{ results: CountResult[] }>('/counting/search', { params: { store_code: store, location, q }, signal })
    .then(response => response.data.results)

export const getCountCard = (store: string, location: string, productId: number, signal?: AbortSignal) =>
  client.get<CountCard>('/counting/card', { params: { store_code: store, location, product_id: productId }, signal })
    .then(response => response.data)

export const getCountProgress = (store: string, location: string, signal?: AbortSignal) =>
  client.get<CountProgress>('/counting/progress', { params: { store_code: store, location }, signal })
    .then(response => response.data)

export async function saveCount(
  body: {
    store_code: string; location: string; product_id: number; entries: CountEntry[]; designs_checked?: boolean
    new_sealed_set?: { quantity: number; boxes_per_set: number }
  },
  key: string,
) {
  return (await client.post('/counting/counts', body, { headers: { 'Idempotency-Key': key } })).data as SavedCard
}

export async function addDesigns(productId: number, names: string[], key: string) {
  return (await client.post(`/products/${productId}/designs`, { design_names: names }, { headers: { 'Idempotency-Key': key } })).data
}

export async function removeDesign(designId: number) {
  return (await client.post(`/products/${designId}/design/remove`)).data
}
