import { useState, useCallback, useEffect, useRef } from 'react'
import FullCalendar from '@fullcalendar/react'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import type { DateClickArg } from '@fullcalendar/interaction'
import type { DatesSetArg, EventClickArg, EventInput } from '@fullcalendar/core'
import dayjs from 'dayjs'
import { CalendarPlus, Copy, RotateCw } from 'lucide-react'
import { message } from 'antd'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog'
import {
  getMyAvailability, getMyShifts, getCalendarFeed, getScheduleConfig, resetCalendarFeed,
  type Availability, type Shift,
} from './scheduleApi'
import AvailabilityModal from './AvailabilityModal'
import {
  DEFAULT_OPEN_HOURS, businessHoursFrom, gridWindow, parseOpenHours,
  type OpenHoursConfig,
} from './openHours'
import { useAppStore } from '../../store'
import { useIsMobile } from '../../hooks/useIsMobile'

export default function EmployeeView() {
  const { selectedStore } = useAppStore()
  const calRef = useRef<FullCalendar>(null)
  const [events, setEvents] = useState<EventInput[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [selectedAvail, setSelectedAvail] = useState<Availability | null>(null)
  const [currentRange, setCurrentRange] = useState<{ start: string; end: string } | null>(null)
  const [syncOpen, setSyncOpen] = useState(false)
  const [feedUrl, setFeedUrl] = useState<string | null>(null)
  const [openHours, setOpenHours] = useState<OpenHoursConfig>(DEFAULT_OPEN_HOURS)
  const [viewType, setViewType] = useState('dayGridMonth')
  const isMobile = useIsMobile()
  const [msgApi, msgCtx] = message.useMessage()

  useEffect(() => {
    getScheduleConfig()
      .then(cfg => setOpenHours(parseOpenHours(cfg.schedule_open_hours)))
      .catch(() => {})
  }, [])

  const availByDate = useRef<Record<string, Availability>>({})

  const loadEvents = useCallback(async (start: string, end: string) => {
    const sc = selectedStore?.code
    const [avails, shifts]: [Availability[], Shift[]] = await Promise.all([
      getMyAvailability(start, end, sc),
      getMyShifts({ start, end, store_code: sc }),
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
      setViewType(arg.view.type)
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

  const openSyncDialog = async () => {
    setSyncOpen(true)
    try {
      const feed = await getCalendarFeed()
      setFeedUrl(`${window.location.origin}${feed.path}`)
    } catch {
      msgApi.error('Could not load your calendar link')
    }
  }

  const handleResetFeed = async () => {
    try {
      const feed = await resetCalendarFeed()
      setFeedUrl(`${window.location.origin}${feed.path}`)
      msgApi.success('New link generated — the old one no longer works')
    } catch {
      msgApi.error('Could not reset the link')
    }
  }

  const copyFeedUrl = async () => {
    if (!feedUrl) return
    try {
      await navigator.clipboard.writeText(feedUrl)
      msgApi.success('Link copied')
    } catch {
      msgApi.error('Copy failed — select and copy the link manually')
    }
  }

  const webcalUrl = feedUrl ? feedUrl.replace(/^https?:\/\//, 'webcal://') : null

  return (
    <div className="space-y-3">
      {msgCtx}
      {/* Legend + calendar sync */}
      <div className="flex items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5" title="Days you marked as available">
          <span className="size-3 rounded-sm shrink-0 bg-emerald-500" />
          Availability
        </span>
        <span className="flex items-center gap-1.5" title="Shifts assigned by a manager">
          <span className="size-3 rounded-sm shrink-0 bg-primary" />
          Assigned shift
        </span>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto h-7 text-xs"
          onClick={openSyncDialog}
        >
          <CalendarPlus className="size-3.5 mr-1" />
          Sync to my calendar
        </Button>
      </div>

      {/* Calendar card */}
      <div
        className={cn(
          'rounded-xl border border-border overflow-hidden',
          isMobile && viewType === 'dayGridMonth' && 'popcore-dots',
        )}
      >
        <FullCalendar
          ref={calRef}
          plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin]}
          initialView="dayGridMonth"
          headerToolbar={{
            left:   'prev,next today',
            center: 'title',
            right:  'dayGridMonth,timeGridWeek,timeGridDay',
          }}
          height="auto"
          timeZone="local"
          events={events}
          datesSet={handleDatesSet}
          dateClick={handleDateClick}
          eventClick={handleEventClick}
          eventTimeFormat={{ hour: '2-digit', minute: '2-digit', hour12: false }}
          businessHours={businessHoursFrom(openHours)}
          allDaySlot={false}
          nowIndicator
          slotMinTime={gridWindow(openHours).slotMinTime}
          slotMaxTime={gridWindow(openHours).slotMaxTime}
          slotDuration="01:00"
          slotLabelInterval="01:00"
        />
      </div>

      <AvailabilityModal
        open={modalOpen}
        date={selectedDate}
        existing={selectedAvail}
        onClose={() => setModalOpen(false)}
        onSaved={handleSaved}
      />

      {/* Calendar sync dialog */}
      <Dialog open={syncOpen} onOpenChange={(o) => !o && setSyncOpen(false)}>
        <DialogContent style={{ maxWidth: 520 }}>
          <DialogHeader>
            <DialogTitle>Sync shifts to your calendar</DialogTitle>
          </DialogHeader>

          <div className="space-y-3 text-sm">
            <p className="text-muted-foreground">
              Subscribe to this personal link in your calendar app. It's a live feed:
              when your schedule changes here, your calendar updates itself on its next
              refresh — no need to sync again. (Apple/Outlook refresh every few hours;
              Google Calendar can take up to a day.)
            </p>

            <div className="flex items-center gap-2">
              <input
                readOnly
                value={feedUrl ?? 'Loading…'}
                onFocus={(e) => e.target.select()}
                className="flex-1 rounded-md border border-border bg-muted/40 px-2 py-1.5 text-xs font-mono"
              />
              <Button variant="outline" size="icon" className="h-8 w-8 shrink-0" title="Copy link" onClick={copyFeedUrl}>
                <Copy className="size-3.5" />
              </Button>
            </div>

            <div className="flex flex-wrap gap-2">
              {webcalUrl && (
                <>
                  <Button asChild variant="outline" size="sm">
                    <a
                      href={`https://calendar.google.com/calendar/r?cid=${encodeURIComponent(webcalUrl)}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Add to Google Calendar
                    </a>
                  </Button>
                  <Button asChild variant="outline" size="sm">
                    <a href={webcalUrl}>Apple / Outlook</a>
                  </Button>
                </>
              )}
            </div>

            <p className="text-xs text-muted-foreground">
              Anyone with this link can see your shifts. If you shared it by mistake,{' '}
              <button
                type="button"
                onClick={handleResetFeed}
                className="underline inline-flex items-center gap-0.5 hover:text-foreground"
              >
                <RotateCw className="size-3" /> generate a new link
              </button>
              {' '}(you'll need to re-subscribe).
            </p>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setSyncOpen(false)}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
