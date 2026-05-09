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
  type Availability,
  type Employee,
  type Shift,
} from './scheduleApi'
import ShiftModal from './ShiftModal'
import { useAppStore } from '../../store'

const STORE_CHIP_COLOR: Record<string, string> = {
  DT: '#3b82f6',
  MK: '#22c55e',
  MT: '#f97316',
}

const EMPLOYEE_COLORS = [
  '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#14B8A6',
  '#F97316', '#06B6D4', '#84CC16', '#A855F7', '#F43F5E',
  '#0EA5E9', '#22C55E', '#FB923C', '#E879F9', '#64748B',
]

function colorForEmployee(idx: number) {
  return EMPLOYEE_COLORS[idx % EMPLOYEE_COLORS.length]
}

type ViewType = 'dayGridMonth' | 'timeGridWeek'

export default function ManagerCalendar() {
  const calRef = useRef<FullCalendar>(null)
  const { selectedStore } = useAppStore()
  const [employees, setEmployees] = useState<Employee[]>([])
  const [empStores, setEmpStores] = useState<Record<number, string[]>>({})
  const [filterEmpId, setFilterEmpId] = useState<number | null>(null)
  const [allEvents, setAllEvents] = useState<EventInput[]>([])
  const [visibleEvents, setVisibleEvents] = useState<EventInput[]>([])
  const [viewTitle, setViewTitle] = useState('')
  const [viewType, setViewType] = useState<ViewType>('dayGridMonth')

  const [modalOpen, setModalOpen] = useState(false)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [selectedShift, setSelectedShift] = useState<Shift | null>(null)
  const [availForDate, setAvailForDate] = useState<Availability[]>([])

  const shiftById = useRef<Record<number, Shift>>({})
  const availsByDate = useRef<Record<string, Availability[]>>({})
  const [currentRange, setCurrentRange] = useState<{ start: string; end: string } | null>(null)

  useEffect(() => {
    getEmployees().then(setEmployees).catch(() => {})
    getEmployeeStores()
      .then(data => {
        const m: Record<number, string[]> = {}
        data.forEach(e => { m[e.employee_id] = e.stores })
        setEmpStores(m)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (employees.length > 0 && currentRange) {
      loadEvents(currentRange.start, currentRange.end)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employees])

  useEffect(() => {
    if (filterEmpId === null) {
      setVisibleEvents(allEvents)
    } else {
      setVisibleEvents(
        allEvents.filter((e) => e.extendedProps?.employee_id === filterEmpId)
      )
    }
  }, [allEvents, filterEmpId])

  const loadEvents = useCallback(
    async (start: string, end: string) => {
      const sc = selectedStore?.code
      const [avails, shifts]: [Availability[], Shift[]] = await Promise.all([
        getAllAvailability(start, end, sc),
        getShifts({ start, end, store_code: sc }),
      ])

      const empIdToIdx: Record<number, number> = {}
      employees.forEach((e, i) => { empIdToIdx[e.id] = i })

      shiftById.current = {}
      availsByDate.current = {}
      const evts: EventInput[] = []

      for (const a of avails) {
        if (!availsByDate.current[a.date]) availsByDate.current[a.date] = []
        availsByDate.current[a.date].push(a)

        const idx   = empIdToIdx[a.employee_id] ?? 0
        const color = colorForEmployee(idx)
        evts.push({
          id: `avail-${a.id}`,
          title: `${a.employee_name ?? 'Employee'} available`,
          start: `${a.date}T${a.start_time}`,
          end: `${a.date}T${a.end_time}`,
          backgroundColor: color + '33',
          borderColor: color,
          textColor: '#374151',
          display: 'background',
          extendedProps: { type: 'availability', employee_id: a.employee_id },
        })
      }

      for (const s of shifts) {
        shiftById.current[s.id] = s
        const idx   = empIdToIdx[s.employee_id] ?? 0
        const color = colorForEmployee(idx)
        evts.push({
          id: `shift-${s.id}`,
          title: `${s.employee_name ?? 'Employee'} ${s.start_time}–${s.end_time}`,
          start: `${s.date}T${s.start_time}`,
          end: `${s.date}T${s.end_time}`,
          backgroundColor: color,
          borderColor: color,
          textColor: '#fff',
          extendedProps: { type: 'shift', shift_id: s.id, employee_id: s.employee_id },
        })
      }

      setAllEvents(evts)
    },
    [employees]
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
    const { type, shift_id } = arg.event.extendedProps as {
      type: string
      shift_id?: number
    }
    if (type === 'shift' && shift_id != null) {
      const shift = shiftById.current[shift_id]
      if (shift) {
        setSelectedDate(shift.date)
        setSelectedShift(shift)
        setAvailForDate(availsByDate.current[shift.date] ?? [])
        setModalOpen(true)
      }
    } else if (type === 'availability') {
      // Fallback: some FC versions fire eventClick for background events
      // even though dateClick should fire instead (after the CSS fix).
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

      {/* Employee legend: horizontal pill chips */}
      {employees.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {employees.map((e, i) => (
            <span
              key={e.id}
              className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground"
            >
              <span
                className="size-2 rounded-full shrink-0"
                style={{ background: colorForEmployee(i) }}
              />
              {e.name || e.email || `Employee ${e.id}`}
              {(empStores[e.id] ?? []).map(code => (
                <span
                  key={code}
                  style={{
                    background: STORE_CHIP_COLOR[code] ?? '#9ca3af',
                    color: '#fff',
                    fontSize: 9,
                    borderRadius: 3,
                    padding: '1px 4px',
                    lineHeight: 1.4,
                    fontWeight: 600,
                  }}
                >{code}</span>
              ))}
            </span>
          ))}
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
