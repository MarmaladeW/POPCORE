import client from '../../api/client'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Employee {
  id: number
  auth0_id: string
  name: string
  email: string
  is_active: number
  is_schedulable: number
  is_trainee?: number
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
  store_code?: string
}

export interface Shift {
  id: number
  employee_id: number
  date: string        // YYYY-MM-DD
  start_time: string  // HH:MM
  end_time: string    // HH:MM
  assigned_by: string
  notes: string
  position?: string
  created_at: string
  updated_at: string
  // joined fields
  employee_name?: string
  auth0_id?: string
  is_trainee?: number
  store_code?: string
}

export interface ConflictInfo {
  shift_id:   number
  store_code: string
  store_name: string
  start_time: string  // ISO datetime e.g. "2026-05-09T12:00"
  end_time:   string
}

export interface ConflictResult {
  has_conflict: boolean
  conflicts:    ConflictInfo[]
}

export interface WeekBreakdown {
  total: number
  days: Record<string, number>
}

export interface EmployeeMonthlyHours {
  id: number
  name: string
  email: string
  is_trainee?: number
  total_hours: number
  weeks: Record<string, WeekBreakdown>
}

export interface MonthlyReport {
  month: string
  employees: EmployeeMonthlyHours[]
  period_start: string     // YYYY-MM-DD (wage period start, e.g. Aug 4)
  period_end: string       // YYYY-MM-DD (wage period end,   e.g. Sep 3)
  month_start_day: number  // configurable day the wage month starts on
}

export interface EmployeeHours {
  employee_id: number
  name: string
  email: string
  period_start: string
  period_end: string
  month_start_day: number
  total_hours: number
  shift_count: number
  by_store: Record<string, number>
}

export interface CalendarFeed {
  token: string
  path: string
}

export interface ScheduleConfig {
  schedule_month_start_day: string
  schedule_required_staff: string
  schedule_open_hours: string
  schedule_shift_presets: string
  schedule_positions: string
}

export interface ScheduleNote {
  period_key: string
  content: string
  updated_by: string
  updated_at: string | null
}

export interface ChecklistEntry {
  id?: number
  period_key: string
  employee_id: number
  considered: number
  note: string
  updated_by?: string
  updated_at?: string
}

export interface EmployeeStoreAssignment {
  employee_id: number
  auth0_id:    string
  name:        string
  color:       string
  is_schedulable: number
  stores:      string[]   // store codes, e.g. ['DT', 'MK']
}

// ── Schedule config (any logged-in user) ──────────────────────────────────────

export const getScheduleConfig = () =>
  client.get<ScheduleConfig>('/schedule/config').then((r) => r.data)

// ── Employee profile ──────────────────────────────────────────────────────────

export const getMe = () =>
  client.get<Employee>('/schedule/me').then((r) => r.data)

export const patchMe = (data: { name?: string; email?: string }) =>
  client.patch<Employee>('/schedule/me', data).then((r) => r.data)

export const getEmployees = () =>
  client.get<Employee[]>('/schedule/employees').then((r) => r.data)

export const renameEmployee = (employeeId: number, name: string) =>
  client.patch<Employee>(`/schedule/employees/${employeeId}`, { name }).then((r) => r.data)

// ── Trainees ──────────────────────────────────────────────────────────────────

export const getTrainees = () =>
  client.get<Employee[]>('/schedule/trainees').then((r) => r.data)

export const createTrainee = (name: string) =>
  client.post<Employee>('/schedule/trainees', { name }).then((r) => r.data)

export const deleteTrainee = (traineeId: number) =>
  client.delete(`/schedule/trainees/${traineeId}`).then((r) => r.data)

// ── Period notes + coverage checklist ─────────────────────────────────────────

export const getScheduleNote = (key: string) =>
  client.get<ScheduleNote>('/schedule/notes', { params: { key } }).then((r) => r.data)

export const saveScheduleNote = (periodKey: string, content: string) =>
  client.put<ScheduleNote>('/schedule/notes', { period_key: periodKey, content }).then((r) => r.data)

export const getChecklist = (key: string) =>
  client.get<ChecklistEntry[]>('/schedule/checklist', { params: { key } }).then((r) => r.data)

