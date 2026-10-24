import { useRef, useState } from 'react'
import { Alert, Button, Skeleton } from 'antd'
import { Link } from 'react-router-dom'
import { useHasRole } from '../../auth/useRole'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { formatCents } from '../../lib/money'
import { allCheckouts, itemsText, methodGroups, methodLabel, splitNote, torontoTime, type MethodGroup } from '../Sales/dailySummary'
import { DayStepper, SalesTabs, dayTitle } from '../Sales/SalesNav'
import { Status } from './Incoming'
import { useStoreDay, type StoreDay, type StoreEvent } from './storeDay'
import './Home.css'
import './Summary.css'

const recordKinds: Array<[string, string, string]> = [
  ['claw_prize', '娃娃机出奖', 'Prize won'], ['claw_refill', '入娃娃机', 'Claw refill'],
  ['cash_exchange', '换现金', 'Cash exchange'], ['display_in', '入 display', 'Display arrival'],
]
const kindName = (kind: string) => recordKinds.find(([key]) => key === kind)?.[1] || kind

function recordText(event: StoreEvent) {
  return event.kind === 'cash_exchange' ? `${event.tender ? methodLabel(event.tender).zh : ''}收款，现金支出` : event.product_name || '—'
}
function recordAmount(event: StoreEvent) {
  return event.kind === 'cash_exchange' ? formatCents(event.amount_cents) : `× ${event.quantity ?? '—'}`
}

// Totals strip (Figma: phone 订单 · 收款 · 待完成; desktop 订单 then one cell per payment method).
function Figures({ groups, orders, pending, phone }: { groups: MethodGroup[]; orders: number; pending?: number; phone: boolean }) {
  const total = groups.reduce((sum, group) => sum + group.cents, 0)
  const split = groups.length ? new Set(groups.flatMap(group => group.parts.filter(part => part.others.length).map(part => part.order.id))).size : 0
  if (phone) return <div className="pc-figures pc-figures-3">
    <div><span>订单 Orders</span><strong>{orders}</strong></div>
    <div><span>收款 Total</span><strong>{formatCents(total)}</strong></div>
    <div><span>待完成 Open</span><strong className={pending ? 'pc-figure-warn' : undefined}>{pending ?? '—'}</strong></div>
  </div>
  return <div className="pc-figures pc-figures-6">
    <div><span>订单 Orders</span><strong>{orders}</strong><small>拆单 {split} · 待完成 {pending ?? '—'}</small></div>
    {groups.slice(0, 5).map(group => <div key={group.tender}><span>{group.zh} {group.en}</span><strong>{formatCents(group.cents)}</strong><small>{group.count} 笔</small></div>)}
  </div>
}

function OpenOrders({ pending }: { pending?: number }) {
  if (!pending) return null
  return <div className="pc-sum-open"><i className="pc-dot pc-dot-warn" aria-hidden="true" /><strong>{pending} 单未完成 Open order · 还没收款</strong><Link to="/checkout">去买单</Link></div>
}

function MethodCards({ groups }: { groups: MethodGroup[] }) {
  return <div className="pc-sum-methods">
    {groups.map(group => <section key={group.tender} className="pc-panel" aria-label={`${group.en || group.zh} payments`}>
      <header className="pc-panel-head"><h3>{group.zh} {group.en}</h3><span>{group.count} 笔 · {formatCents(group.cents)}</span></header>
      {group.parts.length ? group.parts.map(part => <div key={part.order.id} className="pc-sum-order">
        <time>{torontoTime(part.order.created_at)}</time>
        <span><strong>{itemsText(part.order) || '—'}</strong><small>{[part.order.reference, splitNote(part)].filter(Boolean).join(' · ')}</small></span>
        <b>{formatCents(part.cents)}</b>
      </div>) : <p className="pc-quiet">今天没有 · None today</p>}
    </section>)}
  </div>
}

function MethodTable({ groups, orders }: { groups: MethodGroup[]; orders: number }) {
  const parts = groups.reduce((sum, group) => sum + group.count, 0)
  const total = groups.reduce((sum, group) => sum + group.cents, 0)
  return <section className="pc-panel" aria-label="Payments by method">
    <header className="pc-panel-head"><h2><span>按付款方式</span> <small>Payments by method</small></h2><span className="pc-panel-count">{orders} 单 · {parts} 笔 · {formatCents(total)}</span></header>
    <div className="pc-sum-table-wrap"><table className="pc-sum-table">
      <thead><tr><th>时间</th><th>收银员</th><th>订单号</th><th>商品</th><th>备注</th><th>金额</th></tr></thead>
      {groups.filter(group => group.count).map(group => <tbody key={group.tender}>
        <tr className="pc-sum-group"><th colSpan={6} scope="rowgroup">{group.zh} {group.en} <small>{group.count} 笔 · {formatCents(group.cents)}</small></th></tr>
        {group.parts.map(part => <tr key={part.order.id}>
          <td className="pc-sum-muted">{torontoTime(part.order.created_at)}</td><td>{part.order.actor_name}</td><td>{part.order.reference}</td>
          <td>{itemsText(part.order) || '—'}</td><td className="pc-sum-muted">{splitNote(part, ' · ')}</td><td>{formatCents(part.cents)}</td>
        </tr>)}
      </tbody>)}
      {!parts && <tbody><tr><td colSpan={6} className="pc-sum-muted">今天还没有已完成的买单 · No completed checkouts yet.</td></tr></tbody>}
    </table></div>
  </section>
}

