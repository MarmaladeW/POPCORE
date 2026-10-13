import client from './client'
import type { InventoryLocation, NativeUnit } from './goods'

export interface SeriesBalance {
  location_id: number
  disposition: string
  quantity: number | null
  version: number | null
}
export interface SeriesProduct {
  id: number
  sku: string
  name: string
  series_id: number
  stock_form: 'sealed_set' | 'random_box' | 'confirmed_design' | 'ordinary' | null
  stock_unit: NativeUnit | null
  design_name: string | null
  identity_status: string
  image_filename?: string | null
  balances: SeriesBalance[]
  open_sets?: Array<{id:number;location_id:number;purpose:string;remaining_qty:number;opening_document_id:number}>
}
export interface InventorySeries { id:number; name:string; products:SeriesProduct[] }
export interface SeriesInventoryResult {
  mode: string
  locations: InventoryLocation[]
  unassigned_count: number
  series: InventorySeries[]
}
export interface SeriesMovement {
  id:number; document_id:number; kind:string; product_id:number; product_name:string
  location_id:number; location_name:string; disposition:string; quantity:number
  business_date:string; posted_at:string; reason:string|null; open_set_id?:number|null
  actor_sub?:string; store_code?:string; source_type?:string|null; source_id?:string|null; native_unit?:string|null
}
export const getInventorySeries = (storeCode:string, query:string, seriesId:number|undefined, signal:AbortSignal) =>
  client.get<SeriesInventoryResult>('/inventory/series', {params:{store_code:storeCode,q:query||undefined,series_id:seriesId},signal}).then(response=>response.data)
export interface SeriesHistoryFilters {
  product_id?:number; date_from?:string; date_to?:string; before_id?:number; limit?:number
}
export interface SeriesHistoryPage {items:SeriesMovement[]; has_more:boolean; next_before_id:number|null}
export interface InventoryDocument {
  id:number; kind:string; actor_sub:string; business_date:string; posted_at:string
  source_type:string|null; source_id:string|null; reason:string|null; correction_of:number|null
  corrections:number[]
  lines:Array<{line_no:number; product_id:number; native_unit:string; quantity:number
    from_location_id:number|null; from_disposition:string|null; to_location_id:number|null; to_disposition:string|null
    from_version:number|null; to_version:number|null; conversion_id:number|null; conversion_factor:number|null; open_set_id:number|null}>
}
export const getSeriesHistory = (id:number, storeCode:string, signal:AbortSignal, filters:SeriesHistoryFilters={}) =>
  client.get<SeriesHistoryPage>(`/inventory/series/${id}/history`, {params:{store_code:storeCode,...filters},signal}).then(response=>response.data)
export const getInventoryDocument = (id:number, signal:AbortSignal) =>
  client.get<InventoryDocument>(`/inventory/documents/${id}`,{signal}).then(response=>response.data)
export const getSeriesCatalog = (signal:AbortSignal) =>
  client.get<Array<{id:number;name:string}>>('/product-series',{signal}).then(response=>response.data)
