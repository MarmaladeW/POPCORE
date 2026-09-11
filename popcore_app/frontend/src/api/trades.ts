import client from './client'
import { newRequestKey } from './salesDocuments'

export interface TradeProduct { product_id: number; name: string; stock_form: string; stock_unit: string; quantity: number; balance_version: number }
export interface TradeSlot { slot_id: number; store_id: number; series_id: number; location_id: number; occupant_unit_id: number | null; version: number; occupant?: { unit_id: number; design_product_id: number; design_name: string; condition_disclosure: string }; products?: TradeProduct[] }
export const listTradeSlots = async (storeId: number) => (await client.get('/trade-slots', { params: { store_id: storeId } })).data as TradeSlot[]
export const getTradeSlot = async (id: number) => (await client.get(`/trade-slots/${id}`)).data as TradeSlot
export const getTradeSetup = async (storeId: number) => (await client.get('/trade-setup', { params: { store_id: storeId } })).data as { floor_location_id: number; series: { series_id: number; name: string }[] }
export const createTradeSlot = async (body: object) => (await client.post('/trade-slots', body, { headers: { 'Idempotency-Key': newRequestKey() } })).data
export const openTradeSlot = async (id: number, body: object) => (await client.post(`/trade-slots/${id}/open`, body, { headers: { 'Idempotency-Key': newRequestKey() } })).data
export const inspectTrade = async (id: number, body: object) => (await client.post(`/trade-slots/${id}/inspections`, body, { headers: { 'Idempotency-Key': newRequestKey() } })).data as { inspection_id: number; incoming_unit_id: number; decision: string }
export const swapTrade = async (id: number, body: object) => (await client.post(`/trade-slots/${id}/swap`, body, { headers: { 'Idempotency-Key': newRequestKey() } })).data
export const sellTrade = async (id: number, body: object) => (await client.post(`/trade-slots/${id}/sale`, body, { headers: { 'Idempotency-Key': newRequestKey() } })).data
export const createConditionCase = async (body: object) => (await client.post('/condition-cases', body, { headers: { 'Idempotency-Key': newRequestKey() } })).data as { case_id: number }
export const getConditionCase = async (id: number) => (await client.get(`/condition-cases/${id}`)).data
export const decideConditionCase = async (id: number, body: object) => (await client.post(`/condition-cases/${id}/decision`, body, { headers: { 'Idempotency-Key': newRequestKey() } })).data
export const uploadConditionEvidence = async (id: number, file: File) => { const form = new FormData(); form.append('image', file); return (await client.post(`/condition-cases/${id}/evidence`, form, { headers: { 'Idempotency-Key': newRequestKey() } })).data }
