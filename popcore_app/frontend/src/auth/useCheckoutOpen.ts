import { canCheckout } from '../lib/inventoryView'
import { useAppStore } from '../store'
import { useHasRole } from './useRole'

/** Whether this account takes checkouts at the selected store (staff wait for its Clover). */
export function useCheckoutOpen(): boolean {
  const manager = useHasRole('manager')
  const stores = useAppStore(state => state.stores)
  const selected = useAppStore(state => state.selectedStore)
  return canCheckout(stores, selected, manager)
}
