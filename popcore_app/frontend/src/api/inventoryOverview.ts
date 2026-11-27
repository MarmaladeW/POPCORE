import client from './client'
import type { NativeUnit } from './goods'

export interface OverviewFigure {
  key:string; codes:string[]; units:number; products:number; by_unit:Partial<Record<NativeUnit,number>>
  // This place's retail value in cents; admins only (null for anyone else).
  value_cents?:number|null; unpriced_units?:number|null
}
export interface OverviewItem {
  product_id:number; store_code:string; name:string; sku:string; unit:NativeUnit|null
  link:{series_id?:number; product_id?:number}; counted:boolean
  floor:number; back:number; back_sets:number; set_size:number|null
  // How it sells: admins only (owner, 2026-11-26), null for anyone else.
  sold_week:number|null; sold_recent:number|null; last_sold:string|null; pace:number|null; days_left:number|null
}
export interface OverviewChange { business_date:string; kind:string; store_code:string; added:number; removed:number; saleable:number; documents:number }
// Retail value of the saleable stock in the figures, in cents; admins only (null for anyone else).
export interface OverviewValue { value_cents:number; unpriced_units:number; unpriced_products:number }
export interface InventoryOverview {
  live:boolean; stores?:string[]; today?:string
  // report_days is null (not 0) when the account does not see sales figures: then there is no best-seller list either.
  figures?:OverviewFigure[]; in_stock_products?:number; report_days?:number|null; last_report?:string|null
  refill?:OverviewItem[]; refill_count?:number; running_low?:OverviewItem[]; sold_out?:OverviewItem[]; best_sellers?:OverviewItem[]
  recent_changes?:OverviewChange[]; not_set_up?:number; value?:OverviewValue|null
}
export const getInventoryOverview = (storeCode:string, signal:AbortSignal) =>
  client.get<InventoryOverview>('/inventory/overview', {params:{store_code:storeCode},signal}).then(response=>response.data)
