import { useEffect, useMemo, useRef, useState } from 'react'
import { Input, message } from 'antd'
import { CheckCircle2, ChevronDown, ChevronUp, Circle, CircleAlert } from 'lucide-react'
import dayjs from 'dayjs'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  getChecklist,
  getScheduleNote,
  saveChecklistEntry,
  saveScheduleNote,
  type ChecklistEntry,
  type Employee,
  type Shift,
} from './scheduleApi'

interface Props {
  /** month:YYYY-MM | week:YYYY-MM-DD | day:YYYY-MM-DD */
  periodKey: string
  /** Human title of the visible period (e.g. "August 2026") */
  periodLabel: string
  /** Real period bounds (end exclusive) — excludes month-grid padding days */
  periodRange: { start: string; end: string } | null
  employees: Employee[]
  /** All shifts loaded for the visible range (all stores) */
  shifts: Shift[]
}

interface PersonStats { count: number; hours: number }

function hoursBetween(start: string, end: string): number {
  const toMin = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5))
  return Math.max(0, (toMin(end) - toMin(start)) / 60)
}

/** Coverage checklist + notes for the visible schedule period. Everyone on
 *  the team must either have a shift or be explicitly marked "considered"
 *  (with an optional reason) for the period to count as fully covered. */
export default function CoveragePanel({
  periodKey, periodLabel, periodRange, employees, shifts,
}: Props) {
  const [open, setOpen] = useState(true)
  const [entries, setEntries] = useState<Record<number, ChecklistEntry>>({})
  const [noteContent, setNoteContent] = useState('')
  const [noteDirty, setNoteDirty] = useState(false)
  const [noteSavedAt, setNoteSavedAt] = useState<string | null>(null)
  const [savingNote, setSavingNote] = useState(false)
  const [msgApi, msgCtx] = message.useMessage()
  // Ignore late responses after the period changed
  const keyRef = useRef(periodKey)
  keyRef.current = periodKey

  useEffect(() => {
    setEntries({})
    setNoteContent('')
    setNoteDirty(false)
    setNoteSavedAt(null)
    getChecklist(periodKey)
      .then(rows => {
        if (keyRef.current !== periodKey) return
        const m: Record<number, ChecklistEntry> = {}
        rows.forEach(r => { m[r.employee_id] = r })
        setEntries(m)
      })
      .catch(() => {})
    getScheduleNote(periodKey)
      .then(n => {
        if (keyRef.current !== periodKey) return
        setNoteContent(n.content)
        setNoteSavedAt(n.updated_at)
      })
      .catch(() => {})
  }, [periodKey])

  const statsByEmp = useMemo(() => {
    const m: Record<number, PersonStats> = {}
    for (const s of shifts) {
      if (periodRange && (s.date < periodRange.start || s.date >= periodRange.end)) continue
      const cur = m[s.employee_id] ?? { count: 0, hours: 0 }
      cur.count += 1
      cur.hours += hoursBetween(s.start_time, s.end_time)
      m[s.employee_id] = cur
    }
    return m
  }, [shifts, periodRange])

  const people = useMemo(
    () => [...employees].sort((a, b) => {
      const aOpen = statsByEmp[a.id] ? 0 : 1
      const bOpen = statsByEmp[b.id] ? 0 : 1
      if (aOpen !== bOpen) return bOpen - aOpen   // unassigned first
      return (a.name || a.email || '').localeCompare(b.name || b.email || '')
    }),
    [employees, statsByEmp],
  )

  const scheduledCount  = people.filter(p => statsByEmp[p.id]).length
  const consideredCount = people.filter(p => !statsByEmp[p.id] && entries[p.id]?.considered).length
  const openCount       = people.length - scheduledCount - consideredCount
  const allAccounted    = openCount === 0

  const toggleConsidered = async (emp: Employee) => {
    const cur = entries[emp.id]
    const next = !(cur?.considered)
    // optimistic
    setEntries(prev => ({
      ...prev,
      [emp.id]: {
        period_key: periodKey, employee_id: emp.id,
        considered: next ? 1 : 0, note: cur?.note ?? '',
      },
    }))
    try {
      await saveChecklistEntry({
        period_key: periodKey, employee_id: emp.id,
        considered: next, note: cur?.note ?? '',
      })
    } catch {
      msgApi.error('Could not save — try again')
      setEntries(prev => ({ ...prev, [emp.id]: cur ?? {
        period_key: periodKey, employee_id: emp.id, considered: 0, note: '',
      } }))
    }
  }

  const saveConsideredNote = async (emp: Employee, note: string) => {
    const cur = entries[emp.id]
    setEntries(prev => ({
      ...prev,
      [emp.id]: {
        period_key: periodKey, employee_id: emp.id,
        considered: cur?.considered ?? 0, note,
      },
    }))
    try {
      await saveChecklistEntry({
        period_key: periodKey, employee_id: emp.id,
        considered: !!(cur?.considered), note,
      })
    } catch {
      msgApi.error('Could not save the note')
    }
  }

  const handleSaveNote = async () => {
    setSavingNote(true)
    try {
      const saved = await saveScheduleNote(periodKey, noteContent)
      setNoteDirty(false)
      setNoteSavedAt(saved.updated_at)
      msgApi.success('Notes saved')
    } catch {
      msgApi.error('Could not save notes')
    } finally {
      setSavingNote(false)
    }
  }

  return (
    <div className="rounded-xl border border-border overflow-hidden">
      {msgCtx}
      {/* Header: status summary, always visible */}
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 bg-muted/40 text-left"
      >
        {allAccounted ? (
          <CheckCircle2 className="size-4 text-emerald-600 shrink-0" />
        ) : (
          <CircleAlert className="size-4 text-red-500 shrink-0" />
        )}
        <span className="text-sm font-semibold">
          Coverage checklist — {periodLabel}
        </span>
        <span className={cn('text-xs', allAccounted ? 'text-emerald-700' : 'text-red-600')}>
          {allAccounted
            ? 'Everyone assigned or considered'
            : `${openCount} not yet assigned or considered`}
        </span>
        <span className="ml-auto text-xs text-muted-foreground whitespace-nowrap">
          {scheduledCount} scheduled · {consideredCount} considered
          {open ? <ChevronUp className="inline size-3.5 ml-1" /> : <ChevronDown className="inline size-3.5 ml-1" />}
        </span>
      </button>

      {open && (
        <div className="p-3 space-y-3">
          {/* One row per person */}
          <ul className="m-0 p-0 list-none grid gap-1 sm:grid-cols-2">
            {people.map(p => {
              const st    = statsByEmp[p.id]
              const entry = entries[p.id]
              const name  = p.name || p.email || `Employee ${p.id}`
              const state: 'scheduled' | 'considered' | 'open' =
                st ? 'scheduled' : entry?.considered ? 'considered' : 'open'
              return (
                <li
                  key={p.id}
                  className={cn(
                    'flex items-center gap-2 rounded-lg border px-2 py-1.5 text-sm',
                    state === 'scheduled'  && 'border-emerald-200 bg-emerald-50/60',
                    state === 'considered' && 'border-sky-200 bg-sky-50/60',
                    state === 'open'       && 'border-red-200 bg-red-50/60',
                  )}
                >
                  {state === 'scheduled' ? (
                    <CheckCircle2 className="size-4 text-emerald-600 shrink-0" />
                  ) : (
                    <button
                      type="button"
                      title={entry?.considered
                        ? 'Marked as considered — click to unmark'
                        : 'No shifts this period — click to mark as considered (e.g. on vacation)'}
                      onClick={() => toggleConsidered(p)}
                      className="shrink-0 leading-none"
                    >
                      {entry?.considered
                        ? <CheckCircle2 className="size-4 text-sky-600" />
                        : <Circle className="size-4 text-red-400" />}
                    </button>
                  )}
                  <span className="font-medium truncate">{name}</span>
                  {!!p.is_trainee && (
                    <span
                      className="shrink-0 text-[9px] font-bold rounded px-1"
                      style={{ background: '#FEF3C7', color: '#92400E', border: '1px dashed #F59E0B' }}
                    >
                      TRAINEE
                    </span>
                  )}
                  {state === 'scheduled' && st && (
                    <span className="ml-auto text-xs text-muted-foreground whitespace-nowrap">
                      {st.count} shift{st.count === 1 ? '' : 's'} · {Math.round(st.hours * 10) / 10}h
                    </span>
                  )}
                  {state !== 'scheduled' && (
                    <Input
                      size="small"
                      className="ml-auto"
                      style={{ maxWidth: 160, fontSize: 12 }}
                      placeholder={entry?.considered ? 'Reason (optional)' : 'Not scheduled yet'}
                      defaultValue={entry?.note ?? ''}
                      onBlur={e => {
                        const v = e.target.value.trim()
                        if (v !== (entry?.note ?? '')) saveConsideredNote(p, v)
                      }}
                      onPressEnter={e => (e.target as HTMLInputElement).blur()}
                    />
                  )}
                </li>
              )
            })}
          </ul>

          {/* Notes for this period */}
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-sm font-semibold">Notes — {periodLabel}</span>
              {noteSavedAt && !noteDirty && (
                <span className="text-xs text-muted-foreground">
                  saved {dayjs(noteSavedAt + 'Z').isValid() ? dayjs(noteSavedAt + 'Z').format('MMM D, HH:mm') : noteSavedAt}
                </span>
              )}
            </div>
            <Input.TextArea
              rows={2}
              autoSize={{ minRows: 2, maxRows: 6 }}
              placeholder="Notes for this schedule period (visible to managers)…"
              value={noteContent}
              onChange={e => { setNoteContent(e.target.value); setNoteDirty(true) }}
            />
            {noteDirty && (
              <div className="mt-1.5">
                <Button size="sm" onClick={handleSaveNote} disabled={savingNote}>
                  {savingNote ? 'Saving…' : 'Save notes'}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
