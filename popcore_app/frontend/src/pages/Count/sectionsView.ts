// 分区盘点 Section count: what the screens show, kept apart so it can be tested without a browser.
import type {
  CountSection, NextUp, SectionCountView, SectionKind, SectionPart, SectionsProposal,
} from '../../api/countSections'
import type { CountRole } from '../../api/counting'

export const PLACES: Record<string, string> = {
  floor: '店内 Floor', upstairs: '楼上 Upstairs', warehouse: '仓库 Warehouse', claw: '娃娃机 Claw', display: '陈列 Display',
}
export const placeName = (code: string, name?: string) => PLACES[code] || name || code

export const KINDS: SectionKind[] = ['display', 'blind_box', 'design', 'other']
export const KIND_NAMES: Record<SectionKind, string> = {
  display: '展示 Display', blind_box: '盲盒 Blind boxes', design: '确认款 Confirmed designs', other: '其他 Other products',
}

const DAY = 86_400_000
const dayNumber = (iso: string) => Math.round(Date.parse(`${iso}T00:00:00Z`) / DAY)
export const linesText = (count: number) => `${count} line${count === 1 ? '' : 's'}`
export const shortDay = (iso: string) => { const [, month, day] = iso.split('-'); return `${Number(month)}/${Number(day)}` }

// The section counted longest ago comes first; never counted is longest ago of all.
export function orderSections(sections: CountSection[]) {
  return [...sections].sort((a, b) =>
    (a.last_count?.business_date || '').localeCompare(b.last_count?.business_date || '') || a.sort_order - b.sort_order || a.id - b.id)
}

export function dueLabel(next: NextUp, today: string) {
  const days = dayNumber(next.due_on) - dayNumber(today)
  if (days < 0) return `逾期 ${-days} 天 · Overdue ${-days} day${days === -1 ? '' : 's'}`
  if (days === 0) return '今天到期 · Due today'
  return `${shortDay(next.due_on)} 到期 · Due in ${days} day${days === 1 ? '' : 's'}`
}

export function lastCounted(section: CountSection) {
  const last = section.last_count
  return last ? `上次 ${shortDay(last.business_date)} · ${last.counter_name}` : '还没点过 · Not counted yet'
}

// A few of a section's groups, biggest first, for a one-line description.
export function groupsLine(section: CountSection, shown = 4) {
  const names = section.groups.map(group => group.name)
  const unique = names.filter((name, index) => names.indexOf(name) === index)
  return unique.slice(0, shown).join(' · ') + (unique.length > shown ? ` +${unique.length - shown}` : '')
}

// The next card still to count after the one just counted, in list order; then any before it.
export function nextCard(view: SectionCountView, placeId: number | null, productId: number | null) {
  const cards = view.places.flatMap(place => place.cards.map(card => ({ place, card })))
  const at = cards.findIndex(entry => entry.place.id === placeId && entry.card.product_id === productId)
  const after = [...cards.slice(at + 1), ...cards.slice(0, Math.max(at, 0))]
  return after.find(entry => entry.card.counted < entry.card.lines && !(entry.place.id === placeId && entry.card.product_id === productId)) ?? null
}

// A section enters only its own items of a card (盲盒: boxes and whole sets; 确认款: the designs).
export function onlyRoles<T extends { role: CountRole }>(items: T[], roles: CountRole[] | null | undefined) {
  return roles ? items.filter(item => roles.includes(item.role)) : items
}

// The layout as the editor holds it: each section's parts, ids kept so its counts stay with it.
export interface DraftSection { id?: number; name: string; parts: SectionPart[] }

const STAR = '*'
const same = (a: SectionPart, b: SectionPart) => a.kind === b.kind && a.group_key === b.group_key

export function draftFrom(sections: CountSection[]): DraftSection[] {
  // A kind kept in one section needs no parts but its '*': new IPs of it simply land there too.
  const homes = new Map<SectionKind, Set<number>>()
  sections.forEach((section, index) => {
    section.groups.forEach(group => { if (!homes.has(group.kind)) homes.set(group.kind, new Set()); homes.get(group.kind)!.add(index) })
    section.takes_new.forEach(kind => { if (!homes.has(kind)) homes.set(kind, new Set()); homes.get(kind)!.add(index) })
  })
  return sections.map(section => ({
    id: section.id, name: section.name,
    parts: [
      ...section.groups.filter(group => (homes.get(group.kind)?.size ?? 0) > 1)
        .map(group => ({ kind: group.kind, group_key: group.group_key })),
      ...section.takes_new.map(kind => ({ kind, group_key: STAR })),
    ],
  }))
}

// A fresh proposal, reusing the ids of sections with the same name so their last counts stay.
export function draftFromProposal(proposal: SectionsProposal, current: CountSection[]): DraftSection[] {
  const ids = new Map(current.map(section => [section.name, section.id]))
  return proposal.sections.map(section => ({ id: ids.get(section.name), name: section.name, parts: section.parts }))
}

// Move one group (of one kind) to another section: it gets its own part there. The other groups of
// its kind keep landing where they did, through their own parts or their kind's '*'.
export function moveGroup(draft: DraftSection[], part: SectionPart, to: number): DraftSection[] {
  return draft.map((section, index) => {
    const rest = section.parts.filter(existing => !same(existing, part))
    return index === to ? { ...section, parts: [...rest, part] } : { ...section, parts: rest }
  })
}

// Which section takes the new products of a kind.
export function takesNew(draft: DraftSection[], kind: SectionKind) {
  return draft.findIndex(section => section.parts.some(part => part.kind === kind && part.group_key === STAR))
}

export function setTakesNew(draft: DraftSection[], kind: SectionKind, to: number): DraftSection[] {
  const star = { kind, group_key: STAR }
  return draft.map((section, index) => {
    const rest = section.parts.filter(part => !same(part, star))
    return index === to ? { ...section, parts: [...rest, star] } : { ...section, parts: rest }
  })
}

// Where a group sits in the draft: its own part, else the section taking its kind's new products.
export function sectionOfGroup(draft: DraftSection[], part: SectionPart) {
  const own = draft.findIndex(section => section.parts.some(existing => same(existing, part)))
  return own >= 0 ? own : takesNew(draft, part.kind)
}
