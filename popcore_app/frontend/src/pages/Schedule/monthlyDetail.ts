// 转正 and the per-employee monthly hours breakdown (owner, 2026-11-03).
const DAY_MS = 86_400_000
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

/** Shifts on or after the 转正 date are marked 正. Dates are YYYY-MM-DD, so they compare as text. */
export function isRegularOn(regularSince: string | null | undefined, date: string): boolean {
  return !!regularSince && date >= regularSince
}

export interface DetailShift {
  id: number
  date: string
  store_code: string
  start_time: string
  end_time: string
  actual_end_time: string | null
  position: string
  hours: number
}

export interface DetailWeek {
  start: string
  end: string
  shifts: DetailShift[]
  hours: number
}

const dayNumber = (date: string) => Date.parse(`${date}T00:00:00Z`)
const isoDate = (ms: number) => new Date(ms).toISOString().slice(0, 10)

/** Hours add up as shown: each shift is already rounded to 2 decimals. */
export function sumHours(values: number[]): number {
  return Math.round(values.reduce((total, value) => total + value, 0) * 100) / 100
}

/** "8", "7.5", "8.33": no trailing zeros. */
export function formatHours(hours: number): string {
  return String(Math.round(hours * 100) / 100)
}

/** Every Monday-to-Sunday week the period touches, cut to the period, each with its own shifts,
 *  so a week with no shifts still shows as a week. */
export function weeksOfPeriod(periodStart: string, periodEnd: string, shifts: DetailShift[]): DetailWeek[] {
  const first = dayNumber(periodStart)
  const last = dayNumber(periodEnd)
  const weeks: DetailWeek[] = []
  let start = first
  while (start <= last) {
    const weekday = (new Date(start).getUTCDay() + 6) % 7  // Monday = 0
    const end = Math.min(start + (6 - weekday) * DAY_MS, last)
    const startDate = isoDate(start)
    const endDate = isoDate(end)
    const inWeek = shifts.filter(shift => shift.date >= startDate && shift.date <= endDate)
    weeks.push({ start: startDate, end: endDate, shifts: inWeek, hours: sumHours(inWeek.map(shift => shift.hours)) })
    start = end + DAY_MS
  }
  return weeks
}

/** Hours before and after a 转正 date that falls inside the period; null when it doesn't split it. */
export function regularSplit(
  regularSince: string | null | undefined, periodStart: string, periodEnd: string, shifts: DetailShift[],
): { before: number; after: number } | null {
  if (!regularSince || regularSince <= periodStart || regularSince > periodEnd) return null
  return {
    before: sumHours(shifts.filter(shift => !isRegularOn(regularSince, shift.date)).map(shift => shift.hours)),
    after: sumHours(shifts.filter(shift => isRegularOn(regularSince, shift.date)).map(shift => shift.hours)),
  }
}

/** "Mon 10/06" */
export function shortDay(date: string): string {
  return `${WEEKDAYS[new Date(dayNumber(date)).getUTCDay()]} ${date.slice(5, 7)}/${date.slice(8, 10)}`
}

/** "10/06" */
export function monthDay(date: string): string {
  return `${date.slice(5, 7)}/${date.slice(8, 10)}`
}

/** The time a shift's hours count to: the recorded finish, else the planned end. */
export function countedRange(shift: Pick<DetailShift, 'start_time' | 'end_time' | 'actual_end_time'>): string {
  const finish = shift.actual_end_time || shift.end_time
  const nextDay = !!shift.actual_end_time && shift.actual_end_time <= shift.start_time && shift.actual_end_time <= '01:00'
  return `${shift.start_time}–${finish}${nextDay ? ' +1' : ''}`
}
