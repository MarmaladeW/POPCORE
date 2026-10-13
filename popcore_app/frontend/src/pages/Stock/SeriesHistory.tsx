import { Alert, Button, Drawer, Skeleton } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { useAuth0 } from '@auth0/auth0-react'
import { useRole } from '../../auth/useRole'
import { getInventoryDocument, getSeriesHistory, type InventoryDocument, type InventorySeries, type SeriesHistoryFilters, type SeriesHistoryPage, type SeriesMovement } from '../../api/inventorySeries'
import './seriesHistory.css'

const failure = (cause:unknown) => (cause as {_serverMessage?:string})?._serverMessage || 'Unable to load this record. Please retry.'
const label = (value:string) => value.replaceAll('_',' ')
const quantity = (value:number, unit:string|null|undefined) => `${value} ${unit==='box' ? (Math.abs(value)===1?'box':'boxes') : `${unit||'unit'}${Math.abs(value)===1?'':'s'}`}`
type Props = {series:InventorySeries;storeCode:string;onClose:()=>void}

export default function SeriesHistory({series,storeCode,onClose}:Props) {
  const {user} = useAuth0(), role = useRole()
  return <Drawer rootClassName="pc-inventory-drawer pc-history-drawer" title="Movement history" width="min(100%, 640px)" open onClose={onClose}>
    <HistoryBody key={`${series.id}|${storeCode}|${user?.sub}|${role}`} series={series} storeCode={storeCode}/>
  </Drawer>
}

function HistoryBody({series,storeCode}:Omit<Props,'onClose'>) {
  const [product,setProduct] = useState(''), [dateFrom,setDateFrom] = useState(''), [dateTo,setDateTo] = useState('')
  const [filters,setFilters] = useState<SeriesHistoryFilters>({}), [filterRun,setFilterRun] = useState(0)
  const [documentId,setDocumentId] = useState<number>()
  const [movements,setMovements] = useState<SeriesMovement[]>([])
  const invalidRange = !!dateFrom && !!dateTo && dateFrom>dateTo
  return <div className="pc-history">
    <div hidden={documentId!==undefined}>
      <p className="pc-series-drawer-intro">{series.name} · {storeCode}<br/>Posted stock movements, newest first. Up to 100 per page. Dates use the recorded business date.</p>
      <form className="pc-history-filters" onSubmit={event=>{event.preventDefault();if(!invalidRange){setFilters({product_id:product?Number(product):undefined,date_from:dateFrom||undefined,date_to:dateTo||undefined});setFilterRun(value=>value+1)}}}>
        <label className="pc-history-product">Product<select aria-label="Product" value={product} onChange={event=>setProduct(event.target.value)}><option value="">All products in this series</option>{series.products.map(item=><option key={item.id} value={item.id}>{item.name} · {label(item.stock_form||'unreviewed')}</option>)}</select></label>
        <label>From date<input type="date" value={dateFrom} onChange={event=>setDateFrom(event.target.value)} /></label>
        <label>Through date<input type="date" value={dateTo} onChange={event=>setDateTo(event.target.value)} /></label>
        <Button htmlType="submit" disabled={invalidRange}>Apply filters</Button>
        {invalidRange&&<p role="alert" className="pc-history-range-error">From date must be on or before through date.</p>}
      </form>
      <HistoryFeed key={filterRun} series={series} storeCode={storeCode} filters={filters} onDocument={setDocumentId} onMovements={setMovements}/>
    </div>
    {documentId!==undefined&&<DocumentDetail key={documentId} id={documentId} series={series} movements={movements} onDocument={setDocumentId} onBack={()=>setDocumentId(undefined)}/>}
  </div>
}

