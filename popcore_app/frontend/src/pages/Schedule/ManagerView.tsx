import { Tabs } from 'antd'
import { EllipsisOutlined } from '@ant-design/icons'
import ManagerCalendar from './ManagerCalendar'
import EmployeeView from './EmployeeView'
import TeamAvailability from './TeamAvailability'
import MonthlyReport from './MonthlyReport'
import Trainees from './Trainees'

export default function ManagerView() {
  return <Tabs className="pc-schedule-tabs" defaultActiveKey="calendar" more={{ icon: <EllipsisOutlined aria-label="More tabs" /> }} items={[
    { key: 'calendar', label: '团队排班 Team schedule', children: <ManagerCalendar /> },
    { key: 'availability', label: '可上时间 Availability', children: <TeamAvailability /> },
    { key: 'shifts', label: '我的班 My shifts', children: <EmployeeView /> },
    { key: 'trainees', label: '实习生 Trainees', children: <Trainees /> },
    { key: 'report', label: '月工时 Monthly report', children: <MonthlyReport /> },
  ]} />
}
