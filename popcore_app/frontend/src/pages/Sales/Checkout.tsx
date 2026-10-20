import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Skeleton, Switch } from 'antd'
import { CameraOutlined, ReloadOutlined } from '@ant-design/icons'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import client from '../../api/client'
import { useAppStore } from '../../store'
import { formatCents } from '../../lib/money'
import { useRole } from '../../auth/useRole'
import ManualCheckout from './ManualCheckout'
import CloverOrder from './CloverOrder'
import ManualPayment from './ManualPayment'
import useCheckoutMutation from './useCheckoutMutation'
import { autoOpenCandidate, groupLines } from './cloverOrderView'
import { cnyCharged, historyQuery, missingPhotos, paymentSummary, TENDER_FILTERS } from './checkoutHistory'
import { electronic, errorMessage, label, OrderNotes, PhotoCapture, RefundTools, TakeBack, type Access, type Draft, type DraftUpdate, type Order, type Queue, type Store } from './checkoutParts'
import './Checkout.css'

const AUTO_OPEN_KEY = 'popcore.checkout.cloverAutoOpen'
const readAutoOpen = () => { try { return localStorage.getItem(AUTO_OPEN_KEY) !== '0' } catch { return true } }
const clock = (ms:number) => new Date(ms).toLocaleTimeString('en-CA',{hour:'numeric',minute:'2-digit',timeZone:'America/Toronto'})
const checkoutLink = (path:string,history=false) => history?`${path}?view=history`:path
// A finished order still waiting for its payment photo keeps the screen busy.
const needsPhoto = (order:Order) => order.attempts.some(a=>a.status==='completed'&&electronic(a.tender)&&a.can_upload&&!a.photos.length)
const initialDraft = (_order:Order):Draft => ({target:'',tender:'',split:'',includesCard:false})

