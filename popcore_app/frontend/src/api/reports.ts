import client from './client'
export type ReportName='inventory'|'movements'|'goods-exceptions'|'sales'|'tenders'|'evidence-exceptions'|'cash-variance'|'count-discrepancies'|'closed-days'
export interface ReportResult{report:ReportName;scope:string;store_ids:number[];items:Record<string,unknown>[];total_rows:number;known_gross_cents?:number;incomplete_count?:number;gross_complete?:boolean}
export const getReport=(name:ReportName,params:Record<string,string|number>)=>client.get<ReportResult>(`/reports/${name}`,{params}).then(r=>r.data)
export async function downloadReport(name:ReportName,params:Record<string,string|number>){
  const response=await client.get(`/reports/${name}/export.csv`,{params,responseType:'blob'})
  const url=URL.createObjectURL(response.data)
  const link=document.createElement('a')
  link.href=url;link.download=`${name}.csv`;link.click();URL.revokeObjectURL(url)
}
