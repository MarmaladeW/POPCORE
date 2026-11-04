import type { NameStatus, PastedDay, PastedName, PickPlace } from '../../api/namePicks.ts'

// Every status label is three characters wide so a list's labels line up (redesign alignment rule).
export const STATUS_TEXT: Record<NameStatus, { zh: string; en: string; tone: 'warn' | 'ok' | 'out' | 'quiet' }> = {
  open:       { zh: '待选择', en: 'To pick',          tone: 'warn' },
  disputed:   { zh: '有冲突', en: 'Two products',     tone: 'out' },
  stale:      { zh: '已改动', en: 'Product changed',  tone: 'warn' },
  confirm:    { zh: '再确认', en: 'Confirm here',     tone: 'warn' },
  remembered: { zh: '已记住', en: 'Remembered',       tone: 'ok' },
  ignored:    { zh: '已忽略', en: 'Ignored',          tone: 'quiet' },
}

export const SECTION_TEXT: Record<string, string> = {
  pos: '卡机 POS', cash: '随手记 Non-POS', refill: '出库入店', stock_in: '入店', stock_out: '出店',
  display_sold: '卖Display', break_display: '拆Display', display_stock_in: '入Display', claw_prize: '娃娃机出奖',
}

/** A name the next paste would still ask about: what this page is for. */
export const needsAnswer = (status: NameStatus) => status === 'open' || status === 'disputed' || status === 'stale' || status === 'confirm'

export function dayCounts(day: PastedDay) {
  const { open, disputed, stale, confirm, remembered, ignored } = day.counts
  return { toAnswer: open + disputed + stale + confirm, remembered, ignored }
}

/** The day to open first: the newest one that still has names to answer, else the newest. */
export function firstDay(days: PastedDay[]): PastedDay | undefined {
  return days.find(day => dayCounts(day).toAnswer > 0) ?? days[0]
}

/** The name as the report wrote it: "SA kiss（秘密）". */
export const writtenName = (name: { raw_name: string; note: string }) => name.note ? `${name.raw_name}（${name.note}）` : name.raw_name

/** How much the line said, in its own form: "× 2", or sets "12 × 2 + 1". */
export function quantityText(name: Pick<PastedName, 'section' | 'qty' | 'box_size' | 'loose_qty'>): string {
  if (!name.qty) return ''
  if ((name.section === 'refill' || name.section === 'stock_in') && (name.box_size ?? 1) > 1) {
    return `${name.box_size} × ${name.qty}${name.loose_qty ? ` + ${name.loose_qty}` : ''}`
  }
  return `× ${name.qty + (name.loose_qty ?? 0)}`
}

export const placeText = (place: PickPlace) => `${place.store_code} ${SECTION_TEXT[place.section] ?? place.section}${place.display ? ' · Display' : ''}`

/** A day's names under the report's own section headings, in paste order. */
export function bySection(names: PastedName[]): { section: string; names: PastedName[] }[] {
  const groups: { section: string; names: PastedName[] }[] = []
  for (const name of names) {
    const group = groups.find(entry => entry.section === name.section)
    if (group) group.names.push(name)
    else groups.push({ section: name.section, names: [name] })
  }
  return groups
}
