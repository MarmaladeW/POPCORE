import type { ShiftKind } from './openHours'

export function shiftKindLabel(kind: ShiftKind): string {
  return { full: 'Full day', first: 'Half day (AM)', second: 'Half day (PM)', custom: 'Custom' }[kind]
}

interface ShiftAccessibleLabelInput {
  employeeName: string
  startTime: string
  endTime: string
  actualEndTime?: string | null
  position: string
  isTrainee: boolean
  shiftType?: string
}

export function compactEmployeeLabel(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  if (words.length > 1) {
    return words.slice(0, 3).map(word => word[0]).join('').toUpperCase()
  }
  const only = words[0]
  return only.length <= 3 ? only.toUpperCase() : only.slice(0, 3).toUpperCase()
}

export function mobileShiftAccessibleLabel({
  employeeName,
  startTime,
  endTime,
  actualEndTime,
  position,
  isTrainee,
  shiftType,
}: ShiftAccessibleLabelInput): string {
  return [
    employeeName,
    shiftTimeRange(startTime, endTime, actualEndTime),
    shiftType,
    position || null,
    isTrainee ? 'Trainee' : null,
  ].filter(Boolean).join(' · ')
}

/** An actual finish at or before the start, up to 01:00, is on the next day. */
export function finishesNextDay(startTime: string, actualEndTime: string): boolean {
  return actualEndTime <= startTime && actualEndTime <= '01:00'
}

/** Planned range, plus the recorded actual finish when a manager has set one. */
export function shiftTimeRange(startTime: string, endTime: string, actualEndTime?: string | null): string {
  const range = `${startTime}–${endTime}`
  if (!actualEndTime) return range
  return `${range} · finished ${actualEndTime}${finishesNextDay(startTime, actualEndTime) ? ' (next day)' : ''}`
}

/** Compact calendar marker for a recorded actual finish: "✓ 21:25", or "✓ 00:45 +1" the next day. */
export function actualFinishChip(startTime: string, actualEndTime?: string | null): string {
  if (!actualEndTime) return ''
  return `✓ ${actualEndTime}${finishesNextDay(startTime, actualEndTime) ? ' +1' : ''}`
}

export function shiftColorPresentation(employeeColor: string, _kind: string) {
  return {
    backgroundColor: employeeColor,
    borderColor: employeeColor,
    display: 'block' as const,
  }
}

export function manualChecklistPresentation(checked: boolean) {
  return checked
    ? { state: 'checked' as const, statusLabel: 'Checked' }
    : { state: 'unchecked' as const, statusLabel: 'Unchecked' }
}
