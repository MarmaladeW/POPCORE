import dayjs from 'dayjs'

export interface DayHours { open: string; close: string }
export interface OpenHoursConfig { weekday: DayHours; weekend: DayHours }
/** Opening hours per store code. A '*' entry (from the legacy global format)
 *  acts as the fallback for stores without their own entry. */
export type StoreHoursMap = Record<string, OpenHoursConfig>

/** Fallback for stores with no configured hours. */
export const DEFAULT_OPEN_HOURS: OpenHoursConfig = {
  weekday: { open: '12:00', close: '22:00' },
  weekend: { open: '11:00', close: '22:00' },
}

/** Used until the schedule_open_hours setting is loaded (or if malformed):
 *  DT 12–22 / 11–22, MK closes at 21:00. */
export const DEFAULT_STORE_HOURS: StoreHoursMap = {
  DT: { weekday: { open: '12:00', close: '22:00' }, weekend: { open: '11:00', close: '22:00' } },
  MK: { weekday: { open: '12:00', close: '21:00' }, weekend: { open: '11:00', close: '21:00' } },
}

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/

function normalizeBlock(block: unknown): DayHours | null {
  const b = block as { open?: unknown; close?: unknown } | undefined
  const open  = String(b?.open ?? '')
  const close = String(b?.close ?? '')
  return TIME_RE.test(open) && TIME_RE.test(close) && open < close ? { open, close } : null
}

function normalizeConfig(cfg: unknown): OpenHoursConfig | null {
  const c = cfg as { weekday?: unknown; weekend?: unknown } | undefined
  const weekday = normalizeBlock(c?.weekday)
  const weekend = normalizeBlock(c?.weekend)
  if (!weekday && !weekend) return null
  return {
    weekday: weekday ?? DEFAULT_OPEN_HOURS.weekday,
    weekend: weekend ?? DEFAULT_OPEN_HOURS.weekend,
  }
}

/** Parse the schedule_open_hours app setting. Accepts the per-store format
 *  ({"DT": {weekday, weekend}, ...}) and the legacy global format
 *  ({weekday, weekend} — stored under '*' as the all-stores fallback).
 *  Falls back to the defaults on missing/malformed input. */
export function parseStoreOpenHours(raw: string | null | undefined): StoreHoursMap {
  if (!raw) return DEFAULT_STORE_HOURS
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>
    if (!parsed || typeof parsed !== 'object') return DEFAULT_STORE_HOURS
    if ('weekday' in parsed || 'weekend' in parsed) {
      const cfg = normalizeConfig(parsed)
      return cfg ? { '*': cfg } : DEFAULT_STORE_HOURS
    }
    const out: StoreHoursMap = {}
    for (const [code, cfg] of Object.entries(parsed)) {
      const n = normalizeConfig(cfg)
      if (n) out[code] = n
    }
    return Object.keys(out).length > 0 ? out : DEFAULT_STORE_HOURS
  } catch {
    return DEFAULT_STORE_HOURS
  }
}

/** Opening hours for one store, with '*' (legacy global) then default fallback. */
export function hoursForStore(storeCode: string, map: StoreHoursMap = DEFAULT_STORE_HOURS): OpenHoursConfig {
  return map[storeCode] ?? map['*'] ?? DEFAULT_STORE_HOURS[storeCode] ?? DEFAULT_OPEN_HOURS
}

/** Earliest open / latest close across all stores — for calendars that mix
 *  stores (e.g. an employee's own schedule). */
export function unionHours(map: StoreHoursMap = DEFAULT_STORE_HOURS): OpenHoursConfig {
  const cfgs = Object.values(map)
  if (cfgs.length === 0) return DEFAULT_OPEN_HOURS
  const union = (sel: (c: OpenHoursConfig) => DayHours): DayHours => {
    const opens  = cfgs.map(c => sel(c).open).sort()
    const closes = cfgs.map(c => sel(c).close).sort()
    return { open: opens[0], close: closes[closes.length - 1] }
  }
  return { weekday: union(c => c.weekday), weekend: union(c => c.weekend) }
}

/** Opening hours applying to `date`. */
export function openHoursFor(
  date: string,
  hours: OpenHoursConfig = DEFAULT_OPEN_HOURS,
): { start: string; end: string } {
  const dow = dayjs(date).day()
  const block = (dow === 0 || dow === 6) ? hours.weekend : hours.weekday
  return { start: block.open, end: block.close }
}

/** FullCalendar businessHours config matching openHoursFor. */
export function businessHoursFrom(hours: OpenHoursConfig) {
  return [
    { daysOfWeek: [1, 2, 3, 4, 5], startTime: hours.weekday.open, endTime: hours.weekday.close },
    { daysOfWeek: [0, 6],          startTime: hours.weekend.open, endTime: hours.weekend.close },
  ]
}

export const BUSINESS_HOURS = businessHoursFrom(DEFAULT_OPEN_HOURS)

