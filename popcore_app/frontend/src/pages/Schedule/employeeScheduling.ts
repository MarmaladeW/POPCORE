import type { Employee } from './scheduleApi'


export function assignableEmployees<T extends { is_schedulable?: number }>(employees: T[]): T[] {
  return employees.filter((employee) => employee.is_schedulable !== 0)
}

export function shiftModalEmployees(
  employees: Employee[],
  currentEmployeeId?: number,
): Employee[] {
  return employees.filter(
    (employee) => employee.is_schedulable !== 0 || employee.id === currentEmployeeId,
  )
}

export function normalizeEmployeeColor(value: string): string {
  return value.toUpperCase()
}

export async function pickScreenColor(
  open: () => Promise<{ sRGBHex: string }>,
): Promise<string> {
  const result = await open()
  return normalizeEmployeeColor(result.sRGBHex)
}

export function isEyeDropperCancellation(error: unknown): boolean {
  return !!error
    && typeof error === 'object'
    && 'name' in error
    && error.name === 'AbortError'
}

export function updateEmployeeSetting<T>(
  entries: Record<string, T>,
  auth0Id: string,
  patch: Partial<T>,
): Record<string, T> {
  const entry = entries[auth0Id]
  if (!entry) return entries
  return {
    ...entries,
    [auth0Id]: { ...entry, ...patch },
  }
}

export function scheduleApiErrorMessage(error: unknown, fallback: string): string {
  if (!error || typeof error !== 'object' || !('response' in error)) return fallback
  const response = (error as { response?: { data?: { error?: unknown } } }).response
  const detail = response?.data?.error
  return typeof detail === 'string' && detail.trim() ? detail : fallback
}

export async function persistEmployeeSetting<T>(options: {
  optimistic: () => void
  persist: () => Promise<T>
  rollback: () => void
}): Promise<T> {
  options.optimistic()
  try {
    return await options.persist()
  } catch (error) {
    options.rollback()
    throw error
  }
}