export const saveChecklistEntry = (entry: {
  period_key: string
  employee_id: number
  considered: boolean
  note?: string
}) => client.put<ChecklistEntry>('/schedule/checklist', entry).then((r) => r.data)

export const getEmployeeStores = () =>
  client.get<EmployeeStoreAssignment[]>('/employees/stores').then((r) => r.data)

export const setEmployeeStores = (employeeId: number, storeCodes: string[]) =>
  client.put<{ employee_id: number; stores: string[] }>(
    `/employees/${employeeId}/stores`,
    { store_codes: storeCodes }
  ).then((r) => r.data)

export const setEmployeeColor = (employeeId: number, color: string) =>
  client.patch<Employee>(`/employees/${employeeId}/color`, { color }).then((r) => r.data)

export const setEmployeeSchedulable = (employeeId: number, enabled: boolean) =>
  client.patch<Employee>(`/employees/${employeeId}/schedulable`, {
    is_schedulable: enabled,
  }).then((r) => r.data)

// ── Availability ──────────────────────────────────────────────────────────────

export const getMyAvailability = (start?: string, end?: string, storeCode?: string) => {
  const params: Record<string, string> = {}
  if (start) params.start = start
  if (end) params.end = end
  if (storeCode) params.store_code = storeCode
  return client.get<Availability[]>('/schedule/availability/me', { params }).then((r) => r.data)
}

export const getAllAvailability = (start?: string, end?: string, storeCode?: string) => {
  const params: Record<string, string> = {}
  if (start) params.start = start
  if (end) params.end = end
  if (storeCode) params.store_code = storeCode
  return client.get<Availability[]>('/schedule/availability', { params }).then((r) => r.data)
}

export const upsertAvailability = (data: {
  date: string
  start_time: string
  end_time: string
  notes?: string
  store_code?: string
}) => client.post<Availability>('/schedule/availability', data).then((r) => r.data)

export const deleteAvailability = (id: number) =>
  client.delete(`/schedule/availability/${id}`).then((r) => r.data)

// ── Shifts ────────────────────────────────────────────────────────────────────

export const getShifts = (params?: {
  start?: string
  end?: string
  employee_id?: number
  store_code?: string
}) => client.get<Shift[]>('/schedule/shifts', { params }).then((r) => r.data)

export const getMyShifts = (params?: { start?: string; end?: string; store_code?: string }) =>
  client.get<Shift[]>('/schedule/shifts/me', { params }).then((r) => r.data)

export const createShift = (data: {
  employee_id: number
  date: string
  start_time: string
  end_time: string
  notes?: string
  position?: string
  store_code?: string
}) => client.post<Shift>('/schedule/shifts', data).then((r) => r.data)

export const updateShift = (
  id: number,
  data: { start_time?: string; end_time?: string; notes?: string; position?: string; store_code?: string }
) => client.patch<Shift>(`/schedule/shifts/${id}`, data).then((r) => r.data)

export const deleteShift = (id: number) =>
  client.delete(`/schedule/shifts/${id}`).then((r) => r.data)

export const checkConflicts = (params: {
  employee_id: number
  date: string
  store_code: string
}) => client.get<ConflictResult>('/schedule/conflicts', { params }).then((r) => r.data)

// ── Reports ───────────────────────────────────────────────────────────────────

export const getMonthlyReport = (year: number, month: number, storeCode?: string) =>
  client
    .get<MonthlyReport>('/schedule/reports/monthly', { params: { year, month, store_code: storeCode } })
    .then((r) => r.data)

export const getEmployeeHours = (employeeId: number, year?: number, month?: number) =>
  client
    .get<EmployeeHours>(`/schedule/employees/${employeeId}/hours`, { params: { year, month } })
    .then((r) => r.data)

// ── Calendar sync ─────────────────────────────────────────────────────────────

export const getCalendarFeed = () =>
  client.get<CalendarFeed>('/schedule/calendar-feed').then((r) => r.data)

export const resetCalendarFeed = () =>
  client.post<CalendarFeed>('/schedule/calendar-feed/reset').then((r) => r.data)
