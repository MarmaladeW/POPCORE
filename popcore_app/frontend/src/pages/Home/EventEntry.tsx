import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Form, Input, InputNumber, Select } from 'antd'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { useRole } from '../../auth/useRole'
import { useAppStore } from '../../store'
import client from '../../api/client'
import { parseMoneyToCents } from '../../lib/money'
import type { GoodsProduct } from '../../api/goods'
import useCheckoutMutation from '../Sales/useCheckoutMutation'
import { eventNames } from './storeDay'
import './Home.css'

export default function EventEntry({ display = false }: { display?: boolean }) {
  const store = useAppStore(state => state.selectedStore)
  const { user } = useAuth0()
  const role = useRole()
  return <EventForm key={`${store?.id}|${user?.sub}|${role}|${display}`} display={display} />
}

function EventForm({ display }: { display: boolean }) {
  const store = useAppStore(state => state.selectedStore)
  const navigate = useNavigate()
  const [kind, setKind] = useState(display ? 'display_in' : 'claw_prize')
  const [options, setOptions] = useState<GoodsProduct[]>([])
  const [searchError, setSearchError] = useState('')
  const [searching, setSearching] = useState(false)
  const [form] = Form.useForm()
  const search = useRef<AbortController>()
  const [denied, setDenied] = useState(false)
  const mutation = useCheckoutMutation(() => navigate('/', { state: { saved: true, storeId: store?.id } }))
  const exchange = kind === 'cash_exchange'

  useEffect(() => {
    const invalidate = () => { setDenied(true); form.resetFields(); setOptions([]); search.current?.abort() }
    window.addEventListener('popcore:checkout-access-denied', invalidate)
    return () => { window.removeEventListener('popcore:checkout-access-denied', invalidate); search.current?.abort() }
  }, [form])
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('popcore:checkout-busy', { detail: mutation.pending }))
    return () => { window.dispatchEvent(new CustomEvent('popcore:checkout-busy', { detail: false })) }
  }, [mutation.pending])

  async function findProduct(query: string) {
    search.current?.abort()
    const controller = new AbortController()
    search.current = controller
    setSearchError(''); setSearching(true); setOptions([])
    try {
      const response = await client.get<GoodsProduct[]>('/products/search', { params: { q: query, limit: 20 }, signal: controller.signal, timeout: 15000 })
      if (!controller.signal.aborted) setOptions(response.data)
    } catch (cause) {
      if (!controller.signal.aborted) setSearchError((cause as { _serverMessage?: string })?._serverMessage || 'Product search failed. Type again to retry.')
    } finally { if (!controller.signal.aborted) setSearching(false) }
  }

  function save(values: { product_id?: number; quantity?: number; tender?: string; amount?: string; note?: string }) {
    if (!store || store.code === 'ALL' || denied) return
    const details = exchange
      ? { tender: values.tender, amount_cents: parseMoneyToCents(values.amount || '') }
      : { product_id: values.product_id, quantity: values.quantity }
    void mutation.run('/store-events', { store_id: store.id, kind, ...details, note: values.note?.trim() || '' })
  }

  return <div className="pc-home pc-home-task">
    <Link className="pc-home-back" to="/">← Back to home</Link>
    <header className="pc-home-heading"><h1>{display ? '入 display / Display arrival' : '娃娃机 / Claw machine'}</h1><p>{store?.name} · Record what happened.</p></header>
    {!store || store.code === 'ALL' ? <Alert type="info" showIcon message="Choose one store before recording an event." />
      : denied ? <Alert role="alert" type="error" showIcon message="You no longer have access to record events here. Check your assigned shift in Schedule." />
      : <>
        {!display && <div className="pc-home-event-options" role="group" aria-label="Claw machine actions">
          {['claw_prize', 'claw_refill', 'cash_exchange'].map(value => <Button key={value} aria-pressed={kind === value} type={kind === value ? 'primary' : 'default'} disabled={mutation.pending}
            onClick={() => { setKind(value); form.resetFields(); setOptions([]) }}>{eventNames[value]}</Button>)}
        </div>}
        <p className="pc-home-muted">{exchange ? 'Record after receiving the digital payment and handing over the same amount in cash. This reduces the expected cash drawer.' : 'This adds a record to the daily summary. Stock balances are unchanged.'}</p>
        {mutation.notice}
        {searchError && <Alert role="alert" type="error" message={searchError} />}
        <Form form={form} layout="vertical" disabled={mutation.pending} onFinish={save}>
          {exchange ? <>
            <Form.Item name="tender" label="Payment received by" rules={[{ required: true }]}><Select options={[{ value: 'card', label: 'Card' }, { value: 'e_transfer', label: 'E-transfer' }]} /></Form.Item>
            <Form.Item name="amount" label="Cash exchanged (CAD)" rules={[{ validator: (_, value) => {
              const cents = parseMoneyToCents(value || '')
              return cents != null && cents > 0 ? Promise.resolve() : Promise.reject(new Error('Enter a positive amount, with at most two decimal places.'))
            } }]}><Input inputMode="decimal" prefix="$" /></Form.Item>
          </> : <>
            <Form.Item name="product_id" label="Product" rules={[{ required: true, message: 'Choose the product from search results.' }]}>
              <Select showSearch filterOption={false} loading={searching} onSearch={findProduct} placeholder="Search product name or SKU"
                options={options.map(product => ({ value: product.id, label: `${product.sku} · ${product.jizhanming}` }))} />
            </Form.Item>
            <Form.Item name="quantity" label="Quantity" rules={[{ required: true }, { type: 'integer', min: 1 }]}><InputNumber min={1} precision={0} /></Form.Item>
          </>}
          <Form.Item name="note" label={exchange ? 'Payment reference / Note' : display ? 'From / Note' : 'Note (optional)'} rules={exchange ? [{ required: true, whitespace: true, message: 'Add the payment reference or a note to identify this exchange.' }] : []}>
            <Input.TextArea rows={2} maxLength={500} />
          </Form.Item>
          {exchange && <Form.Item name="confirmed" valuePropName="checked" rules={[{ validator: (_, value) => value === true ? Promise.resolve() : Promise.reject(new Error('Confirm the payment received and cash handed over.')) }]}>
            <Checkbox>I received this digital payment and handed over the same amount in cash.</Checkbox>
          </Form.Item>}
          <Button htmlType="submit" type="primary" loading={mutation.saving} disabled={mutation.pending}>Save record</Button>
        </Form>
      </>}
  </div>
}
