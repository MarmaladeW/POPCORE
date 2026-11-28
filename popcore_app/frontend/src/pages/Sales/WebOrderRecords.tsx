import { useEffect, useState } from 'react'
import { Alert, Button, Input, Skeleton } from 'antd'
import { Link } from 'react-router-dom'
import client from '../../api/client'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { formatCents } from '../../lib/money'
import { useAppStore } from '../../store'
import { torontoDate } from '../Dashboard/todayPresentation'
import { torontoTime } from './dailySummary'
import { SalesTabs, dayTitle } from './SalesNav'
import { byDay, countText, kindText, monthTitle, shiftMonth, statusText, takenText,
  type WebOrderRecord, type WebOrderRecords } from './webOrderRecordsView'
import './orderRecords.css'

const dot = (order: WebOrderRecord) => order.waiting ? 'pc-dot pc-dot-warn' : order.status === 'done' ? 'pc-dot pc-dot-ok' : ''
// "14:20", or "11月28日 周五 09:05" when it happened on a later day than the order was paid.
function when(gmt: string, paidDay: string) {
  const day = torontoDate(new Date(`${gmt}Z`))
  return `${day === paidDay ? '' : `${dayTitle(day)} `}${torontoTime(gmt)}`
}

// One web order: number, kind and store, the items as the customer bought them, where it stands and its amount.
// Opening it shows the rest of what POPCORE keeps (line amounts, POPCORE names, payment, tax, packed, done).
export function WebOrderRow({ order }: { order: WebOrderRecord }) {
  const taken = takenText(order)
  const facts = [
    order.payment && `付款 ${order.payment}`,
    order.tax_cents != null && `税 ${formatCents(order.tax_cents)}`,
    order.packed_at && ['打包', order.packed_by, when(order.packed_at, order.day)].filter(Boolean).join(' '),
    order.closed_at && `${statusText(order)} ${when(order.closed_at, order.day)}`,
    order.settled && `已处理：${order.settled}`,
  ].filter(Boolean)
  return <details className={order.counted ? 'pc-wo' : 'pc-wo pc-wo-void'}>
    <summary>
      <time>{torontoTime(order.paid_at)}</time>
      <span className="pc-wo-main">
        <span className="pc-wo-top"><strong>#{order.number}</strong><span>{kindText(order)}</span>{taken && <span>{taken}</span>}</span>
        {order.lines.map((line, index) => <span key={index} className="pc-wo-item">
          {line.name || line.popcore_id || '—'} <b className={line.quantity > 1 ? 'pc-wo-many' : undefined}>× {line.quantity}</b>
        </span>)}
      </span>
      <span className="pc-wo-end">
        <b>{formatCents(order.total_cents ?? 0)}</b>
        <span className="pc-wo-status">{dot(order) && <i className={dot(order)} aria-hidden="true" />}{order.waiting ? '待处理' : statusText(order)}</span>
      </span>
      <Icon name="down" size={14} className="pc-wo-chev" />
    </summary>
    <div className="pc-wo-more">
      <table>
        <tbody>{order.lines.map((line, index) => <tr key={index}>
          <td>{line.name || '—'}{(line.popcore_name || line.popcore_id) && <small>{[line.popcore_name, line.popcore_id].filter(Boolean).join(' · ')}</small>}</td>
          <td>× {line.quantity}</td><td>{line.total_cents == null ? '' : formatCents(line.total_cents)}</td>
        </tr>)}</tbody>
      </table>
      {!!facts.length && <p>{facts.join(' · ')}</p>}
      {order.waiting && <p className="pc-wo-wait"><i className="pc-dot pc-dot-warn" aria-hidden="true" />没能自动出库，需要在网站页处理 <Link to="/website">去处理</Link></p>}
    </div>
  </details>
}

// 销售 → 网站订单 (popcore-work#157): every web order of a month, by day, or the ones a search finds in any month.
export default function WebOrderRecordsPage() {
  const store = useAppStore(state => state.selectedStore)
  const phone = useIsPhone()
  const [month, setMonth] = useState(() => torontoDate().slice(0, 7))
  const [typed, setTyped] = useState('')
  const [query, setQuery] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [result, setResult] = useState<{ key: string; data?: WebOrderRecords; error?: string }>()
  const code = store && store.code !== 'ALL' ? store.code : undefined
  const key = `${month}|${query}|${code}|${attempt}`
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(typed.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [typed])
  useEffect(() => {
    const controller = new AbortController()
    client.get<WebOrderRecords>('/website/records', { params: query ? { q: query, store: code } : { month, store: code }, signal: controller.signal, timeout: 15000 })
      .then(response => { if (!controller.signal.aborted) setResult({ key, data: response.data }) })
      .catch(cause => { if (!controller.signal.aborted) setResult({ key, error: cause?._serverMessage || 'Unable to load web orders. Please retry.' }) })
    return () => controller.abort()
  }, [key])
  const data = result?.key === key ? result.data : undefined
  const error = result?.key === key ? result.error : undefined
  const thisMonth = torontoDate().slice(0, 7)
  const stepper = <div className="pc-day pc-wo-month" aria-label="Month">
    <button type="button" aria-label="Previous month" disabled={!!query} onClick={() => setMonth(value => shiftMonth(value, -1))}><Icon name="back" size={16} /></button>
    <span className="pc-day-date"><strong>{monthTitle(month)}</strong><small>{month === thisMonth ? '本月' : month}</small></span>
    <button type="button" aria-label="Next month" disabled={!!query || month >= thisMonth} onClick={() => setMonth(value => shiftMonth(value, 1))}><Icon name="right" size={16} /></button>
  </div>

  return <div className="pc-wo-page">
    <PageHeader title="销售" sub={phone ? 'Web orders' : `Web orders${code ? ` · ${store?.name}` : ' · all stores'}`} actions={stepper} />
    <SalesTabs />
    <div className="pc-wo-tools">
      {phone && stepper}
      <Input allowClear prefix={<Icon name="search" size={16} />} value={typed} onChange={event => setTyped(event.target.value)}
        placeholder="订单号或商品 Order number or item" aria-label="Search web orders" />
    </div>
    {error ? <Alert role="alert" type="error" message={error} action={<Button onClick={() => setAttempt(value => value + 1)}>Retry</Button>} />
      : !data ? <Skeleton active /> : <section className="pc-panel" aria-label="Web orders">
        <header className="pc-panel-head pc-wo-head">
          <h2><span>{query ? '搜索结果' : monthTitle(month)}</span> <small>{query ? '所有月份' : code || '所有门店'}</small></h2>
          {!!data.orders.length && <span className="pc-wo-sum"><b>{formatCents(data.totals.cents)}</b><small>{countText(data.totals)}</small></span>}
        </header>
        {!data.orders.length ? <p className="pc-quiet">{query ? '没有找到这个订单 · No web order matches.' : '这个月没有网站订单 · No web orders this month.'}</p>
          : byDay(data.orders).map(day => <div key={day.day} className="pc-wo-day">
            <h3><span>{dayTitle(day.day)}</span><small>{day.count} 单 · {formatCents(day.cents)}</small></h3>
            {day.orders.map(order => <WebOrderRow key={order.id} order={order} />)}
          </div>)}
      </section>}
  </div>
}