/** A manual order (a phone order, an item from another store): paid the same way as a Clover order. */
function CurrentOrder({order,draft,setDraft,updated,refresh,onBusy,cnyPerCad,cnyRateSource}:{order:Order;draft:Draft;setDraft:(v:DraftUpdate)=>void;updated:(o?:Order)=>void;refresh:()=>void;onBusy:(v:boolean)=>void;cnyPerCad:string;cnyRateSource?:string|null}) {
  const mutation=useCheckoutMutation((data:Order)=>updated(data))
  const [photoStates,setPhotoStates]=useState<Record<number,boolean>>({})
  const [refundBusy,setRefundBusy]=useState(false),[noteBusy,setNoteBusy]=useState(false)
  const blocked=mutation.pending||refundBusy||noteBusy||Object.values(photoStates).some(Boolean)
  useEffect(()=>{onBusy(blocked);return()=>onBusy(false)},[blocked])
  const run=(name:string,body:object)=>mutation.run(`/checkouts/${order.id}/${name}`,{expected_version:order.version,...body})
  const pricing=order.pricing
  const photoAttempts=order.attempts.filter(a=>a.status!=='cancelled'&&electronic(a.tender))
  const payments=order.attempts.filter(a=>a.status==='completed')
  return <article className="co-order" aria-label={`Checkout ${order.reference}`}>
    <header className="co-order-heading"><div><span className="co-muted">{order.register_name||'POPCORE order'} · {order.reference}</span><h2>{order.status==='cancelled'?'Checkout cancelled':order.status==='completed'?'Checkout complete':'Current order'}</h2></div><span className={'co-status co-status-'+order.status}>{order.abandoned_reason&&order.status==='open'?'Refund due':order.status==='open'?'In progress':order.status}</span></header>
    <dl className="co-facts"><div><dt>Cashier</dt><dd>{order.cashier_name}</dd></div><div><dt>Order date</dt><dd>{order.business_date}</dd></div></dl>
    <ul className="co-items">{order.order.lines.map((line,i)=><li key={i}><span className="co-quantity">{line.quantity}×</span><span className="co-line-name">{line.product_name_snapshot}</span></li>)}</ul>
    <dl className="co-original"><div><dt>Subtotal</dt><dd>{formatCents(order.order.subtotal_cents)}</dd></div><div><dt>Tax</dt><dd>{formatCents(order.order.source_tax_cents)}</dd></div>{order.status!=='completed'&&order.order.reduction_cents>0&&<div><dt>Agreed discount</dt><dd>−{formatCents(order.order.reduction_cents)}</dd></div>}{order.status==='completed'?<div><dt>Customer paid</dt><dd>{formatCents(order.order.collected_cents)}</dd></div>:<div><dt>Order total</dt><dd>{formatCents(pricing?.total_cents??order.order.collected_cents)}</dd></div>}</dl>
    <OrderNotes order={order} updated={updated} disabled={blocked} onBusy={setNoteBusy}/>
    {mutation.notice}
    {pricing?.can_receive&&pricing.collect&&<ManualPayment order={order} draft={draft} setDraft={setDraft} busy={blocked} saving={mutation.saving} run={run} cnyPerCad={cnyPerCad} cnyRateSource={cnyRateSource}/>}
    {order.abandoned_reason&&<Alert type="warning" message={`${formatCents(order.refund_due_cents)} still to refund`} description={order.abandoned_reason}/>}
    {!order.can_process&&order.status==='open'&&!order.abandoned_reason&&<p className="co-muted">View only. Recording payments needs the cashier’s account and an assigned shift at this location today.</p>}
    {!!payments.length&&<section className="co-receipts"><h3>Payments</h3>{payments.map(a=><div key={a.id} className="co-receipt"><p><span>{label(a.tender)}</span><strong>{formatCents(a.amount_cents)}</strong></p>{cnyCharged(a)&&<small className="co-receipt-cny">{cnyCharged(a)}</small>}{pricing?.undo_attempt_ids.includes(a.id)&&<TakeBack attempt={a} disabled={blocked} run={body=>mutation.run(`/checkouts/${order.id}/undo`,body)}/>}</div>)}{order.refunded_cents>0&&<p><span>Refunded</span><strong>{formatCents(order.refunded_cents)}</strong></p>}</section>}
    {photoAttempts.map(attempt=><PhotoCapture key={attempt.id} disabled={blocked} primary={attempt.id===(photoAttempts.find(a=>a.can_upload&&!a.photos.length)?.id)} order={order} attempt={attempt} draft={draft} setDraft={setDraft} updated={refresh} onBusy={value=>setPhotoStates(old=>({...old,[attempt.id]:value}))}/>)}
    {order.received_cents>0&&<RefundTools order={order} disabled={blocked} updated={updated} onBusy={setRefundBusy}/>}
    {order.can_manage&&order.status==='open'&&order.received_cents===0&&<details className="co-secondary"><summary>Cancel this order</summary><Button danger disabled={blocked} loading={mutation.saving} onClick={()=>run('cancel',{})}>Cancel unpaid order</Button></details>}
  </article>
}

