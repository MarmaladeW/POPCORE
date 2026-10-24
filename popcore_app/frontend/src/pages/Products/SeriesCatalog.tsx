import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Input, Skeleton } from 'antd'
import { useAuth0 } from '@auth0/auth0-react'
import { useSearchParams } from 'react-router-dom'
import { getInventorySeries, type InventorySeries, type SeriesInventoryResult, type SeriesProduct } from '../../api/inventorySeries'
import { useRole } from '../../auth/useRole'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { useAppStore } from '../../store'
import useCheckoutMutation from '../Sales/useCheckoutMutation'
import { ProductsTabs } from './ProductsNav'
import './products.css'

const failure = (cause: unknown) => (cause as { _serverMessage?: string })?._serverMessage || 'Unable to load series. Please retry.'
const formNames: Record<string, string> = { sealed_set: '端', random_box: '盒', confirmed_design: '个', ordinary: '' }
const saleable = (product: SeriesProduct, locationId: number) => product.balances.find(balance => balance.location_id === locationId && balance.disposition === 'saleable')?.quantity
const isBack = (code: string) => ['upstairs', 'warehouse'].includes(code)

// 系列 Series (Figma Products · Series and designs): each series' sealed set, single blind box and every
// confirmed design with stock per location. 设置款式 / 新建系列 use the same /product-series/setup rules
// as Inventory's series page: unique names, up to 100, each a verified piece product with a generated SKU.
export default function SeriesCatalog() {
  const store = useAppStore(state => state.selectedStore)
  const { user } = useAuth0(), role = useRole()
  return <SeriesScope key={`${store?.code}|${user?.sub}|${role}`} storeCode={store?.code} storeName={store?.name} manager={['manager', 'admin'].includes(role)} />
}

