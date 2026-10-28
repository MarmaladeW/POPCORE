import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Button, Checkbox, Input, InputNumber, Radio, Skeleton, type InputRef } from 'antd'
import PageHeader from '../../components/pc/PageHeader'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { useHasRole } from '../../auth/useRole'
import { useAppStore } from '../../store'
import InventoryPhoto from '../Stock/InventoryPhoto'
import {
  addDesigns, countRequestKey, getCountCard, getCountProgress, getCountScope, removeDesign, saveCount, searchCount,
  type CountCard, type CountEntry, type CountForm, type CountItem, type CountProgress, type CountResult,
  type CountScope, type SavedCount,
} from '../../api/counting'
import '../Stock/seriesInventory.css'
import './count.css'

// 盘点 Count (owner, 2026-10-27): look at the shelf, search the product, enter what is there; for a
// blind box also its sealed sets and, once its designs are checked, each design.
const PLACES: Record<string, string> = {
  floor: '店内 Floor', upstairs: '楼上 Upstairs', warehouse: '仓库 Warehouse', claw: '娃娃机 Claw', display: '陈列 Display',
}
const UNITS: Record<string, string> = { box: '盒', set: '端', piece: '个' }

type Failure = { _serverMessage?: string; response?: { data?: { code?: string } } }
const messageOf = (cause: unknown, fallback: string) => (cause as Failure)?._serverMessage || fallback
const codeOf = (cause: unknown) => (cause as Failure)?.response?.data?.code
const placeName = (code: string, name: string) => PLACES[code] || name
// The server keeps UTC ("YYYY-MM-DD HH:MM:SS"); show the device's local time.
function localTime(text: string, withDate = false) {
  const when = new Date(`${text.replace(' ', 'T')}Z`)
  if (Number.isNaN(when.getTime())) return text
  return withDate ? when.toLocaleString([], { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
const remembered = (store: string) => { try { return localStorage.getItem(`pc-count-location-${store}`) || '' } catch { return '' } }
const remember = (store: string, location: string) => { try { localStorage.setItem(`pc-count-location-${store}`, location) } catch { /* private mode */ } }

function kindOf(result: CountResult) {
  if (result.designs) return `盲盒 · ${result.designs} 款${result.sets ? ' · 整端' : ''}`
  if (result.kind === 'box') return `盲盒 Blind box${result.sets ? ' · 整端' : ''}`
  if (result.kind === 'series') return '系列 Series'
  return '单品 Item'
}

export default function CountPage() {
  const phone = useIsPhone()
  const store = useAppStore(state => state.selectedStore)
  const storeCode = store && store.code !== 'ALL' ? store.code : ''
  const [params, setParams] = useSearchParams()
  const location = params.get('location') || ''
  const productId = Number(params.get('product')) || 0
  const [scope, setScope] = useState<CountScope | null>(null)
  const [scopeError, setScopeError] = useState('')
  const [scopeVersion, setScopeVersion] = useState(0)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<CountResult[] | null>(null)
  const [searchError, setSearchError] = useState('')
  const openSingle = useRef(false)
  const searchBox = useRef<InputRef>(null)

  const go = (next: { location?: string; product?: number }) => {
    const values: Record<string, string> = {}
    const place = next.location ?? location
    if (place) values.location = place
    if (next.product) values.product = String(next.product)
    setParams(values)
  }

  useEffect(() => {
    if (!storeCode) return
    const controller = new AbortController()
    setScopeError('')
    getCountScope(storeCode, controller.signal).then(data => {
      setScope(data)
      const codes = data.locations.map(item => item.code)
      if (!codes.includes(location)) {
        const saved = remembered(storeCode)
        const place = codes.includes(saved) ? saved : codes[0]
        if (place) setParams({ location: place }, { replace: true })
      }
    }).catch(cause => { if (!controller.signal.aborted) setScopeError(messageOf(cause, 'Unable to load this store.')) })
    return () => controller.abort()
  }, [storeCode, scopeVersion]) // the location is read once per load; choosing one goes through go()

  useEffect(() => { if (storeCode && location) remember(storeCode, location) }, [storeCode, location])

  useEffect(() => {
    const text = query.trim()
    if (!storeCode || !location || !text) { setResults(null); setSearchError(''); return }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      searchCount(storeCode, location, text, controller.signal).then(found => {
        setResults(found)
        setSearchError('')
        // A scanner types the code and presses Enter: one match opens straight away.
        if (openSingle.current && found.length === 1) { setQuery(''); go({ product: found[0].product_id }) }
        openSingle.current = false
      }).catch(cause => { if (!controller.signal.aborted) setSearchError(messageOf(cause, 'Search failed. Try again.')) })
    }, 250)
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [storeCode, location, query])

  const next = () => { setQuery(''); setResults(null); go({}); window.setTimeout(() => searchBox.current?.focus(), 0) }
  const here = scope?.locations.find(item => item.code === location)

  return <div className="pc-count">
    <PageHeader title="盘点" sub={phone ? undefined : `Stocktake${store?.name && storeCode ? ` · ${store.name}` : ''}`}
      back={productId ? { label: '盘点', to: `/count${location ? `?location=${location}` : ''}` } : phone ? { label: '首页', to: '/' } : undefined} />
    {!storeCode ? <Alert type="info" showIcon message="Choose one store before counting." />
      : scopeError ? <Alert type="error" showIcon message={scopeError} action={<Button onClick={() => setScopeVersion(n => n + 1)}>Retry</Button>} />
      : !scope ? <Skeleton active paragraph={{ rows: 3 }} />
      : <>
        <nav className="pc-count-places" aria-label="Count location">
          {scope.locations.map(item => <button key={item.code} type="button" aria-pressed={item.code === location}
            className={item.code === location ? 'is-on' : ''} onClick={() => { setQuery(''); go({ location: item.code }) }}>
            {placeName(item.code, item.name)}{!scope.live && <small>{item.counted}</small>}
          </button>)}
        </nav>
        <p className={`pc-count-mode${scope.live ? ' is-live' : ''}`}>
          {scope.live ? '这家店已上线 · Saving a count changes this store’s stock now.'
            : '盘点中 · Counts are saved; stock changes when the store goes live.'}
        </p>
        {productId && location
          ? <CountCardPanel key={`${location}:${productId}`} storeCode={storeCode} location={location} productId={productId}
              onSaved={() => setScopeVersion(n => n + 1)} onNext={next} />
          : location && <>
            <Input ref={searchBox} autoFocus allowClear value={query} className="pc-count-search"
              aria-label="Search or scan a product" placeholder="搜索名称 / 记账名 / SKU，或扫码 Search or scan"
              onChange={event => setQuery(event.target.value)}
              onPressEnter={() => {
                openSingle.current = true
                if (results && results.length === 1 && query.trim()) { openSingle.current = false; setQuery(''); go({ product: results[0].product_id }) }
              }} />
            {searchError && <Alert type="error" showIcon message={searchError} />}
            {results && (results.length ? <ul className="pc-count-results" aria-label="Products">
              {results.map(result => <li key={result.product_id}>
                <button type="button" onClick={() => { setQuery(''); go({ product: result.product_id }) }}>
                  <InventoryPhoto filename={result.image} name={result.title} />
                  <span className="pc-count-result-text"><strong>{result.title}</strong><small>{kindOf(result)}{result.sku ? ` · ${result.sku}` : ''}</small></span>
                  {!scope.live && <span className={`pc-count-done${result.counted ? ' is-done' : ''}`}>
                    {result.counted ? `已点 ${result.counted}/${result.members}` : '未点'}</span>}
                </button>
              </li>)}
            </ul> : <p className="pc-count-quiet">No product matches. Try another word, the 记账名 or the SKU.</p>)}
            {!scope.live && here && <ProgressPanel storeCode={storeCode} location={location} counted={here.counted}
              onOpen={product => go({ product })} />}
          </>}
      </>}
  </div>
}

function ProgressPanel({ storeCode, location, counted, onOpen }: { storeCode: string; location: string; counted: number; onOpen: (product: number) => void }) {
  const [open, setOpen] = useState(false)
  const [progress, setProgress] = useState<CountProgress | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setProgress(null)
    setError('')
    getCountProgress(storeCode, location, controller.signal).then(setProgress)
      .catch(cause => { if (!controller.signal.aborted) setError(messageOf(cause, 'Unable to load what is left.')) })
    return () => controller.abort()
  }, [open, storeCode, location, counted])
  return <section className="pc-count-progress" aria-label="What is left to count">
    <button type="button" className="pc-count-progress-toggle" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      已点 {counted} 个 · 还没点的 What's left {open ? '▴' : '▾'}
    </button>
    {open && (error ? <Alert type="error" showIcon message={error} />
      : !progress ? <Skeleton active title={false} paragraph={{ rows: 2 }} />
      : !progress.missing_total ? <p className="pc-count-quiet">Everything POPCORE has here is counted.</p>
      : <>
        <p className="pc-count-quiet">POPCORE has stock here for these, and nobody has counted them yet. Count each one, entering 0 if there is none.
          {progress.missing.some(item => item.blocks_opening) && ' The ones marked 必点 must be counted before the store can go live.'}</p>
        <ul className="pc-count-missing">
          {progress.missing.map(item => <li key={item.product_id}><button type="button" onClick={() => onOpen(item.product_id)}>
            <span>{item.name}<small>{item.sku}</small></span>{item.blocks_opening && <em>必点</em>}
          </button></li>)}
        </ul>
        {progress.missing_total > progress.missing.length && <p className="pc-count-quiet">…and {progress.missing_total - progress.missing.length} more.</p>}
      </>)}
  </section>
}

