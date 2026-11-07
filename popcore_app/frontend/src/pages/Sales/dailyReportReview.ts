import type { ReportAnnotations } from '../../api/matcher.ts'

export const REPORT_NOTE_SECTIONS = [
  { key: 'employee_discounts', label: '员工折扣 / Employee discounts', description: '已计入销售汇总，仅保留备注，不再增加数量。' },
  { key: 'display_sales', label: '卖Display / Display sales', description: '已计入销售汇总，不再增加销售数量或收款。' },
  { key: 'claw_prizes', label: '娃娃机出奖 / Claw prizes out', description: '独立奖品库存出库记录，不是销售或收款；仅保存原文，不更改库存。' },
  { key: 'cash_exchanges', label: 'EMT 换现金 / Cash exchanges', description: '收到 EMT 后付出等额现金，不是销售。*2 不代表已知金额，不据此计算现金差额。' },
  { key: 'claw_stock_in', label: '娃娃机入库 / Claw stock in', description: '奖品入库记录；仅保存原文，不更改库存。补货请在 娃娃机 → 补货 Refill 记录。' },
  { key: 'display_stock_in', label: '已拆 Display 入库 / Opened display stock in', description: '已拆展示品入库记录；仅保存原文，不增加未拆封库存。' },
  { key: 'display_stock_out', label: '已拆 Display 出库 / Opened display stock out', description: '已拆展示品出库记录；仅保存原文，不扣减未拆封库存。' },
  { key: 'store_transfers', label: '出库 / To the other store', description: '仅保存原文，日报不移库存。请用 调拨 Transfer 记录送去另一家店的货。' },
] as const

// A live store's stock lines (owner, 2026-11-03), what each one moves, and the ones written as sets.
export const STOCK_LINE_SECTIONS = ['refill', 'stock_in', 'stock_out', 'display_sold', 'break_display', 'display_stock_in', 'claw_prize'] as const
export const PACKED_SECTIONS = ['stock_in', 'refill']
const LEGACY_SECTIONS = ['pos', 'cash', 'stock_in', 'stock_out', 'break_display']

export function stockLineEffect(row: { section: string; to_display?: boolean; from_display?: boolean }): string | null {
  switch (row.section) {
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
}): Record<string, number | boolean | null | undefined> {
  if (row.section === 'cash') return { qty_cash: row.qty }
  if (PACKED_SECTIONS.includes(row.section)) {
    return { box_size: row.box_size, num_boxes: row.qty, loose_qty: row.loose_qty ?? 0, to_display: !!row.to_display }
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
