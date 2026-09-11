import client from './client'

export type SaleEntryMode = 'planned_entry' | 'already_paid'

export interface SaleLineInput {
  product_id?: number
  raw_product_text?: string
  unit?: 'box' | 'set' | 'piece'
  quantity: number
  unit_price_cents?: number | null
  source_tax_cents?: number | null
  open_set_id?: number
}

export interface SaleInput {
  store_id: number
  business_date: string
  entry_mode: SaleEntryMode
  source: { system: string; account: string; reference: string }
  subtotal_cents?: number | null
  source_tax_cents?: number | null
  gross_cents?: number | null
  reduction_cents?: number | null
  rounding_cents?: number | null
  collected_cents?: number | null
  lines: SaleLineInput[]
}

export interface SaleResult {
  sale_id: number
  version: number
  status: 'draft' | 'posted'
  financial_status?: 'draft' | 'recorded'
  allocation_status?: 'draft' | 'pending' | 'allocated'
  allocation_reason?: string | null
  inventory_document_id?: number | null
  unresolved_reasons?: string[]
  entry_mode?: SaleEntryMode
  business_date?: string
  collected_cents?: number | null
  lines?: Array<SaleLineInput & {
    line_no: number
    product_name_snapshot?: string | null
  }>
  sources?: Array<{
    source_system: string
    source_account: string
    source_reference: string
  }>
  payments?: Array<{
    id: number
    tender: 'cash' | 'card' | 'e_transfer' | 'wechat' | 'alipay'
    amount_cents: number | null
    effective_amount_cents: number | null
    state: 'recorded' | 'verified' | 'rejected'
    events?: Array<{ id:number; event_type:string; direction:string; amount_cents:number|null; reason:string }>
    evidence?: Array<{ id:number; status:'pending'|'accepted'|'rejected'; mime_type:string; byte_size:number }>
  }>
  returns?: Array<{ id:number; sale_line_no:number; quantity:number; disposition:string; reason:string }>
  payment_total_cents?: number | null
  payment_difference_cents?: number | null
}

export const newRequestKey = () => crypto.randomUUID()

export async function createSale(body: SaleInput, key: string) {
  return (await client.post('/sale-documents', body, {
    headers: { 'Idempotency-Key': key },
  })).data as SaleResult
}

export async function postSale(id: number, version: number, key: string) {
  return (await client.post(`/sale-documents/${id}/post`, {
    expected_version: version,
  }, { headers: { 'Idempotency-Key': key } })).data as SaleResult
}

export async function fetchSale(id: number) {
  return (await client.get(`/sale-documents/${id}`)).data as SaleResult
}

export async function addPayments(
  id: number, version: number,
  payments: Array<{ tender: string; amount_cents: number | null }>, key: string,
) {
  return (await client.post(`/sale-documents/${id}/payments`, {
    expected_version: version, payments,
  }, { headers: { 'Idempotency-Key': key } })).data as SaleResult
}

export async function allocateSale(id: number, version: number, reason: string,
  mappings: Array<{line_no:number;product_id:number;open_set_id?:number}>, key: string) {
  return (await client.post(`/sale-documents/${id}/allocate`, {
    expected_version: version,
    reason, mappings,
  }, { headers: { 'Idempotency-Key': key } })).data as SaleResult
}

export async function reviewPayment(paymentId:number, saleVersion:number,
  decision:'verify'|'reject',reason:string,key:string){
  return (await client.post(`/payments/${paymentId}/${decision}`,{
    expected_version:saleVersion,reason,
  },{headers:{'Idempotency-Key':key}})).data
}

export async function reviewEvidence(evidenceId:number,decision:'accepted'|'rejected',
  reason:string,key:string){
  return (await client.post(`/payment-evidence/${evidenceId}/review`,{decision,reason},
    {headers:{'Idempotency-Key':key}})).data
}

export async function addSaleSource(id:number,version:number,source:{source_system:string;source_account:string;source_reference:string},reason:string,key:string){
  return (await client.post(`/sale-documents/${id}/source-links`,{
    expected_version:version,...source,reason,
  },{headers:{'Idempotency-Key':key}})).data
}

export async function recordRefund(paymentId:number,saleVersion:number,amountCents:number,reason:string,key:string){
  return (await client.post(`/payments/${paymentId}/events`,{
    event_type:'refund',expected_version:saleVersion,amount_cents:amountCents,reason,
  },{headers:{'Idempotency-Key':key}})).data
}

export async function recordPhysicalReturn(id:number,version:number,lineNo:number,
  quantity:number,disposition:'saleable'|'damaged'|'hold',reason:string,key:string){
  return (await client.post(`/sale-documents/${id}/returns`,{
    expected_version:version,line_no:lineNo,quantity,disposition,reason,
  },{headers:{'Idempotency-Key':key}})).data
}

export async function uploadPaymentEvidence(paymentId: number, file: File, key: string) {
  const body = new FormData()
  body.append('image', file)
  return (await client.post(`/payments/${paymentId}/evidence`, body, {
    headers: { 'Idempotency-Key': key },
  })).data as {
    evidence_id: number
    payment_id: number
    mime_type: string
    byte_size: number
    status: 'pending'
  }
}
