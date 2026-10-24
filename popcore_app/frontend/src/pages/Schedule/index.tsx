import { useEffect } from 'react'
import { Tabs } from 'antd'
import { EllipsisOutlined } from '@ant-design/icons'
import { useHasRole } from '../../auth/useRole'
import EmployeeView from './EmployeeView'
import ManagerView from './ManagerView'
import AvailabilityCalendar from './AvailabilityCalendar'
import { getMe } from './scheduleApi'
import PunchIn from './PunchIn'
import PageHeader from '../../components/pc/PageHeader'
import './schedule.css'

export default function SchedulePage() {
  const isManager = useHasRole('manager')
  useEffect(() => { getMe().catch(() => {}) }, [])
  return <div className="pc-schedule-page">
    <PageHeader title="排班" sub="Schedule" />
    <PunchIn />
    {isManager ? <ManagerView /> : <Tabs className="pc-schedule-tabs" defaultActiveKey="availability" more={{ icon: <EllipsisOutlined aria-label="More tabs" /> }} items={[
      { key: 'availability', label: '可上时间 My availability', children: <AvailabilityCalendar /> },
      { key: 'shifts', label: '我的班 My shifts', children: <EmployeeView /> },
    ]} />}
  </div>
}
