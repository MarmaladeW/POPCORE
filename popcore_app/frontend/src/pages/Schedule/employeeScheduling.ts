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
