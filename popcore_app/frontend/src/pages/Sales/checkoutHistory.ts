// What an order-history card says about money: what was actually paid, and how.

export const TENDER_LABELS: Record<string, string> = {
  cash: 'Cash', card: 'Card', e_transfer: 'E-transfer', wechat: 'WeChat Pay', alipay: 'Alipay',
}
export const TENDER_FILTERS = Object.entries(TENDER_LABELS).map(([value, label]) => ({ value, label }))

type Charged = { cny_fen?: number | null; cny_per_cad?: string | null }
type Attempt = Charged & { tender: string; amount_cents: number; status: string }

const CNY = new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' })
const CNY_TENDERS = new Set(['wechat', 'alipay'])

/** The yuan a WeChat Pay or Alipay payment charged and its rate; '' when the payment kept none. */
export function cnyCharged(payment: Charged) {
  return payment.cny_fen == null || !payment.cny_per_cad ? ''
    : `${CNY.format(payment.cny_fen / 100)} charged at 1 CAD = ${payment.cny_per_cad} CNY`
}

/** Money actually received (completed payments only) and the methods used, in the order they were paid.
 *  WeChat Pay and Alipay also give the yuan they charged. */
export function paymentSummary(attempts: Attempt[]) {
  const paid = attempts.filter(attempt => attempt.status === 'completed')
  const methods = [...new Set(paid.map(attempt => attempt.tender))].map(tender => {
    const name = TENDER_LABELS[tender] || tender
    const same = paid.filter(attempt => attempt.tender === tender)
    if (!CNY_TENDERS.has(tender) || same.some(attempt => attempt.cny_fen == null)) return name
    return `${name} ${CNY.format(same.reduce((total, attempt) => total + (attempt.cny_fen ?? 0), 0) / 100)}`
  })
  return { cents: paid.reduce((total, attempt) => total + attempt.amount_cents, 0), methods }
}

const NEEDS_PHOTO = new Set(['e_transfer', 'wechat', 'alipay'])

/** Received e-transfer, WeChat Pay and Alipay payments that still have no payment photo. */
export function missingPhotos(attempts: (Attempt & { photos?: unknown[] })[]) {
  return attempts.filter(attempt => attempt.status === 'completed' && NEEDS_PHOTO.has(attempt.tender)
    && !(attempt.photos && attempt.photos.length)).length
}

/** The search text sent to the server: trimmed, at most 80 characters, empty when blank. */
export function historyQuery(value: string) {
  return value.trim().slice(0, 80)
}
