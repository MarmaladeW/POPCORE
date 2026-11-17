// 图鉴 Dex step 4: the words the listings panel shows (docs/dex-book-contract.md §7), as plain functions.
import type { DexListingCandidate, DexListingKind, DexListingRow, DexListingState } from '../api/dex'
import { formatCents } from './money.ts'

export const LISTING_KIND: Record<DexListingKind, { zh: string; en: string }> = {
  blind_box: { zh: '盲盒', en: 'Blind box' },
  ordinary: { zh: '普通', en: 'Item' },
  confirmed_design: { zh: '确认款', en: 'Design' },
}

export const LISTING_STATE: Record<DexListingState, string> = {
  approved: '已批准 Approved',
  created: '已建草稿 Draft made',
  completed: '已补选项 Option added',
  exists: '网站已有 Already there',
  refused: '网站拒收 Refused',
  error: '没做成 Not made',
}

// The website's codes (contract §7), with what a manager does about each.
export const LISTING_CODE: Record<string, string> = {
  item_malformed: 'popcore.ca could not read what POPCORE sent for it. Tell whoever looks after POPCORE.',
  book_unknown: 'popcore.ca does not know its book entry. Check the Dex sync has sent it, then retry.',
  id_in_use: 'The ID is on another item on popcore.ca, on two items, or its Single Box and Whole Set sit on two listings. Fix it on the website, then retry.',
  listing_not_variable: 'A simple listing holds the ID, so it cannot take the missing option; popcore.ca never converts one. Fix it on the website by hand.',
  options_unusual: 'The listing’s options are not the usual Single Box / Whole Set. Add the option on the website by hand.',
  listing_failed: 'popcore.ca could not make it and nothing was made. Retry.',
}

export const codeHint = (code: string | null) => (code && LISTING_CODE[code]) || 'popcore.ca did not take it.'

// What will be listed, in one line: the options and prices it starts with, and its stock.
export function listingFacts(item: DexListingCandidate) {
  const parts: string[] = []
  if (item.kind === 'blind_box') {
    parts.push(item.action === 'complete' ? `补 Adds ${item.missing.join(', ')}` : item.set_id ? 'Single Box + Whole Set' : 'Single Box')
  }
  const price = item.price_cents == null
    ? (item.kind === 'confirmed_design' ? 'no design price (the website keeps its own)' : 'no price')
    : formatCents(item.price_cents)
  parts.push(item.set_price_cents ? `${price} · 整端 set ${formatCents(item.set_price_cents)} (${item.boxes_per_set} boxes)` : price)
  parts.push(`库存 stock ${item.stock}${item.set_stock ? ` + ${item.set_stock} sets` : ''}`)
  return parts.join(' · ')
}

export function candidateState(item: DexListingCandidate) {
  if (item.state === 'ready') return '可发送 Ready'
  if (item.state === 'book_waiting') return '等图鉴 Waits for its book entry to reach popcore.ca'
  return '不在图鉴 Not in the book: link it first'
}

// What an approved item waits for.
export function waitText(row: DexListingRow, enabled: boolean) {
  if (row.waits === 'not_in_book') return '不在图鉴 No longer in the book: link it again, or withdraw it'
  if (row.waits === 'book') return '等图鉴 Goes once its book entry is on popcore.ca'
  return enabled ? '下次同步 Goes with the next pass' : '同步关着 Goes once the Dex sync is on'
}
