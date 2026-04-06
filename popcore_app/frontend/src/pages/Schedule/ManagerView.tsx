import { Tabs } from 'antd'
import ManagerCalendar from './ManagerCalendar'
import EmployeeView from './EmployeeView'
import MonthlyReport from './MonthlyReport'

export default function ManagerView() {
  return (
    <Tabs
      defaultActiveKey="calendar"
      items={[
        {
          key: 'calendar',
          label: 'Team Schedule',
          children: <ManagerCalendar />,
        },
        {
          key: 'availability',
          label: 'My Availability',
          children: <EmployeeView />,
        },
        {
          key: 'report',
          label: 'Monthly Report',
          children: <MonthlyReport />,
        },
      ]}
    />
  )
}
