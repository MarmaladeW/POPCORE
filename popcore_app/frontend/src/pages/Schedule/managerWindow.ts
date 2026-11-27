import dayjs from 'dayjs'

// 店长/副店长 see this month and next in 排班 (owner, 2026-11-27: "the manager can only see the schedule for that month,
// not all of them"). The server refuses the rest; these keep the page's arrows and month picker inside the window.
// `null` is an admin (every month) or a page that has not heard from the server yet.
export type ManagerWindow = { first: string; last: string; months: string[] }

export const MONTH_LIMIT_HINT = '店长只能看这个月和下个月 · Managers see this month and next only'

/** Whether the period before one starting on `periodStart` still has days inside the window. */
export const canGoBack = (window: ManagerWindow | null, periodStart: string) => !window || periodStart > window.first

/** Whether the period starting on `nextPeriodStart` still has days inside the window. */
export const canGoForward = (window: ManagerWindow | null, nextPeriodStart: string) => !window || nextPeriodStart <= window.last

/** A 月工时 month (YYYY-MM) a manager cannot open. */
export const monthClosed = (window: ManagerWindow | null, month: string) => !!window && !window.months.includes(month)

/** FullCalendar's validRange: days outside are greyed and cannot be navigated to (end is exclusive). */
export const calendarRange = (window: ManagerWindow | null) =>
  window ? { start: window.first, end: dayjs(window.last).add(1, 'day').format('YYYY-MM-DD') } : undefined
