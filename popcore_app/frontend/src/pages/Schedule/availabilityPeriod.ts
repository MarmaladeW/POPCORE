const DAY_MS = 86_400_000
const ANCHOR = Date.UTC(2026, 8, 14)

export function cycleStart(date: string): string {
  const day = Date.parse(`${date}T00:00:00Z`)
  return new Date(ANCHOR + Math.floor((day - ANCHOR) / (14 * DAY_MS)) * 14 * DAY_MS).toISOString().slice(0, 10)
}

export function cycleDates(start: string): string[] {
  const day = Date.parse(`${start}T00:00:00Z`)
  return Array.from({ length: 14 }, (_, i) => new Date(day + i * DAY_MS).toISOString().slice(0, 10))
}

export interface AvailabilityDraft {
  date: string
  status: 'unset' | 'available' | 'unavailable'
  start_time: string
  end_time: string
  notes: string
}

export function emptyDays(start: string): AvailabilityDraft[] {
  return cycleDates(start).map(date => ({ date, status: 'unset', start_time: '', end_time: '', notes: '' }))
}

export function repeatFirstWeek(days: AvailabilityDraft[]): AvailabilityDraft[] {
  return days.map((day, i) => i < 7 ? { ...day } : { ...days[i - 7], date: day.date })
}

export function availabilityIssue(
  availability: { status?: string; store_code?: string; start_time: string; end_time: string; submitted_at?: string | null } | undefined,
  store: string, start: string, end: string,
): string | null {
  if (!availability || availability.store_code !== store || !availability.submitted_at) return 'Availability not submitted for this store and period.'
  if (availability.status === 'unavailable') return 'Employee marked this date unavailable.'
  if (!start || !end || end <= start) return 'End time must be after start time.'
  if (start < availability.start_time || end > availability.end_time) return `Shift is outside availability (${availability.start_time}–${availability.end_time}).`
  return null
}

export function finishSubmission<T extends { version: number; dirty: boolean; submittedAt: string | null }>(
  current: T | undefined, sent: T, saved: { version: number; submitted_at: string | null },
): T | undefined {
  if (!current || current.version > saved.version) return current
  return { ...current, dirty: current === sent ? false : current.dirty, version: saved.version, submittedAt: saved.submitted_at }
}
