import client from './client'

// POP MART 价格 POP MART prices (owner, 2026-11-25, popcore-work#91): POPCORE reads POP MART Canada and US every
// Monday at 6:00 AM and sets their prices beside POPCORE's. Nothing here changes a price. See docs/popmart-prices.md.

export type PopmartStatus = 'same' | 'higher' | 'lower' | 'not_found' | 'no_option' | 'no_price'

// One region's POP MART product for a POPCORE product: the option that is the same thing (Single box for a box, Whole
// Set for a set), its price (a POP MART sale price when there is one) and in stock or sold out.
export interface PopmartSide {
  popmart_id: string; title: string; currency: 'CAD' | 'USD'; url: string; options: string[]
  option: string | null; price_cents: number | null; regular_cents?: number | null; sale_cents: number | null
  in_stock: boolean | null
}

export interface PopmartRow {
  product_id: number; sku: string; name: string; kind: 'box' | 'set' | 'item'; base_id: number
  our_cents: number | null; ca: PopmartSide | null; us: PopmartSide | null; status: PopmartStatus
  diff_cents: number | null; linked: boolean; linked_title: string | null; suggestions: string[]
}

export interface PopmartRead {
  region: 'ca' | 'us'; trigger: 'weekly' | 'manual'; status: 'running' | 'ok' | 'error'
  started_at: string; finished_at: string | null; products: number; options: number; error: string | null
}

export interface PopmartPriceList {
  rows: PopmartRow[]
  counts: Record<PopmartStatus, number>
  reads: Record<'ca' | 'us', { last: PopmartRead | null; good_at: string | null; products: number; options: number }>
  titles: string[]
  next_read: string
  requested: boolean
  running: boolean
}

export const getPopmartPrices = async () => (await client.get('/popmart-prices')).data as PopmartPriceList
export const readPopmartNow = async () => (await client.post('/popmart-prices/read')).data as { requested: boolean }
export const linkPopmart = async (productId: number, title: string) =>
  (await client.put(`/popmart-prices/links/${productId}`, { title })).data as { linked: boolean }
export const unlinkPopmart = async (productId: number) =>
  (await client.delete(`/popmart-prices/links/${productId}`)).data as { linked: boolean }
