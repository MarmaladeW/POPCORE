import { createContext, useContext, useEffect, useState } from 'react'

// Checkout announces an unsaved order with this event; the store picker locks while it is busy.
// The shell listens once (it never remounts) and shares the state, so a store chip that mounts
// later still sees an order already in progress.
export function useCheckoutBusyListener() {
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const update = (event: Event) => setBusy(Boolean((event as CustomEvent).detail))
    window.addEventListener('popcore:checkout-busy', update)
    return () => window.removeEventListener('popcore:checkout-busy', update)
  }, [])
  return busy
}

export const CheckoutBusy = createContext(false)
export const useCheckoutBusy = () => useContext(CheckoutBusy)
