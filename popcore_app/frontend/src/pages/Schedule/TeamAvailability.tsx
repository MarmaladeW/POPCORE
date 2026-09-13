import { useEffect, useState } from 'react'
import { useAuth0 } from '@auth0/auth0-react'
import { Alert, Button, Select, Space, Spin } from 'antd'
import dayjs from 'dayjs'
import { useAppStore } from '../../store'
import AvailabilityCalendar from './AvailabilityCalendar'
import { cycleDates, cycleStart } from './availabilityPeriod'
import { getAllAvailability, getEmployees, type Availability, type Employee } from './scheduleApi'
import { scheduleApiErrorMessage } from './employeeScheduling'

export default function TeamAvailability() {
  const { user } = useAuth0()
  const { stores, selectedStore, setSelectedStore } = useAppStore()
  const [employeeId, setEmployeeId] = useState<number | 'me'>('me')
  const [employees, setEmployees] = useState<Employee[]>([])
  const [rosterError, setRosterError] = useState('')
  const [period, setPeriod] = useState(() => cycleStart(dayjs().format('YYYY-MM-DD')))
  const [reload, setReload] = useState(0)
  const [response, setResponse] = useState<{ scope: string; days: Availability[]; error?: string }>()
  const storeCode = selectedStore?.code !== 'ALL' ? selectedStore?.code : undefined
  const scope = `${employeeId}:${storeCode}:${period}`
  const dates = cycleDates(period)
  const current = response?.scope === scope ? response : undefined
  const submittedAt = current?.days.find(day => day.submitted_at)?.submitted_at

  useEffect(() => {
    let active = true
    getEmployees().then(result => {
      if (active) { setEmployees(result); setRosterError('') }
    }).catch(error => {
      if (active) setRosterError(scheduleApiErrorMessage(error, 'Could not load employees.'))
    })
    return () => { active = false }
  }, [reload])

  useEffect(() => {
    if (employeeId === 'me' || !storeCode) return
    let active = true
    setResponse(undefined)
    getAllAvailability(period, dates[13], storeCode).then(days => {
      if (active) setResponse({ scope, days: days.filter(day => day.employee_id === employeeId) })
    }).catch(error => {
      if (active) setResponse({ scope, days: [], error: scheduleApiErrorMessage(error, 'Could not load declared availability.') })
    })
    return () => { active = false }
  }, [employeeId, storeCode, period, scope, reload])

  return <div>
    <label className="pc-availability-employee">Employee <Select aria-label="Availability employee" showSearch optionFilterProp="label"
      value={employeeId} onChange={setEmployeeId} style={{ width: 240, maxWidth: '100%' }}
      options={[
        { value: 'me', label: 'My availability' },
        ...employees.filter(employee => employee.auth0_id !== user?.sub && !employee.is_trainee).map(employee => ({
          value: employee.id, label: employee.name || employee.email || `Employee ${employee.id}`,
        })),
      ]} /></label>
    {rosterError && <Alert type="error" message={rosterError} action={<Button onClick={() => setReload(value => value + 1)}>Retry employees</Button>} />}
    {employeeId === 'me' ? <AvailabilityCalendar /> : <section aria-label="Declared availability">
      <div className="pc-page-actions">
        <Space wrap>
          <Button aria-label="Previous declared availability period" onClick={() => setPeriod(dayjs(period).subtract(14, 'day').format('YYYY-MM-DD'))}>‹</Button>
          <strong>{dayjs(period).format('MMM D')} – {dayjs(dates[13]).format('MMM D, YYYY')}</strong>
          <Button aria-label="Next declared availability period" onClick={() => setPeriod(dayjs(period).add(14, 'day').format('YYYY-MM-DD'))}>›</Button>
          <Button onClick={() => setReload(value => value + 1)}>Refresh availability</Button>
        </Space>
        <label>Availability store <Select aria-label="Availability store" placeholder="Choose a store" value={storeCode} style={{ minWidth: 160 }}
          options={stores.filter(store => store.code !== 'ALL').map(store => ({ value: store.code, label: store.name || store.code }))}
          onChange={code => { const store = stores.find(item => item.code === code); if (store) setSelectedStore(store) }} /></label>
      </div>
      <p className="text-muted-foreground">Declared availability · Read only. Assign shifts from Team schedule.</p>
      {!storeCode ? <Alert type="info" message="Choose a store to view declared availability." /> : !current ? <Spin aria-label="Loading declared availability" /> : current.error ?
        <Alert type="error" message={current.error} action={<Button onClick={() => setReload(value => value + 1)}>Retry availability</Button>} /> : <>
          <p role="status">{submittedAt ? `Submitted · ${dayjs(submittedAt).format('MMM D, HH:mm')}` : 'Not submitted for this store and period.'}</p>
          <div className="pc-availability-grid" aria-label="Employee two-week availability calendar">
            {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(day => <div key={day} className="pc-availability-weekday">{day}</div>)}
            {dates.map(date => {
              const day = current.days.find(item => item.date === date && item.submitted_at)
              return <div key={date} className={`pc-availability-day pc-availability-${day?.status ?? 'unset'}`}>
                <strong>{dayjs(date).format('D')}{date.endsWith('-01') ? ` ${dayjs(date).format('MMM')}` : ''}</strong>
                <span>{!day ? 'Not submitted' : day.status === 'unavailable' ? 'Unavailable' : 'Available'}</span>
                {day && day.status !== 'unavailable' && <small>{day.start_time}–{day.end_time}</small>}
                {day?.notes && <small>{day.notes}</small>}
              </div>
            })}
          </div>
        </>}
    </section>}
  </div>
}
