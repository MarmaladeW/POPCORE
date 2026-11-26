// 记一笔 Quick sale (owner, 2026-11-21): the 随手记 a cashier keeps, recorded in POPCORE. The server prices every cart
// (quick_sale.py); these helpers only keep the cart and say what to show.

export type Tender = 'cash' | 'e_transfer' | 'wechat' | 'alipay' | 'card'
// The names staff use (owner, 2026-11-21: show what's needed, reduce noise), with an English name for screen readers.
export const TENDERS: { value: Tender; zh: string; en: string }[] = [
  { value: 'cash', zh: '现金', en: 'Cash' },
  { value: 'e_transfer', zh: 'E-transfer', en: 'E-transfer' },
  { value: 'wechat', zh: '微信', en: 'WeChat Pay' },
  { value: 'alipay', zh: '支付宝', en: 'Alipay' },
  { value: 'card', zh: '刷卡', en: 'Card machine' },
]
// A photo of the payment is asked for these (owner: saved without one, the sale says so).
export const PHOTO_TENDERS: Tender[] = ['e_transfer', 'wechat', 'alipay']
export const tenderName = (tender: string) => TENDERS.find(entry => entry.value === tender)?.zh ?? tender
export const CNY_TENDERS: Tender[] = ['wechat', 'alipay']

/** What Save says while it cannot save yet: the one thing still missing. */
export function waitingFor(quote: Quote | undefined, tender: Tender | ''): string {
  if (!quote) return '算价中…'
  if (quote.missing_prices.length) return '先填价格'
  if (!tender) return '选付款方式'
  if (CNY_TENDERS.includes(tender) && !quote.cny) return '没有汇率'
  return ''
}

export type CartItem = {
  productId: number; name: string; sku: string; quantity: number; fromDisplay: boolean; priceCents?: number
}
export type QuoteLine = {
  product_id: number; sku: string; name: string; quantity: number; from_display: boolean
  unit_price_cents: number | null; list_price_cents: number | null; price_from: 'typed' | 'product' | 'series' | null
  line_cents: number | null
}
export type Quote = {
  store_id: number; business_date: string; lines: QuoteLine[]; missing_prices: string[]
  discount_cents: number; tenders: Record<Tender, number> | null
  /** Who buys with a staff discount, when the quote was asked with one: the server checks they have a use left. */
  staff_discount_employee_id?: number | null
  subtotal_cents?: number; tax_cents?: number; total_cents?: number; price_label?: string | null
  other_due_cents?: number; cny?: { rate: string; source: string; fen: number } | null
}
export type SalePayment = { id: number; tender: Tender; amount_cents: number; cny_fen: number | null; photos: number; needs_photo: boolean }
export type SaleRow = {
  id: number; reference: string; store_id: number; business_date: string; created_at: string; status: string
  sale_id: number | null; cashier: string; total_cents: number; discount_cents: number; card: boolean
  lines: { product_id: number | null; name: string; quantity: number; from_display: boolean }[]
  payments: SalePayment[]; needs_photo: boolean; can_add_photo: boolean; quick: boolean
  // 员工折扣: the staff member who bought it.
  staff_discount: string | null
  // 撤销: taken back as recorded by mistake (kept, left out of totals and the 日报 summary).
  undone: null | { at: string; by: string; reason: string }; can_undo: boolean; report_saved: boolean
}
/** Where a day stands: its stock comes off once, when a manager reviews it here (审核). */
export type DayReview = {
  state: 'none' | 'reviewed' | 'changed' | 'pasted'; by: string | null; at: string | null
  // A day whose 日报 was pasted instead: where the pasted sales and 记一笔 disagree.
  differs: { product_id: number; name: string; quick: number; report: number }[]
  // The cash the manager counted that day, if they wrote it down.
  cash_counted_cents: number | null
}
export type SalesDay = {
  store_id: number; business_date: string; today: string; sales: SaleRow[]; summary_text: string; needs_photo: number
  total_cents: number; cash_cents: number; count: number; review: DayReview; can_review: boolean; unreviewed_days: string[]
}
/** What a review would move (the same result a saved 日报 gave). */
export type ReviewStock = {
  taken: number; returned: number
  short: { product_id: number; name: string; quantity: number }[]
  sets_opened?: { product_id: number; text: string }[]
  lines?: { line_key: string; label: string; moving: number; short: number; from?: { name: string } | null }[]
}

/** "11/22" for a day. */
export const shortDay = (day: string) => `${Number(day.slice(5, 7))}/${Number(day.slice(8, 10))}`

const same = (item: CartItem, productId: number, fromDisplay: boolean) => item.productId === productId && item.fromDisplay === fromDisplay

/** Add one of a product; the same product (from the same place) only counts up. */
export function addToCart(cart: CartItem[], product: { id: number; name: string; sku: string }): CartItem[] {
  if (cart.some(item => same(item, product.id, false)))
    return cart.map(item => same(item, product.id, false) ? { ...item, quantity: Math.min(999, item.quantity + 1) } : item)
  return [...cart, { productId: product.id, name: product.name, sku: product.sku, quantity: 1, fromDisplay: false }]
}

export function changeItem(cart: CartItem[], index: number, change: Partial<CartItem>): CartItem[] {
  const next = cart.map((item, at) => at === index ? { ...item, ...change } : item)
  const item = next[index]
  // Two lines of the same product from the same place become one.
  if (item && change.fromDisplay !== undefined) {
    const twin = next.findIndex((other, at) => at !== index && same(other, item.productId, item.fromDisplay))
    if (twin >= 0) {
      next[twin] = { ...next[twin], quantity: Math.min(999, next[twin].quantity + item.quantity) }
      next.splice(index, 1)
    }
  }
  return next
}

export const removeItem = (cart: CartItem[], index: number) => cart.filter((_, at) => at !== index)

/** The request lines for /quick-sales/quote and /quick-sales. */
export function requestLines(cart: CartItem[]) {
  return cart.map(item => ({
    product_id: item.productId, quantity: item.quantity,
    ...(item.fromDisplay ? { from_display: true } : {}),
    ...(item.priceCents ? { unit_price_cents: item.priceCents } : {}),
  }))
}

/** Whether a quote can be saved with this tender: every price known and, for WeChat Pay and Alipay, a CNY rate. */
export function canSave(quote: Quote | undefined, tender: Tender | ''): boolean {
  if (!quote || !tender || !quote.tenders || quote.missing_prices.length) return false
  return !((tender === 'wechat' || tender === 'alipay') && !quote.cny)
}

/** One sale as the 日报 writes it: "SA Santa*2，蒙奇奇*1（display）". */
export const itemsText = (lines: SaleRow['lines']) =>
  lines.map(line => `${line.name}*${line.quantity}${line.from_display ? '（display）' : ''}`).join('，')

/** Keeps one Idempotency-Key per save of the same cart and tender, so a retry after a dropped connection is the
 *  same sale, and anything else (a changed cart, an answer from the server) starts a new one. */
export class SaveKey {
  private body = ''
  private key = ''
  private readonly make: () => string
  constructor(make: () => string = () => crypto.randomUUID()) { this.make = make }
  for(body: string): string {
    if (body !== this.body || !this.key) { this.body = body; this.key = this.make() }
    return this.key
  }
  reset() { this.body = ''; this.key = '' }
}
