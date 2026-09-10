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

export const requestKey = () => crypto.randomUUID()

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
  id: number, action: 'dispatch' | 'receive' | 'return' | 'short_close',
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
  })).data as WorkflowResult
}
