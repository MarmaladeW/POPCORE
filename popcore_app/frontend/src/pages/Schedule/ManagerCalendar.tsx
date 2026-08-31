import { useState, useCallback, useRef, useEffect } from 'react'
import FullCalendar from '@fullcalendar/react'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import type { DateClickArg } from '@fullcalendar/interaction'
import type {
  CalendarApi, DatesSetArg, EventClickArg, EventContentArg, EventInput,
} from '@fullcalendar/core'
import { RefreshCw, ChevronLeft, ChevronRight, Info } from 'lucide-react'
import { Popover } from 'antd'
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
  getScheduleConfig,
  type Availability,
  type Employee,
  type EmployeeHours,
  type Shift,
} from './scheduleApi'
import {
  DEFAULT_STAFF_REQUIREMENTS,
  DEFAULT_STORE_HOURS,
  businessHoursFrom,
  compactRange,
  gridWindow,
  hoursForStore,
  parseShiftPresets,
  parsePositions,
  parseStoreOpenHours,
  parseStaffRequirements,
  shiftKindFor,
  understaffedIntervals,
  type PositionsMap,
  type ShiftKind,
  type ShiftPreset,
  type StaffRequirements,
  type StoreHoursMap,
} from './openHours'
import ShiftModal from './ShiftModal'
import CoveragePanel from './CoveragePanel'
import { useAppStore } from '../../store'
import { useIsMobile } from '../../hooks/useIsMobile'
import {
  compactEmployeeLabel,
  mobileMonthEventLimit,
  mobileShiftAccessibleLabel,
  shiftColorPresentation,
} from './schedulePresentation'

import { EMPLOYEE_PALETTE, textColorOn } from '../../lib/palette'

/** Fallback palette used when DB color is not yet loaded */
const FALLBACK_COLORS = EMPLOYEE_PALETTE

const UNCOVERED_COLOR = '#ef4444'

type ViewType = 'dayGridMonth' | 'timeGridWeek' | 'timeGridDay'

const VIEW_OPTIONS: { value: ViewType; label: string }[] = [
  { value: 'dayGridMonth', label: 'Month' },
  { value: 'timeGridWeek', label: 'Week' },
  { value: 'timeGridDay',  label: 'Day' },
]

