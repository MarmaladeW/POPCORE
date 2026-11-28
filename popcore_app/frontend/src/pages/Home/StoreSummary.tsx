import { useRef, useState } from 'react'
import { Alert, Button, Skeleton } from 'antd'
import { Link } from 'react-router-dom'
import { useHasRole } from '../../auth/useRole'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { formatCents } from '../../lib/money'
import { allCheckouts, itemsText, methodGroups, methodLabel, torontoTime, type SummaryCheckout } from '../Sales/dailySummary'
import { DayStepper, SalesTabs } from '../Sales/SalesNav'
import { WebOrderRow } from '../Sales/WebOrderRecords'
import { countText, type WebOrderRecords } from '../Sales/webOrderRecordsView'
import { Status } from './Incoming'
import { useStoreDay, type StoreDay, type StoreEvent } from './storeDay'
import './Home.css'
import './Summary.css'

const kindNames: Record<string, string> = {
  claw_prize: '娃娃机出奖', claw_refill: '入娃娃机', cash_exchange: '换现金', display_in: '入 display',
}

function recordText(event: StoreEvent) {
  return event.kind === 'cash_exchange' ? `${event.tender ? methodLabel(event.tender).zh : ''}收款，现金支出` : event.product_name || '—'
}
function recordAmount(event: StoreEvent) {
  return event.kind === 'cash_exchange' ? formatCents(event.amount_cents) : `× ${event.quantity ?? '—'}`
}

// A channel's head (owner, 2026-11-28: 汇总 is "instore orders and website orders"): its name, its total large, and
// what the total is made of on one line under it.
function ChannelHead({ zh, en, cents, parts, link }: { zh: string; en: string; cents: number; parts: string; link?: { to: string; text: string } }) {
  return <header className="pc-ch-head">
    <h2><span>{zh}</span> <small>{en}</small></h2>
    {link && <Link to={link.to}>{link.text}</Link>}
    <strong>{formatCents(cents)}</strong>
    <small>{parts}</small>
  </header>
}

// "现金 $28.00 + 刷卡 $22.00" for a split order, "刷卡" for one method.
function paidText(order: SummaryCheckout) {
  const byTender = new Map<string, number>()
  for (const payment of order.payments) byTender.set(payment.tender, (byTender.get(payment.tender) || 0) + payment.amount_cents)
  return byTender.size > 1
    ? [...byTender].map(([tender, cents]) => `${methodLabel(tender).zh} ${formatCents(cents)}`).join(' + ')
    : methodLabel(order.payments[0]?.tender || '').zh
}

// 门店订单: every completed 记一笔 / 买单 of the day, in time order, with the payment split in the head.
function InStore({ data }: { data: StoreDay }) {
  const orders = allCheckouts(data.checkouts)
  const groups = methodGroups(data.checkouts).filter(group => group.count)
  const total = groups.reduce((sum, group) => sum + group.cents, 0)
  const parts = [`${orders.length} 单`, ...groups.map(group => `${group.zh} ${formatCents(group.cents)}`)].join(' · ')
  return <section className="pc-panel pc-ch" aria-label="In-store orders">
    <ChannelHead zh="门店订单" en="In-store" cents={total} parts={parts} link={{ to: '/checkout/history', text: '订单记录' }} />
    {!!data.pending && <div className="pc-sum-open"><i className="pc-dot pc-dot-warn" aria-hidden="true" /><strong>{data.pending} 单未完成 · 还没收款</strong><Link to="/checkout">去买单</Link></div>}
    {orders.length ? orders.map(order => {
      const amount = order.payments.reduce((sum, payment) => sum + payment.amount_cents, 0)
      return <div key={order.id} className="pc-ch-row">
        <time>{torontoTime(order.created_at)}</time>
        <span className="pc-ch-main"><strong>{itemsText(order) || '—'}</strong><small>{[order.actor_name, order.reference].filter(Boolean).join(' · ')}</small></span>
        <span className="pc-ch-end"><b>{formatCents(amount)}</b><small>{paidText(order)}</small></span>
      </div>
    }) : <p className="pc-quiet">今天还没有门店订单 · 记一笔和买单收款后在这里</p>}
  </section>
}

// 网站订单: the store's web orders paid that day. Their own line: they never add to the store's total.
function Web({ web }: { web: WebOrderRecords }) {
  const orders = [...web.orders].sort((a, b) => a.paid_at.localeCompare(b.paid_at) || a.id - b.id)
  return <section className="pc-panel pc-ch" aria-label="Web orders">
    <ChannelHead zh="网站订单" en="Web · 不计入门店" cents={web.totals.cents} parts={countText(web.totals)}
      link={{ to: '/sales/web-orders', text: '全部记录' }} />
    {orders.length ? orders.map(order => <WebOrderRow key={order.id} order={order} />)
      : <p className="pc-quiet">今天没有网站订单</p>}
  </section>
}

