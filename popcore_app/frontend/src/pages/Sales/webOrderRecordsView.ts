// 网站订单记录 Web order records (owner, 2026-11-28, popcore-work#157): every paid web order with its details, for the
// admin. On 每日汇总 as their own line beside the store's in-store orders; on 销售 → 网站订单 by month or search.

export type WebOrderStatus = 'open' | 'packed' | 'ready' | 'out' | 'done' | 'cancelled' | 'refunded'
export type WebOrderLine = { name: string; quantity: number; total_cents: number | null; popcore_id: string | null; popcore_name: string | null }
export type WebOrderRecord = {
  id: number; number: string; paid_at: string; day: string; kind: 'pickup' | 'delivery'
  stores: string[]; taken_from: string[]; status: WebOrderStatus; waiting: boolean; settled: string | null
  total_cents: number | null; tax_cents: number | null; payment: string | null; counted: boolean; pieces: number
  lines: WebOrderLine[]; closed_at: string | null; packed_at?: string | null; packed_by?: string | null
}
export type WebOrderTotals = { orders: number; cents: number; pieces: number; not_counted: number; pickup: number; delivery: number }
export type WebOrderRecords = { orders: WebOrderRecord[]; totals: WebOrderTotals; month?: string | null; query?: string | null }

/** Where an order stands, in the board's words (自取 待拣货 / 配送 待打包 while it is open). */
export function statusText(order: Pick<WebOrderRecord, 'status' | 'kind'>) {
  switch (order.status) {
    case 'open': return order.kind === 'pickup' ? '待拣货' : '待打包'
    case 'packed': return '已打包'
    case 'ready': return '待取货'
    case 'out': return '派送中'
    case 'done': return '已完成'
    case 'cancelled': return '已取消'
    case 'refunded': return '已退款'
  }
}

/** "自取 DT", "配送 MK"; a delivery whose stock is still in two stores: "配送 DT + MK". */
export const kindText = (order: Pick<WebOrderRecord, 'kind' | 'stores'>) =>
  `${order.kind === 'pickup' ? '自取' : '配送'} ${order.stores.join(' + ')}`

/** The stores its stock came from, when that is not where it is handed over: "货从 MK". */
export function takenText(order: Pick<WebOrderRecord, 'stores' | 'taken_from'>) {
  const elsewhere = order.taken_from.filter(code => !order.stores.includes(code))
  return elsewhere.length ? `货从 ${elsewhere.join(' + ')}` : ''
}

/** "9 单 · 自取 5 · 配送 4" — only the kinds the day had. */
export function countText(totals: WebOrderTotals) {
  return [`${totals.orders} 单`, totals.pickup ? `自取 ${totals.pickup}` : '', totals.delivery ? `配送 ${totals.delivery}` : '',
    totals.not_counted ? `${totals.not_counted} 单取消不计` : ''].filter(Boolean).join(' · ')
}

/** Orders by Toronto day of payment, newest day first, each with its own total. */
export function byDay(orders: WebOrderRecord[]) {
  const days = new Map<string, WebOrderRecord[]>()
  for (const order of orders) days.set(order.day, [...(days.get(order.day) || []), order])
  return [...days].sort(([a], [b]) => b.localeCompare(a)).map(([day, list]) => {
    const counted = list.filter(order => order.counted)
    return { day, orders: list, count: counted.length, cents: counted.reduce((sum, order) => sum + (order.total_cents || 0), 0) }
  })
}

/** ‹ 2026年11月 › */
export function monthTitle(month: string) {
  const [year, number] = month.split('-')
  return `${year}年${Number(number)}月`
}

export function shiftMonth(month: string, by: number) {
  const [year, number] = month.split('-').map(Number)
  const date = new Date(Date.UTC(year, number - 1 + by, 1))
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`
}
