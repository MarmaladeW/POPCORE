import { useState, useCallback, useRef, useEffect } from 'react'
import FullCalendar from '@fullcalendar/react'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import type { DateClickArg } from '@fullcalendar/interaction'
import type { DatesSetArg, EventClickArg, EventInput } from '@fullcalendar/core'
import { Select, Typography, Tooltip } from 'antd'
import dayjs from 'dayjs'
import {
  getAllAvailability,
  getShifts,
  getEmployees,
  type Availability,
  type Employee,
  type Shift,
} from './scheduleApi'
import ShiftModal from './ShiftModal'

const { Text } = Typography

// A small palette of distinct colours for up to 15 employees
const EMPLOYEE_COLORS = [
  '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#14B8A6',
  '#F97316', '#06B6D4', '#84CC16', '#A855F7', '#F43F5E',
  '#0EA5E9', '#22C55E', '#FB923C', '#E879F9', '#64748B',
]

function colorForEmployee(idx: number) {
  return EMPLOYEE_COLORS[idx % EMPLOYEE_COLORS.length]
}

export default function ManagerCalendar() {
  const calRef = useRef<FullCalendar>(null)
  const [employees, setEmployees] = useState<Employee[]>([])
  const [filterEmpId, setFilterEmpId] = useState<number | null>(null)
  const [allEvents, setAllEvents] = useState<EventInput[]>([])
  const [visibleEvents, setVisibleEvents] = useState<EventInput[]>([])

  const [modalOpen, setModalOpen] = useState(false)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [selectedShift, setSelectedShift] = useState<Shift | null>(null)

  const shiftById = useRef<Record<number, Shift>>({})
  const [currentRange, setCurrentRange] = useState<{ start: string; end: string } | null>(null)

  useEffect(() => {
    getEmployees().then(setEmployees).catch(() => {})
  }, [])

  // Re-load events once employees arrive (fixes race with initial datesSet)
  useEffect(() => {
    if (employees.length > 0 && currentRange) {
      loadEvents(currentRange.start, currentRange.end)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employees])

  // Rebuild visible events whenever filter or allEvents changes
  useEffect(() => {
    if (filterEmpId === null) {
      setVisibleEvents(allEvents)
    } else {
      setVisibleEvents(
        allEvents.filter(
          (e) => e.extendedProps?.employee_id === filterEmpId
        )
      )
    }
  }, [allEvents, filterEmpId])

  const loadEvents = useCallback(
    async (start: string, end: string) => {
      const [avails, shifts]: [Availability[], Shift[]] = await Promise.all([
        getAllAvailability(start, end),
        getShifts({ start, end }),
      ])

      // Build a stable colour map keyed by employee_id
      const empIdToIdx: Record<number, number> = {}
      employees.forEach((e, i) => { empIdToIdx[e.id] = i })

      shiftById.current = {}
      const evts: EventInput[] = []

      for (const a of avails) {
        const idx   = empIdToIdx[a.employee_id] ?? 0
        const color = colorForEmployee(idx)
        evts.push({
          id: `avail-${a.id}`,
          title: `${a.employee_name ?? 'Employee'} available`,
          start: `${a.date}T${a.start_time}`,
          end: `${a.date}T${a.end_time}`,
          backgroundColor: color + '33',  // 20% opacity
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
      loadEvents(start, end)
    },
    [loadEvents]
  )

  const handleDateClick = useCallback((arg: DateClickArg) => {
    setSelectedDate(arg.dateStr)
    setSelectedShift(null)
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

  return (
    <div>
      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <Text>Filter by employee:</Text>
        <Select
          allowClear
          placeholder="All employees"
          style={{ width: 200 }}
          value={filterEmpId}
          onChange={(v) => setFilterEmpId(v ?? null)}
          options={employees.map((e) => ({
            value: e.id,
            label: e.name || e.email || e.auth0_id,
          }))}
        />
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          {employees.map((e, i) => (
            <Tooltip key={e.id} title={e.name || e.email}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
                <span
                  style={{
                    display: 'inline-block',
                    width: 10,
                    height: 10,
                    borderRadius: 2,
                    background: colorForEmployee(i),
                  }}
                />
                {e.name || e.email || `Employee ${e.id}`}
              </span>
            </Tooltip>
          ))}
        </div>
      </div>

      <FullCalendar
        ref={calRef}
        plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin]}
        initialView="dayGridMonth"
        headerToolbar={{
          left:   'prev,next today',
          center: 'title',
          right:  'dayGridMonth,timeGridWeek',
        }}
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
        onClose={() => setModalOpen(false)}
        onSaved={handleSaved}
      />
    </div>
  )
}
