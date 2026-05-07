import { useState, useCallback, useRef } from 'react'
import FullCalendar from '@fullcalendar/react'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import type { DateClickArg } from '@fullcalendar/interaction'
import type { DatesSetArg, EventClickArg, EventInput } from '@fullcalendar/core'
import dayjs from 'dayjs'
import { getMyAvailability, getMyShifts, type Availability, type Shift } from './scheduleApi'
import AvailabilityModal from './AvailabilityModal'

export default function EmployeeView() {
  const calRef = useRef<FullCalendar>(null)
  const [events, setEvents] = useState<EventInput[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [selectedAvail, setSelectedAvail] = useState<Availability | null>(null)
  const [currentRange, setCurrentRange] = useState<{ start: string; end: string } | null>(null)

  const availByDate = useRef<Record<string, Availability>>({})

  const loadEvents = useCallback(async (start: string, end: string) => {
    const [avails, shifts]: [Availability[], Shift[]] = await Promise.all([
      getMyAvailability(start, end),
      getMyShifts({ start, end }),
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
      setCurrentRange({ start, end })
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
    if (currentRange) {
      loadEvents(currentRange.start, currentRange.end)
    }
  }, [loadEvents, currentRange])

  return (
    <div className="space-y-3">
      {/* Legend */}
      <div className="flex items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5" title="Days you marked as available">
          <span className="size-3 rounded-sm shrink-0 bg-emerald-500" />
          Availability
        </span>
        <span className="flex items-center gap-1.5" title="Shifts assigned by a manager">
          <span className="size-3 rounded-sm shrink-0 bg-primary" />
          Assigned shift
        </span>
      </div>

      {/* Calendar card */}
      <div className="rounded-xl border border-border overflow-hidden">
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
      </div>

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
