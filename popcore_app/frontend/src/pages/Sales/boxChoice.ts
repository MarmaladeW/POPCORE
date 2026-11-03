// Blind boxes over half a set (owner, 2026-10-27): 开新端 a new sealed set, 已开的端 a set already opened on the
// floor, or 散盒 loose boxes from the floor. The server asks; these helpers word the answers the way it takes them.

export type BoxChoice = 'fresh_set' | 'open_set' | 'loose'
// Without 库存查看 at the store (owner, 2026-11-01) the counts are null: the server says only whether
// there is a sealed set (out_of_stock) and loose boxes (loose_out_of_stock) to take.
export type BoxOptions = {
  set_size: number
  fresh_set: { product_id: number; conversion_id: number; conversion_factor: number; name: string; sets_on_hand: number | null; out_of_stock?: boolean } | null
  open_sets: { id: number; remaining_qty: number | null }[]
  loose_units: number | null
  stock_hidden?: boolean
  loose_out_of_stock?: boolean
}
export type BoxAnswer = { choice: BoxChoice; openSetId?: number | null }

export const BOX_CHOICES: { value: BoxChoice; zh: string; label: string }[] = [
  { value: 'fresh_set', zh: '开新端', label: 'Opened a new sealed set' },
  { value: 'open_set', zh: '已开的端', label: 'From an opened set' },
  { value: 'loose', zh: '散盒', label: 'Loose boxes from the floor' },
]

/** The server's rule: more than half a set of a blind box that has a sealed set needs an answer. */
export function needsBoxAnswer(options: BoxOptions | null | undefined, quantity: number) {
  return !!options && Number.isSafeInteger(quantity) && quantity > Math.floor(options.set_size / 2)
}

/** Whether an answer can be sent: a sealed set must exist to open, and an opened set must be picked. */
export function boxAnswerReady(answer: BoxAnswer | undefined, options: BoxOptions) {
  if (!answer) return false
  if (answer.choice === 'fresh_set') return options.fresh_set !== null
  if (answer.choice === 'open_set') return options.open_sets.some(set => set.id === answer.openSetId)
  return true
}

/** Sale line fields for an answer: exactly one of fresh_set, open_set_id or loose_boxes is set. */
export function boxAnswerFields(answer: BoxAnswer, options: BoxOptions) {
  const set = answer.choice === 'fresh_set' ? options.fresh_set : null
  return {
    open_set_id: answer.choice === 'open_set' ? answer.openSetId ?? null : null,
    fresh_set: set ? { product_id: set.product_id, conversion_id: set.conversion_id, conversion_factor: set.conversion_factor } : null,
    loose_boxes: answer.choice === 'loose',
  }
}

const inStock = (out?: boolean) => out ? '缺货 Out of stock' : '有货 In stock'

/** What each answer has on hand, for its button. */
export function boxChoiceNote(choice: BoxChoice, options: BoxOptions | null) {
  if (!options) return ''
  if (choice === 'fresh_set') {
    if (!options.fresh_set) return 'No sealed set'
    return options.stock_hidden ? inStock(options.fresh_set.out_of_stock) : `${options.fresh_set.sets_on_hand} sealed on hand`
  }
  if (choice === 'open_set') {
    if (!options.open_sets.length) return 'None opened'
    if (options.stock_hidden) return `${options.open_sets.length} opened`
    const left = options.open_sets.reduce((sum, set) => sum + (set.remaining_qty ?? 0), 0)
    return `${left} left in ${options.open_sets.length} opened`
  }
  return options.stock_hidden ? inStock(options.loose_out_of_stock) : `${options.loose_units} loose`
}

/** An opened set's button: which set, and how many are left in it where that is shown. */
export function openSetLabel(set: { id: number; remaining_qty: number | null }) {
  return set.remaining_qty == null ? `Opened set #${set.id}` : `Opened set #${set.id} · ${set.remaining_qty} left`
}
