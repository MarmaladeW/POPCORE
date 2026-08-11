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

const toMin  = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5))
const toHHMM = (m: number) =>
  `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`

/** Opening-hour intervals of `date` not covered by any of `shifts`. */
export function uncoveredIntervals(
  date: string,
  shifts: Array<{ start_time: string; end_time: string }>,
): Array<{ start: string; end: string }> {
  const { start, end } = openHoursFor(date)
  const open = toMin(start)
  const close = toMin(end)
  const covered = shifts
    .map((s) => [Math.max(open, toMin(s.start_time)), Math.min(close, toMin(s.end_time))] as [number, number])
    .filter(([a, b]) => b > a)
    .sort((a, b) => a[0] - b[0])
  const gaps: Array<{ start: string; end: string }> = []
  let cursor = open
  for (const [a, b] of covered) {
    if (a > cursor) gaps.push({ start: toHHMM(cursor), end: toHHMM(a) })
    cursor = Math.max(cursor, b)
  }
  if (cursor < close) gaps.push({ start: toHHMM(cursor), end: toHHMM(close) })
  return gaps
}
