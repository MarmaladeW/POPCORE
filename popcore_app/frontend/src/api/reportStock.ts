import client from './client'

// A live store's daily report takes its sales off the floor; what it could not, or may have taken
// twice, waits here for a manager or admin (owner, 2026-10-29).
export type ReportStockReason = 'not_counted' | 'not_enough_on_floor' | 'set_size_unknown' | 'counted_same_day'
export interface ReportStockNotice {
  id: number; store_id: number; store_code: string; business_date: string; product_id: number
  sku: string | null; name: string; unit: string; reason: ReportStockReason; quantity: number
  counted_at: string | null; floor: number; upstairs: number; updated_at: string
}
export interface ReportStockResult {
  business_date: string; documents: number[]; taken: number; returned: number
  short: Array<{ product_id: number; sku: string | null; name: string; quantity: number; reason: ReportStockReason }>
  counted_same_day: Array<{ product_id: number; sku: string | null; name: string; quantity: number; counted_at: string }>
}

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
}

export const getReportStockNotices = (store_code: string, signal?: AbortSignal) =>
  client.get<{ notices: ReportStockNotice[] }>('/sales/report-stock', { params: { store_code }, signal }).then(r => r.data.notices)
export const checkReportStockNotice = (id: number) => client.post(`/sales/report-stock/${id}/check`).then(r => r.data)
export const retryReportStock = (store_code: string, date: string) =>
  client.post<{ ok: boolean; stock: ReportStockResult | null }>('/sales/report-stock/retry', { store_code, date }).then(r => r.data.stock)
