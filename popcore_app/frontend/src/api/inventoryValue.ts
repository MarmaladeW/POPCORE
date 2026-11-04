import client from './client'
import type { NativeUnit } from './goods'

// 库存价值 by IP line (owner, 2026-11-03); managers and admins only. Money is in cents.
type Units = Partial<Record<NativeUnit, number>>
export interface ValueTotal { units:number; value_cents:number; unpriced_units:number; guessed_units:number }
export interface ValuePlace extends ValueTotal { key:string; codes:string[]; by_unit:Units }
export interface ValueLine extends ValueTotal { name:string; products:number; by_unit:Units; share:number }
export interface ValueMember extends ValueTotal {
  product_id:number; name:string; sku:string|null; stock_form:string|null; unit:NativeUnit|null; price_cents:number|null; guessed:boolean
}
export interface ValueGroup extends ValueTotal {
  name:string; link:{series_id?:number; product_id?:number}; by_unit:Units; share:number; members:ValueMember[]
}
export interface ValueCategory extends ValueTotal {
  name:string; products:number; by_unit:Units; by_place:Array<ValueTotal & {key:string}>; groups:ValueGroup[]
}
export interface InventoryValue {
  live:boolean; stores?:string[]; place?:string; places?:ValuePlace[]; total?:ValueTotal; shown?:ValueTotal
  categories?:ValueLine[]; category?:ValueCategory|null
}
export const UNSORTED = '未分类'
export const getInventoryValue = (storeCode:string, place:string, line:string|null, signal:AbortSignal) =>
  client.get<InventoryValue>('/inventory/value', { params:{ store_code:storeCode, place, ...(line ? { line } : {}) }, signal })
    .then(response => response.data)
