import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Input, Popconfirm, Skeleton } from 'antd'
import { Link } from 'react-router-dom'
import client from '../../api/client'
import { searchGoodsProducts, type GoodsProduct } from '../../api/goods'
import { getStaffDiscounts, type DiscountPerson } from '../../api/staffDiscounts'
import { useAppStore } from '../../store'
import { formatCents, tryParseMoneyToCents } from '../../lib/money'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { Panel } from '../../components/pc/Panel'
import { checkoutStores, errorMessage, type Access } from './checkoutParts'
import { CheckoutTabs } from './CheckoutNav'
import { useIsPhone } from '../../components/pc/useIsPhone'
import {
  addToCart, canSave, changeItem, CNY_TENDERS, itemsText, PHOTO_TENDERS, removeItem, requestLines, SaveKey, tenderName,
  shortDay, TENDERS, waitingFor, type CartItem, type Quote, type QuoteLine, type ReviewStock, type SaleRow, type SalesDay,
  type Tender,
} from './quickSaleCart'
import './Checkout.css'
import './QuickSale.css'

const yuan = (fen: number) => `¥${(fen / 100).toFixed(2)}`
const status = (cause: unknown) => (cause as { response?: { status: number } })?.response?.status
const noAnswer = (cause: unknown) => !(cause as { response?: unknown })?.response
// "13:05" in Toronto time; a server time without a zone is UTC.
const clock = (timestamp: string) => {
  const parsed = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(timestamp) ? timestamp : `${timestamp.replace(' ', 'T')}Z`)
  return Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'America/Toronto' })
}
export const Problem = ({ children }: { children: React.ReactNode }) => <p className="qs-problem" role="alert"><i className="pc-dot pc-dot-out" aria-hidden="true" />{children}</p>

/** 记一笔 Quick sale (owner, 2026-11-21): the 随手记 of every sale, cash, e-transfer, WeChat Pay, Alipay and the card
 *  machine, recorded in POPCORE by its 记账名 and priced by the app. Its stock comes off with the day's reviewed 日报.
 *  Show what's needed, reduce noise: one price (the tender's), one thing missing at a time on Save. */
