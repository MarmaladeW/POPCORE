import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Skeleton, Space } from 'antd'
import { useAuth0 } from '@auth0/auth0-react'
import { useHasRole } from '../../auth/useRole'
import { torontoDate } from '../Dashboard/todayPresentation'
import { getAttendanceToday, punchInToday, type AttendanceToday } from './scheduleApi'

export default function PunchIn({ home = false }: { home?: boolean }) {
  const staff = useHasRole('staff')
  const { user } = useAuth0()
  return staff ? <AttendancePrompt key={user?.sub} home={home} /> : null
}

function AttendancePrompt({ home }: { home: boolean }) {
  const [snapshot, setSnapshot] = useState<{ localDay: string; data: AttendanceToday } | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const request = useRef(0)
  const posting = useRef(false)
  const observedDay = useRef(torontoDate())

  const refresh = useCallback(async function load(): Promise<void> {
    if (posting.current) return
    const current = ++request.current
    const localDay = torontoDate()
    setLoading(true)
    setError('')
    try {
      const data = await getAttendanceToday()
      if (current !== request.current) return
      if (localDay !== torontoDate()) { setSnapshot(null); return load() }
      setSnapshot({ localDay, data })
    } catch {
      if (current === request.current) setError('Unable to check your attendance. Please retry.')
    } finally {
      if (current === request.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
    const onFocus = () => {
      const today = torontoDate()
      if (today !== observedDay.current) {
        observedDay.current = today
        setSnapshot(null)
      }
      void refresh()
    }
    const onVisible = () => { if (document.visibilityState === 'visible') onFocus() }
    const timer = window.setInterval(() => {
      if (torontoDate() !== observedDay.current) onFocus()
    }, 30_000)
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      request.current++
      window.clearInterval(timer)
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [refresh])

  const status = snapshot?.localDay === torontoDate() ? snapshot.data : null
  const recorded = status?.attendance?.business_date === status?.business_date && status?.attendance
  const shift = status?.shift?.date === status?.business_date ? status?.shift : null

  async function punch() {
    if (posting.current || loading || !shift || recorded) return
    const current = ++request.current
    const localDay = torontoDate()
    // A midnight click must refresh the assigned shift before posting.
    if (snapshot?.localDay !== localDay) { void refresh(); return }
    posting.current = true
    setSaving(true)
    setError('')
    try {
      const data = await punchInToday(shift.id)
      if (current !== request.current || localDay !== torontoDate()) return
      setSnapshot({ localDay, data })
      if (!data.attendance || data.attendance.business_date !== data.business_date) {
        setError('Punch-in was not confirmed. Please check your attendance and retry.')
      }
    } catch {
      if (current === request.current) setError('Punch-in could not be confirmed. Please retry; a retry will not create a second punch.')
    } finally {
      posting.current = false
      if (current === request.current) {
        setSaving(false)
        if (localDay !== torontoDate()) void refresh()
      }
    }
  }

  if (home && (recorded || (!error && (!status || !shift)))) return null

  return <section aria-label="Today's attendance" style={{ marginBottom: 16 }}>
    <Card size="small" title="Punch in / 上班打卡">
      <Space direction="vertical" style={{ width: '100%' }}>
        {error && <Alert type="error" showIcon role="alert" message={error}
          action={<Button style={{ minHeight: 44 }} disabled={loading || saving} onClick={() => void refresh()}>Retry attendance</Button>} />}
        {!status ? (loading && <Skeleton active paragraph={{ rows: 1 }} />) : recorded ? (
          <div role="status">Punched in at <time dateTime={recorded.punched_in_at}>{new Intl.DateTimeFormat('en-CA', {
            timeZone: 'America/Toronto', hour: '2-digit', minute: '2-digit', hour12: false,
          }).format(new Date(recorded.punched_in_at))}</time> (Toronto) · {status.business_date}</div>
        ) : shift ? <>
          <div>{status.business_date} · {shift.store_name || shift.store_code} · {shift.start_time}–{shift.end_time}</div>
          <Button type="primary" aria-label="Punch in" loading={saving} disabled={loading || saving} onClick={punch}
            style={{ minHeight: 44, minWidth: 120 }}>Punch in</Button>
        </> : <div role="status">No assigned shift today · {status.business_date}</div>}
      </Space>
    </Card>
  </section>
}
