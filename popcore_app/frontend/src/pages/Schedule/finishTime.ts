// 实际下班 Actual finish typed as digits only (numeric keypad): 2134 → 21:34, 934 → 09:34,
// 21 → 21:00, 045 → 00:45. A typed colon is ignored. The saved value stays HH:mm.

/** HH:mm for the typed digits, or null when they are not a time of day. Empty input gives ''. */
export function parseFinishDigits(input: string): string | null {
  const digits = input.replace(/\D/g, '')
  if (!digits) return ''
  if (digits.length > 4) return null
  const [hour, minute] = digits.length <= 2 ? [Number(digits), 0]
    : digits.length === 3 ? [Number(digits.slice(0, 1)), Number(digits.slice(1))]
    : [Number(digits.slice(0, 2)), Number(digits.slice(2))]
  if (hour > 23 || minute > 59) return null
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
}

/** The digits a saved HH:mm shows as in the field (21:34 → 2134, 00:45 → 045). */
export function finishDigits(value: string | null | undefined): string {
  if (!value || !/^\d\d:\d\d$/.test(value)) return ''
  const [hour, minute] = value.split(':')
  return `${Number(hour)}${minute}`.padStart(value.startsWith('00') ? 3 : 1, '0')
}

/** Form value for text that is not a time yet; validation rejects it so it is never saved. */
export const INVALID_FINISH = 'invalid'
