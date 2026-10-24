import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Form, Input, InputNumber, Radio, Select } from 'antd'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { useRole } from '../../auth/useRole'
import { useAppStore } from '../../store'
import client from '../../api/client'
import { productNames } from '../../lib/productLabel'
import { tryParseMoneyToCents } from '../../lib/money'
import PageHeader from '../../components/pc/PageHeader'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { CheckoutTabs, useOpenSpecialOrders } from '../Sales/CheckoutNav'
import type { GoodsProduct } from '../../api/goods'
import useCheckoutMutation from '../Sales/useCheckoutMutation'
import { eventNames } from './storeDay'
import './Home.css'

// display: 入 display (Receive goods). exchange: 换现金, a tab of Checkout. Otherwise the claw machine.
export default function EventEntry({ display = false, exchange: exchangeOnly = false, inline = false }: { display?: boolean; exchange?: boolean; inline?: boolean }) {
  const store = useAppStore(state => state.selectedStore)
  const { user } = useAuth0()
  const role = useRole()
  return <EventForm key={`${store?.id}|${user?.sub}|${role}|${display}|${exchangeOnly}`} display={display} exchangeOnly={exchangeOnly} inline={inline} />
}

function EventForm({ display, exchangeOnly, inline }: { display: boolean; exchangeOnly: boolean; inline: boolean }) {
  const store = useAppStore(state => state.selectedStore)
  const navigate = useNavigate()
  const [kind, setKind] = useState(display ? 'display_in' : exchangeOnly ? 'cash_exchange' : 'claw_prize')
  const phone = useIsPhone()
  const specialCount = useOpenSpecialOrders(exchangeOnly)
  const [options, setOptions] = useState<GoodsProduct[]>([])
  const [searchError, setSearchError] = useState('')
  const [searching, setSearching] = useState(false)
  const [form] = Form.useForm()
  const search = useRef<AbortController>()
  const [denied, setDenied] = useState(false)
  const mutation = useCheckoutMutation(() => navigate('/', { state: { saved: true, storeId: store?.id } }))
  const exchange = kind === 'cash_exchange'
  const refill = kind === 'claw_refill'
  const [sources, setSources] = useState<Array<{ id: number; code: string; name: string }>>()

  useEffect(() => {
    if (!refill || !store || store.code === 'ALL') return
    let current = true
    setSources(undefined)
    client.get<Array<{ id: number; code: string; name: string }>>('/inventory/locations', { params: { store_code: store.code } })
      .then(response => { if (current) setSources(response.data.filter(location => ['floor', 'upstairs', 'warehouse'].includes(location.code))) })
      .catch(() => { if (current) setSources([]) })
    return () => { current = false }
  }, [refill, store])

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

  function save(values: { product_id?: number; quantity?: number; source_location_id?: number; tender?: string; amount?: string; note?: string }) {
    if (!store || store.code === 'ALL' || denied) return
    const details = exchange
      ? { tender: values.tender, amount_cents: tryParseMoneyToCents(values.amount || '') }
      : { product_id: values.product_id, quantity: values.quantity, ...(refill && values.source_location_id ? { source_location_id: values.source_location_id } : {}) }
    void mutation.run('/store-events', { store_id: store.id, kind, ...details, note: values.note?.trim() || '' })
  }

  if (exchangeOnly) return <div className="pc-exchange">
    <PageHeader title={phone ? '换现金' : '买单'} sub={phone ? undefined : `Checkout${store?.name ? ` · ${store.name}` : ''}`} back={phone ? { label: '买单', to: '/checkout' } : undefined} />
    {!phone && <CheckoutTabs specialCount={specialCount} />}
    <div className="pc-exchange-body">
      {!phone && <h2 className="pc-exchange-title">换现金 <small>Cash exchange</small></h2>}
      <p className="pc-exchange-lead">收到电子付款后，给顾客同样金额的现金。这会减少钱箱应有现金。<span>Record after receiving the digital payment and handing over the same amount in cash. This reduces the expected cash drawer.</span></p>
      {!store || store.code === 'ALL' ? <Alert type="info" showIcon message="Choose one store before recording an event." />
        : denied ? <Alert role="alert" type="error" showIcon message="You no longer have access to record events here. Check your assigned shift in Schedule." />
        : <>
          {mutation.notice}
          <Form form={form} layout="vertical" disabled={mutation.pending} onFinish={save} requiredMark={false}>
            <Form.Item name="tender" label="顾客用什么付 Paid by" rules={[{ required: true, message: 'Choose how the customer paid.' }]}>
              <Radio.Group className="pc-exchange-tender" optionType="button" options={[{ value: 'card', label: <><strong>刷卡</strong><small>Card</small></>, title: 'Card' }, { value: 'e_transfer', label: <><strong>转账</strong><small>E-transfer</small></>, title: 'E-transfer' }]} />
            </Form.Item>
            <Form.Item name="amount" label="换出现金 Cash given (CAD)" rules={[{ validator: (_, value) => {
              const cents = tryParseMoneyToCents(value || '')
              return cents != null && cents > 0 ? Promise.resolve() : Promise.reject(new Error('Enter a positive amount, with at most two decimal places.'))
            } }]}><Input className="pc-exchange-amount" inputMode="decimal" prefix="$" /></Form.Item>
            <Form.Item name="note" label="付款参考号 Payment reference" rules={[{ required: true, whitespace: true, message: 'Add the payment reference or a note to identify this exchange.' }]}>
              <Input maxLength={500} />
            </Form.Item>
            <Form.Item name="confirmed" valuePropName="checked" rules={[{ validator: (_, value) => value === true ? Promise.resolve() : Promise.reject(new Error('Confirm the payment received and cash handed over.')) }]}>
              <Checkbox>I received this digital payment and handed over the same amount in cash.</Checkbox>
            </Form.Item>
            <Button className="pc-exchange-save" htmlType="submit" type="primary" size="large" loading={mutation.saving} disabled={mutation.pending}>保存记录 Save record</Button>
          </Form>
        </>}
    </div>
  </div>

  return <div className={inline ? 'pc-home-task pc-event-inline' : 'pc-home pc-home-task'}>
    {!inline && <Link className="pc-home-back" to="/">← Back to home</Link>}
    {!inline && <header className="pc-home-heading"><h1>{display ? '入 display / Display arrival' : '娃娃机 / Claw machine'}</h1><p>{store?.name} · Record what happened.</p></header>}
    {!store || store.code === 'ALL' ? <Alert type="info" showIcon message="Choose one store before recording an event." />
      : denied ? <Alert role="alert" type="error" showIcon message="You no longer have access to record events here. Check your assigned shift in Schedule." />
      : <>
        {!display && <div className="pc-home-event-options" role="group" aria-label="Claw machine actions">
          {['claw_prize', 'claw_refill'].map(value => <Button key={value} aria-pressed={kind === value} type={kind === value ? 'primary' : 'default'} disabled={mutation.pending}
            onClick={() => { setKind(value); form.resetFields(); setOptions([]) }}>{eventNames[value]}</Button>)}
        </div>}
        <p className="pc-home-muted">{exchange ? 'Record after receiving the digital payment and handing over the same amount in cash. This reduces the expected cash drawer.'
          : display ? 'This adds a record to the daily summary. Stock balances are unchanged.'
          : refill ? 'This adds a record to the daily summary. Once inventory counts are live, it also moves the stock into the claw machine.'
          : 'This adds a record to the daily summary. Once inventory counts are live, it also takes the stock out of the claw machine.'}</p>
        {mutation.notice}
        {searchError && <Alert role="alert" type="error" message={searchError} />}
        <Form form={form} layout="vertical" disabled={mutation.pending} onFinish={save}>
          {exchange ? <>
            <Form.Item name="tender" label="Payment received by" rules={[{ required: true }]}><Select options={[{ value: 'card', label: 'Card' }, { value: 'e_transfer', label: 'E-transfer' }]} /></Form.Item>
            <Form.Item name="amount" label="Cash exchanged (CAD)" rules={[{ validator: (_, value) => {
              const cents = tryParseMoneyToCents(value || '')
              return cents != null && cents > 0 ? Promise.resolve() : Promise.reject(new Error('Enter a positive amount, with at most two decimal places.'))
            } }]}><Input inputMode="decimal" prefix="$" /></Form.Item>
          </> : <>
            <Form.Item name="product_id" label="产品 Product" rules={[{ required: true, message: 'Choose the product from search results.' }]}>
              <Select showSearch filterOption={false} loading={searching} onSearch={findProduct} placeholder="Search product name or SKU"
                options={options.map(product => ({ value: product.id, label: `${product.sku} · ${productNames(product)}` }))} />
            </Form.Item>
            <Form.Item name="quantity" label="数量 Quantity" rules={[{ required: true }, { type: 'integer', min: 1 }]}><InputNumber min={1} precision={0} /></Form.Item>
            {refill && sources?.length !== 0 && <Form.Item name="source_location_id" label="从哪里拿 / Take from" rules={[{ required: true, message: 'Choose where the stock came from.' }]}>
              <Select loading={!sources} options={sources?.map(location => ({ value: location.id, label: location.name }))} />
            </Form.Item>}
          </>}
          <Form.Item name="note" label={exchange ? 'Payment reference / Note' : display ? '来源 From / Note' : '备注 Note (optional)'} rules={exchange ? [{ required: true, whitespace: true, message: 'Add the payment reference or a note to identify this exchange.' }] : []}>
            <Input.TextArea rows={2} maxLength={500} />
          </Form.Item>
          {exchange && <Form.Item name="confirmed" valuePropName="checked" rules={[{ validator: (_, value) => value === true ? Promise.resolve() : Promise.reject(new Error('Confirm the payment received and cash handed over.')) }]}>
            <Checkbox>I received this digital payment and handed over the same amount in cash.</Checkbox>
          </Form.Item>}
          <Button className="pc-exchange-save" htmlType="submit" type="primary" size="large" loading={mutation.saving} disabled={mutation.pending}>保存记录 Save record</Button>
        </Form>
      </>}
  </div>
}
