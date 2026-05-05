import { useAuth0 } from '@auth0/auth0-react'
import { Result } from 'antd'
import { Button } from '@/components/ui/button'
import { Spinner } from '../components/Spinner'

export default function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isLoading, isAuthenticated, loginWithRedirect, error } = useAuth0()

  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spinner />
      </div>
    )
  }

  if (error) {
    return (
      <Result
        status="error"
        title="登录失败"
        subTitle={error.message}
        extra={<Button onClick={() => loginWithRedirect()}>重试</Button>}
      />
    )
  }

  if (!isAuthenticated) {
    loginWithRedirect()
    return null
  }

  return <>{children}</>
}
