import { create } from 'zustand'

interface AppState {
  series: string[]
  productTypes: string[]
  setSeries: (s: string[]) => void
  setProductTypes: (t: string[]) => void
}

export const useAppStore = create<AppState>((set) => ({
  series: [],
  productTypes: [],
  setSeries: (series) => set({ series }),
  setProductTypes: (productTypes) => set({ productTypes }),
}))
