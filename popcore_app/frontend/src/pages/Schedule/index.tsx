import { useEffect } from 'react'
import { Tabs } from 'antd'
import { EllipsisOutlined } from '@ant-design/icons'
import { useHasRole } from '../../auth/useRole'
import EmployeeView from './EmployeeView'
import ManagerView from './ManagerView'
import AvailabilityCalendar from './AvailabilityCalendar'
import { getMe } from './scheduleApi'
import PunchIn from './PunchIn'
import './schedule.css'

export default function SchedulePage() {
  const isManager = useHasRole('manager')
  useEffect(() => { getMe().catch(() => {}) }, [])
  return <div className="pc-schedule-page">
    <h2>Schedule</h2>
    <PunchIn />
    {isManager ? <ManagerView /> : <Tabs defaultActiveKey="availability" more={{ icon: <EllipsisOutlined aria-label="More tabs" /> }} items={[
      { key: 'availability', label: 'My availability', children: <AvailabilityCalendar /> },
      { key: 'shifts', label: 'My shifts', children: <EmployeeView /> },
    ]} />}
  </div>
}
