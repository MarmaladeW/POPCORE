import { useState, useCallback, useEffect, useRef } from 'react'
import FullCalendar from '@fullcalendar/react'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import type { DatesSetArg, EventInput } from '@fullcalendar/core'
import dayjs from 'dayjs'
import { CalendarPlus, Copy, RotateCw } from 'lucide-react'
import { message } from 'antd'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog'
import {
  getMyShifts, getCalendarFeed, getScheduleConfig, resetCalendarFeed,
  type Shift,
} from './scheduleApi'
import {
  DEFAULT_STORE_HOURS, businessHoursFrom, gridWindow, parseStoreOpenHours, unionHours,
  type OpenHoursConfig,
} from './openHours'
import { useIsMobile } from '../../hooks/useIsMobile'

export default function EmployeeView() {
  const calRef = useRef<FullCalendar>(null)
  const [events, setEvents] = useState<EventInput[]>([])
  const [currentRange, setCurrentRange] = useState<{ start: string; end: string } | null>(null)
  const [syncOpen, setSyncOpen] = useState(false)
  const [feedUrl, setFeedUrl] = useState<string | null>(null)
  // This calendar mixes stores, so shade with the widest hours across stores
  const [openHours, setOpenHours] = useState<OpenHoursConfig>(unionHours(DEFAULT_STORE_HOURS))
  const [viewType, setViewType] = useState('dayGridMonth')
  const [loadError, setLoadError] = useState(false)
  const isMobile = useIsMobile()
  const [msgApi, msgCtx] = message.useMessage()

  useEffect(() => {
    getScheduleConfig()
      .then(cfg => setOpenHours(unionHours(parseStoreOpenHours(cfg.schedule_open_hours))))
      .catch(() => {})
  }, [])


  const loadEvents = useCallback(async (start: string, end: string) => {
    setLoadError(false)
    const shifts: Shift[] = await getMyShifts({ start, end, store_code: 'ALL' })
    const evts: EventInput[] = []

    for (const s of shifts) {
      evts.push({
        id: `shift-${s.id}`,
        title: `${s.store_code || ''} ${s.start_time}–${s.end_time}${s.position ? ` · ${s.position}` : ''}`,
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
      loadEvents(start, end).catch(() => { setEvents([]); setLoadError(true) })
    },
    [loadEvents]
  )

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
      {loadError && <div role="alert">Could not load your shifts. <Button variant="outline" onClick={() => currentRange && loadEvents(currentRange.start, currentRange.end).catch(() => setLoadError(true))}>Retry</Button></div>}
      {/* Legend + calendar sync */}
      <div className="flex items-center gap-4 text-xs text-muted-foreground">
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
          firstDay={1}
          events={events}
          datesSet={handleDatesSet}
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
