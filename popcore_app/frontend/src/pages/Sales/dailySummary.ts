// Daily summary view model (Figma Summary): every completed checkout under each payment method it
// used. A split order's part sits under each of its methods, marked 拆单 with the other parts.

export type SummaryCheckout = {
  id: number; reference: string; actor_name: string; created_at: string
  items: { name: string; quantity: number }[]
  payments: { tender: string; amount_cents: number }[]
}
export type SummaryCheckouts = Partial<Record<'pos' | 'non_pos' | 'split', SummaryCheckout[]>>
export type MethodPart = { order: SummaryCheckout; cents: number; others: { tender: string; cents: number }[] }
export type MethodGroup = { tender: string; zh: string; en: string; count: number; cents: number; parts: MethodPart[] }

export const SUMMARY_METHODS: Array<[string, string, string]> = [
  ['cash', '现金', 'Cash'], ['card', '刷卡', 'Card'], ['e_transfer', '转账 EMT', 'E-transfer'],
  ['wechat', '微信', 'WeChat'], ['alipay', '支付宝', 'Alipay'],
]

export function methodLabel(tender: string) {
  const known = SUMMARY_METHODS.find(([key]) => key === tender)
  return known ? { zh: known[1], en: known[2] } : { zh: tender, en: '' }
}

function totals(order: SummaryCheckout) {
  const byTender = new Map<string, number>()
  for (const payment of order.payments) byTender.set(payment.tender, (byTender.get(payment.tender) || 0) + payment.amount_cents)
  return byTender
}

export function allCheckouts(checkouts?: SummaryCheckouts) {
  return [...(checkouts?.pos || []), ...(checkouts?.non_pos || []), ...(checkouts?.split || [])]
    .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id)
}

export function methodGroups(checkouts?: SummaryCheckouts): MethodGroup[] {
  const orders = allCheckouts(checkouts)
  const extra = [...new Set(orders.flatMap(order => order.payments.map(payment => payment.tender)))]
    .filter(tender => !SUMMARY_METHODS.some(([key]) => key === tender)).sort()
  return [...SUMMARY_METHODS.map(([key]) => key), ...extra].map(tender => {
    const parts = orders.flatMap(order => {
      const byTender = totals(order)
      if (!byTender.has(tender)) return []
      const others = [...byTender].filter(([key]) => key !== tender).map(([key, cents]) => ({ tender: key, cents }))
      return [{ order, cents: byTender.get(tender)!, others }]
    })
    return { tender, ...methodLabel(tender), count: parts.length, cents: parts.reduce((sum, part) => sum + part.cents, 0), parts }
  })
}

export function itemsText(order: SummaryCheckout) {
  return order.items.map(item => `${item.name} × ${item.quantity}`).join(' · ')
}

// "拆单，另刷卡 $22.00" (phone) / "拆单 · 另刷卡 $22.00" (desktop table).
export function splitNote(part: MethodPart, separator = '，') {
  if (!part.others.length) return ''
  return `拆单${separator}${part.others.map(other => `另${methodLabel(other.tender).zh} $${(other.cents / 100).toFixed(2)}`).join('，')}`
}

export function torontoTime(timestamp: string) {
  const parsed = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(timestamp) ? timestamp : `${timestamp}Z`)
  if (Number.isNaN(parsed.getTime())) return ''
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Toronto', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(parsed)
}
