import { useEffect, useState } from 'react'
import { Alert, Button, Input, Modal, Popconfirm, Skeleton, message } from 'antd'

import {
  dismissWebsiteOrder, getWebsiteOrders, getWebsitePreview, getWebsiteStatus, okWebsitePrices, problemText, refreshWebsite,
  retryWebsiteOrder, switchWebsite, type PriceReason, type WebsiteHeldPrice, type WebsiteOrder, type WebsitePreview,
  type WebsiteStatus,
} from '../../api/website'
import { useHasRole } from '../../auth/useRole'
import PageHeader from '../../components/pc/PageHeader'
import { ListRow, Panel } from '../../components/pc/Panel'
import './website.css'

// Times are saved in UTC and shown as Toronto time.
function when(utc: string | null) {
  if (!utc) return '—'
  const time = new Date(`${utc.replace(' ', 'T')}Z`)
  return Number.isNaN(time.getTime()) ? utc
    : time.toLocaleString('en-CA', { timeZone: 'America/Toronto', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
}

// The shop's sales reach POPCORE with the pasted daily report (owner, 2026-11-02).
const reportText = (row: WebsiteStatus['report'][number]) =>
  `${row.store}: ${row.days_behind ? `${row.days_behind} day${row.days_behind > 1 ? 's' : ''} not reported yet` : 'report up to date'}`

const money = (cents: number | null) => cents === null ? '' : `$${(cents / 100).toFixed(2)}`

// Why a price change waits for a manager's OK (owner, 2026-11-10).
function reasonText(reason: PriceReason) {
  switch (reason.code) {
    case 'first_price': return 'the website has no price for it yet, so customers could start buying it'
    case 'big_change': return `${reason.percent > 0 ? '+' : ''}${reason.percent}%, a big change`
    case 'no_clover_name': return 'no Clover item name in POPCORE, so this price was never checked against Clover'
    case 'set_size': return reason.website
      ? `the website’s Whole Set is ${reason.website} boxes, POPCORE counts ${reason.popcore}`
      : `the website’s Whole Set price is not ${reason.popcore} × the box’s ${money(reason.single_cents)}`
    case 'sale_stops': return `its sale price ${money(reason.sale_cents)} is not below it, so the website would end the sale`
    case 'sale_starts': return `its sale price ${money(reason.sale_cents)} would start discounting again, and its followers would be emailed that it is on sale`
    case 'other_option': return 'goes with the other option'
  }
}

// "Echo — Whole Set" in the Echo group reads "Whole Set"; a simple product needs no option name.
const optionName = (name: string, group: WebsiteHeldPrice) => name.startsWith(`${group.name} — `) ? `${name.slice(group.name.length + 3)} ` : ''
const stockText = (row: { manage_stock: number; stock_quantity: number | null; stock_status: string | null }) =>
  row.manage_stock ? String(row.stock_quantity ?? 0) : row.stock_status === 'instock' ? '有货 in stock' : '缺货 sold out'

// 网站 Website: popcore.ca takes its stock from POPCORE, and paid web orders take their items out of
// stock as Website orders, apart from the stores' closing (owner, 2026-11-01). POPCORE also sends the
// regular prices of linked boxes, Whole Sets and items; doubtful changes wait for a manager (owner, 2026-11-10).
export default function WebsitePage() {
  const admin = useHasRole('admin')
  const [status, setStatus] = useState<WebsiteStatus>()
  const [preview, setPreview] = useState<WebsitePreview | null>()
  const [orders, setOrders] = useState<WebsiteOrder[]>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [showAll, setShowAll] = useState(false)
  const [showAllPrices, setShowAllPrices] = useState(false)
  const [settling, setSettling] = useState<WebsiteOrder>()
  const [reason, setReason] = useState('')

  useEffect(() => {
    let live = true
    const load = () => Promise.all([getWebsiteStatus(), getWebsiteOrders()]).then(async ([next, rows]) => {
      if (!live) return
      setStatus(next)
      setOrders(rows)
      setError('')
      setPreview(next.configured && next.refreshed_at ? await getWebsitePreview().catch(() => null) : null)
    }).catch((cause: any) => { if (live) setError(cause?._serverMessage || 'Unable to load the website sync.') })
    load()
    // The sync runs every 30 seconds on the server; keep the page in step while it is open.
    const timer = window.setInterval(load, 15000)
    return () => { live = false; window.clearInterval(timer) }
  }, [attempt])

  async function run(key: string, action: () => Promise<string>) {
    setBusy(key)
    try {
      message.success(await action())
    } catch (cause: any) {
      message.error(cause?._serverMessage || 'That did not save. Try again.')
    } finally {
      setBusy('')
      setAttempt(value => value + 1)
    }
  }

  if (error) return <div className="pc-website">
    <PageHeader title="网站" sub="Website · popcore.ca" />
    <Alert role="alert" type="error" showIcon message={error} action={<Button onClick={() => setAttempt(value => value + 1)}>Retry</Button>} />
  </div>
  if (!status || !orders) return <div className="pc-website">
    <PageHeader title="网站" sub="Website · popcore.ca" />
    <div className="pc-panel"><div className="pc-quiet"><Skeleton active /></div></div>
  </div>

  const waiting = orders.filter(order => order.state === 'needs_review' || order.state === 'cancel_failed')
  const recent = orders.filter(order => !waiting.includes(order)).slice(0, 30)
  const changes = preview ? (showAll ? preview.changes : preview.changes.slice(0, 20)) : []
  // Held changes have their own panel; these are the ones that go by themselves.
  const going = preview ? preview.prices.filter(row => row.price_state !== 'held') : []
  const prices = showAllPrices ? going : going.slice(0, 20)

  return <div className="pc-website">
    <PageHeader title="网站" sub="Website · popcore.ca" />

    <Panel zh="库存同步" en="Stock sync" label="Stock sync" tone={status.enabled ? 'ok' : 'warn'}
      count={status.enabled ? '开 On' : '关 Off'}
      action={admin && status.configured ? <span className="pc-row-actions">
        <Button size="small" loading={busy === 'refresh' || status.refresh_requested} disabled={!!busy}
          onClick={() => run('refresh', async () => { await refreshWebsite(); return 'Reading the website’s products. This takes a minute or two.' })}>
          读取网站产品 Read website products</Button>
        {status.enabled
          ? <Popconfirm title="Turn the website sync off?" description="The website keeps its last numbers and prices, and stops taking web orders out of POPCORE stock."
            okText="Turn off" onConfirm={() => run('switch', async () => { await switchWebsite(false); return 'Website sync is off' })}>
            <Button size="small" danger loading={busy === 'switch'} disabled={!!busy}>关闭 Turn off</Button>
          </Popconfirm>
          : <Popconfirm title="Turn the website sync on?"
            description={preview ? `POPCORE will write ${preview.stock_changes} stock numbers${preview.price_send ? `, ${preview.price_send} price${preview.price_send === 1 ? '' : 's'}` : ''} and ${preview.pickup_changes} pickup lists to popcore.ca within a minute, then keep them in step. Turn off the website’s low-stock emails first.` : 'Read the website’s products first.'}
            okText="Turn on" okButtonProps={{ disabled: !preview }}
            onConfirm={() => run('switch', async () => { await switchWebsite(true); return 'Website sync is on' })}>
            <Button size="small" type="primary" loading={busy === 'switch'} disabled={!!busy || !preview}>开启 Turn on</Button>
          </Popconfirm>}
      </span> : undefined}>
      {!status.configured ? <p className="pc-quiet">
        {status.settings_problem || 'No website key is set on the server yet. An admin adds WEBSITE_URL, WEBSITE_KEY and WEBSITE_SECRET to the server settings (docs/website-sync.md).'}
      </p> : <>
        {status.last_error && <Alert type="error" showIcon message={status.last_error.message}
          description={`Last tried ${when(status.last_error.at)}`} />}
        <ListRow label="网站" title={status.url} meta={`Live stores: ${status.live_stores.join(', ') || 'none'} · Delivery orders from ${status.delivery_store}${status.keep_back ? ` · Also kept back on every item: ${status.keep_back}` : ''}`} />
        <ListRow label="日报" title={status.report.length ? status.report.map(reportText).join(' · ') : 'No live store yet'}
          meta={`${status.report.length ? `Sales in up to ${status.report.map(row => `${row.reported_through ?? '—'}${status.report.length > 1 ? ` (${row.store})` : ''}`).join(', ')}. ` : ''}${preview?.shop_hold.items ?`${preview.shop_hold.units} units of ${preview.shop_hold.items} items held back for the shop. ` : preview ? 'Nothing held back for the shop. ' : ''}Until a shop day’s report is in, each item holds back what the shop sold on its best day of the last 30 reports; a shop day starts at ${status.shop_opens}.`} />
        <ListRow label="产品" title={status.refreshed_at ? `Read ${when(status.refreshed_at)}` : 'Not read yet'}
          meta={preview ? `${preview.items} linked to POPCORE · ${preview.unknown_id} POPCORE ID not in POPCORE (shown sold out)${preview.shared_id ? ` · ${preview.shared_id} share a POPCORE ID (shown sold out)` : ''} · ${preview.no_id} without a POPCORE ID, left as they are` : undefined} />
        <ListRow label="同步" title={status.enabled ? `On since ${when(status.switched_at)}` : 'Off: nothing is written to the website'}
          meta={`Last run ${when(status.last_run)} · Last write ${when(status.last_push_at)}${status.last_push_count ? ` (${status.last_push_count})` : ''}${status.push_errors ? ` · ${status.push_errors} refused by the website` : ''}`} />
      </>}
    </Panel>

    {preview && <Panel zh={status.enabled ? '待同步' : '开启后会改变'} en={status.enabled ? 'Waiting to be written' : 'What turning on changes'}
      count={preview.stock_changes} label="Changes" tone={preview.stock_changes ? 'warn' : 'ok'}>
      {!status.enabled && <p className="pc-quiet">
        Turning on sets the website’s stock to POPCORE’s for every linked item: {preview.back_in_stock} come back in stock,
        {' '}{preview.sold_out} sell out, {preview.low_stock} have 1–2 left, and {preview.pickup_changes} products change their pickup stores.
        Back-in-stock writes email their followers; low stock emails the website’s low-stock recipient unless it is off.
      </p>}
      {changes.map(row => <ListRow key={row.wc_id} label={row.popcore_id} title={row.name}
        meta={`网站 Website ${stockText(row)} → POPCORE ${row.quantity}`} />)}
      {preview.changes.length > 20 && <Button type="link" onClick={() => setShowAll(value => !value)}>
        {showAll ? 'Show fewer' : `Show all ${preview.changes.length}`}</Button>}
      {preview.unknown_ids.length > 0 && <details className="pc-website-unknown">
        <summary>{preview.unknown_ids.length} website items carry a POPCORE ID that POPCORE does not have{preview.shared_id ? ', or one that another item also carries' : ''}, so they show sold out. Fix the POPCORE ID on the website, or add the item in POPCORE.</summary>
        {preview.unknown_ids.map(row => <ListRow key={row.wc_id} label={row.popcore_id} title={row.name}
          meta={row.shared ? '编号重复 · Another website item carries this POPCORE ID too' : '不在 POPCORE · Not in POPCORE'} />)}
      </details>}
      {preview.no_ids.length > 0 && <details className="pc-website-unknown">
        <summary>{preview.no_id} website items have no POPCORE ID; POPCORE leaves them as they are</summary>
        {preview.no_ids.map(row => <ListRow key={row.wc_id} title={row.name} meta={row.sku ? `SKU ${row.sku}` : undefined} />)}
      </details>}
    </Panel>}

    {preview && preview.held_prices.length > 0 && <Panel zh="价格待确认" en="Prices waiting for an OK"
      label="Prices waiting for an OK" count={preview.held_prices.length} tone="warn">
      <p className="pc-quiet">
        POPCORE does not send these prices until a manager presses OK. If POPCORE’s price or set size is wrong, fix it in POPCORE
        instead; once the cause is fixed, the change goes or disappears by itself.
      </p>
      {preview.held_prices.map(group => <ListRow key={group.wc_id} label={group.options.map(row => row.popcore_id).join(' / ')}
        title={group.name}
        meta={group.options.map(row => `${optionName(row.name, group)}${money(row.regular_cents) || 'no price'} → ${money(row.price)}: ${row.price_reasons.map(reasonText).join('; ')}`).join(' · ')}
        trailing={<Popconfirm title="Send these prices to popcore.ca?"
          description={group.options.map(row => `${optionName(row.name, group)}${money(row.price)}`).join(', ')} okText="Send"
          onConfirm={() => run(`ok:${group.wc_id}`, async () => {
            await okWebsitePrices(group.wc_id, group.ok_key)
            return status.enabled ? `${group.name}: the new prices go with the next write` : `${group.name}: the new prices go once the sync is on`
          })}>
          <Button size="small" loading={busy === `ok:${group.wc_id}`} disabled={!!busy}>确认 OK</Button>
        </Popconfirm>} />)}
    </Panel>}

    {preview && <Panel zh="价格" en="Prices" label="Prices" count={going.length} tone="ok">
      <p className="pc-quiet">
        Each linked box and item gets its Clover price, and each Whole Set its sealed set’s price. Regular prices only: sale
        prices stay. Left as the website has them: {preview.price_left.design} designs and Vault products,
        {' '}{preview.price_left.not_clover} on old POPCORE records, {preview.price_left.no_price} without a POPCORE price.
        {preview.price_daily ? ` ${preview.price_daily} Daily Shop price${preview.price_daily === 1 ? ' waits' : 's wait'} for the hour after the ${status.daily_reset} reset.` : ''}
      </p>
      {going.length === 0 && <p className="pc-quiet">没有 · Every linked price matches POPCORE’s{preview.price_held ? ', apart from those waiting for an OK' : ''}.</p>}
      {prices.map(row => <ListRow key={row.wc_id} label={row.popcore_id} title={row.name}
        meta={`网站 Website ${money(row.regular_cents) || 'no price'} → POPCORE ${money(row.price)}${row.price_state === 'daily' ? ` · 每日 Daily Shop: after the ${status.daily_reset} reset` : ''}${row.sale_cents !== null ? ` · sale ${money(row.sale_cents)} stays` : ''}`} />)}
      {going.length > 20 && <Button type="link" onClick={() => setShowAllPrices(value => !value)}>
        {showAllPrices ? 'Show fewer' : `Show all ${going.length}`}</Button>}
      {status.price_writes.length > 0 && <details className="pc-website-unknown">
        <summary>Prices POPCORE wrote lately</summary>
        {status.price_writes.map(row => <ListRow key={`${row.wc_id}-${row.written_at}`} label={row.popcore_id} title={row.name}
          meta={`${money(row.old_cents) || 'no price'} → ${money(row.new_cents)} · ${when(row.written_at)}${row.approved_by ? ' · OK’d by a manager' : ''}`} />)}
      </details>}
    </Panel>}

    <Panel zh="待处理网站订单" en="Web orders to review" count={waiting.length} label="Web orders to review" tone={waiting.length ? 'warn' : 'ok'}>
      {waiting.length === 0 ? <p className="pc-quiet">没有 · Every paid web order came out of stock.</p>
        : waiting.map(order => <ListRow key={order.wc_order_id} label={`#${order.number}`}
          title={order.lines.map(line => `${line.popcore_id || line.name} × ${line.quantity}`).join(', ')}
          meta={[order.state === 'cancel_failed' ? `取消未退回 · Cancelled on the website, not yet back in stock (${order.problem})`
            : problemText[order.problem || ''] || order.problem, when(order.paid_at), money(order.total_cents)].filter(Boolean).join(' · ')}
          trailing={<span className="pc-row-actions">
            <Button size="small" loading={busy === `retry:${order.wc_order_id}`} disabled={!!busy}
              onClick={() => run(`retry:${order.wc_order_id}`, async () => {
                const next = await retryWebsiteOrder(order.wc_order_id)
                return next === 'recorded' || next === 'cancelled' ? `#${order.number} done` : `#${order.number} still waits`
              })}>再试 Try again</Button>
            <Button size="small" disabled={!!busy} onClick={() => { setSettling(order); setReason('') }}>已处理 Settled</Button>
          </span>} />)}
    </Panel>

    <Panel zh="网站订单" en="Web orders" count={status.orders.recorded} label="Web orders" tone="ok">
      {recent.length === 0 ? <p className="pc-quiet">No web orders yet.</p>
        : recent.map(order => <ListRow key={order.wc_order_id} label={`#${order.number}`}
          title={order.lines.map(line => `${line.popcore_id || line.name} × ${line.quantity}`).join(', ')}
          meta={[{ recorded: `出库 ${order.store_code}`, cancelled: '已取消 Cancelled', dismissed: `已处理 Settled: ${order.dismiss_reason}`, new: '新 New' }[order.state as string],
            order.refunded ? '已退款 Refunded on the website' : null, order.fulfilment, when(order.paid_at), money(order.total_cents)].filter(Boolean).join(' · ')} />)}
    </Panel>

    <Modal open={!!settling} title={settling ? `Settle web order #${settling.number}` : ''} okText="Settle"
      okButtonProps={{ disabled: !reason.trim(), loading: busy === 'dismiss' }} onCancel={() => setSettling(undefined)}
      onOk={() => settling && run('dismiss', async () => {
        await dismissWebsiteOrder(settling.wc_order_id, reason.trim())
        setSettling(undefined)
        return `#${settling.number} settled`
      })}>
      <p>Settling takes nothing out of stock. Use it when the stock was already recorded another way, or the order was filled from somewhere POPCORE does not count.</p>
      <Input.TextArea aria-label="Reason" rows={3} value={reason} onChange={event => setReason(event.target.value)} placeholder="Reason" />
    </Modal>
  </div>
}