function SeriesScope({ storeCode, storeName, manager }: { storeCode?: string; storeName?: string; manager: boolean }) {
  const phone = useIsPhone()
  const [params, setParams] = useSearchParams()
  const rawId = params.get('series_id'), selectedId = rawId && /^\d+$/.test(rawId) ? Number(rawId) : undefined
  const [query, setQuery] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [result, setResult] = useState<{ scope: string; data?: SeriesInventoryResult; error?: string }>()
  const [panel, setPanel] = useState<'designs' | 'series'>()
  const [seriesName, setSeriesName] = useState(''), [designNames, setDesignNames] = useState('')
  const [saved, setSaved] = useState('')
  const usable = !!storeCode && storeCode !== 'ALL'
  const scope = `${storeCode}|${refresh}`
  useEffect(() => {
    if (!usable) return
    const controller = new AbortController()
    getInventorySeries(storeCode!, '', undefined, controller.signal)
      .then(value => { if (!controller.signal.aborted) setResult({ scope, data: value }) })
      .catch(cause => { if (!controller.signal.aborted) setResult({ scope, error: failure(cause) }) })
    return () => controller.abort()
  }, [scope, storeCode, usable])
  const current = result?.scope === scope ? result : undefined
  const data = current?.data
  const needle = query.trim().normalize('NFKC').toLocaleLowerCase()
  const list = useMemo(() => (data?.series || []).filter(series => !needle || series.name.normalize('NFKC').toLocaleLowerCase().includes(needle)), [data, needle])
  const selected = data?.series.find(series => series.id === selectedId) || (!phone ? list[0] : undefined)

  const mutation = useCheckoutMutation(response => {
    setSaved(panel === 'series' ? 'Series saved.' : 'Designs saved.')
    setPanel(undefined); setDesignNames(''); setSeriesName('')
    if (response?.id) setParams({ series_id: String(response.id) })
    setRefresh(value => value + 1)
  })
  const locked = mutation.pending || mutation.saving
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('popcore:checkout-busy', { detail: locked }))
    return () => { window.dispatchEvent(new CustomEvent('popcore:checkout-busy', { detail: false })) }
  }, [locked])
  const names = designNames.split('\n').map(name => name.trim()).filter(Boolean)
  const duplicate = new Set(names.map(name => name.toLocaleLowerCase())).size !== names.length
  const ready = manager && usable && names.length > 0 && names.length <= 100 && !duplicate && (panel === 'designs' ? !!selected : !!seriesName.trim())
  function save() {
    if (!ready) return
    void mutation.run('/product-series/setup', { ...(panel === 'series' ? { name: seriesName.trim() } : { series_id: selected!.id }), design_names: names })
  }

  const setupPanel = panel && manager && <section className="pc-panel pc-ser-setup" aria-label={panel === 'series' ? 'New series' : 'Add designs'}>
    <header className="pc-panel-head"><h2><span>{panel === 'series' ? '新建系列' : '设置款式'}</span> <small>{panel === 'series' ? 'New series' : 'Add designs to this series'}</small></h2><button type="button" className="pc-btn" onClick={() => setPanel(undefined)} disabled={locked}>取消 Cancel</button></header>
    <div className="pc-ser-setup-body">
      <div>
        {panel === 'series' && <label className="pc-ser-field">系列名 Series name<Input value={seriesName} onChange={event => setSeriesName(event.target.value)} disabled={locked} /></label>}
        <label className="pc-ser-field">新款式名字，每行一个 · New design names, one per line
          <Input.TextArea aria-label="Design names, one per line" rows={5} value={designNames} onChange={event => setDesignNames(event.target.value)} disabled={locked} /></label>
        {duplicate && <p className="pc-ser-error">Each design name must be unique.</p>}
        {names.length > 100 && <p className="pc-ser-error">Up to 100 names at a time.</p>}
      </div>
      <div className="pc-ser-setup-side">
        <p>Each name becomes a verified confirmed-design product (unit: piece) with a generated SKU. Names must be unique within the series; up to 100 at a time.</p>
        <Button type="primary" disabled={!ready || locked} loading={mutation.saving} onClick={save}>保存 {names.length || ''} 个款式 Save {names.length || ''} designs</Button>
      </div>
    </div>
    <div className="pc-ser-alert">{mutation.notice}</div>
  </section>

  return <div className="pc-ser">
    <PageHeader title="产品" sub={phone ? 'Series' : `Products · ${data ? `${data.series.length} 个系列` : 'Series'}${storeName ? ` · ${storeName}` : ''}`}
      actions={manager && usable ? <Button disabled={locked} onClick={() => { setPanel('series'); setSaved('') }}><Icon name="plus" size={16} />新建系列 New series</Button> : undefined} />
    <ProductsTabs />
    {!usable ? <p className="pc-notice">Choose one store to see series stock.</p>
      : current?.error ? <Alert role="alert" type="error" showIcon message={current.error} action={<Button onClick={() => setRefresh(value => value + 1)}>Retry</Button>} />
      : !data ? <Skeleton active />
      : <>
        {saved && <p className="pc-ser-saved" role="status">{saved}</p>}
        {phone && manager && <Button className="pc-ser-new" disabled={locked} onClick={() => { setPanel('series'); setSaved('') }}><Icon name="plus" size={16} />新建系列 New series</Button>}
        {panel === 'series' && setupPanel}
        <div className="pc-ser-grid">
          {(!phone || !selected) && <aside className="pc-ser-side">
            <Input allowClear prefix={<Icon name="search" size={16} />} placeholder="搜索系列 Search series" aria-label="Search series" value={query} onChange={event => setQuery(event.target.value)} />
            <ul className="pc-panel pc-ser-list">
              {list.map(series => { const designs = series.products.filter(product => product.stock_form === 'confirmed_design').length
                return <li key={series.id}><button type="button" aria-current={selected?.id === series.id ? 'true' : undefined} onClick={() => setParams({ series_id: String(series.id) })}>
                  <span><strong>{series.name}</strong><small>{designs ? `${series.products.length} 个产品` : `还没有确认款 · ${series.products.length} 个产品`}</small></span><b>{designs} 款</b>
                </button></li> })}
              {!list.length && <li className="pc-quiet">No series match.</li>}
            </ul>
          </aside>}
          {selected && <SeriesDetail series={selected} locations={data.locations} manager={manager && usable} phone={phone}
            onBack={() => setParams({})} onAdd={() => { setPanel('designs'); setSaved('') }} locked={locked} setup={panel === 'designs' ? setupPanel : null} />}
        </div>
      </>}
  </div>
}