export default function QuickSalePage() {
  const phone = useIsPhone()
  const selected = useAppStore(state => state.selectedStore), setSelected = useAppStore(state => state.setSelectedStore)
  const allStores = useAppStore(state => state.stores)
  const [access, setAccess] = useState<Access>(), [accessError, setAccessError] = useState('')
  const [cart, setCart] = useState<CartItem[]>([]), [discount, setDiscount] = useState(''), [showDiscount, setShowDiscount] = useState(false)
  const [quote, setQuote] = useState<Quote>(), [quoteError, setQuoteError] = useState(''), [quoting, setQuoting] = useState(false)
  const [tender, setTender] = useState<Tender | ''>(''), [saving, setSaving] = useState(false), [saveError, setSaveError] = useState('')
  const [saved, setSaved] = useState<SaleRow>(), [day, setDay] = useState<SalesDay>(), [dayError, setDayError] = useState('')
  const [refresh, setRefresh] = useState(0), [requote, setRequote] = useState(0)
  // A manager may open an earlier day to review it; sales are always recorded for today.
  const [viewDay, setViewDay] = useState<string>()
  // 员工折扣 (owner, 2026-11-24: in the app, not the 日报): a tick, then who bought.
  const [staffOpen, setStaffOpen] = useState(false), [buyer, setBuyer] = useState<number>()
  const [people, setPeople] = useState<DiscountPerson[]>(), [peopleError, setPeopleError] = useState('')
  const saveKey = useRef(new SaveKey())
  const stores = access ? checkoutStores(access) : []
  const store = stores.find(entry => entry.id === selected?.id)
  const discountCents = showDiscount ? tryParseMoneyToCents(discount) : 0
  const lines = useMemo(() => requestLines(cart), [cart])
  const quoteBody = JSON.stringify({ store_id: store?.id, lines, discount_cents: discountCents || 0 })

  useEffect(() => {
    const controller = new AbortController()
    client.get<Access>('/checkouts/access', { signal: controller.signal, timeout: 15000 })
      .then(response => { setAccess(response.data); setAccessError('') })
      .catch(cause => { if (!controller.signal.aborted) setAccessError(status(cause) === 403 ? 'access' : errorMessage(cause)) })
    return () => controller.abort()
  }, [])
  useEffect(() => {
    if (access && !store && stores.length === 1) {
      const only = stores[0]
      setSelected(allStores.find(entry => entry.id === only.id) || { ...only, color: '#4F46E5' })
    }
  }, [access, store?.id])

  // Every change of the cart is priced again by the server; the screen shows only its numbers.
  useEffect(() => {
    if (!store || !cart.length || discountCents === null) { setQuote(undefined); setQuoteError(''); return }
    const controller = new AbortController()
    setQuoting(true)
    const timer = setTimeout(() => {
      client.post<Quote>('/quick-sales/quote', JSON.parse(quoteBody), { signal: controller.signal, timeout: 15000 })
        .then(response => { setQuote(response.data); setQuoteError('') })
        .catch(cause => { if (!controller.signal.aborted) { setQuote(undefined); setQuoteError(errorMessage(cause)) } })
        .finally(() => { if (!controller.signal.aborted) setQuoting(false) })
    }, 200)
    return () => { controller.abort(); clearTimeout(timer) }
  }, [quoteBody, requote])

  useEffect(() => {
    if (!store) { setDay(undefined); return }
    const controller = new AbortController()
    client.get<SalesDay>('/quick-sales', { params: { store_id: store.id, ...(viewDay ? { date: viewDay } : {}) }, signal: controller.signal, timeout: 15000 })
      .then(response => { setDay(response.data); setDayError('') })
      .catch(cause => { if (!controller.signal.aborted) setDayError(errorMessage(cause)) })
    return () => controller.abort()
  }, [store?.id, refresh, viewDay])

  useEffect(() => {
    if (!staffOpen || people) return
    const controller = new AbortController()
    getStaffDiscounts((day?.today ?? new Date().toISOString()).slice(0, 7), controller.signal)
      .then(page => { setPeople(page.people.filter(person => person.eligible && person.active)); setPeopleError('') })
      .catch(cause => { if (!controller.signal.aborted) setPeopleError(errorMessage(cause)) })
    return () => controller.abort()
  }, [staffOpen, people])

  const current = !quoting && quote && JSON.stringify({ store_id: quote.store_id, lines, discount_cents: quote.discount_cents }) === quoteBody
    ? quote : undefined
  const amount = current && tender && current.tenders ? current.tenders[tender] : undefined
  const cny = current?.cny && tender && CNY_TENDERS.includes(tender) ? current.cny : null

  async function save() {
    if (!store || !current || !tender || amount === undefined || !canSave(current, tender) || (staffOpen && !buyer)) return
    const body = { store_id: store.id, lines, discount_cents: current.discount_cents, tender, expected_amount_cents: amount,
      ...(cny ? { cny_fen: cny.fen } : {}), ...(staffOpen && buyer ? { staff_discount_employee_id: buyer } : {}) }
    const key = saveKey.current.for(JSON.stringify(body))
    setSaving(true); setSaveError('')
    try {
      const { data } = await client.post<SaleRow>('/quick-sales', body, { headers: { 'Idempotency-Key': key }, timeout: 20000 })
      saveKey.current.reset()
      setSaved(data); setCart([]); setTender(''); setDiscount(''); setShowDiscount(false); setRefresh(n => n + 1)
      setStaffOpen(false); setBuyer(undefined); setPeople(undefined)
    } catch (cause) {
      // No answer: the sale may have saved, so the same tap again is the same sale. Any answer starts fresh.
      if (!noAnswer(cause)) saveKey.current.reset()
      // No answer: the list below is read again, so the cashier sees whether this sale went in before tapping again.
      setSaveError(noAnswer(cause) ? '网络断了。先看下面 今天 有没有这一笔；没有就再按一次保存，不会记两次。' : errorMessage(cause))
      setRefresh(n => n + 1)
      // A changed price or rate: price the cart again so the new amount shows and Save works.
      if (status(cause) === 409) { setQuote(undefined); setRequote(n => n + 1) }
    } finally { setSaving(false) }
  }

  const header = <PageHeader title="记一笔" sub={store ? store.name : 'Record a sale'} />
  if (accessError) return <div className="qs-page co-workspace">{header}{!phone && <CheckoutTabs />}
    <div className="co-empty"><h2>{accessError === 'access' ? '用自己的员工账号记账' : '打不开记一笔'}</h2>
      <p>{accessError === 'access' ? '今天在这家店有排班的员工，用自己的手机登录后记账。' : accessError}</p>
      <Link to="/schedule">排班</Link></div></div>
  if (!access) return <div className="qs-page co-workspace">{header}<Skeleton active paragraph={{ rows: 6 }} /></div>
  if (!stores.length) return <div className="qs-page co-workspace">{header}{!phone && <CheckoutTabs />}
    <div className="co-empty"><h2>{access.live_stores.length ? '这家店的员工记账还没开' : '今天没有排班'}</h2>
      <p>{access.live_stores.length ? '店长在 设置 → 员工买单 打开后就能记账。之前照常写日报。' : '在你今天排班的店里记账。'}</p>
      <Link to="/schedule">排班</Link></div></div>

  const photoTender = tender && PHOTO_TENDERS.includes(tender)
  return <div className="qs-page co-workspace">
    {header}
    {!phone && <CheckoutTabs />}
    {stores.length > 1 && <div className="qs-stores" role="group" aria-label="Store">
      {stores.map(entry => <button key={entry.id} type="button" aria-pressed={entry.id === store?.id}
        onClick={() => { setSelected(allStores.find(s => s.id === entry.id) || { ...entry, color: '#4F46E5' }); setCart([]); setSaved(undefined) }}>
        {entry.name}</button>)}
    </div>}
    {!store ? <p className="qs-quiet">选你今天上班的店。</p> : <div className="qs-columns">
      <div className="qs-main">
        {saved && <Saved sale={saved} onPhoto={() => { setRefresh(n => n + 1); setSaved(undefined) }} onClose={() => setSaved(undefined)}
          onUndone={() => { setRefresh(n => n + 1); setSaved(undefined) }} />}
        <ProductSearch phone={phone} onPick={product => { setSaved(undefined); setCart(old => addToCart(old, product)) }} />
        {cart.length > 0 && <Panel zh="这一笔" className="qs-cart" label="This sale"
          action={<button type="button" className="qs-link" onClick={() => { setCart([]); setTender('') }}>清空</button>}>
          <ul className="qs-lines" aria-label="Items">
            {cart.map((item, index) => <CartLine key={`${item.productId}-${item.fromDisplay}`} item={item}
              line={current?.lines[index]}
              change={change => setCart(old => changeItem(old, index, change))}
              remove={() => setCart(old => removeItem(old, index))} />)}
          </ul>
          <div className="qs-discount">
            {showDiscount ? <>
              <label>优惠 $<Input inputMode="decimal" aria-label="Discount" value={discount} autoFocus
                status={discountCents === null ? 'error' : undefined} onChange={event => setDiscount(event.target.value)} /></label>
              <button type="button" className="qs-link" onClick={() => { setShowDiscount(false); setDiscount('') }}>不要优惠</button>
            </> : <button type="button" className="qs-link" onClick={() => setShowDiscount(true)}>+ 优惠</button>}
            {!staffOpen && <button type="button" className="qs-link" onClick={() => setStaffOpen(true)}>+ 员工折扣</button>}
          </div>
          {staffOpen && <div className="qs-staff" role="group" aria-label="Staff discount">
            <p><span>员工折扣 · 谁买的</span>
              <button type="button" className="qs-link" onClick={() => { setStaffOpen(false); setBuyer(undefined) }}>不是员工折扣</button></p>
            {peopleError ? <Problem>{peopleError}</Problem> : !people ? <p className="qs-quiet">…</p>
              : !people.length ? <p className="qs-quiet">还没有可以用员工折扣的员工，请店长在 员工 里设职级。</p>
              : <div className="qs-staff-people">{people.map(person => <button key={person.employee_id} type="button" className="qs-chip"
                aria-pressed={buyer === person.employee_id} onClick={() => setBuyer(person.employee_id)}>
                {person.name}<small>剩 {person.left ?? 0}</small></button>)}</div>}
          </div>}
          {quoteError && <Problem>{quoteError}</Problem>}
          <div className="qs-tenders" role="radiogroup" aria-label="Paid by">
            {TENDERS.map(entry => {
              const cents = current?.tenders ? current.tenders[entry.value] : undefined
              const noRate = CNY_TENDERS.includes(entry.value) && !!current?.tenders && !current.cny
              return <button key={entry.value} type="button" role="radio" aria-checked={tender === entry.value}
                aria-label={`${entry.zh} ${entry.en}${cents !== undefined ? ' ' + formatCents(cents) : ''}`}
                disabled={saving || noRate} onClick={() => setTender(entry.value)}>
                <span>{entry.zh}</span>
                <strong>{noRate ? '—' : cents === undefined ? '…' : formatCents(cents)}</strong>
                {noRate ? <small>请店长设汇率</small>
                  : CNY_TENDERS.includes(entry.value) && current?.cny ? <small className="qs-cny">{yuan(current.cny.fen)}</small> : null}
              </button>
            })}
          </div>
          {tender === 'card' && current?.tenders && <p className="qs-quiet">含 HST {formatCents(current.tax_cents)}</p>}
          <div className="qs-paybar">
            {saveError && <Problem>{saveError}</Problem>}
            <Button type="primary" block size="large" className="qs-save" loading={saving}
              disabled={!current || !tender || !canSave(current, tender) || (staffOpen && !buyer)} onClick={save}>
              {staffOpen && !buyer && current && tender && canSave(current, tender) ? '选哪位员工买的'
                : current && tender && amount !== undefined && canSave(current, tender)
                ? <span className="qs-save-label"><span>保存 · {tenderName(tender)} {formatCents(amount)}{cny ? ` · ${yuan(cny.fen)}` : ''}</span>{photoTender ? <small>然后拍付款截图</small> : null}</span>
                : waitingFor(current, tender)}
            </Button>
          </div>
        </Panel>}
      </div>
      <div className="qs-side">
        <Today day={day} error={dayError} storeCode={store.code} changed={() => setRefresh(n => n + 1)}
          open={next => { setDay(undefined); setViewDay(next) }} />
      </div>
    </div>}
  </div>
}

