import { formatCents, parseMoneyToCents } from '../../lib/money.ts'

// The server prices every Clover order; these helpers only word and check what it sent.

export type CloverCollect = { due_cents: number; mode: string | null; label: string | null; rest_cents: number }
export type CloverLine = {
  name: string; quantity: number; unit: string; unit_price_cents: number | null; item_code: string | null
  product_id: number | null; product_name: string | null; mapped: boolean
}
export type CloverBlock = {
  order_id: string; merchant_id: string; created_ms: number; state: string; state_label: string
  source: 'cloud' | 'device'; seen_at: number; fresh: boolean; employee_name: string | null
  register: boolean; register_name: string; popcore_reference: string; total_cents: number | null
  lines: CloverLine[]; discounts: { name: string; amount_cents: number | null; percentage: number | null; popcore: boolean }[]
  popcore_discount_label: string | null; pricing_verified: boolean; plan: 'pay' | 'split_card' | null
  pricing_mode: string | null; due_cents: number | null; clover_cents: number; popcore_cents: number
  remaining_cents: number; collect: { pay: CloverCollect; split_card: CloverCollect; card: { due_cents: number; rest_cents: number } } | null
  undo_attempt_ids: number[]; card_blocked: boolean
  writeback: { state: 'none' | 'pending' | 'failed' | 'done'; error: string | null; mode?: 'label' | 'pay'; label?: string | null }
  review: { reason: string; detail: string } | null; delete_pending: boolean
  can_claim: boolean; can_release: boolean; can_receive: boolean; can_cancel: boolean
  can_cancel_local: boolean; can_retry: boolean; can_mark_handled: boolean
}

export const POPCORE_TENDERS = ['cash', 'e_transfer', 'wechat', 'alipay'] as const

/** Clover keeps one line per unit; show identical units once with their count. */
export function groupLines(lines: CloverLine[]) {
  const groups: (CloverLine & { key: string })[] = []
  for (const line of lines) {
    const key = [line.name, line.unit_price_cents, line.product_id, line.item_code, line.unit].join('|')
    const group = line.quantity === 1 ? groups.find(g => g.key === key) : undefined
    if (group) group.quantity += 1
    else groups.push({ ...line, key: line.quantity === 1 ? key : `${key}|${groups.length}` })
  }
  return groups
}

/** What the cashier taps on the Clover device for the card part of a split. */
export function splitInstruction(cardCents: number) {
  return `Pay → ⋮ → Split Custom Amount → ${formatCents(cardCents)} → Card`
}

export function stateTone(state: string) {
  if (state === 'PAID') return 'paid'
  if (state === 'PARTIALLY_PAID') return 'partial'
  if (state === 'DELETED') return 'deleted'
  if (state.includes('REFUND') || state === 'CREDITED') return 'refunded'
  return 'open'
}

/** Money typed by the cashier, or null when it is not a usable amount. */
export function centsOrNull(value: string) {
  try {
    const cents = parseMoneyToCents(value)
    return cents === null || cents <= 0 ? null : cents
  } catch {
    return null
  }
}

/** The card part of a split: more than nothing, less than the split total. */
export function cardPart(value: string, splitDue: number) {
  const cents = centsOrNull(value)
  if (cents === null) return { cents: null, error: '' }
  if (cents >= splitDue) return { cents: null, error: `The card part must be less than ${formatCents(splitDue)}. Use Card for the whole order.` }
  return { cents, error: '' }
}

const REVIEWS: Record<string, string> = {
  overpaid: 'More money was received than the customer owes.',
  tender_unknown: 'Clover took a payment with a tender POPCORE does not know.',
  clover_deleted: 'The order was deleted on Clover after money was recorded.',
  clover_refunded: 'Clover recorded a refund, void or credit after the sale was recorded.',
  clover_payment_added: 'Clover took another payment after the sale was recorded.',
  clover_changed: 'The order changed on Clover after the sale was recorded.',
}

export function reviewTitle(reason: string) {
  return REVIEWS[reason] || 'This order needs a manager’s review.'
}

/** How the Clover side of a finished sale stands. */
export function writebackStatus(block: CloverBlock, orderStatus: string) {
  if (orderStatus !== 'completed') return null
  const { state, error, mode, label } = block.writeback
  if (mode === 'label') {
    // Owner, 2026-10-20: money taken outside Clover leaves the Clover order open, titled "Paid <date> <time>".
    switch (state) {
      case 'done': return { tone: 'success' as const, title: 'Labelled paid on Clover', detail: `The Clover order is titled “${label}” and stays open there.` }
      case 'pending': return { tone: 'info' as const, title: 'Labelling the Clover order…', detail: 'The sale is recorded in POPCORE.' }
      case 'failed': return { tone: 'warning' as const, title: 'Not labelled on Clover yet', detail: `${error || 'Clover did not confirm the update.'} The sale is recorded in POPCORE; the store day cannot close until the Clover order is labelled.` }
      default: return block.popcore_cents
        ? { tone: 'warning' as const, title: 'Clover not labelled', detail: 'This POPCORE copy only reads Clover. Title the Clover order on the device.' }
        : { tone: 'success' as const, title: 'Paid on Clover', detail: 'Clover took the payment.' }
    }
  }
  switch (state) {
    case 'done': return { tone: 'success' as const, title: 'Paid on Clover', detail: `Clover shows this order as paid${label ? `, titled “${label}”` : ''}.` }
    case 'pending': return { tone: 'info' as const, title: 'Marking the Clover order paid…', detail: 'The sale is recorded in POPCORE.' }
    case 'failed': return { tone: 'warning' as const, title: 'Not on Clover yet', detail: `${error || 'Clover did not confirm the update.'} The sale is recorded in POPCORE; the store day cannot close until Clover matches.` }
    default: return block.popcore_cents
      ? { tone: 'warning' as const, title: 'Clover not updated', detail: 'This POPCORE copy only reads Clover. Mark the Clover order paid on the device.' }
      : { tone: 'success' as const, title: 'Paid on Clover', detail: 'Clover took the payment.' }
  }
}

export type AutoOpenInput = { enabled: boolean; idle: boolean; visible: boolean; known: Set<number>; orders: { id: number; status: string; clover?: CloverBlock | null }[] }

/** The newly scanned Clover order an idle screen should pick up, if any (oldest first). */
export function autoOpenCandidate({ enabled, idle, visible, known, orders }: AutoOpenInput) {
  if (!enabled || !idle || !visible) return null
  const fresh = orders.filter(order => !known.has(order.id) && order.status === 'open' && order.clover?.register && order.clover.can_claim)
  return fresh.length ? fresh.reduce((oldest, order) => order.id < oldest.id ? order : oldest).id : null
}
