import type { ReportAnnotations } from '../../api/matcher.ts'

export const REPORT_NOTE_SECTIONS = [
  { key: 'employee_discounts', label: '员工折扣 / Employee discounts', description: '已计入销售汇总，仅保留备注，不再增加数量。' },
  { key: 'display_sales', label: '卖Display / Display sales', description: '已计入销售汇总，不再增加销售数量或收款。' },
  { key: 'claw_prizes', label: '娃娃机出奖 / Claw prizes out', description: '独立奖品库存出库记录，不是销售或收款；仅保存原文，不更改库存。' },
  { key: 'cash_exchanges', label: 'EMT 换现金 / Cash exchanges', description: '收到 EMT 后付出等额现金，不是销售。*2 不代表已知金额，不据此计算现金差额。' },
  { key: 'claw_stock_in', label: '娃娃机入库 / Claw stock in', description: '奖品入库记录；仅保存原文，不更改库存。补货请在 娃娃机 → 补货 Refill 记录。' },
  { key: 'display_stock_in', label: '已拆 Display 入库 / Opened display stock in', description: '已拆展示品入库记录；仅保存原文，不增加未拆封库存。' },
  { key: 'display_stock_out', label: '已拆 Display 出库 / Opened display stock out', description: '已拆展示品出库记录；仅保存原文，不扣减未拆封库存。' },
  { key: 'store_transfers', label: '出库 / To the other store', description: '日报不移这些货（另一家店还没上线，或这一天早于日报开始自己移出库的那天）：仅保存原文。请用 调拨 Transfer 记录送去另一家店的货。' },
] as const

// A live store's stock lines (owner, 2026-11-03; 入库, 出库, 出店入库, 出display入店 since 2026-11-13), what each
// one moves, and the ones written as sets.
export const STOCK_LINE_SECTIONS = [
  'refill', 'stock_in', 'stock_out', 'display_sold', 'break_display', 'display_stock_in', 'claw_prize',
  'back_in', 'transfer_out', 'floor_to_back', 'display_to_floor',
] as const
export const PACKED_SECTIONS = ['stock_in', 'refill', 'back_in', 'transfer_out', 'floor_to_back', 'display_to_floor']
const LEGACY_SECTIONS = ['pos', 'cash', 'stock_in', 'stock_out', 'break_display']

export function stockLineEffect(row: { section: string; to_display?: boolean; from_display?: boolean; to_floor?: boolean }): string | null {
  switch (row.section) {
    case 'back_in': return '新货 New goods → 楼上/仓库'
    case 'transfer_out': return row.to_floor ? '楼上/仓库 → 另一家店楼下 Other store floor' : '楼上/仓库 → 另一家店楼上/仓库 Other store'
    case 'floor_to_back': return '楼下 Floor → 楼上/仓库'
    case 'display_to_floor': return 'Display → 楼下 Floor'

    case 'refill': return row.to_display ? '楼上/仓库 → Display' : '楼上/仓库 → 楼下 Floor'
    case 'stock_in': return row.to_display ? '新货 New goods → Display' : '新货 New goods → 楼下 Floor'
    case 'stock_out': return row.from_display ? 'Display → 出店 Out' : '楼下 Floor → 出店 Out'
    case 'display_sold': return 'Display → 卖出 Sold (楼下不扣 floor not taken)'
    case 'break_display': return '楼下 Floor → Display'
    case 'display_stock_in': return '楼上/仓库 → Display'
    case 'claw_prize': return '娃娃机 Claw → 出奖 Prize'
    default: return null
  }
}

/** Where a format check points: "第 82、86 行 · 出库入店", or the whole report when it names no line. */
export function formatCheckWhere(check: { lines: number[]; section: string | null }): string {
  const where = check.lines.length ? `第 ${check.lines.join('、')} 行 Line ${check.lines.join(', ')}` : '整份日报 Whole report'
  return check.section ? `${where} · ${check.section}` : where
}

export function hasReportNotes(notes: ReportAnnotations): boolean {
  return REPORT_NOTE_SECTIONS.some(({ key }) => !!notes[key]?.length)
}

export function positiveInteger(value: number | null): boolean {
  return value != null && Number.isSafeInteger(value) && value >= 1
}