export function ProductSearch({ phone, onPick }: { phone: boolean; onPick: (product: { id: number; name: string; sku: string }) => void }) {
  const [text, setText] = useState(''), [found, setFound] = useState<GoodsProduct[]>(), [error, setError] = useState('')
  const input = useRef<React.ComponentRef<typeof Input>>(null)
  const query = text.trim()
  useEffect(() => {
    setFound(undefined); setError('')
    if (!query) return
    const controller = new AbortController()
    const timer = setTimeout(() => searchGoodsProducts(query, controller.signal)
      // A whole set (整端) sells too: the 日报 takes it off as its boxes.
      .then(rows => setFound(rows.slice(0, 8)))
      .catch(() => { if (!controller.signal.aborted) setError('搜索不了，请再试。') }), 200)
    return () => { controller.abort(); clearTimeout(timer) }
  }, [query])
  const sellable = (product: GoodsProduct) => product.identity_status === 'verified' && !!product.stock_form
  return <section className="qs-search" aria-label="Find a product">
    <Input ref={input} size="large" allowClear prefix={<Icon name="search" size={18} />} value={text} autoComplete="off"
      aria-label="记账名、名字或编号 Name or code" placeholder="记账名 / 名字 / 编号" onChange={event => setText(event.target.value)} />
    {error && <Problem>{error}</Problem>}
    {query && !error && !found && <p className="qs-quiet" role="status">搜索中…</p>}
    {found && found.length > 0 && <ul className="qs-results" aria-label="Products found">
      {found.map(product => <li key={product.id}><button type="button" disabled={!sellable(product)} onClick={() => {
        onPick({ id: product.id, name: product.jizhanming || product.name_cn_en || product.sku, sku: product.sku })
        setText('')
        // On a phone the keyboard closes so the cart and the tenders show; on a computer the next item can be typed.
        if (phone) input.current?.blur(); else input.current?.focus()
      }}>
        <span><strong>{product.jizhanming || product.name_cn_en}</strong>
          {sellable(product) ? product.jizhanming && product.name_cn_en && product.name_cn_en !== product.jizhanming && <small>{product.name_cn_en}</small>
            : <small>还没核对，告诉店长</small>}</span>
        {sellable(product) && <><em>{product.price ? formatCents(Math.round(product.price * 100)) : ''}</em><Icon name="plus" size={16} /></>}
      </button></li>)}
    </ul>}
    {found && !found.length && <p className="qs-quiet">没找到「{query}」</p>}
  </section>
}

