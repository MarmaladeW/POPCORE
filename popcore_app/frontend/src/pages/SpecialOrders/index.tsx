import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Empty, Spin, Tabs } from 'antd'

import client from '../../api/client'
import { useHasRole } from '../../auth/useRole'
import { formatCents, parseMoneyToCents } from '../../lib/money'
import { useAppStore } from '../../store'
import type { SpecialOrder } from './specialOrders'
import { paymentState } from './specialOrders'
import './SpecialOrders.css'

type Status = 'open' | 'completed'
type Mutation = {
  method: 'post' | 'patch'
  url: string
  body: Record<string, unknown>
  key: string
  kind: 'create' | 'payment' | 'complete' | 'correct'
}
type Employee = { auth0_id: string; name?: string; email?: string }
type Tender = 'cash' | 'card' | 'e_transfer' | 'wechat' | 'alipay'

const tenders: { value: Tender; label: string }[] = [
  { value: 'cash', label: 'Cash' },
  { value: 'card', label: 'Card' },
  { value: 'e_transfer', label: 'E-transfer' },
  { value: 'wechat', label: 'WeChat Pay' },
  { value: 'alipay', label: 'Alipay' },
]

const tenderLabel = (value: Tender | null) =>
  tenders.find(tender => tender.value === value)?.label || 'Not recorded'

const errorText = (error: unknown, fallback: string) =>
  (error as { _serverMessage?: string })?._serverMessage || fallback

