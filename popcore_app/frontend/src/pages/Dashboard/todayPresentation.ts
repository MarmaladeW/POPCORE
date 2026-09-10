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
