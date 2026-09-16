import type { ShiftKind } from './openHours'

export function shiftKindLabel(kind: ShiftKind): string {
  return { full: 'Full day', first: 'Half day (AM)', second: 'Half day (PM)', custom: 'Custom' }[kind]
}

interface ShiftAccessibleLabelInput {
  employeeName: string
  startTime: string
  endTime: string
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
  position,
  isTrainee,
  shiftType,
}: ShiftAccessibleLabelInput): string {
  return [
    employeeName,
    `${startTime}–${endTime}`,
    shiftType,
    position || null,
    isTrainee ? 'Trainee' : null,
  ].filter(Boolean).join(' · ')
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
