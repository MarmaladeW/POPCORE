import dayjs from 'dayjs'

// 店长/副店长 only look at 排班 for the current wage month, e.g. Nov 4 – Dec 3 (owner, 2026-11-28: managers do not edit
// the schedule and see one month).
// The server refuses the rest; these keep the page's arrows and month picker inside the window.
// `null` is an admin (every month) or a page that has not heard from the server yet.
export type ManagerWindow = { first: string; last: string; months: string[] }

/** Why a manager's arrows stop: the dates they may see. */
export const monthLimitHint = (window: ManagerWindow | null) =>
  window ? `店长只能看 ${window.first} 到 ${window.last} 的排班 · Managers see ${window.first} – ${window.last} only` : undefined

/** What a 店长/副店长 sees instead of the add and edit buttons. */
export const READ_ONLY_HINT = '只能查看 · View only: 排班由管理员安排 The admin makes the schedule'

/** Whether the period before one starting on `periodStart` still has days inside the window. */
export const canGoBack = (window: ManagerWindow | null, periodStart: string) => !window || periodStart > window.first

/** Whether the period starting on `nextPeriodStart` still has days inside the window. */
export const canGoForward = (window: ManagerWindow | null, nextPeriodStart: string) => !window || nextPeriodStart <= window.last

/** A 月工时 month (YYYY-MM) a manager cannot open. */
export const monthClosed = (window: ManagerWindow | null, month: string) => !!window && !window.months.includes(month)

/** FullCalendar's validRange: days outside are greyed and cannot be navigated to (end is exclusive). */
export const calendarRange = (window: ManagerWindow | null) =>
  window ? { start: window.first, end: dayjs(window.last).add(1, 'day').format('YYYY-MM-DD') } : undefined
