import client from './client'
export interface TodayRow { source_id:number; type:string; status:string; store_id:number|null; version:number|null; updated_at:string|null; link:string; label?:string }
export interface TodaySection { total:number; rows:TodayRow[] }
export interface TodayPayload { business_date:string; generated_at:string; role:string; scope:string; store_ids:number[]; authorized_stores:{id:number;code:string;name:string}[]; sections:Record<string,TodaySection> }
export const getToday=(store_code:string,business_date:string,signal?:AbortSignal)=>client.get<TodayPayload>('/today',{params:{store_code,business_date},signal}).then(r=>r.data)
