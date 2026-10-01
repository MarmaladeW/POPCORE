import { Alert, Button, Drawer, Input, Skeleton, Space, Typography } from 'antd'
import { ArrowLeftOutlined, ArrowRightOutlined, HistoryOutlined, ReloadOutlined } from '@ant-design/icons'
import { useEffect, useState } from 'react'
import { useAuth0 } from '@auth0/auth0-react'
import { Link, useSearchParams } from 'react-router-dom'
import { useRole, type Role } from '../../auth/useRole'
import { useAppStore } from '../../store'
import { getInventorySeries, getSeriesCatalog, type SeriesInventoryResult, type SeriesProduct } from '../../api/inventorySeries'
import useCheckoutMutation from '../Sales/useCheckoutMutation'
import { torontoDate } from '../Dashboard/todayPresentation'
import SeriesHistory from './SeriesHistory'
import SeriesWorksheet from './SeriesWorksheet'
import SeriesStockAction from './SeriesStockAction'
import InventoryPhoto from './InventoryPhoto'
import { inventoryAttention, type InventoryAttention } from '../../lib/inventoryAttention'
import './seriesInventory.css'

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
  const [params,setParams] = useSearchParams()
  const rawId = params.get('series_id'), selectedId = rawId && /^\d+$/.test(rawId) && Number(rawId)>0 ? Number(rawId) : undefined
  const [query,setQuery] = useState(''), [search,setSearch] = useState(''), [refresh,setRefresh] = useState(0)
  const [result,setResult] = useState<{scope:string;data?:SeriesInventoryResult;error?:string}>()
  const [historyOpen,setHistoryOpen] = useState(false)
  const [panel,setPanel] = useState<'setup'|'identify'>(), [saved,setSaved] = useState<'setup'|'identify'|'stock'>()
  const [catalog,setCatalog] = useState<Array<{id:number;name:string}>>(), [catalogError,setCatalogError] = useState('')
  const [setupSeries,setSetupSeries] = useState('new'), [seriesName,setSeriesName] = useState(''), [designNames,setDesignNames] = useState(''), [nameRows,setNameRows] = useState(6)
  const [sourceId,setSourceId] = useState<number>(), [designId,setDesignId] = useState<number>(), [locationId,setLocationId] = useState<number>()
  const [openSetId,setOpenSetId] = useState<number>()
  const [detailId,setDetailId] = useState<number>()
  const [worksheet,setWorksheet] = useState<'count'|'receive'>()
  const [stockAction,setStockAction] = useState<{kind:'move'|'open_set';productId:number}>()
  const [stockFilter,setStockFilter] = useState<'all'|'attention'|InventoryAttention>('all')
  const [designQuery,setDesignQuery] = useState('')
  const [quantity,setQuantity] = useState(1), [reason,setReason] = useState('')
  const scope = `${storeCode}|${selectedId}|${search}|${refresh}`
  const current = result?.scope===scope ? result : undefined
  const data = current?.data, selected = data?.series.find(series=>series.id===selectedId)
  const canWork = storeCode!=='ALL' && ['staff','manager','admin'].includes(role)
  const manager = storeCode!=='ALL' && ['manager','admin'].includes(role)
  const mutation = useCheckoutMutation(response=>{
    setSaved(response.consume_document_id ? 'identify' : 'setup')
    setPanel(undefined); setSourceId(undefined); setDesignId(undefined); setLocationId(undefined); setOpenSetId(undefined); setQuantity(1); setReason('')
    if (!response.consume_document_id) { setParams({series_id:String(response.id)}); setDesignNames(''); setSeriesName('') }
    setRefresh(value=>value+1)
  })
  const locked = mutation.pending || mutation.saving
  useEffect(()=>{
    window.dispatchEvent(new CustomEvent('popcore:checkout-busy',{detail:locked}))
    return ()=>{window.dispatchEvent(new CustomEvent('popcore:checkout-busy',{detail:false}))}
  },[locked])

  useEffect(()=>{
    const controller = new AbortController()
    getInventorySeries(storeCode,selectedId ? '' : search,selectedId,controller.signal)
      .then(value=>{if(!controller.signal.aborted)setResult({scope,data:value})})
      .catch(cause=>{if(!controller.signal.aborted)setResult({scope,error:failure(cause)})})
    return ()=>controller.abort()
  },[scope,storeCode,selectedId,search])
  useEffect(()=>{
    if(panel!=='setup')return
    const controller = new AbortController(); setCatalog(undefined); setCatalogError('')
    getSeriesCatalog(controller.signal).then(items=>{if(!controller.signal.aborted)setCatalog(items)}).catch(cause=>{if(!controller.signal.aborted)setCatalogError(failure(cause))})
    return ()=>controller.abort()
  },[panel,refresh])
  useEffect(()=>{setPanel(undefined);setHistoryOpen(false);setDetailId(undefined);setWorksheet(undefined);setStockAction(undefined);setDesignQuery('');setSourceId(undefined);setDesignId(undefined);setLocationId(undefined);setOpenSetId(undefined)},[selectedId])

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
  const names = designNames.split('\n').map(name=>name.trim()).filter(Boolean)
  const duplicateNames = new Set(names.map(name=>name.toLocaleLowerCase())).size!==names.length
  const setupReady = manager && !!catalog && names.length>0 && !duplicateNames && (setupSeries!=='new'||!!seriesName.trim())

  function identify() {
    if (!identifyReady || !source || !design || !location || !sourceBalance || !targetBalance) return
    void mutation.run('/goods/identify',{source_product_id:source.id,design_product_id:design.id,location_id:location.id,quantity,
      source_version:sourceBalance.version,target_version:targetBalance.version,business_date:torontoDate(),reason:reason.trim(),...(openSetId?{open_set_id:openSetId}:{})})
  }
  function setup() {
    if (!setupReady)return
    void mutation.run('/product-series/setup',{...(setupSeries==='new'?{name:seriesName.trim()}:{series_id:Number(setupSeries)}),design_names:names})
  }
  function openSetup() {setSetupSeries(selectedId?String(selectedId):'new');setPanel('setup');setSaved(undefined)}

  const detail = selected?.products.find(product=>product.id===detailId)
  const actionProduct = selected?.products.find(product=>product.id===stockAction?.productId)
  function openStockAction(kind:'move'|'open_set',productId:number) {setDetailId(undefined);setStockAction({kind,productId});setSaved(undefined)}
  const designs = selected?.products.filter(product=>product.stock_form==='confirmed_design') || []
  function matchesStock(product:SeriesProduct) {
    const attention=inventoryAttention(product,data?.locations||[])
    const match=stockFilter==='all'||(stockFilter==='attention'?attention.length>0:attention.includes(stockFilter))
    const needle=designQuery.trim().normalize('NFKC').toLocaleLowerCase()
    return match&&(!selected||!needle||`${product.name} ${product.design_name||''} ${product.sku}`.normalize('NFKC').toLocaleLowerCase().includes(needle))
  }
  const visibleSeries=data?.series.filter(series=>stockFilter==='all'||series.products.some(matchesStock))||[]

  return <main className="pc-series-inventory">
    <header className="pc-series-header">
      {selectedId&&<Link className="pc-series-back" to="/stock"><ArrowLeftOutlined />All series</Link>}
      <div className="pc-series-titlebar">
        <div><Typography.Title level={2}>{selected?.name || 'Inventory by series'}</Typography.Title>
          <p>{storeName}{selected&&<> <span aria-hidden="true">/</span> {data?.locations.length} stock locations</>}</p>
        </div>
        <div className="pc-series-toolbar">
          <Button className="pc-series-refresh" aria-label="Refresh inventory" title="Refresh inventory" icon={<ReloadOutlined />} disabled={locked} onClick={()=>setRefresh(value=>value+1)} />
          {selected&&<Button aria-label="Show movement history" icon={<HistoryOutlined />} disabled={locked} onClick={()=>setHistoryOpen(true)}>History</Button>}
          {selected&&canWork?<Button disabled={locked} onClick={()=>{setPanel('identify');setSaved(undefined)}}>Identify boxes</Button>:manager&&<Button type="primary" disabled={locked} onClick={openSetup}>Set up series</Button>}
        </div>
      </div>
    </header>
    {storeCode==='ALL'&&<Alert type="info" showIcon message="Viewing all authorized stores. Select one store to receive, count, or identify stock." />}
    {saved&&<Alert type="success" showIcon message={saved==='setup'?'Series saved. No stock quantities were added.':`${saved==='stock'?'Stock change':'Identification'} saved. ${data?'Inventory refreshed.':current?.error?'Inventory refresh needed.':'Refreshing inventory.'}`} />}
    {!panel&&mutation.notice}
    {!current?<Skeleton active paragraph={{rows:5}}/>:current.error?<Alert type="error" showIcon message={current.error} action={<Button disabled={locked} onClick={()=>setRefresh(value=>value+1)}>Retry inventory</Button>}/>:
      data&&<>
        {data.mode!=='authoritative'&&<Alert type="warning" showIcon message="Inventory opening has not been activated. Quantities remain unknown until reviewed."/>}
        <div className="pc-series-filterbar"><label>Stock view<select aria-label="Inventory status" value={stockFilter} onChange={event=>setStockFilter(event.target.value as typeof stockFilter)} disabled={locked}>
          <option value="all">All stock</option><option value="attention">Needs attention</option><option value="out_of_stock">Out of stock</option><option value="replenish">Replenish floor</option><option value="unverified">Unverified stock</option><option value="condition">On hold / damaged</option>
        </select></label>{selected&&<Input aria-label="Find design in this series" placeholder="Find a design" value={designQuery} onChange={event=>setDesignQuery(event.target.value)} disabled={locked} allowClear/>}</div>
        {rawId&&!selectedId?<Alert type="error" message="Invalid series reference."/>:selectedId&&!selected?<Alert type="warning" message="This series is unavailable in the selected scope."/>:selected?<>
          <section className="pc-series-section" aria-labelledby="designs-heading">
            <div className="pc-series-section-heading"><div><h3 id="designs-heading">Confirmed designs</h3><p>Available stock by location. Each design is stocked separately.</p></div><div className="pc-series-batch"><span className="pc-series-count">{designs.length} designs</span>{canWork&&<><Button disabled={locked||data.mode!=='authoritative'||!designs.length} onClick={()=>setWorksheet('count')}>Count series</Button><Button type="primary" disabled={locked||data.mode!=='authoritative'||!designs.length} onClick={()=>setWorksheet('receive')}>Receive series</Button></>}</div></div>
            <StockTable products={designs.filter(matchesStock)} data={data} canWork={canWork&&!locked} onDetail={setDetailId} label="Confirmed design inventory" />
          </section>
          {(['sealed_set','random_box','ordinary',null] as const).map(form=>{
            const products=selected.products.filter(product=>product.stock_form===form&&matchesStock(product))
            if(!products.length)return null
            return <section className="pc-series-section pc-series-packaging" key={form||'review'}>
              <div className="pc-series-section-heading"><h3>{form===null?'Identity needs review':({sealed_set:'Sealed sets',random_box:'Unidentified boxes',ordinary:'Other products'})[form]}</h3><span>{form==='sealed_set'?'Unopened sets':form==='random_box'?'Design not yet confirmed':''}</span></div>
              <StockTable products={products} data={data} canWork={canWork&&!locked&&form!==null} onDetail={setDetailId} label={form===null?'Unreviewed inventory':({sealed_set:'Sealed set inventory',random_box:'Unidentified box inventory',ordinary:'Other product inventory'})[form]} />
            </section>
          })}
        </>:!rawId&&<>
          <div className="pc-series-search"><Input.Search aria-label="Search series or design" placeholder="Search series, design, or SKU" value={query} onChange={event=>setQuery(event.target.value)} onSearch={value=>setSearch(value.trim())} disabled={locked} allowClear /><span>{visibleSeries.length} series</span></div>
          {!visibleSeries.length?<p className="pc-series-empty">No series match this search and stock view.</p>:<ul className="pc-series-list">{visibleSeries.map(series=><li key={series.id}><Link to={`/stock?series_id=${series.id}`}><span className="pc-series-list-name">{series.name}<span>{series.products.length} products{series.products.some(product=>inventoryAttention(product,data.locations).length>0)&&<> · {series.products.filter(product=>inventoryAttention(product,data.locations).length>0).length} need attention</>}</span></span><span className="pc-series-list-count">{series.products.filter(product=>product.stock_form==='confirmed_design').length} named designs</span><ArrowRightOutlined aria-hidden /></Link></li>)}</ul>}
        </>}
        <footer className="pc-series-footer">
          {selected&&manager&&<Button disabled={locked} onClick={openSetup}>Set up series</Button>}
          {data.unassigned_count>0&&<Link to="/stock/products">{data.unassigned_count} products need series assignment</Link>}
          <nav aria-label="Inventory tools" className="pc-inventory-tools"><Link to="/products">Products</Link><Link to="/restock">Restock</Link><Link to="/trades">Trades</Link><Link to="/stock/products">All products</Link></nav>
        </footer>
      </>}
    <Drawer rootClassName="pc-inventory-drawer" title="Set up a named design roster" width="min(100%, 520px)" open={panel==='setup'} onClose={()=>{if(!locked)setPanel(undefined)}} keyboard={!locked} maskClosable={!locked} closable={!locked} destroyOnClose
      footer={<Space wrap><Button type="primary" disabled={locked||!setupReady} loading={mutation.saving} onClick={setup}>Save named designs</Button><Button disabled={locked} onClick={()=>setPanel(undefined)}>Cancel setup</Button></Space>}>
      {panel==='setup'&&<div className="pc-series-form">{mutation.notice}
      <p>Enter the actual design names, one per line. The 6, 9, and 12 row presets only size this form; they do not set pack size or add stock. Extra named designs are welcome.</p>
      {catalogError&&<Alert type="error" message={catalogError} action={<Button onClick={()=>setRefresh(value=>value+1)}>Retry series list</Button>} />}
      <fieldset disabled={locked||!catalog}>
        <label>Series to update<select aria-label="Series to update" value={setupSeries} onChange={event=>setSetupSeries(event.target.value)}><option value="new">Create a new series</option>{catalog?.map(series=><option key={series.id} value={series.id}>{series.name}</option>)}</select></label>
        {setupSeries==='new'&&<label>New series name<input aria-label="New series name" value={seriesName} onChange={event=>setSeriesName(event.target.value)} maxLength={200}/></label>}
        <div className="pc-series-presets">Name-entry rows: {[6,9,12].map(count=><button type="button" key={count} aria-pressed={nameRows===count} onClick={()=>setNameRows(count)}>{count}</button>)}</div>
        <label>Design names<textarea aria-label="Design names" rows={nameRows} value={designNames} placeholder="One confirmed design name per line" onChange={event=>setDesignNames(event.target.value)}/></label>
        <p>{names.length} named designs to add. {duplicateNames?'Remove duplicate design names before saving.':''}</p>
      </fieldset>
      </div>}
    </Drawer>
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
      {detail&&data&&<div className="pc-series-stock-details"><InventoryPhoto key={detail.image_filename} filename={detail.image_filename} name={detail.design_name||detail.name} large/><h2>{detail.design_name||detail.name}</h2><p>{detail.sku} · {detail.identity_status==='verified'?'Reviewed identity':'Identity needs review'}</p>
        {canWork&&data.mode==='authoritative'&&detail.identity_status==='verified'&&<Space wrap><Button onClick={()=>openStockAction('move',detail.id)}>Move stock</Button>{detail.stock_form==='sealed_set'&&<Button type="primary" onClick={()=>openStockAction('open_set',detail.id)}>Open sealed set</Button>}</Space>}
        {data.locations.map(location=><section key={location.id}><h3>{location.store_code} · {location.name}</h3>{!location.opening_verified&&<p className="pc-series-warning">Opening count needs review</p>}<dl>{detail.balances.filter(balance=>balance.location_id===location.id).map(balance=><div key={balance.disposition}><dt>{dispositionName(balance.disposition)}:</dt> <dd>{balance.quantity===null?'Unknown':units(balance.quantity,detail.stock_unit)}</dd></div>)}</dl></section>)}
      </div>}
    </Drawer>
  </main>
}

