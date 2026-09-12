import { Alert, Button, Card, DatePicker, Descriptions, Form, Input, InputNumber, List, Select, Space, Typography } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useRef, useState } from 'react'
import { useBeforeUnload, useNavigate } from 'react-router-dom'

import client from '../../api/client'
import { searchGoodsProducts, type GoodsProduct } from '../../api/goods'
import { addPayments, createSale, fetchSale, newRequestKey, postSale, type SaleEntryMode, type SaleInput, type SaleResult } from '../../api/salesDocuments'
import { formatCents, parseMoneyToCents } from '../../lib/money'
import { useHistoryReconciliationGuard } from '../../lib/reconciliationNavigation'
import { useAppStore, type Store } from '../../store'
import { torontoDate } from '../Dashboard/todayPresentation'

const { Title, Text } = Typography
type Tender = { tender: string; amount_cents: number | null }
type CreateIntent = { key: string; body: SaleInput; product: GoodsProduct; tenders: Tender[] }
interface FormValues {
  business_date: dayjs.Dayjs; entry_mode: SaleEntryMode; source_reference: string
  product_id: number; quantity: number; unit_price: string; source_tax: string
  collected: string; cash: string; card: string; e_transfer: string; wechat: string; alipay: string
}
const responseStatus = (cause: unknown) => (cause as { response?: { status?: number } })?.response?.status
const serverError = (cause: unknown, fallback: string) => (cause as { _serverMessage?: string })?._serverMessage || fallback
const exactCents = (value: number) => {
  if (!Number.isSafeInteger(value)) throw new RangeError('Calculated sale total is too large.')
  return value
}

