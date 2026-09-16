import { useEffect, useState } from 'react'
import { useAuth0 } from '@auth0/auth0-react'
import client from '../../api/client'
import { useRole } from '../../auth/useRole'
import { useAppStore } from '../../store'
import { torontoDate } from '../Dashboard/todayPresentation'

export type StoreEvent = {
  id: number; kind: string; business_date: string; created_at: string; actor_name: string
  product_name: string | null; quantity: number | null; amount_cents: number | null
  tender: string | null; note: string
}
export type StoreDay = {
  business_date: string; scope: 'personal' | 'store'; events: StoreEvent[]; summary_text: string
  receipts: { id: number; shipment_reference: string | null; status: string }[]
  transfers: { id: number; status: string; source_store_id: number; destination_store_id: number }[]
}
export const eventNames: Record<string, string> = {
  claw_prize: '出奖 / Prize won', claw_refill: '补货 / Refill', cash_exchange: '换现金 / Cash exchange', display_in: '入 display / Display arrival',
}

export function useStoreDay(date?: string) {
  const store = useAppStore(state => state.selectedStore)
  const { user } = useAuth0()
  const role = useRole()
  const [today, setToday] = useState(torontoDate)
  const [attempt, setAttempt] = useState(0)
  const [result, setResult] = useState<{ key: string; data?: StoreDay; error?: string }>()
  const day = date || today
  const key = `${store?.id}|${user?.sub}|${role}|${day}|${today}|${attempt}`
  useEffect(() => {
    const update = () => setToday(torontoDate())
    const timer = window.setInterval(update, 30_000)
    const onFocus = () => { update(); setAttempt(value => value + 1) }
    window.addEventListener('focus', onFocus)
    return () => { window.clearInterval(timer); window.removeEventListener('focus', onFocus) }
  }, [])
  useEffect(() => {
    if (!store || store.code === 'ALL') return
    const controller = new AbortController()
    client.get<StoreDay>('/store-events', { params: { store_id: store.id, business_date: day }, signal: controller.signal, timeout: 15000 })
      .then(response => { if (!controller.signal.aborted) setResult({ key, data: response.data }) })
      .catch(cause => { if (!controller.signal.aborted) setResult({ key, error: cause?._serverMessage || 'Unable to load records. Please retry.' }) })
    return () => controller.abort()
  }, [key, store?.id, day])
  return { store, day, data: result?.key === key ? result.data : undefined,
    error: result?.key === key ? result.error : undefined, retry: () => setAttempt(value => value + 1) }
}
