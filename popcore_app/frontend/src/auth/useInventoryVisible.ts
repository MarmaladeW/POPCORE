import { canSeeInventory } from '../lib/inventoryView'
import { useAppStore } from '../store'
import { useHasRole } from './useRole'

/** Whether this account may see the selected store's stock (库存查看). */
export function useInventoryVisible(): boolean {
  const manager = useHasRole('manager')
  const stores = useAppStore(state => state.stores)
  const selected = useAppStore(state => state.selectedStore)
  return canSeeInventory(stores, selected, manager)
}
