import client from './client'

export type MatchDecision = 'merge' | 'keep' | 'delete' | null
export type MatchStatus = 'undecided' | 'ready' | 'keep' | 'applied' | 'missing'

export interface CloverMatch {
  legacy_product_id: number
  legacy_sku: string | null
  legacy_jizhanming: string | null
  legacy_name: string | null
  legacy_price: number | null
  references: number | null
  decision: MatchDecision
  target_product_id: number | null
  target_name: string | null
  target_price: number | null
  source: string
  confidence: 'high' | 'medium' | 'low' | null
  note: string | null
  updated_by: string | null
  updated_at: string
  applied_at: string | null
  applied_by: string | null
  applied_outcome: string | null
  status: MatchStatus
}

export interface CloverProductOption { id: number; name: string; price: number | null; brand: string | null; search_text?: string }

export interface CloverMatches { items: CloverMatch[]; clover_products: CloverProductOption[]; unlisted_legacy: number }

export interface ApplyResult { legacy_product_id: number; status: 'applied' | 'refused'; message: string }

export const getCloverMatches = (signal?: AbortSignal) =>
  client.get<CloverMatches>('/catalog/clover-matches', { signal }).then(response => response.data)

export const updateCloverMatch = (legacyId: number, decision: MatchDecision, targetProductId: number | null) =>
  client.put<CloverMatch>(`/catalog/clover-matches/${legacyId}`, { decision, target_product_id: targetProductId })
    .then(response => response.data)

export const applyCloverMatches = (legacyIds: number[]) =>
  client.post<{ results: ApplyResult[] }>('/catalog/clover-matches/apply', { legacy_product_ids: legacyIds })
    .then(response => response.data.results)
