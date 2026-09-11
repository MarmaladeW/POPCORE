import { useSyncExternalStore, type ReactNode } from 'react'

type AuthState = {
  loading?: boolean
  authenticated?: boolean
  role?: 'viewer' | 'staff' | 'manager' | 'admin'
  tokenReject?: boolean
  redirectReject?: boolean
  token?: string
}

function state(): AuthState {
  return (globalThis as any).__FOUNDATION_AUTH ?? {}
}

export function Auth0Provider({ children }: { children: ReactNode }) {
  return children
}

export function useAuth0() {
  useSyncExternalStore(
    (notify) => { globalThis.addEventListener('foundation-auth', notify); return () => globalThis.removeEventListener('foundation-auth', notify) },
    () => JSON.stringify((globalThis as any).__FOUNDATION_AUTH ?? {}),
  )
  const current = state()
  const role = current.role ?? 'admin'
  return {
    isLoading: current.loading ?? false,
    isAuthenticated: current.authenticated ?? true,
    error: undefined,
    user: {
      sub: `fixture|${role}`,
      name: 'Foundation User',
      email: 'foundation@example.invalid',
      picture: '',
      'https://popcore/role': role,
    },
    getAccessTokenSilently: async () => {
      if (state().tokenReject) throw new Error('Synthetic token failure')
      return state().token ?? 'fixture-token'
    },
    loginWithRedirect: async () => {
      ;(globalThis as any).__FOUNDATION_REDIRECTS = ((globalThis as any).__FOUNDATION_REDIRECTS ?? 0) + 1
      if (state().redirectReject) throw new Error('Synthetic redirect failure')
    },
    logout: () => undefined,
  }
}
