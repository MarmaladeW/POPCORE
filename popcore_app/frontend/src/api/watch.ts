import client from './client'

// 异常提醒 Alerts from the watcher (owner, 2026-11-24): what POPCORE's rules found, and Claude's note when it had one.
export type WatchKind = 'stock_loss' | 'count_gap' | 'cash_gap' | 'floor_short' | 'sync_stuck'
export interface WatchAlert {
  id: number; kind: WatchKind; severity: 'high' | 'normal'; status: 'open' | 'checked' | 'resolved'
  store: string | null; business_date: string | null; title_zh: string; title_en: string
  verdict: 'keep' | 'low' | null; note_zh: string | null; note_en: string | null
  created_at: string; checked_at: string | null; checked_by: string | null
}
// Admins only: the switches, today's spend and exactly what the last review sent to Anthropic.
export interface WatchStatus {
  enabled: boolean; claude: boolean; has_key: boolean; model: string; daily_cap_usd: number; review_time: string
  spent_today_usd: number; review_requested: boolean
  last_rules: { at: string; found: number } | null
  last_review: { at: string; outcome: string; found: number; cost_usd: number; error: string | null
    summary_zh: string | null; summary_en: string | null } | null
  last_sent: { at: string; payload: unknown } | null
}
export interface WatchView { alerts: WatchAlert[]; recent: WatchAlert[]; admin: boolean; status?: WatchStatus }

export const getWatch = (signal?: AbortSignal) => client.get<WatchView>('/watch', { signal }).then(r => r.data)
export const checkWatchAlert = (id: number) => client.post<{ ok: boolean }>(`/watch/${id}/check`).then(r => r.data)
export const runWatchNow = () => client.post<{ found: number; status: WatchStatus }>('/watch/run').then(r => r.data)