function SeriesDetail({ series, locations, manager, phone, onBack, onAdd, locked, setup }: {
  series: InventorySeries; locations: SeriesInventoryResult['locations']; manager: boolean; phone: boolean
  onBack: () => void; onAdd: () => void; locked: boolean; setup: React.ReactNode
}) {
  const sets = series.products.filter(product => product.stock_form !== 'confirmed_design')
  const designs = series.products.filter(product => product.stock_form === 'confirmed_design')
  const total = (product: SeriesProduct) => {
    const values = locations.map(location => saleable(product, location.id))
    return values.some(value => value == null) ? null : values.reduce<number>((sum, value) => sum + (value || 0), 0)
  }
  const designStock = designs.map(total)
  const known = designStock.every(value => value != null)
  const byKind = (form: string) => sets.filter(product => product.stock_form === form).length
  const sumAt = (filter: (code: string) => boolean) => designs.reduce((sum, product) => sum + locations.filter(location => filter(location.code)).reduce((inner, location) => inner + (saleable(product, location.id) || 0), 0), 0)
  const row = (product: SeriesProduct, index?: number) => {
    const sum = total(product)
    return <tr key={product.id}>
      <td className="pc-ser-muted">{index ?? '—'}</td>
      <td><strong>{product.design_name || product.jizhanming || product.name}</strong></td>
      <td className="pc-ser-muted pc-ser-sku">{product.sku}</td>
      <td><span className={product.identity_status === 'verified' ? 'pc-ser-id' : 'pc-ser-id pc-ser-id-warn'}>{product.identity_status === 'verified' ? '已核对' : '未核对'}</span> <small className="pc-ser-muted">{product.stock_unit ? formNames[product.stock_form || ''] || product.stock_unit : '—'}</small></td>
      {locations.map(location => <td key={location.id} className={isBack(location.code) ? 'pc-ser-num pc-kraft-cell' : 'pc-ser-num'}>{saleable(product, location.id) ?? '—'}</td>)}
      <td className="pc-ser-num"><i className={`pc-dot ${sum == null ? '' : sum === 0 ? 'pc-dot-out' : sum <= 2 ? 'pc-dot-warn' : 'pc-dot-ok'}`} aria-hidden="true" /> <b>{sum ?? '—'}</b></td>
    </tr>
  }
  return <section className="pc-ser-detail" aria-label={series.name}>
    {phone && <button type="button" className="pc-ser-back" onClick={onBack}><Icon name="back" size={14} />全部系列 All series</button>}
    <div className="pc-figures pc-figures-4">
      <div><span>确认款 Confirmed designs</span><strong>{designs.length}</strong></div>
      <div><span>整端 Sealed sets</span><strong>{byKind('sealed_set')}</strong></div>
      <div><span>盲盒 Single boxes</span><strong>{byKind('random_box')}</strong></div>
      <div><span>确认款库存 Design stock</span><strong>{known ? designStock.reduce<number>((sum, value) => sum + (value || 0), 0) : '—'}</strong>
        {known && <small>楼面 {sumAt(code => !isBack(code))} · 楼上 {sumAt(isBack)}</small>}</div>
    </div>
    <div className="pc-panel">
      <header className="pc-panel-head"><h2><span>{series.name}</span> <small>{designs.length} 款确认款</small></h2>
        {manager && <button type="button" className="pc-btn" disabled={locked} onClick={onAdd}>设置款式 Add designs</button>}</header>
      <div className="pc-ser-table-wrap"><table className="pc-ser-table">
        <thead><tr><th>#</th><th>款式 Design</th><th>SKU</th><th>库存身份</th>{locations.map(location => <th key={location.id} className={isBack(location.code) ? 'pc-ser-num pc-kraft-cell' : 'pc-ser-num'}>{location.name}</th>)}<th className="pc-ser-num">合计</th></tr></thead>
        <tbody>
          <tr className="pc-ser-group"><th colSpan={5 + locations.length} scope="rowgroup">整端与盲盒 Sets &amp; boxes <small>{sets.length} 个产品</small></th></tr>
          {sets.map(product => row(product))}
          {!sets.length && <tr><td colSpan={5 + locations.length} className="pc-ser-muted">—</td></tr>}
          <tr className="pc-ser-group"><th colSpan={5 + locations.length} scope="rowgroup">确认款 Confirmed designs <small>{designs.length} 款</small></th></tr>
          {designs.map((product, index) => row(product, index + 1))}
          {!designs.length && <tr><td colSpan={5 + locations.length} className="pc-ser-muted">还没有确认款 · No confirmed designs yet.</td></tr>}
        </tbody>
      </table></div>
    </div>
    {setup}
  </section>
}
