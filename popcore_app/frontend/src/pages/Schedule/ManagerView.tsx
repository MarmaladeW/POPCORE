import { Tabs } from 'antd'
import { EllipsisOutlined } from '@ant-design/icons'
import { useHasRole } from '../../auth/useRole'
import ManagerCalendar from './ManagerCalendar'
import EmployeeView from './EmployeeView'
import TeamAvailability from './TeamAvailability'
import AvailabilityCalendar from './AvailabilityCalendar'
import MonthlyReport from './MonthlyReport'
import MyHours from './MyHours'

export default function ManagerView() {
  // The admin makes the schedule, a 店长/副店长's shifts too (owner, 2026-11-28), so they send their own hours as staff do.
  const isAdmin = useHasRole('admin')
  return <Tabs className="pc-schedule-tabs" defaultActiveKey="calendar" more={{ icon: <EllipsisOutlined aria-label="More tabs" /> }} items={[
    { key: 'calendar', label: '团队排班 Team schedule', children: <ManagerCalendar /> },
    { key: 'availability', label: '可上时间 Availability', children: <TeamAvailability /> },
    ...isAdmin ? [] : [{ key: 'my-availability', label: '我的可上时间 My availability', children: <AvailabilityCalendar /> }],
    { key: 'shifts', label: '我的班 My shifts', children: <EmployeeView /> },
    // A 店长/副店长 sees only their own 月工时, as staff do (2026-11-28); the team report is the admin's.
    isAdmin ? { key: 'report', label: '月工时 Monthly report', children: <MonthlyReport /> }
      : { key: 'hours', label: '月工时 My hours', children: <MyHours /> },
  ]} />
}
