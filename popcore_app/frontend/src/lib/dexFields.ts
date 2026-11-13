// 图鉴 Dex labels and the refined copy's fields (docs/dex-book-contract.md §4), as plain inputs.
// market_prices is kept as the load gives it and not edited here.
import type { DexExtras, DexKind, DexRarity } from '../api/dex'

export const KIND_LABEL: Record<DexKind, { zh: string; en: string }> = {
  company: { zh: '公司', en: 'Company' },
  partner: { zh: '联名', en: 'Partner' },
  ip: { zh: 'IP', en: 'IP' },
  character: { zh: '角色', en: 'Character' },
  series: { zh: '系列', en: 'Series' },
  product: { zh: '产品', en: 'Product' },
  design: { zh: '款', en: 'Design' },
}
export const kindText = (kind: DexKind) => KIND_LABEL[kind].zh === KIND_LABEL[kind].en
  ? KIND_LABEL[kind].en : `${KIND_LABEL[kind].zh} ${KIND_LABEL[kind].en}`

export const RARITIES: { value: DexRarity; label: string }[] = [
  { value: 'normal', label: '普通 Normal' },
  { value: 'popular', label: '热门 Popular' },
  { value: 'secret', label: '隐藏 Secret' },
  { value: 'super_secret', label: '超隐 Super secret' },
  { value: 'special', label: '特别 Special' },
]

export type ExtraInput = 'text' | 'long' | 'number' | 'list' | 'yesno'
export interface ExtraField { key: string; label: string; input: ExtraInput }

const f = (key: string, label: string, input: ExtraInput = 'text'): ExtraField => ({ key, label, input })
export const EXTRA_FIELDS: Record<DexKind, ExtraField[]> = {
  company: [],
  partner: [],
  ip: [f('description', '介绍 Description', 'long'), f('aliases', '别名 Other names', 'list'), f('is_original', '原创 Original IP', 'yesno')],
  character: [f('description', '介绍 Description', 'long'), f('aliases', '别名 Other names', 'list')],
  series: [f('release_date', '发售日期 Release date'), f('flavour_text', '文案 Flavour text', 'long'),
    f('description', '介绍 Description', 'long'), f('aliases', '别名 Other names', 'list'), f('tags', '标签 Tags', 'list'),
    f('designer', '设计师 Designer')],
  product: [f('format', '形式 Format'), f('release_label', '发售 Release label'), f('description', '介绍 Description', 'long'),
    f('retail_price', '零售价 Retail price', 'number'), f('retail_price_box', '整端零售价 Retail price, box', 'number'),
    f('retail_currency', '币种 Currency'), f('variants_normal', '普通款数 Normal designs', 'number'),
    f('variants_secret', '隐藏款数 Secret designs', 'number'), f('secret_odds', '隐藏概率 Secret odds'),
    f('parent_category', '分组 Group on the IP page')],
  design: [f('weight', '重量 Weight (g)'), f('weight_range', '重量范围 Weight range'),
    f('shake_level', '摇盒 Shake level'), f('shake_note', '摇盒说明 Shake note', 'long'), f('flavour_text', '文案 Flavour text', 'long')],
}

type FormValue = string | number | boolean | null | undefined

// A stored value as its input shows it.
export function toInput(field: ExtraField, value: unknown): FormValue {
  if (value === null || value === undefined) return field.input === 'yesno' ? undefined : ''
  if (field.input === 'list') return Array.isArray(value) ? value.join(', ') : String(value)
  if (field.input === 'number') return typeof value === 'number' ? value : Number(value)
  if (field.input === 'yesno') return Boolean(value)
  return String(value)
}

// An input's value as it is stored: blank is null, a list is split on commas.
export function fromInput(field: ExtraField, value: FormValue): unknown {
  if (value === undefined || value === null) return null
  if (field.input === 'yesno') return Boolean(value)
  if (field.input === 'number') return value === '' ? null : Number(value)
  const text = String(value).trim()
  if (!text) return null
  if (field.input === 'list') {
    const parts = text.split(/[,，、]/).map(part => part.trim()).filter(Boolean)
    return parts.length ? parts : null
  }
  return text
}

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null)

// Only the keys whose value changed, so an untouched key is never sent (a key sent with null clears it).
export function extrasChange(kind: DexKind, before: DexExtras, values: Record<string, FormValue>): DexExtras {
  const change: DexExtras = {}
  for (const field of EXTRA_FIELDS[kind]) {
    const next = fromInput(field, values[field.key])
    if (!same(next, before[field.key])) change[field.key] = next
  }
  return change
}
