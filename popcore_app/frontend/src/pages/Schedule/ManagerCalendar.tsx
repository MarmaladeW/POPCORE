import { useState, useCallback, useRef, useEffect } from 'react'
import FullCalendar from '@fullcalendar/react'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import type { DateClickArg } from '@fullcalendar/interaction'
import type { CalendarApi, DatesSetArg, EventClickArg, EventInput } from '@fullcalendar/core'
import { RefreshCw, ChevronLeft, ChevronRight } from 'lucide-react'
import dayjs from 'dayjs'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import {
  getAllAvailability,
  getShifts,
  getEmployees,
  getEmployeeStores,
  getEmployeeHours,
  type Availability,
  type Employee,
  type EmployeeHours,
  type Shift,
} from './scheduleApi'
import { BUSINESS_HOURS, understaffedIntervals } from './openHours'
import ShiftModal from './ShiftModal'
import { useAppStore } from '../../store'
import { useHasRole } from '../../auth/useRole'
import client from '../../api/client'

/** Fallback palette used when DB color is not yet loaded */
const FALLBACK_COLORS = [
  '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#14B8A6',
  '#F97316', '#06B6D4', '#84CC16', '#A855F7', '#F43F5E',
  '#0EA5E9', '#22C55E', '#FB923C', '#E879F9', '#64748B',
]

const UNCOVERED_COLOR = '#ef4444'

type ViewType = 'dayGridMonth' | 'timeGridWeek' | 'timeGridDay'

const VIEW_OPTIONS: { value: ViewType; label: string }[] = [
  { value: 'dayGridMonth', label: 'Month' },
  { value: 'timeGridWeek', label: 'Week' },
  { value: 'timeGridDay',  label: 'Day' },
]

