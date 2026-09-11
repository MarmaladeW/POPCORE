import { Alert, Button, Card, DatePicker, Form, Input, InputNumber, Select, Space, Typography } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import client from '../../api/client'
import {
  addPayments, createSale, newRequestKey, postSale, type SaleEntryMode, type SaleResult,
} from '../../api/salesDocuments'
import type { GoodsProduct } from '../../api/goods'
import { useAppStore } from '../../store'

const { Title, Text } = Typography

interface FormValues {
  business_date: dayjs.Dayjs
  entry_mode: SaleEntryMode
  source_reference: string
  product_id: number
  quantity: number
  unit_price_cents?: number
  source_tax_cents?: number
  collected_cents?: number
  cash_cents?: number
  card_cents?: number
  e_transfer_cents?: number
  wechat_cents?: number
  alipay_cents?: number
}

export default function SaleEntryPage() {
  const selectedStore = useAppStore(state => state.selectedStore)
  const navigate = useNavigate()
  const [form] = Form.useForm<FormValues>()
  const [products, setProducts] = useState<GoodsProduct[]>([])
  const [draft, setDraft] = useState<SaleResult | null>(null)
  const [result, setResult] = useState<SaleResult | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const createKey = useRef(newRequestKey())
  const postKey = useRef(newRequestKey())
  const paymentKey = useRef(newRequestKey())
  const tenderValues = useRef<Array<{ tender: string; amount_cents: number | null }>>([])

  useEffect(() => {
    if (!selectedStore || selectedStore.code === 'ALL') return
    client.get('/stock', { params: {
      store_code: selectedStore.code, page: 1, page_size: 500,
    } }).then(response => setProducts(response.data.items)).catch(() => {
      setError('Unable to load products for this store.')
    })
  }, [selectedStore])

  if (!selectedStore || selectedStore.code === 'ALL') {
    return <Alert type="info" showIcon message="Select one store before entering a sale." />
  }
  const store = selectedStore

  async function save(values: FormValues) {
    const product = products.find(item => item.id === values.product_id)
    if (!product?.stock_unit) {
      setError('Choose a product with verified inventory units.')
      return
    }
    setSaving(true)
    setError('')
    try {
      const subtotal = values.unit_price_cents == null
        ? null : values.unit_price_cents * values.quantity
      const tax = values.source_tax_cents ?? null
      const gross = subtotal == null || tax == null ? null : subtotal + tax
      const created = await createSale({
        store_id: store.id,
        business_date: values.business_date.format('YYYY-MM-DD'),
        entry_mode: values.entry_mode,
        source: {
          system: 'manual', account: store.code,
          reference: values.source_reference,
        },
        subtotal_cents: subtotal,
        source_tax_cents: tax,
        gross_cents: gross,
        reduction_cents: gross == null || values.collected_cents == null
          ? null : Math.max(0, gross - values.collected_cents),
        rounding_cents: gross == null || values.collected_cents == null ? null : 0,
        collected_cents: values.collected_cents ?? null,
        lines: [{
          product_id: product.id,
          unit: product.stock_unit,
          quantity: values.quantity,
          unit_price_cents: values.unit_price_cents ?? null,
          source_tax_cents: tax,
        }],
      }, createKey.current)
      tenderValues.current = [
        ['cash', values.cash_cents], ['card', values.card_cents],
        ['e_transfer', values.e_transfer_cents], ['wechat', values.wechat_cents],
        ['alipay', values.alipay_cents],
      ].filter((item): item is [string, number] => item[1] != null)
        .map(([tender, amount_cents]) => ({ tender, amount_cents }))
      setDraft(created)
      createKey.current = newRequestKey()
    } catch {
      setError('The draft was not confirmed. Retry with the same details to recover it safely.')
    } finally {
      setSaving(false)
    }
  }

  async function recordSale() {
    if (!draft) return
    setSaving(true)
    setError('')
    try {
      const posted = await postSale(draft.sale_id, draft.version, postKey.current)
      setResult(posted)
      postKey.current = newRequestKey()
      if (tenderValues.current.length) {
        const paid = await addPayments(
          draft.sale_id, posted.version, tenderValues.current, paymentKey.current,
        )
        setResult(paid)
        paymentKey.current = newRequestKey()
        tenderValues.current = []
      }
    } catch {
      setError('Posting was not confirmed. Retry this draft; inventory will not be submitted twice.')
    } finally {
      setSaving(false)
    }
  }

  async function retryTenders() {
    if (!result || !tenderValues.current.length) return
    setSaving(true)
    setError('')
    try {
      const paid = await addPayments(
        result.sale_id, result.version, tenderValues.current, paymentKey.current,
      )
      setResult(paid)
      paymentKey.current = newRequestKey()
      tenderValues.current = []
    } catch {
      setError('Tender save was not confirmed. Retry to recover the same request safely.')
    } finally { setSaving(false) }
  }

  return (
    <div style={{ maxWidth: 720, margin: '0 auto' }}>
      <Title level={3}>Enter completed sale</Title>
      <Text type="secondary">Record the actual receipt from {store.name}.</Text>
      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 16 }} />}
      {result ? (
        <Card style={{ marginTop: 16 }}>
          <Alert
            showIcon
            type={result.allocation_status === 'allocated' ? 'success' : 'warning'}
            message="Sale recorded"
            description={result.allocation_status === 'allocated'
              ? 'Payment facts were saved and stock was allocated.'
              : 'Payment facts were saved. Stock allocation still needs manager review.'}
          />
          <Button type="primary" style={{ marginTop: 16 }}
            onClick={() => navigate(`/sales/documents/${result.sale_id}`)}>
            View sale
          </Button>
          {tenderValues.current.length > 0 && (
            <Button loading={saving} onClick={retryTenders} style={{ margin: '16px 0 0 8px' }}>
              Retry tender save
            </Button>
          )}
        </Card>
      ) : draft ? (
        <Card title={`Draft sale #${draft.sale_id}`} style={{ marginTop: 16 }}>
          <Alert type="info" showIcon message="Draft saved"
            description="Review and record it once. A failed attempt can be retried safely." />
          <Space style={{ marginTop: 16 }}>
            <Button type="primary" loading={saving} onClick={recordSale}>Record sale</Button>
            <Button onClick={() => navigate(`/sales/documents/${draft.sale_id}`)}>View draft</Button>
          </Space>
        </Card>
      ) : (
        <Card style={{ marginTop: 16 }}>
          <Form form={form} layout="vertical" onFinish={save} initialValues={{
            business_date: dayjs(), entry_mode: 'already_paid', quantity: 1,
          }}>
            <Form.Item name="business_date" label="Business date" rules={[{ required: true }]}>
              <DatePicker style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="entry_mode" label="Sale state" rules={[{ required: true }]}>
              <Select options={[
                { value: 'already_paid', label: 'Already paid in the POS' },
                { value: 'planned_entry', label: 'Check stock before recording' },
              ]} />
            </Form.Item>
            <Form.Item name="source_reference" label="Receipt or order reference"
              rules={[{ required: true, whitespace: true }]}>
              <Input autoComplete="off" />
            </Form.Item>
            <Form.Item name="product_id" label="Product" rules={[{ required: true }]}>
              <Select showSearch optionFilterProp="label" options={products.map(product => ({
                value: product.id, label: product.jizhanming || product.sku,
              }))} />
            </Form.Item>
            <Form.Item name="quantity" label="Quantity" rules={[{ required: true }]}>
              <InputNumber min={1} precision={0} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="unit_price_cents" label="Actual unit price (cents)">
              <InputNumber precision={0} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="source_tax_cents" label="Actual tax (cents)">
              <InputNumber precision={0} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="collected_cents" label="Actual collected total (cents)">
              <InputNumber precision={0} style={{ width: '100%' }} />
            </Form.Item>
            <Title level={5}>Tender amounts (cents)</Title>
            <Text type="secondary">Enter each actual component of a split payment separately.</Text>
            {[
              ['cash_cents', 'Cash'], ['card_cents', 'Card'],
              ['e_transfer_cents', 'E-transfer'], ['wechat_cents', 'WeChat Pay'],
              ['alipay_cents', 'Alipay'],
            ].map(([name, label]) => (
              <Form.Item key={name} name={name} label={label}>
                <InputNumber min={0} precision={0} style={{ width: '100%' }} />
              </Form.Item>
            ))}
            <Button type="primary" htmlType="submit" loading={saving}>Save draft</Button>
          </Form>
        </Card>
      )}
    </div>
  )
}
