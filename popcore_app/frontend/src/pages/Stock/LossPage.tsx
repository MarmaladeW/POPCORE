import { Alert, Button, Input, InputNumber, Popconfirm, Skeleton, type InputRef } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { getLossPage, searchLossProducts, type DamagedItem, type LossPage as Page, type LossProduct, type LossReason, type LossRecord } from '../../api/losses'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { Panel } from '../../components/pc/Panel'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { formatCents } from '../../lib/money'
import { useAppStore } from '../../store'
import { torontoDate } from '../Dashboard/todayPresentation'
import useCheckoutMutation from '../Sales/useCheckoutMutation'
import './losses.css'

// 损耗 Loss (owner, 2026-11-03): managers and admins record stock that broke, was stolen or went missing.
// Broken goods wait on the damaged pile at their location until they go back on sale (as 瑕疵) or are
// thrown away; everything else leaves stock at once. The month's records and their value sit below.

const PLACES:Record<string,string> = { floor:'楼面 Floor', upstairs:'楼上 Upstairs', warehouse:'仓库 Warehouse', display:'陈列 Display', claw:'娃娃机 Claw' }
const UNITS:Record<string,string> = { box:'盒', set:'端', piece:'个' }
const BACK = ['upstairs', 'warehouse']  // back stock keeps its kraft colour
const REASONS:Array<{ key:LossReason; zh:string; en:string; effect:string }> = [
  { key:'broken', zh:'破损', en:'Broken', effect:'Kept aside on the damaged pile' },
  { key:'stolen', zh:'被偷', en:'Stolen', effect:'Leaves stock' },
  { key:'missing', zh:'丢失', en:'Missing', effect:'Leaves stock' },
  { key:'other', zh:'其他', en:'Other', effect:'Leaves stock · say what happened' },
]
const reasonOf = (key:LossReason) => REASONS.find(reason => reason.key === key)!
const place = (code:string, name:string) => PLACES[code] ?? name
const units = (amount:number, unit:string) => `${amount.toLocaleString('en-US')} ${UNITS[unit] ?? ''}`.trim()
const plural = (amount:number, word:string) => `${amount.toLocaleString('en-US')} ${word}${amount === 1 ? '' : 's'}`
const shortDate = (iso:string) => `${Number(iso.slice(5, 7))}-${Number(iso.slice(8, 10))}`
const messageOf = (cause:unknown, fallback:string) => (cause as { _serverMessage?:string })?._serverMessage || fallback

