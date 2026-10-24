import { Alert, Button, Checkbox, DatePicker, Form, Input, InputNumber, Select, Spin, Typography } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useBeforeUnload, useSearchParams } from 'react-router-dom'

import { addCashCount, addCashEvent, closeClosing, createClosing, fetchClosing, newRequestKey, returnClosing, submitClosing, updateClosing, type ClosingSession } from '../../api/closing'
import { useHasRole } from '../../auth/useRole'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { formatCents, parseMoneyToCents } from '../../lib/money'
import { useHistoryReconciliationGuard } from '../../lib/reconciliationNavigation'
import { useAppStore } from '../../store'
import { torontoDate } from '../Dashboard/todayPresentation'
import { methodLabel, SUMMARY_METHODS } from '../Sales/dailySummary'
import { SalesTabs } from '../Sales/SalesNav'
import './closing.css'

const { Text } = Typography
const statusNames: Record<string, string> = { draft: '进行中', submitted: '待审核', closed: '已收工' }
const eventNames: Record<string, string> = { paid_in: '放入 Paid in', refund: '现金退款 Cash refund', payout: '支出 Paid out', removal: '取走 Removal' }
const denominations = [['10000', '$100'], ['5000', '$50'], ['2000', '$20'], ['1000', '$10'], ['500', '$5'], ['200', '$2'], ['100', '$1'], ['25', '25¢'], ['10', '10¢'], ['5', '5¢']]
const responseStatus = (cause: unknown) => (cause as {response?:{status?:number}})?.response?.status
const inputDollars = (cents: number | null | undefined) => cents == null ? '' : formatCents(cents).replace('$', '')
type PendingMutation = { name: string; key: string; run: (key: string) => Promise<unknown> }

function issueLabel(code: string, subject?: string) {
  const [kind, id] = code.split(':')
  const labels: Record<string,string> = {
    sales_intake_incomplete: 'Sales intake declaration is incomplete.', cash_count_missing: 'A deliberate cash count is required.',
    retained_float_shortfall: 'Counted cash is below the retained float; no removal can be calculated.',
    checkout_unresolved: `${subject || `Checkout #${id}`} is still open. Complete or cancel it before closing.`,
    checkout_cash_unresolved: `${subject || `Checkout payment #${id}`} needs cash payment review.`,
    pending_allocation: `${subject || `Sale #${id}`} still needs stock allocation.`, cash_payment_unresolved: `${subject || `Cash payment #${id}`} has an unknown or unverified amount.`,
    delivery_unresolved: `${subject || `Delivery #${id}`} is still open.`, restock_unresolved: `${subject || `Restock #${id}`} is still open.`,
    hot_item_count_missing: `Required hot-item count for ${subject || `product #${id}`} is missing.`, payment_unverified: `${subject || `Payment #${id}`} is not verified.`,
    evidence_pending: `${subject || `Evidence #${id}`} is pending review.`, evidence_rejected: `${subject || `Evidence #${id}`} was rejected.`,
    clover_sync_stale: 'Clover’s orders for this day were not checked in the last 10 minutes. Keep Clover connected, then refresh.',
    clover_writeback_pending: `${subject || `Clover order #${id}`} is paid in POPCORE but not marked paid on Clover yet. Open it and retry.`,
    clover_review: `${subject || `Clover order #${id}`} changed on Clover after the sale was recorded. Review it on Clover.`,
  }
  return labels[kind] || code
}
function Issue({ code, target }: { code: string; target?: {href: string; label?: string} }) {
  const label = issueLabel(code, target?.label)
  if (target?.href.startsWith('#')) return <a href={target.href}>{label}</a>
  if (target) return <Link to={target.href}>{label}</Link>
  return <Text>{label}</Text>
}

