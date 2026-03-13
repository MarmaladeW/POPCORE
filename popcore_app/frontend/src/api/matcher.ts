import client from './client'

export interface MatchCandidate {
  id: number
  sku: string
  jizhanming: string
  name_cn_en: string
  price: number
  ip_series: string
  product_type: string
}

export interface MatchResult {
  query: string
  status: 'matched' | 'fuzzy' | 'unmatched' | 'skipped'
  candidates: MatchCandidate[]
}

const _TRAILING_PUNCT = /[*＊:：、。！!～~]+$/
// Matches date strings like 2024/3/10, 2024-3-10, 2024年3月10日, 3月10日, 3/10
const _DATE_RE = /^\d{1,4}[年\/\-]\d{1,2}([月\/\-]\d{1,2}[日号]?)?[日号]?\s*[：:]*\s*$/

export type SectionType = 'pos' | 'cash' | 'stock_in' | 'stock_out' | 'claw' | 'ignore'

// Whitelist of known section headers (substring match, longer/more-specific first)
const SECTION_WHITELIST: Array<{ keyword: string; section: SectionType }> = [
  { keyword: '卡机汇总',  section: 'pos'      },
  { keyword: '随手记汇总', section: 'cash'     },
  { keyword: '随手记',    section: 'cash'     },
  { keyword: '入店',      section: 'stock_in' },
  { keyword: '出店',      section: 'stock_out'},
  { keyword: '娃娃机',    section: 'claw'     },
  { keyword: '卖display', section: 'ignore'   },
  { keyword: '拆display', section: 'ignore'   },
  { keyword: '员工折扣',  section: 'ignore'   },
  { keyword: '晚盘',      section: 'ignore'   },
  { keyword: '博主探店',  section: 'ignore'   },
  { keyword: '现金',      section: 'ignore'   },
]

/**
 * Returns the section type if the line is a known section header, or null if
 * it looks like a product line. Uses substring containment — no colon required.
 */
export function detectSalesSection(line: string): SectionType | null {
  const lower = line.toLowerCase()
  for (const { keyword, section } of SECTION_WHITELIST) {
    if (lower.includes(keyword.toLowerCase())) return section
  }
  return null
}

export function cleanName(raw: string): string {
  return raw.trim().replace(_TRAILING_PUNCT, '').trim()
}

export function isHeaderLine(raw: string): boolean {
  const s = cleanName(raw)
  if (!s) return true
  if (s.endsWith(':') || s.endsWith('：')) return true
  if (_DATE_RE.test(raw.trim())) return true
  return detectSalesSection(raw) !== null
}

export async function batchMatch(
  queries: string[],
  threshold = 75,
): Promise<MatchResult[]> {
  const r = await client.post('/products/match', { queries, threshold })
  return r.data.results as MatchResult[]
}

export async function saveAlias(productId: number, alias: string): Promise<void> {
  await client.post('/products/aliases', { product_id: productId, alias })
}

export async function deleteAlias(productId: number, aliasId: number): Promise<void> {
  await client.delete(`/products/${productId}/aliases/${aliasId}`)
}