function CartLine({ item, line, change, remove }: { item: CartItem; line?: QuoteLine; change: (value: Partial<CartItem>) => void; remove: () => void }) {
  const [editing, setEditing] = useState(false), [price, setPrice] = useState('')
  const missing = line && line.unit_price_cents === null
  const typed = tryParseMoneyToCents(price)
  const apply = () => { if (typed) { change({ priceCents: typed }); setEditing(false) } }
  return <li className="qs-line">
    <div className="qs-line-head">
      <strong className="qs-line-name">{item.name}</strong>
      <span className="qs-qty" role="group" aria-label={`${item.name} quantity`}>
        <button type="button" aria-label={item.quantity > 1 ? 'Fewer' : `Remove ${item.name}`}
          onClick={() => item.quantity > 1 ? change({ quantity: item.quantity - 1 }) : remove()}><Icon name="minus" size={16} /></button>
        <output aria-live="polite">{item.quantity}</output>
        <button type="button" aria-label="More" onClick={() => change({ quantity: Math.min(999, item.quantity + 1) })}><Icon name="plus" size={16} /></button>
      </span>
    </div>
    <div className="qs-line-foot">
      <button type="button" className="qs-chip" aria-pressed={item.fromDisplay} onClick={() => change({ fromDisplay: !item.fromDisplay })}>Display</button>
      {(editing || missing) ? <span className="qs-price-edit">
        <Input inputMode="decimal" aria-label={`${item.name} price`} placeholder="价格 $" value={price} autoFocus={editing}
          status={price && typed === null ? 'error' : undefined} onChange={event => setPrice(event.target.value)} onPressEnter={apply} />
        <button type="button" className="qs-link" disabled={!typed} onClick={apply}>好</button>
        {item.priceCents && <button type="button" className="qs-link" onClick={() => { change({ priceCents: undefined }); setPrice(''); setEditing(false) }}>原价</button>}
      </span> : <button type="button" className="qs-link" onClick={() => setEditing(true)}>
        {line?.unit_price_cents != null ? `${formatCents(line.unit_price_cents)} 改价` : '…'}
      </button>}
      <span className="qs-line-total">{line?.line_cents != null ? formatCents(line.line_cents) : ''}</span>
    </div>
  </li>
}

