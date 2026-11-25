import client from './client'
import type { CountRole } from './counting'

// 分区盘点 Section count (popcore_app/count_sections.py): a live store is counted one section every two
// weeks; the manager enters what the counter found on 盘点's cards.
export type SectionKind = 'display' | 'blind_box' | 'design' | 'other'
export interface SectionPart { kind: SectionKind; group_key: string }
export interface SectionGroup { kind: SectionKind; group_key: string; name: string; lines: number; new: boolean }
export interface SectionLastCount { id: number; finished_at: string; business_date: string; counter_name: string }
export interface SectionOpenCount { id: number; started_at: string; counter_name: string }

export interface CountSection {
  id: number
  name: string
  sort_order: number
  lines: number
  kinds: SectionKind[]
  groups: SectionGroup[]
  // The kinds whose new products (an IP no section names yet) land here.
  takes_new: SectionKind[]
  uneven: boolean
  last_count: SectionLastCount | null
  open_count: SectionOpenCount | null
}

export interface NextUp { section_id: number; name: string; last_counted: string | null; due_on: string; overdue: boolean }

export interface SectionsOverview {
  store: { id: number; code: string; name: string }
  version: number
  sections: CountSection[]
  lines: number
  outside: number
  next_up: NextUp | null
  cycle_days: number
  // Every (kind, group) with stock now, biggest first.
  groups: Array<{ kind: SectionKind; group_key: string; name: string; lines: number }>
  today: string
  // Open 待盘点 count-next-time marks of the store: the next section count that starts lists them first.
  marks: number
}

export interface ProposedSection { name: string; parts: SectionPart[]; lines: number; groups: string[] }
export interface SectionsProposal { store: { id: number; code: string; name: string }; lines: number; sections: ProposedSection[] }

export interface SectionCountCard {
  product_id: number
  title: string
  sku: string | null
  image: string | null
  lines: number
  counted: number
  added: boolean
  // The card items this section enters here; null: all of them (on display).
  roles: CountRole[] | null
  // 待盘点: items of this card a manager marked to count here, also from another section (listed first).
  marks: SectionCardMark[]
}
export interface SectionCardMark { product_id: number; name: string; reason: string; counted: boolean }
export interface SectionCountPlace { id: number; code: string; name: string; cards: SectionCountCard[] }

export interface SectionCountView {
  id: number
  store: { id: number; code: string; name: string }
  section: { id: number; name: string }
  counter: string
  business_date: string
  started_at: string
  started_by: string
  finished_at: string | null
  finished_by: string | null
  places: SectionCountPlace[]
  lines: number
  counted: number
}

export interface SummaryRow {
  product_id: number
  card_product_id: number
  sku: string | null
  name: string
  place: string
  place_name: string
  added: boolean
  // A 待盘点 count-next-time line.
  marked: boolean
  price: number | null
  expected: number
  counted: number | null
  difference: number | null
  value: number | null
  entered_by?: string
  entered_at?: string
}

export interface SectionCountSummary extends Omit<SectionCountView, 'places'> {
  more: number
  less: number
  value: number
  rows: SummaryRow[]
}

export interface SectionCountHistoryRow {
  id: number; section_id: number; section_name: string; counter_name: string; business_date: string
  started_at: string; finished_at: string | null; lines: number
}

// here: works at this store (its staff come first).
export interface Employee { employee_id: number; name: string; here: boolean }

export const getSections = (store: string, signal?: AbortSignal) =>
  client.get<SectionsOverview>('/counting/sections', { params: { store_code: store }, signal }).then(response => response.data)

export const getSectionsProposal = (store: string, signal?: AbortSignal) =>
  client.get<SectionsProposal>('/counting/sections/proposal', { params: { store_code: store }, signal })
    .then(response => response.data)

export const saveSections = (store: string, version: number, sections: Array<{ id?: number; name: string; parts: SectionPart[] }>) =>
  client.put<SectionsOverview>('/counting/sections', { store_code: store, version, sections }).then(response => response.data)

export const getSectionCountHistory = (store: string, signal?: AbortSignal) =>
  client.get<{ counts: SectionCountHistoryRow[] }>('/counting/section-counts', { params: { store_code: store }, signal })
    .then(response => response.data.counts)

export const startSectionCount = (store: string, sectionId: number, counterEmployeeId: number) =>
  client.post<SectionCountView>('/counting/section-counts',
    { store_code: store, section_id: sectionId, counter_employee_id: counterEmployeeId }).then(response => response.data)

export const getSectionCount = (id: number, signal?: AbortSignal) =>
  client.get<SectionCountView>(`/counting/section-counts/${id}`, { signal }).then(response => response.data)

export const getSectionCountSummary = (id: number, signal?: AbortSignal) =>
  client.get<SectionCountSummary>(`/counting/section-counts/${id}/summary`, { signal }).then(response => response.data)

export const finishSectionCount = (id: number) =>
  client.post<SectionCountSummary>(`/counting/section-counts/${id}/finish`).then(response => response.data)

// Active staff, this store's first, for "who is counting".
export const getCounters = (store: string, signal?: AbortSignal) =>
  client.get<{ employees: Employee[] }>('/counting/counters', { params: { store_code: store }, signal })
    .then(response => response.data.employees)
