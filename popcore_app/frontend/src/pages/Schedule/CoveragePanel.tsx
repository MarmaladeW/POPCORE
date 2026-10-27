import { useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent } from 'react'
import { createPortal } from 'react-dom'
import { Input, message } from 'antd'
import dayjs from 'dayjs'
import { ListChecks } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { EMPLOYEE_PALETTE, textColorOn } from '@/lib/palette'
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
  employeeColors: Record<number, string>
}

/** Where the floating checklist was left, per browser. */
const PLACEMENT_KEY = 'popcore_schedule_checklist'
const EDGE = 8

function savedPlacement(): { x: number; y: number; open: boolean } {
  try {
    const saved = JSON.parse(localStorage.getItem(PLACEMENT_KEY) || '') as { x?: unknown; y?: unknown; open?: unknown }
    if (Number.isFinite(saved.x) && Number.isFinite(saved.y)) return { x: saved.x as number, y: saved.y as number, open: saved.open === true }
  } catch { /* first visit or storage unavailable */ }
  // Bottom right, clear of the phone navigation bar.
  return { x: window.innerWidth, y: window.innerHeight - 140, open: false }
}

/** A deliberately manual checklist for the visible schedule period. Shift
 *  assignments never change these ticks; managers confirm each person.
 *  It floats over the schedule: drag its header to move it, click the header
 *  to collapse it to an icon or open it again. */