export default function ManagerCalendar() {
  const { stores } = useAppStore()
  const isMobile = useIsMobile()
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

  const [staffReqs,      setStaffReqs]      = useState<StaffRequirements>(DEFAULT_STAFF_REQUIREMENTS)
  const [storeHours,     setStoreHours]     = useState<StoreHoursMap>(DEFAULT_STORE_HOURS)
  const [shiftPresets,   setShiftPresets]   = useState<ShiftPreset[]>([])
  const [positionsMap,   setPositionsMap]   = useState<PositionsMap>({})

  // Coverage checklist + notes are keyed to the calendar's real period
  // (month:YYYY-MM / week:YYYY-MM-DD / day:YYYY-MM-DD)
  const [periodKey,    setPeriodKey]    = useState<string>('')

  const [modalOpen,      setModalOpen]      = useState(false)
  const [modalStoreCode, setModalStoreCode] = useState<string | null>(null)
  const [selectedDate,   setSelectedDate]   = useState<string | null>(null)
  const [selectedShift,  setSelectedShift]  = useState<Shift | null>(null)
  const [availForDate,   setAvailForDate]   = useState<Availability[]>([])

  const shiftById    = useRef<Record<number, Shift>>({})
  const availsByDate = useRef<Record<string, Availability[]>>({})
  const [currentRange, setCurrentRange] = useState<{ start: string; end: string } | null>(null)
  // Real period bounds (end exclusive) — used to skip the red understaffing
  // tint on a month grid's padding days from adjacent months
  const periodRangeRef = useRef<{ start: string; end: string } | null>(null)

  // Employee colors are state (rather than a ref) so events reload if the
  // saved color map arrives after FullCalendar's first date-range request.
  const [empColors, setEmpColors] = useState<Record<number, string>>({})
  const storeColorRef = useRef<Record<string, string>>({})

  // Keep storeColorRef in sync with global stores (updated on mount + color PATCH)
  useEffect(() => {
    const m: Record<string, string> = {}
    stores.forEach(s => { m[s.code] = s.color || '#6366f1' })
    storeColorRef.current = m
  }, [stores])

  useEffect(() => {
    // Staffing requirements + opening hours are admin-configurable in
    // Settings → Scheduling
    getScheduleConfig()
      .then(cfg => {
        setStaffReqs(parseStaffRequirements(cfg.schedule_required_staff))
        setStoreHours(parseStoreOpenHours(cfg.schedule_open_hours))
        setShiftPresets(parseShiftPresets(cfg.schedule_shift_presets))
        setPositionsMap(parsePositions(cfg.schedule_positions))
      })
      .catch(() => {})   // keep defaults if config can't be loaded
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
        setEmpColors(colorM)
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
        const empColor = empColors[a.employee_id]
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

        const empColor = empColors[s.employee_id]
          ?? FALLBACK_COLORS[empIdToIdx[s.employee_id] ?? 0]
        const kind      = shiftKindFor(s.date, hoursForStore(code, storeHours), s.start_time, s.end_time)
        const isTrainee = !!s.is_trainee
        // Every shift keeps the employee's solid color. Time, AM/PM, position,
        // and trainee markers carry the details without weakening that color.
        const shiftColors = shiftColorPresentation(empColor, kind)
        byStore[code].push({
          id:              `shift-${s.id}`,
          title:           `${s.employee_name ?? 'Employee'} ${s.start_time}–${s.end_time}`,
          start:           `${s.date}T${s.start_time}`,
          end:             `${s.date}T${s.end_time}`,
          backgroundColor: shiftColors.backgroundColor,
          borderColor:     shiftColors.borderColor,
          textColor:       textColorOn(empColor),
          display:         shiftColors.display,
          classNames:      ['pc-ev', `pc-ev-${kind}`, ...(isTrainee ? ['pc-ev-trainee'] : [])],
          extendedProps:   {
            type: 'shift', shift_id: s.id, employee_id: s.employee_id,
            kind, is_trainee: isTrainee, position: s.position || '',
            emp_name: s.employee_name ?? 'Employee', emp_color: empColor,
            start_time: s.start_time, end_time: s.end_time,
          },
        })
      }

      // Staffing overlays: opening hours (12–22 Mon–Fri, 11–22 Sat–Sun) with
      // fewer staff scheduled than the store requires (Settings → Scheduling)
      // show up red. Month view tints the whole day; week/day views mark the
      // exact time range, labelled "have/need".
      const last = dayjs(end)
      const period = periodRangeRef.current
      for (const code of codes) {
        for (let d = dayjs(start); d.isBefore(last); d = d.add(1, 'day')) {
          const dateStr = d.format('YYYY-MM-DD')
          // Month grid: don't flood padding days from adjacent months with red
          if (vt === 'dayGridMonth' && period
              && (dateStr < period.start || dateStr >= period.end)) continue
          const gaps = understaffedIntervals(
            code, dateStr, shiftsByStoreDate[code][dateStr] ?? [], staffReqs, storeHours,
          )
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
    [employees, storesKey, staffReqs, storeHours, empColors]
  )

  useEffect(() => {
    if (currentRange) {
      loadEvents(currentRange.start, currentRange.end, viewType)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employees, storesKey, staffReqs, storeHours, empColors])

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
      // The "real" period (month/week/day) — excludes the padding days a
      // month grid shows from adjacent months
      const cs = dayjs(arg.view.currentStart)
      setPeriodKey(
        vt === 'dayGridMonth' ? `month:${cs.format('YYYY-MM')}`
        : vt === 'timeGridWeek' ? `week:${cs.format('YYYY-MM-DD')}`
        : `day:${cs.format('YYYY-MM-DD')}`,
      )
      const pr = {
        start: cs.format('YYYY-MM-DD'),
        end:   dayjs(arg.view.currentEnd).format('YYYY-MM-DD'),
      }
      periodRangeRef.current = pr
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

  const visibleEvents = (evts: EventInput[]) =>
    filterEmpId === null
      ? evts
      : evts.filter((e) => {
          const p = e.extendedProps as { type?: string; employee_id?: number } | undefined
          return p?.type === 'coverage' || p?.employee_id === filterEmpId
        })

  // Custom shift rendering: no more duplicated "12:00 name 12:00–22:00" —
  // one bold name plus a compact, readable time chip (and position if set).
  const renderEvent = (arg: EventContentArg) => {
    const p = arg.event.extendedProps as {
      type?: string; kind?: ShiftKind; is_trainee?: boolean; position?: string
      emp_name?: string; emp_color?: string; start_time?: string; end_time?: string
    }
    if (p.type !== 'shift') return true   // default rendering for backgrounds
    const kind    = p.kind ?? 'custom'
    const halfTag = kind === 'first' ? 'AM' : kind === 'second' ? 'PM' : ''
    const range   = compactRange(p.start_time ?? '', p.end_time ?? '')
    const tip = [
      p.emp_name,
      `${p.start_time}–${p.end_time}`,
      kind === 'full' ? 'Full day' : kind === 'custom' ? 'Custom slot' : `Half day (${halfTag})`,
      p.position && `Position: ${p.position}`,
      p.is_trainee && 'TRAINEE',
    ].filter(Boolean).join(' · ')

    if (arg.view.type === 'dayGridMonth') {
      if (isMobile) {
        const employeeName = p.emp_name ?? 'Employee'
        const employeeColor = p.emp_color ?? '#6366F1'
        const accessibleLabel = mobileShiftAccessibleLabel({
          employeeName,
          startTime: p.start_time ?? '',
          endTime: p.end_time ?? '',
          position: p.position ?? '',
          isTrainee: !!p.is_trainee,
        })
        return (
          <div
            className="pc-mobile-shift"
            aria-label={accessibleLabel}
            title={accessibleLabel}
            style={{ background: employeeColor, color: textColorOn(employeeColor) }}
          >
            <span>{compactEmployeeLabel(employeeName)}</span>
            {p.is_trainee && <span className="pc-mobile-shift-t">T</span>}
          </div>
        )
      }
      return (
        <div className="pc-shift" title={tip}>
          {p.is_trainee && <span className="pc-shift-t">T</span>}
          {halfTag && <span className="pc-shift-half">{halfTag}</span>}
          <span className="pc-shift-name">{p.emp_name}</span>
          {p.position && <span className="pc-shift-pos">{p.position}</span>}
          <span className="pc-shift-time">{range}</span>
        </div>
      )
    }
    return (
      <div className="pc-shift pc-shift-grid" title={tip}>
        <div className="pc-shift-time">
          {p.start_time}–{p.end_time}
          {halfTag && <span className="pc-shift-half">{halfTag}</span>}
          {p.is_trainee && <span className="pc-shift-t">T</span>}
        </div>
        <div className="pc-shift-name">{p.emp_name}</div>
        {p.position && <div className="pc-shift-pos">{p.position}</div>}
      </div>
    )
  }

  const isTimeGrid = viewType !== 'dayGridMonth'

  return (
    <div className="space-y-3">

      {/* Row 1: navigation ←→ + Today + Month|Week|Day toggle */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center justify-between gap-0.5 sm:justify-start">
          <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => eachCal((a) => a.prev())}>
            <ChevronLeft className="size-4" />
          </Button>
          <span className="text-sm font-semibold min-w-24 text-center px-1">{viewTitle}</span>
          <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => eachCal((a) => a.next())}>
            <ChevronRight className="size-4" />
          </Button>
        </div>
        <div className="grid grid-cols-[auto_1fr] items-center gap-2 sm:flex">
          <Button variant="outline" size="sm" className="h-8" onClick={() => eachCal((a) => a.today())}>Today</Button>
          <div className="flex min-w-0 rounded-lg border border-border overflow-hidden text-xs font-medium">
            {VIEW_OPTIONS.map((v, i) => (
              <button
                key={v.value}
                aria-pressed={viewType === v.value}
                className={cn(
                  'flex-1 px-2 h-8 transition-colors sm:flex-none sm:px-3',
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

      {/* Row 2: employee filter (desktop) + refresh + staffing info */}
      <div className="flex items-center gap-2">
        {!isMobile && (
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
                  {(e.name || e.email || e.auth0_id) + (e.is_trainee ? ' (Trainee)' : '')}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          title="Refresh"
          onClick={() => currentRange && loadEvents(currentRange.start, currentRange.end, viewType)}
        >
          <RefreshCw className="size-3.5" />
        </Button>
        {isMobile ? (
          <Popover
            trigger="click"
            placement="bottomRight"
            content={
              <div className="text-xs space-y-1.5" style={{ maxWidth: 270 }}>
                <p className="m-0">
                  <span
                    className="inline-block size-2.5 rounded-sm align-middle mr-1.5"
                    style={{ background: UNCOVERED_COLOR, opacity: 0.35 }}
                  />
                  Red = fewer staff scheduled than required during opening hours.
                </p>
                {realStores.map(s => {
                  const r = staffReqs[s.code]
                  const h = hoursForStore(s.code, storeHours)
                  return (
                    <p className="m-0" key={s.code}>
                      <span className="font-medium">{s.name || s.code}:</span>{' '}
                      Mon–Fri {h.weekday.open}–{h.weekday.close},
                      Sat–Sun {h.weekend.open}–{h.weekend.close} ·
                      needs {r?.weekday ?? 1}/{r?.weekend ?? 1} staff (wk/wknd)
                    </p>
                  )
                })}
                <p className="m-0 text-muted-foreground">Adjustable in Settings → Scheduling.</p>
              </div>
            }
          >
            <Button variant="ghost" size="icon" className="h-8 w-8 ml-auto" title="Staffing rules">
              <Info className="size-4" />
            </Button>
          </Popover>
        ) : (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground ml-auto">
            <span
              className="size-3 rounded-sm shrink-0"
              style={{ background: UNCOVERED_COLOR, opacity: 0.35 }}
            />
            Understaffed — needs {realStores.map(s => {
              const r = staffReqs[s.code]
              return `${s.code} ${r?.weekday ?? 1}/${r?.weekend ?? 1}`
            }).join(' · ')} (weekday/weekend)
          </span>
        )}
      </div>

      {/* Employee filter — avatar strip on mobile, pill chips on desktop.
          Both: click to filter the calendars + show that person's hours. */}
      {isMobile && employees.length > 0 && (
        <div
          className="flex gap-2.5 overflow-x-auto -mx-2 px-2 pb-1"
          style={{ scrollbarWidth: 'none' }}
        >
          <button
            type="button"
            onClick={() => setFilterEmpId(null)}
            className="flex flex-col items-center gap-1 shrink-0 w-12"
          >
            <span
              className={cn(
                'flex items-center justify-center rounded-full size-10 text-[10px] font-semibold border-2 border-dashed',
                filterEmpId === null
                  ? 'border-primary text-primary'
                  : 'border-muted-foreground/40 text-muted-foreground',
              )}
            >
              ALL
            </span>
            <span
              className={cn(
                'text-[10px] leading-tight',
                filterEmpId === null ? 'text-foreground font-semibold' : 'text-muted-foreground',
              )}
            >
              All
            </span>
          </button>
          {employees.map((e) => {
            const empColor = empColors[e.id] ?? FALLBACK_COLORS[0]
            const name     = e.name || e.email || `E${e.id}`
            const active   = filterEmpId === e.id
            const dimmed   = filterEmpId !== null && !active
            return (
              <button
                key={e.id}
                type="button"
                onClick={() => setFilterEmpId(active ? null : e.id)}
                className={cn(
                  'flex flex-col items-center gap-1 shrink-0 w-12 transition-opacity',
                  dimmed && 'opacity-40',
                )}
              >
                <span
                  className={cn(
                    'relative flex items-center justify-center rounded-full size-10 text-sm font-semibold',
                    active && 'ring-2 ring-primary ring-offset-2 ring-offset-background',
                  )}
                  style={{
                    background: empColor,
                    color: textColorOn(empColor),
                    ...(e.is_trainee ? { outline: '2px dashed #F59E0B', outlineOffset: 1 } : {}),
                  }}
                >
                  {name.trim().slice(0, 2).toUpperCase()}
                  <span className="absolute -bottom-0.5 -right-1 flex gap-0.5">
                    {(empStores[e.id] ?? []).map(code => (
                      <span
                        key={code}
                        className="size-2 rounded-full border border-background"
                        style={{ background: storeColorRef.current[code] ?? '#9ca3af' }}
                      />
                    ))}
                  </span>
                </span>
                <span
                  className={cn(
                    'text-[10px] leading-tight max-w-12 truncate',
                    active ? 'text-foreground font-semibold' : 'text-muted-foreground',
                  )}
                >
                  {name}
                </span>
              </button>
            )
          })}
        </div>
      )}
      {!isMobile && employees.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {employees.map((e) => {
            const empColor = empColors[e.id] ?? FALLBACK_COLORS[0]
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
                {!!e.is_trainee && (
                  <span
                    style={{
                      background: '#FEF3C7', color: '#92400E', border: '1px dashed #F59E0B',
                      fontSize: 9, borderRadius: 3, padding: '0px 4px', lineHeight: 1.5, fontWeight: 700,
                    }}
                  >T</span>
                )}
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

      {/* Coverage checklist + period notes: proves everyone was assigned or
          at least considered for the visible month/week/day */}
      {periodKey && employees.length > 0 && (
        <CoveragePanel
          periodKey={periodKey}
          periodLabel={viewTitle}
          employees={employees}
        />
      )}

      {/* Legend: shift styles */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground px-1">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-6 h-3 rounded-sm" style={{ background: '#6366f1' }} />
          Full day
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-flex w-6 h-3 items-center justify-center rounded-sm text-[7px] font-bold text-white"
            style={{ background: '#6366f1' }}
          >AM</span>
          Half day / custom slot
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-6 h-3 rounded-sm"
            style={{ background: '#6366f1', outline: '1.5px dashed #F59E0B', outlineOffset: '-1.5px' }}
          />
          Trainee (keeps employee color)
        </span>
      </div>

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
            className={cn(
              'rounded-xl border overflow-hidden',
              isMobile && viewType === 'dayGridMonth' && 'pc-mobile-month',
            )}
            style={{ borderColor: (st.color || '#6366f1') + '66' }}
          >
            <FullCalendar
              ref={(el) => { calRefs.current[st.code] = el }}
              plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin]}
              initialView={viewType}
              headerToolbar={false}
              height="auto"
              timeZone="local"
              firstDay={1}
              events={visibleEvents(eventsByStore[st.code] ?? [])}
              datesSet={i === 0 ? handleDatesSet : undefined}
              dateClick={handleDateClickFor(st.code)}
              eventClick={handleEventClick}
              eventContent={renderEvent}
              eventTimeFormat={{ hour: '2-digit', minute: '2-digit', hour12: false }}
              businessHours={businessHoursFrom(hoursForStore(st.code, storeHours))}
              dayMaxEvents={mobileMonthEventLimit(isMobile, viewType)}
              moreLinkContent={(arg) => isMobile ? `+${arg.num}` : `+${arg.num} more`}
              allDaySlot={false}
              nowIndicator
              slotMinTime={gridWindow(hoursForStore(st.code, storeHours)).slotMinTime}
              slotMaxTime={gridWindow(hoursForStore(st.code, storeHours)).slotMaxTime}
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
        storeHours={storeHours}
        shiftPresets={shiftPresets}
        positionsMap={positionsMap}
        onClose={() => setModalOpen(false)}
        onSaved={handleSaved}
      />
    </div>
  )
}
