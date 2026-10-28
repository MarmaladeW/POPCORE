import { Alert, Button, Drawer, Input, Skeleton, Space, Typography } from 'antd'
import { ArrowLeftOutlined, ArrowRightOutlined, HistoryOutlined, ReloadOutlined } from '@ant-design/icons'
import { useEffect, useState } from 'react'
import { useAuth0 } from '@auth0/auth0-react'
import { Link, useSearchParams } from 'react-router-dom'
import { useRole, type Role } from '../../auth/useRole'
import { useAppStore } from '../../store'
import { getInventorySeries, type InventorySeries, type SeriesInventoryResult, type SeriesProduct } from '../../api/inventorySeries'
import useCheckoutMutation from '../Sales/useCheckoutMutation'
import { torontoDate } from '../Dashboard/todayPresentation'
import SeriesHistory from './SeriesHistory'
import SeriesWorksheet from './SeriesWorksheet'
import SeriesStockAction from './SeriesStockAction'
import InventoryPhoto from './InventoryPhoto'
import { inventoryAttention, type InventoryAttention } from '../../lib/inventoryAttention'
import './seriesInventory.css'
import PageHeader from '../../components/pc/PageHeader'
import { useIsPhone } from '../../components/pc/useIsPhone'
import InventoryNav, { useHasClaw } from './InventoryNav'
import RemoveDesigns from '../Products/RemoveDesigns'

const failure = (cause:unknown) => (cause as {_serverMessage?:string})?._serverMessage || 'Unable to load inventory. Please retry.'
const units = (amount:number, unit:string|null) => `${amount} ${unit === 'box' ? (amount===1?'box':'boxes') : `${unit||'unit'}${amount===1?'':'s'}`}`
const dispositionName = (value:string) => ({saleable:'Saleable',hold:'On hold',damaged:'Damaged',trade:'Trade eligible',transit:'In transit',display:'Display'}[value] || value.replaceAll('_',' '))
const saleable = (product:SeriesProduct|undefined, locationId:number|undefined) => product?.balances.find(balance=>balance.location_id===locationId&&balance.disposition==='saleable')

export default function SeriesInventory() {
  const store = useAppStore(state=>state.selectedStore)
  const {user} = useAuth0(), role = useRole()
  if (!store) return <Alert type="info" message="Select a store to view inventory." />
  return <InventoryScope key={`${store.id}|${user?.sub}|${role}`} storeCode={store.code} storeName={store.name} role={role} />
}