export default function CoveragePanel({
  periodKey, periodLabel, employees, employeeColors,
}: Props) {
  const [entries, setEntries] = useState<Record<number, ChecklistEntry>>({})
  const [noteContent, setNoteContent] = useState('')
  const [noteDirty, setNoteDirty] = useState(false)
  const [noteSavedAt, setNoteSavedAt] = useState<string | null>(null)
  const [savingNote, setSavingNote] = useState(false)
  // Saving is allowed only over a note that loaded: an empty box after a failed load must not replace the stored note.
  const [noteLoad, setNoteLoad] = useState<'loading' | 'loaded' | 'failed'>('loading')
  // Unsaved notes per period: changing the period keeps them for that period instead of dropping them.
  const noteDrafts = useRef<Record<string, string>>({})
  const shownKey = useRef(periodKey)
  const noteRequest = useRef(0)
  const [msgApi, msgCtx] = message.useMessage()
  const [placement, setPlacement] = useState(savedPlacement)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [viewport, setViewport] = useState({ width: window.innerWidth, height: window.innerHeight })
  const floatRef = useRef<HTMLElement>(null)
  const drag = useRef<{ pointerX: number; pointerY: number; x: number; y: number; moved: boolean } | null>(null)
  const { open } = placement
  // The saved spot is where the icon rests; an open panel is nudged back on screen without moving that spot.
  const shown = {
    x: Math.max(EDGE, Math.min(placement.x, viewport.width - size.width - EDGE)),
    y: Math.max(EDGE, Math.min(placement.y, viewport.height - size.height - EDGE)),
  }

  useLayoutEffect(() => {
    const element = floatRef.current
    if (!element) return
    const measure = () => setSize(previous => previous.width === element.offsetWidth && previous.height === element.offsetHeight
      ? previous : { width: element.offsetWidth, height: element.offsetHeight })
    const resize = () => setViewport({ width: window.innerWidth, height: window.innerHeight })
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    window.addEventListener('resize', resize)
    return () => { observer.disconnect(); window.removeEventListener('resize', resize) }
  }, [])

  const place = (next: { x: number; y: number; open: boolean }) => {
    setPlacement(next)
    try { localStorage.setItem(PLACEMENT_KEY, JSON.stringify(next)) } catch { /* placement is a convenience */ }
  }
  const startDrag = (event: PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0) return
    event.currentTarget.setPointerCapture(event.pointerId)
    drag.current = { pointerX: event.clientX, pointerY: event.clientY, x: shown.x, y: shown.y, moved: false }
  }
  const moveDrag = (event: PointerEvent<HTMLButtonElement>) => {
    const from = drag.current
    if (!from) return
    const dx = event.clientX - from.pointerX
    const dy = event.clientY - from.pointerY
    if (!from.moved && Math.hypot(dx, dy) < 5) return
    from.moved = true
    setPlacement(previous => ({ ...previous, x: from.x + dx, y: from.y + dy }))
  }
  const endDrag = () => {
    if (!drag.current?.moved) return
    place({ ...placement, x: shown.x, y: shown.y })
    // The click that follows this release is ignored by toggle; clear the mark in case none follows.
    setTimeout(() => { drag.current = null })
  }
  const toggle = () => {
    // A drag ends with a click on the same header; only a plain click toggles.
    const dragged = drag.current?.moved
    drag.current = null
    if (!dragged) place({ ...placement, open: !open })
  }

  // Ignore late responses after the period changed
  const keyRef = useRef(periodKey)
  keyRef.current = periodKey

  const loadNote = (key: string) => {
    const request = ++noteRequest.current
    setNoteLoad('loading')
    getScheduleNote(key)
      .then(n => {
        if (request !== noteRequest.current) return
        if (!(key in noteDrafts.current)) setNoteContent(n.content)
        setNoteSavedAt(n.updated_at)
        setNoteLoad('loaded')
      })
      .catch(() => { if (request === noteRequest.current) setNoteLoad('failed') })
  }

  useEffect(() => {
    if (shownKey.current !== periodKey && shownKey.current in noteDrafts.current) {
      msgApi.warning('Unsaved period notes were kept. Go back to that period to save them.')
    }
    shownKey.current = periodKey
    setEntries({})
    const draft = noteDrafts.current[periodKey]
    setNoteContent(draft ?? '')
    setNoteDirty(draft !== undefined)
    setNoteSavedAt(null)
    getChecklist(periodKey)
      .then(rows => {
        if (keyRef.current !== periodKey) return
        const m: Record<number, ChecklistEntry> = {}
        rows.forEach(r => { m[r.employee_id] = r })
        setEntries(m)
      })
      .catch(() => {})
    loadNote(periodKey)
  }, [periodKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const people = useMemo(
    () => [...employees].sort((a, b) =>
      Number(!!entries[a.id]?.considered) - Number(!!entries[b.id]?.considered) ||
      (a.name || a.email || '').localeCompare(b.name || b.email || '')),
    [employees, entries],
  )

  const checkedCount   = people.filter(p => !!entries[p.id]?.considered).length

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
    const key = periodKey, content = noteContent
    if (noteLoad !== 'loaded') return
    setSavingNote(true)
    try {
      const saved = await saveScheduleNote(key, content)
      // Text typed while saving stays unsaved; a save that finishes after a period change only clears its own draft.
      if (noteDrafts.current[key] === content) delete noteDrafts.current[key]
      if (keyRef.current !== key) return
      setNoteDirty(key in noteDrafts.current)
      setNoteSavedAt(saved.updated_at)
      msgApi.success('Notes saved')
    } catch {
      msgApi.error('Could not save notes')
    } finally {
      setSavingNote(false)
    }
  }

  return createPortal(
    <section ref={floatRef} className={cn('pc-coverage-float', open && 'pc-coverage-float-open')}
      style={{ left: shown.x, top: shown.y }} aria-label="Period employee checklist">
      {msgCtx}
      <button type="button" className="pc-coverage-toggle" aria-expanded={open}
        aria-label={`Employee checklist, ${checkedCount}/${people.length} reviewed`}
        title={open ? 'Drag to move · click to collapse' : 'Employee checklist — drag to move · click to open'}
        onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={() => { drag.current = null }} onClick={toggle}>
        <ListChecks className="size-4 shrink-0" aria-hidden />
        {open && <strong>Employee checklist</strong>}
        <span role="status">{checkedCount}/{people.length}{open && ' reviewed'}</span>
      </button>
        <div className="pc-coverage-panel space-y-2" hidden={!open}>
          <small className="pc-coverage-period">{periodLabel} · Both stores</small>
          {/* Small manual checks wrap naturally; shift data never controls them. */}
          <ul
            className="pc-coverage-list"
            aria-label="Employee coverage checklist"
          >
            {people.map(p => {
              const entry = entries[p.id]
              const name  = p.name || p.email || `Employee ${p.id}`
              const presentation = manualChecklistPresentation(!!entry?.considered)
              const { state } = presentation
              const color = employeeColors[p.id] ?? EMPLOYEE_PALETTE[0]
              return (
                <li key={p.id}>
                  <label>
                    <input
                      type="checkbox"
                      checked={state === 'checked'}
                      onChange={() => toggleManualCheck(p)}
                      className="size-3.5 shrink-0 accent-indigo-600"
                      aria-label={`${presentation.statusLabel}: ${name}`}
                    />
                    <span className="pc-employee-name" style={{ background: color, color: textColorOn(color) }}>{name}</span>
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
                aria-label="Period notes"
                rows={2}
                autoSize={{ minRows: 2, maxRows: 5 }}
                placeholder={`Notes for ${periodLabel} (visible to managers)…`}
                value={noteContent}
                disabled={noteLoad !== 'loaded'}
                onChange={e => { setNoteContent(e.target.value); setNoteDirty(true); noteDrafts.current[periodKey] = e.target.value }}
              />
              {noteLoad === 'failed' && (
                <div className="mt-1.5 flex items-center gap-2 text-[11px] text-destructive" role="alert">
                  Notes could not be loaded, so they cannot be saved yet.
                  <Button size="sm" variant="outline" onClick={() => loadNote(periodKey)}>Retry</Button>
                </div>
              )}
              {noteDirty && (
                <div className="mt-1.5">
                  <Button size="sm" onClick={handleSaveNote} disabled={savingNote || noteLoad !== 'loaded'}>
                    {savingNote ? 'Saving…' : 'Save notes'}
                  </Button>
                </div>
              )}
            </div>
          </details>
        </div>
    </section>,
    document.body,
  )
}
