import client from './client'
import type { BoxOptions } from '../pages/Sales/boxChoice'

export type SaleEntryMode = 'planned_entry' | 'already_paid'

export interface SaleLineInput {
  product_id?: number
  raw_product_text?: string
  unit?: 'box' | 'set' | 'piece'
  quantity: number
  unit_price_cents?: number | null
  source_tax_cents?: number | null
  /** Where blind boxes over half a set came from: an opened set, a new sealed set, or loose boxes. */
  open_set_id?: number | null
  fresh_set?: { product_id: number; conversion_id: number; conversion_factor: number } | null
  loose_boxes?: boolean
  /** Sold from the store's display location instead of the floor. */
  from_display?: boolean
}

export type AllocationMapping = { line_no: number; product_id?: number; non_stock?: boolean } & Pick<SaleLineInput, 'open_set_id' | 'fresh_set' | 'loose_boxes'>

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
  store_id?: number
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
    cny_fen?: number | null
    cny_per_cad?: string | null
    state: 'recorded' | 'verified' | 'rejected'
    /** Refunds of this payment; each counts on its own business_date, the day the money left. */
    refunded_cents?: number
    events?: Array<{ id:number; event_type:string; direction:string; amount_cents:number|null; reason:string; business_date?:string|null }>
    evidence?: Array<{ id:number; status:'pending'|'accepted'|'rejected'; mime_type:string; byte_size:number }>
  }>
  returns?: Array<{ id:number; sale_line_no:number; quantity:number; disposition:string; reason:string; business_date?:string|null }>
  /** Money received, as corrected; a rejected payment is not in it (it is in rejected_cents). */
  payment_total_cents?: number | null
  payment_difference_cents?: number | null
  rejected_cents?: number
  refunded_cents?: number
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

export async function fetchSale(id: number, signal?: AbortSignal) {
  return (await client.get(`/sale-documents/${id}`, { signal })).data as SaleResult
}

export async function fetchPaymentEvidence(id: number, signal?: AbortSignal) {
  return (await client.get(`/payment-evidence/${id}/content`, { responseType: 'blob', signal })).data as Blob
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
  mappings: AllocationMapping[], key: string) {
  return (await client.post(`/sale-documents/${id}/allocate`, {
    expected_version: version,
    reason, mappings,
  }, { headers: { 'Idempotency-Key': key } })).data as SaleResult
}

/** A blind box at a store's floor: its set size and what each answer has on hand; null for other products. */
export async function fetchBoxOptions(storeId: number, productId: number, signal?: AbortSignal) {
  const { data } = await client.get('/sale-documents/box-options', { params: { store_id: storeId, product_id: productId }, signal })
  return (data?.options ?? null) as BoxOptions | null
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