const dateTime = (value: string | null) => value
  ? new Intl.DateTimeFormat('en-CA', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  : '—'

const localDateTime = (value: string | null) => value
  ? new Date(new Date(value).getTime() - new Date(value).getTimezoneOffset() * 60000).toISOString().slice(0, 16)
  : ''

export default function SpecialOrdersPage() {
  const admin = useHasRole('admin')
  const stores = useAppStore(state => state.stores).filter(store => store.code !== 'ALL')
  const selectedStore = useAppStore(state => state.selectedStore)
  const [status, setStatus] = useState<Status>('open')
  const [orders, setOrders] = useState<SpecialOrder[]>([])
  const [selected, setSelected] = useState<SpecialOrder>()
  const [employees, setEmployees] = useState<Employee[]>([])
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef<Mutation>()
  const paymentForm = useRef<HTMLFormElement>(null)

  const load = async (nextStatus = status) => {
    setLoading(true); setError('')
    try {
      const data = (await client.get<SpecialOrder[]>('/special-orders', { params: { status: nextStatus } })).data
      setOrders(data)
      setSelected(current => data.find(order => order.id === current?.id) || data[0])
    } catch (cause) {
      setError(errorText(cause, 'Unable to load special orders.'))
    } finally { setLoading(false) }
  }

  useEffect(() => { void load(status) }, [status]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (admin) client.get<Employee[]>('/schedule/employees').then(response => setEmployees(response.data)).catch(() => {})
  }, [admin])

  const accept = (order: SpecialOrder, kind: Mutation['kind']) => {
    setSelected(order)
    setOrders(current => order.status === status
      ? [order, ...current.filter(item => item.id !== order.id)]
      : current.filter(item => item.id !== order.id))
    if (kind === 'create') setCreating(false)
    if (kind === 'payment') paymentForm.current?.reset()
  }

  const mutate = async (next?: Omit<Mutation, 'key'>) => {
    if (saving) return
    if (!pending.current && next) pending.current = { ...next, key: crypto.randomUUID() }
    if (!pending.current) return
    setSaving(true); setError('')
    try {
      const intent = pending.current
      const response = await client.request<SpecialOrder>({
        method: intent.method, url: intent.url, data: intent.body,
        headers: { 'Idempotency-Key': intent.key }, timeout: 15000,
      })
      pending.current = undefined
      accept(response.data, intent.kind)
    } catch (cause) {
      const responseStatus = (cause as { response?: { status?: number } })?.response?.status
      if (responseStatus && responseStatus < 500) pending.current = undefined
      setError(errorText(cause, 'Unable to confirm this request. Retry to check its result.'))
    } finally { setSaving(false) }
  }

  const create = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    try {
      const total = parseMoneyToCents(String(data.get('total') || ''))
      const paid = parseMoneyToCents(String(data.get('paid') || ''))
      const pickupStoreId = Number(data.get('pickup_store_id'))
      const initialTender = String(data.get('initial_tender') || '')
      if (total == null || total <= 0) throw new RangeError('Enter a total price greater than zero.')
      if (paid == null || paid < 0 || paid > total) throw new RangeError('Amount paid must be between zero and the total price.')
      if (!stores.some(store => store.id === pickupStoreId)) throw new RangeError('Choose a pickup location.')
      if (paid > 0 && !tenders.some(tender => tender.value === initialTender)) {
        throw new RangeError('Choose the initial payment method.')
      }
      void mutate({
        method: 'post', url: '/special-orders', kind: 'create',
        body: {
          customer_name: data.get('customer_name'), customer_phone: data.get('customer_phone'),
          item_description: data.get('item_description'), total_cents: total,
          initial_paid_cents: paid, pickup_store_id: pickupStoreId,
          initial_tender: paid > 0 ? initialTender : null,
        },
      })
    } catch (cause) { setError((cause as Error).message) }
  }

  const addPayment = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selected) return
    try {
      const data = new FormData(event.currentTarget)
      const amount = parseMoneyToCents(String(data.get('amount') || ''))
      const tender = String(data.get('tender') || '')
      if (amount == null || amount <= 0 || amount > selected.remaining_cents) {
        throw new RangeError('Enter an amount within the remaining balance.')
      }
      if (!tenders.some(option => option.value === tender)) {
        throw new RangeError('Choose a payment method.')
      }
      void mutate({
        method: 'post', url: `/special-orders/${selected.id}/payments`, kind: 'payment',
        body: { expected_version: selected.version, amount_cents: amount, tender },
      })
    } catch (cause) { setError((cause as Error).message) }
  }

  return <main className="so-page">
    <header className="so-heading">
      <div><p className="so-eyebrow">Customer requests</p><h1>Special Orders</h1><p>Track unique requests, separate payments, and customer pickup.</p></div>
      <Button type="primary" size="large" onClick={() => setCreating(value => !value)}>
        {creating ? 'Close form' : 'New special order'}
      </Button>
    </header>

    {error && <Alert role="alert" type="error" showIcon message={error} action={pending.current
      ? <Button onClick={() => void mutate()} disabled={saving}>Retry</Button>
      : <Button onClick={() => void load()} disabled={loading}>Refresh</Button>} />}

    {creating && <form className="so-form" onSubmit={create}>
      <h2>New special order</h2>
      <div className="so-fields">
        <label>Customer name<input name="customer_name" required maxLength={120} /></label>
        <label>Phone number<input name="customer_phone" type="tel" required maxLength={80} /></label>
        <label className="so-wide">Item description<textarea name="item_description" required maxLength={500} rows={3} /></label>
        <label>Pickup location<select name="pickup_store_id" defaultValue={selectedStore?.code !== 'ALL' ? String(selectedStore?.id || '') : ''} required><option value="" disabled>Select location</option>{stores.map(store => <option key={store.id} value={store.id}>{store.name} · {store.code}</option>)}</select></label>
        <label>Total price<input name="total" inputMode="decimal" placeholder="0.00" required /></label>
        <label>Amount paid now<input name="paid" inputMode="decimal" defaultValue="0.00" required /></label>
        <label>Initial payment method<select name="initial_tender" defaultValue=""><option value="">Select if taking payment</option>{tenders.map(tender => <option key={tender.value} value={tender.value}>{tender.label}</option>)}</select></label>
      </div>
      <div className="so-form-actions"><Button onClick={() => setCreating(false)}>Cancel</Button><Button type="primary" htmlType="submit" loading={saving}>Create order</Button></div>
    </form>}

    <Tabs activeKey={status} onChange={key => setStatus(key as Status)} items={[
      { key: 'open', label: 'Open' }, { key: 'completed', label: 'Completed' },
    ]} />

    <Spin spinning={loading}>
      <div className="so-workspace">
        <nav className="so-list" aria-label={`${status === 'open' ? 'Open' : 'Completed'} special orders`}>
          {!orders.length && !loading && <Empty description={`No ${status} special orders`} />}
          {orders.map(order => <button type="button" key={order.id} className={selected?.id === order.id ? 'active' : ''} onClick={() => setSelected(order)}>
            <span><strong>{order.customer_name}</strong><b>{formatCents(order.total_cents)}</b></span>
            <em>{order.item_description}</em>
            <small>{order.created_by_name} · {dateTime(order.created_at)}</small>
            <i>{paymentState(order) === 'paid' ? 'Paid in full' : `${formatCents(order.remaining_cents)} due`}</i>
          </button>)}
        </nav>

        <section className="so-detail">
          {!selected ? <Empty description="Select an order" /> : <>
            <header><div><p className="so-eyebrow">Order #{selected.id}</p><h2>{selected.customer_name}</h2><p>{selected.item_description}</p></div><span className={`so-status ${selected.status}`}>{selected.status}</span></header>
            <dl className="so-facts">
              <div><dt>Phone</dt><dd>{selected.customer_phone || <span className="so-private">Phone hidden unless you are today's Cashier or a manager.</span>}</dd></div>
              <div><dt>Pickup location</dt><dd>{selected.pickup_store_name && selected.pickup_store_code ? `${selected.pickup_store_name} · ${selected.pickup_store_code}` : 'Not recorded'}</dd></div>
              <div><dt>Created</dt><dd>{dateTime(selected.created_at)} by {selected.created_by_name}</dd></div>
              {selected.completed_at && <div><dt>Customer received</dt><dd>{dateTime(selected.completed_at)} by {selected.completed_by_name}</dd></div>}
            </dl>
            <div className="so-money">
              <div><span>Total</span><strong>{formatCents(selected.total_cents)}</strong></div>
              <div><span>Paid</span><strong>{formatCents(selected.paid_cents)}</strong></div>
              <div className="remaining"><span>Balance</span><strong>{selected.remaining_cents ? `${formatCents(selected.remaining_cents)} remaining` : 'Paid in full'}</strong></div>
            </div>

            <section className="so-payments"><h3>Payments</h3>{selected.payments.length
              ? selected.payments.map(payment => <div key={payment.id}><span><b>{tenderLabel(payment.tender)}</b><small>{dateTime(payment.paid_at)}</small></span><strong>{formatCents(payment.amount_cents)}</strong></div>)
              : <p>No payments recorded.</p>}</section>

            {selected.status === 'open' && <div className="so-actions" key={selected.id}>
              <form className="so-action-form" onSubmit={event => {
                event.preventDefault()
                const pickupStoreId = Number(new FormData(event.currentTarget).get('pickup_store_id'))
                void mutate({
                  method: 'patch', url: `/special-orders/${selected.id}`, kind: 'correct',
                  body: { expected_version: selected.version, pickup_store_id: pickupStoreId },
                })
              }}>
                <label>Pickup location<select name="pickup_store_id" defaultValue={String(selected.pickup_store_id || '')} required><option value="" disabled>Select location</option>{stores.map(store => <option key={store.id} value={store.id}>{store.name} · {store.code}</option>)}</select></label>
                <Button htmlType="submit" loading={saving}>Update pickup location</Button>
              </form>
              {selected.remaining_cents > 0 && <form className="so-action-form" ref={paymentForm} onSubmit={addPayment}>
                <label>Payment amount<input name="amount" inputMode="decimal" placeholder="0.00" required /></label>
                <label>Payment method<select name="tender" defaultValue="" required><option value="" disabled>Select method</option>{tenders.map(tender => <option key={tender.value} value={tender.value}>{tender.label}</option>)}</select></label>
                <Button htmlType="submit" loading={saving && pending.current?.kind === 'payment'}>Add payment</Button>
              </form>}
              <Button type="primary" size="large" disabled={selected.remaining_cents !== 0} loading={saving} onClick={() => void mutate({
                method: 'post', url: `/special-orders/${selected.id}/complete`, kind: 'complete',
                body: { expected_version: selected.version },
              })}>Mark customer received</Button>
              {selected.remaining_cents !== 0 && <small>Full payment is required before customer handoff.</small>}
            </div>}
            {selected.status === 'completed' && <Alert type="success" showIcon message="Customer received the item" />}
            {admin && <AdminCorrection order={selected} employees={employees} stores={stores} saving={saving} save={body => void mutate({
              method: 'patch', url: `/special-orders/${selected.id}`, kind: 'correct', body,
            })} />}
          </>}
        </section>
      </div>
    </Spin>
  </main>
}

