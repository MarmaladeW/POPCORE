import { useAuth0 } from '@auth0/auth0-react'
import { useEffect, useRef, useState } from 'react'
import { Result } from 'antd'
import { Button } from '@/components/ui/button'
import { Spinner } from '../components/Spinner'
import { hasRole } from './useRole'

export default function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isLoading, isAuthenticated, loginWithRedirect, logout, error, user } = useAuth0()
  const redirectStarted = useRef(false)
  const [redirectError, setRedirectError] = useState<string | null>(null)

  useEffect(() => {
    if (!isLoading && !isAuthenticated && !error && !redirectStarted.current) {
      redirectStarted.current = true
      loginWithRedirect().catch(err => {
        setRedirectError(err instanceof Error ? err.message : 'Unable to start sign-in')
      })
    }
  }, [error, isAuthenticated, isLoading, loginWithRedirect])

  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spinner />
      </div>
    )
  }

  if (error || redirectError) {
    return (
      <Result
        status="error"
        title="登录失败"
        subTitle={error?.message ?? redirectError}
        extra={<Button onClick={() => {
          redirectStarted.current = false
          setRedirectError(null)
          loginWithRedirect().catch(err => {
            setRedirectError(err instanceof Error ? err.message : 'Unable to start sign-in')
          })
        }}>Retry sign-in</Button>}
      />
    )
  }

  if (!isAuthenticated) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spinner />
      </div>
    )
  }

  // An account a manager never gave a role (for example one made through a public sign-up) gets nothing.
  if (!hasRole(user)) {
    return (
      <Result
        status="403"
        title="没有权限 / No access"
        subTitle="This account has not been set up for POPCORE. Ask a manager to add you."
        extra={<Button onClick={() => logout({ logoutParams: { returnTo: window.location.origin } })}>
          退出登录 / Sign out
        </Button>}
      />
    )
  }

  return <>{children}</>
}
