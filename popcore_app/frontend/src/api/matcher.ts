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
const _HEADER_RE = /^(卡机|现金|转账|合计|汇总|小计|总计|入店|出店|在店|随手记|pos).*[：:]\s*$/i
// Matches date strings like 2024/3/10, 2024-3-10, 2024年3月10日, 3月10日, 3/10
const _DATE_RE = /^\d{1,4}[年\/\-]\d{1,2}([月\/\-]\d{1,2}[日号]?)?[日号]?\s*[：:]*\s*$/

export function cleanName(raw: string): string {
  return raw.trim().replace(_TRAILING_PUNCT, '').trim()
}

export function isHeaderLine(raw: string): boolean {
  const s = cleanName(raw)
  if (!s) return true
  if (s.endsWith(':') || s.endsWith('：')) return true
  if (_DATE_RE.test(raw.trim())) return true
  return _HEADER_RE.test(raw.trim())
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
