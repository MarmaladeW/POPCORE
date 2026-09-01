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
