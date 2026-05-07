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
    <div className="px-2">
      <h3 className="text-xl font-semibold mb-4">
        {isManager ? 'Shift Scheduling' : 'My Schedule'}
      </h3>
      {isManager ? <ManagerView /> : <EmployeeView />}
    </div>
  )
}