export default function ClosingPage() {
  const store = useAppStore(state => state.selectedStore), isManager = useHasRole('manager')
  const setSelectedStore = useAppStore(state => state.setSelectedStore)
  const [session, setSession] = useState<ClosingSession | null>(null), [error, setError] = useState('')
  const [loading, setLoading] = useState(false), [working, setWorking] = useState(false), [refreshNeeded, setRefreshNeeded] = useState(false)
  const [reviewReason, setReviewReason] = useState(''), [exceptionReasons, setExceptionReasons] = useState<Record<string,string>>({})
  const [cashEventType, setCashEventType] = useState<'paid_in'|'refund'|'payout'>('paid_in')
  const [cashEventAmount, setCashEventAmount] = useState(''), [cashEventReason, setCashEventReason] = useState('')
  const [params, setParams] = useSearchParams(), pendingMutation = useRef<PendingMutation | null>(null)
  const scope = useRef(store), restoringStore = useRef(false)
  const [pendingName, setPendingName] = useState('')
  const closingId = Number(params.get('closing_id'))
  const phone = useIsPhone()

  useHistoryReconciliationGuard(Boolean(pendingName), 'This closing request is not confirmed. Leave it reconciliation-pending and exit this page?')
  useBeforeUnload(event => { if (pendingName) event.preventDefault() })
  useEffect(() => {
    if (!pendingName) return
    const confirmLink = (event: MouseEvent) => {
      const link = (event.target as HTMLElement).closest('a')
      if (link && link.target !== '_blank' && !window.confirm('This closing request is not confirmed. Leave it reconciliation-pending and exit this page?')) event.preventDefault()
    }
    document.addEventListener('click', confirmLink, true)
    return () => document.removeEventListener('click', confirmLink, true)
  }, [pendingName])

  async function load(id: number, signal?: AbortSignal) {
    const current = await fetchClosing(id, signal); setSession(current); setRefreshNeeded(false); return current
  }
  useEffect(() => {
    if (restoringStore.current && store?.code === scope.current?.code) { restoringStore.current = false; return }
    if (pendingMutation.current && store?.code !== scope.current?.code && scope.current) {
      restoringStore.current = true; setError('Resolve or acknowledge the unconfirmed closing request before changing stores.'); setSelectedStore(scope.current); return
    }
    scope.current = store
    setSession(null); setError(''); setRefreshNeeded(false); pendingMutation.current = null; setPendingName('')
    if (!params.has('closing_id')) return
    if (!Number.isInteger(closingId) || closingId < 1) { setError('Closing reference is invalid.'); return }
    const controller = new AbortController(); setLoading(true)
    load(closingId, controller.signal).catch(cause => { if ((cause as {code?:string})?.code !== 'ERR_CANCELED') setError('Unable to resume this closing session.') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [closingId, store?.code, isManager]) // eslint-disable-line react-hooks/exhaustive-deps

  async function refresh() {
    if (!session) return false
    try { await load(session.closing_id); setError(''); return true }
    catch { setRefreshNeeded(true); setError('Saved; refresh needed before another closing action.'); return false }
  }
  async function mutate(name: string, run: (key: string) => Promise<unknown>) {
    if (working || refreshNeeded || (pendingMutation.current && pendingMutation.current.name !== name)) return
    const intent = pendingMutation.current ?? { name, key: newRequestKey(), run }; pendingMutation.current = intent
    setWorking(true); setPendingName(name); setError('')
    try {
      await intent.run(intent.key); pendingMutation.current = null; setPendingName(''); await refresh()
    } catch (cause) {
      const status = responseStatus(cause)
      if (status === 409) {
        pendingMutation.current = null; setPendingName(''); setExceptionReasons({}); setReviewReason('')
        const refreshed = await refresh()
        if (refreshed) setError('Closing sources changed. Review the refreshed facts before creating a new request.')
      } else if (status && status < 500) {
        pendingMutation.current = null; setPendingName(''); setError((cause as {_serverMessage?:string})?._serverMessage || 'The closing request was rejected. Review the saved facts.')
      } else setError('The closing result was not confirmed. Retry sends the identical request and payload.')
    } finally { setWorking(false) }
  }
  async function start(values: { business_date: dayjs.Dayjs }) {
    if (!store || store.code === 'ALL') return
    await mutate('create', async key => {
      const created = await createClosing(store.id, values.business_date.format('YYYY-MM-DD'), key)
      setSession(created); setParams({ closing_id: String(created.closing_id) })
    })
  }
  async function saveCount(values: Record<string, string|number>) {
    if (!session) return
    try {
      const opening = parseMoneyToCents(String(values.opening_coin ?? '')), retained = parseMoneyToCents(String(values.retained_coin ?? ''))
      if (opening == null || retained == null) throw new RangeError('Opening and retained coins are required.')
      const counts = Object.fromEntries(denominations.map(([key]) => [key, values[`d_${key}`] as number]))
      await mutate('cash-count', key => addCashCount(session.closing_id, session, opening, retained, counts, key))
    } catch (cause) { setError((cause as Error).message) }
  }
  async function saveCashEvent() {
    if (!session || !cashEventReason.trim()) return
    try {
      const amount = parseMoneyToCents(cashEventAmount)
      if (!amount) throw new RangeError('Enter a cash-event amount greater than zero.')
      await mutate('cash-event', key => addCashEvent(session.closing_id, session, cashEventType, amount, cashEventReason.trim(), key))
    } catch (cause) { setError((cause as Error).message) }
  }

  const header = (title: string, sub?: ReactNode, actions?: ReactNode) => <PageHeader
    title={<>收工<span className="pc-sr"> {title}</span></>} sub={sub} actions={actions} />
  if ((!store || store.code === 'ALL') && !params.has('closing_id')) return <div className="pc-close">{header('Store closing', phone ? undefined : 'Closing')}{isManager && <SalesTabs />}<p className="pc-notice">Select one store before closing a day.</p></div>
  if (loading && !session) return <Spin />
  if (params.has('closing_id') && !session) return <Alert type="error" showIcon message={error || 'Unable to resume this closing session.'} />
  const mutableScope = Boolean(store && store.code !== 'ALL' && (!session || store.id === session.store_id))
  const busy = working || refreshNeeded || !!pendingName, closed = session?.status === 'closed'
  const count = session?.latest_cash_count
  const initialCount = count ? { opening_coin: inputDollars(count.opening_coin_cents), retained_coin: inputDollars(count.retained_coin_cents), ...Object.fromEntries(denominations.map(([key]) => [`d_${key}`, count.denomination_counts[key]])) } : undefined

  const visibleBlockers = isManager ? session?.hard_blockers : session?.actionable_blockers
  const storeLine = session ? `${session.store_name || (store?.id === session.store_id ? store.name : `Store #${session.store_id}`)} · ${session.business_date}` : store?.name
  const reviewing = Boolean(session && session.status === 'submitted' && isManager && mutableScope && !closed)
  const canSubmit = Boolean(session && session.status === 'draft' && mutableScope && count && !closed)
  const refreshButton = <Button disabled={busy} onClick={refresh}><Icon name="refresh" size={16} />刷新来源 Refresh sources</Button>
  const submitButton = session && <Button type="primary" disabled={busy || Boolean(session.hard_blockers?.length)} onClick={() => mutate('submit', key => submitClosing(session, key))}>提交给经理 Submit for manager review</Button>
  const returnButton = session && <Button danger disabled={busy || !reviewReason.trim()} onClick={() => mutate('return', key => returnClosing(session, reviewReason.trim(), key))}>退回修改 Return for changes</Button>
  const closeButton = session && <Button type="primary" disabled={busy || (session.review_exceptions ?? []).some(code => !exceptionReasons[code]?.trim())} onClick={() => mutate('close', key => closeClosing(session, exceptionReasons, key))}>确认收工 Close store day</Button>
  // Desktop: the step's actions sit in the header (Figma Closing review); phone: under the review list.
  const stepActions = reviewing ? <>{refreshButton}{returnButton}{closeButton}</> : canSubmit ? <>{refreshButton}{submitButton}</> : null
  const sub = session ? <><span>{storeLine}</span> · <span className="pc-close-status">{statusNames[session.status] || session.status} <small>{session.status}</small></span> · 第 {session.version} 版 <small>Version {session.version}</small></> : storeLine

  if (!session) return <div className="pc-close">
    {header('Store closing', phone ? 'Close the day' : <>Closing{store?.name ? ` · ${store.name}` : ''}</>)}
    {isManager && <SalesTabs />}
    {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} action={pendingMutation.current ? <Button onClick={() => mutate(pendingMutation.current!.name, pendingMutation.current!.run)}>Retry identical request</Button> : undefined} />}
    <section className="pc-panel pc-close-start" aria-label="Start closing">
      <header className="pc-panel-head"><h2><span>开始收工</span> <small>Start the closing for one business day</small></h2></header>
      <Form layout="vertical" onFinish={start} initialValues={{ business_date: dayjs(torontoDate()) }}>
        <Form.Item name="business_date" label="Business date" rules={[{ required: true }]}><DatePicker style={{ width: '100%' }} /></Form.Item>
        <Button type="primary" htmlType="submit" loading={working}>开始收工 Start closing</Button>
      </Form>
    </section>
  </div>

  const steps = [
    { id: 'closing-sources', zh: '核对', en: 'Sources', done: Boolean(session.intake_complete) },
    { id: 'closing-cash-count', zh: '点钱', en: 'Count cash', done: Boolean(count) },
    { id: 'closing-review', zh: session.status === 'draft' || !isManager ? '提交' : '经理审核', en: session.status === 'draft' || !isManager ? 'Submit' : 'Manager review', done: closed },
  ]
  const current = closed ? -1 : session.status !== 'draft' ? 2 : steps.findIndex(step => !step.done)
  const sources = <section id="closing-sources" className="pc-panel" aria-label="Sources">
    <PanelHead zh="今天的单据" en="Source completeness" note={session.intake_complete ? '已确认全部录入' : undefined} count={(session.source_documents?.sales.length ?? 0) + (session.source_documents?.receipts.length ?? 0) + (session.source_documents?.counts.length ?? 0)} />
    <div className="pc-close-intake">
      {session.status === 'draft' && mutableScope ? <Checkbox checked={Boolean(session.intake_complete)} disabled={busy} onChange={event => { const checked = event.target.checked; mutate('intake', key => updateClosing(session.closing_id, session.version, checked, key)) }}>
        <span className="pc-close-intake-text">今天所有已完成的买单和漏记的交易都已录入。<small>All completed POS sales and missing transactions for this day have been entered.</small></span>
      </Checkbox> : <p className="pc-quiet">{session.intake_complete ? 'Sales intake was declared complete.' : 'Sales intake declaration is unavailable.'}</p>}
    </div>
    <ul className="pc-close-docs">
      {(session.source_documents?.sales ?? []).map(item => <li key={`sale-${item.id}`}><Link to={`/sales/documents/${item.id}`}><span><strong>销售 Sale #{item.id}</strong><small>{item.allocation_status} · {item.financial_status}</small></span><em>{item.status}</em></Link></li>)}
      {(session.source_documents?.receipts ?? []).map(item => <li key={`receipt-${item.id}`}><Link to={`/goods/receiving?receipt_id=${item.id}`}><span><strong>收货 Receipt #{item.id}</strong></span><em>{item.status}</em></Link></li>)}
      {(session.source_documents?.counts ?? []).map(item => <li key={`count-${item.id}`}><Link to={`/goods/counts?count_id=${item.id}`}><span><strong>盘点 Count #{item.id}</strong></span><em>{item.status}</em></Link></li>)}
      {!session.source_documents?.sales.length && !session.source_documents?.receipts.length && !session.source_documents?.counts.length && <li className="pc-quiet">No linked documents for this business date.</li>}
    </ul>
  </section>
  const cashCount = <section id="closing-cash-count" className="pc-panel" aria-label="Cash count">
    <PanelHead zh="点钱" en="Cash count" note={count ? `第 ${count.revision} 次` : undefined} count={count ? formatCents(count.counted_cents) : undefined} />
    {session.status === 'draft' && mutableScope ? <Form key={`${session.closing_id}-${count?.revision ?? 0}`} layout="vertical" onFinish={saveCount} initialValues={initialCount} className="pc-close-count-form">
      <DenominationRows busy={busy} />
      <div className="pc-close-coins">
        <Form.Item name="opening_coin" label="开店硬币 Opening coins ($)" rules={[{ required: true }]}><Input prefix="$" inputMode="decimal" disabled={busy} /></Form.Item>
        <Form.Item name="retained_coin" label="留下硬币 Retained coins ($)" rules={[{ required: true }]}><Input prefix="$" inputMode="decimal" disabled={busy} /></Form.Item>
      </div>
      <CountedBar busy={busy} working={working} />
    </Form> : count ? <SavedCount count={count} /> : <p className="pc-quiet">No cash count was saved.</p>}
  </section>
  const cashActivity = <section className="pc-panel" aria-label="Cash activity">
    <PanelHead zh="现金" en="Cash activity" />
    <dl className="pc-close-rows">
      <Row label="钱箱底 Opening bills · 固定" value="$650.00 fixed float" />
      <Row label="开店现金 Opening cash" value={formatCents(session.cash.opening_cash_cents)} />
      <Row label="现金收款 Verified cash receipts" value={formatCents(session.cash.verified_cash_receipts_cents)} />
      {Object.entries(session.cash.event_totals_cents).map(([name, value]) => <Row key={name} label={eventNames[name] || name.replace('_', ' ')} value={formatCents(value)} />)}
      <Row strong label="应有 Expected drawer" value={formatCents(session.cash.expected_drawer_cents)} />
      {count && <>
        <Row label="点到 Counted" value={formatCents(count.counted_cents)} />
        <Row label="差额 Variance" value={formatCents(count.variance_cents)} tone={count.variance_cents < 0 ? 'warn' : undefined} />
        <Row label="留底 Retained float" value={formatCents(count.retained_cents)} />
        <Row label="取走 Removal" value={count.removal_cents == null ? 'No removal: counted cash is below the retained float.' : formatCents(count.removal_cents)} />
      </>}
    </dl>
    {session.status === 'draft' && mutableScope && <div className="pc-close-event">
      <p>记一笔现金 <small>Add cash event</small></p>
      <div className="pc-close-event-row">
        <Select aria-label="Cash event type" disabled={busy} value={cashEventType} options={[['paid_in', '放入 Paid in'], ['refund', '现金退款 Cash refund'], ['payout', '支出 Paid out']].map(([value, label]) => ({ value, label }))} onChange={setCashEventType} />
        <Input aria-label="Cash event amount ($)" disabled={busy} prefix="$" inputMode="decimal" placeholder="金额 Amount" value={cashEventAmount} onChange={event => setCashEventAmount(event.target.value)} />
      </div>
      <div className="pc-close-event-row">
        <Input aria-label="Cash event reason" disabled={busy} placeholder="原因 Required reason" value={cashEventReason} onChange={event => setCashEventReason(event.target.value)} />
        <Button disabled={busy || !cashEventAmount || !cashEventReason.trim()} onClick={saveCashEvent}>记录 Record cash event</Button>
      </div>
    </div>}
  </section>
  const tenders = isManager && session.tender_totals_cents && <section className="pc-panel" aria-label="Tender review">
    <PanelHead zh="付款方式" en="Tender review" />
    <dl className="pc-close-rows">{tenderOrder(session.tender_totals_cents).map(([name, value]) => { const label = methodLabel(name); return <Row key={name} label={`${label.zh} ${label.en}`.trim()} value={formatCents(value)} /> })}</dl>
    {session.unknown_payment_ids?.length ? <p className="pc-notice pc-close-inline"><strong>Unknown payment amounts</strong> {session.unknown_payment_ids.map(id => `Payment #${id}`).join(', ')}</p> : <p className="pc-quiet">No unknown manager-visible payment amounts.</p>}
  </section>
  const review = !closed && mutableScope && <section id="closing-review" className="pc-panel" aria-label={session.status === 'submitted' ? 'Manager review' : 'Blockers and review'}>
    <PanelHead zh={session.status === 'submitted' ? '审核事项' : '还需处理'} en={session.status === 'submitted' ? 'Review exceptions' : 'Blockers and review'} count={(visibleBlockers ?? []).length + (reviewing ? (session.review_exceptions ?? []).length : 0) || undefined} />
    <ul className="pc-close-issues">
      {(visibleBlockers ?? []).map(code => <li key={code}><i className="pc-dot pc-dot-warn" aria-hidden="true" /><Issue code={code} target={session.issue_targets?.[code]} /></li>)}
      {reviewing && (session.review_exceptions ?? []).map(code => <li key={`x-${code}`} className="pc-close-exception"><i className="pc-dot pc-dot-warn" aria-hidden="true" /><div><Issue code={code} target={session.issue_targets?.[code]} /><Input aria-label={`${code} acceptance reason`} disabled={busy} value={exceptionReasons[code] ?? ''} onChange={event => setExceptionReasons({ ...exceptionReasons, [code]: event.target.value })} placeholder="接受原因 Separate acceptance reason" /></div></li>)}
      {reviewing && !(session.review_exceptions ?? []).length && <li className="pc-quiet">No review exceptions.</li>}
      {!(visibleBlockers ?? []).length && isManager && session.hard_blockers !== undefined && <li className="pc-quiet">No current hard blockers.</li>}
    </ul>
    {!isManager && <p className="pc-close-note">A manager reviews any remaining payment or evidence blockers.</p>}
    {reviewing && <div className="pc-close-return"><Input aria-label="Return-for-changes reason" disabled={busy} value={reviewReason} onChange={event => setReviewReason(event.target.value)} placeholder="退回原因 Separate reason for returning this closing" /></div>}
    {phone && stepActions && <div className="pc-close-actions">{stepActions}</div>}
  </section>
  const snapshot = closed && isManager && session.snapshot && <section className="pc-panel" aria-label="Closing snapshot">
    <PanelHead zh="收工快照" en="Immutable closing snapshot" />
    <dl className="pc-close-rows">
      <Row label="应有 Expected" value={formatCents(session.snapshot.expected_cents)} />
      <Row label="点到 Counted" value={formatCents(session.snapshot.counted_cents)} />
      <Row label="差额 Variance" value={formatCents(session.snapshot.variance_cents)} tone={session.snapshot.variance_cents < 0 ? 'warn' : undefined} />
      <Row label="留底 Retained float" value={formatCents(session.snapshot.retained_cents)} />
      <Row label="取走 Removal" value={session.snapshot.removal_cents == null ? 'No removal' : formatCents(session.snapshot.removal_cents)} />
      {Object.entries(session.snapshot.tender_totals_cents ?? {}).map(([name, value]) => <Row key={name} label={`Saved ${name.replace('_', ' ')}`} value={formatCents(value)} />)}
    </dl>
    {session.snapshot.unknown_payment_ids?.length ? <p className="pc-notice pc-close-inline"><strong>Saved unknown payment references</strong> <span>{session.snapshot.unknown_payment_ids.map(id => `Payment #${id}`).join(', ')}</span></p> : <p className="pc-quiet">No unknown payments were saved in this closing snapshot.</p>}
    <h3 className="pc-close-subhead">Accepted exceptions</h3>
    <ul className="pc-close-issues">{(Array.isArray(session.snapshot.accepted_exceptions) ? session.snapshot.accepted_exceptions.map(code => ({ code, reason: '' })) : Object.entries(session.snapshot.accepted_exceptions).map(([code, reason]) => ({ code, reason })))
      .map(item => <li key={item.code}>{issueLabel(item.code)}{item.reason ? ` — ${item.reason}` : ''}</li>)}
      {!(Array.isArray(session.snapshot.accepted_exceptions) ? session.snapshot.accepted_exceptions.length : Object.keys(session.snapshot.accepted_exceptions).length) && <li className="pc-quiet">No accepted exceptions.</li>}
    </ul>
  </section>
  const later = closed && isManager && <section className="pc-panel" aria-label="Later adjustments">
    <PanelHead zh="之后的调整" en="Later adjustments" />
    <ul className="pc-close-issues">{(session.late_adjustments ?? []).map(item => <li key={item.id} className="pc-close-later"><strong>{item.reason}</strong><small>{item.source_type} {item.source_id}</small></li>)}
      {!(session.late_adjustments ?? []).length && <li className="pc-quiet">No later adjustments.</li>}</ul>
  </section>
  const note = <p className="pc-close-note">Each exception needs its own reason. Closing saves an unchangeable snapshot; later adjustments are listed separately.</p>

  return <div className={phone ? 'pc-close pc-close-phone' : 'pc-close'}>
    {header(closed ? 'Store day closed' : 'Store closing', phone ? <>Close the day · {session.business_date}</> : <>Closing · {sub}</>, !phone ? stepActions : undefined)}
    {isManager && <SalesTabs />}
    {phone && <p className="pc-close-meta">{sub}</p>}
    {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} action={pendingMutation.current ? <Button onClick={() => mutate(pendingMutation.current!.name, pendingMutation.current!.run)}>Retry identical request</Button> : refreshNeeded ? <Button onClick={refresh}>Retry refresh</Button> : undefined} />}
    {store && store.code !== 'ALL' && store.id !== session.store_id && <p className="pc-notice">Select the closing document's store to make changes.</p>}
    {store && store.code !== 'ALL' && store.id !== session.store_id && <p className="pc-notice">Selected store does not match this closing document. Switch to the document store before making changes.</p>}
    {closed && <p className="pc-close-closed"><Icon name="check" size={16} />已收工 · 这一天的收工已保存，不能再改</p>}
    <nav className="pc-close-steps" aria-label="Closing steps">
      {steps.map((step, index) => <a key={step.id} href={`#${step.id}`} aria-current={index === current ? 'step' : undefined} className={step.done ? 'pc-close-step-done' : undefined}>
        <b aria-hidden="true">{step.done ? <Icon name="check" size={12} /> : index + 1}</b>{step.zh} <span>{step.en}</span>
      </a>)}
    </nav>
    {phone ? <div className="pc-close-stack">{sources}{cashCount}{cashActivity}{tenders}{review}{snapshot}{later}</div>
      : <div className="pc-close-grid">
        <div>{cashCount}{sources}</div>
        <div>{cashActivity}</div>
        <div>{tenders}{review}{snapshot}{later}{(reviewing || closed) && note}</div>
      </div>}
  </div>
}

function tenderOrder(totals: Record<string, number>) {
  const rank = (name: string) => { const index = SUMMARY_METHODS.findIndex(([key]) => key === name); return index < 0 ? SUMMARY_METHODS.length : index }
  return Object.entries(totals).sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
}

function PanelHead({ zh, en, note, count }: { zh: string; en: string; note?: string; count?: ReactNode }) {
  return <header className="pc-panel-head"><h2><span>{zh}</span> <small>{en}</small>{note && <small className="pc-close-head-note"> · {note}</small>}</h2>{count !== undefined && <span className="pc-panel-count">{count}</span>}</header>
}

function Row({ label, value, strong, tone }: { label: ReactNode; value: ReactNode; strong?: boolean; tone?: 'warn' }) {
  return <div className={strong ? 'pc-close-row pc-close-row-strong' : 'pc-close-row'}><dt>{label}</dt><dd className={tone === 'warn' ? 'pc-close-warn' : undefined}>{value}</dd></div>
}

// Figma 点钱: one row per note or coin with − / + and the row subtotal; the total updates as it is typed.
function DenominationRows({ busy }: { busy: boolean }) {
  const form = Form.useFormInstance()
  const values = Form.useWatch([], form) as Record<string, unknown> | undefined
  const bump = (key: string, delta: number) => {
    const name = `d_${key}`, current = Number(form.getFieldValue(name)) || 0
    form.setFieldValue(name, Math.max(0, current + delta))
  }
  return <div className="pc-close-denoms" role="group" aria-label="Denominations">
    <div className="pc-close-denom pc-close-denom-head" aria-hidden="true"><span>面额</span><span>张 / 枚 Count</span><span>小计</span></div>
    {denominations.map(([key, label]) => {
      const pieces = Number(values?.[`d_${key}`])
      return <div key={key} className="pc-close-denom">
        <span>{label}</span>
        <div className="pc-close-stepper">
          <button type="button" aria-label={`Fewer ${label}`} disabled={busy} onClick={() => bump(key, -1)}>−</button>
          <Form.Item name={`d_${key}`} rules={[{ required: true, message: `${label} count is required` }]}><InputNumber aria-label={`${label} count`} min={0} precision={0} controls={false} inputMode="numeric" disabled={busy} /></Form.Item>
          <button type="button" aria-label={`More ${label}`} disabled={busy} onClick={() => bump(key, 1)}>+</button>
        </div>
        <span className="pc-close-subtotal">{Number.isFinite(pieces) && values?.[`d_${key}`] != null ? formatCents(pieces * Number(key)) : '—'}</span>
      </div>
    })}
  </div>
}

function CountedBar({ busy, working }: { busy: boolean; working: boolean }) {
  const values = Form.useWatch([], Form.useFormInstance()) as Record<string, unknown> | undefined
  const total = denominations.reduce((sum, [key]) => sum + (Number(values?.[`d_${key}`]) || 0) * Number(key), 0)
  return <div className="pc-close-counted">
    <span>点到 Counted <strong>{formatCents(total)}</strong></span>
    <Button type="primary" htmlType="submit" loading={working} disabled={busy}>保存点钱 Save cash count</Button>
  </div>
}

function SavedCount({ count }: { count: NonNullable<ClosingSession['latest_cash_count']> }) {
  return <div className="pc-close-denoms">
    <div className="pc-close-denom pc-close-denom-head"><span>面额</span><span>张 / 枚</span><span>小计</span></div>
    {denominations.map(([key, label]) => <div key={key} className="pc-close-denom"><span>{label}</span><span>{count.denomination_counts[key] ?? '—'}</span><span className="pc-close-subtotal">{formatCents((count.denomination_counts[key] ?? 0) * Number(key))}</span></div>)}
    <div className="pc-close-denom pc-close-denom-coins"><span>硬币</span><span>开店 {formatCents(count.opening_coin_cents)} · 留下 {formatCents(count.retained_coin_cents)}</span><span /></div>
  </div>
}
