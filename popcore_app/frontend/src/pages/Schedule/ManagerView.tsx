import { Tabs } from 'antd'
import { EllipsisOutlined } from '@ant-design/icons'
import ManagerCalendar from './ManagerCalendar'
import EmployeeView from './EmployeeView'
import TeamAvailability from './TeamAvailability'
import MonthlyReport from './MonthlyReport'
import Trainees from './Trainees'

export default function ManagerView() {
  return <Tabs defaultActiveKey="calendar" more={{ icon: <EllipsisOutlined aria-label="More tabs" /> }} items={[
    { key: 'calendar', label: 'Team schedule', children: <ManagerCalendar /> },
    { key: 'availability', label: 'Availability', children: <TeamAvailability /> },
    { key: 'shifts', label: 'My shifts', children: <EmployeeView /> },
    { key: 'trainees', label: 'Trainees', children: <Trainees /> },
    { key: 'report', label: 'Monthly report', children: <MonthlyReport /> },
  ]} />
}
