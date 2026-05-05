import { Result } from 'antd'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'

export default function NotFound() {
  const navigate = useNavigate()
  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60vh' }}>
      <Result
        status="404"
        title="404"
        subTitle="This page does not exist."
        extra={
          <Button onClick={() => navigate('/')}>Back to Home</Button>
        }
      />
    </div>
  )
}
