import { Alert, AutoComplete, Button, Drawer, Skeleton } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { useAuth0 } from '@auth0/auth0-react'
import { Link, useSearchParams } from 'react-router-dom'
import dayjs from 'dayjs'
import client from '../../api/client'
import { getHistoricalMatches, type HistoricalMatch, type HistoricalMatches, type HistoryCandidate, type ProductReference } from '../../api/storeInsights'
import { useRole } from '../../auth/useRole'
import { useAppStore } from '../../store'
import { productLabel } from '../../lib/productLabel'
import { torontoDate } from '../Dashboard/todayPresentation'
import useCheckoutMutation from './useCheckoutMutation'
import './historicalMatchReview.css'

const failure=(cause:unknown)=>(cause as {_serverMessage?:string})?._serverMessage||'Unable to load historical name matches.'
const snapshotPrice=(price:number|null)=>price==null?'Not recorded':`CA$${price.toFixed(2)}`

export default function HistoricalMatchReview() {
  const store=useAppStore(state=>state.selectedStore),{user}=useAuth0(),role=useRole()
  if(!store)return <Alert type="info" message="Choose a store to review historical sales names."/>
  return <ReviewScope key={`${store.code}|${user?.sub}|${role}`} storeCode={store.code} storeName={store.name}/>
}
function ReviewScope({storeCode,storeName}:{storeCode:string;storeName:string}) {
  const [params,setParams]=useSearchParams()
  const to=params.get('to')||torontoDate(),from=params.get('from')||dayjs(to).subtract(29,'day').format('YYYY-MM-DD')
  const requestedPage=Number(params.get('page')||1),page=Number.isSafeInteger(requestedPage)&&requestedPage>0?requestedPage:1,query=params.get('q')||''
  const [search,setSearch]=useState(query),[result,setResult]=useState<HistoricalMatches>(),[error,setError]=useState(''),[retry,setRetry]=useState(0)
  const [selected,setSelected]=useState<HistoricalMatch>(),[saved,setSaved]=useState(false)
  useEffect(()=>setSearch(query),[query])
  const rangeError=from>to?'From date must be on or before through date.':''
  useEffect(()=>{
    setResult(undefined);setError('');setSelected(undefined)
    if(rangeError)return
    const controller=new AbortController()
    getHistoricalMatches({store_code:storeCode,date_from:from,date_to:to,page,page_size:25,q:query},controller.signal)
      .then(data=>{if(!controller.signal.aborted)setResult(data)})
      .catch(cause=>{if(!controller.signal.aborted)setError(failure(cause))})
    return()=>controller.abort()
  },[storeCode,from,to,page,query,retry,rangeError])
  const update=(changes:Record<string,string>)=>{const next=new URLSearchParams(params);Object.entries(changes).forEach(([key,value])=>value?next.set(key,value):next.delete(key));setParams(next)}
  return <main className="pc-match-review">
    <header><Link to={`/reports?${new URLSearchParams({from,to})}`}>Insights &amp; reports</Link><h1>Review past sales names</h1><p>{storeName} · Match saved daily-report names to exact products.</p></header>
    <p className="pc-match-intro">Suggestions need your review. Corrections change the product linked to a whole saved row; original names, quantities and saved prices stay intact.</p>
    <form className="pc-match-filters" onSubmit={event=>{event.preventDefault();update({q:search.trim(),page:'1'})}}>
      <label>From date<input type="date" value={from} onChange={event=>update({from:event.target.value,page:'1'})}/></label>
      <label>Through date<input type="date" value={to} onChange={event=>update({to:event.target.value,page:'1'})}/></label>
      <label className="pc-match-search">Find a name or product<input type="search" value={search} placeholder="Original name, product or SKU" onChange={event=>setSearch(event.target.value)}/></label>
      <Button htmlType="submit">Search records</Button><Button onClick={()=>setRetry(value=>value+1)}>Refresh records</Button>
    </form>
    {storeCode==='ALL'&&<Alert type="info" message="All authorized stores are shown. Select one store to save a correction."/>}
    {saved&&<Alert type="success" message="Product match corrected and audit saved. Stock and quantities were unchanged."/>}
    {rangeError||error?<Alert type="error" message={rangeError||error} action={!rangeError&&<Button onClick={()=>setRetry(value=>value+1)}>Retry records</Button>}/>:!result?<Skeleton active paragraph={{rows:5}}/>:<>
      <div className="pc-match-count">{result.total_rows} saved rows · newest first</div>
      {!result.items.length?<div className="pc-match-empty"><h2>No saved rows in this period</h2><p>Choose another date range or open Daily Sales to review a pasted report.</p><Link to={`/sales?date=${to}`}>Open Daily Sales</Link></div>:<ul className="pc-match-rows">{result.items.map(row=><li key={row.id}>
        <div><span className="pc-match-date">{row.date} · {row.store} · {row.qty_sold} reported</span><h2>{row.raw_name||'Original name not recorded'}</h2>{row.notes&&<p className="pc-match-note">{row.notes}</p>}<p>Saved as <strong>{productLabel(row.current_product)}</strong><span className="pc-match-sku">{row.current_product.sku}</span></p></div>
        <div className="pc-match-row-action"><span>{row.match_label||row.match_status.replaceAll('_',' ')}</span>{row.blocked_reason&&<small>Separate reconciliation required</small>}<Button onClick={()=>{setSelected(row);setSaved(false)}}>Review row</Button></div>
      </li>)}</ul>}
      <nav className="pc-match-pagination" aria-label="Historical match pages"><Button disabled={page<=1} onClick={()=>update({page:String(page-1)})}>Previous</Button><span>Page {page}</span><Button disabled={page*result.page_size>=result.total_rows} onClick={()=>update({page:String(page+1)})}>Next</Button></nav>
    </>}
    {selected&&<ReviewRow key={selected.id} row={selected} canSave={storeCode!=='ALL'} onClose={()=>setSelected(undefined)} onSaved={()=>{setSelected(undefined);setSaved(true);setRetry(value=>value+1)}}/>}
  </main>
}
function ReviewRow({row,canSave,onClose,onSaved}:{row:HistoricalMatch;canSave:boolean;onClose:()=>void;onSaved:()=>void}) {
  const [candidates,setCandidates]=useState(row.candidates),[choice,setChoice]=useState<HistoryCandidate>(),[reason,setReason]=useState(''),[confirmed,setConfirmed]=useState(false)
  const [search,setSearch]=useState(''),[options,setOptions]=useState<Array<{value:string;label:string;product:ProductReference}>>([]),[searchError,setSearchError]=useState(''),[searching,setSearching]=useState(false),[denied,setDenied]=useState(false)
  const controller=useRef<AbortController>(),feedback=useRef<HTMLDivElement>(null)
  const mutation=useCheckoutMutation(onSaved),locked=mutation.pending||mutation.saving
  useEffect(()=>()=>controller.current?.abort(),[])
  useEffect(()=>{if(mutation.error)feedback.current?.focus()},[mutation.error])
  useEffect(()=>{const reject=()=>setDenied(true);window.addEventListener('popcore:checkout-access-denied',reject);return()=>window.removeEventListener('popcore:checkout-access-denied',reject)},[])
  useEffect(()=>{window.dispatchEvent(new CustomEvent('popcore:checkout-busy',{detail:locked}));return()=>{window.dispatchEvent(new CustomEvent('popcore:checkout-busy',{detail:false}))}},[locked])
  async function searchProducts(value:string) {
    setSearch(value);setChoice(undefined);setConfirmed(false);setOptions([]);setSearchError('');controller.current?.abort()
    if(!value.trim()){setSearching(false);return}
    const request=new AbortController();controller.current=request;setSearching(true)
    try {const response=await client.get<ProductReference[]>('/products/search',{params:{q:value,limit:8},signal:request.signal});if(!request.signal.aborted)setOptions(response.data.map(product=>({value:String(product.id),label:`${productLabel(product)} · ${product.sku}`,product})))}
    catch(cause){if(!request.signal.aborted)setSearchError(failure(cause))}
    finally{if(!request.signal.aborted)setSearching(false)}
  }
  async function chooseSearch(value:string) {
    controller.current?.abort();const request=new AbortController();controller.current=request;setChoice(undefined);setConfirmed(false);setSearching(true);setSearchError('');setOptions([])
    try {
      const data=await getHistoricalMatches({store_code:row.store,record_id:row.id,target_product_id:Number(value),date_from:row.date,date_to:row.date},request.signal)
      if(request.signal.aborted)return
      const current=data.items.find(item=>item.id===row.id),target=current?.candidates.find(product=>product.id===Number(value))
      if(current?.row_token!==row.row_token){setSearchError('This saved row changed. Close this review and refresh records.');return}
      if(!target){setSearchError('This product is unavailable for review. Choose another product or refresh records.');return}
      setCandidates(items=>[target,...items.filter(product=>product.id!==target.id)]);setChoice(target);setConfirmed(false);setSearch(productLabel(target))
    }catch(cause){if(!request.signal.aborted)setSearchError(failure(cause))}finally{if(!request.signal.aborted)setSearching(false)}
  }
  const ready=canSave&&!row.blocked_reason&&!denied&&!locked&&!searching&&choice&&choice.id!==row.current_product.id&&reason.trim()&&confirmed
  function save(){if(!ready||!choice)return;void mutation.run(`/sales/record/${row.id}/remap`,{store_code:row.store,product_id:choice.id,row_token:row.row_token,target_identity_token:choice.target_identity_token,reason:reason.trim()})}
  return <Drawer rootClassName="pc-inventory-drawer pc-match-drawer" title="Review historical product match" width="min(100%, 680px)" open onClose={()=>{if(!locked)onClose()}} keyboard={!locked} maskClosable={!locked} closable={!locked}
    footer={<div className="pc-match-footer"><Button type="primary" aria-label="Save product correction" disabled={!ready} loading={mutation.saving} onClick={save}>Save product correction</Button><Button disabled={locked} onClick={onClose}>Cancel</Button></div>}>
    <div ref={feedback} tabIndex={-1}>{mutation.notice}</div>
    <div className="pc-match-source"><p>{row.date} · {row.store}</p><h2>{row.raw_name||'Original name not recorded'}</h2>{row.notes&&<p>{row.notes}</p>}<dl><dt>Currently linked</dt><dd>{productLabel(row.current_product)} · {row.current_product.sku}</dd><dt>Reported quantities</dt><dd>POS {row.qty_pos} · Non-POS {row.qty_cash} · Claw {row.qty_claw} · Display {row.qty_display} · Employee {row.qty_employee}</dd><dt>Saved unit price</dt><dd>{snapshotPrice(row.unit_price)}</dd></dl></div>
    {row.last_review&&<p className="pc-match-last-review">Last corrected {row.last_review.created_at} by {row.last_review.actor_sub}: {row.last_review.reason}</p>}
    {row.blocked_reason?<Alert type="warning" showIcon message="This record needs separate reconciliation" description={row.blocked_reason}/>:<>
      {!canSave&&<Alert type="info" message="Choose one store before correcting this row."/>}
      {denied&&<Alert type="error" message="Access was denied. Close this review and refresh your store access."/>}
      <fieldset disabled={locked||denied||!canSave}><legend>Choose the exact product</legend><p>Suggestions are based on names. Check the series, design, packaging and SKU; no suggestion is preselected.</p>
        <label className="pc-match-product-search">Search another product<AutoComplete aria-label="Search another product" value={search} options={options} onSearch={searchProducts} onSelect={chooseSearch} disabled={locked||denied||!canSave} placeholder="Search by name or SKU"/></label>
        {searching&&<p role="status">Checking product…</p>}{searchError&&<Alert type="error" message={searchError}/>}
        <div className="pc-match-candidates">{candidates.map(product=><label key={product.id}><input type="radio" name="historical-product" value={product.id} checked={choice?.id===product.id} disabled={product.id===row.current_product.id||searching} onChange={()=>{setChoice(product);setConfirmed(false)}}/><span><strong>{productLabel(product)}</strong><small>{product.sku} · {product.stock_unit||'Unit not reviewed'} · {product.reason}{product.id===row.current_product.id?' · Current product':''}</small></span></label>)}</div>
        {!candidates.length&&<p>No suggestions. Search the catalog by its exact name or SKU.</p>}
        <label className="pc-match-reason">Reason for correction<textarea value={reason} maxLength={1000} rows={3} onChange={event=>setReason(event.target.value)}/></label>
        <label className="pc-match-confirm"><input type="checkbox" checked={confirmed} onChange={event=>setConfirmed(event.target.checked)}/><span>I checked the original record. This entire saved row, including every listed channel, belongs to the selected product.</span></label>
      </fieldset>
    </>}
    <p className="pc-match-limit">Stock, payments and saved quantities are unchanged. This correction is audited; it does not create a general name alias.</p>
  </Drawer>
}