function InventoryScope({storeCode,storeName,role}:{storeCode:string;storeName:string;role:Role}) {
  const phone = useIsPhone()
  const hasClaw = useHasClaw()
  const [params,setParams] = useSearchParams()
  const rawId = params.get('series_id'), selectedId = rawId && /^\d+$/.test(rawId) && Number(rawId)>0 ? Number(rawId) : undefined
  // A plain product (no designs or sealed set, so no series) opens on its own.
  const rawProductId = params.get('product_id'), productId = rawProductId && /^\d+$/.test(rawProductId) && Number(rawProductId)>0 ? Number(rawProductId) : undefined
  const opened = selectedId ?? productId
  const [query,setQuery] = useState(''), [search,setSearch] = useState(''), [refresh,setRefresh] = useState(0)
  const [result,setResult] = useState<{scope:string;data?:SeriesInventoryResult;error?:string}>()
  const [historyOpen,setHistoryOpen] = useState(false)
  const [panel,setPanel] = useState<'identify'>(), [saved,setSaved] = useState<'identify'|'stock'>()
  const [sourceId,setSourceId] = useState<number>(), [designId,setDesignId] = useState<number>(), [locationId,setLocationId] = useState<number>()
  const [openSetId,setOpenSetId] = useState<number>()
  const [detailId,setDetailId] = useState<number>()
  const [worksheet,setWorksheet] = useState<'count'|'receive'>()
  const [stockAction,setStockAction] = useState<{kind:'move'|'open_set';productId:number}>()
  const [stockFilter,setStockFilter] = useState<'all'|'attention'|InventoryAttention>('all')
  const [designQuery,setDesignQuery] = useState('')
  const [showSideLocations,setShowSideLocations] = useState(false)
  const [quantity,setQuantity] = useState(1), [reason,setReason] = useState('')
  const scope = `${storeCode}|${selectedId}|${productId}|${search}|${refresh}`
  const current = result?.scope===scope ? result : undefined
  const data = current?.data
  const plain = productId ? data?.products?.find(product=>product.id===productId) : undefined
  const selected = selectedId ? data?.series.find(series=>series.id===selectedId)
    : plain ? {id:0, name:plain.name, product_id:plain.id, products:[plain, ...(plain.variations ?? [])]} : undefined
  const standalone = !!plain
  const canWork = storeCode!=='ALL' && ['staff','manager','admin'].includes(role)
  const manager = storeCode!=='ALL' && ['manager','admin'].includes(role)
  const mutation = useCheckoutMutation(()=>{
    setSaved('identify')
    setPanel(undefined); setSourceId(undefined); setDesignId(undefined); setLocationId(undefined); setOpenSetId(undefined); setQuantity(1); setReason('')
    setRefresh(value=>value+1)
  })
  const locked = mutation.pending || mutation.saving
  useEffect(()=>{
    window.dispatchEvent(new CustomEvent('popcore:checkout-busy',{detail:locked}))
    return ()=>{window.dispatchEvent(new CustomEvent('popcore:checkout-busy',{detail:false}))}
  },[locked])

  useEffect(()=>{
    const controller = new AbortController()
    getInventorySeries(storeCode,opened ? '' : search,selectedId,controller.signal,productId)
      .then(value=>{if(!controller.signal.aborted)setResult({scope,data:value})})
      .catch(cause=>{if(!controller.signal.aborted)setResult({scope,error:failure(cause)})})
    return ()=>controller.abort()
  },[scope,storeCode,selectedId,productId,opened,search])
  useEffect(()=>{setPanel(undefined);setHistoryOpen(false);setDetailId(undefined);setWorksheet(undefined);setStockAction(undefined);setDesignQuery('');setSourceId(undefined);setDesignId(undefined);setLocationId(undefined);setOpenSetId(undefined)},[selectedId,productId])

  const source = selected?.products.find(product=>product.id===sourceId)
  const design = selected?.products.find(product=>product.id===designId)
  const sourceBalance = saleable(source,locationId), targetBalance = saleable(design,locationId)
  const location = data?.locations.find(item=>item.id===locationId)
  const openSets = source?.open_sets?.filter(item=>item.location_id===locationId&&item.remaining_qty>0) || []
  const selectedSet = openSets.find(item=>item.id===openSetId)
  const availableSource = openSetId ? selectedSet?.remaining_qty||0 : Math.max(0,(sourceBalance?.quantity||0)-openSets.reduce((total,item)=>total+item.remaining_qty,0))
  const identifyReady = canWork && data?.mode==='authoritative' && location?.opening_verified && source?.identity_status==='verified' && design?.identity_status==='verified'
    && sourceBalance?.quantity!=null && sourceBalance.version!=null && targetBalance?.quantity!=null && targetBalance.version!=null
    && Number.isInteger(quantity) && quantity>0 && quantity<=sourceBalance.quantity && quantity<=availableSource && !!reason.trim()

  function identify() {
    if (!identifyReady || !source || !design || !location || !sourceBalance || !targetBalance) return
    void mutation.run('/goods/identify',{source_product_id:source.id,design_product_id:design.id,location_id:location.id,quantity,
      source_version:sourceBalance.version,target_version:targetBalance.version,business_date:torontoDate(),reason:reason.trim(),...(openSetId?{open_set_id:openSetId}:{})})
  }

  const detail = selected?.products.find(product=>product.id===detailId)
  const actionProduct = selected?.products.find(product=>product.id===stockAction?.productId)
  function openStockAction(kind:'move'|'open_set',productId:number) {setDetailId(undefined);setStockAction({kind,productId});setSaved(undefined)}
  const designs = selected?.products.filter(product=>product.stock_form==='confirmed_design') || []
  // A series reads top-down: sealed sets, then single boxes, then each confirmed design. The product that owns the
  // series is its single box even before its stock type is reviewed.
  const isBox = (product:SeriesProduct) => product.stock_form==='random_box' || (product.stock_form===null && product.id===selected?.product_id)
  const sections = standalone && selected && !plain?.variations?.length ? [
    {key:'stock', title:'库存 Stock', note:'', label:'Product inventory', products:selected.products},
  ] : selected ? ([
    {key:'sealed_set', title:'整端 Sealed sets', note:'Unopened sets', label:'Sealed set inventory', products:selected.products.filter(product=>product.stock_form==='sealed_set')},
    {key:'random_box', title:'单盒 Single boxes', note:'Design not yet confirmed', label:'Single box inventory', products:selected.products.filter(isBox)},
    {key:'confirmed_design', title:'确认款 Confirmed designs', note:'Each design is stocked separately', label:'Confirmed design inventory', products:designs},
    {key:'ordinary', title:'Other products', note:'', label:'Other product inventory', products:selected.products.filter(product=>product.stock_form==='ordinary')},
    {key:'review', title:'Identity needs review', note:'', label:'Unreviewed inventory', products:selected.products.filter(product=>product.stock_form===null&&!isBox(product))},
  ] as const) : []
  function matchesStock(product:SeriesProduct) {
    const attention=inventoryAttention(product,data?.locations||[])
    const match=stockFilter==='all'||(stockFilter==='attention'?attention.length>0:attention.includes(stockFilter))
    const needle=designQuery.trim().normalize('NFKC').toLocaleLowerCase()
    return match&&(!selected||!needle||[product.name,product.design_name,product.sku,product.jizhanming,product.name_cn_en,product.alias_text].filter(Boolean).join(' ').normalize('NFKC').toLocaleLowerCase().includes(needle))
  }
  const visibleSeries=data?.series.filter(series=>stockFilter==='all'||series.products.some(matchesStock))||[]
  const visiblePlain=data?.products?.filter(product=>stockFilter==='all'||[product,...(product.variations??[])].some(matchesStock))||[]
  // One A-Z list: a series (product with designs or a sealed set) opens its sub-items; a plain product opens its own stock.
  const entries:{key:string;name:string;series?:InventorySeries;product?:SeriesProduct}[]=[...visibleSeries.map(series=>({key:`s${series.id}`,name:series.name,series})),...visiblePlain.map(product=>({key:`p${product.id}`,name:product.name,product}))]
    .sort((a,b)=>a.name.localeCompare(b.name,'zh-CN'))

  return <main className="pc-series-inventory">
    <PageHeader
      title={selected?.name || '库存'}
      sub={selected ? <>{storeName} · {data?.locations.length} stock locations</> : `Inventory${storeName ? ` · ${storeName}` : ''}`}
      back={opened ? { label: '全部商品', to: '/stock' } : undefined}
      actions={<>{!opened&&<InventoryNav hasClaw={hasClaw} phone={false}/>}<div className="pc-series-toolbar">
          <Button className="pc-series-refresh" aria-label="Refresh inventory" title="Refresh inventory" icon={<ReloadOutlined />} disabled={locked} onClick={()=>setRefresh(value=>value+1)} />
          {selected&&!standalone&&<Button aria-label="Show movement history" icon={<HistoryOutlined />} disabled={locked} onClick={()=>setHistoryOpen(true)}>记录 History</Button>}
          {selected&&!standalone&&canWork&&<Button disabled={locked} onClick={()=>{setPanel('identify');setSaved(undefined)}}>识别盲盒 Identify boxes</Button>}
        </div></>}
      phoneAction={undefined} />
    {phone&&!opened&&<InventoryNav hasClaw={hasClaw} phone/>}
    {phone&&<div className="pc-series-phone-tools"><div className="pc-series-toolbar">
          <Button className="pc-series-refresh" aria-label="Refresh inventory" title="Refresh inventory" icon={<ReloadOutlined />} disabled={locked} onClick={()=>setRefresh(value=>value+1)} />
          {selected&&!standalone&&<Button aria-label="Show movement history" icon={<HistoryOutlined />} disabled={locked} onClick={()=>setHistoryOpen(true)}>记录 History</Button>}
          {selected&&!standalone&&canWork&&<Button disabled={locked} onClick={()=>{setPanel('identify');setSaved(undefined)}}>识别盲盒 Identify boxes</Button>}
        </div></div>}
    {storeCode==='ALL'&&<Alert type="info" showIcon message="Viewing all authorized stores. Select one store to receive, count, or identify stock." />}
    {saved&&<Alert type="success" showIcon message={`${saved==='stock'?'Stock change':'Identification'} saved. ${data?'Inventory refreshed.':current?.error?'Inventory refresh needed.':'Refreshing inventory.'}`} />}
    {!panel&&mutation.notice}
    {!current?<Skeleton active paragraph={{rows:5}}/>:current.error?<Alert type="error" showIcon message={current.error} action={<Button disabled={locked} onClick={()=>setRefresh(value=>value+1)}>Retry inventory</Button>}/>:
      data&&<>
        {data.mode!=='authoritative'&&<Alert type="warning" showIcon message="Not live yet: quantities stay Unknown until the store goes live. Counts saved in 盘点 Stocktake are kept and show here from then."/>}
        <div className="pc-series-filterbar"><label>Stock view<select aria-label="Inventory status" value={stockFilter} onChange={event=>setStockFilter(event.target.value as typeof stockFilter)} disabled={locked}>
          <option value="all">All stock</option><option value="attention">Needs attention</option><option value="out_of_stock">Out of stock</option><option value="replenish">Replenish floor</option><option value="unverified">Unverified stock</option><option value="condition">On hold / damaged</option>
        </select></label>{selected&&<Input aria-label="Find design in this series" placeholder="Find a design" value={designQuery} onChange={event=>setDesignQuery(event.target.value)} disabled={locked} allowClear/>}
          {selected&&data.locations.some(location=>SIDE_LOCATIONS.includes(location.code))&&<label className="pc-series-side-toggle"><input type="checkbox" checked={showSideLocations} onChange={event=>setShowSideLocations(event.target.checked)}/>陈列 · 娃娃机 Display &amp; claw</label>}</div>
        {(rawId&&!selectedId)||(rawProductId&&!productId)?<Alert type="error" message="Invalid inventory reference."/>:opened&&!selected?<Alert type="warning" message="This product is unavailable in the selected scope."/>:selected?<>
          {/* Before go-live, counting is 盘点 and there is no stock to receive into. */}
          {canWork&&<div className="pc-series-batch pc-series-batch-top">{data.mode==='authoritative'?<><Button disabled={locked||!selected.products.length} onClick={()=>setWorksheet('count')}>{standalone?'晚盘 Count':'按系列晚盘 Count series'}</Button><Button type="primary" disabled={locked||!selected.products.length} onClick={()=>setWorksheet('receive')}>{standalone?'入库 Receive':'整系列入库 Receive series'}</Button></>
            :(selected.product_id||selected.products[0])&&<Link className="pc-btn pc-btn-ink" to={`/count?product=${selected.product_id||selected.products[0].id}`}>盘点 Count</Link>}</div>}
          {sections.map(section=>{
            const products=section.products.filter(matchesStock)
            if(!section.products.length&&(section.key!=='confirmed_design'||standalone))return null
            return <section className={'pc-series-section'+(section.key==='confirmed_design'?'':' pc-series-packaging')} key={section.key} aria-labelledby={`${section.key}-heading`}>
              <div className="pc-series-section-heading"><div><h3 id={`${section.key}-heading`}>{section.title}</h3>{section.note&&<p>{section.note}</p>}</div>{section.key==='confirmed_design'&&<span className="pc-series-count">{designs.length} designs</span>}</div>
              <StockTable products={products} data={data} canWork={canWork&&!locked&&section.key!=='review'} onDetail={setDetailId} label={section.label}
                locations={visibleLocations(data.locations, products, showSideLocations, section.key==='confirmed_design')} />
            </section>
          })}
        </>:!rawId&&!rawProductId&&<>
          <div className="pc-series-search"><Input.Search aria-label="Search series or design" placeholder="Search product, series, design, or SKU" value={query} onChange={event=>setQuery(event.target.value)} onSearch={value=>setSearch(value.trim())} disabled={locked} allowClear /><span>{entries.length} products · {visibleSeries.length} with designs</span></div>
          {!entries.length?<p className="pc-series-empty">No products match this search and stock view.</p>:<ul className="pc-series-list">{entries.map(entry=>{
            // Before the opening count every quantity is unverified, so "needs attention" would be on every row.
            const flagged=(product:SeriesProduct)=>inventoryAttention(product,data.locations).some(reason=>data.mode==='authoritative'||reason!=='unverified')
            if(entry.series){const series=entry.series,attention=series.products.filter(flagged).length
              return <li key={entry.key}><Link to={`/stock?series_id=${series.id}`}><span className="pc-series-list-name">{series.name}<span>{series.products.length} products{attention>0&&<> · {attention} need attention</>}</span></span><span className="pc-series-list-count">{series.products.filter(product=>product.stock_form==='confirmed_design').length} named designs</span><ArrowRightOutlined aria-hidden /></Link></li>}
            const product=entry.product!,attention=[product,...(product.variations??[])].some(flagged)
            return <li key={entry.key}><Link to={`/stock?product_id=${product.id}`}><span className="pc-series-list-name">{product.name}<span>{product.sku}{attention&&<> · needs attention</>}</span></span><span className="pc-series-list-count">{product.variations?.length?'整端 · 单盒':''}</span><ArrowRightOutlined aria-hidden /></Link></li>
          })}</ul>}
        </>}
        <footer className="pc-series-footer">
          {selected?.product_id&&manager&&<Link to={`/products?product_id=${selected.product_id}`}>添加确认款或整端 Add designs or a sealed set</Link>}
          {selected?.product_id&&manager&&!standalone&&<RemoveDesigns productId={selected.product_id} designCount={designs.length} onDone={()=>setParams({product_id:String(selected.product_id)})}/>}
          <nav aria-label="Inventory tools" className="pc-inventory-tools"><Link to="/products">Products</Link><Link to="/restock">Restock</Link><Link to="/trades">Trades</Link><Link to="/stock/products">All products</Link></nav>
        </footer>
      </>}
    <Drawer rootClassName="pc-inventory-drawer" title="Identify boxes" width="min(100%, 560px)" open={panel==='identify'} onClose={()=>{if(!locked)setPanel(undefined)}} keyboard={!locked} maskClosable={!locked} closable={!locked} destroyOnClose
      footer={<Space wrap><Button type="primary" disabled={locked||!identifyReady} loading={mutation.saving} onClick={identify}>Confirm identification</Button><Button disabled={locked} onClick={()=>setPanel(undefined)}>Cancel identification</Button></Space>}>
      {panel==='identify'&&selected&&data&&<div className="pc-series-form">{mutation.notice}
            <p>After physically confirming the design, move the box quantity into that exact named design at the same location. Sealed sets stay separate.</p>
            <fieldset className="pc-series-identify-fields" disabled={locked}>
              <label>Source boxes<select aria-label="Source boxes" value={sourceId||''} onChange={event=>{setSourceId(Number(event.target.value)||undefined);setOpenSetId(undefined)}}><option value="">Choose unidentified boxes</option>{selected.products.filter(product=>product.stock_form==='random_box'&&product.identity_status==='verified').map(product=><option key={product.id} value={product.id}>{product.name}</option>)}</select></label>
              <label>Named design<select aria-label="Named design" value={designId||''} onChange={event=>setDesignId(Number(event.target.value)||undefined)}><option value="">Choose the confirmed design</option>{selected.products.filter(product=>product.stock_form==='confirmed_design'&&product.identity_status==='verified').map(product=><option key={product.id} value={product.id}>{product.design_name||product.name}</option>)}</select></label>
              <label>Location<select aria-label="Location" value={locationId||''} onChange={event=>{setLocationId(Number(event.target.value)||undefined);setOpenSetId(undefined)}}><option value="">Choose a reviewed location</option>{data.locations.map(item=><option key={item.id} value={item.id} disabled={!item.opening_verified}>{item.name}{item.opening_verified?'':' · Opening unreviewed'}</option>)}</select></label>
              <label>Box source<select aria-label="Box source" value={openSetId||''} disabled={!source||!location} onChange={event=>setOpenSetId(Number(event.target.value)||undefined)}><option value="">Loose boxes</option>{openSets.map(item=><option key={item.id} value={item.id}>Opened set #{item.id} · {units(item.remaining_qty,'box')} remaining · {item.purpose.replaceAll('_',' ')}</option>)}</select></label>
              <label>Identification quantity<input aria-label="Identification quantity" type="number" min="1" step="1" value={quantity} onChange={event=>setQuantity(Number(event.target.value))}/></label>
              <label>Identification reason<input aria-label="Identification reason" value={reason} onChange={event=>setReason(event.target.value)} maxLength={1000}/></label>
            </fieldset>
            <p className="pc-series-effect">−{quantity} {quantity===1?'box':'boxes'} → +{quantity} {design?.design_name||design?.name||'named design'} {quantity===1?'piece':'pieces'}</p>
            {sourceBalance?.quantity!=null&&<p>{units(availableSource,'box')} available from {selectedSet?`opened set #${selectedSet.id}`:'loose stock'} at {location?.name} before identification.</p>}
            {!mutation.pending&&/opened set|protected units/i.test(mutation.error)&&<Alert type="warning" showIcon message="This stock needs its retained opened-set record." description="Choose the correct opened set in Box source above. If it is missing, refresh inventory before continuing. The rejected identification did not change stock."/>}
      </div>}
    </Drawer>
    {worksheet&&selected&&data&&canWork&&<SeriesWorksheet mode={worksheet} series={selected} locations={data.locations} onClose={()=>setWorksheet(undefined)}/>}
    {stockAction&&actionProduct&&data&&canWork&&<SeriesStockAction kind={stockAction.kind} product={actionProduct} data={data} onClose={()=>setStockAction(undefined)} onSaved={()=>{setStockAction(undefined);setSaved('stock');setRefresh(value=>value+1)}}/>}
    {historyOpen&&selected&&<SeriesHistory series={selected} storeCode={storeCode} onClose={()=>setHistoryOpen(false)}/>}
    <Drawer rootClassName="pc-inventory-drawer" title="Stock details" width="min(100%, 480px)" open={!!detail} onClose={()=>setDetailId(undefined)} destroyOnClose>
      {detail&&data&&<div className="pc-series-stock-details"><InventoryPhoto key={detail.image_filename} filename={detail.image_filename} name={detail.design_name||detail.name} large/><h2>{detail.design_name||detail.name}</h2><p>{detail.name_cn_en&&detail.name_cn_en!==(detail.design_name||detail.name)&&<>{detail.name_cn_en} · </>}{detail.sku} · {detail.identity_status==='verified'?'Reviewed identity':'Identity needs review'}</p>
        {canWork&&data.mode==='authoritative'&&detail.identity_status==='verified'&&<Space wrap><Button onClick={()=>openStockAction('move',detail.id)}>Move stock</Button>{detail.stock_form==='sealed_set'&&<Button type="primary" onClick={()=>openStockAction('open_set',detail.id)}>Open sealed set</Button>}</Space>}
        {data.locations.map(location=><section key={location.id}><h3>{location.store_code} · {location.name}</h3>{!location.opening_verified&&<p className="pc-series-warning">Opening count needs review</p>}<dl>{detail.balances.filter(balance=>balance.location_id===location.id).map(balance=><div key={balance.disposition}><dt>{dispositionName(balance.disposition)}:</dt> <dd>{balance.quantity===null?'Unknown':units(balance.quantity,detail.stock_unit)}</dd></div>)}</dl></section>)}
      </div>}
    </Drawer>
  </main>
}

type SeriesLocation = SeriesInventoryResult['locations'][number]
// Display and the claw machine are secondary in day-to-day work: hidden until asked for, except where they hold stock.
// Confirmed designs don't go into the claw machine, so it never shows for them unless stock is actually there.
const SIDE_LOCATIONS = ['display','claw']
function visibleLocations(locations:SeriesLocation[], products:SeriesProduct[], showSide:boolean, designs:boolean) {
  return locations.filter(location=>{
    if (!SIDE_LOCATIONS.includes(location.code)) return true
    if (products.some(product=>(saleable(product,location.id)?.quantity||0)>0)) return true
    return showSide && !(designs && location.code==='claw')
  })
}

function StockTable({products,data,canWork,onDetail,label,locations}:{products:SeriesProduct[];data:SeriesInventoryResult;canWork:boolean;onDetail:(id:number)=>void;label:string;locations:SeriesLocation[]}) {
  const multipleStores = new Set(data.locations.map(item=>item.store_code)).size>1
  if(!products.length)return <p className="pc-series-empty">No products match this stock view. Try All stock or clear the search.</p>
  return <div className="pc-series-table-wrap"><table className="pc-series-table" aria-label={label}>
    <thead><tr><th scope="col">Design / product</th>{locations.map(location=><th scope="col" key={location.id} className={'pc-series-location'+(['upstairs','warehouse'].includes(location.code)?' pc-kraft-cell':'')}>{multipleStores&&<span>{location.store_code} · </span>}{location.name}</th>)}<th scope="col" className="pc-series-available">Available</th>{canWork&&<th scope="col" className="pc-series-actions-heading"><span className="pc-series-sr-only">Actions</span></th>}</tr></thead>
    <tbody>{products.map(product=>{
      const balances=product.balances.filter(balance=>balance.disposition==='saleable')
      const known=balances.length>0&&balances.every(balance=>balance.quantity!==null)
      const total=balances.reduce((sum,balance)=>sum+(balance.quantity||0),0)
      const name=product.design_name||product.name
      const exceptions=product.balances.filter(balance=>balance.disposition!=='saleable'&&balance.quantity!==null&&balance.quantity>0)
      return <tr className="pc-series-product" key={product.id}>
        <th scope="row" className="pc-series-product-name"><button type="button" aria-label={`Stock details for ${name}`} onClick={()=>onDetail(product.id)}><InventoryPhoto key={product.image_filename} filename={product.image_filename} name={name}/><span>{name}</span></button>
          <div className="pc-series-mobile-locations">{locations.map(location=><span key={location.id}>{multipleStores?`${location.store_code} · `:''}{location.name} <b>{saleable(product,location.id)?.quantity??'Unknown'}</b></span>)}</div>
          {inventoryAttention(product,data.locations).includes('replenish')&&<span className="pc-series-warning">Replenish floor · back stock available</span>}
          {product.identity_status!=='verified'&&<span className="pc-series-warning">Identity needs review</span>}
          {exceptions.length>0&&<span className="pc-series-exceptions">{exceptions.map(balance=>{const location=data.locations.find(item=>item.id===balance.location_id);return <span key={`${balance.location_id}|${balance.disposition}`}>{dispositionName(balance.disposition)}: {units(balance.quantity!,product.stock_unit)} · {multipleStores?`${location?.store_code} · `:''}{location?.name}</span>})}</span>}
        </th>
        {locations.map(location=><td key={location.id} className={'pc-series-location'+(['upstairs','warehouse'].includes(location.code)?' pc-kraft-cell':'')}>{saleable(product,location.id)?.quantity??<span className="pc-series-unknown">Unknown</span>}</td>)}
        <td className={`pc-series-available ${known&&total===0?'pc-series-zero':''}`}><strong>{known?units(total,product.stock_unit):'Unknown'}</strong>{known&&total===0&&<span>Out of stock</span>}{!known&&<span>Not yet verified</span>}</td>
        {canWork&&<td className="pc-series-product-actions">{data.mode!=='authoritative'?<Link aria-label={`盘点 Count ${name}`} to={`/count?product=${product.id}`}>盘点</Link>
          :product.identity_status==='verified'&&product.stock_unit&&<><Link aria-label={`Receive ${name}`} to={`/goods/receiving?product_id=${product.id}`}>Receive</Link><Link aria-label={`Count ${name}`} to={`/goods/counts?product_id=${product.id}`}>Count</Link></>}</td>}
      </tr>
    })}</tbody>
  </table></div>
}
