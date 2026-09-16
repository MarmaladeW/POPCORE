export type DiscountQuote = {
  discountCents: number
  predictedTotalCents: number
  savingsCents: number
  warning: boolean
}

const safeNonnegative = (value: number) => Number.isSafeInteger(value) && value >= 0

const roundRatio = (numerator: bigint, denominator: bigint) =>
  (numerator * 2n + denominator) / (denominator * 2n)

export function suggestPayable(totalCents: number): number {
  if (!safeNonnegative(totalCents)) throw new RangeError('Total cents must be a nonnegative safe integer.')
  if (totalCents === 0) return 0

  const total = BigInt(totalCents)
  if (totalCents < 100) return Number((total * 390n + 199n) / 400n) || 1

  let suggestion = Number((total * 1950n + 99_999n) / 200_000n) * 100
  if (suggestion >= totalCents) suggestion -= 100
  return Math.max(1, suggestion)
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
