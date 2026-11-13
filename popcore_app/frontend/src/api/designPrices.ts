import client from './client'
import type { PriceReason } from './website'

// 确认款价格 Design prices (owner, 2026-11-12): a manager sets confirmed designs' prices in bulk, and the website
// sync sends them on. A design is created with its box's price; only a price set here (or in the design's own
// editor) goes to popcore.ca. See docs/website-sync.md.

// A design's website item. base_cents is the design's own website price, the one a jump is measured from:
// null when it has none there (a draft, which mostly carries its box's price, or no price).
export interface DesignWebItem {
  wc_id: number; name: string; status: string; published: boolean
  regular_cents: number | null; sale_cents: number | null; base_cents: number | null
}
export interface DesignPriceRow {
  id: number; sku: string; name: string; box_id: number | null; price_cents: number | null
  // set: a manager set this price; unset: it still carries what it was created with (its box's price);
  // elsewhere: a price was set, then changed some other way, so it does not go to the website.
  state: 'set' | 'unset' | 'elsewhere'; set_at: string | null; set_by: string | null; changed_today: boolean
  secret: boolean; web: DesignWebItem[]
}
export interface DesignBox {
  id: number | null; sku: string | null; name: string; full_name: string | null; price_cents: number | null
  ip: string | null; brand: string | null
}
export interface PriceLink { configured: boolean; enabled: boolean; refreshed_at: string | null }
export interface DesignPriceList {
  boxes: DesignBox[]; designs: DesignPriceRow[]; today: string; look_pct: number; website: PriceLink
}
export interface DesignPriceChange { product_id: number; cents: number; expected_cents: number | null }
// What the website sync does with a design's new price: send it, wait for a manager's OK, wait for the Daily
// Shop's reset, nothing (the website has it already), or leave it.
export interface DesignEffect {
  wc_id: number; name: string; status: string; published: boolean; regular_cents: number | null
  sale_cents: number | null; price: number | null; state: 'send' | 'held' | 'daily' | 'same' | 'left'
  first: boolean; reasons: PriceReason[]
}
export interface DesignPriceSummary {
  changes: Array<{ product_id: number; sku: string; name: string; box_name: string | null; old_cents: number | null
    new_cents: number; first: boolean; box_cents: number | null; website: DesignEffect[] }>
  counts: { changes: number; first: number; website_first: number; send: number; daily: number; held: number
    same: number; not_linked: number }
  website: PriceLink
  saved?: number
}

export const getDesignPrices = async () => (await client.get('/design-prices')).data as DesignPriceList
export const checkDesignPrices = async (changes: DesignPriceChange[]) =>
  (await client.post('/design-prices/check', { changes })).data as DesignPriceSummary
export const saveDesignPrices = async (changes: DesignPriceChange[], key: string) =>
  (await client.post('/design-prices', { changes }, { headers: { 'Idempotency-Key': key } })).data as DesignPriceSummary
