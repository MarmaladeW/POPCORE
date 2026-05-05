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
  type Availability,
  type Employee,
  type Shift,
} from './scheduleApi'
import ShiftModal from './ShiftModal'

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
  const [employees, setEmployees] = useState<Employee[]>([])
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
      const [avails, shifts]: [Availability[], Shift[]] = await Promise.all([
        getAllAvailability(start, end),
        getShifts({ start, end }),
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
    <div>
      {/* Row A: employee filter + refresh */}
      <div className="flex items-center gap-2 flex-wrap mb-2">
        <span style={{ fontSize: 13, color: '#6b7280' }}>Filter:</span>
        <Select
          value={filterEmpId !== null ? String(filterEmpId) : '__all__'}
          onValueChange={(v) => setFilterEmpId(v === '__all__' ? null : Number(v))}
        >
          <SelectTrigger style={{ width: 180 }}>
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
          variant="outline"
          size="icon"
          title="Refresh"
          onClick={() => currentRange && loadEvents(currentRange.start, currentRange.end)}
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>

      {/* Row B: calendar navigation */}
      <div className="flex items-center gap-1 mb-2">
        <Button variant="ghost" size="icon" onClick={() => cal()?.prev()}>
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <span className="flex-1 text-center text-sm font-medium">{viewTitle}</span>
        <Button variant="ghost" size="icon" onClick={() => cal()?.next()}>
          <ChevronRight className="h-4 w-4" />
        </Button>
        <Button variant="outline" size="sm" onClick={() => cal()?.today()}>Today</Button>
        <div className="flex rounded-md border overflow-hidden text-sm">
          <button
            className={cn(
              'px-3 py-1 transition-colors',
              viewType === 'dayGridMonth'
                ? 'bg-primary text-primary-foreground'
                : 'bg-background text-foreground hover:bg-muted',
            )}
            onClick={() => cal()?.changeView('dayGridMonth')}
          >
            Month
          </button>
          <button
            className={cn(
              'px-3 py-1 border-l transition-colors',
              viewType === 'timeGridWeek'
                ? 'bg-primary text-primary-foreground'
                : 'bg-background text-foreground hover:bg-muted',
            )}
            onClick={() => cal()?.changeView('timeGridWeek')}
          >
            Week
          </button>
        </div>
      </div>

      {/* Employee legend: 2-column grid */}
      {employees.length > 0 && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 mb-3">
          {employees.map((e, i) => (
            <span key={e.id} className="flex items-center gap-2 text-xs text-muted-foreground">
              <span
                className="w-2 h-2 rounded-sm flex-shrink-0"
                style={{ background: colorForEmployee(i) }}
              />
              {e.name || e.email || `Employee ${e.id}`}
            </span>
          ))}
        </div>
      )}

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
