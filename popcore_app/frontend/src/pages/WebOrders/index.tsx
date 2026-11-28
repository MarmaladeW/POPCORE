import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Alert, Button, Input, Popconfirm, Skeleton, message, notification } from 'antd'

import {
  findWebOrderCode, getWebOrdersBoard, handWebOrderOver, markWebOrderPacked, markWebOrderReady, undoWebOrderPacked,
  type WebOrderCard, type WebOrderCodeResult, type WebOrdersBoard, type WebOrderStep,
} from '../../api/webOrders'
import { useHasRole } from '../../auth/useRole'
import PageHeader from '../../components/pc/PageHeader'
import { Panel } from '../../components/pc/Panel'
import { ALL_STORES, useAppStore } from '../../store'
import Notifications, { chime, soundOn } from './Notifications'
import {
  COLUMNS, ageText, announceText, cardStores, clockText as clock, codeMissText, courierText, deliveryFacts, longWait, moveText,
  nameMatches, newCards, openCards, packedText, picksText, pieceCount, reasonText, websiteTime,
} from './webOrdersView'
import './webOrders.css'

// The board follows the sync's pace (every 30 seconds) without reloading the page.
const REFRESH_MS = 30_000
// A found order's customer name leaves the screen by itself after this long.
const CODE_CARD_MS = 3 * 60_000

const errorText = (cause: any, fallback: string) => cause?._serverMessage || fallback

function stepMessage(step: WebOrderStep, done: string, already: string) {
  if (step.result === 'already') message.info(already)
  else if (step.noted === false) message.warning(`${done} The website did not take the note saying who did it.`)
  else message.success(done)
}

// One order (owner, 2026-11-27: "make the important information clearer ... the order number is not what is
// important here, what important here are: item name, item piece, pickup code, delivery name"). A quiet line says
// pickup or delivery, the store, the order number and its age, with the pieces in all; then who it is for (a
// delivery's name, or the customer once their code is typed), large; then each item by name, its quantity in its own
// box, filled in ink when it is more than one; the delivery's details come last, quiet. The pickup code itself stays
// off the board: it proves the person at the counter is the customer.
type Customer = { name: string; collector: string | null }
function OrderCard({ card, now, customer, action, extra }: {
  card: WebOrderCard; now: number; customer?: Customer; action?: React.ReactNode; extra?: React.ReactNode
}) {
  const waited = longWait(card, now)
  const move = moveText(card)
  const courier = courierText(card)
  const packed = packedText(card)
  const facts = card.kind === 'delivery' ? deliveryFacts(card) : []
  const pieces = pieceCount(card)
  const shipTo = card.kind === 'delivery' ? card.name : null
  return <article className="pc-web-card" aria-label={`Order #${card.number}`}>
    <header className="pc-web-head">
      <span className="pc-web-kind">{card.kind === 'pickup' ? '自取' : '配送'} <b>{cardStores(card).join(' + ')}</b></span>
      <span className="pc-web-meta"><span className="pc-web-number"><span aria-hidden="true">#</span>{card.number}</span>
        <time>{ageText(card.paid_at, now)}</time></span>
      <span className="pc-web-pieces"><b>{pieces}</b> 件 <small>{pieces === 1 ? 'item' : 'items'}</small></span>
    </header>
    {customer ? <p className="pc-web-person"><small>顾客 Customer</small> <strong>{customer.name || '—'}</strong>
      {customer.collector && <small>代取 Collecting: <b>{customer.collector}</b></small>}</p>
      : shipTo && <p className="pc-web-person"><small>收件人 Ship to</small> <strong>{shipTo}</strong></p>}
    {waited > 0 && <p className="pc-web-flag"><i className="pc-dot pc-dot-warn" aria-hidden="true" />已等 {waited} 天 · Waiting {waited} days</p>}
    {move && <p className="pc-web-flag"><i className="pc-dot pc-dot-warn" aria-hidden="true" />{move}</p>}
    {courier && <p className="pc-web-flag"><i className="pc-dot pc-dot-warn" aria-hidden="true" />{courier}</p>}
    <ul className="pc-web-lines">
      {card.lines.map((line, index) => <li key={index}>
        <span className={line.quantity > 1 ? 'pc-web-qty pc-web-qty-many' : 'pc-web-qty'}><span className="sr-only">× </span>{line.quantity}</span>
        <strong>{line.name}</strong>
        {(line.popcore_id || line.picks.length > 0) && <small>{[line.popcore_id, picksText(line)].filter(Boolean).join(' · ')}</small>}
      </li>)}
    </ul>
    {facts.length > 0 && <ul className="pc-web-facts">
      {facts.map(fact => <li key={fact}>{fact}</li>)}
    </ul>}
    {packed && <p className="pc-web-packed"><i className="pc-dot pc-dot-ok" aria-hidden="true" />{packed}</p>}
    {extra}
    {action}
  </article>
}

