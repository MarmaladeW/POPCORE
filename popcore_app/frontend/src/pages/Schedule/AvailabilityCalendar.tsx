import { useEffect, useRef, useState } from 'react'
import { useAuth0 } from '@auth0/auth0-react'
import { Alert, Button, Input, Select, Space, Spin } from 'antd'
import dayjs from 'dayjs'
import { useAppStore } from '../../store'
import { getAvailabilityPeriod, submitAvailabilityPeriod } from './scheduleApi'
import { scheduleApiErrorMessage } from './employeeScheduling'
import { cycleStart, cycleDates, emptyDays, repeatFirstWeek, finishSubmission, type AvailabilityDraft } from './availabilityPeriod'

export default function AvailabilityCalendar() {
  const { stores, selectedStore, setSelectedStore, availabilityDrafts: drafts, setAvailabilityDrafts: setDrafts } = useAppStore()
  const { user } = useAuth0()
  const [period, setPeriod] = useState(() => cycleStart(dayjs().format('YYYY-MM-DD')))
  const storeCode = selectedStore?.code !== 'ALL' ? selectedStore?.code : undefined
  const scope = `${user?.sub}:${storeCode}:${period}`
  const draft = drafts[scope]
  const [selected, setSelected] = useState<string[]>([])
  const [status, setStatus] = useState<'available' | 'unavailable'>('available')
  const [start, setStart] = useState('12:00')
  const [end, setEnd] = useState('22:00')
  const [notes, setNotes] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  const draftCache = useRef(drafts)
  draftCache.current = drafts
  const dates = cycleDates(period)
  const completed = draft?.days.filter(day => day.status !== 'unset').length ?? 0
  useEffect(() => {
    setSelected([])
    if (!storeCode || draftCache.current[scope]) return
    let current = true
    getAvailabilityPeriod(period, storeCode).then(result => {
      if (!current) return
      const saved = new Map(result.days.map(day => [day.date, day]))
      setDrafts(previous => ({ ...previous, [scope]: {
        days: emptyDays(period).map(day => {
          const value = saved.get(day.date)
          return value ? { ...day, ...value, status: value.status ?? 'available' } : day
        }),
        version: result.version, submittedAt: result.submitted_at, dirty: false,
      } }))
      setErrors(previous => ({ ...previous, [scope]: '' }))
    }).catch(error => {
      if (current) setErrors(previous => ({ ...previous, [scope]: scheduleApiErrorMessage(error, 'Could not load availability.') }))
    })
    return () => { current = false }
  }, [period, storeCode, scope, reload])

  const update = (days: AvailabilityDraft[]) => {
    if (!draft) return
    setDrafts(previous => ({ ...previous, [scope]: { ...draft, days, dirty: true } }))
    setErrors(previous => ({ ...previous, [scope]: '' }))
  }
  const apply = () => {
    if (!draft) return
    if (status === 'available' && (!/^\d{2}:\d{2}$/.test(start) || !/^\d{2}:\d{2}$/.test(end) || start >= end)) {
      setErrors(previous => ({ ...previous, [scope]: 'Choose valid hours. End time must be after start time.' }))
      return
    }
    update(draft.days.map(day => selected.includes(day.date) ? {
      ...day, status, start_time: status === 'available' ? start : '', end_time: status === 'available' ? end : '', notes,
    } : day))
    setSelected([])
  }
  const submit = async () => {
    if (!draft || !storeCode || completed !== 14 || busy) return
    setBusy(true)
    try {
      const saved = await submitAvailabilityPeriod({
        period_start: period, store_code: storeCode, version: draft.version,
        days: draft.days.map(day => ({ ...day, status: day.status as 'available' | 'unavailable' })),
      })
      setDrafts(previous => {
        const latest = finishSubmission(previous[scope], draft, saved)
        return latest ? { ...previous, [scope]: latest } : previous
      })
      setErrors(previous => ({ ...previous, [scope]: '' }))
      window.dispatchEvent(new Event('popcore:availability-submitted'))
    } catch (error) {
      setErrors(previous => ({ ...previous, [scope]: scheduleApiErrorMessage(error, 'Submission failed. Your edits are still here; retry to submit.') }))
    } finally { setBusy(false) }
  }

  return <section aria-label="My biweekly availability">
    <div className="pc-page-actions">
      <Space wrap>
        <Button aria-label="Previous availability period" disabled={busy} onClick={() => setPeriod(dayjs(period).subtract(14, 'day').format('YYYY-MM-DD'))}>‹</Button>
        <strong>{dayjs(period).format('MMM D')} – {dayjs(dates[13]).format('MMM D, YYYY')}</strong>
        <Button aria-label="Next availability period" disabled={busy} onClick={() => setPeriod(dayjs(period).add(14, 'day').format('YYYY-MM-DD'))}>›</Button>
        <Button disabled={busy} onClick={() => setPeriod(cycleStart(dayjs().format('YYYY-MM-DD')))}>Current period</Button>
      </Space>
      <label>Availability store <Select aria-label="Availability store" placeholder="Choose a store" value={storeCode} disabled={busy}
        style={{ minWidth: 160 }} options={stores.filter(store => store.code !== 'ALL').map(store => ({ value: store.code, label: store.name || store.code }))}
        onChange={code => { const store = stores.find(item => item.code === code); if (store) setSelectedStore(store) }} /></label>
    </div>
    <p className="text-muted-foreground">Submit by {dayjs(period).subtract(3, 'day').format('ddd, MMM D')}. Select dates to set hours or mark unavailable. Assigned shifts stay unchanged.</p>
    {!storeCode ? <Alert type="info" message="Choose a store to enter your availability." /> : <>
      {errors[scope] && <Alert type="error" showIcon message={errors[scope]} action={<Button onClick={() => {
        setDrafts(previous => { const next = { ...previous }; delete next[scope]; return next })
        setErrors(previous => ({ ...previous, [scope]: '' })); setReload(value => value + 1)
      }} disabled={busy}>{draft?.dirty ? 'Discard edits and reload' : 'Retry loading'}</Button>} />}
      {!draft ? !errors[scope] && <Spin aria-label="Loading availability" /> : <>
        <div className="pc-availability-grid" aria-label="Two-week availability calendar">
          {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(day => <div key={day} className="pc-availability-weekday">{day}</div>)}
          {draft.days.map(day => <button type="button" key={day.date} disabled={busy}
            className={`pc-availability-day pc-availability-${day.status}`}
            aria-pressed={selected.includes(day.date)}
            aria-label={`${dayjs(day.date).format('dddd, MMM D')}: ${day.status === 'unset' ? 'Not set' : day.status === 'unavailable' ? 'Unavailable' : `Available ${day.start_time}–${day.end_time}`}`}
            onClick={() => {
              if (selected.length === 0) { setStatus(day.status === 'unavailable' ? 'unavailable' : 'available'); setStart(day.start_time || '12:00'); setEnd(day.end_time || '22:00'); setNotes(day.notes) }
              setSelected(previous => previous.includes(day.date) ? previous.filter(date => date !== day.date) : [...previous, day.date])
            }}>
            <strong>{dayjs(day.date).format('D')}{day.date.endsWith('-01') ? ` ${dayjs(day.date).format('MMM')}` : ''}</strong>
            <span>{day.status === 'unset' ? 'Not set' : day.status === 'unavailable' ? 'Unavailable' : 'Available'}</span>
            {day.status === 'available' && <small>{day.start_time}–{day.end_time}</small>}
          </button>)}
        </div>
        <div className="pc-schedule-detail">
          <h4>{selected.length === 1 ? dayjs(selected[0]).format('dddd, MMM D') : `${selected.length} dates selected`}</h4>
          <div className="pc-availability-editor">
            <label>Availability <Select aria-label="Availability response" value={status} disabled={busy} onChange={setStatus} options={[{ value: 'available', label: 'Available' }, { value: 'unavailable', label: 'Unavailable' }]} /></label>
            {status === 'available' && <>
              <label>From <input aria-label="Available from" type="time" value={start} disabled={busy} onChange={event => setStart(event.target.value)} /></label>
              <label>Until <input aria-label="Available until" type="time" value={end} disabled={busy} onChange={event => setEnd(event.target.value)} /></label>
            </>}
            <label>Note (optional) <Input aria-label="Availability note" maxLength={1000} value={notes} disabled={busy} onChange={event => setNotes(event.target.value)} /></label>
            <Button type="primary" disabled={!selected.length || busy} onClick={apply}>Apply to selected dates</Button>
          </div>
        </div>
        <div className="pc-schedule-footer">
          <span role="status">{draft.dirty ? `${completed}/14 dates completed · Changes not submitted` : draft.submittedAt ? `Submitted · ${dayjs(draft.submittedAt).format('MMM D, HH:mm')}` : `${completed}/14 dates completed · Not submitted`}</span>
          <Space wrap><Button disabled={busy} onClick={() => update(repeatFirstWeek(draft.days))}>Repeat first week</Button>
            <Button type="primary" loading={busy} disabled={completed !== 14 || (!draft.dirty && !!draft.submittedAt)} onClick={submit}>{draft.submittedAt ? 'Resubmit two weeks' : 'Submit two weeks'}</Button></Space>
        </div>
      </>}
    </>}
  </section>
}
