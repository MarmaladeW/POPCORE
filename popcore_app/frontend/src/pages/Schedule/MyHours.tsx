import { useState } from 'react'
import dayjs from 'dayjs'
import EmployeeMonthReport from './EmployeeMonthReport'

// 月工时 My hours (owner, 2026-11-26): one's own month. Staff and 店长/副店长 alike (2026-11-28: "manager can only see
// their own monthly report"); only the admin opens the team report.
export default function MyHours() {
  const [month, setMonth] = useState(() => dayjs().startOf('month'))
  const latest = dayjs().startOf('month').add(1, 'month')
  return <EmployeeMonthReport employeeId="me" year={month.year()} month={month.month() + 1} storeCode="ALL"
    onPrevious={() => setMonth(value => value.subtract(1, 'month'))}
    onNext={month.isBefore(latest) ? () => setMonth(value => value.add(1, 'month')) : undefined} />
}
