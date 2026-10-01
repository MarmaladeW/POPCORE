import client from './client'

export interface ProductReference {
  id:number;sku:string;jizhanming:string;name_cn_en:string;series_id:number|null;series_name:string|null
  stock_form:string|null;stock_unit:string|null;design_name:string|null;identity_status:string
}
export interface HistoryCandidate extends ProductReference {target_identity_token:string;score:number|null;reason:string}
export interface HistoricalMatch {
  id:number;date:string;store:string;raw_name:string;notes:string;qty_sold:number;qty_pos:number;qty_cash:number
  qty_claw:number;qty_display:number;qty_employee:number;unit_price:number|null;row_token:string
  current_product:ProductReference;candidates:HistoryCandidate[];match_status:string;match_label:string
  blocked_reason:string|null;blocked_code?:string|null
  last_review:{actor_sub:string;reason:string;created_at:string;from_product_id:number;to_product_id:number}|null
}
export interface HistoricalMatches {items:HistoricalMatch[];total_rows:number;page:number;page_size:number}
export const getHistoricalMatches=(params:Record<string,string|number>,signal:AbortSignal)=>
  client.get<HistoricalMatches>('/sales/history-matches',{params,signal}).then(response=>response.data)

export interface StoreOverview {
  scope:string;stores:Array<{id:number;code:string}>;period:{from:string;to:string;calendar_days:number};generated_at:string
  posted_sales:{document_count:number;known_gross_cents:number;unknown_gross_count:number;gross_complete:boolean;days_with_documents:number
    daily:Array<{date:string;document_count:number;known_gross_cents:number|null;unknown_gross_count:number;coverage:string}>
    top_products:Array<{product_id:number|null;product_name:string;native_unit:string|null;quantity:number;line_count:number;identity_complete:boolean;series_id:number|null}>}
  checkout_activity:{order_count:number;open_count:number;completed_count:number;cancelled_count:number;completed_in_posted_sales:number;completed_without_posted_sale:number;open_snapshot_gross_cents:number;open_unknown_gross_count:number}
  historical_reports:{row_count:number;product_count:number;days_with_rows:number;days_with_metadata:number
    daily:Array<{date:string;row_count:number;metadata_count:number;reported_quantity:number|null;coverage:string}>
    top_products:Array<{product_id:number;product_name:string;raw_names:string[];reported_quantity:number;days_reported:number}>}
  current_inventory:{mode:string;reviewed_location_count:number;unreviewed_location_count:number;unknown_product_count:number
    exception_counts:{out_of_stock:number;replenish:number;condition:number;unverified:number}
    items:Array<{product_id:number;product_name:string;series_id:number|null;native_unit:string|null;reasons:string[]}>;total_exception_products:number}
  limitations:string[]
}
export const getStoreOverview=(params:Record<string,string>,signal:AbortSignal)=>
  client.get<StoreOverview>('/reports/overview',{params,signal}).then(response=>response.data)