function shiftMonth(month:string, by:number) {
  const [year, number] = month.split('-').map(Number)
  const date = new Date(Date.UTC(year, number - 1 + by, 1))
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`
}
const monthTitle = (month:string) => `${month.slice(0, 4)}年${Number(month.slice(5, 7))}月`
const monthName = (month:string) => new Date(`${month}-15T12:00:00Z`).toLocaleString('en-US', { month:'long', timeZone:'UTC' })

export default function LossPage() {
  const store = useAppStore(state => state.selectedStore)
  const phone = useIsPhone()
  const current = torontoDate().slice(0, 7)
  const [month, setMonth] = useState(current)
  const [refresh, setRefresh] = useState(0)
  const [result, setResult] = useState<{ key:string; data?:Page; error?:string }>()
  const storeCode = store && store.code !== 'ALL' ? store.code : ''
  const key = `${storeCode}|${month}|${refresh}`
  useEffect(() => {
    if (!storeCode) return
    const controller = new AbortController()
    getLossPage(storeCode, month, controller.signal)
      .then(data => { if (!controller.signal.aborted) setResult({ key, data }) })
      .catch(cause => { if (!controller.signal.aborted) setResult({ key, error:messageOf(cause, 'The 损耗 page did not load.') }) })
    return () => controller.abort()
  }, [key, storeCode, month])
  const loaded = result?.key === key ? result : undefined
  // Keep the last page on screen while a refresh loads.
  const data = loaded?.data ?? (loaded ? undefined : result?.data?.store.code === storeCode ? result?.data : undefined)
  const reload = () => setRefresh(value => value + 1)

  return <div className="pc-loss">
    <PageHeader title="损耗" sub={phone ? undefined : `Loss${store?.name && storeCode ? ` · ${store.name}` : ''}`}
      back={phone ? { label:'库存', to:'/stock' } : undefined} />
    {!storeCode ? <Alert type="info" showIcon message="Choose one store to record losses." />
      : loaded?.error ? <Alert type="error" showIcon message={loaded.error} action={<Button onClick={reload}>Retry</Button>} />
      : !data ? <Skeleton active paragraph={{ rows:5 }} />
      : !data.live ? <p className="pc-notice">{storeCode} 还没上线 · 损耗 starts once this store’s stock is counted and live.</p>
      : <div className="pc-loss-layout">
        <section className="pc-loss-record" aria-label="记一笔损耗 Record a loss">
          <RecordLoss storeCode={storeCode} onSaved={() => { setMonth(current); reload() }} />
        </section>
        <DamagedPile storeCode={storeCode} pile={data.pile ?? []} onDone={reload} />
        <MonthFigures data={data} month={month} current={current} onMonth={setMonth} />
        <Records records={data.records ?? []} month={month} onDone={reload} />
      </div>}
  </div>
}

function RecordLoss({ storeCode, onSaved }:{ storeCode:string; onSaved:()=>void }) {
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<{ text:string; results?:LossProduct[]; error?:string }>()
  const [product, setProduct] = useState<LossProduct>()
  const [locationId, setLocationId] = useState<number>()
  const [reason, setReason] = useState<LossReason>()
  const [quantity, setQuantity] = useState<number|null>(1)
  const [note, setNote] = useState('')
  const [saved, setSaved] = useState<{ name:string; text:string }>()
  const searchBox = useRef<InputRef>(null)
  const mutation = useCheckoutMutation(data => {
    const loss = data.loss as LossRecord
    setSaved({ name:loss.name, text:`${units(loss.quantity, loss.unit)} · ${reasonOf(loss.reason).zh} ${reasonOf(loss.reason).en} · ${place(loss.location_code, loss.location_name)}` })
    setProduct(undefined); setQuery(''); setFound(undefined)
    onSaved()
    window.setTimeout(() => searchBox.current?.focus(), 0)
  })
  const locked = mutation.saving || mutation.pending

  useEffect(() => {
    const text = query.trim()
    if (!text) { setFound(undefined); return }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      searchLossProducts(storeCode, text, controller.signal)
        .then(results => { if (!controller.signal.aborted) setFound({ text, results }) })
        .catch(cause => { if (!controller.signal.aborted) setFound({ text, error:messageOf(cause, 'Search did not work. Try again.') }) })
    }, 200)
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [query, storeCode])

  function pick(next:LossProduct) {
    setProduct(next); setSaved(undefined)
    setLocationId(next.places.length === 1 ? next.places[0].location_id : undefined)
    setReason(undefined); setQuantity(1); setNote('')
  }
  // After a refused save (stock changed meanwhile), read this product's stock again.
  async function lookAgain() {
    if (!product) return
    const results = await searchLossProducts(storeCode, product.sku || product.name).catch(() => [])
    const fresh = results.find(item => item.product_id === product.product_id)
    if (fresh) { setProduct(fresh); if (!fresh.places.some(item => item.location_id === locationId)) setLocationId(undefined) }
    else { setProduct(undefined); setQuery('') }
  }

  if (!product) return <div className="pc-loss-search">
    <h2 className="pc-loss-title">记一笔损耗 <small>Record a loss</small></h2>
    <Input ref={searchBox} allowClear value={query} className="pc-loss-search-input" prefix={<Icon name="search" size={16} />}
      aria-label="Search a product with stock here" placeholder="搜索名称 / 记账名 / SKU"
      onChange={event => { setQuery(event.target.value); setSaved(undefined) }}
      onPressEnter={() => { if (found?.text === query.trim() && found.results?.length === 1) pick(found.results[0]) }} />
    {saved && <p className="pc-loss-saved" role="status"><Icon name="check" size={16} /><span><strong>已记录 Recorded</strong>{saved.name} · {saved.text}</span></p>}
    {found?.error && <Alert type="error" showIcon message={found.error} />}
    {found?.results && (found.results.length ? <ul className="pc-loss-results" aria-label="Products">
      {found.results.map(item => <li key={item.product_id}>
        <button type="button" onClick={() => pick(item)}>
          <span className="pc-row-main"><strong>{item.name}</strong><small>{[item.sku, item.price_cents != null ? `${formatCents(item.price_cents)} each` : 'No price'].filter(Boolean).join(' · ')}</small></span>
          <span className="pc-loss-where">{item.places.map(spot => <span key={spot.location_id} className={BACK.includes(spot.code) ? 'is-back' : undefined}>
            {place(spot.code, spot.name).split(' ')[0]} <b>{spot.available.toLocaleString('en-US')}</b></span>)}</span>
        </button>
      </li>)}
    </ul> : <p className="pc-loss-quiet">No product with stock at this store matches. Try the 记账名 or the SKU.</p>)}
    {!query.trim() && !saved && <p className="pc-loss-quiet">Find the product, then say where it was and what happened. Only products with stock here show up.</p>}
  </div>

  const spot = product.places.find(item => item.location_id === locationId)
  const count = quantity ?? 0
  const valid = !!spot && !!reason && Number.isSafeInteger(count) && count >= 1 && count <= spot.available && (reason !== 'other' || !!note.trim())
  const unitLabel = UNITS[product.unit] ?? ''
  function save() {
    if (!valid || !spot || !reason || locked) return
    void mutation.run('/losses', { store_code:storeCode, product_id:product!.product_id, location_id:spot.location_id, quantity:count,
      reason, expected_version:spot.version, ...(note.trim() ? { note:note.trim() } : {}) })
  }
  return <div className="pc-loss-card">
    <header className="pc-loss-card-head">
      <div><h2>{product.name}</h2><p>{[product.sku, product.price_cents != null ? `${formatCents(product.price_cents)} each` : 'No price set, so no value is counted'].filter(Boolean).join(' · ')}</p></div>
      <Button disabled={locked} onClick={() => { setProduct(undefined); window.setTimeout(() => searchBox.current?.focus(), 0) }}>换一个 Change</Button>
    </header>
    {mutation.notice}
    {mutation.error && !mutation.pending && <Button className="pc-loss-again" onClick={() => void lookAgain()}>重新查看库存 Check stock again</Button>}
    <fieldset className="pc-loss-field" disabled={locked}>
      <legend>在哪里 <small>Where was it</small></legend>
      <div className="pc-loss-choices">{product.places.map(item => <button key={item.location_id} type="button" aria-pressed={item.location_id === locationId}
        onClick={() => { setLocationId(item.location_id); if (count > item.available) setQuantity(item.available) }}>
        <span>{place(item.code, item.name)}</span><small>{units(item.available, product.unit)} here</small>
      </button>)}</div>
    </fieldset>
    <fieldset className="pc-loss-field" disabled={locked}>
      <legend>怎么了 <small>What happened</small></legend>
      <div className="pc-loss-choices pc-loss-reasons">{REASONS.map(item => <button key={item.key} type="button" aria-pressed={item.key === reason} onClick={() => setReason(item.key)}>
        <span>{item.zh} <small>{item.en}</small></span><small>{item.effect}</small>
      </button>)}</div>
    </fieldset>
    <div className="pc-loss-amount">
      <label htmlFor="pc-loss-quantity">数量 <small>Quantity</small></label>
      <span className="pc-loss-stepper">
        <Button aria-label="One fewer" icon={<Icon name="minus" size={16} />} disabled={locked || count <= 1} onClick={() => setQuantity(Math.max(1, count - 1))} />
        <InputNumber id="pc-loss-quantity" min={1} max={spot?.available} precision={0} controls={false} value={quantity} disabled={locked} inputMode="numeric"
          onChange={value => setQuantity(value)} />
        <Button aria-label="One more" icon={<Icon name="plus" size={16} />} disabled={locked || !spot || count >= spot.available}
          onClick={() => setQuantity(Math.min(spot?.available ?? 1, count + 1))} />
        <span>{unitLabel}</span>
      </span>
    </div>
    <label className="pc-loss-note">
      <span>备注 <small>{reason === 'other' ? 'What happened (needed for 其他)' : 'Note, if it helps later'}</small></span>
      <Input.TextArea rows={2} maxLength={500} value={note} disabled={locked} aria-label="备注 Note" onChange={event => setNote(event.target.value)}
        placeholder={reason === 'broken' ? 'e.g. dropped while restocking' : reason === 'stolen' ? 'e.g. seen on camera at 3 pm' : ''} />
    </label>
    <p className="pc-loss-effect" aria-live="polite">{spot && reason && count >= 1 && count <= spot.available
      ? <>{place(spot.code, spot.name).split(' ')[0]} {spot.available.toLocaleString('en-US')} → {(spot.available - count).toLocaleString('en-US')} · {units(count, product.unit)} {reason === 'broken' ? 'go to the damaged pile' : 'leave stock'}{product.price_cents != null && <> · <b>{formatCents(product.price_cents * count)}</b></>}</>
      : spot && count > spot.available ? `${place(spot.code, spot.name)} has only ${units(spot.available, product.unit)}.`
      : 'Choose where it was and what happened.'}</p>
    <div className="pc-loss-actions">
      <Button type="primary" disabled={!valid || locked} loading={mutation.saving} onClick={save}>记录损耗 Record loss</Button>
      <Button disabled={locked} onClick={() => setProduct(undefined)}>取消 Cancel</Button>
    </div>
  </div>
}

function DamagedPile({ storeCode, pile, onDone }:{ storeCode:string; pile:DamagedItem[]; onDone:()=>void }) {
  const [amounts, setAmounts] = useState<Record<string, number|null>>({})
  const [busy, setBusy] = useState<string>()
  const mutation = useCheckoutMutation(() => { setBusy(undefined); setAmounts({}); onDone() })
  const locked = mutation.saving || mutation.pending
  const value = pile.reduce((sum, item) => sum + (item.value_cents ?? 0), 0)
  function act(item:DamagedItem, action:'restore'|'discard') {
    const id = `${item.product_id}|${item.location_id}`
    const amount = amounts[id] ?? item.quantity
    if (locked || !amount || amount > item.quantity) return
    setBusy(id)
    void mutation.run('/losses/pile', { store_code:storeCode, product_id:item.product_id, location_id:item.location_id,
      quantity:amount, action, expected_version:item.version })
  }
  return <Panel zh="损坏待处理" en="Damaged pile" label="损坏待处理 Damaged pile" className="pc-loss-pile"
    count={pile.length ? (value ? formatCents(value) : plural(pile.length, 'product')) : undefined} tone="warn">
    {mutation.notice && <div className="pc-loss-panel-notice">{mutation.notice}</div>}
    {!pile.length ? <p className="pc-quiet"><strong>没有待处理的损坏商品</strong>Broken goods you record wait here until you put them back on sale or throw them away.</p>
      : <ul className="pc-loss-rows">{pile.map(item => {
        const id = `${item.product_id}|${item.location_id}`
        const amount = amounts[id] ?? item.quantity
        return <li key={id} className="pc-row pc-loss-pile-row">
          <span className="pc-row-main"><strong>{item.name}</strong><small>{place(item.location_code, item.location_name)}{item.value_cents != null ? ` · ${formatCents(item.value_cents)}` : ''}</small></span>
          <span className="pc-loss-num">{units(item.quantity, item.unit)}<small>set aside</small></span>
          <span className="pc-loss-pile-actions">
            {item.quantity > 1 && <InputNumber aria-label={`How many of ${item.name}`} min={1} max={item.quantity} precision={0} controls={false} value={amount}
              disabled={locked} onChange={next => setAmounts(current => ({ ...current, [id]:next }))} />}
            <Button disabled={locked || !amount} loading={mutation.saving && busy === id} onClick={() => act(item, 'restore')}>放回可售 <small>Back to sale</small></Button>
            <Popconfirm title={`Throw away ${units(amount ?? 0, item.unit)} of ${item.name}?`} description="They leave stock for good." okText="报废 Throw away" cancelText="Keep"
              okButtonProps={{ danger:true }} disabled={locked || !amount} onConfirm={() => act(item, 'discard')}>
              <Button danger disabled={locked || !amount}>报废 <small>Throw away</small></Button>
            </Popconfirm>
          </span>
        </li>
      })}</ul>}
  </Panel>
}

function MonthFigures({ data, month, current, onMonth }:{ data:Page; month:string; current:string; onMonth:(month:string)=>void }) {
  const totals = data.totals
  const records = REASONS.reduce((sum, item) => sum + (totals?.[item.key].records ?? 0), 0)
  const priceless = (unpriced:number) => unpriced ? ` · ${unpriced} without a price` : ''
  return <section className="pc-loss-month" aria-label={`损耗 ${monthTitle(month)}`}>
    <div className="pc-loss-month-head">
      <h2 className="pc-loss-title">每月损耗 <small>Losses by month</small></h2>
      <div className="pc-day pc-loss-months">
        <button type="button" aria-label="Previous month" onClick={() => onMonth(shiftMonth(month, -1))}><Icon name="back" size={16} /></button>
        <span className="pc-day-date"><strong>{monthTitle(month)}</strong><small>{month === current ? '本月 This month' : month}</small></span>
        <button type="button" aria-label="Next month" disabled={month >= current} onClick={() => onMonth(shiftMonth(month, 1))}><Icon name="right" size={16} /></button>
      </div>
    </div>
    <div className="pc-figures pc-loss-figures">
      <div className="pc-loss-total">
        <span>合计 <small>Total</small></span>
        <strong>{formatCents(data.value_cents ?? 0)}</strong>
        <small>{records ? `${plural(data.units ?? 0, 'unit')} · ${plural(records, 'record')}${priceless(data.unpriced_units ?? 0)}` : 'Nothing recorded'}</small>
      </div>
      {REASONS.map(item => {
        const total = totals?.[item.key]
        return <div key={item.key} className={total?.records ? undefined : 'is-none'}>
          <span>{item.zh} <small>{item.en}</small></span>
          <strong>{formatCents(total?.value_cents ?? 0)}</strong>
          <small>{total?.records ? `${plural(total.units, 'unit')}${priceless(total.unpriced_units)}` : 'None'}</small>
        </div>
      })}
    </div>
  </section>
}

function Records({ records, month, onDone }:{ records:LossRecord[]; month:string; onDone:()=>void }) {
  const [busy, setBusy] = useState<number>()
  const mutation = useCheckoutMutation(() => { setBusy(undefined); onDone() })
  const locked = mutation.saving || mutation.pending
  return <Panel zh="记录" en={`Records · ${monthName(month)}`} label="损耗记录 Loss records" className="pc-loss-records">
    {mutation.notice && <div className="pc-loss-panel-notice">{mutation.notice}</div>}
    {!records.length ? <p className="pc-quiet"><strong>{monthTitle(month)}没有损耗记录</strong>Nothing was recorded as broken, stolen or missing this month.</p>
      : <ul className="pc-loss-rows">{records.map(record => {
        const why = reasonOf(record.reason)
        return <li key={record.id} className={`pc-row pc-loss-record-row${record.undone ? ' is-undone' : ''}`}>
          <span className="pc-row-label pc-loss-when"><span>{shortDate(record.business_date)}</span><span className="pc-tag" title={why.en}>{why.zh}</span></span>
          <span className="pc-row-main">
            <strong>{record.name}</strong>
            <small>{[place(record.location_code, record.location_name), record.note, record.undone ? `撤销 Undone by ${record.undone_by_name ?? '—'}` : `by ${record.actor_name}`].filter(Boolean).join(' · ')}</small>
          </span>
          <span className="pc-loss-num">−{units(record.quantity, record.unit)}<small>{record.value_cents != null ? formatCents(record.value_cents) : 'No price'}</small></span>
          <span className="pc-loss-undo">{record.undone ? <span className="pc-loss-undone">已撤销</span>
            : <Popconfirm title={`Undo this ${why.en.toLowerCase()} record?`} okText="撤销 Undo" cancelText="Keep"
                description={`${units(record.quantity, record.unit)} of ${record.name} go back to ${place(record.location_code, record.location_name)}${record.reason === 'broken' ? ' from the damaged pile' : ''}.`}
                disabled={locked} onConfirm={() => { setBusy(record.id); void mutation.run(`/losses/${record.id}/undo`, {}) }}>
              <Button type="text" disabled={locked} loading={mutation.saving && busy === record.id}>撤销 <small>Undo</small></Button>
            </Popconfirm>}</span>
        </li>
      })}</ul>}
  </Panel>
}
