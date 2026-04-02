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

export interface SectionAlias {
  id: number
  alias_norm: string
  section_type: SectionType
  created_at: string
}

const _TRAILING_PUNCT = /[*＊:：、。！!～~]+$/
// Matches date strings like 2024/3/10, 2024-3-10, 2024年3月10日, 3月10日, 3/10
const _DATE_RE = /^\d{1,4}[年\/\-]\d{1,2}([月\/\-]\d{1,2}[日号]?)?[日号]?\s*[：:]*\s*$/

export type SectionType =
  | 'pos'               // 卡机汇总
  | 'cash'              // 随手记汇总
  | 'stock_in'          // 入店
  | 'stock_out'         // 出店
  | 'claw'              // 娃娃机汇总
  | 'sell_display'      // 卖display
  | 'break_display'     // 拆display
  | 'employee_discount' // 员工折扣
  | 'ignore'            // known non-product sections (晚盘, 博主探店, etc.)
  | 'unknown'           // unrecognized — requires user classification

// Whitelist of known section headers (substring match, longer/more-specific first)
const SECTION_WHITELIST: Array<{ keyword: string; section: SectionType }> = [
  { keyword: '卡机汇总',    section: 'pos'               },
  { keyword: '卡机',        section: 'pos'               },
  { keyword: '随手记汇总',  section: 'cash'              },
  { keyword: '随手记',      section: 'cash'              },
  { keyword: '入店',        section: 'stock_in'          },
  { keyword: '出店',        section: 'stock_out'         },
  { keyword: '娃娃机',      section: 'claw'              },
  { keyword: '卖display',   section: 'sell_display'      },
  { keyword: '卖Display',   section: 'sell_display'      },
  { keyword: '拆display',   section: 'break_display'     },
  { keyword: '拆Display',   section: 'break_display'     },
  { keyword: '员工折扣',    section: 'employee_discount' },
  { keyword: '晚盘',        section: 'ignore'            },
  { keyword: '博主探店',    section: 'ignore'            },
  { keyword: '现金汇总',    section: 'ignore'            },
]

/** Normalize a string for section alias lookup: remove all whitespace, lowercase */
function normSection(s: string): string {
  return s.replace(/\s+/g, '').toLowerCase()
}

/**
 * Detect section type from a line using:
 * 1. Hard-coded keyword whitelist (Tier 1)
 * 2. User-saved section aliases (Tier 2)
 * Returns null if the line is not a section header at all.
 */
export function detectSalesSection(
  line: string,
  savedAliases: SectionAlias[] = [],
): SectionType | null {
  const lower = line.toLowerCase()

  // Tier 1: built-in whitelist
  for (const { keyword, section } of SECTION_WHITELIST) {
    if (lower.includes(keyword.toLowerCase())) return section
  }

  // Tier 2: user-defined aliases (normalize and compare)
  const lineNorm = normSection(line)
  for (const a of savedAliases) {
    if (lineNorm.includes(a.alias_norm) || a.alias_norm.includes(lineNorm)) {
      return a.section_type
    }
  }

  return null
}

/**
 * Determine if a line is a section header or should be skipped entirely.
 * Returns the section type if it IS a section header, null if it's a product line.
 * 'unknown' means it looks like a header but isn't recognized.
 */
export function detectSectionHeader(
  line: string,
  savedAliases: SectionAlias[] = [],
): SectionType | null {
  const s = cleanName(line)
  if (!s) return 'ignore'
  if (_DATE_RE.test(line.trim())) return 'ignore'

  // Must end with colon (full or half-width) OR contain a known keyword to be considered a header
  const looksLikeHeader = s.endsWith(':') || s.endsWith('：')
  const knownSection = detectSalesSection(line, savedAliases)

  if (knownSection !== null) return knownSection
  if (looksLikeHeader) return 'unknown'
  return null
}

export function cleanName(raw: string): string {
  return raw.trim().replace(_TRAILING_PUNCT, '').trim()
}

export function isHeaderLine(raw: string, savedAliases: SectionAlias[] = []): boolean {
  const s = cleanName(raw)
  if (!s) return true
  if (s.endsWith(':') || s.endsWith('：')) return true
  if (_DATE_RE.test(raw.trim())) return true
  return detectSalesSection(raw, savedAliases) !== null
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

export async function getSectionAliases(): Promise<SectionAlias[]> {
  const r = await client.get('/section-aliases')
  return r.data as SectionAlias[]
}

export async function saveSectionAlias(alias: string, sectionType: SectionType): Promise<void> {
  await client.post('/section-aliases', { alias, section_type: sectionType })
}

export async function deleteSectionAlias(aliasId: number): Promise<void> {
  await client.delete(`/section-aliases/${aliasId}`)
}
