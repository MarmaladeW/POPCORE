import { useEffect } from 'react'
import { Tabs } from 'antd'
import { useHasRole } from '../../auth/useRole'
import EmployeeView from './EmployeeView'
import ManagerView from './ManagerView'
import AvailabilityCalendar from './AvailabilityCalendar'
import { getMe } from './scheduleApi'
import './schedule.css'

export default function SchedulePage() {
  const isManager = useHasRole('manager')
  useEffect(() => { getMe().catch(() => {}) }, [])
  return <div className="pc-schedule-page">
    <h2>Schedule</h2>
    {isManager ? <ManagerView /> : <Tabs defaultActiveKey="availability" items={[
      { key: 'availability', label: 'My availability', children: <AvailabilityCalendar /> },
      { key: 'shifts', label: 'My shifts', children: <EmployeeView /> },
    ]} />}
  </div>
}