function AdminCorrection({ order, employees, stores, saving, save }: {
  order: SpecialOrder
  employees: Employee[]
  stores: { id: number; code: string; name: string }[]
  saving: boolean
  save: (body: Record<string, unknown>) => void
}) {
  return <details className="so-admin"><summary>Admin corrections</summary><form key={order.version} onSubmit={event => {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const body: Record<string, unknown> = {
      expected_version: order.version,
      created_by: data.get('created_by'),
      created_at: new Date(String(data.get('created_at'))).toISOString(),
    }
    if (order.status === 'completed') {
      const pickupStoreId = Number(data.get('pickup_store_id'))
      if (pickupStoreId) body.pickup_store_id = pickupStoreId
      body.completed_by = data.get('completed_by')
      body.completed_at = new Date(String(data.get('completed_at'))).toISOString()
    }
    save(body)
  }}>
    <label>Created by<select name="created_by" defaultValue={order.created_by} required>{employees.map(employee => <option key={employee.auth0_id} value={employee.auth0_id}>{employee.name || employee.email || employee.auth0_id}</option>)}</select></label>
    <label>Created date<input name="created_at" type="datetime-local" defaultValue={localDateTime(order.created_at)} required /></label>
    {order.status === 'completed' && <>
      <label>Pickup location<select name="pickup_store_id" defaultValue={String(order.pickup_store_id || '')}><option value="">Not recorded</option>{stores.map(store => <option key={store.id} value={store.id}>{store.name} · {store.code}</option>)}</select></label>
      <label>Completed by<select name="completed_by" defaultValue={order.completed_by || ''} required>{employees.map(employee => <option key={employee.auth0_id} value={employee.auth0_id}>{employee.name || employee.email || employee.auth0_id}</option>)}</select></label>
      <label>Completion date<input name="completed_at" type="datetime-local" defaultValue={localDateTime(order.completed_at)} required /></label>
    </>}
    <Button htmlType="submit" loading={saving}>Save corrections</Button>
  </form></details>
}
