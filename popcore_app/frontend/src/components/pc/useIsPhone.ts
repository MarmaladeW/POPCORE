import { useSyncExternalStore } from 'react'

// Phone layout below 768px (antd's md breakpoint). Synchronous from the first render, so desktop
// never mounts the phone layout for a frame.
const query = '(max-width: 767.98px)'
const subscribe = (notify: () => void) => {
  const list = window.matchMedia(query)
  list.addEventListener('change', notify)
  return () => list.removeEventListener('change', notify)
}
export function useIsPhone() {
  return useSyncExternalStore(subscribe, () => window.matchMedia(query).matches, () => false)
}
