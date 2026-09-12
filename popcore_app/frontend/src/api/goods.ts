import client from './client'

export type NativeUnit = 'box' | 'set' | 'piece'

export interface GoodsProduct {
  id: number
  sku: string
  jizhanming: string
  stock_unit?: NativeUnit | null
  identity_status?: 'verified' | 'unverified'
}
export interface InventoryLocation {
  id: number
  store_id: number
  store_code: string
  code: string
  name: string
  opening_verified: boolean
}

export interface WorkflowResult {
  id: number
  version: number
  status: string
  inventory_document_id?: number | null
}

export interface ReceiptLine {line_no:number;product_id:number;native_unit:NativeUnit;expected_quantity:number|null;saleable_quantity:number;damaged_quantity:number;hold_quantity:number;discrepancy_note:string|null}
export interface ReceiptDetail extends WorkflowResult {store_id:number;destination_location_id:number;business_date:string;shipment_reference:string|null;supplier:string|null;lines:ReceiptLine[]}
export interface TransferLine {line_no:number;product_id:number;native_unit:NativeUnit;requested_quantity:number;dispatched_quantity:number;received_quantity:number;returned_quantity:number;loss_quantity:number;short_quantity:number;outstanding_transit:number}
export interface TransferDetail extends WorkflowResult {kind:'transfer'|'restock';source_location_id:number;destination_location_id:number;business_date:string;restock_session_id:number|null;lines:TransferLine[]}
export interface CountLine {line_no:number;product_id:number;native_unit:NativeUnit;expected_quantity:number;observed_quantity:number;captured_balance_version:number}
export interface CountDetail extends WorkflowResult {store_id:number;location_id:number;disposition:string;business_date:string;lines:CountLine[]}
export interface CountResult extends WorkflowResult {recount_id?:number}

export const getReceipt=(id:number,signal?:AbortSignal)=>client.get<ReceiptDetail>(`/goods/receipts/${id}`,{signal}).then(response=>response.data)
export const getTransfer=(id:number,signal?:AbortSignal)=>client.get<TransferDetail>(`/goods/transfers/${id}`,{signal}).then(response=>response.data)
export const getCount=(id:number,signal?:AbortSignal)=>client.get<CountDetail>(`/goods/counts/${id}`,{signal}).then(response=>response.data)

export async function updateReceipt(id:number,body:object,key:string){return (await client.patch(`/goods/receipts/${id}`,body,{headers:{'Idempotency-Key':key}})).data as WorkflowResult}
export async function cancelReceipt(id:number,version:number,key:string){return (await client.post(`/goods/receipts/${id}/cancel`,{expected_version:version},{headers:{'Idempotency-Key':key}})).data as WorkflowResult}

export const requestKey = () => crypto.randomUUID()
export const searchGoodsProducts=(query:string)=>client.get<GoodsProduct[]>('/products/search',{params:{q:query,limit:20}}).then(response=>response.data)

export async function resolveGoodsBarcode(code: string, purpose = 'receive') {
  const response = await client.get('/goods/barcodes/resolve', { params: { code, purpose } })
  return response.data as {
    status: 'exact' | 'ambiguous' | 'unknown'
    candidates: Array<{ product_id: number; stock_unit: NativeUnit; quantity_per_scan: number }>
  }
}

export async function createReceipt(body: object, key: string) {
  return (await client.post('/goods/receipts', body, {
    headers: { 'Idempotency-Key': key },
  })).data as WorkflowResult
}

export async function postReceipt(id: number, version: number, key: string) {
  return (await client.post(`/goods/receipts/${id}/post`, {
    expected_version: version,
  }, { headers: { 'Idempotency-Key': key } })).data as WorkflowResult
}

export async function createTransfer(body: object, key: string) {
  return (await client.post('/goods/transfers', body, {
    headers: { 'Idempotency-Key': key },
  })).data as WorkflowResult
}

export async function actOnTransfer(
  id: number, action: 'dispatch' | 'receive' | 'return' | 'resolve_loss' | 'short_close',
  body: object, key: string,
) {
  return (await client.post(`/goods/transfers/${id}/${action}`, body, {
    headers: { 'Idempotency-Key': key },
  })).data as WorkflowResult
}

export async function createCount(body: object, key: string) {
  return (await client.post('/goods/counts', body, {
    headers: { 'Idempotency-Key': key },
  })).data as WorkflowResult
}

export async function actOnCount(
  id: number, action: 'submit' | 'approve' | 'return', body: object, key: string,
) {
  return (await client.post(`/goods/counts/${id}/${action}`, body, {
    headers: { 'Idempotency-Key': key },
  })).data as CountResult
}
