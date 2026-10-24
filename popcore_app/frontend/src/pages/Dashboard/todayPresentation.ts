import type { TodaySection } from '../../api/today'
import type { Shift } from '../Schedule/scheduleApi'

export function torontoDate(value=new Date()){
  const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Toronto',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(value)
  const get=(type:string)=>parts.find(p=>p.type===type)?.value || ''
  return `${get('year')}-${get('month')}-${get('day')}`
}
export function selectShiftHighlights(shifts:Shift[],today:string){
  const valid=shifts.filter(s=>/^\d{4}-\d{2}-\d{2}$/.test(s.date)&&/^\d{2}:\d{2}$/.test(s.start_time)&&/^\d{2}:\d{2}$/.test(s.end_time))
  const sorted=[...valid].sort((a,b)=>a.date.localeCompare(b.date)||a.start_time.localeCompare(b.start_time)||a.id-b.id)
  const todayRows=sorted.filter(s=>s.date===today)
  const nextDate=sorted.find(s=>s.date>today)?.date
  return {today:todayRows,next:nextDate?sorted.filter(s=>s.date===nextDate):[],invalidCount:shifts.length-valid.length}
}

// 我的工作 and 门店工作 read as one 待处理 list, oldest first; rows without a time go last.
export function mergeWork(sections: Record<string, TodaySection>): Record<string, TodaySection> {
  const parts = ['my_work', 'operations'].filter(name => sections[name])
  if (!parts.length) return sections
  const rows = parts.flatMap(name => sections[name].rows).sort((a, b) =>
    (a.updated_at || '\uffff').localeCompare(b.updated_at || '\uffff') || a.type.localeCompare(b.type) || a.source_id - b.source_id)
  const rest = Object.fromEntries(Object.entries(sections).filter(([name]) => !parts.includes(name)))
  return { tasks: { total: parts.reduce((sum, name) => sum + sections[name].total, 0), rows }, ...rest }
}
