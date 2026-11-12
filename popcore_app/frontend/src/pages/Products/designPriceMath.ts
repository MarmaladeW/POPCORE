// 确认款价格 Design prices: the arithmetic behind the bulk editor (owner, 2026-11-12), kept apart so it can be tested.

export const MAX_CENTS = 100_000_000 // $1,000,000, as the design editor allows

// "45.99", "$45.99", "CA$1,299", "45" → cents; anything else (or 0) → null.
export function parseMoney(text: string): number | null {
  const cleaned = text.trim().replace(/^(?:CA\$|C\$|CAD|\$)\s*/i, '').replace(/\s*(?:CAD|\$)$/i, '')
  if (!/^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?$|^\.\d{1,2}$/.test(cleaned)) return null
  const cents = Math.round(Number(cleaned.replace(/,/g, '')) * 100)
  return cents > 0 && cents <= MAX_CENTS ? cents : null
}

export const money = (cents: number | null | undefined) => cents == null ? '' : `$${(cents / 100).toFixed(2)}`
export const plainMoney = (cents: number) => (cents / 100).toFixed(2)

// The change from one price to another, in whole percent (+36, -12).
export const percent = (from: number, to: number) => Math.round(100 * (to - from) / from)

// Raise or lower a price by a percentage or an amount, to the cent. null when it would be $0 or less.
export function adjust(cents: number, by: number, unit: '%' | '$'): number | null {
  const next = unit === '%' ? Math.round(cents * (100 + by) / 100) : cents + Math.round(by * 100)
  return next > 0 && next <= MAX_CENTS ? next : null
}

export type PasteLine = { id: string | null; text: string }

// A block copied from a spreadsheet: one line per row. A row holding a POPCORE ID (PM0123-05) and a price is
// matched by that ID; a row with only a price fills the next design down the column. Blank rows are kept,
// so a gap in the column leaves that design as it is.
export function readPaste(text: string, ids: ReadonlySet<string>): PasteLine[] {
  const lines = text.replace(/\r\n?/g, '\n').replace(/\n$/, '').split('\n')
  return lines.map(line => {
    const cells = line.split('\t').map(cell => cell.trim())
    const id = cells.find(cell => ids.has(cell.toUpperCase()))?.toUpperCase() ?? null
    const price = cells.find(cell => cell.toUpperCase() !== id && parseMoney(cell) !== null) ?? (cells.length === 1 ? cells[0] : '')
    return { id, text: price }
  })
}

// Where pasted prices go: by ID when the block names IDs, otherwise down the shown designs from `start`.
export function placePaste(lines: PasteLine[], order: readonly { id: number; sku: string }[], start: number) {
  const placed: Record<number, string> = {}
  const byId = lines.some(line => line.id)
  if (byId) {
    const bySku = new Map(order.map(row => [row.sku.toUpperCase(), row.id]))
    let missed = 0
    for (const line of lines) {
      const id = line.id && bySku.get(line.id)
      if (id && line.text) placed[id] = line.text
      else if (line.text) missed += 1
    }
    return { placed, missed, byId }
  }
  lines.forEach((line, index) => {
    const row = order[start + index]
    if (row && line.text) placed[row.id] = line.text
  })
  return { placed, missed: Math.max(0, start + lines.length - order.length), byId }
}
