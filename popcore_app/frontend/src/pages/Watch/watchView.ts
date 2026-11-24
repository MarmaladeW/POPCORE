import type { WatchAlert, WatchKind, WatchStatus } from '../../api/watch'

export const kindLabels: Record<WatchKind, { zh: string; en: string }> = {
  stock_loss: { zh: '损耗', en: 'Loss' },
  count_gap: { zh: '盘点差', en: 'Count gap' },
  cash_gap: { zh: '现金差', en: 'Cash gap' },
  floor_short: { zh: '日报差异', en: 'Report notices' },
  sync_stuck: { zh: '同步', en: 'Sync' },
}

// Where to go to look into one alert.
export const kindLinks: Record<WatchKind, string> = {
  stock_loss: '/stock/loss', count_gap: '/count', cash_gap: '/sales', floor_short: '/sales/report-stock', sync_stuck: '/website',
}

// Open alerts in two groups: what needs a look, and what Claude thought looked normal (shown folded, never hidden).
export function splitAlerts(alerts: WatchAlert[]) {
  return { look: alerts.filter(alert => alert.verdict !== 'low'), quiet: alerts.filter(alert => alert.verdict === 'low') }
}

// A saved UTC time ('2026-11-24 15:00:00') as Toronto month/day and time.
export function torontoTime(utc: string | null) {
  if (!utc) return ''
  const time = new Date(`${utc.replace(' ', 'T')}Z`)
  if (Number.isNaN(time.getTime())) return utc
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Toronto', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(time).map(part => [part.type, part.value]))
  return `${parts.month}/${parts.day} ${parts.hour}:${parts.minute}`
}

const outcomes: Record<string, [string, string]> = {
  ok: ['Claude 已看过', 'Claude reviewed'], none: ['没有新提醒', 'Nothing new'],
  no_key: ['服务器没有 Claude 密钥', 'No Claude key on the server'], claude_off: ['Claude 已关闭', 'Claude is off'],
  cap: ['已到今日花费上限', 'Daily spend cap reached'], failed: ['Claude 调用失败', 'Claude call failed'],
  refused: ['Claude 没有回答', 'Claude declined'], cut_off: ['回答不完整', 'Reply cut off'],
  bad_reply: ['回答无法读取', 'Reply unreadable'], running: ['进行中', 'Running'],
}
export function outcomeText(outcome: string) {
  const [zh, en] = outcomes[outcome] ?? [outcome, outcome]
  return `${zh} ${en}`
}

// One line for admins: on or off, and what the Claude review can do right now.
export function statusLine(status: WatchStatus) {
  if (!status.enabled) return '监控已关闭 · The watcher is off (设置 Settings → 异常提醒)'
  const claude = !status.claude ? 'Claude 关闭 off'
    : !status.has_key ? '没有密钥 no key'
    : `${status.model} · 每天 ${status.review_time} · 今日 $${status.spent_today_usd.toFixed(2)} / $${status.daily_cap_usd.toFixed(2)}`
  return `监控已开启 · Watching · ${claude}`
}
