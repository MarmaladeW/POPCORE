// After signing in, go back to the page you were on (owner, 2026-11-20: a tapped 网站订单 notification used to land
// on Home). Only a path inside POPCORE is followed.
export type ReturnState = { returnTo?: string }

export function hereState(location: { pathname: string; search: string } = window.location): ReturnState {
  return { returnTo: `${location.pathname}${location.search}` }
}

export function safeReturn(value: unknown): string {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && !value.includes('\\')
    && !/^\/\?(.*&)?(code|state)=/.test(value) ? value : '/'
}
