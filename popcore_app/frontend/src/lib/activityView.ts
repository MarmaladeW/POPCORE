import { torontoStamp } from './marks.ts'

// One 动态 line as the group chat would show it: time, who (and on which computer), what.
export interface ActivityLine { actor_name: string | null; via_name: string | null; at: string; summary: string; detail: string }

export function activityWho(entry: Pick<ActivityLine, 'actor_name' | 'via_name'>): string {
  const who = entry.actor_name || '没有员工资料 No staff profile'
  return entry.via_name ? `${who}（${entry.via_name}）` : who
}

export function activityTime(entry: Pick<ActivityLine, 'at'>): string {
  return torontoStamp(entry.at).split(' ')[1] ?? ''
}

export function shiftDay(day: string, days: number): string {
  const date = new Date(`${day}T12:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}