export default function ManagerCalendar() {
  const isManager = useHasRole('manager')
  const { stores, setStores } = useAppStore()
  const realStores = stores.filter((s) => s.code !== 'ALL')
  const storesKey  = realStores.map((s) => s.code).join(',')

  const calRefs = useRef<Record<string, FullCalendar | null>>({})

  const [employees,     setEmployees]     = useState<Employee[]>([])
  const [empStores,     setEmpStores]     = useState<Record<number, string[]>>({})
  const [filterEmpId,   setFilterEmpId]   = useState<number | null>(null)
  const [eventsByStore, setEventsByStore] = useState<Record<string, EventInput[]>>({})
  const [viewTitle,     setViewTitle]     = useState('')
  const [viewType,      setViewType]      = useState<ViewType>('dayGridMonth')
  const [empHours,      setEmpHours]      = useState<EmployeeHours | null>(null)

  const [modalOpen,      setModalOpen]      = useState(false)
  const [modalStoreCode, setModalStoreCode] = useState<string | null>(null)
  const [selectedDate,   setSelectedDate]   = useState<string | null>(null)
  const [selectedShift,  setSelectedShift]  = useState<Shift | null>(null)
  const [availForDate,   setAvailForDate]   = useState<Availability[]>([])

  const shiftById    = useRef<Record<number, Shift>>({})
  const availsByDate = useRef<Record<string, Availability[]>>({})
  const [currentRange, setCurrentRange] = useState<{ start: string; end: string } | null>(null)

  // Color maps kept in refs so loadEvents always reads current values
  // without needing to be in useCallback deps
  const empColorRef   = useRef<Record<number, string>>({})
  const storeColorRef = useRef<Record<string, string>>({})

  // Keep storeColorRef in sync with global stores (updated on mount + color PATCH)
  useEffect(() => {
    const m: Record<string, string> = {}
    stores.forEach(s => { m[s.code] = s.color || '#6366f1' })
    storeColorRef.current = m
  }, [stores])

  useEffect(() => {
    getEmployees().then(setEmployees).catch(() => {})
    getEmployeeStores()
      .then(data => {
        const storeM: Record<number, string[]> = {}
        const colorM: Record<number, string>   = {}
        data.forEach(e => {
          storeM[e.employee_id] = e.stores
          colorM[e.employee_id] = e.color || FALLBACK_COLORS[0]
        })
        setEmpStores(storeM)
        empColorRef.current = colorM
      })
      .catch(() => {})
  }, [])

  const loadEvents = useCallback(
    async (start: string, end: string, vt: ViewType) => {
      const [avails, shifts]: [Availability[], Shift[]] = await Promise.all([
        getAllAvailability(start, end, 'ALL'),
        getShifts({ start, end, store_code: 'ALL' }),
      ])

      const empIdToIdx: Record<number, number> = {}
      employees.forEach((e, i) => { empIdToIdx[e.id] = i })

      shiftById.current    = {}
      availsByDate.current = {}

      const byStore: Record<string, EventInput[]> = {}
      const codes = realStores.map((s) => s.code)
      codes.forEach((c) => { byStore[c] = [] })

      for (const a of avails) {
        if (!availsByDate.current[a.date]) availsByDate.current[a.date] = []
        availsByDate.current[a.date].push(a)

        const code = a.store_code || ''
        if (!byStore[code]) continue
        const empColor = empColorRef.current[a.employee_id]
          ?? FALLBACK_COLORS[empIdToIdx[a.employee_id] ?? 0]
        byStore[code].push({
          id: `avail-${a.id}`,
          title: `${a.employee_name ?? 'Employee'} available`,
          start: `${a.date}T${a.start_time}`,
          end:   `${a.date}T${a.end_time}`,
          backgroundColor: empColor + '33',
          borderColor:     empColor,
          textColor:       '#374151',
          display:         'background',
          extendedProps:   { type: 'availability', employee_id: a.employee_id },
        })
      }

      // Shifts, plus per-store per-day index for coverage computation
      const shiftsByStoreDate: Record<string, Record<string, Shift[]>> = {}
      codes.forEach((c) => { shiftsByStoreDate[c] = {} })

      for (const s of shifts) {
        shiftById.current[s.id] = s
        const code = s.store_code || ''
        if (!byStore[code]) continue
        if (!shiftsByStoreDate[code][s.date]) shiftsByStoreDate[code][s.date] = []
        shiftsByStoreDate[code][s.date].push(s)

        const empColor = empColorRef.current[s.employee_id]
          ?? FALLBACK_COLORS[empIdToIdx[s.employee_id] ?? 0]
        byStore[code].push({
          id:              `shift-${s.id}`,
          title:           `${s.employee_name ?? 'Employee'} ${s.start_time}–${s.end_time}`,
          start:           `${s.date}T${s.start_time}`,
          end:             `${s.date}T${s.end_time}`,
          backgroundColor: empColor,
          borderColor:     empColor,
          textColor:       '#fff',
          extendedProps:   { type: 'shift', shift_id: s.id, employee_id: s.employee_id },
        })
      }

      // Staffing overlays: opening hours (12–22 Mon–Fri, 11–22 Sat–Sun) with
      // fewer staff scheduled than the store requires (DT: 3 at all times,
      // MK: 1 weekday / 2 weekend) show up red. Month view tints the whole
      // day; week/day views mark the exact time range, labelled "have/need".
      const last = dayjs(end)
      for (const code of codes) {
        for (let d = dayjs(start); d.isBefore(last); d = d.add(1, 'day')) {
          const dateStr = d.format('YYYY-MM-DD')
          const gaps = understaffedIntervals(code, dateStr, shiftsByStoreDate[code][dateStr] ?? [])
          if (gaps.length === 0) continue
          if (vt === 'dayGridMonth') {
            byStore[code].push({
              id:              `gap-${code}-${dateStr}`,
              start:           dateStr,
              allDay:          true,
              display:         'background',
              backgroundColor: UNCOVERED_COLOR,
              extendedProps:   { type: 'coverage' },
            })
          } else {
            gaps.forEach((g, i) => {
              byStore[code].push({
                id:              `gap-${code}-${dateStr}-${i}`,
                title:           `${g.have}/${g.need}`,
                start:           `${dateStr}T${g.start}`,
                end:             `${dateStr}T${g.end}`,
                display:         'background',
                backgroundColor: UNCOVERED_COLOR,
                extendedProps:   { type: 'coverage' },
              })
            })
          }
        }
      }

      setEventsByStore(byStore)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [employees, storesKey]
  )

  useEffect(() => {
    if (currentRange) {
      loadEvents(currentRange.start, currentRange.end, viewType)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employees, storesKey])

  // Hours worked this wage month for the filtered employee
  useEffect(() => {
    if (filterEmpId === null) {
      setEmpHours(null)
      return
    }
    let cancelled = false
    getEmployeeHours(filterEmpId)
      .then((h) => { if (!cancelled) setEmpHours(h) })
      .catch(() => { if (!cancelled) setEmpHours(null) })
    return () => { cancelled = true }
  }, [filterEmpId, eventsByStore])

  const eachCal = (fn: (api: CalendarApi) => void) => {
    Object.values(calRefs.current).forEach((c) => {
      const api = c?.getApi()
      if (api) fn(api)
    })
  }

  // Only the first store's calendar drives range/title state + data loading —
  // all calendars are always navigated together so their ranges are identical.
  const handleDatesSet = useCallback(
    (arg: DatesSetArg) => {
      const start = dayjs(arg.start).format('YYYY-MM-DD')
      const end   = dayjs(arg.end).format('YYYY-MM-DD')
      const vt    = arg.view.type as ViewType
      setCurrentRange({ start, end })
      setViewTitle(arg.view.title)
      setViewType(vt)
      loadEvents(start, end, vt)
    },
    [loadEvents]
  )

  const handleDateClickFor = (storeCode: string) => (arg: DateClickArg) => {
    const dateStr = arg.dateStr.slice(0, 10)
    setModalStoreCode(storeCode)
    setSelectedDate(dateStr)
    setSelectedShift(null)
    setAvailForDate(availsByDate.current[dateStr] ?? [])
    setModalOpen(true)
  }

  const handleEventClick = useCallback((arg: EventClickArg) => {
    const { type, shift_id } = arg.event.extendedProps as { type: string; shift_id?: number }
    if (type === 'shift' && shift_id != null) {
      const shift = shiftById.current[shift_id]
      if (shift) {
        setModalStoreCode(shift.store_code ?? null)
        setSelectedDate(shift.date)
        setSelectedShift(shift)
        setAvailForDate(availsByDate.current[shift.date] ?? [])
        setModalOpen(true)
      }
    }
  }, [])

  const handleSaved = useCallback(() => {
    if (currentRange) {
      loadEvents(currentRange.start, currentRange.end, viewType)
    }
  }, [loadEvents, currentRange, viewType])

  async function handleStoreColorChange(storeId: number, storeCode: string, color: string) {
    // Optimistically update global store list so picker reflects new color immediately
    setStores(stores.map(s => s.id === storeId ? { ...s, color } : s))
    storeColorRef.current = { ...storeColorRef.current, [storeCode]: color }
    try {
      await client.patch(`/stores/${storeId}/color`, { color })
    } catch {
      // On failure revert: refetch stores is not straightforward here;
      // the optimistic update stays until page refresh. Non-critical.
    }
  }

  const visibleEvents = (evts: EventInput[]) =>
    filterEmpId === null
      ? evts
      : evts.filter((e) => {
          const p = e.extendedProps as { type?: string; employee_id?: number } | undefined
          return p?.type === 'coverage' || p?.employee_id === filterEmpId
        })

  const isTimeGrid = viewType !== 'dayGridMonth'

  return (
    <div className="space-y-3">

      {/* Row 1: navigation ←→ + Today + Month|Week|Day toggle */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-0.5">
          <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => eachCal((a) => a.prev())}>
            <ChevronLeft className="size-4" />
          </Button>
          <span className="text-sm font-semibold min-w-24 text-center px-1">{viewTitle}</span>
          <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => eachCal((a) => a.next())}>
            <ChevronRight className="size-4" />
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" className="h-8" onClick={() => eachCal((a) => a.today())}>Today</Button>
          <div className="flex rounded-lg border border-border overflow-hidden text-xs font-medium">
            {VIEW_OPTIONS.map((v, i) => (
              <button
                key={v.value}
                className={cn(
                  'px-3 h-8 transition-colors',
                  i > 0 && 'border-l border-border',
                  viewType === v.value
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-background text-foreground hover:bg-muted',
                )}
                onClick={() => eachCal((a) => a.changeView(v.value))}
              >{v.label}</button>
            ))}
          </div>
        </div>
      </div>

      {/* Row 2: employee filter + refresh */}
      <div className="flex items-center gap-2">
        <Select
          value={filterEmpId !== null ? String(filterEmpId) : '__all__'}
          onValueChange={(v) => setFilterEmpId(v === '__all__' ? null : Number(v))}
        >
          <SelectTrigger className="h-8 text-sm w-44">
            <SelectValue placeholder="All employees" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">All employees</SelectItem>
            {employees.map((e) => (
              <SelectItem key={e.id} value={String(e.id)}>
                {e.name || e.email || e.auth0_id}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          title="Refresh"
          onClick={() => currentRange && loadEvents(currentRange.start, currentRange.end, viewType)}
        >
          <RefreshCw className="size-3.5" />
        </Button>
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground ml-auto">
          <span
            className="size-3 rounded-sm shrink-0"
            style={{ background: UNCOVERED_COLOR, opacity: 0.35 }}
          />
          Understaffed opening hours — DT needs 3 at all times · MK needs 1 weekday / 2 weekend
        </span>
      </div>

      {/* Store Colors panel — managers only */}
      {isManager && realStores.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 px-3 py-2 rounded-lg border border-border bg-muted/30">
          <span className="text-xs font-medium text-muted-foreground">Store Colors:</span>
          {realStores.map(st => (
            <label
              key={st.id}
              className="flex items-center gap-1.5 cursor-pointer"
              title={`Change color for ${st.name || st.code}`}
            >
              <input
                type="color"
                value={st.color || '#6366f1'}
                onChange={e => handleStoreColorChange(st.id, st.code, e.target.value)}
                style={{
                  width: 22, height: 22, padding: 1,
                  borderRadius: 4, border: '1px solid #e5e7eb',
                  cursor: 'pointer', background: 'none',
                }}
              />
              <span className="text-xs text-muted-foreground">{st.name || st.code}</span>
            </label>
          ))}
        </div>
      )}

      {/* Employee legend: clickable pill chips — click to filter + see hours */}
      {employees.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {employees.map((e) => {
            const empColor = empColorRef.current[e.id] ?? FALLBACK_COLORS[0]
            const active   = filterEmpId === e.id
            return (
              <button
                key={e.id}
                type="button"
                onClick={() => setFilterEmpId(active ? null : e.id)}
                title={active
                  ? 'Click to show everyone again'
                  : `Show only ${e.name || e.email || `Employee ${e.id}`}'s shifts and their hours this month`}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs cursor-pointer transition-colors border',
                  active
                    ? 'bg-primary text-primary-foreground border-primary'
                    : 'bg-muted text-muted-foreground border-transparent hover:border-border',
                )}
              >
                <span
                  className="size-2 rounded-full shrink-0"
                  style={{ background: empColor }}
                />
                {e.name || e.email || `Employee ${e.id}`}
                {(empStores[e.id] ?? []).map(code => (
                  <span
                    key={code}
                    style={{
                      background: storeColorRef.current[code] ?? '#9ca3af',
                      color:      '#fff',
                      fontSize:   9,
                      borderRadius: 3,
                      padding:    '1px 4px',
                      lineHeight: 1.4,
                      fontWeight: 600,
                    }}
                  >{code}</span>
                ))}
              </button>
            )
          })}
        </div>
      )}

      {/* Hours summary for the filtered employee (wage month, all stores) */}
      {filterEmpId !== null && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm">
          {empHours ? (
            <>
              <span className="font-semibold">
                {empHours.name || empHours.email || `Employee ${empHours.employee_id}`}
              </span>
              <span>
                <span className="font-semibold">{empHours.total_hours}h</span>
                <span className="text-muted-foreground"> · {empHours.shift_count} shift{empHours.shift_count === 1 ? '' : 's'}</span>
              </span>
              <span className="text-muted-foreground">
                {dayjs(empHours.period_start).format('MMM D')} – {dayjs(empHours.period_end).format('MMM D, YYYY')}
              </span>
              {Object.entries(empHours.by_store).map(([code, h]) => (
                <span key={code} className="text-xs text-muted-foreground">
                  <span
                    className="inline-block size-2 rounded-full mr-1"
                    style={{ background: storeColorRef.current[code] ?? '#9ca3af' }}
                  />
                  {code}: {h}h
                </span>
              ))}
              <button
                type="button"
                className="ml-auto text-xs text-muted-foreground underline hover:text-foreground"
                onClick={() => setFilterEmpId(null)}
              >
                Show everyone
              </button>
            </>
          ) : (
            <span className="text-muted-foreground">Loading hours…</span>
          )}
        </div>
      )}

      {/* One calendar section per store, always navigated in sync */}
      {realStores.length === 0 && (
        <div className="text-sm text-muted-foreground px-1">Loading stores…</div>
      )}
      {realStores.map((st, i) => (
        <section key={st.code} className="space-y-1.5">
          <header className="flex items-center gap-2 pt-1">
            <span
              className="size-3 rounded-full shrink-0"
              style={{ background: st.color || '#6366f1' }}
            />
            <h4 className="text-sm font-semibold m-0">{st.name || st.code}</h4>
            <span className="text-xs text-muted-foreground">{st.code}</span>
          </header>
          <div
            className="rounded-xl border overflow-hidden"
            style={{ borderColor: (st.color || '#6366f1') + '66' }}
          >
            <FullCalendar
              ref={(el) => { calRefs.current[st.code] = el }}
              plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin]}
              initialView={viewType}
              headerToolbar={false}
              height="auto"
              timeZone="local"
              events={visibleEvents(eventsByStore[st.code] ?? [])}
              datesSet={i === 0 ? handleDatesSet : undefined}
              dateClick={handleDateClickFor(st.code)}
              eventClick={handleEventClick}
              eventTimeFormat={{ hour: '2-digit', minute: '2-digit', hour12: false }}
              businessHours={BUSINESS_HOURS}
              dayMaxEvents={4}
              allDaySlot={false}
              nowIndicator
              slotMinTime="10:00"
              slotMaxTime="23:00"
              slotDuration="01:00"
              slotLabelInterval="01:00"
            />
          </div>
        </section>
      ))}
      {isTimeGrid && realStores.length > 0 && (
        <p className="text-xs text-muted-foreground px-1">
          Grey areas are outside opening hours. Red areas have fewer staff scheduled than
          the store needs (labelled scheduled/required) — click one to assign a shift.
        </p>
      )}

      <ShiftModal
        open={modalOpen}
        date={selectedDate}
        employees={employees}
        existing={selectedShift}
        availForDate={availForDate}
        defaultStoreCode={modalStoreCode}
        onClose={() => setModalOpen(false)}
        onSaved={handleSaved}
      />
    </div>
  )
}