// The code the customer gives at the counter: the only way to Handed over. The first name (and who collects) is read
// live from the website for this one order and leaves the screen when the card closes.
function CodeBox({ store, onChange }: { store?: string; onChange: () => void }) {
  const [code, setCode] = useState('')
  const [typed, setTyped] = useState('')
  const [result, setResult] = useState<WebOrderCodeResult | null>(null)
  const [busy, setBusy] = useState('')
  const [now, setNow] = useState(Date.now())
  const close = () => { setResult(null); setTyped('') }

  useEffect(() => {
    if (!result) return
    const timer = window.setTimeout(close, CODE_CARD_MS)
    return () => window.clearTimeout(timer)
  }, [result])

  async function find(event: FormEvent) {
    event.preventDefault()
    if (!code.trim()) return
    setBusy('find')
    try {
      const found = await findWebOrderCode(code, store)
      setResult(found)
      setTyped(code)
      setCode('')
      setNow(Date.now())
      if (found.found) onChange()  // its status was read live
    } catch (cause) {
      message.error(errorText(cause, 'The website could not be asked. Try again.'))
    } finally {
      setBusy('')
    }
  }

  async function handOver(card: WebOrderCard) {
    setBusy('hand')
    try {
      stepMessage(await handWebOrderOver(card.id, typed), `#${card.number} handed over. The website emails the customer.`,
        `#${card.number} was already handed over.`)
      close()
      onChange()
    } catch (cause) {
      message.error(errorText(cause, 'The website did not take it. Nothing changed; try again.'))
    } finally {
      setBusy('')
    }
  }

  return <section className="pc-panel pc-web-code" aria-label="Pickup code">
    <form onSubmit={find}>
      <label htmlFor="web-order-code"><strong>取货码</strong> <small>Pickup code</small></label>
      <Input id="web-order-code" value={code} onChange={event => setCode(event.target.value)} autoComplete="off"
        spellCheck={false} maxLength={16} placeholder="例 e.g. K7R4MX" disabled={!!busy} />
      <Button type="primary" htmlType="submit" loading={busy === 'find'} disabled={!code.trim() || !!busy}>查找 Find</Button>
    </form>
    {result && !result.found && <Alert role="status" type="info" showIcon message={codeMissText(result)}
      action={<Button size="small" onClick={close}>关闭 Close</Button>} />}
    {result?.found && <OrderCard card={result.order} now={now}
      customer={{ name: result.first_name, collector: result.collector }}
      extra={result.needs_manager && <p className="pc-web-flag"><i className="pc-dot pc-dot-warn" aria-hidden="true" />
        POPCORE could not take this order out of stock. Hand it over, then tell the admin.</p>}
      action={<div className="pc-web-actions">
        <Popconfirm title={`Hand over #${result.order.number}?`} description="The website marks it completed and emails the customer."
          okText="Handed over" onConfirm={() => handOver(result.order)}>
          <Button type="primary" loading={busy === 'hand'} disabled={!!busy}>已取货 Handed over</Button>
        </Popconfirm>
        <Button onClick={close} disabled={!!busy}>关闭 Close</Button>
      </div>} />}
  </section>
}

