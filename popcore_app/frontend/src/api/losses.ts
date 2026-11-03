import client from './client'
import type { NativeUnit } from './goods'

// 损耗 Loss (owner, 2026-11-03): managers and admins record broken, stolen or missing stock. Money is in cents.
export type LossReason = 'broken' | 'stolen' | 'missing' | 'other'
export interface LossLocation { id:number; code:string; name:string }
export interface LossRecord {
  id:number; business_date:string; created_at:string; product_id:number; name:string; sku:string|null
  unit:NativeUnit; quantity:number; reason:LossReason; note:string|null
  location_id:number; location_code:string; location_name:string
  unit_price_cents:number|null; value_cents:number|null
  actor_name:string; undone:boolean; undone_at:string|null; undone_by_name:string|null
}
export interface LossTotal { units:number; value_cents:number; unpriced_units:number; records:number }
export interface DamagedItem {
  product_id:number; name:string; sku:string|null; unit:NativeUnit; location_id:number; location_code:string
  location_name:string; quantity:number; version:number; price_cents:number|null; value_cents:number|null
}
export interface LossPage {
  live:boolean; store:{id:number; code:string; name:string; live:boolean}; month:string; today?:string
  locations?:LossLocation[]; records?:LossRecord[]; totals?:Record<LossReason,LossTotal>
  units?:number; value_cents?:number; unpriced_units?:number; pile?:DamagedItem[]
}
export interface LossPlace { location_id:number; code:string; name:string; available:number; version:number }
export interface LossProduct {
  product_id:number; name:string; sku:string|null; unit:NativeUnit; stock_form:string; price_cents:number|null; places:LossPlace[]
}

export const getLossPage = (storeCode:string, month:string, signal?:AbortSignal) =>
  client.get<LossPage>('/losses', { params:{ store_code:storeCode, month }, signal }).then(response=>response.data)
export const searchLossProducts = (storeCode:string, q:string, signal?:AbortSignal) =>
  client.get<{results:LossProduct[]}>('/losses/search', { params:{ store_code:storeCode, q }, signal }).then(response=>response.data.results)
