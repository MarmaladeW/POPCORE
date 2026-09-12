const wholeDollars = new Intl.NumberFormat('en-CA', { maximumFractionDigits: 0 })

export function parseMoneyToCents(value: string): number | null {
  const input = value.trim()
  if (!input) return null
  if (!/^\d+(?:\.\d{1,2})?$/.test(input)) throw new RangeError('Enter a nonnegative dollar amount with at most two decimals.')
  const [whole, fraction = ''] = input.split('.')
  const cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'))
  if (cents > BigInt(Number.MAX_SAFE_INTEGER)) throw new RangeError('Dollar amount is too large.')
  return Number(cents)
}

export function formatCents(value: number | null | undefined): string {
  if (value == null) return 'Unknown'
  if (!Number.isSafeInteger(value)) throw new RangeError('Cents must be a safe integer.')
  const cents = BigInt(value)
  const absolute = cents < 0 ? -cents : cents
  const whole = absolute / 100n
  const fraction = String(absolute % 100n).padStart(2, '0')
  return `${cents < 0 ? '-' : ''}$${wholeDollars.format(Number(whole))}.${fraction}`
}