function CountCardPanel({ storeCode, location, productId, onSaved, onNext }: {
  storeCode: string; location: string; productId: number; onSaved: () => void; onNext: () => void
}) {
  const manager = useHasRole('manager')
  const [card, setCard] = useState<CountCard | null>(null)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [values, setValues] = useState<Record<number, number | null>>({})
  const [forms, setForms] = useState<Record<number, CountForm>>({})
  const [designsOk, setDesignsOk] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<SavedCount[] | null>(null)
  const [newDesigns, setNewDesigns] = useState('')
  const [fixing, setFixing] = useState(false)
  const key = useRef('')

  useEffect(() => {
    const controller = new AbortController()
    setError('')
    getCountCard(storeCode, location, productId, controller.signal).then(data => {
      setCard(data)
      setValues(Object.fromEntries(data.items.map(item => [item.product_id, item.count?.quantity ?? null])))
      const chosen: Record<number, CountForm> = {}
      data.items.forEach(item => {
        const form = item.stock_form || item.suggested_form
        if (item.identity_status !== 'verified' && form) chosen[item.product_id] = form
      })
      setForms(chosen)
      setDesignsOk(Boolean(data.designs_checked))
    }).catch(cause => { if (!controller.signal.aborted) setError(messageOf(cause, 'Unable to load this product.')) })
    return () => controller.abort()
  }, [storeCode, location, productId, reload])

  if (!card) return error ? <Alert type="error" showIcon message={error} action={<Button onClick={() => setReload(n => n + 1)}>Retry</Button>} />
    : <Skeleton active paragraph={{ rows: 4 }} />

  const head = card.items.find(item => item.role === 'box' || item.role === 'item')
  const sets = card.items.filter(item => item.role === 'set')
  const designs = card.items.filter(item => item.role === 'design')
  const changed = () => { key.current = ''; setSaved(null) }
  const setValue = (item: CountItem, value: number | null) => { changed(); setValues(current => ({ ...current, [item.product_id]: value })) }
  const formOf = (item: CountItem) => item.identity_status === 'verified' ? item.stock_form : forms[item.product_id]
  const amount = (item?: CountItem) => (item && values[item.product_id]) || 0
  const boxesInSets = sets.reduce((sum, item) => sum + amount(item) * (item.boxes_per_set || 0), 0)
  const designTotal = designs.reduce((sum, item) => sum + amount(item), 0)
  const headUnit = head ? (formOf(head) === 'random_box' ? 'box' : formOf(head) === 'ordinary' ? 'piece' : head.stock_unit) : null
  const entries: CountEntry[] = card.items.filter(item => values[item.product_id] != null).map(item => ({
    product_id: item.product_id, quantity: values[item.product_id] as number,
    ...(item.identity_status !== 'verified' && forms[item.product_id] ? { stock_form: forms[item.product_id] } : {}),
    ...(card.live && item.balance_version != null ? { balance_version: item.balance_version } : {}),
  }))
  const designsCounted = entries.some(entry => designs.some(item => item.product_id === entry.product_id))

  const save = async () => {
    if (!entries.length) { setError('Enter at least one count. Enter 0 when there are none.'); return }
    if (designsCounted && !designsOk) { setError('Tick “款式正确 Designs are right” before counting the designs.'); return }
    if (head && head.identity_status !== 'verified' && values[head.product_id] != null && !forms[head.product_id]) {
      setError('Choose blind box or item first.'); return
    }
    if (!key.current) key.current = countRequestKey()
    setSaving(true)
    setError('')
    try {
      const response = await saveCount({
        store_code: storeCode, location, product_id: card.product_id, entries,
        ...(card.series_id && designsOk && !card.designs_checked ? { designs_checked: true } : {}),
      }, key.current)
      key.current = ''
      setSaved(response.results)
      onSaved()
      setReload(n => n + 1)
    } catch (cause) {
      if (codeOf(cause) === 'count_stale') {
        key.current = ''
        setError('Stock changed while you were counting (a sale or a move). Count it again.')
        setReload(n => n + 1)
      } else {
        setError(messageOf(cause, 'Could not save. Check the connection and press Save again; it will not be counted twice.'))
      }
    } finally {
      setSaving(false)
    }
  }

  const fixDesigns = async (work: () => Promise<unknown>, failure: string) => {
    setFixing(true)
    setError('')
    try { await work(); changed(); setReload(n => n + 1) } catch (cause) { setError(messageOf(cause, failure)) } finally { setFixing(false) }
  }
  const addNew = () => {
    const names = newDesigns.split('\n').map(name => name.trim()).filter(Boolean)
    if (names.length) void fixDesigns(async () => { await addDesigns(card.product_id, names, countRequestKey()); setNewDesigns('') }, 'Could not add the designs.')
  }
  const drop = (item: CountItem) => {
    if (window.confirm(`Remove the design “${item.design_name}”? A count of it here goes with it.`)) {
      void fixDesigns(() => removeDesign(item.product_id), 'Could not remove the design.')
    }
  }
  const nameOf = (id: number) => {
    const item = card.items.find(entry => entry.product_id === id)
    return item?.role === 'design' ? item.design_name : item?.role === 'set' ? '整端 Sealed set' : item?.name
  }

  return <section className="pc-count-card" aria-label={`Count ${card.title}`}>
    <header className="pc-count-card-head">
      {head && <InventoryPhoto filename={head.image} name={card.title} />}
      <div><h2>{card.title}</h2><p>{placeName(card.location.code, card.location.name)} · 数一数架上有多少 Count what is here</p></div>
    </header>

    {head && head.identity_status !== 'verified' && <fieldset className="pc-count-kind">
      <legend>这是什么? What is it?</legend>
      <Radio.Group value={forms[head.product_id]} onChange={event => { changed(); setForms(current => ({ ...current, [head.product_id]: event.target.value })) }}
        options={[{ value: 'random_box', label: '盲盒 Blind box (盒)' }, { value: 'ordinary', label: '单品 Item (个)' }]} />
    </fieldset>}

    {sets.map(item => <CountField key={item.product_id} item={item} value={values[item.product_id]} onChange={setValue}
      label="整端 Sealed sets" unit="端" note={item.boxes_per_set ? `${item.boxes_per_set} 盒/端` : undefined} />)}
    {head && <CountField item={head} value={values[head.product_id]} onChange={setValue}
      label={headUnit === 'box' ? (sets.length || designs.length ? '散盒 Loose boxes' : '盲盒 Boxes') : '数量 Count'}
      unit={headUnit ? UNITS[headUnit] : ''} />}

    {(designs.length > 0 || (manager && head && headUnit === 'box')) && <div className="pc-count-designs">
      <div className="pc-count-designs-head">
        <h3>确认款 Designs</h3>
        {designs.length > 0 && <Checkbox checked={designsOk} disabled={Boolean(card.designs_checked)}
          onChange={event => { changed(); setDesignsOk(event.target.checked) }}>款式正确 Designs are right</Checkbox>}
      </div>
      {card.designs_checked ? <p className="pc-count-quiet">Checked by {card.designs_checked.by} · {localTime(card.designs_checked.at, true)}</p>
        : designs.length > 0 && <p className="pc-count-quiet">Check the list matches the shelf, then tick it to enter each design.</p>}
      {designs.map(item => <CountField key={item.product_id} item={item} value={values[item.product_id]} onChange={setValue}
        label={item.design_name || item.name} unit="个" disabled={!designsOk}
        action={manager ? <Button size="small" disabled={fixing} aria-label={`Remove design ${item.design_name}`} onClick={() => drop(item)}>✕</Button> : undefined} />)}
      {manager && head && <div className="pc-count-add-designs">
        <Input.TextArea value={newDesigns} onChange={event => setNewDesigns(event.target.value)} autoSize={{ minRows: 1, maxRows: 6 }}
          aria-label="Designs to add, one per line" placeholder="缺的款，一行一个 Missing designs, one per line" />
        <Button disabled={fixing || !newDesigns.trim()} onClick={addNew}>添加 Add</Button>
      </div>}
    </div>}

    <p className="pc-count-total" aria-live="polite">
      合计 Total: {sets.length > 0 && <>{sets.reduce((sum, item) => sum + amount(item), 0)} 端 ({boxesInSets} 盒) + </>}
      {head && <>{amount(head)} {headUnit ? UNITS[headUnit] : ''}</>}
      {designs.length > 0 && <> · 确认款 {designTotal} 个</>}
    </p>
    {error && <Alert type="error" showIcon message={error} />}
    {saved && <div className="pc-count-saved" role="status">
      <strong>已保存 Saved</strong>
      <ul>{saved.map(row => <li key={row.product_id}>
        <span>{nameOf(row.product_id)}</span>
        <span>{row.popcore_quantity == null ? `点到 ${row.quantity}` : card.live ? `库存 ${row.popcore_quantity} → ${row.quantity}`
          : `POPCORE ${row.popcore_quantity} · 点到 ${row.quantity}`}
          {row.popcore_quantity != null && row.popcore_quantity !== row.quantity &&
            <em className={row.quantity > row.popcore_quantity ? 'is-more' : 'is-less'}>{row.quantity > row.popcore_quantity ? '+' : ''}{row.quantity - row.popcore_quantity}</em>}
        </span>
      </li>)}</ul>
    </div>}
    <div className="pc-count-actions">
      <Button onClick={onNext}>{saved ? '下一个 Next' : '返回 Back'}</Button>
      <Button type="primary" loading={saving} onClick={() => void save()}>保存 Save</Button>
    </div>
  </section>
}

function CountField({ item, label, unit, note, value, onChange, disabled, action }: {
  item: CountItem; label: string; unit: string; note?: string; value: number | null | undefined
  onChange: (item: CountItem, value: number | null) => void; disabled?: boolean; action?: ReactNode
}) {
  return <div className="pc-count-field">
    <label htmlFor={`count-${item.product_id}`}>
      <span>{label}</span>
      {note && <small>{note}</small>}
      {item.count && <small>已点 {item.count.quantity} · {item.count.counted_by} {localTime(item.count.counted_at)}</small>}
    </label>
    <span className="pc-count-input">
      <InputNumber id={`count-${item.product_id}`} min={0} max={100000} precision={0} inputMode="numeric" disabled={disabled}
        value={value ?? null} onChange={next => onChange(item, typeof next === 'number' ? next : null)} />
      <span className="pc-count-unit">{unit}</span>
      {action}
    </span>
  </div>
}
