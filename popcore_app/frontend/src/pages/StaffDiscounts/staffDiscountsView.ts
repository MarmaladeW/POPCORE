// 员工折扣 Staff discounts (owner, 2026-11-19): what the page writes about a month, a person and a use record.
import type { DiscountItem, DiscountPerson } from '../../api/staffDiscounts'

export const PAYMENTS: Array<{ code:string; zh:string; en:string }> = [
  { code:'emt', zh:'EMT', en:'' }, { code:'card', zh:'卡机', en:'Card' }, { code:'cash', zh:'现金', en:'Cash' },
  { code:'wechat', zh:'微信', en:'WeChat' }, { code:'alipay', zh:'支付宝', en:'Alipay' },
]

export function shiftMonth(month:string, by:number) {
  const [year, number] = month.split('-').map(Number)
  const date = new Date(Date.UTC(year, number - 1 + by, 1))
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`
}
export const monthTitle = (month:string) => `${month.slice(0, 4)}年${Number(month.slice(5, 7))}月`
export const shortDate = (iso:string) => `${Number(iso.slice(5, 7))}-${Number(iso.slice(8, 10))}`

/** "卡机 Card", or "现金 Cash + 卡机 Card" for a purchase paid two ways. */
export function paymentLabel(code:string|null|undefined) {
  if (!code) return ''
  return code.split('+').map(part => {
    const known = PAYMENTS.find(payment => payment.code === part)
    return known ? [known.zh, known.en].filter(Boolean).join(' ') : part
  }).join(' + ')
}

/** "Angry Molly 迪士尼公主毛绒 ×2 · SA Animal ver2 ×1": every product with its quantity. */
export const itemsText = (items:DiscountItem[]) => items.map(item => `${item.name} ×${item.qty}`).join(' · ')

/** One use is one product: the products a hand-added record counts, the same name (any case) once. */
export function distinctProducts(rows:Array<{ name:string; qty:number|null }>) {
  return new Set(rows.map(row => row.name.trim().toLowerCase().replace(/\s+/g, '')).filter(Boolean)).size
}

export type Standing = { tone:'ok'|'warn'|'out'|'none'; zh:string; en:string }

/** Where a person stands this month: uses left, all used, over 4 (red), or 不适用 for 培训员工 and no rank. */
export function standing(person:Pick<DiscountPerson, 'eligible'|'left'|'over'>): Standing {
  if (!person.eligible) return { tone:'none', zh:'不适用', en:'Not eligible' }
  if (person.over > 0) return { tone:'out', zh:`超出 ${person.over} 次`, en:`Over by ${person.over}` }
  if (!person.left) return { tone:'warn', zh:'用完了', en:'All used' }
  return { tone:'ok', zh:`剩 ${person.left} 次`, en:`${person.left} left` }
}
