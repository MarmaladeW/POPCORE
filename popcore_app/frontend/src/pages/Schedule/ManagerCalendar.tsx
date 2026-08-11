import { useState, useCallback, useRef, useEffect } from 'react'
import FullCalendar from '@fullcalendar/react'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import type { DateClickArg } from '@fullcalendar/interaction'
import type { DatesSetArg, EventClickArg, EventInput } from '@fullcalendar/core'
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

type ViewType = 'dayGridMonth' | 'timeGridWeek'

export default function ManagerCalendar() {
  const calRef  = useRef<FullCalendar>(null)
  const isManager = useHasRole('manager')
  const { selectedStore, stores, setStores } = useAppStore()
  const isAll = selectedStore?.code === 'ALL'

  const [employees,    setEmployees]    = useState<Employee[]>([])
  const [empStores,    setEmpStores]    = useState<Record<number, string[]>>({})
  const [filterEmpId,  setFilterEmpId]  = useState<number | null>(null)
  const [allEvents,    setAllEvents]    = useState<EventInput[]>([])
  const [visibleEvents, setVisibleEvents] = useState<EventInput[]>([])
  const [viewTitle,    setViewTitle]    = useState('')
  const [viewType,     setViewType]     = useState<ViewType>('dayGridMonth')

  const [modalOpen,    setModalOpen]    = useState(false)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [selectedShift, setSelectedShift] = useState<Shift | null>(null)
  const [availForDate, setAvailForDate] = useState<Availability[]>([])
  const [empHours,     setEmpHours]     = useState<EmployeeHours | null>(null)

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

  useEffect(() => {
    if (employees.length > 0 && currentRange) {
      loadEvents(currentRange.start, currentRange.end)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employees])

  // Reload when selected store changes
  useEffect(() => {
    if (currentRange) {
      loadEvents(currentRange.start, currentRange.end)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedStore])

  useEffect(() => {
    if (filterEmpId === null) {
      setVisibleEvents(allEvents)
    } else {
      setVisibleEvents(
        allEvents.filter((e) => e.extendedProps?.employee_id === filterEmpId)
      )
    }
  }, [allEvents, filterEmpId])

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
  }, [filterEmpId, allEvents])

  const loadEvents = useCallback(
    async (start: string, end: string) => {
      const sc    = selectedStore?.code
      const isAllMode = sc === 'ALL'

      const [avails, shifts]: [Availability[], Shift[]] = await Promise.all([
        getAllAvailability(start, end, sc),
        getShifts({ start, end, store_code: sc }),
      ])

      const empIdToIdx: Record<number, number> = {}
      employees.forEach((e, i) => { empIdToIdx[e.id] = i })

      shiftById.current    = {}
      availsByDate.current = {}
      const evts: EventInput[] = []

      for (const a of avails) {
        if (!availsByDate.current[a.date]) availsByDate.current[a.date] = []
        availsByDate.current[a.date].push(a)

        const empColor = empColorRef.current[a.employee_id]
          ?? FALLBACK_COLORS[empIdToIdx[a.employee_id] ?? 0]
        evts.push({
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

      for (const s of shifts) {
        shiftById.current[s.id] = s
        const empColor = empColorRef.current[s.employee_id]
          ?? FALLBACK_COLORS[empIdToIdx[s.employee_id] ?? 0]
        const storePrefix = isAllMode && s.store_code ? `[${s.store_code}] ` : ''

        let bgColor: string
        let borderColor: string
        let textColor: string

        if (isAllMode && s.store_code) {
          // Tint with store color; use employee color for border so employees remain distinct
          const storeColor = storeColorRef.current[s.store_code] || '#6366f1'
          bgColor     = storeColor + '33'  // ~20% opacity tint
          borderColor = empColor
          textColor   = '#1f2937'
        } else {
          bgColor     = empColor
          borderColor = empColor
          textColor   = '#fff'
        }

        evts.push({
          id:              `shift-${s.id}`,
          title:           `${storePrefix}${s.employee_name ?? 'Employee'} ${s.start_time}–${s.end_time}`,
          start:           `${s.date}T${s.start_time}`,
          end:             `${s.date}T${s.end_time}`,
          backgroundColor: bgColor,
          borderColor:     borderColor,
          textColor:       textColor,
          extendedProps:   { type: 'shift', shift_id: s.id, employee_id: s.employee_id },
        })
      }

      setAllEvents(evts)
    },
    [employees, selectedStore]
  )

  const handleDatesSet = useCallback(
    (arg: DatesSetArg) => {
      const start = dayjs(arg.start).format('YYYY-MM-DD')
      const end   = dayjs(arg.end).format('YYYY-MM-DD')
      setCurrentRange({ start, end })
      setViewTitle(arg.view.title)
      setViewType(arg.view.type as ViewType)
      loadEvents(start, end)
    },
    [loadEvents]
  )

  const handleDateClick = useCallback((arg: DateClickArg) => {
    setSelectedDate(arg.dateStr)
    setSelectedShift(null)
    setAvailForDate(availsByDate.current[arg.dateStr] ?? [])
    setModalOpen(true)
  }, [])

  const handleEventClick = useCallback((arg: EventClickArg) => {
    const { type, shift_id } = arg.event.extendedProps as { type: string; shift_id?: number }
    if (type === 'shift' && shift_id != null) {
      const shift = shiftById.current[shift_id]
      if (shift) {
        setSelectedDate(shift.date)
        setSelectedShift(shift)
        setAvailForDate(availsByDate.current[shift.date] ?? [])
        setModalOpen(true)
      }
    } else if (type === 'availability') {
      const dateStr = dayjs(arg.event.start!).format('YYYY-MM-DD')
      setSelectedDate(dateStr)
      setSelectedShift(null)
      setAvailForDate(availsByDate.current[dateStr] ?? [])
      setModalOpen(true)
    }
  }, [])

  const handleSaved = useCallback(() => {
    const api = calRef.current?.getApi()
    if (api) {
      const view  = api.view
      const start = dayjs(view.activeStart).format('YYYY-MM-DD')
      const end   = dayjs(view.activeEnd).format('YYYY-MM-DD')
      loadEvents(start, end)
    }
  }, [loadEvents])

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
    // Rebuild events with new store tint
    if (currentRange) loadEvents(currentRange.start, currentRange.end)
  }

  const cal = () => calRef.current?.getApi()

  return (
    <div className="space-y-3">

      {/* Row 1: navigation ←→ + Today + Month|Week toggle */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-0.5">
          <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => cal()?.prev()}>
            <ChevronLeft className="size-4" />
          </Button>
          <span className="text-sm font-semibold min-w-24 text-center px-1">{viewTitle}</span>
          <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => cal()?.next()}>
            <ChevronRight className="size-4" />
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" className="h-8" onClick={() => cal()?.today()}>Today</Button>
          <div className="flex rounded-lg border border-border overflow-hidden text-xs font-medium">
            <button
              className={cn(
                'px-3 h-8 transition-colors',
                viewType === 'dayGridMonth'
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-background text-foreground hover:bg-muted',
              )}
              onClick={() => cal()?.changeView('dayGridMonth')}
            >Month</button>
            <button
              className={cn(
                'px-3 h-8 border-l border-border transition-colors',
                viewType === 'timeGridWeek'
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-background text-foreground hover:bg-muted',
              )}
              onClick={() => cal()?.changeView('timeGridWeek')}
            >Week</button>
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
          onClick={() => currentRange && loadEvents(currentRange.start, currentRange.end)}
        >
          <RefreshCw className="size-3.5" />
        </Button>
      </div>

      {/* Store Colors panel — managers only, visible in ALL mode */}
      {isAll && isManager && stores.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 px-3 py-2 rounded-lg border border-border bg-muted/30">
          <span className="text-xs font-medium text-muted-foreground">Store Colors:</span>
          {stores.map(st => (
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

      {/* Calendar card */}
      <div className="rounded-xl border border-border overflow-hidden">
        <FullCalendar
          ref={calRef}
          plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin]}
          initialView="dayGridMonth"
          headerToolbar={false}
          height="auto"
          timeZone="local"
          events={visibleEvents}
          datesSet={handleDatesSet}
          dateClick={handleDateClick}
          eventClick={handleEventClick}
          eventTimeFormat={{ hour: '2-digit', minute: '2-digit', hour12: false }}
        />
      </div>

      <ShiftModal
        open={modalOpen}
        date={selectedDate}
        employees={employees}
        existing={selectedShift}
        availForDate={availForDate}
        onClose={() => setModalOpen(false)}
        onSaved={handleSaved}
      />
    </div>
  )
}