function QueueRow({item,history,busy,select,me}:{item:Order;history:boolean;busy:boolean;select:(o:Order)=>void;me?:string}) {
  const clover=item.clover
  const total=clover?clover.total_cents:item.order.collected_cents
  const paid=paymentSummary(item.attempts)
  // History says what the customer actually paid and how; current orders say what the order comes to.
  const amount=history&&item.status==='cancelled'?'Cancelled':history&&item.status==='completed'?formatCents(paid.cents):total===null?'Total pending':formatCents(total)
  const money=!history||item.status==='cancelled'?null:item.status==='completed'
    ?[paid.methods.join(' + '),item.refunded_cents?`refunded ${formatCents(item.refunded_cents)}`:'',total!==null&&total!==paid.cents?`${clover?'Clover':'Order'} total ${formatCents(total)}`:''].filter(Boolean).join(' · ')
    :paid.cents?`${formatCents(paid.cents)} paid so far · ${paid.methods.join(' + ')}`:'Not paid yet'
  const who=item.cashier_sub===me?'You':item.cashier_name
  const missing=missingPhotos(item.attempts)
  const paidLine=<>{money&&<span className="co-switch-paid">{money}</span>}{missing>0&&<span className="co-switch-alert">{missing>1?`${missing} payment photos missing`:'Payment photo missing'}</span>}</>
  if(!clover)return <button type="button" disabled={busy} onClick={()=>select(item)}><span className="co-switch-top"><strong>{item.register_name||'POPCORE order'}</strong><b>{amount}</b></span>{paidLine}<span className="co-switch-items">{item.order.lines.map(l=>`${l.quantity}× ${l.product_name_snapshot}`).join(' · ')}</span><span className="co-switch-meta">{item.reference}{history?` · ${item.business_date} · ${who}`:''}</span><span className="co-switch-state">{item.status==='completed'?'Complete':item.status==='cancelled'?'Cancelled':'In progress'}</span></button>
  return <button type="button" disabled={busy} onClick={()=>select(item)}><span className="co-switch-top"><strong className="co-clover-id">{clover.order_id}</strong><b>{amount}</b></span>{paidLine}<span className="co-switch-items">{groupLines(clover.lines).map(l=>`${l.quantity}× ${l.name}`).join(' · ')}</span><span className="co-switch-meta">{clock(clover.created_ms)} · {clover.register?'Not picked up':who}{history?` · ${item.business_date}`:''}</span><span className="co-switch-state">{item.status==='completed'?'Sale recorded':item.status==='cancelled'?'Cancelled':'In progress'} · Clover: {clover.state_label}</span></button>
}

