import { useAuth0 } from '@auth0/auth0-react'

export type Role = 'viewer' | 'staff' | 'manager' | 'admin'

const ROLE_LEVELS: Record<Role, number> = {
  viewer:  0,
  staff:   1,
  manager: 2,
  admin:   3,
}

const ROLE_CLAIM = 'https://popcore/role'

export function useRole(): Role {
  const { user } = useAuth0()
  return (user?.[ROLE_CLAIM] as Role) ?? 'viewer'
}

export function useHasRole(minRole: Role): boolean {
  const role = useRole()
  return ROLE_LEVELS[role] >= ROLE_LEVELS[minRole]
}

export { ROLE_LEVELS }