function RecordGroups({ data }: { data: StoreDay }) {
  return <div className="pc-sum-methods">
    {recordKinds.map(([kind, zh, en]) => {
      const events = data.events.filter(event => event.kind === kind)
      return <section key={kind} className="pc-panel" aria-label={en}>
        <header className="pc-panel-head"><h3>{zh} {kind === 'display_in' ? '' : en}</h3><span>{kind === 'claw_prize' ? events.reduce((sum, event) => sum + (event.quantity || 0), 0) : events.length}</span></header>
        {events.length ? events.map(event => <div key={event.id} className="pc-sum-order">
          <time>{torontoTime(event.created_at)}</time>
          <span><strong>{recordText(event)}</strong><small>{[event.actor_name, event.note].filter(Boolean).join(' · ')}</small></span>
          <b>{recordAmount(event)}</b>
        </div>) : <p className="pc-quiet">今天没有 · None today</p>}
      </section>
    })}
    <section className="pc-panel" aria-label="Incoming">
      <header className="pc-panel-head"><h3>入店 Incoming</h3><span>收货 {data.receipts.length} · 调拨 {data.transfers.length}</span></header>
      {data.receipts.map(receipt => <Link key={`r${receipt.id}`} className="pc-sum-order" to={`/goods/receiving?receipt_id=${receipt.id}`}>
        <time>收货</time><span><strong>{receipt.shipment_reference || `Receipt #${receipt.id}`}</strong><small>Receipt {receipt.id}</small></span><Status status={receipt.status} />
      </Link>)}
      {data.transfers.map(transfer => <Link key={`t${transfer.id}`} className="pc-sum-order" to={`/goods/transfers?transfer_id=${transfer.id}`}>
        <time>调拨</time><span><strong>Transfer #{transfer.id}</strong></span><Status status={transfer.status} />
      </Link>)}
      {!data.receipts.length && !data.transfers.length && <p className="pc-quiet">今天没有 · None today</p>}
    </section>
  </div>
}

function RecordTable({ data }: { data: StoreDay }) {
  const count = data.events.length + data.receipts.length + data.transfers.length
  return <section className="pc-panel" aria-label="Store records">
    <header className="pc-panel-head"><h2><span>记录</span> <small>Store records</small></h2><span className="pc-panel-count">{count}</span></header>
    <div className="pc-sum-table-wrap"><table className="pc-sum-table">
      <thead><tr><th>时间</th><th>员工</th><th>类型</th><th>内容</th><th>数量 / 金额</th></tr></thead>
      <tbody>
        {data.events.map(event => <tr key={event.id}>
          <td className="pc-sum-muted">{torontoTime(event.created_at)}</td><td>{event.actor_name}</td><td><span className="pc-sum-kind">{kindName(event.kind)}</span></td>
          <td>{[recordText(event), event.note].filter(Boolean).join(' · ')}</td><td>{recordAmount(event)}</td>
        </tr>)}
        {data.receipts.map(receipt => <tr key={`r${receipt.id}`}>
          <td className="pc-sum-muted">收货</td><td /><td><span className="pc-sum-kind">入店收货</span></td>
          <td><Link to={`/goods/receiving?receipt_id=${receipt.id}`}>{[receipt.shipment_reference, `Receipt ${receipt.id}`].filter(Boolean).join(' · ')}</Link></td><td><Status status={receipt.status} /></td>
        </tr>)}
        {data.transfers.map(transfer => <tr key={`t${transfer.id}`}>
          <td className="pc-sum-muted">调拨</td><td /><td><span className="pc-sum-kind">入店调拨</span></td>
          <td><Link to={`/goods/transfers?transfer_id=${transfer.id}`}>Transfer #{transfer.id}</Link></td><td><Status status={transfer.status} /></td>
        </tr>)}
        {!count && <tr><td colSpan={5} className="pc-sum-muted">今天没有记录 · No records today.</td></tr>}
      </tbody>
    </table></div>
  </section>
}

