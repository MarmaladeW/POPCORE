import client from './client'

// 员工折扣 Staff discounts (owner, 2026-11-19): 正式员工 and above have 4 uses a month; one use is one product.
// Money is in cents and only reaches managers. Staff get only their own row and records (owner, 2026-11-26).
export type DiscountState = 'counted' | 'waiting' | 'ignored'
export interface DiscountItem { name:string; qty:number }
export interface DiscountUse {
  id:number; date:string; store:string; store_name:string|null; source:'report'|'manual'
  employee_id:number|null; name:string|null; rank:string|null; written_name:string|null
  items:DiscountItem[]; uses:number; payment:string|null; readable:boolean; state:DiscountState; waiting_for:'person'|'reading'|null
  ignored_reason:string|null; edited:boolean; edited_by_name:string|null; created_by_name:string|null
  amount_cents?:number|null; raw_text?:string|null
}
export interface DiscountPerson {
  employee_id:number; name:string; rank:string|null; rank_label:string|null; eligible:boolean; active:boolean
  used:number; left:number|null; over:number
}
export interface DiscountStoreTotal { store:string; name:string|null; amount_cents:number; records:number; no_amount:number }
export interface DiscountPage {
  month:string; this_month:string; today:string; monthly_uses:number; can_manage:boolean
  people:DiscountPerson[]; uses:DiscountUse[]; waiting:DiscountUse[]; ignored:DiscountUse[]
  totals?:{ stores:DiscountStoreTotal[]; amount_cents:number; records:number; no_amount:number }
  stores?:Array<{ code:string; name:string }>
  employees?:Array<{ employee_id:number; name:string; rank:string|null; rank_label:string|null }>
}

export const getStaffDiscounts = (month:string, signal?:AbortSignal) =>
  client.get<DiscountPage>('/staff-discounts', { params:{ month }, signal }).then(response => response.data)

// Who can buy with a staff discount, for the 记一笔 "谁买的" picker: names only, for everyone on shift.
export interface DiscountBuyer { employee_id:number; name:string }
export const getDiscountBuyers = (signal?:AbortSignal) =>
  client.get<{ buyers:DiscountBuyer[] }>('/staff-discounts/buyers', { signal }).then(response => response.data.buyers ?? [])
