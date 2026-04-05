import { Tabs } from 'antd'
import ManagerCalendar from './ManagerCalendar'
import MonthlyReport from './MonthlyReport'

export default function ManagerView() {
  return (
    <Tabs
      defaultActiveKey="calendar"
      items={[
        {
          key: 'calendar',
          label: 'Schedule',
          children: <ManagerCalendar />,
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
