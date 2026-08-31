import { useEffect, useMemo, useRef, useState } from 'react'
import { Input, message } from 'antd'
import { CheckCircle2, ChevronDown, ChevronUp, CircleAlert } from 'lucide-react'
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
} from './scheduleApi'
import { manualChecklistPresentation } from './schedulePresentation'

interface Props {
  /** month:YYYY-MM | week:YYYY-MM-DD | day:YYYY-MM-DD */
  periodKey: string
  /** Human title of the visible period (e.g. "August 2026") */
  periodLabel: string
  employees: Employee[]
}

/** A deliberately manual checklist for the visible schedule period. Shift
 *  assignments never change these ticks; managers confirm each person. */
export default function CoveragePanel({
  periodKey, periodLabel, employees,
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

  const people = useMemo(
    () => [...employees].sort((a, b) =>
      (a.name || a.email || '').localeCompare(b.name || b.email || '')),
    [employees],
  )

  const checkedCount   = people.filter(p => !!entries[p.id]?.considered).length
  const uncheckedCount = people.length - checkedCount
  const allChecked     = uncheckedCount === 0

  const toggleManualCheck = async (emp: Employee) => {
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
        className="pc-coverage-header w-full flex items-center gap-2 px-3 py-2 bg-muted/40 text-left"
      >
        {allChecked ? (
          <CheckCircle2 className="size-4 text-emerald-600 shrink-0" />
        ) : (
          <CircleAlert className="size-4 text-red-500 shrink-0" />
        )}
        <span className="text-sm font-semibold">
          Coverage<span className="hidden sm:inline"> checklist</span> — {periodLabel}
        </span>
        <span className={cn('text-xs', allChecked ? 'text-emerald-700' : 'text-red-600')}>
          {allChecked ? 'All checked' : `${uncheckedCount} unchecked`}
        </span>
        <span className="ml-auto hidden text-xs text-muted-foreground whitespace-nowrap sm:inline">
          {checkedCount} checked
        </span>
        {open ? <ChevronUp className="size-3.5 shrink-0" /> : <ChevronDown className="size-3.5 shrink-0" />}
      </button>

      {open && (
        <div className="p-2 space-y-2">
          {/* Small manual checks wrap naturally; shift data never controls them. */}
          <ul
            className="pc-coverage-list m-0 flex list-none flex-wrap gap-1.5 p-0"
            aria-label="Employee coverage checklist"
          >
            {people.map(p => {
              const entry = entries[p.id]
              const name  = p.name || p.email || `Employee ${p.id}`
              const presentation = manualChecklistPresentation(!!entry?.considered)
              const { state } = presentation
              return (
                <li key={p.id}>
                  <label
                  className={cn(
                      'inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md border px-2 text-xs transition-colors',
                      state === 'checked'
                        ? 'border-emerald-200 bg-emerald-50 text-emerald-900'
                        : 'border-border bg-background text-foreground hover:bg-muted/60',
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={state === 'checked'}
                      onChange={() => toggleManualCheck(p)}
                      className="size-3.5 shrink-0 accent-emerald-600"
                      aria-label={`${presentation.statusLabel}: ${name}`}
                    />
                    <span className="max-w-36 truncate font-medium">{name}</span>
                    {!!p.is_trainee && (
                      <span
                        className="shrink-0 rounded px-1 text-[8px] font-bold"
                        style={{ background: '#FEF3C7', color: '#92400E', border: '1px dashed #F59E0B' }}
                      >
                        T
                      </span>
                    )}
                  </label>
                </li>
              )
            })}
          </ul>

          {/* Notes for this period */}
          <details className="pc-coverage-notes rounded-md border border-border bg-muted/20">
            <summary className="flex min-h-8 cursor-pointer items-center gap-2 px-2 text-xs font-medium">
              <span>Period notes</span>
              {noteContent && <span className="size-1.5 rounded-full bg-primary" aria-label="Has notes" />}
              {noteSavedAt && !noteDirty && (
                <span className="ml-auto text-[11px] font-normal text-muted-foreground">
                  saved {dayjs(noteSavedAt + 'Z').isValid() ? dayjs(noteSavedAt + 'Z').format('MMM D, HH:mm') : noteSavedAt}
                </span>
              )}
            </summary>
            <div className="border-t border-border p-2">
              <Input.TextArea
                rows={2}
                autoSize={{ minRows: 2, maxRows: 5 }}
                placeholder={`Notes for ${periodLabel} (visible to managers)…`}
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
          </details>
        </div>
      )}
    </div>
  )
}