function StockTable({products,data,canWork,onDetail,label}:{products:SeriesProduct[];data:SeriesInventoryResult;canWork:boolean;onDetail:(id:number)=>void;label:string}) {
  const multipleStores = new Set(data.locations.map(item=>item.store_code)).size>1
  if(!products.length)return <p className="pc-series-empty">No products match this stock view. Try All stock or clear the search.</p>
  return <div className="pc-series-table-wrap"><table className="pc-series-table" aria-label={label}>
    <thead><tr><th scope="col">Design / product</th>{data.locations.map(location=><th scope="col" key={location.id} className="pc-series-location">{multipleStores&&<span>{location.store_code} · </span>}{location.name}</th>)}<th scope="col" className="pc-series-available">Available</th>{canWork&&<th scope="col" className="pc-series-actions-heading"><span className="pc-series-sr-only">Actions</span></th>}</tr></thead>
    <tbody>{products.map(product=>{
      const balances=product.balances.filter(balance=>balance.disposition==='saleable')
      const known=balances.length>0&&balances.every(balance=>balance.quantity!==null)
      const total=balances.reduce((sum,balance)=>sum+(balance.quantity||0),0)
      const name=product.design_name||product.name
      const exceptions=product.balances.filter(balance=>balance.disposition!=='saleable'&&balance.quantity!==null&&balance.quantity>0)
      return <tr className="pc-series-product" key={product.id}>
        <th scope="row" className="pc-series-product-name"><button type="button" aria-label={`Stock details for ${name}`} onClick={()=>onDetail(product.id)}><InventoryPhoto key={product.image_filename} filename={product.image_filename} name={name}/><span>{name}</span></button>
          <div className="pc-series-mobile-locations">{data.locations.map(location=><span key={location.id}>{multipleStores?`${location.store_code} · `:''}{location.name} <b>{saleable(product,location.id)?.quantity??'Unknown'}</b></span>)}</div>
          {inventoryAttention(product,data.locations).includes('replenish')&&<span className="pc-series-warning">Replenish floor · back stock available</span>}
          {product.identity_status!=='verified'&&<span className="pc-series-warning">Identity needs review</span>}
          {exceptions.length>0&&<span className="pc-series-exceptions">{exceptions.map(balance=>{const location=data.locations.find(item=>item.id===balance.location_id);return <span key={`${balance.location_id}|${balance.disposition}`}>{dispositionName(balance.disposition)}: {units(balance.quantity!,product.stock_unit)} · {multipleStores?`${location?.store_code} · `:''}{location?.name}</span>})}</span>}
        </th>
        {data.locations.map(location=><td key={location.id} className="pc-series-location">{saleable(product,location.id)?.quantity??<span className="pc-series-unknown">Unknown</span>}</td>)}
        <td className={`pc-series-available ${known&&total===0?'pc-series-zero':''}`}><strong>{known?units(total,product.stock_unit):'Unknown'}</strong>{known&&total===0&&<span>Out of stock</span>}{!known&&<span>Not yet verified</span>}</td>
        {canWork&&<td className="pc-series-product-actions">{product.identity_status==='verified'&&product.stock_unit&&<><Link aria-label={`Receive ${name}`} to={`/goods/receiving?product_id=${product.id}`}>Receive</Link><Link aria-label={`Count ${name}`} to={`/goods/counts?product_id=${product.id}`}>Count</Link></>}</td>}
      </tr>
    })}</tbody>
  </table></div>
}
