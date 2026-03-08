import { useAuth0 } from '@auth0/auth0-react'
import { Spin, Result, Button } from 'antd'

export default function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isLoading, isAuthenticated, loginWithRedirect, error } = useAuth0()

  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spin size="large" />
      </div>
    )
  }

  if (error) {
    return (
      <Result
        status="error"
        title="登录失败"
        subTitle={error.message}
        extra={<Button type="primary" onClick={() => loginWithRedirect()}>重试</Button>}
      />
    )
  }

  if (!isAuthenticated) {
    loginWithRedirect()
    return null
  }

  return <>{children}</>
}
