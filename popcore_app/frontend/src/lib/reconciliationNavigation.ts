import { useEffect } from 'react'

let activeMessage = ''
let activeIndex: number | null = null
let restoring = false

window.addEventListener('popstate', event => {
  if (restoring) {
    restoring = false
    event.stopImmediatePropagation()
    return
  }
  if (activeMessage && !window.confirm(activeMessage)) {
    const targetIndex = Number(event.state?.idx)
    const delta = activeIndex !== null && Number.isInteger(targetIndex) ? activeIndex - targetIndex : 1
    restoring = true
    event.stopImmediatePropagation()
    window.history.go(delta)
  }
})

export function useHistoryReconciliationGuard(active: boolean, message: string) {
  useEffect(() => {
    if (!active) return
    activeMessage = message
    activeIndex = Number.isInteger(window.history.state?.idx) ? window.history.state.idx : null
    return () => {
      if (activeMessage !== message) return
      activeMessage = ''
      activeIndex = null
    }
  }, [active, message])
}
