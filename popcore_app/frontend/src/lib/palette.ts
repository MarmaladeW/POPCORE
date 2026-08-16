/** Curated employee color palette — 12 hue-spaced, calendar-friendly colors.
 *  All are dark enough that white text stays legible on a solid block.
 *  Keep in sync with the assign_curated_employee_colors migration (db.py). */
export const EMPLOYEE_PALETTE = [
  '#3D74C4', // blue
  '#2E7FA3', // steel
  '#2C8A86', // teal
  '#2E8A5B', // emerald
  '#5E8A32', // green
  '#8B7C2A', // olive
  '#C0762F', // amber
  '#C75B54', // terracotta
  '#B4508F', // rose
  '#9455C8', // purple
  '#5F63C2', // iris
  '#8A5A3C', // brown
]

/** Readable text color (dark ink or white) for an arbitrary hex background —
 *  keeps labels legible even on colors picked before the curated palette. */
export function textColorOn(hex: string): string {
  let m = /^#?([0-9a-f]{6})$/i.exec((hex || '').trim())
  if (!m) {
    const short = /^#?([0-9a-f]{3})$/i.exec((hex || '').trim())
    if (!short) return '#ffffff'
    m = [short[0], short[1].split('').map(c => c + c).join('')] as unknown as RegExpExecArray
  }
  const n = parseInt(m[1], 16)
  const r = (n >> 16) & 255
  const g = (n >> 8) & 255
  const b = n & 255
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
  return lum > 0.62 ? '#1f2937' : '#ffffff'
}
