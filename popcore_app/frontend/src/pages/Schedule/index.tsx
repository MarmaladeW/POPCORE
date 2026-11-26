import { useEffect, useState } from 'react'
import { Tabs } from 'antd'
import dayjs from 'dayjs'
import { EllipsisOutlined } from '@ant-design/icons'
import { useHasRole } from '../../auth/useRole'
import EmployeeView from './EmployeeView'
import ManagerView from './ManagerView'
import AvailabilityCalendar from './AvailabilityCalendar'
import { getMe } from './scheduleApi'
import PunchIn from './PunchIn'
import EmployeeMonthReport from './EmployeeMonthReport'
import PageHeader from '../../components/pc/PageHeader'
import './schedule.css'

// 月工时 My hours (owner, 2026-11-26): one's own month, the same slip a manager opens from the monthly report.
function MyHours() {
  const [month, setMonth] = useState(() => dayjs().startOf('month'))
  const latest = dayjs().startOf('month').add(1, 'month')
  return <EmployeeMonthReport employeeId="me" year={month.year()} month={month.month() + 1} storeCode="ALL"
    onPrevious={() => setMonth(value => value.subtract(1, 'month'))}
    onNext={month.isBefore(latest) ? () => setMonth(value => value.add(1, 'month')) : undefined} />
}

export default function SchedulePage() {
  const isManager = useHasRole('manager')
  useEffect(() => { getMe().catch(() => {}) }, [])
  return <div className="pc-schedule-page">
    <PageHeader title="排班" sub="Schedule" />
    <PunchIn />
    {isManager ? <ManagerView /> : <Tabs className="pc-schedule-tabs" defaultActiveKey="availability" more={{ icon: <EllipsisOutlined aria-label="More tabs" /> }} items={[
      { key: 'availability', label: '可上时间 My availability', children: <AvailabilityCalendar /> },
      { key: 'shifts', label: '我的班 My shifts', children: <EmployeeView /> },
      { key: 'hours', label: '月工时 My hours', children: <MyHours /> },
    ]} />}
  </div>
}
