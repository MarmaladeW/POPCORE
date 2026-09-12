import client from './client'
import { newRequestKey } from './salesDocuments'

export { newRequestKey }

export interface ClosingSession {
  closing_id: number
  store_id: number
  business_date: string
  status: 'draft' | 'submitted' | 'closed'
  version: number
  intake_complete: number
  source_token: string
  cash: {
    opening_cash_cents: number
    verified_cash_receipts_cents: number
    expected_drawer_cents: number
    unknown_cash_payment_ids: number[]
    event_totals_cents: Record<'paid_in'|'refund'|'payout'|'removal',number>
  }
  latest_cash_count?: {
    id: number
    revision: number
    opening_coin_cents: number
    retained_coin_cents: number
    denomination_counts: Record<string,number>
    counted_cents: number
    expected_cents: number
    variance_cents: number
    retained_cents: number
    removal_cents: number | null
  } | null
  tender_totals_cents?: Record<'cash'|'card'|'e_transfer'|'wechat'|'alipay',number>
  unknown_payment_ids?: number[]
  snapshot?: {
    snapshot_id: number
    counted_cents: number
    expected_cents: number
    variance_cents: number
    retained_cents: number
    removal_cents: number | null
    accepted_exceptions: Record<string,string> | string[]
    tender_totals_cents?: Record<string,number>
    unknown_payment_ids?: number[]
  }
  late_adjustments?: Array<{id:number;source_type:string;source_id:string;reason:string;created_at?:string}>
  hard_blockers?: string[]
  review_exceptions?: string[]
  source_documents?: {
    sales:Array<{id:number;status:string;allocation_status:string;financial_status:string}>
    receipts:Array<{id:number;status:string}>
    counts:Array<{id:number;status:string}>
  }
}

export async function createClosing(storeId: number, businessDate: string, key: string) {
  return (await client.post('/closing', {
    store_id: storeId, business_date: businessDate,
  }, { headers: { 'Idempotency-Key': key } })).data as ClosingSession
}

export async function updateClosing(
  id: number, version: number, intakeComplete: boolean, key: string,
) {
  return (await client.patch(`/closing/${id}`, {
    expected_version: version, intake_complete: intakeComplete,
  }, { headers: { 'Idempotency-Key': key } })).data as ClosingSession
}

export async function addCashCount(id: number, session: ClosingSession,
  openingCoinCents: number, retainedCoinCents: number,
  denominationCounts: Record<string, number>, key: string) {
  return (await client.post(`/closing/${id}/cash-counts`, {
    expected_version: session.version,
    source_token: session.source_token,
    opening_coin_cents: openingCoinCents,
    retained_coin_cents: retainedCoinCents,
    denomination_counts: denominationCounts,
  }, { headers: { 'Idempotency-Key': key } })).data as ClosingSession
}

export async function fetchClosing(id: number, signal?: AbortSignal) {
  return (await client.get(`/closing/${id}`, { signal })).data as ClosingSession
}

export async function addCashEvent(id:number,session:ClosingSession,eventType:'paid_in'|'refund'|'payout',amountCents:number,reason:string,key:string){
  return (await client.post(`/closing/${id}/cash-events`,{
    expected_version:session.version,event_type:eventType,amount_cents:amountCents,reason,
  },{headers:{'Idempotency-Key':key}})).data as ClosingSession
}

export async function submitClosing(session: ClosingSession, key: string) {
  return (await client.post(`/closing/${session.closing_id}/submit`, {
    expected_version: session.version, source_token: session.source_token,
  }, { headers: { 'Idempotency-Key': key } })).data as ClosingSession
}

export async function closeClosing(
  session: ClosingSession, reasons: Record<string,string>, key: string,
) {
  return (await client.post(`/closing/${session.closing_id}/close`, {
    expected_version: session.version,
    source_token: session.source_token,
    accepted_exceptions: (session.review_exceptions ?? []).map(code => ({ code, reason:reasons[code] })),
  }, { headers: { 'Idempotency-Key': key } })).data as {
    closing_id: number
    snapshot_id: number
    status: 'closed'
  }
}

export async function returnClosing(session:ClosingSession,reason:string,key:string){
  return (await client.post(`/closing/${session.closing_id}/return`,{
    expected_version:session.version,reason,
  },{headers:{'Idempotency-Key':key}})).data as ClosingSession
}
