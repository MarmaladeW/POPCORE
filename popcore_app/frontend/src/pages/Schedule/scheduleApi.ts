import client from '../../api/client'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Employee {
  id: number
  auth0_id: string
  name: string
  email: string
  is_active: number
  created_at: string
}

export interface Availability {
  id: number
  employee_id: number
  date: string        // YYYY-MM-DD
  start_time: string  // HH:MM
  end_time: string    // HH:MM
  notes: string
  created_at: string
  updated_at: string
  // joined fields (all-avail endpoint)
  employee_name?: string
  auth0_id?: string
}

export interface Shift {
  id: number
  employee_id: number
  date: string        // YYYY-MM-DD
  start_time: string  // HH:MM
  end_time: string    // HH:MM
  assigned_by: string
  notes: string
  created_at: string
  updated_at: string
  // joined fields
  employee_name?: string
  auth0_id?: string
}

export interface WeekBreakdown {
  total: number
  days: Record<string, number>
}

export interface EmployeeMonthlyHours {
  id: number
  name: string
  email: string
  total_hours: number
  weeks: Record<string, WeekBreakdown>
}

export interface MonthlyReport {
  month: string
  employees: EmployeeMonthlyHours[]
}

// ── Employee profile ──────────────────────────────────────────────────────────

export const getMe = () =>
  client.get<Employee>('/schedule/me').then((r) => r.data)

export const patchMe = (data: { name?: string; email?: string }) =>
  client.patch<Employee>('/schedule/me', data).then((r) => r.data)

export const getEmployees = () =>
  client.get<Employee[]>('/schedule/employees').then((r) => r.data)

// ── Availability ──────────────────────────────────────────────────────────────

export const getMyAvailability = (start?: string, end?: string) => {
  const params: Record<string, string> = {}
  if (start) params.start = start
  if (end) params.end = end
  return client.get<Availability[]>('/schedule/availability/me', { params }).then((r) => r.data)
}

export const getAllAvailability = (start?: string, end?: string) => {
  const params: Record<string, string> = {}
  if (start) params.start = start
  if (end) params.end = end
  return client.get<Availability[]>('/schedule/availability', { params }).then((r) => r.data)
}

export const upsertAvailability = (data: {
  date: string
  start_time: string
  end_time: string
  notes?: string
}) => client.post<Availability>('/schedule/availability', data).then((r) => r.data)

export const deleteAvailability = (id: number) =>
  client.delete(`/schedule/availability/${id}`).then((r) => r.data)

// ── Shifts ────────────────────────────────────────────────────────────────────

export const getShifts = (params?: {
  start?: string
  end?: string
  employee_id?: number
}) => client.get<Shift[]>('/schedule/shifts', { params }).then((r) => r.data)

export const createShift = (data: {
  employee_id: number
  date: string
  start_time: string
  end_time: string
  notes?: string
}) => client.post<Shift>('/schedule/shifts', data).then((r) => r.data)

export const updateShift = (
  id: number,
  data: { start_time?: string; end_time?: string; notes?: string }
) => client.patch<Shift>(`/schedule/shifts/${id}`, data).then((r) => r.data)

export const deleteShift = (id: number) =>
  client.delete(`/schedule/shifts/${id}`).then((r) => r.data)

// ── Reports ───────────────────────────────────────────────────────────────────

export const getMonthlyReport = (year: number, month: number) =>
  client
    .get<MonthlyReport>('/schedule/reports/monthly', { params: { year, month } })
    .then((r) => r.data)