function PhotoButtons({ sale, payment, done, big = false }: { sale: SaleRow; payment: SaleRow['payments'][number]; done: () => void; big?: boolean }) {
  const camera = useRef<HTMLInputElement>(null), upload = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const key = useRef('')
  async function send(file: File | undefined) {
    if (!file) return
    const body = new FormData(); body.append('image', file)
    key.current = key.current || crypto.randomUUID()
    setBusy(true); setError('')
    try {
      await client.post(`/checkouts/${sale.id}/attempts/${payment.id}/evidence`, body, { headers: { 'Idempotency-Key': key.current }, timeout: 30000 })
      key.current = ''; done()
    } catch (cause) { if (!noAnswer(cause)) key.current = ''; setError(errorMessage(cause)) } finally { setBusy(false) }
  }
  return <div className={big ? 'qs-photo qs-photo-big' : 'qs-photo'}>
    <input ref={camera} className="co-file-input" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" aria-label="Payment photo"
      onChange={event => { send(event.target.files?.[0]); event.target.value = '' }} />
    <input ref={upload} className="co-file-input" type="file" accept="image/jpeg,image/png,image/webp" aria-label="Upload payment photo"
      onChange={event => { send(event.target.files?.[0]); event.target.value = '' }} />
    <Button type={big ? 'primary' : 'default'} size={big ? 'large' : 'middle'} loading={busy} icon={<Icon name="camera" size={16} />}
      onClick={() => camera.current?.click()}>拍付款截图</Button>
    <Button size={big ? 'large' : 'middle'} disabled={busy} onClick={() => upload.current?.click()}>上传截图</Button>
    {error && <Problem>{error}</Problem>}
  </div>
}

