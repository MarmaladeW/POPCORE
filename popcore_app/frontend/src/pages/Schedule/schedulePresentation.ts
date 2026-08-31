interface ShiftAccessibleLabelInput {
  employeeName: string
  startTime: string
  endTime: string
  position: string
  isTrainee: boolean
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
}: ShiftAccessibleLabelInput): string {
  return [
    employeeName,
    `${startTime}–${endTime}`,
    position || null,
    isTrainee ? 'Trainee' : null,
  ].filter(Boolean).join(' · ')
}

export function mobileMonthEventLimit(isMobile: boolean, viewType: string): number {
  return isMobile && viewType === 'dayGridMonth' ? 3 : 4
}

export function shiftColorPresentation(employeeColor: string, kind: string) {
  return {
    backgroundColor: kind === 'full' ? employeeColor : `${employeeColor}26`,
    borderColor: employeeColor,
  }
}

export function coverageRowPresentation(hasShifts: boolean, considered: boolean) {
  if (hasShifts) {
    return { state: 'scheduled' as const, showReasonInput: false, statusLabel: '' }
  }
  if (considered) {
    return { state: 'considered' as const, showReasonInput: true, statusLabel: '' }
  }
  return { state: 'open' as const, showReasonInput: false, statusLabel: 'No shifts' }
}
