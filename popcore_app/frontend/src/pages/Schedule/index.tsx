import { useEffect } from 'react'
import { Typography } from 'antd'
import { useHasRole } from '../../auth/useRole'
import EmployeeView from './EmployeeView'
import ManagerView from './ManagerView'
import { getMe } from './scheduleApi'

const { Title } = Typography

export default function SchedulePage() {
  const isManager = useHasRole('manager')

  // Auto-register the current user in the employees table on first visit
  useEffect(() => {
    getMe().catch(() => {})
  }, [])

  return (
    <div style={{ padding: '0 8px' }}>
      <Title level={3} style={{ marginBottom: 16 }}>
        {isManager ? 'Shift Scheduling' : 'My Schedule'}
      </Title>
      {isManager ? <ManagerView /> : <EmployeeView />}
    </div>
  )
}

