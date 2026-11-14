// 图鉴 Dex price trend (owner: "we should store these data, so it becomes a price trend over time"): the prices a
// manager set for a design, drawn as a small plain SVG line (no chart library). Times are stored UTC, shown Toronto.

export interface TrendInput { at: string; cents: number }
export interface TrendDot { x: number; y: number; cents: number; at: string }
export interface Trend { line: string; dots: TrendDot[]; website: TrendDot[]; low: number; high: number }

// A stored time ('2026-11-13 18:00:00', UTC) or a day ('2026-11-02') as milliseconds; NaN when it is neither.
export function timeOf(value: string | null | undefined): number {
  if (!value) return NaN
  const text = value.trim()
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return Date.parse(`${text}T12:00:00Z`)
  const iso = text.replace(' ', 'T')
  return Date.parse(/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`)
}

// The Toronto date of a stored time, as 'Nov 13, 2026'; a bare day stays that day.
export function torontoDay(value: string | null | undefined): string {
  const time = timeOf(value)
  if (Number.isNaN(time)) return value || ''
  return new Date(time).toLocaleDateString('en-CA', { timeZone: 'America/Toronto', year: 'numeric', month: 'short', day: 'numeric' })
}

// Points placed by time across the box (left to right, oldest first), price from the bottom; the line joins the set
// prices, the website's prices are dots on the same scale. Needs two set prices for a line.
export function trend(points: TrendInput[], website: TrendInput[], width: number, height: number, pad = 6): Trend | null {
  const set = points.map(p => ({ ...p, t: timeOf(p.at) })).filter(p => !Number.isNaN(p.t)).sort((a, b) => a.t - b.t)
  if (set.length < 2) return null
  const web = website.map(p => ({ ...p, t: timeOf(p.at) })).filter(p => !Number.isNaN(p.t))
  const all = [...set, ...web]
  const first = Math.min(...all.map(p => p.t)), last = Math.max(...all.map(p => p.t))
  const low = Math.min(...all.map(p => p.cents)), high = Math.max(...all.map(p => p.cents))
  const x = (t: number) => last === first ? width / 2 : pad + (t - first) / (last - first) * (width - 2 * pad)
  const y = (cents: number) => high === low ? height / 2 : pad + (high - cents) / (high - low) * (height - 2 * pad)
  const place = (p: typeof all[number]): TrendDot => ({ x: round(x(p.t)), y: round(y(p.cents)), cents: p.cents, at: p.at })
  const dots = set.map(place)
  return { line: dots.map((d, i) => `${i ? 'L' : 'M'}${d.x} ${d.y}`).join(' '), dots, website: web.map(place), low, high }
}

const round = (value: number) => Math.round(value * 10) / 10
