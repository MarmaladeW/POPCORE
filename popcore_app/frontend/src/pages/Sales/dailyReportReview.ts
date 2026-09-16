import type { ReportAnnotations } from '../../api/matcher.ts'

export const REPORT_NOTE_SECTIONS = [
  { key: 'employee_discounts', label: '员工折扣 / Employee discounts', description: '已计入销售汇总，仅保留备注，不再增加数量。' },
  { key: 'display_sales', label: '卖Display / Display sales', description: '已计入销售汇总，不再增加销售数量或收款。' },
  { key: 'claw_prizes', label: '娃娃机出奖 / Claw prizes out', description: '独立奖品库存出库记录，不是销售或收款；仅保存原文，不更改库存。' },
  { key: 'cash_exchanges', label: 'EMT 换现金 / Cash exchanges', description: '收到 EMT 后付出等额现金，不是销售。*2 不代表已知金额，不据此计算现金差额。' },
  { key: 'claw_stock_in', label: '娃娃机入库 / Claw stock in', description: '奖品入库记录；仅保存原文，不更改奖品或未拆封库存。' },
  { key: 'display_stock_in', label: '已拆 Display 入库 / Opened display stock in', description: '已拆展示品入库记录；仅保存原文，不增加未拆封库存。' },
  { key: 'display_stock_out', label: '已拆 Display 出库 / Opened display stock out', description: '已拆展示品出库记录；仅保存原文，不扣减未拆封库存。' },
] as const

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
  return positiveInteger(row.qty) && (row.section !== 'stock_in' || (
    positiveInteger(row.box_size) && Number.isSafeInteger(loose) && loose >= 0
    && Number.isSafeInteger(row.qty * row.box_size! + loose)
  ))
}

export function isReportRowReady(row: {
  section: string; qty: number; box_size: number | null; loose_qty?: number; flagged: boolean
  product?: { id: number }; accepted?: boolean; unknown_header?: string | null
}): boolean {
  return !!row.product?.id && row.accepted !== false && !row.flagged && !row.unknown_header
    && ['pos', 'cash', 'stock_in', 'stock_out', 'break_display'].includes(row.section)
    && validReportQuantity(row)
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