// 门店记录: 娃娃机, 换现金, 入 display and 入店, one line each, only the ones there were.
function Records({ data }: { data: StoreDay }) {
  const count = data.events.length + data.receipts.length + data.transfers.length
  return <section className="pc-panel pc-ch" aria-label="Store records">
    <header className="pc-panel-head"><h2><span>门店记录</span> <small>Store records</small></h2><span className="pc-panel-count">{count}</span></header>
    {data.events.map(event => <div key={event.id} className="pc-ch-row">
      <time>{torontoTime(event.created_at)}</time>
      <span className="pc-ch-main"><strong><span className="pc-sum-kind">{kindNames[event.kind] || event.kind}</span>{recordText(event)}</strong>
        <small>{[event.actor_name, event.note].filter(Boolean).join(' · ')}</small></span>
      <span className="pc-ch-end"><b>{recordAmount(event)}</b></span>
    </div>)}
    {data.receipts.map(receipt => <Link key={`r${receipt.id}`} className="pc-ch-row" to={`/goods/receiving?receipt_id=${receipt.id}`}>
      <time>收货</time>
      <span className="pc-ch-main"><strong><span className="pc-sum-kind">入店收货</span>{receipt.shipment_reference || `Receipt ${receipt.id}`}</strong></span>
      <span className="pc-ch-end"><Status status={receipt.status} /></span>
    </Link>)}
    {data.transfers.map(transfer => <Link key={`t${transfer.id}`} className="pc-ch-row" to={`/goods/transfers?transfer_id=${transfer.id}`}>
      <time>调拨</time>
      <span className="pc-ch-main"><strong><span className="pc-sum-kind">入店调拨</span>Transfer {transfer.id}</strong></span>
      <span className="pc-ch-end"><Status status={transfer.status} /></span>
    </Link>)}
    {!count && <p className="pc-quiet">今天没有 · None today</p>}
  </section>
}

export default function StoreSummary() {
  const [date, setDate] = useState('')
  const { store, day, data, error, retry } = useStoreDay(date)
  // The whole store's day (销售 and its tabs) is the admin's (owner, 2026-11-26); everyone else has their own 汇总.
  const admin = useHasRole('admin')
  const phone = useIsPhone()
  const [copied, setCopied] = useState('')
  const [showText, setShowText] = useState(false)
  const text = useRef<HTMLTextAreaElement>(null)
  async function copy() {
    try { await navigator.clipboard.writeText(data?.summary_text || ''); setCopied('已复制 · Copied. Paste it into the store chat.') }
    catch {
      setShowText(true)
      window.setTimeout(() => { text.current?.focus(); text.current?.select() })
      setCopied('Select and copy the summary below.')
    }
  }
  const pick = (value: string) => { setDate(value); setCopied('') }
  const stepper = <DayStepper value={day} onChange={pick} />
  const refresh = <Button onClick={retry} aria-label="Refresh"><Icon name="refresh" size={16} />{!phone && '刷新'}</Button>
  const copyButton = <button type="button" className="pc-btn" onClick={copy} disabled={!data}>复制汇总文字<span className="pc-sr"> Copy summary</span></button>
  const personal = data?.scope === 'personal'
  const lines = (data?.summary_text || '').split('\n').length

  // The text staff paste into the store chat, folded away: the panels above carry the same facts.
  const copyText = data && <details className="pc-panel pc-sum-copy" open={showText} onToggle={event => setShowText(event.currentTarget.open)}>
    <summary><span>汇总文字</span> <small>Copy text</small><Icon name="down" size={14} className="pc-wo-chev" /></summary>
    <textarea ref={text} aria-label="Summary text" value={data.summary_text} readOnly rows={Math.min(Math.max(lines, 6), 24)} />
  </details>

  return <div className={phone ? 'pc-sum pc-sum-phone' : 'pc-sum'}>
    <PageHeader title={admin ? '销售' : '汇总'}
      sub={phone ? 'Daily summary' : `${admin ? 'Sales' : 'Daily summary'}${store?.name ? ` · ${store.name}` : ''}${personal ? ' · your records' : ''}`}
      actions={<>{stepper}{refresh}{copyButton}</>} />
    {admin && <SalesTabs />}
    {phone && stepper}
    {!store || store.code === 'ALL' ? <p className="pc-notice">Choose one store to review its summary.</p>
      : error ? <Alert role="alert" type="error" message={error} action={<Button onClick={retry}>Retry</Button>} />
      : !data ? <Skeleton active /> : <>
        {personal && <p className="pc-sum-scope">只有你自己的订单和记录 · Your own orders and records.</p>}
        {copied && !phone && <p role="status" className="pc-sum-copied">{copied}</p>}
        <div className={data.web_orders ? 'pc-ch-pair' : 'pc-ch-one'}>
          <InStore data={data} />
          {data.web_orders && <Web web={data.web_orders} />}
        </div>
        <div className="pc-ch-after">
          <Records data={data} />
          {copyText}
        </div>
        {phone && <div className="pc-sum-dock">
          {copied && <p role="status" className="pc-sum-copied">{copied}</p>}
          <div className="pc-sum-dock-row">{refresh}<button type="button" className="pc-btn pc-btn-ink" onClick={copy}>复制汇总文字 Copy summary</button></div>
        </div>}
      </>}
  </div>
}
