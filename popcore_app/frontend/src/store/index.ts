import { create } from 'zustand'

export interface Store {
  id: number
  code: string
  name: string
}

/** Sentinel representing "all stores combined" — read-only, never written to. */
export const ALL_STORES: Store = { id: 0, code: 'ALL', name: 'All Stores' }

const STORE_KEY = 'popcore_selected_store'

function loadPersistedStore(): Store | null {
  try {
    const raw = localStorage.getItem(STORE_KEY)
    return raw ? (JSON.parse(raw) as Store) : null
  } catch {
    return null
  }
}

interface AppState {
  series: string[]
  productTypes: string[]
  stores: Store[]
  selectedStore: Store | null
  setSeries: (s: string[]) => void
  setProductTypes: (t: string[]) => void
  setStores: (stores: Store[]) => void
  setSelectedStore: (store: Store) => void
}

export const useAppStore = create<AppState>((set) => ({
  series: [],
  productTypes: [],
  stores: [],
  selectedStore: loadPersistedStore(),
  setSeries: (series) => set({ series }),
  setProductTypes: (productTypes) => set({ productTypes }),
  setStores: (stores) => set({ stores }),
  setSelectedStore: (store) => {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(store)) } catch { /* ignore */ }
    set({ selectedStore: store })
  },
}))
