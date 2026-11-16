// Restock quantities are in each product's own stock unit; an unreviewed product has none yet.
const UNIT_NAMES: Record<string, string> = { set: '端', box: '盒', piece: '个' }

export function withUnit(quantity: number | null | undefined, unit?: string | null) {
  return unit ? `${quantity ?? 0} ${UNIT_NAMES[unit] ?? unit}` : `${quantity ?? 0}`
}

// The most one line can carry: the sheet's own limit, which the picker and the floor count share.
export const MAX_QUANTITY = 999

// 整端 stay upstairs and the floor holds blind boxes as boxes (owner, 2026-10-29): a whole set that
// comes down is put on the floor as its boxes, "1 端 = 12 盒". Empty for anything that is not a set.
export function setAsBoxes(sets: number | null | undefined, boxesPerSet?: number | null) {
  return boxesPerSet && sets && sets > 0 ? `${sets} 端 = ${sets * boxesPerSet} 盒` : ''
}
