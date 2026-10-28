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
  type CountScope, type SavedCard,
} from '../../api/counting'
import '../Stock/seriesInventory.css'
import './count.css'

// 盘点 Count (owner, 2026-10-27): look at the shelf, search the product, enter what is there; for a
// blind box also its sealed sets and, once its designs are checked, each design.
const PLACES: Record<string, string> = {
  floor: '店内 Floor', upstairs: '楼上 Upstairs', warehouse: '仓库 Warehouse', claw: '娃娃机 Claw', display: '陈列 Display',
}
const UNITS: Record<string, string> = { box: '盒', set: '端', piece: '个' }

type Failure = { _serverMessage?: string; response?: { data?: { code?: string; product_ids?: number[] } } }
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
  const [resultsFor, setResultsFor] = useState('')
  const [searchError, setSearchError] = useState('')
  // The text Enter was pressed on before its results came: its single match opens when they do.
  const openSingle = useRef('')
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
        // A link from Inventory names only the product: it opens here at the counter's location.
        if (place) setParams({ location: place, ...(productId ? { product: String(productId) } : {}) }, { replace: true })
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
        setResultsFor(text)
        setSearchError('')
        // A scanner types the code and presses Enter: one match opens straight away.
        if (openSingle.current === text && found.length === 1) { setQuery(''); go({ product: found[0].product_id }) }
        openSingle.current = ''
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
              live={scope.live} refreshScope={() => setScopeVersion(n => n + 1)} onNext={next} />
          : location && <>
            <Input ref={searchBox} autoFocus allowClear value={query} className="pc-count-search"
              aria-label="Search or scan a product" placeholder="搜索名称 / 记账名 / SKU，或扫码 Search or scan"
              onChange={event => setQuery(event.target.value)}
              onPressEnter={() => {
                const text = query.trim()
                // Results of the text before this one are not this text's match.
                if (text && resultsFor === text && results?.length === 1) { setQuery(''); go({ product: results[0].product_id }) }
                else openSingle.current = text
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
            </ul> : <p className="pc-count-quiet">{/^\d{8,}$/.test(query.trim())
              // No product has a barcode saved yet (2026-10-28), so a scan only finds what was linked.
              ? '这个条码还没存进 POPCORE · This barcode is not saved in POPCORE yet. Search by name, 记账名 or SKU instead.'
              : 'No product matches. Try another word, the 记账名 or the SKU.'}</p>)}
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
          {progress.missing.some(item => item.blocks_opening) && ' The ones marked 必点 must be counted before the store can go live.'}
          {progress.missing.some(item => item.recount) && ' 重点 Recount: its type changed in Products after it was counted here, so count it again.'}</p>
        <ul className="pc-count-missing">
          {progress.missing.map(item => <li key={item.product_id}><button type="button" onClick={() => onOpen(item.product_id)}>
            <span>{item.name}<small>{item.sku}</small></span>{item.blocks_opening && <em>{item.recount ? '重点 Recount' : '必点'}</em>}
          </button></li>)}
        </ul>
        {progress.missing_total > progress.missing.length && <p className="pc-count-quiet">…and {progress.missing_total - progress.missing.length} more.</p>}
      </>)}
  </section>
}

