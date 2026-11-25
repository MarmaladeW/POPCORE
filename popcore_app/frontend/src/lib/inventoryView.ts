// 库存查看 Inventory view (owner, 2026-11-01): managers and admins see every store's stock; anyone
// else only the stores an admin ticked in Users. The server enforces it; this only shapes the app.
type ViewStore = { code: string; inventory_visible?: boolean }

export function canSeeInventory(stores: ViewStore[], selected: { code: string } | null, manager: boolean): boolean {
  if (manager) return true
  if (!selected) return false
  if (selected.code === 'ALL') return stores.length > 0 && stores.every(store => store.inventory_visible)
  return !!stores.find(store => store.code === selected.code)?.inventory_visible
}

// 买单 Checkout and 补录 (owner, 2026-11-20): until a store's Clover is connected its sales come from the 日报, so
// staff do not see 买单 there; managers and admins always do. A store the server says nothing about stays open.
export function canCheckout(stores: { code: string; checkout?: boolean }[], selected: { code: string } | null, manager: boolean): boolean {
  if (manager) return true
  const open = (store?: { checkout?: boolean }) => !!store && store.checkout !== false
  if (!selected || selected.code === 'ALL') return stores.length === 0 || stores.some(open)
  return open(stores.find(store => store.code === selected.code))
}

// What a stock figure shows: a number, or for a hidden store only whether it is out of stock.
export type StockFigureValue = number | 'in' | 'out' | null

export function stockFigureValue(row: { stock_hidden?: boolean; out_of_stock?: boolean }, total: number): StockFigureValue {
  return row.stock_hidden ? (row.out_of_stock ? 'out' : 'in') : total
}
