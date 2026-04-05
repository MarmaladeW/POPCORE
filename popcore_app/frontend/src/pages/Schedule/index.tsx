import { Typography } from 'antd'
import { useHasRole } from '../../auth/useRole'
import EmployeeView from './EmployeeView'
import ManagerView from './ManagerView'

const { Title } = Typography

export default function SchedulePage() {
  const isManager = useHasRole('manager')

  return (
    <div style={{ padding: '0 8px' }}>
      <Title level={3} style={{ marginBottom: 16 }}>
        {isManager ? 'Shift Scheduling' : 'My Schedule'}
      </Title>
      {isManager ? <ManagerView /> : <EmployeeView />}
    </div>
  )
}
