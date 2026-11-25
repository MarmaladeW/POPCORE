import client from './client'

// A live store's daily report takes its sales off the floor; what it could not, or may have taken
// twice, waits here for a manager or admin (owner, 2026-10-29).
export type ReportStockReason = 'not_counted' | 'not_enough_on_floor' | 'set_size_unknown' | 'counted_same_day' | 'move_short'
export interface ReportStockNotice {
  id: number; store_id: number; store_code: string; business_date: string; product_id: number
  sku: string | null; name: string; unit: string; reason: ReportStockReason; quantity: number
  counted_at: string | null; floor: number; upstairs: number; updated_at: string
  // move_short: where the line was short ('not_counted' when the product was never counted), and stock there now.
  where?: string[]; stock?: Record<string, number>
}
// One stock line of a report (出库入店, 入店, 出店, 卖display, 拆display, 入display, 娃娃机; owner, 2026-11-03;
// 入库, 出库, 出店入库, 出display入店 since 2026-11-13): what it moves now (negative: put back after a
// re-paste), what it moved before and what its source lacked. `store` is set when the side is the other
// store's (出库 goes onto the other store at once).
export interface ReportStockSide {
  product_id: number; sku: string | null; name: string; unit: string; location: string; per: number
  before: number; after: number; store?: string | null
}
export interface ReportStockLine {
  line_key: string; section: string; label: string; raw: string; kind: string; ratio: number
  wanted: number; moved_before: number; checked: number; moving: number; short: number
  problem: 'not_enough' | 'not_counted' | 'settled_short' | null; document_id: number | null
  from?: ReportStockSide; to?: ReportStockSide
  // A place counted after the report's day already shows the line (owner, 2026-11-20): `settled` is what that count
  // absorbed, `counted_later` the count day per side, `one_sided` what moved on the side nobody counted later.
  settled?: number; counted_later?: Partial<Record<'from' | 'to', string>>; one_sided?: number
  // What another app screen already recorded that day (补货, 收货, 调拨, display, 娃娃机), so the line moves less.
  covered?: number; covered_by?: string[]
}
export interface ReportStockResult {
  business_date: string; documents: number[]; taken: number; returned: number
  short: Array<{ product_id: number; sku: string | null; name: string; quantity: number; reason: ReportStockReason }>
  counted_same_day: Array<{ product_id: number; sku: string | null; name: string; quantity: number; counted_at: string }>
  // Sales a later count of the floor already shows: nothing came off for them.
  counted_later?: Array<{ product_id: number; sku: string | null; name: string; quantity: number; counted_on: string }>
  lines?: ReportStockLine[]
  from_display?: Array<{ product_id: number; sku: string | null; name: string; quantity: number }>
  // Sales already recorded in the app that day (记一笔, 买单): they took their own stock, so the report did not.
  in_app?: Array<{ product_id: number; sku: string | null; name: string; quantity: number }>
  moves_short?: Array<{ product_id: number; sku: string | null; name: string; quantity: number; where: string[] }>
  // Sealed sets the paste opened by itself because the floor ran out (negative: counted whole again).
  sets_opened?: ReportSetOpened[]
}

export interface ReportSetOpened {
  product_id: number; sku: string | null; name: string; set_product_id: number; set_sku: string | null
  location: string; sets: number; boxes: number; per: number; document_id: number; text: string
}

export { locationText, whereText } from '../lib/stockPlaces.ts'

export const reasonText: Record<ReportStockReason, { zh: string; en: string; help: string }> = {
  not_counted: {
    zh: '这家店还没盘点', en: 'Never counted at this store',
    help: 'Nothing came off. Count it on the floor in 盘点; the count already leaves these sales out, so then mark it checked.',
  },
  not_enough_on_floor: {
    zh: '楼下不够', en: 'Not enough on the floor',
    help: 'The floor had less than was sold, so the rest did not come off. If stock came down or arrived without being recorded, record it and take it off again. If you count the floor after these sales, mark it checked.',
  },
  set_size_unknown: {
    zh: '不知道每端几盒', en: 'Set size not recorded',
    help: 'A whole set sold, but how many boxes it holds was never recorded, so nothing came off. Record the set size in 产品 Products, then take it off again.',
  },
  counted_same_day: {
    zh: '当天盘点过', en: 'Floor counted the same day',
    help: 'These came off after the floor was counted that day. If the count was after these sales, they came off twice: count the floor again. If it was before opening, mark it checked.',
  },
  move_short: {
    zh: '库存没移完', en: 'Stock line not moved in full',
    help: 'A stock line on the report (出库入店, 入店, 出店, 入库, 出库, display, 娃娃机) needed more than where it comes from had, or the product was never counted here, so the rest did not move. Record or count what is missing, then take it off again. If a count after the report already shows it, mark it checked.',
  },
}

export const getReportStockNotices = (store_code: string, signal?: AbortSignal) =>
  client.get<{ notices: ReportStockNotice[] }>('/sales/report-stock', { params: { store_code }, signal }).then(r => r.data.notices)
export const checkReportStockNotice = (id: number) => client.post(`/sales/report-stock/${id}/check`).then(r => r.data)
export const retryReportStock = (store_code: string, date: string) =>
  client.post<{ ok: boolean; stock: ReportStockResult | null }>('/sales/report-stock/retry', { store_code, date }).then(r => r.data.stock)
