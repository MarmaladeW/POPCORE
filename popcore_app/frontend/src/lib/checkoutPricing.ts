export type DiscountQuote = {
  discountCents: number
  predictedTotalCents: number
  savingsCents: number
  warning: boolean
}

const safeNonnegative = (value: number) => Number.isSafeInteger(value) && value >= 0

const roundRatio = (numerator: bigint, denominator: bigint) =>
  (numerator * 2n + denominator) / (denominator * 2n)

export function convertCadCentsToCnyFen(cadCents: number, rate: string): number | null {
  if (!safeNonnegative(cadCents) || !/^\d{1,3}(?:\.\d{1,4})?$/.test(rate)) return null
  const [whole, fraction = ''] = rate.split('.')
  const scaled = BigInt(whole) * 10_000n + BigInt(fraction.padEnd(4, '0') || '0')
  if (scaled === 0n || scaled > 1_000_000n) return null
  const fen = (BigInt(cadCents) * scaled + 5_000n) / 10_000n
  return fen <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(fen) : null
}

export const HST_PERCENT = 13

/** Ontario HST on a subtotal, rounded half up to the cent (as Clover rounds tax). */
export function hstCents(subtotalCents: number): number {
  if (!safeNonnegative(subtotalCents)) throw new RangeError('Subtotal cents must be a nonnegative safe integer.')
  return Number((BigInt(subtotalCents) * BigInt(HST_PERCENT) + 50n) / 100n)
}

/** The cash price, for cash, e-transfer, WeChat Pay and Alipay, before cash is rounded to 5¢ (mirrors
 *  clover_pricing.suggest_payable). Under $1: about 2.5% off. $1 to $9.99: down to the previous whole dollar
 *  ($5.10 → $5, $9.99 → $9). $10 to $15: the nearest whole dollar to 2.5% off, ties down, below the total.
 *  Above $15: at least $1 off, or 2.5% when that is more, rounded down. */
export function suggestPayable(totalCents: number): number {
  if (!safeNonnegative(totalCents)) throw new RangeError('Total cents must be a nonnegative safe integer.')
  if (totalCents === 0) return 0

  const total = BigInt(totalCents)
  if (totalCents < 100) return Number((total * 390n + 199n) / 400n) || 1
  if (totalCents < 1000) return totalCents % 100 === 0 ? wholeDollarUnderTen(totalCents) : totalCents - totalCents % 100
  if (totalCents > 1500) {
    // Above $15: at least $1 off, or 2.5% when that is more, rounded down to the dollar.
    const percent = total * 975n / 100_000n, dollar = (total - 100n) / 100n
    return Number(percent < dollar ? percent : dollar) * 100
  }

  let suggestion = Number((total * 1950n + 99_999n) / 200_000n) * 100
  if (suggestion >= totalCents) suggestion -= 100
  return Math.max(1, suggestion)
}

/** A whole-dollar total from $1 to $9 keeps its price: $5.00 stays $5.00 (owner, 2026-10-27). The owner may
 *  switch this to one dollar less; change it here and in clover_pricing.whole_dollar_under_ten. */
export function wholeDollarUnderTen(totalCents: number): number {
  return totalCents
}

/** Canadian cash rounding to 5¢: ending in 1–2 goes down to 0, 3–4 up to 5, 6–7 down to 5, 8–9 up to 10. */
export function nickel(cents: number): number {
  if (!Number.isSafeInteger(cents)) throw new RangeError('Cents must be a safe integer.')
  return Math.floor((cents + 2) / 5) * 5
}

/** What the customer hands over in cash for the cents still owed (mirrors clover_pricing.cash_rest).
 *  A rest of 1–2¢ would round to nothing, so it stays exact. */
export function cashRest(restCents: number): number {
  return nickel(restCents) || restCents
}

/** What the whole order costs paid one way. Cash alone is then rounded to 5¢; in a split only the cash part
 *  is rounded, when it is collected. */
export function suggestPaymentTarget(totalCents: number, tender: string, mixedWithCard: boolean): number {
  const discounted = suggestPayable(totalCents)
  if (mixedWithCard) return totalCents < 1000 ? totalCents : totalCents - totalCents % 100
  if (tender === 'card') return totalCents
  return tender === 'cash' ? cashRest(discounted) : discounted
}

export function discountQuote(
  subtotalCents: number,
  taxCents: number,
  targetCents: number,
): DiscountQuote | null {
  if (![subtotalCents, taxCents, targetCents].every(safeNonnegative)) return null
  if (targetCents <= 0 || subtotalCents > Number.MAX_SAFE_INTEGER - taxCents) return null

  const originalTotalCents = subtotalCents + taxCents
  if (targetCents > originalTotalCents || subtotalCents === 0) return null

  const subtotal = BigInt(subtotalCents)
  const tax = BigInt(taxCents)
  const total = BigInt(originalTotalCents)
  const idealRemaining = BigInt(targetCents) * subtotal / total
  let best: { remaining: bigint; predicted: bigint } | null = null

  for (let offset = -2n; offset <= 3n; offset += 1n) {
    const remaining = idealRemaining + offset
    if (remaining < 0n || remaining > subtotal) continue
    const predicted = remaining + roundRatio(remaining * tax, subtotal)
    const distance = predicted > BigInt(targetCents)
      ? predicted - BigInt(targetCents)
      : BigInt(targetCents) - predicted
    const bestDistance = best == null
      ? null
      : best.predicted > BigInt(targetCents)
        ? best.predicted - BigInt(targetCents)
        : BigInt(targetCents) - best.predicted
    if (best == null || distance < bestDistance! || (distance === bestDistance && predicted < best.predicted)) {
      best = { remaining, predicted }
    }
  }

  if (best == null) return null
  const predictedTotalCents = Number(best.predicted)
  const savingsCents = originalTotalCents - targetCents
  return {
    discountCents: subtotalCents - Number(best.remaining),
    predictedTotalCents,
    savingsCents,
    warning: BigInt(savingsCents) * 5n > total,
  }
}
