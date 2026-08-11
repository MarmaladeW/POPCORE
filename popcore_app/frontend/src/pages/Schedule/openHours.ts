import dayjs from 'dayjs'

/** Store opening hours: 12:00–22:00 Mon–Fri, 11:00–22:00 Sat–Sun. */
export function openHoursFor(date: string): { start: string; end: string } {
  const dow = dayjs(date).day()
  const weekend = dow === 0 || dow === 6
  return { start: weekend ? '11:00' : '12:00', end: '22:00' }
}

/** FullCalendar businessHours config matching openHoursFor. */
export const BUSINESS_HOURS = [
  { daysOfWeek: [1, 2, 3, 4, 5], startTime: '12:00', endTime: '22:00' },
  { daysOfWeek: [0, 6],          startTime: '11:00', endTime: '22:00' },
]

/** Time where half-day shifts split: 17:00 on weekdays, 16:30 on weekends
 *  (so weekend halves are 11:00–16:30 and 16:30–22:00). */
export function halfSplitFor(date: string): string {
  const dow = dayjs(date).day()
  return (dow === 0 || dow === 6) ? '16:30' : '17:00'
}

/** Minimum staff required on the floor during opening hours.
 *  DT needs 3 at all times; MK needs 1 on weekdays and 2 on weekends;
 *  any other store defaults to 1. */
export function requiredStaff(storeCode: string, date: string): number {
  const dow = dayjs(date).day()
  const weekend = dow === 0 || dow === 6
  switch (storeCode) {
    case 'DT': return 3
    case 'MK': return weekend ? 2 : 1
    default:   return 1
  }
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
): StaffGap[] {
  const { start, end } = openHoursFor(date)
  const open  = toMin(start)
  const close = toMin(end)
  const need  = requiredStaff(storeCode, date)

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