// Owner, 2026-11-25: "we should be able to see the name for the delivery orders, as well as searching up the name."
// It looks through the delivery cards the board already has, as staff type: nothing is sent anywhere. Pickups stay
// behind their code.
function NameBox({ board, now }: { board: WebOrdersBoard; now: number }) {
  const [typed, setTyped] = useState('')
  const found = typed.trim() ? nameMatches(board, typed) : []
  return <section className="pc-panel pc-web-code pc-web-name" aria-label="Delivery name">
    <form onSubmit={event => event.preventDefault()}>
      <label htmlFor="web-order-name"><strong>收件人</strong> <small>Delivery name</small></label>
      <Input id="web-order-name" value={typed} onChange={event => setTyped(event.target.value)} autoComplete="off"
        spellCheck={false} maxLength={60} placeholder="例 e.g. Chen" onKeyDown={event => { if (event.key === 'Escape') setTyped('') }} />
      {typed && <Button onClick={() => setTyped('')}>清除 Clear</Button>}
    </form>
    {!board.names_read && <p className="pc-web-flag pc-web-note"><i className="pc-dot pc-dot-warn" aria-hidden="true" />
      网站没有回答 · The website did not send the names. They come back at the next refresh.</p>}
    {typed.trim() && (found.length === 0
      ? <Alert role="status" type="info" showIcon message="没有这个名字的配送订单 · No delivery order on the board has this name. Pickup orders are found by their code." />
      : <div className="pc-web-found" role="list" aria-label="Deliveries found">
        {found.map(({ card, where }) => <div role="listitem" key={card.id}>
          <OrderCard card={card} now={now} extra={<p className="pc-web-reason">{where}</p>} />
        </div>)}
      </div>)}
  </section>
}