/** 撤销: a sale recorded by mistake (wrong item, wrong way of paying). It is kept, shown as 已撤销, and leaves the
 *  day's total and the 日报 summary; record it again correctly. */
function UndoButton({ sale, done }: { sale: SaleRow; done: () => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [reason, setReason] = useState('')
  const key = useRef('')
  async function undo() {
    key.current = key.current || crypto.randomUUID()
    setBusy(true); setError('')
    try {
      const { data } = await client.post<SaleRow>(`/quick-sales/${sale.id}/undo`, reason.trim() ? { reason: reason.trim() } : {},
        { headers: { 'Idempotency-Key': key.current }, timeout: 20000 })
      key.current = ''
      done()
    } catch (cause) { if (!noAnswer(cause)) key.current = ''; setError(errorMessage(cause)) } finally { setBusy(false) }
  }
  return <>
    <Popconfirm title="撤销这一笔？" okText="撤销" cancelText="不了" onConfirm={undo} okButtonProps={{ danger: true }}
      description={<Input aria-label="Why" placeholder="原因（可以不写）" value={reason} maxLength={200} onChange={event => setReason(event.target.value)} />}>
      <button type="button" className="qs-link" disabled={busy}>{busy ? '撤销中…' : '撤销'}</button>
    </Popconfirm>
    {error && <Problem>{error}</Problem>}
  </>
}

function Saved({ sale, onPhoto, onClose, onUndone }: { sale: SaleRow; onPhoto: () => void; onClose: () => void; onUndone: () => void }) {
  const payment = sale.payments[0]
  return <section className="qs-saved" role="status" aria-label="Saved">
    <p className="qs-saved-head"><i className="pc-dot pc-dot-ok" aria-hidden="true" /><strong>已记好</strong>
      {payment && <span>{tenderName(payment.tender)} {formatCents(payment.amount_cents)}{payment.cny_fen != null ? ` · ${yuan(payment.cny_fen)}` : ''}</span>}</p>
    <p className="qs-quiet">{itemsText(sale.lines)}{sale.staff_discount ? ` · 员工折扣 ${sale.staff_discount}` : ''}</p>
    {sale.needs_photo && payment && <PhotoButtons sale={sale} payment={payment} done={onPhoto} big />}
    <div className="qs-saved-actions">
      {sale.needs_photo && payment && <button type="button" className="qs-link" onClick={onClose}>稍后再拍</button>}
      {sale.can_undo && <UndoButton sale={sale} done={onUndone} />}
    </div>
  </section>
}

/** 审核 Review the day (owner, 2026-11-24: no 日报 pasting): the day's stock comes off once, when a manager looks over
 *  the day's sales here and confirms. The preview shows what would move before anything does. */
function Review({ day, storeCode, changed }: { day: SalesDay; storeCode: string; changed: () => void }) {
  const [preview, setPreview] = useState<{ stock: ReviewStock | null; digest?: string }>()
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [done, setDone] = useState('')
  const review = day.review
  // The cash counted that day: one box, not required (owner, 2026-11-24: in the app, not the 日报).
  const [cash, setCash] = useState('')
  useEffect(() => { setPreview(undefined); setError(''); setDone('') }, [day.business_date, day.store_id])
  useEffect(() => { setCash(review.cash_counted_cents == null ? '' : (review.cash_counted_cents / 100).toFixed(2)) },
    [day.business_date, day.store_id, review.cash_counted_cents])
  const cashCents = cash.trim() ? tryParseMoneyToCents(cash) : undefined
  async function send(confirm: boolean) {
    if (confirm && cashCents === null) { setError('现金写成数字，比如 935.05'); return }
    setBusy(true); setError('')
    try {
      const { data } = await client.post('/sales/submit_daily_report', {
        source: 'quick_sale', date: day.business_date, store_code: storeCode,
        ...(confirm ? { stock_digest: preview?.digest, ...(cashCents != null ? { cash_counted_cents: cashCents } : {}) }
          : { dry_run: true }) }, { timeout: 60000 })
      if (confirm) {
        setPreview(undefined)
        setDone(data.stock ? `这次扣了 ${data.stock.taken}${data.stock.returned ? `，放回 ${data.stock.returned}` : ''}` : '')
        changed()
      } else setPreview({ stock: data.stock ?? null, digest: data.stock_digest })
    } catch (cause) {
      const again = (cause as { response?: { data?: { code?: string; stock?: ReviewStock; stock_digest?: string } } })?.response?.data
      // What would move changed since the preview (a sale came in): show the new preview to confirm.
      if (again?.code === 'stock_confirmation_required') setPreview({ stock: again.stock ?? null, digest: again.stock_digest })
      else setError(errorMessage(cause))
    } finally { setBusy(false) }
  }
  const stock = preview?.stock
  const status = review.state === 'reviewed' ? <><i className="pc-dot pc-dot-ok" aria-hidden="true" />已审核，库存已扣{review.by ? ` · ${review.by}` : ''}{review.at ? ` · ${clock(review.at)}` : ''}{review.cash_counted_cents != null ? ` · 现金 ${formatCents(review.cash_counted_cents)}` : ''}</>
    : review.state === 'changed' ? <><i className="pc-dot pc-dot-warn" aria-hidden="true" />审核后有改动，再审核一次</>
    : review.state === 'pasted' ? <><i className="pc-dot pc-dot-ok" aria-hidden="true" />这天贴过日报，库存按日报扣了</>
    : day.count ? <><i className="pc-dot pc-dot-warn" aria-hidden="true" />库存还没扣，店长审核后一起扣</> : null
  const canReview = day.can_review && (review.state === 'changed' || (review.state === 'none' && day.count > 0))
  return <section className="qs-review" aria-label="Review the day">
    {status && <p className="qs-review-status" role="status">{status}</p>}
    {review.state === 'pasted' && review.differs.length > 0 && <ul className="qs-differs" aria-label="Differs from the pasted report">
      {review.differs.map(row => <li key={row.product_id}><span>{row.name}</span><span>记一笔 {row.quick} · 日报 {row.report}</span></li>)}
    </ul>}
    {preview && <div className="qs-preview" role="group" aria-label="What this review moves">
      <p><strong>{stock ? `从楼下扣 ${stock.taken}${stock.returned ? `，放回 ${stock.returned}` : ''}` : '这家店不扣库存'}</strong></p>
      {(stock?.lines ?? []).filter(line => line.moving).map(line => <p key={line.line_key}>{line.label} · {line.from?.name} {line.moving}</p>)}
      {(stock?.sets_opened ?? []).map(opened => <p key={opened.product_id}>{opened.text}</p>)}
      {!!stock?.short.length && <div className="qs-preview-short">
        <p><i className="pc-dot pc-dot-out" aria-hidden="true" />电脑里楼下不够，先扣有的，其余留待核对：</p>
        <ul>{stock.short.map(row => <li key={row.product_id}>{row.name} · 差 {row.quantity}</li>)}</ul>
      </div>}
      <label className="qs-cash">数到的现金 $<Input inputMode="decimal" aria-label="Cash counted" placeholder="可不填" value={cash}
        status={cashCents === null ? 'error' : undefined} onChange={event => setCash(event.target.value)} />
        {day.cash_cents > 0 && <small>现金销售 {formatCents(day.cash_cents)}</small>}</label>
      <div className="qs-preview-actions">
        <Button type="primary" size="large" loading={busy} onClick={() => send(true)}>确认扣库存</Button>
        <Button size="large" disabled={busy} onClick={() => setPreview(undefined)}>取消</Button>
      </div>
    </div>}
    {error && <Problem>{error}</Problem>}
    {done && <p className="qs-quiet" role="status">{done}</p>}
    {canReview && !preview && <Button block type="primary" size="large" loading={busy} onClick={() => send(false)}>审核并扣库存</Button>}
  </section>
}

function Today({ day, error, storeCode, changed, open }: {
  day?: SalesDay; error: string; storeCode: string; changed: () => void; open: (day?: string) => void
}) {
  const [shown, setShown] = useState(false), [copied, setCopied] = useState('')
  if (error) return <Problem>{error}</Problem>
  if (!day) return <Skeleton active paragraph={{ rows: 3 }} />
  const isToday = day.business_date === day.today
  async function copy() {
    setShown(true)
    try { await navigator.clipboard.writeText(day!.summary_text); setCopied('已复制') }
    catch { setCopied('复制不了，长按上面的文字复制') }
  }
  return <Panel zh={isToday ? '今天' : shortDay(day.business_date)} en={day.count ? `${day.count} 笔 · ${formatCents(day.total_cents)}` : undefined} label="Today's sales"
    count={day.needs_photo ? `${day.needs_photo} 没照片` : undefined} tone="out"
    action={isToday ? undefined : <button type="button" className="qs-link" onClick={() => open(undefined)}>回到今天</button>}>
    {day.can_review && day.unreviewed_days.length > 0 && <p className="qs-earlier">
      <span><i className="pc-dot pc-dot-warn" aria-hidden="true" />还没审核：</span>
      {day.unreviewed_days.map(earlier => <button key={earlier} type="button" className="qs-link" onClick={() => open(earlier)}>{shortDay(earlier)}</button>)}
    </p>}
    {!day.sales.length ? <p className="qs-quiet qs-empty">{isToday ? '今天还没有记账。' : '这天没有记账。'}</p>
      : <ol className="qs-day" aria-label="Sales today" reversed>
        {[...day.sales].reverse().map(sale => <li key={sale.id} className={sale.undone ? 'qs-undone' : undefined}>
          <span className="qs-day-main"><strong>{itemsText(sale.lines)}</strong><small>{clock(sale.created_at)} · {sale.cashier}{sale.staff_discount ? ` · 员工折扣 ${sale.staff_discount}` : ''}</small></span>
          {sale.undone && <span className="qs-flag qs-flag-undone">已撤销 · {sale.undone.by}{sale.undone.reason.includes(' · ') ? ` · ${sale.undone.reason.split(' · ').slice(1).join(' · ')}` : ''}</span>}
          <span className="qs-day-pay">{sale.payments.map(payment => <span key={payment.id}>{tenderName(payment.tender)} {formatCents(payment.amount_cents)}{payment.cny_fen != null ? ` · ${yuan(payment.cny_fen)}` : ''}</span>)}
            {sale.needs_photo && <span className="qs-flag"><i className="pc-dot pc-dot-out" aria-hidden="true" />没照片</span>}
            {sale.can_undo && <span className="qs-row-actions"><UndoButton sale={sale} done={changed} /></span>}
          </span>
          {sale.needs_photo && sale.can_add_photo && sale.payments.filter(payment => payment.needs_photo).map(payment =>
            <PhotoButtons key={payment.id} sale={sale} payment={payment} done={changed} />)}
        </li>)}
      </ol>}
    {(day.count > 0 || day.review.state !== 'none') && <div className="qs-copy">
      <Review day={day} storeCode={storeCode} changed={changed} />
      {shown && <pre className="qs-summary" aria-label="Day summary">{day.summary_text}</pre>}
      {day.count > 0 && <p className="qs-copy-link"><button type="button" className="qs-link" onClick={copy}>复制汇总</button>{copied && <span role="status">{copied}</span>}</p>}
    </div>}
  </Panel>
}
