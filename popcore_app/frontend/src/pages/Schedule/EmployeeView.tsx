import { useState, useCallback, useRef } from 'react'
import FullCalendar from '@fullcalendar/react'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import type { DateClickArg } from '@fullcalendar/interaction'
import type { DatesSetArg, EventClickArg, EventInput } from '@fullcalendar/core'
import { Typography, Tooltip } from 'antd'
import dayjs from 'dayjs'
import { getMyAvailability, getShifts, type Availability, type Shift } from './scheduleApi'
import AvailabilityModal from './AvailabilityModal'

const { Title } = Typography

export default function EmployeeView() {
  const calRef = useRef<FullCalendar>(null)
  const [events, setEvents] = useState<EventInput[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [selectedAvail, setSelectedAvail] = useState<Availability | null>(null)

  // Cache availability by date for quick lookup on click
  const availByDate = useRef<Record<string, Availability>>({})

  const loadEvents = useCallback(async (start: string, end: string) => {
    const [avails, shifts]: [Availability[], Shift[]] = await Promise.all([
      getMyAvailability(start, end),
      getShifts({ start, end }),
    ])

    availByDate.current = {}
    const evts: EventInput[] = []

    for (const a of avails) {
      availByDate.current[a.date] = a
      evts.push({
        id: `avail-${a.id}`,
        title: `Available ${a.start_time}–${a.end_time}`,
        start: `${a.date}T${a.start_time}`,
        end: `${a.date}T${a.end_time}`,
        backgroundColor: '#10B981',
        borderColor: '#059669',
        textColor: '#fff',
        extendedProps: { type: 'availability', avail: a },
      })
    }

    for (const s of shifts) {
      evts.push({
        id: `shift-${s.id}`,
        title: `Shift ${s.start_time}–${s.end_time}`,
        start: `${s.date}T${s.start_time}`,
        end: `${s.date}T${s.end_time}`,
        backgroundColor: '#6366F1',
        borderColor: '#4F46E5',
        textColor: '#fff',
        extendedProps: { type: 'shift', shift: s },
      })
    }

    setEvents(evts)
  }, [])

  const handleDatesSet = useCallback(
    (arg: DatesSetArg) => {
      const start = dayjs(arg.start).format('YYYY-MM-DD')
      const end   = dayjs(arg.end).format('YYYY-MM-DD')
      loadEvents(start, end)
    },
    [loadEvents]
  )

  const handleDateClick = useCallback((arg: DateClickArg) => {
    setSelectedDate(arg.dateStr)
    setSelectedAvail(availByDate.current[arg.dateStr] ?? null)
    setModalOpen(true)
  }, [])

  const handleEventClick = useCallback((arg: EventClickArg) => {
    const { type, avail } = arg.event.extendedProps as {
      type: string
      avail?: Availability
    }
    if (type === 'availability' && avail) {
      setSelectedDate(avail.date)
      setSelectedAvail(avail)
      setModalOpen(true)
    }
  }, [])

  const handleSaved = useCallback(() => {
    const api = calRef.current?.getApi()
    if (api) {
      const view = api.view
      const start = dayjs(view.activeStart).format('YYYY-MM-DD')
      const end   = dayjs(view.activeEnd).format('YYYY-MM-DD')
      loadEvents(start, end)
    }
  }, [loadEvents])

  return (
    <div style={{ padding: '0 4px' }}>
      <Title level={4} style={{ marginBottom: 12 }}>My Schedule</Title>

      <div style={{ marginBottom: 8, display: 'flex', gap: 16, fontSize: 13 }}>
        <Tooltip title="Days you marked as available">
          <span>
            <span
              style={{
                display: 'inline-block',
                width: 12,
                height: 12,
                background: '#10B981',
                borderRadius: 2,
                marginRight: 4,
              }}
            />
            Availability
          </span>
        </Tooltip>
        <Tooltip title="Shifts assigned by a manager">
          <span>
            <span
              style={{
                display: 'inline-block',
                width: 12,
                height: 12,
                background: '#6366F1',
                borderRadius: 2,
                marginRight: 4,
              }}
            />
            Assigned shift
          </span>
        </Tooltip>
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
        events={events}
        datesSet={handleDatesSet}
        dateClick={handleDateClick}
        eventClick={handleEventClick}
        eventTimeFormat={{ hour: '2-digit', minute: '2-digit', hour12: false }}
      />

      <AvailabilityModal
        open={modalOpen}
        date={selectedDate}
        existing={selectedAvail}
        onClose={() => setModalOpen(false)}
        onSaved={handleSaved}
      />
    </div>
  )
}
