// Who did something and when, as one short line (owner, 2026-11-20: "in WeChat it is super easy to check who did
// what"). Times are Toronto time; a server time without a zone is UTC.
export function torontoStamp(timestamp: string | null | undefined): string {
  if (!timestamp) return ''
  const parsed = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(timestamp) ? timestamp : `${timestamp.replace(' ', 'T')}Z`)
  if (Number.isNaN(parsed.getTime())) return ''
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Toronto', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(parsed).map(part => [part.type, part.value]))
  return `${parts.month}/${parts.day} ${parts.hour}:${parts.minute}`
}

export function markText(name: string | null | undefined, timestamp?: string | null): string {
  return [name || '没有员工资料 No staff profile', torontoStamp(timestamp)].filter(Boolean).join(' · ')
}

export const DELIVERY_ACTIONS: Record<string, string> = {
  dispatch: '发出 Sent', receive: '收到 Received', return: '退回 Returned', resolve_loss: '记为丢失 Lost', short_close: '关闭 Closed short',
}
