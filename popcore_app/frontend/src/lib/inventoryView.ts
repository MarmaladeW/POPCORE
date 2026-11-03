// 库存查看 Inventory view (owner, 2026-11-01): managers and admins see every store's stock; anyone
// else only the stores an admin ticked in Users. The server enforces it; this only shapes the app.
type ViewStore = { code: string; inventory_visible?: boolean }

export function canSeeInventory(stores: ViewStore[], selected: { code: string } | null, manager: boolean): boolean {
  if (manager) return true
  if (!selected) return false
  if (selected.code === 'ALL') return stores.length > 0 && stores.every(store => store.inventory_visible)
  return !!stores.find(store => store.code === selected.code)?.inventory_visible
}

// What a stock figure shows: a number, or for a hidden store only whether it is out of stock.
export type StockFigureValue = number | 'in' | 'out' | null

export function stockFigureValue(row: { stock_hidden?: boolean; out_of_stock?: boolean }, total: number): StockFigureValue {
  return row.stock_hidden ? (row.out_of_stock ? 'out' : 'in') : total
}