/** Time-grid window: from ~2h before the earliest open to 1h after close. */
export function gridWindow(hours: OpenHoursConfig): { slotMinTime: string; slotMaxTime: string } {
  const openMin  = Math.min(toMin(hours.weekday.open),  toMin(hours.weekend.open))
  const closeMax = Math.max(toMin(hours.weekday.close), toMin(hours.weekend.close))
  return {
    slotMinTime: toHHMM(Math.max(0, openMin - 120)),
    slotMaxTime: toHHMM(Math.min(24 * 60, closeMax + 60)),
  }
}

/** Time where half-day shifts split: 17:00 on weekdays, 16:30 on weekends
 *  (so weekend halves are 11:00–16:30 and 16:30–22:00). */
export function halfSplitFor(date: string): string {
  const dow = dayjs(date).day()
  return (dow === 0 || dow === 6) ? '16:30' : '17:00'
}

export interface StaffRequirement { weekday: number; weekend: number }
export type StaffRequirements = Record<string, StaffRequirement>

/** Used until the schedule_required_staff setting is loaded (or if it is
 *  malformed): DT needs 3 at all times, MK 1 weekday / 2 weekend. */
export const DEFAULT_STAFF_REQUIREMENTS: StaffRequirements = {
  DT: { weekday: 3, weekend: 3 },
  MK: { weekday: 1, weekend: 2 },
}

/** Parse the schedule_required_staff app setting (JSON keyed by store code).
 *  Falls back to the defaults on missing/malformed input. */
export function parseStaffRequirements(raw: string | null | undefined): StaffRequirements {
  if (!raw) return DEFAULT_STAFF_REQUIREMENTS
  try {
    const parsed = JSON.parse(raw) as Record<string, { weekday?: unknown; weekend?: unknown }>
    if (!parsed || typeof parsed !== 'object') return DEFAULT_STAFF_REQUIREMENTS
    const out: StaffRequirements = {}
    for (const [code, req] of Object.entries(parsed)) {
      const weekday = Number(req?.weekday)
      const weekend = Number(req?.weekend)
      if (Number.isFinite(weekday) && Number.isFinite(weekend)) {
        out[code] = { weekday, weekend }
      }
    }
    return out
  } catch {
    return DEFAULT_STAFF_REQUIREMENTS
  }
}

/** Minimum staff required on the floor during opening hours.
 *  Stores without a configured requirement default to 1. */
export function requiredStaff(
  storeCode: string,
  date: string,
  reqs: StaffRequirements = DEFAULT_STAFF_REQUIREMENTS,
): number {
  const req = reqs[storeCode]
  if (!req) return 1
  const dow = dayjs(date).day()
  return (dow === 0 || dow === 6) ? req.weekend : req.weekday
}

const toMin  = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5))
const toHHMM = (m: number) =>
  `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`

export interface StaffGap {
  start: string  // HH:MM
  end: string    // HH:MM
  have: number   // staff actually scheduled in this interval
  need: number   // staff required
}

/** Opening-hour intervals of `date` where fewer than the required number of
 *  staff are scheduled at `storeCode`. Adjacent intervals with the same
 *  headcount are merged so each gap can be labelled "have/need". */
export function understaffedIntervals(
  storeCode: string,
  date: string,
  shifts: Array<{ start_time: string; end_time: string }>,
  reqs: StaffRequirements = DEFAULT_STAFF_REQUIREMENTS,
  storeHours: StoreHoursMap = DEFAULT_STORE_HOURS,
): StaffGap[] {
  const { start, end } = openHoursFor(date, hoursForStore(storeCode, storeHours))
  const open  = toMin(start)
  const close = toMin(end)
  const need  = requiredStaff(storeCode, date, reqs)

  const ivs = shifts
    .map((s) => [Math.max(open, toMin(s.start_time)), Math.min(close, toMin(s.end_time))] as [number, number])
    .filter(([a, b]) => b > a)

  const points = Array.from(new Set([open, close, ...ivs.flat()]))
    .filter((p) => p >= open && p <= close)
    .sort((a, b) => a - b)

  // Segments between consecutive boundary points have a constant headcount
  const segs: Array<{ a: number; b: number; have: number }> = []
  for (let i = 0; i < points.length - 1; i++) {
    const p = points[i]
    const q = points[i + 1]
    const have = ivs.filter(([a, b]) => a <= p && b >= q).length
    if (have < need) segs.push({ a: p, b: q, have })
  }

  // Merge touching segments with the same headcount
  const merged: typeof segs = []
  for (const s of segs) {
    const last = merged[merged.length - 1]
    if (last && last.b === s.a && last.have === s.have) {
      last.b = s.b
    } else {
      merged.push({ ...s })
    }
  }

  return merged.map((s) => ({ start: toHHMM(s.a), end: toHHMM(s.b), have: s.have, need }))
}
