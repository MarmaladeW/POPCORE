// 移货 Move stock (owner, 2026-11-24: one easy way to move goods): from where, to where, which goods and how many.
// The server decides what each move does to stock (stock_moves.py); these helpers keep the list being written.

export type Place = { code: string; name: string }
export type Places = { from: Place[]; to: Place[]; reasons: Place[]; usual: { from: string | null; to: string | null } }
export type MoveItem = {
  productId: number; name: string; sku: string; unit: string | null
  perSet: number | null; canHaveSet: boolean; sets: string; units: string; setSize: string
}
// `opened`: whole sets that open because loose boxes are taken out of them.
export type CheckLine = { product_id: number; name: string; per_set: number | null; sets: number; units: number; missing: number; opened?: number }
export type MoveLine = { product_id: number; name: string; text: string; sets: number; units: number; missing: number }
export type Move = {
  id: number; store_id: number; business_date: string; created_at: string; from: string; to: string; from_name: string
  to_name: string; reason: string | null; note: string; by: string; lines: MoveLine[]
  undone: null | { at: string; by: string }; can_undo: boolean
}
export type MovesDay = { store_id: number; business_date: string; today: string; moves: Move[] }

export const BACK = ['upstairs', 'warehouse']
const whole = (text: string) => /^\d{1,6}$/.test(text.trim()) ? Number(text.trim()) : 0

/** Whole sets are asked for only where they are kept: out of a back room, or arriving as new goods. */
export const asksSets = (from: string, item: MoveItem) =>
  (BACK.includes(from) && item.perSet !== null) || (from === 'new' && (item.perSet !== null || item.canHaveSet))

/** A blind box with no whole set yet gets its first one from new goods put in a back room: ask its size. */
export const asksSetSize = (from: string, to: string, item: MoveItem) =>
  from === 'new' && BACK.includes(to) && item.perSet === null && item.canHaveSet && whole(item.sets) > 0

/** The lines a move sends: only products with a quantity, sets only where they are asked. */
export function moveLines(items: MoveItem[], from: string, to: string) {
  return items.map(item => {
    const sets = asksSets(from, item) ? whole(item.sets) : 0
    return { product_id: item.productId, sets, units: whole(item.units),
      ...(asksSetSize(from, to, item) && whole(item.setSize) > 1 ? { set_size: whole(item.setSize) } : {}) }
  }).filter(line => line.sets + line.units > 0)
}

/** "6 盒" under a line of whole sets, so the person sees what lands. */
export function landing(item: MoveItem, from: string): string {
  const sets = asksSets(from, item) ? whole(item.sets) : 0
  const per = item.perSet ?? whole(item.setSize)
  return sets && per ? `${sets} 端 = ${sets * per} 盒` : ''
}

/** What still stops Save, one thing at a time; '' when the move can be saved. */
export function waitingFor(from: string, to: string, reason: string, note: string, items: MoveItem[]): string {
  if (!from) return '选从哪里'
  if (!to) return '选到哪里'
  if (from === to) return '从哪里和到哪里一样'
  if (!items.length) return '加要移的货'
  if (!moveLines(items, from, to).length) return '填数量'
  if (items.some(item => asksSetSize(from, to, item) && whole(item.setSize) < 2)) return '填每端几盒'
  if (to === 'gone' && !reason) return '选原因'
  if (to === 'gone' && reason === 'other' && !note.trim()) return '写一下原因'
  return ''
}

export const unitWord = (unit: string | null) => unit === 'box' ? '盒' : unit === 'set' ? '端' : '个'