function CountCardPanel({ storeCode, location, productId, live, refreshScope, onNext }: {
  storeCode: string; location: string; productId: number; live: boolean; refreshScope: () => void; onNext: () => void
}) {
  const manager = useHasRole('manager')
  const [card, setCard] = useState<CountCard | null>(null)
  const [error, setError] = useState('')
  // A refused save reloads the card; its warning has to outlive that reload.
  const [notice, setNotice] = useState('')
  const [info, setInfo] = useState('')
  const [reload, setReload] = useState(0)
  const [values, setValues] = useState<Record<number, number | null>>({})
  // What this counter typed: only these are saved, so a count shown from earlier is never saved
  // again in this counter's name, nor over someone else's newer one.
  const [typed, setTyped] = useState<Record<number, true>>({})
  // Typed numbers that the next reload keeps (a design was added or removed, or other counts went stale).
  const keepTyped = useRef<Set<number> | null>(null)
  const [forms, setForms] = useState<Record<number, CountForm>>({})
  const [designsOk, setDesignsOk] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<SavedCard | null>(null)
  const [newDesigns, setNewDesigns] = useState('')
  const [fixing, setFixing] = useState(false)
  const [newSets, setNewSets] = useState<number | null>(null)
  const [boxesPerSet, setBoxesPerSet] = useState<number | null>(null)
  const key = useRef('')

  useEffect(() => {
    const controller = new AbortController()
    setError('')
    getCountCard(storeCode, location, productId, controller.signal).then(data => {
      const keep = keepTyped.current
      keepTyped.current = null
      const kept = (id: number) => Boolean(keep?.has(id))
      setCard(data)
      setValues(current => Object.fromEntries(data.items.map(item =>
        [item.product_id, kept(item.product_id) ? current[item.product_id] ?? null : item.count?.quantity ?? null])))
      setTyped(Object.fromEntries(data.items.filter(item => kept(item.product_id)).map(item => [item.product_id, true])))
      setForms(current => {
        const chosen: Record<number, CountForm> = {}
        data.items.forEach(item => {
          const form = (kept(item.product_id) && current[item.product_id]) || item.stock_form || item.counted_form || item.suggested_form
          if (item.identity_status !== 'verified' && form) chosen[item.product_id] = form
        })
        return chosen
      })
      setDesignsOk(current => Boolean(data.designs_checked) || (keep !== null && current))
      // Until a whole set is counted there is no set to hold the number: a box counted here with no
      // set has 0 whole sets, and what this counter typed stays on the card through its reloads.
      const boxCounted = data.items.some(item => item.role === 'box' && item.count)
      setNewSets(current => data.new_sealed_set ? current ?? (boxCounted ? 0 : null) : null)
      setBoxesPerSet(current => data.new_sealed_set ? current ?? data.new_sealed_set.boxes_per_set ?? null : null)
    }).catch(cause => { if (!controller.signal.aborted) setError(messageOf(cause, 'Unable to load this product.')) })
    return () => controller.abort()
  }, [storeCode, location, productId, reload])

  // The store went live (or its scope changed) while this page was open: update the banner.
  useEffect(() => { if (card && card.live !== live) refreshScope() }, [card?.live, live])

  if (!card) return error ? <Alert type="error" showIcon message={error} action={<Button onClick={() => setReload(n => n + 1)}>Retry</Button>} />
    : <Skeleton active paragraph={{ rows: 4 }} />

  const head = card.items.find(item => item.role === 'box' || item.role === 'item')
  const sets = card.items.filter(item => item.role === 'set')
  const designs = card.items.filter(item => item.role === 'design')
  const changed = () => { key.current = ''; setSaved(null); setInfo('') }
  const touch = (item: CountItem) => setTyped(current => ({ ...current, [item.product_id]: true }))
  const setValue = (item: CountItem, value: number | null) => {
    changed(); touch(item); setValues(current => ({ ...current, [item.product_id]: value }))
  }
  const formOf = (item: CountItem) => item.identity_status === 'verified' ? item.stock_form : forms[item.product_id]
  const amount = (item?: CountItem) => (item && values[item.product_id]) || 0
  const boxesInSets = sets.reduce((sum, item) => sum + amount(item) * (item.boxes_per_set || 0), 0)
  const designTotal = designs.reduce((sum, item) => sum + amount(item), 0)
  const headUnit = head ? (formOf(head) === 'random_box' ? 'box' : formOf(head) === 'ordinary' ? 'piece' : head.stock_unit) : null
  // The product type decides (owner, 2026-10-27): only an untyped product asks what it is.
  const asksKind = Boolean(head && head.identity_status !== 'verified' && !head.suggested_form)
  // A blind box always counts whole sets; one with no whole set yet gets it on the first count.
  const offerSet = headUnit === 'box' && Boolean(card.new_sealed_set)
  const wantsNewSet = offerSet && (newSets ?? 0) > 0
  const wholeSets = sets.reduce((sum, item) => sum + amount(item), 0) + (offerSet ? newSets || 0 : 0)
  const boxesInWholeSets = boxesInSets + (offerSet ? (newSets || 0) * (boxesPerSet || 0) : 0)
  const entries: CountEntry[] = card.items.filter(item => typed[item.product_id] && values[item.product_id] != null).map(item => ({
    product_id: item.product_id, quantity: values[item.product_id] as number,
    ...(item.identity_status !== 'verified' && forms[item.product_id] ? { stock_form: forms[item.product_id] } : {}),
    // The version this card showed: the save is refused if stock moved or someone else counted since.
    ...(card.live ? { balance_version: item.balance_version } : { count_version: item.count?.version ?? null }),
  }))
  const designsCounted = entries.some(entry => designs.some(item => item.product_id === entry.product_id))
  const headCounted = Boolean(head && (head.count || entries.some(entry => entry.product_id === head.product_id)))

  const save = async () => {
    if (!entries.length && !wantsNewSet) {
      // Only typed numbers are sent: with none, everything shown is already saved.
      if (card.items.some(item => item.count) && !(card.series_id && designsOk && !card.designs_checked)) {
        setError(''); setInfo('没有新的数字 · Nothing new to save: the numbers shown are saved. Change one to save it again.')
      } else setError('Enter at least one count. Enter 0 when there are none.')
      return
    }
    if (wantsNewSet && !(boxesPerSet && boxesPerSet >= 2)) { setError('Enter how many boxes make one whole set (每端几盒).'); return }
    if (designsCounted && !designsOk) { setError('Tick “款式正确 Designs are right” before counting the designs.'); return }
    // A blank 散盒 next to whole sets or designs would leave POPCORE's boxes here uncounted.
    if (!card.live && head && !headCounted) {
      setError(headUnit === 'box' ? '散盒 Enter the single boxes too, 0 if there are none.' : `Enter the count of ${card.title} too, 0 if there are none.`)
      return
    }
    if (head && entries.some(entry => entry.product_id === head.product_id) && head.identity_status !== 'verified' && !forms[head.product_id]) {
      setError('Choose blind box or item first.'); return
    }
    // Boxes per set is kept on the product; a number the counter typed is checked once more.
    if (wantsNewSet && boxesPerSet !== card.new_sealed_set?.boxes_per_set
      && !window.confirm(`每端 ${boxesPerSet} 盒: one whole set of ${card.title} holds ${boxesPerSet} boxes? This is saved to the product.`)) return
    if (!key.current) key.current = countRequestKey()
    setSaving(true)
    setError('')
    setNotice('')
    setInfo('')
    try {
      const response = await saveCount({
        store_code: storeCode, location, product_id: card.product_id, entries,
        ...(card.series_id && designsOk && !card.designs_checked ? { designs_checked: true } : {}),
        ...(wantsNewSet ? { new_sealed_set: { quantity: newSets as number, boxes_per_set: boxesPerSet as number } } : {}),
      }, key.current)
      key.current = ''
      setSaved(response)
      refreshScope()
      setReload(n => n + 1)
    } catch (cause) {
      const code = codeOf(cause)
      const typedIds = Object.keys(typed).map(Number)
      if (code === 'count_stale' || code === 'card_changed') {
        // Keep what this counter typed, except the products that changed: those are counted again.
        const stale = (cause as Failure).response?.data?.product_ids || []
        keepTyped.current = new Set(typedIds.filter(id => !stale.includes(id)))
        key.current = ''
        setNotice(messageOf(cause, 'This card changed while you were counting. Check it and save again.')
          + (keepTyped.current.size ? ' Your other numbers are still filled in.' : ''))
        setReload(n => n + 1)
      } else if (code && ['whole_set_changed', 'whole_set_unavailable', 'identity_changed', 'identity_conflict', 'sealed_set_exists'].includes(code)) {
        // The product changed under this card (another phone added its whole set): show it as it is now.
        key.current = ''
        setNotice(messageOf(cause, 'This product changed while you were counting. Check the card and count again.'))
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
    try {
      await work()
      changed()
      keepTyped.current = new Set(Object.keys(typed).map(Number))
      setReload(n => n + 1)
    } catch (cause) { setError(messageOf(cause, failure)) } finally { setFixing(false) }
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
    return item?.role === 'design' ? item.design_name : item?.role === 'set' ? '整端 Whole sets' : item?.name
  }

  return <section className="pc-count-card" aria-label={`Count ${card.title}`}>
    <header className="pc-count-card-head">
      {head && <InventoryPhoto filename={head.image} name={card.title} />}
      <div><h2>{card.title}</h2><p>{placeName(card.location.code, card.location.name)} · 数一数架上有多少 Count what is here</p></div>
    </header>

    {asksKind && head && <fieldset className="pc-count-kind">
      <legend>这是什么? What is it?</legend>
      <Radio.Group value={forms[head.product_id]} onChange={event => { changed(); touch(head); setForms(current => ({ ...current, [head.product_id]: event.target.value })) }}
        options={[{ value: 'random_box', label: '盲盒 Blind box (盒)' }, { value: 'ordinary', label: '单品 Item (个)' }]} />
    </fieldset>}

    {sets.map(item => <CountField key={item.product_id} item={item} value={values[item.product_id]} onChange={setValue}
      label="整端 Whole sets" unit="端" note={item.boxes_per_set ? `每端 ${item.boxes_per_set} 盒` : undefined} />)}
    {offerSet && <>
      <div className="pc-count-field">
        <label htmlFor="count-new-set"><span>整端 Whole sets</span><small>第一次点整端时会自动添加 · Added on first count</small></label>
        <span className="pc-count-input">
          <InputNumber id="count-new-set" min={0} max={100000} precision={0} inputMode="numeric" value={newSets}
            onChange={next => { changed(); setNewSets(typeof next === 'number' ? next : null) }} />
          <span className="pc-count-unit">端</span>
        </span>
      </div>
      <div className="pc-count-field pc-count-per-set">
        <label htmlFor="count-boxes-per-set"><span>每端几盒 Boxes per set</span></label>
        <span className="pc-count-input">
          <InputNumber id="count-boxes-per-set" min={1} max={100} precision={0} inputMode="numeric" value={boxesPerSet}
            onChange={next => { changed(); setBoxesPerSet(typeof next === 'number' ? next : null) }} />
          <span className="pc-count-unit">盒</span>
        </span>
      </div>
    </>}
    {head && <CountField item={head} value={values[head.product_id]} onChange={setValue}
      label={headUnit === 'box' ? '散盒 Single boxes' : headUnit === 'piece' ? '数量 Pieces' : '数量 Count'}
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
      合计 Total: {(sets.length > 0 || offerSet) && <>{wholeSets} 端 ({boxesInWholeSets} 盒) + </>}
      {head && <>{amount(head)} {headUnit ? UNITS[headUnit] : ''}</>}
      {designs.length > 0 && <> · 确认款 {designTotal} 个</>}
    </p>
    {notice && <Alert type="warning" showIcon message={notice} />}
    {info && <Alert type="info" showIcon message={info} />}
    {error && <Alert type="error" showIcon message={error} />}
    {saved && <div className="pc-count-saved" role="status">
      <strong>已保存 Saved</strong>
      <ul>{saved.results.map(row => <li key={row.product_id}>
        <span>{nameOf(row.product_id)}</span>
        <span>{row.popcore_quantity == null ? `点到 ${row.quantity}` : card.live ? `库存 ${row.popcore_quantity} → ${row.quantity}`
          : `POPCORE ${row.popcore_quantity} · 点到 ${row.quantity}`}
          {row.popcore_quantity != null && <Difference counted={row.quantity} popcore={row.popcore_quantity} />}
        </span>
      </li>)}
      {saved.boxes && <li>
        <span>盒合计 All boxes here</span>
        <span>POPCORE {saved.boxes.popcore} 盒 · 点到 {saved.boxes.counted} 盒<Difference counted={saved.boxes.counted} popcore={saved.boxes.popcore} /></span>
      </li>}</ul>
    </div>}
    <div className="pc-count-actions">
      <Button onClick={onNext}>{saved ? '下一个 Next' : '返回 Back'}</Button>
      <Button type="primary" loading={saving} onClick={() => void save()}>保存 Save</Button>
    </div>
  </section>
}

function Difference({ counted, popcore }: { counted: number; popcore: number }) {
  if (counted === popcore) return null
  return <em className={counted > popcore ? 'is-more' : 'is-less'}>{counted > popcore ? '+' : ''}{counted - popcore}</em>
}

const SET_ASIDE: Record<string, string> = {
  hold: '暂扣 on hold', damaged: '损坏 damaged', trade: '交换 trade', display: '陈列 display', transit: '在途 in transit',
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
      {item.other_stock && <small>另有 {item.other_stock.map(other => `${SET_ASIDE[other.disposition] || other.disposition} ${other.quantity}`).join(' · ')}: not part of this count</small>}
    </label>
    <span className="pc-count-input">
      <InputNumber id={`count-${item.product_id}`} min={0} max={100000} precision={0} inputMode="numeric" disabled={disabled}
        value={value ?? null} onChange={next => onChange(item, typeof next === 'number' ? next : null)} />
      <span className="pc-count-unit">{unit}</span>
      {action}
    </span>
  </div>
}