export default function CheckoutPage() {
  const {id}=useParams(),[params]=useSearchParams(),navigate=useNavigate(),{user}=useAuth0()
  const role=useRole()
  const history=id==='history'||(id!=='new'&&params.get('view')==='history')
  const selectedStore=useAppStore(s=>s.selectedStore),setSelectedStore=useAppStore(s=>s.setSelectedStore)
  const [access,setAccess]=useState<Access>(),[queue,setQueue]=useState<Queue>(),[order,setOrder]=useState<Order>(),[error,setError]=useState('')
  const [refresh,setRefresh]=useState(0),[date,setDate]=useState(''),[cursor,setCursor]=useState<number|null>(null),[busy,setBusy]=useState(false)
  const [autoOpen,setAutoOpen]=useState(readAutoOpen),[findText,setFindText]=useState(''),[findError,setFindError]=useState('')
  const [tender,setTender]=useState(''),[search,setSearch]=useState(''),[query,setQuery]=useState('')
  useEffect(()=>{const timer=setTimeout(()=>setQuery(historyQuery(search)),300);return()=>clearTimeout(timer)},[search])
  const autoOpenNow=useRef(autoOpen)
  autoOpenNow.current=autoOpen
  const [queueScope,setQueueScope]=useState('')
  const day=useRef(''),cachedAccess=useRef<{scope:Access;at:number}|null>(null)
  // Orders this screen has already seen or opened are never picked up automatically.
  const known=useRef<{scope:string;ids:Set<number>}>({scope:'',ids:new Set()})
  const scopeKey=[user?.sub,role,selectedStore?.id,history,date,cursor,tender,query].join('|')
  const [drafts,setDrafts]=useState<Record<number,Draft>>({})
  const manual=id==='new', orderId=id&&/^\d+$/.test(id)?Number(id):null
  const cloverStore=!!queue?.clover.enabled, cloverOn=cloverStore&&!history
  const connected=cloverStore&&!!queue?.clover.connected&&!error
  const filtered=history&&!!(tender||query)
  const chooseStore=(store:Store)=>setSelectedStore(useAppStore.getState().stores.find(s=>s.id===store.id)||{...store,color:'#4F46E5'})
  const invalidate=()=>{cachedAccess.current=null;setAccess(undefined);setQueue(undefined);setOrder(undefined);setDrafts({})}
  useEffect(()=>{const denied=()=>{invalidate();setError('Checkout access changed. Refresh to continue.')};window.addEventListener('popcore:checkout-access-denied',denied);return()=>window.removeEventListener('popcore:checkout-access-denied',denied)},[])
  useEffect(()=>{window.dispatchEvent(new CustomEvent('popcore:checkout-busy',{detail:busy}));return()=>{window.dispatchEvent(new CustomEvent('popcore:checkout-busy',{detail:false}))}},[busy])
  useEffect(()=>{setCursor(null)},[selectedStore?.id,history,date,tender,query])
  useEffect(()=>{cachedAccess.current=null;setAccess(undefined);setDrafts({});setQueue(undefined);setOrder(undefined);setError('')},[user?.sub,role,selectedStore?.id])
  useEffect(()=>{if(orderId)known.current.ids.add(orderId)},[orderId])
  useEffect(()=>{
    if(busy)return
    const controller=new AbortController()
    let timer:ReturnType<typeof setTimeout>|undefined,loading=false,fast=false
    const load=async()=>{
      if(loading)return
      clearTimeout(timer);loading=true
      const started=Date.now()
      try {
        if(document.visibilityState!=='visible')return
        // Fast Clover polling rechecks shift access every 15 seconds; every other load checks it now.
        let scope=fast&&cachedAccess.current&&Date.now()-cachedAccess.current.at<15000?cachedAccess.current.scope:null
        if(!scope){
          scope=(await client.get<Access>('/checkouts/access',{signal:controller.signal,timeout:15000})).data
          if(controller.signal.aborted)return
          cachedAccess.current={scope,at:Date.now()}
        }
        if(day.current&&day.current!==scope.business_date)setDrafts({})
        day.current=scope.business_date
        setAccess(scope)
        if(manual){setError('');return}
        const stores=history?scope.history_stores:scope.live_stores
        let store=stores.find(s=>s.id===selectedStore?.id)
        if(!store&&stores.length===1){chooseStore(stores[0]);return}
        if(!store){setQueue(undefined);setOrder(undefined);setError('');return}
        const {data:rows}=await client.get<Queue>('/checkouts',{params:{store_id:store.id,view:history?'history':'live',...(history&&date?{business_date:date}:{}),...(history&&tender?{tender}:{}),...(history&&query?{q:query}:{}),...(cursor?{before_id:cursor}:{})},signal:controller.signal,timeout:15000})
        const detail=orderId?(await client.get<Order>(`/checkouts/${orderId}`,{signal:controller.signal,timeout:15000})).data:undefined
        if(controller.signal.aborted)return
        if(detail&&detail.store_id!==store.id){setOrder(undefined);navigate(checkoutLink(history?'/checkout/history':'/checkout'),{replace:true});return}
        setQueue(rows);setQueueScope(scopeKey);setOrder(detail);setError('')
        if(detail)setDrafts(old=>old[detail.id]?old:{...old,[detail.id]:initialDraft(detail)})
        fast=!history&&!!rows.clover?.enabled
        if(fast){
          const scopeId=`${user?.sub}|${store.id}`
          if(known.current.scope!==scopeId)known.current={scope:scopeId,ids:new Set(rows.orders.map(o=>o.id))}
          const idle=!orderId||(!!detail&&detail.status!=='open'&&!needsPhoto(detail))
          const next=autoOpenCandidate({enabled:autoOpenNow.current,idle,visible:document.visibilityState==='visible',known:known.current.ids,orders:rows.orders})
          if(next!==null){
            known.current.ids.add(next)
            try {
              await client.post(`/checkouts/${next}/claim`,{},{headers:{'Idempotency-Key':crypto.randomUUID()},timeout:15000})
              if(!controller.signal.aborted)navigate(`/checkout/${next}`)
            } catch { /* another screen picked it up first */ }
          }
        }
      } catch(cause) {
        if(controller.signal.aborted)return
        const status=(cause as {response?:{status:number}})?.response?.status
        if(status===401||status===403)invalidate()
        setError(errorMessage(cause))
      } finally {
        loading=false
        if(!controller.signal.aborted)timer=setTimeout(load,fast?Math.max(100,1000-(Date.now()-started)):15000)
      }
    }
    load()
    window.addEventListener('focus',load);document.addEventListener('visibilitychange',load)
    return()=>{controller.abort();clearTimeout(timer);window.removeEventListener('focus',load);document.removeEventListener('visibilitychange',load)}
  },[id,history,date,cursor,tender,query,selectedStore?.id,refresh,user?.sub,role,busy])
  const stores=history?access?.history_stores:access?.live_stores
  const scoped=stores?.some(s=>s.id===selectedStore?.id)
  function select(next:Order){if(busy)return;navigate(checkoutLink(`/checkout/${next.id}`,history))}
  function updated(value?:Order){if(value)setOrder(value);setRefresh(n=>n+1)}
  function toggleAutoOpen(value:boolean){setAutoOpen(value);try{localStorage.setItem(AUTO_OPEN_KEY,value?'1':'0')}catch{/* this device only */}}
  async function findClover(){
    const code=findText.trim().toUpperCase()
    if(!/^[A-Z0-9]{13}$/.test(code)||!selectedStore){setFindError('Enter the 13-character Clover order ID.');return}
    try {
      const {data}=await client.get<Order>(`/checkouts/clover/${code}`,{params:{store_id:selectedStore.id},timeout:15000})
      setFindError('');setFindText('');navigate(checkoutLink(`/checkout/${data.id}`,data.status!=='open'))
    } catch(cause) {setFindError(errorMessage(cause))}
  }
  const current=order&&order.id===orderId&&order.store_id===selectedStore?.id&&drafts[order.id]?order:undefined
  const setDraft=(orderKey:number)=>(value:DraftUpdate)=>setDrafts(old=>({...old,[orderKey]:typeof value==='function'?value(old[orderKey]):{...old[orderKey],...value}}))
  if(manual&&access?.live_stores.length)return <div className="co-workspace"><ManualCheckout key={`${user?.sub}|${role}`} allowedStoreIds={access.live_stores.map(store=>store.id)}/></div>
  return <div className="co-workspace">
    <header className="co-heading"><div><h1>{orderId?'Order details':history?'Order history':'Checkout'}</h1>{!orderId&&<p>{history?(access?.role==='staff'?'Your orders, including previous shifts, and today’s Clover register sales.':'Orders at the locations you can access today.'):cloverOn?'Scan on Clover. Take card on Clover. Everything else is recorded here.':'Start a manual checkout or continue an open order.'}</p>}</div><Button icon={<ReloadOutlined/>} aria-label="Refresh orders" disabled={busy} onClick={()=>setRefresh(n=>n+1)}/></header>
    {!orderId&&<div className="co-toolbar"><span className={'co-connection'+(connected?' co-connected':'')}><span aria-hidden="true"/>{cloverStore?(connected?`Clover connected${queue?.clover.environment==='sandbox'?' · sandbox':''}${queue?.clover.writes===false?' · read only':''}`:'Clover not responding'):'Clover disconnected'}</span><Link to={checkoutLink(history?'/checkout':'/checkout/history')}>{history?'Current orders':'Order history'}</Link></div>}
    {cloverStore&&!orderId&&queue?.clover.message&&<p className="co-muted">{queue.clover.message}</p>}
    {cloverOn&&!orderId&&<div className="co-clover-tools">
      <label><Switch size="small" checked={autoOpen} onChange={toggleAutoOpen}/>Open new Clover orders automatically on this device</label>
      <div className="co-clover-find"><Input aria-label="Clover order ID" placeholder="Clover order ID" value={findText} maxLength={13} onChange={e=>{setFindText(e.target.value);setFindError('')}} onPressEnter={findClover}/><Button disabled={busy} onClick={findClover}>Find</Button></div>
      {findError&&<Alert type="error" showIcon message={findError}/>}
    </div>}
    {error&&<Alert type="error" showIcon message={error} action={<Button disabled={busy} onClick={()=>setRefresh(n=>n+1)}>Retry</Button>}/>}
    {!access?(!error&&<Skeleton active paragraph={{rows:4}}/>):<>
      {!!stores?.length&&(!orderId||!scoped)&&(stores.length>1||history)&&<div className="co-location"><label htmlFor="checkout-store">Location</label><select id="checkout-store" className="co-select" value={scoped?selectedStore?.id:''} disabled={busy} onChange={e=>{const store=stores.find(s=>s.id===Number(e.target.value));if(store){chooseStore(store);if(!orderId)navigate(checkoutLink(history?'/checkout/history':'/checkout'))}}}><option value="" disabled>Choose location</option>{stores.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select>{history&&!orderId&&<label className="co-date-filter">Order date<input type="date" disabled={busy} value={date} onChange={e=>{setDate(e.target.value);setCursor(null)}}/></label>}</div>}
      {history&&!orderId&&scoped&&<div className="co-history-filters">
        <label>Paid by<select className="co-select" value={tender} disabled={busy} onChange={e=>setTender(e.target.value)}><option value="">Any method</option>{TENDER_FILTERS.map(option=><option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
        <label className="co-history-search">Search<Input allowClear value={search} maxLength={80} placeholder="Item, Clover order ID or note" onChange={e=>setSearch(e.target.value)}/></label>
      </div>}
      {!history&&!orderId&&scoped&&<div className="co-location"><Button type={cloverOn?'default':'primary'} disabled={busy} onClick={()=>navigate('/checkout/new')}>Start manual checkout</Button><Link to="/sales/entry" aria-disabled={busy} onClick={event=>{if(busy)event.preventDefault()}}>Record completed sale</Link></div>}
      {orderId&&<Link className="co-back" to={checkoutLink(history?'/checkout/history':'/checkout')}>← Back to {history?'order history':'current orders'}</Link>}
      {!stores?.length?<div className="co-empty"><CameraOutlined/><h2>{history?'No order history available':'No checkout shift today'}</h2><p>{history?'Your accessible orders will appear here.':'Live orders are available at the location where you have an assigned shift today.'}</p><Link to={checkoutLink(history?'/checkout':'/checkout/history')}>{history?'Go to checkout':'View your order history'}</Link><Link to="/schedule">Open Schedule</Link></div>:!scoped?<p className="co-muted">Choose a location to see its orders.</p>:!queue||queueScope!==scopeKey?<Skeleton active/>:<>
        {!orderId&&!!queue.orders.length&&<nav className={'co-order-switcher'+(history?' co-history-list':'')} aria-label={history?'Order history':'Current orders'}>{queue.orders.map(item=><QueueRow key={item.id} item={item} history={history} busy={busy} select={select} me={user?.sub}/>)}</nav>}
        {!orderId&&queue.next_before_id&&<Button disabled={busy} onClick={()=>setCursor(queue.next_before_id)}>Older orders</Button>}{!orderId&&cursor&&<Button disabled={busy} onClick={()=>setCursor(null)}>Newest orders</Button>}
        {current?(current.clover?<CloverOrder key={`clover|${current.id}`} order={current} draft={drafts[current.id]} cnyPerCad={access.cny_per_cad||''} cnyRateSource={access.cny_rate_source} setDraft={setDraft(current.id)} updated={updated} refresh={()=>setRefresh(n=>n+1)} onBusy={setBusy}/>
          :<CurrentOrder key={current.id} order={current} draft={drafts[current.id]} cnyPerCad={access.cny_per_cad||''} cnyRateSource={access.cny_rate_source} setDraft={setDraft(current.id)} updated={updated} refresh={()=>setRefresh(n=>n+1)} onBusy={setBusy}/>)
          :!orderId&&<div className="co-empty"><CameraOutlined/><h2>{queue.orders.length?'Choose an order above':filtered?'No orders match':history?'No orders for this view':'Ready for your next customer'}</h2><p>{queue.orders.length?'Tap your customer’s order. New arrivals will not switch your current order.':filtered?'Try another payment method or search word.':history?'Try another date, or return to current orders.':cloverOn?(autoOpen?'Scan the items on Clover. The order opens here by itself.':'Scan the items on Clover. The order appears above.'):'Clover is disconnected. Start a manual checkout to enter the items and record payment.'}</p>{!history&&!cloverOn&&<span className="co-muted">Automatic order sync is not active.</span>}</div>}
        {orderId&&order?.id!==orderId&&<Skeleton active paragraph={{rows:5}}/>}
      </>}
    </>}
    <footer className="co-footer">{access?.business_date&&<span>{access.business_date} · Toronto time</span>}<Link to="/schedule">My schedule</Link></footer>
  </div>
}