// 网站订单 Web orders: the shop computer's order fulfilment screen (owner, 2026-11-19). Its buttons: Ready and Handed
// over on pickups, Packed and its Undo on deliveries (owner, 2026-11-27); everything else is for looking. Never the
// pickup code; of the customer, only each delivery's name (owner, 2026-11-25), read live.
export default function WebOrdersPage() {
  const selected = useAppStore(state => state.selectedStore)
  // The 查看者 Viewer here is the store computer, whose stores are its 店铺电脑 ticks in Users.
  const staff = useHasRole('staff')
  const store = selected && selected.code !== ALL_STORES.code ? selected.code : undefined
  const [board, setBoard] = useState<WebOrdersBoard>()
  const [error, setError] = useState('')
  const [loadedAt, setLoadedAt] = useState(Date.now())
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState(0)
  // The open orders the board last showed, per store choice: one it did not have is new.
  const seen = useRef<{ store?: string; ids: Set<number> } | null>(null)

  useEffect(() => {
    let live = true
    const load = () => getWebOrdersBoard(store).then(next => {
      if (!live) return
      if (seen.current && seen.current.store === store) {
        const fresh = newCards(next, seen.current.ids)
        for (const card of fresh) notification.info({
          key: `web-order-${card.id}`, duration: 0, placement: 'bottomRight',
          message: `新网站订单 New web order #${card.number}`, description: announceText(card),
        })
        if (fresh.length && soundOn()) chime()
      }
      seen.current = { store, ids: new Set(openCards(next).map(card => card.id)) }
      setBoard(next)
      setError('')
      setLoadedAt(Date.now())
    }).catch(cause => { if (live) setError(errorText(cause, 'Unable to load the web orders.')) })
    load()
    const timer = window.setInterval(load, REFRESH_MS)
    return () => { live = false; window.clearInterval(timer) }
  }, [store, attempt])

  const reload = () => setAttempt(value => value + 1)

  async function ready(card: WebOrderCard) {
    setBusy(card.id)
    try {
      stepMessage(await markWebOrderReady(card.id), `#${card.number} is ready. The website emails the customer their pickup code.`,
        `#${card.number} was already ready on the website.`)
    } catch (cause) {
      message.error(errorText(cause, 'The website did not take it. Nothing changed; try again.'))
    } finally {
      setBusy(0)
      reload()
    }
  }

  // Packed moves a delivery to 等快递 and Undo back; the website gets a note only (owner, 2026-11-27).
  async function pack(card: WebOrderCard, undo = false) {
    setBusy(card.id)
    try {
      stepMessage(await (undo ? undoWebOrderPacked : markWebOrderPacked)(card.id),
        undo ? `#${card.number} 回到 待打包 · back to be packed.` : `#${card.number} 已打包 Packed: it waits in 等快递 for the courier.`,
        undo ? `#${card.number} was not packed.` : `#${card.number} was already packed.`)
    } catch (cause) {
      message.error(errorText(cause, 'It was not saved. Nothing changed; try again.'))
    } finally {
      setBusy(0)
      reload()
    }
  }

  const actions = (column: string, card: WebOrderCard) => {
    if (column === 'pick') return <div className="pc-web-actions">
      <Popconfirm title={`#${card.number} 拣好了? Mark it ready?`}
        description="The website emails the customer that it is ready, with their pickup code." okText="Ready"
        onConfirm={() => ready(card)}>
        <Button loading={busy === card.id} disabled={!!busy}>拣好了 Ready</Button>
      </Popconfirm>
    </div>
    if (column === 'pack') return <div className="pc-web-actions">
      <Button loading={busy === card.id} disabled={!!busy} onClick={() => pack(card)}>打包好了 Packed</Button>
    </div>
    if (column === 'booked') return <div className="pc-web-actions">
      <Button type="text" loading={busy === card.id} disabled={!!busy} onClick={() => pack(card, true)}>撤销 Undo</Button>
    </div>
    return undefined
  }

  const header = <PageHeader title="网站订单" sub="Web orders · popcore.ca" />
  if (!board) return <div className="pc-web-orders">
    {header}
    {error ? <Alert role="alert" type="error" showIcon message={error} action={<Button onClick={reload}>Retry</Button>} />
      : <div className="pc-panel"><div className="pc-quiet"><Skeleton active /></div></div>}
  </div>

  const now = loadedAt
  const syncedAt = websiteTime(board.last_run)
  return <div className="pc-web-orders">
    {header}
    {error && <Alert role="alert" type="warning" showIcon message={`${error} The board shows what it last read.`}
      action={<Button size="small" onClick={reload}>Retry</Button>} />}
    {!board.configured ? <Alert type="warning" showIcon message="No website key is set on the server, so no web orders reach POPCORE." />
      : !board.enabled && <Alert type="warning" showIcon message="网站同步已关闭 · The website sync is off: new orders and changes do not reach this board." />}
    {board.stores.length === 0 && <Alert type="info" showIcon
      message={!staff ? `${store ? `${store} 没有勾选 店铺电脑` : '还没有勾选 店铺电脑 的门店'} · An admin ticks 店铺电脑 Store computer for ${store || 'a store'} in Users.`
        : store ? `You have no 门店操作权限 Store operations access for ${store}.` : 'You have no 门店操作权限 Store operations access. An admin grants it under Users.'} />}
    <p className="pc-web-fresh">
      {board.stores.join(' + ') || '—'} · <span>更新 Updated {clock(now)}</span> · <span>网站同步 Website read {clock(syncedAt)}</span> · <span>每 30 秒 every 30 s</span>
    </p>

    <Notifications />

    <div className="pc-web-lookups">
      <CodeBox store={store} onChange={reload} />
      {board.deliveries && <NameBox board={board} now={now} />}
    </div>

    <div className="pc-web-columns">
      {COLUMNS.map(column => {
        const cards = board.columns[column.key]
        return <section key={column.key} className="pc-web-column" aria-label={column.en}>
          <header className="pc-panel-head">
            <h2><span>{column.zh}</span> <small>{column.en}</small></h2>
            <span className="pc-panel-count">{cards.length}</span>
          </header>
          {cards.length === 0 ? <p className="pc-quiet">没有 · None</p> : cards.map(card => <OrderCard key={card.id} card={card} now={now}
            action={actions(column.key, card)} />)}
        </section>
      })}
    </div>

    <Panel zh="需要管理员" en="Needs an admin" label="Needs an admin" count={board.needs_manager.length}
      tone={board.needs_manager.length ? 'warn' : 'ok'}>
      {board.needs_manager.length === 0 ? <p className="pc-quiet">没有 · Nothing waits for an admin.</p>
        : <div className="pc-web-grid">{board.needs_manager.map(card => <OrderCard key={card.id} card={card} now={now}
          extra={<p className="pc-web-reason">{reasonText(card)}</p>} />)}</div>}
      {board.needs_manager.length > 0 && <p className="pc-quiet">处理好后，由管理员在 网站 页重试或关闭 · Once it is sorted out, an admin retries or dismisses the order on the 网站 page.</p>}
    </Panel>

    <details className="pc-panel pc-web-done">
      <summary><strong>今天完成</strong> <small>Done today</small> <span className="pc-panel-count">{board.done_today.length}</span></summary>
      {board.done_today.length === 0 ? <p className="pc-quiet">没有 · None yet today.</p>
        : <div className="pc-web-grid">{board.done_today.map(card => <OrderCard key={card.id} card={card} now={now}
          extra={<p className="pc-web-reason">{{ completed: '已完成 Completed', cancelled: '已取消 Cancelled', refunded: '已退款 Refunded' }[card.status] ?? card.status}
            {card.done_at ? ` · ${clock(websiteTime(card.done_at))}` : ''}</p>} />)}</div>}
    </details>
  </div>
}