function HistoryFeed({series,storeCode,filters,onDocument,onMovements}:{series:InventorySeries;storeCode:string;filters:SeriesHistoryFilters;onDocument:(id:number)=>void;onMovements:(items:SeriesMovement[])=>void}) {
  const [page,setPage] = useState<SeriesHistoryPage>(), [cursor,setCursor] = useState<number>()
  const [loading,setLoading] = useState(true), [error,setError] = useState(''), [retry,setRetry] = useState(0)
  useEffect(()=>{
    const controller = new AbortController()
    setLoading(true);setError('')
    getSeriesHistory(series.id,storeCode,controller.signal,{...filters,before_id:cursor})
      .then(result=>{if(!controller.signal.aborted)setPage(previous=>({...result,items:cursor?[...(previous?.items||[]),...result.items]:result.items}))})
      .catch(cause=>{if(!controller.signal.aborted)setError(failure(cause))})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false)})
    return ()=>controller.abort()
  },[series.id,storeCode,filters,cursor,retry])
  useEffect(()=>{onMovements(page?.items||[])},[page,onMovements])
  return <section aria-label="Posted movements" aria-busy={loading}>
    {loading&&!page&&<Skeleton active paragraph={{rows:3}}/>}
    {page?.items.length===0&&!loading&&<p>No posted movements match these filters.</p>}
    {!!page?.items.length&&<ol className="pc-history-movements">{page.items.map(item=><li key={item.id}>
      <div className="pc-history-movement-title"><strong>{item.product_name}</strong><strong>{item.quantity>0?'+':''}{quantity(item.quantity,item.native_unit===undefined?series.products.find(product=>product.id===item.product_id)?.stock_unit:item.native_unit)}</strong></div>
      <span>{item.store_code||storeCode} · {item.location_name} · {label(item.disposition)}</span>
      <span>{item.business_date} · {label(item.kind)} · <button type="button" className="pc-history-link" onClick={()=>onDocument(item.document_id)}>Document #{item.document_id}</button></span>
      <span>Recorded by {item.actor_sub||'Not recorded'}</span>
      {item.open_set_id!=null&&<span>Opened set #{item.open_set_id}</span>}
      {item.reason&&<span>{item.reason}</span>}
    </li>)}</ol>}
    {error&&<Alert type="error" message={error} action={<Button onClick={()=>setRetry(value=>value+1)}>Retry history</Button>}/>}
    {page?.has_more&&page.next_before_id!=null&&!error&&<Button loading={loading} onClick={()=>setCursor(page.next_before_id!)}>Load older</Button>}
  </section>
}

function DocumentDetail({id,series,movements,onDocument,onBack}:{id:number;series:InventorySeries;movements:SeriesMovement[];onDocument:(id:number)=>void;onBack:()=>void}) {
  const [document,setDocument] = useState<InventoryDocument>(), [error,setError] = useState(''), [retry,setRetry] = useState(0)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(()=>{
    const controller = new AbortController()
    setError('');setDocument(undefined);heading.current?.focus()
    getInventoryDocument(id,controller.signal).then(result=>{if(!controller.signal.aborted)setDocument(result)}).catch(cause=>{if(!controller.signal.aborted)setError(failure(cause))})
    return ()=>controller.abort()
  },[id,retry])
  const locationName = (locationId:number|null) => {
    const movement = movements.find(item=>item.location_id===locationId)
    return movement ? `${movement.store_code?movement.store_code+' · ':''}${movement.location_name}` : `Location #${locationId}`
  }
  return <section className="pc-history-document">
    <Button onClick={onBack}>Back to movements</Button>
    <h3 ref={heading} tabIndex={-1}>Document #{id}</h3>
    {error?<Alert type="error" message={error} action={<Button onClick={()=>setRetry(value=>value+1)}>Retry document</Button>}/>:!document?<Skeleton active paragraph={{rows:4}}/>:<>
      <dl>
        <dt>Action</dt><dd>{label(document.kind)}</dd>
        <dt>Business date</dt><dd>{document.business_date}</dd>
        <dt>Posted at</dt><dd>{document.posted_at}</dd>
        <dt>Recorded by</dt><dd>{document.actor_sub}</dd>
        <dt>Reason</dt><dd>{document.reason||'Not recorded'}</dd>
        <dt>Source</dt><dd>{document.source_type?`${label(document.source_type)}${document.source_id ? ' · '+document.source_id : ''}`:'Not recorded'}</dd>
      </dl>
      <h4>Document lines</h4>
      <ol className="pc-history-lines">{document.lines.map(line=><li key={line.line_no}>
        <strong>{series.products.find(product=>product.id===line.product_id)?.name || `Product #${line.product_id}`}</strong>
        <span>{quantity(line.quantity,line.native_unit)}</span>
        {line.from_location_id!=null&&<span>From {locationName(line.from_location_id)} · {label(line.from_disposition||'')}</span>}
        {line.to_location_id!=null&&<span>To {locationName(line.to_location_id)} · {label(line.to_disposition||'')}</span>}
        {line.open_set_id!=null&&<span>Opened set #{line.open_set_id}</span>}
        {line.conversion_id!=null&&<span>Conversion #{line.conversion_id} · factor {line.conversion_factor}</span>}
      </li>)}</ol>
      {(document.correction_of!=null||document.corrections.length>0)&&<div className="pc-history-corrections"><h4>Corrections</h4>
        {document.correction_of!=null&&<p>Corrects <button type="button" className="pc-history-link" onClick={()=>onDocument(document.correction_of!)}>Document #{document.correction_of}</button></p>}
        {document.corrections.map(correction=><p key={correction}>Corrected by <button type="button" className="pc-history-link" onClick={()=>onDocument(correction)}>Document #{correction}</button></p>)}
      </div>}
    </>}
  </section>
}
