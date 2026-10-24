import { ALL_STORES, useAppStore } from '../../store'
import { useCheckoutBusy } from './useCheckoutBusy'

// Figma Chip: the store picker. A native select keeps the phone picker and keyboard behaviour.
export default function StoreChip() {
  const { stores, selectedStore, setSelectedStore } = useAppStore()
  const busy = useCheckoutBusy()
  if (!stores.length || !selectedStore) return null
  return <label className="pc-chip pc-store-chip">
    <span aria-hidden="true">{selectedStore.code}</span>
    <select
      aria-label="Operations store"
      disabled={busy}
      value={selectedStore.code}
      onChange={event => {
        const code = event.target.value
        if (code === 'ALL') setSelectedStore(ALL_STORES)
        else {
          const store = stores.find(entry => entry.code === code)
          if (store) setSelectedStore(store)
        }
      }}
    >
      <option value="ALL">All stores</option>
      {stores.map(store => <option key={store.code} value={store.code}>{store.code} · {store.name || store.code}</option>)}
    </select>
  </label>
}