export default function StoreSummary() {
  const [date, setDate] = useState('')
  const { store, day, data, error, retry } = useStoreDay(date)
  const manager = useHasRole('manager')
  const phone = useIsPhone()
  const [copied, setCopied] = useState('')
  const text = useRef<HTMLTextAreaElement>(null)
  async function copy() {
    try { await navigator.clipboard.writeText(data?.summary_text || ''); setCopied('已复制 · Copied. Paste it into the store chat.') }
    catch { text.current?.focus(); text.current?.select(); setCopied('Select and copy the summary below.') }
  }
  const pick = (value: string) => { setDate(value); setCopied('') }
  const stepper = <DayStepper value={day} onChange={pick} />
  const refresh = <Button onClick={retry}><Icon name="refresh" size={16} />刷新<span className="pc-sr"> Refresh records</span></Button>
  const groups = methodGroups(data?.checkouts)
  const orders = allCheckouts(data?.checkouts).length
  const pending = data?.pending
  const lines = (data?.summary_text || '').split('\n').length

  const copyPanel = data && <section className="pc-panel pc-sum-copy" aria-label="Copy text">
    <header className="pc-panel-head"><h2><span>汇总文字</span> <small>Copy text</small></h2>{!phone && <button type="button" className="pc-btn pc-btn-ink" onClick={copy}>复制 Copy<span className="pc-sr"> summary</span></button>}</header>
    <textarea ref={text} aria-label="Summary text" value={data.summary_text} readOnly rows={Math.min(Math.max(lines, 6), phone ? 18 : 24)} />
    {copied && !phone && <p role="status" className="pc-sum-copied">{copied}</p>}
  </section>
  const closingPanel = <section className="pc-panel pc-sum-closing" aria-label="Closing">
    <header className="pc-panel-head"><h2><span>收工</span> <small>Closing</small></h2></header>
    <p className="pc-quiet">点钱、核对单据、提交给经理审核。<br />Count the drawer, check sources and submit for review.</p>
    <Link className="pc-btn pc-btn-ink" to="/closing">{manager ? '审核收工 Review closing' : '去收工 Close the day'}</Link>
  </section>
  const note = <p className="pc-sum-note">{data?.scope === 'personal'
    ? 'Your own orders and records. A manager reviews the whole store day.'
    : 'Only completed checkouts in POPCORE. Check for missing sales before closing; Clover sync isn’t connected yet.'}</p>

  return <div className={phone ? 'pc-sum pc-sum-phone' : 'pc-sum'}>
    <PageHeader title={manager ? '销售' : '汇总'}
      sub={phone ? 'Daily summary' : `${manager ? 'Sales' : 'Daily summary'}${store?.name ? ` · ${store.name}` : ''}${data ? ` · ${data.scope === 'personal' ? 'your records' : 'whole store'}` : ''}`}
      actions={<>{stepper}{refresh}<Link className="pc-btn" to="/checkout/history">订单记录 Order history</Link></>} />
    {manager && <SalesTabs />}
    {phone && stepper}
    {!store || store.code === 'ALL' ? <p className="pc-notice">Choose one store to review its summary.</p>
      : error ? <Alert role="alert" type="error" message={error} action={<Button onClick={retry}>Retry</Button>} />
      : !data ? <Skeleton active /> : phone ? <>
        <p className="pc-sum-scope">{data.scope === 'personal' ? 'Your own orders and records. A manager reviews the whole store day.' : 'The whole store day, recorded in POPCORE.'}</p>
        <Figures groups={groups} orders={orders} pending={pending} phone />
        <OpenOrders pending={pending} />
        <h2 className="pc-sum-heading">按付款方式 <small>by payment method</small></h2>
        <MethodCards groups={groups} />
        <h2 className="pc-sum-heading">记录 <small>Store records</small></h2>
        <RecordGroups data={data} />
        <h2 className="pc-sum-heading">{dayTitle(day)} <small>汇总文字 · 收工</small></h2>
        {copyPanel}
        <div className="pc-sum-links">{refresh}<Link className="pc-btn" to="/checkout/history">订单记录 Order history</Link></div>
        {closingPanel}
        {note}
        <div className="pc-sum-dock">
          {copied && <p role="status" className="pc-sum-copied">{copied}</p>}
          <button type="button" className="pc-btn pc-btn-ink" onClick={copy}>复制汇总文字 Copy summary</button>
        </div>
      </> : <>
        <Figures groups={groups} orders={orders} pending={pending} phone={false} />
        <div className="pc-sum-grid">
          <div className="pc-sum-main">
            <OpenOrders pending={pending} />
            <MethodTable groups={groups} orders={orders} />
            <RecordTable data={data} />
          </div>
          <aside className="pc-sum-side">{copyPanel}{closingPanel}{note}</aside>
        </div>
      </>}
  </div>
}
