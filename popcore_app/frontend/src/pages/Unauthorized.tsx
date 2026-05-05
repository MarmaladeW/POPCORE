import { Result } from 'antd'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'

export default function Unauthorized() {
  const navigate = useNavigate()
  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60vh' }}>
      <Result
        status="403"
        title="Access Denied"
        subTitle="You don't have permission to view this page. Contact your admin if you need access."
        extra={
          <Button onClick={() => navigate(-1 as any)}>Go Back</Button>
        }
      />
    </div>
  )
}
