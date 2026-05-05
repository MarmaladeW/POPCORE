import { useEffect } from 'react'
import { useHasRole } from '../../auth/useRole'
import EmployeeView from './EmployeeView'
import ManagerView from './ManagerView'
import { getMe } from './scheduleApi'

export default function SchedulePage() {
  const isManager = useHasRole('manager')

  // Auto-register the current user in the employees table on first visit
  useEffect(() => {
    getMe().catch(() => {})
  }, [])

  return (
    <div style={{ padding: '0 8px' }}>
      <h3 style={{ fontSize: 20, fontWeight: 600, marginBottom: 16 }}>
        {isManager ? 'Shift Scheduling' : 'My Schedule'}
      </h3>
      {isManager ? <ManagerView /> : <EmployeeView />}
    </div>
  )
}
