import client from './client'
import type { NativeUnit } from './goods'

export interface OverviewFigure { key:string; codes:string[]; units:number; products:number; by_unit:Partial<Record<NativeUnit,number>> }
export interface OverviewItem {
  product_id:number; store_code:string; name:string; sku:string; unit:NativeUnit|null
  link:{series_id?:number; product_id?:number}; counted:boolean
  floor:number; back:number; back_sets:number; set_size:number|null
  sold_week:number; sold_recent:number; last_sold:string|null; pace:number; days_left:number|null
}
export interface OverviewChange { business_date:string; kind:string; store_code:string; added:number; removed:number; saleable:number; documents:number }
// Retail value of the saleable stock in the figures, in cents; managers and admins only (null for anyone else).
export interface OverviewValue { value_cents:number; unpriced_units:number; unpriced_products:number }
export interface InventoryOverview {
  live:boolean; stores?:string[]; today?:string
  figures?:OverviewFigure[]; in_stock_products?:number; report_days?:number; last_report?:string|null
  refill?:OverviewItem[]; refill_count?:number; running_low?:OverviewItem[]; sold_out?:OverviewItem[]; best_sellers?:OverviewItem[]
  recent_changes?:OverviewChange[]; not_set_up?:number; value?:OverviewValue|null
}
export const getInventoryOverview = (storeCode:string, signal:AbortSignal) =>
  client.get<InventoryOverview>('/inventory/overview', {params:{store_code:storeCode},signal}).then(response=>response.data)
