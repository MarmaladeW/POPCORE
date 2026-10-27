// Restock quantities are in each product's own stock unit; an unreviewed product has none yet.
const UNIT_NAMES: Record<string, string> = { set: '端', box: '盒', piece: '个' }

export function withUnit(quantity: number | null | undefined, unit?: string | null) {
  return unit ? `${quantity ?? 0} ${UNIT_NAMES[unit] ?? unit}` : `${quantity ?? 0}`
}
