import { Alert, Button, Card, Checkbox, DatePicker, Descriptions, Form, Input, InputNumber, List, Select, Space, Spin, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useRef, useState } from 'react'
import { Link, useBeforeUnload, useSearchParams } from 'react-router-dom'

import { addCashCount, addCashEvent, closeClosing, createClosing, fetchClosing, newRequestKey, returnClosing, submitClosing, updateClosing, type ClosingSession } from '../../api/closing'
import { useHasRole } from '../../auth/useRole'
import { formatCents, parseMoneyToCents } from '../../lib/money'
import { useHistoryReconciliationGuard } from '../../lib/reconciliationNavigation'
import { useAppStore } from '../../store'
import { torontoDate } from '../Dashboard/todayPresentation'

const { Title, Text } = Typography
const denominations = [['10000', '$100'], ['5000', '$50'], ['2000', '$20'], ['1000', '$10'], ['500', '$5'], ['200', '$2'], ['100', '$1'], ['25', '25¢'], ['10', '10¢'], ['5', '5¢']]
const responseStatus = (cause: unknown) => (cause as {response?:{status?:number}})?.response?.status
const inputDollars = (cents: number | null | undefined) => cents == null ? '' : formatCents(cents).replace('$', '')
type PendingMutation = { name: string; key: string; run: (key: string) => Promise<unknown> }