export default function SaleEntryPage() {
  const selectedStore = useAppStore(state => state.selectedStore)
  const setSelectedStore = useAppStore(state => state.setSelectedStore)
  const navigate = useNavigate(), [form] = Form.useForm<FormValues>()
  const [products, setProducts] = useState<GoodsProduct[]>([]), [scope, setScope] = useState<Store | null>(selectedStore)
  const [draft, setDraft] = useState<SaleResult | null>(null), [result, setResult] = useState<SaleResult | null>(null)
  const [submitted, setSubmitted] = useState<CreateIntent | null>(null), [error, setError] = useState('')
  const [saving, setSaving] = useState(false), [dirty, setDirty] = useState(false)
  const [pending, setPending] = useState<'create'|'post'|'payment'|null>(null), [refreshNeeded, setRefreshNeeded] = useState(false)
  const createIntent = useRef<CreateIntent | null>(null)
  const postIntent = useRef<{ key: string; version: number } | null>(null)
  const paymentIntent = useRef<{ key: string; version: number; payments: Tender[] } | null>(null)
  const productRequest = useRef(0), restoringStore = useRef(false), guard = dirty || pending !== null

  useHistoryReconciliationGuard(guard, pending ? 'This request is not confirmed. Leave it reconciliation-pending and exit this page?' : 'Discard this unsaved sale entry?')
  useBeforeUnload(event => { if (guard) event.preventDefault() })
  useEffect(() => {
    if (!guard) return
    const confirmLink = (event: MouseEvent) => {
      const link = (event.target as HTMLElement).closest('a')
      if (!link || link.target === '_blank') return
      if (!window.confirm(pending ? 'This request is not confirmed. Leave it reconciliation-pending and exit this page?' : 'Discard this unsaved sale entry?')) event.preventDefault()
    }
    document.addEventListener('click', confirmLink, true)
    return () => document.removeEventListener('click', confirmLink, true)
  }, [guard, pending])

  useEffect(() => {
    if (!selectedStore) return
    if (restoringStore.current && scope && selectedStore.code === scope.code) { restoringStore.current = false; return }
    if (scope && selectedStore.code !== scope.code && pending) {
      restoringStore.current = true; setError('Resolve or acknowledge the unconfirmed sale request before changing stores.'); setSelectedStore(scope); return
    }
    if (scope && selectedStore.code !== scope.code && dirty && !window.confirm('Discard this unsaved sale entry and change stores?')) {
      restoringStore.current = true; setSelectedStore(scope); return
    }
    setScope(selectedStore); setDraft(null); setResult(null); setSubmitted(null); setDirty(false); setError('')
    createIntent.current = null; postIntent.current = null; paymentIntent.current = null
    setPending(null); setRefreshNeeded(false); form.resetFields(); setProducts([])
    const current = ++productRequest.current
    if (selectedStore.code === 'ALL') return
    const controller = new AbortController()
    client.get('/stock', { params: { store_code: selectedStore.code, page: 1, page_size: 500 }, signal: controller.signal })
      .then(response => { if (current === productRequest.current) setProducts(response.data.items) })
      .catch(cause => { if (current === productRequest.current && cause?.code !== 'ERR_CANCELED') setError('Unable to load products for this store.') })
    return () => controller.abort()
  }, [selectedStore?.code]) // eslint-disable-line react-hooks/exhaustive-deps

  async function searchProducts(value: string) {
    if (!value.trim()) return
    const chosen = products.find(item => item.id === form.getFieldValue('product_id')), found = await searchGoodsProducts(value)
    setProducts(chosen && !found.some(item => item.id === chosen.id) ? [chosen, ...found] : found)
  }
  const readMoney = (value?: string) => parseMoneyToCents(value ?? '')

  async function save(values?: FormValues) {
    if (saving) return
    let intent = createIntent.current
    if (!intent) {
      if (!values || !scope || scope.code === 'ALL') return
      const product = products.find(item => item.id === values.product_id)
      if (!product?.stock_unit) { setError('Choose a product with verified inventory units.'); return }
      try {
        if (!Number.isSafeInteger(values.quantity) || values.quantity < 1) throw new RangeError('Quantity must be a positive whole number.')
        const unitPrice = readMoney(values.unit_price), tax = readMoney(values.source_tax), collected = readMoney(values.collected)
        const subtotal = unitPrice == null ? null : exactCents(unitPrice * values.quantity)
        const gross = subtotal == null || tax == null ? null : exactCents(subtotal + tax)
        const tenders = [['cash', values.cash], ['card', values.card], ['e_transfer', values.e_transfer], ['wechat', values.wechat], ['alipay', values.alipay]]
          .map(([tender, value]) => ({ tender, amount_cents: readMoney(value) })).filter(item => item.amount_cents !== null)
        const body: SaleInput = {
          store_id: scope.id, business_date: values.business_date.format('YYYY-MM-DD'), entry_mode: values.entry_mode,
          source: { system: 'manual', account: scope.code, reference: values.source_reference }, subtotal_cents: subtotal,
          source_tax_cents: tax, gross_cents: gross, reduction_cents: gross == null || collected == null ? null : Math.max(0, gross - collected),
          rounding_cents: gross == null || collected == null ? null : 0, collected_cents: collected,
          lines: [{ product_id: product.id, unit: product.stock_unit, quantity: values.quantity, unit_price_cents: unitPrice, source_tax_cents: tax }],
        }
        intent = { key: newRequestKey(), body, product, tenders }; createIntent.current = intent
      } catch (cause) { setError((cause as Error).message); return }
    }
    setSaving(true); setPending('create'); setError('')
    try {
      const created = await createSale(intent.body, intent.key)
      setDraft(created); setSubmitted(intent); setDirty(false); setPending(null); createIntent.current = null
    } catch (cause) {
      const status = responseStatus(cause)
      if (status && status < 500) { createIntent.current = null; setPending(null) }
      setError(serverError(cause, status && status < 500 ? 'The draft was rejected. Correct the form and submit a new request.' : 'The draft was not confirmed. Retry sends the identical request.'))
    } finally { setSaving(false) }
  }

  async function refreshSale(id: number) {
    try {
      const current = await fetchSale(id); setResult(current.status === 'posted' ? current : null); setDraft(current.status === 'draft' ? current : null)
      setRefreshNeeded(false); return true
    } catch { setRefreshNeeded(true); setError('Saved; refresh needed before another action.'); return false }
  }

  async function savePaymentsFor(posted: SaleResult, intent: NonNullable<typeof paymentIntent.current>) {
    setSaving(true); setPending('payment'); setError('')
    try {
      const paid = await addPayments(posted.sale_id, intent.version, intent.payments, intent.key)
      setResult(paid); paymentIntent.current = null; setPending(null); await refreshSale(paid.sale_id)
    } catch (cause) {
      const status = responseStatus(cause)
      if (status === 409) {
        paymentIntent.current = null; setPending(null)
        if (await refreshSale(posted.sale_id)) setError('Payment facts changed. Review the refreshed sale before creating a new payment request.')
      } else if (status && status < 500) {
        paymentIntent.current = null; setPending(null); setError(serverError(cause, 'Payment facts were rejected. Review the sale before trying again.'))
      } else setError('Sale recorded; payment facts not confirmed.')
    } finally { setSaving(false) }
  }

  async function recordSale() {
    if (!draft || !submitted || saving) return
    const intent = postIntent.current ?? { key: newRequestKey(), version: draft.version }; postIntent.current = intent
    setSaving(true); setPending('post'); setError('')
    try {
      const posted = await postSale(draft.sale_id, intent.version, intent.key)
      postIntent.current = null; setPending(null); setResult(posted); setDraft(null)
      if (submitted.tenders.length) {
        const payments = { key: newRequestKey(), version: posted.version, payments: submitted.tenders }
        paymentIntent.current = payments; setSaving(false); await savePaymentsFor(posted, payments)
      } else await refreshSale(posted.sale_id)
    } catch (cause) {
      const status = responseStatus(cause)
      if (status === 409) {
        postIntent.current = null; setPending(null)
        if (await refreshSale(draft.sale_id)) setError('Sale facts changed. Review the refreshed draft before recording it.')
      } else if (status && status < 500) {
        postIntent.current = null; setPending(null); setError(serverError(cause, 'The sale was rejected. Review the saved draft.'))
      } else setError('Posting was not confirmed. Retry sends the identical request.')
    } finally { setSaving(false) }
  }

  async function retryPayments() { if (result && paymentIntent.current && !saving) await savePaymentsFor(result, paymentIntent.current) }

  if (!scope || scope.code === 'ALL') return <Alert type="info" showIcon message="Select one store before entering a sale." />
  return <div className="pc-page" style={{ maxWidth: 760, margin: '0 auto' }}>
    <div className="pc-page-heading"><Title level={3}>Enter completed sale</Title><Text type="secondary">Record the actual receipt from {scope.name}.</Text></div>
    {error && error !== 'Sale recorded; payment facts not confirmed.' && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
    {refreshNeeded && result && <Button onClick={() => refreshSale(result.sale_id)}>Retry refresh</Button>}
    {result ? <Card>
      <Alert showIcon type={paymentIntent.current ? 'warning' : 'success'} message={paymentIntent.current ? 'Sale recorded; payment facts not confirmed.' : 'Sale recorded'}
        description={paymentIntent.current ? 'Inventory will not be posted again. Retry only the identical payment request.' : 'The saved sale facts are ready for review.'} />
      <Space wrap style={{ marginTop: 16 }}><Button type="primary" disabled={refreshNeeded || pending !== null} onClick={() => navigate(`/sales/documents/${result.sale_id}`)}>View sale</Button>{paymentIntent.current && <Button loading={saving} onClick={retryPayments}>Retry payment save</Button>}</Space>
    </Card> : draft && submitted ? <Card title={`Draft sale #${draft.sale_id}`}>
      <Alert type="info" showIcon message="Draft saved" description="Review the saved facts before recording this sale." />
      <Descriptions column={1} size="small" style={{ marginTop: 16 }}>
        <Descriptions.Item label="Source">{submitted.body.source.account} / {submitted.body.source.reference}</Descriptions.Item>
        <Descriptions.Item label="Store and date">{scope.name} · {submitted.body.business_date}</Descriptions.Item>
        <Descriptions.Item label="Item">{submitted.body.lines[0].quantity} {submitted.product.stock_unit}s · {submitted.product.jizhanming || submitted.product.sku}</Descriptions.Item>
        <Descriptions.Item label="Subtotal">{formatCents(submitted.body.subtotal_cents)}</Descriptions.Item>
        <Descriptions.Item label="Gross">{formatCents(submitted.body.gross_cents)}</Descriptions.Item>
        <Descriptions.Item label="Collected">{formatCents(submitted.body.collected_cents)}</Descriptions.Item>
        <Descriptions.Item label="Expected stock effect">-{submitted.body.lines[0].quantity} {submitted.product.stock_unit}s from saleable floor stock after posting</Descriptions.Item>
      </Descriptions>
      <List size="small" header="Actual tenders" locale={{ emptyText: 'No tender facts entered' }} dataSource={submitted.tenders} renderItem={item => <List.Item>{item.tender.replace('_', ' ')} · {formatCents(item.amount_cents)}</List.Item>} />
      <Space wrap><Button type="primary" loading={saving} onClick={recordSale}>Record sale</Button><Button disabled={pending !== null} onClick={() => navigate(`/sales/documents/${draft.sale_id}`)}>View draft</Button></Space>
    </Card> : <Card>
      <Form form={form} layout="vertical" disabled={pending === 'create'} onFinish={save} onValuesChange={() => setDirty(true)} initialValues={{ business_date: dayjs(torontoDate()), entry_mode: 'already_paid', quantity: 1 }}>
        <Title level={5}>Source and date</Title>
        <Form.Item name="business_date" label="Business date" rules={[{ required: true }]}><DatePicker style={{ width: '100%' }} /></Form.Item>
        <Form.Item name="entry_mode" label="Sale state" rules={[{ required: true }]}><Select options={[{ value: 'already_paid', label: 'Already paid in the POS' }, { value: 'planned_entry', label: 'Check stock before recording' }]} /></Form.Item>
        <Form.Item name="source_reference" label="Receipt or order reference" rules={[{ required: true, whitespace: true }]}><Input autoComplete="off" /></Form.Item>
        <Title level={5}>Product and quantity</Title>
        <Form.Item name="product_id" label="Product" rules={[{ required: true }]}><Select showSearch filterOption={false} onSearch={searchProducts} options={products.map(product => ({ value: product.id, label: product.jizhanming || product.sku }))} /></Form.Item>
        <Form.Item name="quantity" label="Quantity" rules={[{ required: true }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item>
        <Title level={5}>Actual money</Title>
        {[['unit_price', 'Actual unit price ($)'], ['source_tax', 'Actual tax ($)'], ['collected', 'Actual collected total ($)'], ['cash', 'Cash ($)'], ['card', 'Card ($)'], ['e_transfer', 'E-transfer ($)'], ['wechat', 'WeChat Pay ($)'], ['alipay', 'Alipay ($)']].map(([name, label]) =>
          <Form.Item key={name} name={name} label={label} rules={[{ validator: async (_, value) => { try { readMoney(value) } catch (cause) { throw new Error((cause as Error).message) } } }]}><Input prefix="$" inputMode="decimal" placeholder="Unknown when blank" /></Form.Item>)}
        {!createIntent.current && <Button type="primary" htmlType="submit" loading={saving}>Save draft</Button>}
      </Form>
      {createIntent.current && <Button type="primary" loading={saving} onClick={() => save()} style={{ marginTop: 16 }}>Retry draft save</Button>}
    </Card>}
  </div>
}