export function validReportQuantity(row: {
  section: string; qty: number; box_size: number | null; loose_qty?: number
}): boolean {
  const loose = row.loose_qty ?? 0
  return positiveInteger(row.qty) && (!PACKED_SECTIONS.includes(row.section) || (
    positiveInteger(row.box_size) && Number.isSafeInteger(loose) && loose >= 0
    && Number.isSafeInteger(row.qty * row.box_size! + loose)
  ))
}

export function isReportRowReady(row: {
  section: string; qty: number; box_size: number | null; loose_qty?: number; flagged: boolean
  product?: { id: number }; accepted?: boolean; unknown_header?: string | null
}, live = false): boolean {
  const sections: readonly string[] = live ? ['pos', 'cash', ...STOCK_LINE_SECTIONS] : LEGACY_SECTIONS
  return !!row.product?.id && row.accepted !== false && !row.flagged && !row.unknown_header
    && sections.includes(row.section)
    && validReportQuantity(row)
}

/** What a report submit sends for one reviewed row, by its section. */
export function reportPayloadQuantities(row: {
  section: string; qty: number; box_size?: number | null; loose_qty?: number; to_display?: boolean; from_display?: boolean
  to_floor?: boolean
}): Record<string, number | boolean | null | undefined> {
  if (row.section === 'cash') return { qty_cash: row.qty }
  if (row.section === 'transfer_out') {
    return { box_size: row.box_size, num_boxes: row.qty, loose_qty: row.loose_qty ?? 0, to_floor: !!row.to_floor }
  }
  if (PACKED_SECTIONS.includes(row.section)) {
    // Only 出库入店 and 入店 can go to display instead of the floor.
    const shown = row.section === 'refill' || row.section === 'stock_in'
    return { box_size: row.box_size, num_boxes: row.qty, loose_qty: row.loose_qty ?? 0, ...(shown ? { to_display: !!row.to_display } : {}) }
  }
  if (row.section === 'stock_out') return { qty: row.qty, from_display: !!row.from_display }
  if ((STOCK_LINE_SECTIONS as readonly string[]).includes(row.section)) return { qty: row.qty }
  return { qty_pos: row.qty }
}

export function matchFeedback(
  candidates: { id: number; score: number }[], selectedId: number,
  originalTopId: number | undefined, originalTopScore: number,
) {
  return {
    fuzzy_score: candidates.find(c => c.id === selectedId)?.score ?? 0,
    top_score: originalTopScore,
    was_top: selectedId === originalTopId,
  }
}

export function cashDifference(actual: number | null, expected: number | null): number | null {
  return actual == null || expected == null ? null : Math.round((actual - expected) * 100) / 100
}

// A stock block written twice word for word (owner, 2026-11-15): its repeat is left out unless the manager counts both.
export type RepeatChoice = 'drop' | 'both'

/** Rows read from a repeat are removed while the choice is to drop it, and back when both count. Other rows stay. */
export function applyRepeatChoice<T extends { repeat_id?: string; removed?: boolean }>(rows: T[], repeatId: string, choice: RepeatChoice): T[] {
  return rows.map(row => row.repeat_id === repeatId ? { ...row, removed: choice === 'drop' } : row)
}

/** The rows as first read: every repeat dropped. */
export function dropRepeats<T extends { repeat_id?: string; removed?: boolean }>(rows: T[]): T[] {
  return rows.map(row => row.repeat_id ? { ...row, removed: true } : row)
}

// Goods the other store's 出库 already sent here (2026-11-24): 'sent' reads the row as them (the default),
// 'new' as other new goods, as written.
export type SentChoice = 'sent' | 'new'
type SentRow = { sent_id?: string; sent_fix?: 'refill' | 'drop'; section_was?: string; section?: string; removed?: boolean }

/** A row read as sent goods: an 入店 taken down from upstairs (出库入店), or left out. 'new' puts it back as written. */
export function applySentChoice<T extends SentRow>(rows: T[], sentId: string, choice: SentChoice): T[] {
  return rows.map(row => {
    if (row.sent_id !== sentId) return row
    if (row.sent_fix === 'drop') return { ...row, removed: choice === 'sent' }
    return { ...row, section: choice === 'sent' ? 'refill' : (row.section_was ?? row.section) }
  })
}

/** The rows as first read: what the other store already sent is left out (the server already made 入店 a 出库入店). */
export function dropSent<T extends SentRow>(rows: T[]): T[] {
  return rows.map(row => row.sent_id && row.sent_fix === 'drop' ? { ...row, removed: true } : row)
}