function issueLabel(code: string) {
  const [kind, id] = code.split(':')
  const labels: Record<string,string> = {
    sales_intake_incomplete: 'Sales intake declaration is incomplete.', cash_count_missing: 'A deliberate cash count is required.',
    retained_float_shortfall: 'Counted cash is below the retained float; no removal can be calculated.',
    checkout_unresolved: `Checkout #${id} is still open. Complete or cancel it before closing.`,
    pending_allocation: `Sale #${id} still needs stock allocation.`, cash_payment_unresolved: `Cash payment #${id} has an unknown or unverified amount.`,
    delivery_unresolved: `Delivery #${id} is still open.`, restock_unresolved: `Restock #${id} is still open.`,
    hot_item_count_missing: `Required hot-item count for product #${id} is missing.`, payment_unverified: `Payment #${id} is not verified.`,
    evidence_pending: `Evidence #${id} is pending review.`, evidence_rejected: `Evidence #${id} was rejected.`,
  }
  return labels[kind] || code
}
function Issue({ code }: { code: string }) {
  const [kind, id] = code.split(':'), label = issueLabel(code)
  if (kind === 'checkout_unresolved') return <Link to={`/checkout/${id}`}>{label}</Link>
  if (kind === 'pending_allocation') return <Link to={`/sales/documents/${id}`}>{label}</Link>
  if (kind === 'restock_unresolved') return <Link to={`/restock?session_id=${id}`}>{label}</Link>
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
  }, [closingId, store?.code]) // eslint-disable-line react-hooks/exhaustive-deps

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

  if ((!store || store.code === 'ALL') && !params.has('closing_id')) return <Alert type="info" showIcon message="Select one store before closing a day." />
  if (loading && !session) return <Spin />
  if (params.has('closing_id') && !session) return <Alert type="error" showIcon message={error || 'Unable to resume this closing session.'} />
  const mutableScope = Boolean(store && store.code !== 'ALL' && (!session || store.id === session.store_id))
  const busy = working || refreshNeeded || !!pendingName, closed = session?.status === 'closed'
  const count = session?.latest_cash_count
  const initialCount = count ? { opening_coin: inputDollars(count.opening_coin_cents), retained_coin: inputDollars(count.retained_coin_cents), ...Object.fromEntries(denominations.map(([key]) => [`d_${key}`, count.denomination_counts[key]])) } : undefined

  return <div className="pc-page" style={{ maxWidth: 940, margin: '0 auto' }}>
    <div className="pc-page-heading"><Title level={3}>{closed ? 'Store day closed' : 'Store closing'}</Title><Text type="secondary">{session ? `Store reference ${session.store_id} · ${session.business_date}` : store?.name}</Text></div>
    {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} action={pendingMutation.current ? <Button onClick={() => mutate(pendingMutation.current!.name, pendingMutation.current!.run)}>Retry identical request</Button> : refreshNeeded ? <Button onClick={refresh}>Retry refresh</Button> : undefined} />}
    {session && store && store.code !== 'ALL' && store.id !== session.store_id && <Alert type="warning" showIcon message="Select the closing document's store to make changes." style={{ marginBottom: 16 }} />}
    {session && store && store.code !== 'ALL' && store.id !== session.store_id && <Alert type="warning" showIcon message="Selected store does not match this closing document. Switch to the document store before making changes." style={{ marginBottom: 16 }} />}
    {!session ? <Card><Form layout="vertical" onFinish={start} initialValues={{ business_date: dayjs(torontoDate()) }}><Form.Item name="business_date" label="Business date" rules={[{ required: true }]}><DatePicker style={{ width: '100%' }} /></Form.Item><Button type="primary" htmlType="submit" loading={working}>Start closing</Button></Form></Card> : <>
      <Card><Space wrap><Tag color={closed ? 'green' : session.status === 'submitted' ? 'orange' : 'blue'}>{session.status}</Tag><Text>Version {session.version}</Text><Text>{session.intake_complete ? 'Sales intake declared complete' : 'Sales intake still incomplete'}</Text></Space></Card>
      <Card title="Source completeness" style={{ marginTop: 16 }}>
        {session.status === 'draft' && mutableScope ? <Checkbox checked={Boolean(session.intake_complete)} disabled={busy} onChange={event => { const checked = event.target.checked; mutate('intake', key => updateClosing(session.closing_id, session.version, checked, key)) }}>All completed POS sales and missing transactions for this day have been entered.</Checkbox> : <Text>{session.intake_complete ? 'Sales intake was declared complete.' : 'Sales intake declaration is unavailable.'}</Text>}
        <List size="small" locale={{ emptyText: 'No linked documents for this business date.' }} dataSource={[
          ...(session.source_documents?.sales ?? []).map(item => ({ key: `sale-${item.id}`, node: <Link to={`/sales/documents/${item.id}`}>Sale #{item.id}: {item.status}, {item.allocation_status}, {item.financial_status}</Link> })),
          ...(session.source_documents?.receipts ?? []).map(item => ({ key: `receipt-${item.id}`, node: <Link to={`/goods/receiving?receipt_id=${item.id}`}>Receipt #{item.id}: {item.status}</Link> })),
          ...(session.source_documents?.counts ?? []).map(item => ({ key: `count-${item.id}`, node: <Link to={`/goods/counts?count_id=${item.id}`}>Count #{item.id}: {item.status}</Link> })),
        ]} renderItem={item => <List.Item key={item.key}>{item.node}</List.Item>} />
      </Card>
      <Card title="Cash activity" style={{ marginTop: 16 }}>
        <Descriptions size="small" column={{ xs: 1, sm: 2 }}><Descriptions.Item label="Opening bills">$650.00 fixed float</Descriptions.Item><Descriptions.Item label="Opening cash">{formatCents(session.cash.opening_cash_cents)}</Descriptions.Item><Descriptions.Item label="Verified cash receipts">{formatCents(session.cash.verified_cash_receipts_cents)}</Descriptions.Item><Descriptions.Item label="Expected drawer">{formatCents(session.cash.expected_drawer_cents)}</Descriptions.Item>{Object.entries(session.cash.event_totals_cents).map(([name, value]) => <Descriptions.Item key={name} label={name.replace('_', ' ')}>{formatCents(value)}</Descriptions.Item>)}</Descriptions>
        {session.status === 'draft' && mutableScope && <Space wrap><Select aria-label="Cash event type" disabled={busy} value={cashEventType} options={[['paid_in', 'Paid in'], ['refund', 'Cash refund'], ['payout', 'Paid out']].map(([value, label]) => ({ value, label }))} onChange={setCashEventType} /><Input aria-label="Cash event amount ($)" disabled={busy} prefix="$" inputMode="decimal" value={cashEventAmount} onChange={event => setCashEventAmount(event.target.value)} /><Input aria-label="Cash event reason" disabled={busy} placeholder="Required cash-event reason" value={cashEventReason} onChange={event => setCashEventReason(event.target.value)} /><Button disabled={busy || !cashEventAmount || !cashEventReason.trim()} onClick={saveCashEvent}>Record cash event</Button></Space>}
      </Card>
      <Card title="Cash count" style={{ marginTop: 16 }}>
        {count && <Descriptions size="small" column={{ xs: 1, sm: 2 }}><Descriptions.Item label="Expected">{formatCents(count.expected_cents)}</Descriptions.Item><Descriptions.Item label="Counted">{formatCents(count.counted_cents)}</Descriptions.Item><Descriptions.Item label="Variance">{formatCents(count.variance_cents)}</Descriptions.Item><Descriptions.Item label="Retained float">{formatCents(count.retained_cents)}</Descriptions.Item><Descriptions.Item label="Removal">{count.removal_cents == null ? 'No removal: counted cash is below the retained float.' : formatCents(count.removal_cents)}</Descriptions.Item></Descriptions>}
        {session.status === 'draft' && mutableScope && <Form key={`${session.closing_id}-${count?.revision ?? 0}`} layout="vertical" onFinish={saveCount} initialValues={initialCount}><Space wrap align="start"><Form.Item name="opening_coin" label="Opening coins ($)" rules={[{ required: true }]}><Input prefix="$" inputMode="decimal" disabled={busy} /></Form.Item><Form.Item name="retained_coin" label="Retained coins ($)" rules={[{ required: true }]}><Input prefix="$" inputMode="decimal" disabled={busy} /></Form.Item>{denominations.map(([key, label]) => <Form.Item key={key} name={`d_${key}`} label={`${label} count`} rules={[{ required: true }]}><InputNumber min={0} precision={0} disabled={busy} /></Form.Item>)}</Space><Button type="primary" htmlType="submit" loading={working} disabled={busy}>Save cash count</Button></Form>}
      </Card>
      {isManager && session.tender_totals_cents && <Card title="Tender review" style={{ marginTop: 16 }}><Descriptions size="small" column={{ xs: 1, sm: 2 }}>{Object.entries(session.tender_totals_cents).map(([name, value]) => <Descriptions.Item key={name} label={name.replace('_', ' ')}>{formatCents(value)}</Descriptions.Item>)}</Descriptions>{session.unknown_payment_ids?.length ? <Alert type="warning" showIcon message="Unknown payment amounts" description={session.unknown_payment_ids.map(id => `Payment #${id}`).join(', ')} /> : <Text>No unknown manager-visible payment amounts.</Text>}</Card>}
      {!closed && mutableScope && <Card title={session.status === 'submitted' ? 'Manager review' : 'Blockers and review'} style={{ marginTop: 16 }}>
        {session.hard_blockers === undefined ? <Alert type="info" showIcon message="Manager-only blocker details are not available for this view." /> : session.hard_blockers.length ? <List dataSource={session.hard_blockers} renderItem={code => <List.Item><Issue code={code} /></List.Item>} /> : <Text>No current hard blockers.</Text>}
        {session.status === 'draft' && mutableScope && count && <Space wrap><Button disabled={busy} onClick={refresh}>Refresh sources</Button><Button type="primary" disabled={busy || Boolean(session.hard_blockers?.length)} onClick={() => mutate('submit', key => submitClosing(session, key))}>Submit for manager review</Button></Space>}
        {session.status === 'submitted' && isManager && <><List dataSource={session.review_exceptions ?? []} locale={{ emptyText: 'No review exceptions.' }} renderItem={code => <List.Item><Space direction="vertical" style={{ width: '100%' }}><Issue code={code} /><Input aria-label={`${code} acceptance reason`} disabled={busy} value={exceptionReasons[code] ?? ''} onChange={event => setExceptionReasons({ ...exceptionReasons, [code]: event.target.value })} placeholder="Separate acceptance reason" /></Space></List.Item>} /><Input aria-label="Return-for-changes reason" disabled={busy} value={reviewReason} onChange={event => setReviewReason(event.target.value)} placeholder="Separate reason for returning this closing" /><Space wrap style={{ marginTop: 12 }}><Button disabled={busy} onClick={refresh}>Refresh sources</Button><Button danger disabled={busy || !reviewReason.trim()} onClick={() => mutate('return', key => returnClosing(session, reviewReason.trim(), key))}>Return for changes</Button><Button type="primary" disabled={busy || (session.review_exceptions ?? []).some(code => !exceptionReasons[code]?.trim())} onClick={() => mutate('close', key => closeClosing(session, exceptionReasons, key))}>Close store day</Button></Space></>}
      </Card>}
      {closed && isManager && session.snapshot && <Card title="Immutable closing snapshot" style={{ marginTop: 16 }}>
        <Descriptions size="small" column={{ xs: 1, sm: 2 }}><Descriptions.Item label="Expected">{formatCents(session.snapshot.expected_cents)}</Descriptions.Item><Descriptions.Item label="Counted">{formatCents(session.snapshot.counted_cents)}</Descriptions.Item><Descriptions.Item label="Variance">{formatCents(session.snapshot.variance_cents)}</Descriptions.Item><Descriptions.Item label="Retained float">{formatCents(session.snapshot.retained_cents)}</Descriptions.Item><Descriptions.Item label="Removal">{session.snapshot.removal_cents == null ? 'No removal' : formatCents(session.snapshot.removal_cents)}</Descriptions.Item>{Object.entries(session.snapshot.tender_totals_cents ?? {}).map(([name, value]) => <Descriptions.Item key={name} label={`Saved ${name.replace('_', ' ')}`}>{formatCents(value)}</Descriptions.Item>)}</Descriptions>
        {session.snapshot.unknown_payment_ids?.length ? <Alert type="warning" showIcon message="Saved unknown payment references" description={session.snapshot.unknown_payment_ids.map(id => `Payment #${id}`).join(', ')} /> : <Text>No unknown payments were saved in this closing snapshot.</Text>}
        <List size="small" header="Accepted exceptions" locale={{ emptyText: 'No accepted exceptions.' }} dataSource={Array.isArray(session.snapshot.accepted_exceptions) ? session.snapshot.accepted_exceptions.map(code => ({ code, reason: '' })) : Object.entries(session.snapshot.accepted_exceptions).map(([code, reason]) => ({ code, reason }))} renderItem={item => <List.Item>{issueLabel(item.code)}{item.reason ? ` — ${item.reason}` : ''}</List.Item>} />
      </Card>}
      {closed && isManager && <Card title="Later adjustments" style={{ marginTop: 16 }}><List locale={{ emptyText: 'No later adjustments.' }} dataSource={session.late_adjustments ?? []} renderItem={item => <List.Item><Space direction="vertical"><Text>{item.reason}</Text><Text type="secondary">{item.source_type} {item.source_id}</Text></Space></List.Item>} /></Card>}
    </>}
  </div>
}
