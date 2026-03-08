import { useHasRole, type Role } from '../auth/useRole'

interface Props {
  minRole: Role
  children: React.ReactNode
  fallback?: React.ReactNode
}

export default function RoleGuard({ minRole, children, fallback = null }: Props) {
  const allowed = useHasRole(minRole)
  return allowed ? <>{children}</> : <>{fallback}</>
}
