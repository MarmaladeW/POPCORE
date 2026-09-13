import { Tabs } from 'antd'
import ManagerCalendar from './ManagerCalendar'
import EmployeeView from './EmployeeView'
import TeamAvailability from './TeamAvailability'
import MonthlyReport from './MonthlyReport'
import Trainees from './Trainees'

export default function ManagerView() {
  return <Tabs defaultActiveKey="calendar" items={[
    { key: 'calendar', label: 'Team schedule', children: <ManagerCalendar /> },
    { key: 'availability', label: 'Availability', children: <TeamAvailability /> },
    { key: 'shifts', label: 'My shifts', children: <EmployeeView /> },
    { key: 'trainees', label: 'Trainees', children: <Trainees /> },
    { key: 'report', label: 'Monthly report', children: <MonthlyReport /> },
  ]} />
}
