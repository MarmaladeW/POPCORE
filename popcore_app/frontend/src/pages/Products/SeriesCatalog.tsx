import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Input, Skeleton } from 'antd'
import { useAuth0 } from '@auth0/auth0-react'
import { Link, useSearchParams } from 'react-router-dom'
import { getInventorySeries, type InventorySeries, type SeriesInventoryResult, type SeriesProduct } from '../../api/inventorySeries'
import { useRole } from '../../auth/useRole'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { useAppStore } from '../../store'
import { ProductsTabs } from './ProductsNav'
import SubItemAdd from './SubItemAdd'
import './products.css'

const failure = (cause: unknown) => (cause as { _serverMessage?: string })?._serverMessage || 'Unable to load sets and designs. Please retry.'
const formNames: Record<string, string> = { sealed_set: '端', random_box: '盒', confirmed_design: '个', ordinary: '' }
const saleable = (product: SeriesProduct, locationId: number) => product.balances.find(balance => balance.location_id === locationId && balance.disposition === 'saleable')?.quantity
const isBack = (code: string) => ['upstairs', 'warehouse'].includes(code)

// 整端·确认款 Sets & designs: every product that has sub-items (its 整端 sealed set and confirmed designs),
// with stock per location. Sub-items are added on the product itself (SubItemAdd); there is no separate
// series to set up.
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

  return <div className="pc-ser">
    <PageHeader title="产品" sub={phone ? 'Sets & designs' : `Products · ${data ? `${data.series.length} 个产品有子项` : 'Sets & designs'}${storeName ? ` · ${storeName}` : ''}`} />
    <ProductsTabs />
    {!usable ? <p className="pc-notice">Choose one store to see stock.</p>
      : current?.error ? <Alert role="alert" type="error" showIcon message={current.error} action={<Button onClick={() => setRefresh(value => value + 1)}>Retry</Button>} />
      : !data ? <Skeleton active />
      : !data.series.length ? <p className="pc-notice">No product has a sealed set or confirmed designs yet. Open a product in the Catalog and add them there.</p>
      : <div className="pc-ser-grid">
          {(!phone || !selected) && <aside className="pc-ser-side">
            <Input allowClear prefix={<Icon name="search" size={16} />} placeholder="搜索产品 Search products" aria-label="Search products" value={query} onChange={event => setQuery(event.target.value)} />
            <ul className="pc-panel pc-ser-list">
              {list.map(series => { const designs = series.products.filter(product => product.stock_form === 'confirmed_design').length
                return <li key={series.id}><button type="button" aria-current={selected?.id === series.id ? 'true' : undefined} onClick={() => setParams({ series_id: String(series.id) })}>
                  <span><strong>{series.name}</strong><small>{designs ? `${series.products.length} 个产品` : `还没有确认款 · ${series.products.length} 个产品`}</small></span><b>{designs} 款</b>
                </button></li> })}
              {!list.length && <li className="pc-quiet">No products match.</li>}
            </ul>
          </aside>}
          {selected && <SeriesDetail series={selected} locations={data.locations} manager={manager} phone={phone}
            onBack={() => setParams({})} onSaved={() => setRefresh(value => value + 1)} />}
        </div>}
  </div>
}

function SeriesDetail({ series, locations, manager, phone, onBack, onSaved }: {
  series: InventorySeries; locations: SeriesInventoryResult['locations']; manager: boolean; phone: boolean
  onBack: () => void; onSaved: () => void
}) {
  const sets = series.products.filter(product => product.stock_form !== 'confirmed_design')
  const designs = series.products.filter(product => product.stock_form === 'confirmed_design')
  const owner = series.products.find(product => product.id === series.product_id)
  const canAddSealedSet = !sets.some(product => product.stock_form === 'sealed_set') && (!owner?.stock_form || owner.stock_form === 'random_box')
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
    {phone && <button type="button" className="pc-ser-back" onClick={onBack}><Icon name="back" size={14} />全部 All products</button>}
    <div className="pc-figures pc-figures-4">
      <div><span>确认款 Confirmed designs</span><strong>{designs.length}</strong></div>
      <div><span>整端 Sealed sets</span><strong>{byKind('sealed_set')}</strong></div>
      <div><span>盲盒 Single boxes</span><strong>{byKind('random_box')}</strong></div>
      <div><span>确认款库存 Design stock</span><strong>{known ? designStock.reduce<number>((sum, value) => sum + (value || 0), 0) : '—'}</strong>
        {known && <small>楼面 {sumAt(code => !isBack(code))} · 楼上 {sumAt(isBack)}</small>}</div>
    </div>
    <div className="pc-panel">
      <header className="pc-panel-head"><h2><span>{series.name}</span> <small>{designs.length} 款确认款</small></h2>
        {series.product_id && <Link className="pc-btn" to={`/products?product_id=${series.product_id}`}>产品详情 Product</Link>}</header>
      {manager && series.product_id && <SubItemAdd key={series.id} productId={series.product_id} canAddSealedSet={canAddSealedSet} onSaved={onSaved} />}
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
  </section>
}
