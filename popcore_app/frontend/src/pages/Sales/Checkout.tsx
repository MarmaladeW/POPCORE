import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Form, Input, Modal, Skeleton } from 'antd'
import { CameraOutlined, CheckCircleOutlined, CreditCardOutlined, DollarOutlined, ReloadOutlined } from '@ant-design/icons'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import client from '../../api/client'
import { useAppStore } from '../../store'
import { formatCents, parseMoneyToCents } from '../../lib/money'
import { discountQuote, suggestPaymentTarget } from '../../lib/checkoutPricing'
import { useHasRole, useRole } from '../../auth/useRole'
import ManualCheckout from './ManualCheckout'
import useCheckoutMutation from './useCheckoutMutation'
import './Checkout.css'

type Store = { id:number; code:string; name:string }
type Access = { business_date:string; role:string; live_stores:Store[]; history_stores:Store[] }
type Photo = { id:number }
type Attempt = { id:number; tender:string; amount_cents:number; status:string; can_upload:boolean; photos:Photo[]; refundable_cents?:number }
type Order = { id:number; store_id:number; reference:string; register_name?:string; business_date:string; status:string; version:number; sale_id:number|null; cashier_name:string; cashier_sub:string; can_manage:boolean; can_process:boolean; can_refund:boolean; received_cents:number; remaining_cents:number; refunded_cents:number; refund_due_cents:number; abandoned_reason:string|null; attempts:Attempt[]; refunds:{id:number;amount_cents:number;reference:string;business_date:string;tender:string}[]; order:{ subtotal_cents:number;source_tax_cents:number;gross_cents:number;reduction_cents:number;collected_cents:number;lines:{product_name_snapshot:string;quantity:number;unit:string}[] } }
type DraftUpdate = Partial<Draft> | ((draft:Draft)=>Draft)
type Queue = { orders:Order[]; next_before_id:number|null; clover:{connected:boolean} }
type Draft = { target:string; tender:string; split:string; includesCard:boolean; photos?:Record<number,File> }
const methods = [{value:'cash',label:'Cash',icon:<DollarOutlined/>},{value:'card',label:'Card',icon:<CreditCardOutlined/>},{value:'e_transfer',label:'E-transfer'},{value:'wechat',label:'WeChat Pay'},{value:'alipay',label:'Alipay'}]
const electronic = (method:string) => ['e_transfer','wechat','alipay'].includes(method)
const moneyInput = (cents:number) => (cents/100).toFixed(2)
const label = (method:string) => methods.find(m=>m.value===method)?.label || method
const errorMessage = (cause:unknown) => (cause as {_serverMessage?:string})?._serverMessage || 'Unable to load checkout. Please retry.'
const initialDraft = (order:Order):Draft => {
  const active=order.attempts.filter(a=>a.status!=='cancelled'),tender=active.find(a=>a.status==='pending')?.tender || ''
  const includesCard=active.some(a=>a.tender==='card')&&active.some(a=>a.tender!=='card')
  const target=order.attempts.length||order.order.reduction_cents?order.order.collected_cents:suggestPaymentTarget(order.order.gross_cents,tender,includesCard)
  return {target:moneyInput(target),tender,split:'',includesCard}
}

function PhotoCapture({order,attempt,draft,setDraft,updated,onBusy,primary,disabled}:{order:Order;attempt:Attempt;primary:boolean;disabled:boolean;draft:Draft;setDraft:(v:DraftUpdate)=>void;updated:()=>void;onBusy:(v:boolean)=>void}) {
  const fileInput=useRef<HTMLInputElement>(null)
  const [preview,setPreview]=useState(''),[viewing,setViewing]=useState(''),[readError,setReadError]=useState('')
  const file=draft.photos?.[attempt.id]
  const viewingRequest=useRef<AbortController|null>(null)
  useEffect(()=>()=>viewingRequest.current?.abort(),[])
  const mutation=useCheckoutMutation(()=>{setDraft(old=>{const photos={...old.photos};delete photos[attempt.id];return {...old,photos}});updated()})
  useEffect(()=>{onBusy(mutation.pending);return()=>onBusy(false)},[mutation.pending])
  useEffect(()=>{if(!file){setPreview('');return}const url=URL.createObjectURL(file);setPreview(url);return()=>URL.revokeObjectURL(url)},[file])
  useEffect(()=>()=>{if(viewing)URL.revokeObjectURL(viewing)},[viewing])
  async function view(photo:Photo) {
    viewingRequest.current?.abort();const controller=new AbortController();viewingRequest.current=controller
    try {const response=await client.get(`/checkouts/${order.id}/evidence/${photo.id}`,{responseType:'blob',signal:controller.signal});if(controller.signal.aborted)return;setViewing(URL.createObjectURL(response.data));setReadError('')}
    catch(cause){if(controller.signal.aborted)return;setViewing('');setReadError(errorMessage(cause));const status=(cause as {response?:{status:number}})?.response?.status;if(status===401||status===403)window.dispatchEvent(new Event('popcore:checkout-access-denied'))}
  }
  return <section className="co-photo" aria-label={`Payment evidence for ${label(attempt.tender)}`}>
    <div className="co-section-title"><h3>Payment photo</h3><span>{label(attempt.tender)} · {formatCents(attempt.amount_cents)}</span></div>
    {mutation.notice}{readError&&<Alert type="error" message={readError}/>}
    {attempt.photos.length>0&&<div className="co-saved" role="status"><CheckCircleOutlined/> <strong>Photo saved</strong>{attempt.photos.map((photo,i)=><Button type="link" key={photo.id} onClick={()=>view(photo)}>View{i?' '+(i+1):''}</Button>)}</div>}
    {attempt.can_upload&&<>
      <input ref={fileInput} className="co-file-input" type="file" aria-label="Payment evidence photo" accept="image/jpeg,image/png,image/webp" capture="environment" disabled={disabled||mutation.pending} onChange={e=>{const selected=e.target.files?.[0];if(selected)setDraft(old=>({...old,photos:{...old.photos,[attempt.id]:selected}}));e.target.value=''}}/>
      {preview?<div className="co-preview">
        <img src={preview} alt="Payment evidence preview"/>
        <p>{order.reference} · {label(attempt.tender)} · {formatCents(attempt.amount_cents)}</p>
        <div className="co-action-pair"><Button disabled={disabled||mutation.pending} onClick={()=>fileInput.current?.click()}>Retake</Button><Button type="primary" loading={mutation.saving} disabled={disabled||mutation.pending&&!mutation.saving} onClick={()=>{if(!file)return;const body=new FormData();body.append('image',file);mutation.run(`/checkouts/${order.id}/attempts/${attempt.id}/evidence`,body)}}>Use photo</Button></div>
      </div>:<Button aria-label={attempt.photos.length?'Add another photo':'Take photo'} className={'co-camera'+(primary&&!attempt.photos.length?' co-camera-primary':'')} type={attempt.photos.length?'default':'primary'} icon={<CameraOutlined/>} disabled={disabled||mutation.pending} onClick={()=>fileInput.current?.click()}>{attempt.photos.length?'Add another photo':'Take photo'}</Button>}
    </>}
    {!attempt.can_upload&&!attempt.photos.length&&<p className="co-muted">Photo missing. Checkout access is required to add evidence.</p>}
    <Modal title="Saved payment evidence" open={Boolean(viewing)} onCancel={()=>setViewing('')} footer={null} destroyOnClose><img src={viewing} alt="Saved payment evidence" style={{width:'100%'}}/></Modal>
  </section>
}

function RefundTools({order,updated,onBusy,disabled}:{order:Order;updated:(o?:Order)=>void;onBusy:(v:boolean)=>void;disabled:boolean}) {
  const manager=useHasRole('manager')
  const mutation=useCheckoutMutation((data:Order)=>updated(data))
  useEffect(()=>{onBusy(mutation.pending);return()=>onBusy(false)},[mutation.pending])
  if(!manager || !order.can_manage || order.status!=='open')return null
  const action=(name:string,body:object)=>mutation.run(`/checkouts/${order.id}/${name}`,{expected_version:order.version,...body})
  return <details className="co-secondary"><summary>Cancel or refund this order</summary>{mutation.notice}
    {!order.abandoned_reason?<Form layout="vertical" disabled={disabled||mutation.pending} onFinish={v=>action(order.received_cents?'abandon':'cancel',{reason:v.reason})}>
      <p>{order.received_cents?'Mark this checkout abandoned, then record the money actually refunded.':'Cancel this unpaid checkout.'}</p>
      {!!order.received_cents&&<Form.Item name="reason" label="Reason" rules={[{required:true,whitespace:true}]}><Input.TextArea rows={2}/></Form.Item>}
      <Button danger htmlType="submit" loading={mutation.saving}>{order.received_cents?'Abandon checkout':'Cancel checkout'}</Button>
    </Form>:<Form key={order.version} layout="vertical" disabled={disabled||mutation.pending} onFinish={v=>action('refund',{...v,attempt_id:Number(v.attempt_id),amount_cents:parseMoneyToCents(v.amount)})}>
      <p><strong>{formatCents(order.refund_due_cents)}</strong> still to refund. Record refunds after returning the money.</p>
      <Form.Item name="attempt_id" label="Original payment" rules={[{required:true}]}><select className="co-select" defaultValue=""><option value="" disabled>Select payment</option>{order.attempts.filter(a=>(a.refundable_cents||0)>0).map(a=><option key={a.id} value={a.id}>{label(a.tender)} · {formatCents(a.refundable_cents||0)} refundable</option>)}</select></Form.Item>
      <div className="co-action-pair"><Form.Item name="amount" label="Refund amount ($)" rules={[{required:true}]}><Input inputMode="decimal"/></Form.Item><Form.Item name="business_date" label="Refund date" rules={[{required:true}]}><Input type="date"/></Form.Item></div>
      <Form.Item name="reference" label="Refund reference" rules={[{required:true,whitespace:true,max:120}]}><Input/></Form.Item>
      <Form.Item name="reason" label="Reason" rules={[{required:true,whitespace:true}]}><Input.TextArea rows={2}/></Form.Item>
      <Form.Item name="confirmed_received_and_refunded" valuePropName="checked" rules={[{validator:(_,value)=>value===true?Promise.resolve():Promise.reject(new Error('Confirm the receipt and actual refund.'))}]}><Checkbox>I checked the original receipt and returned this money.</Checkbox></Form.Item>
      <Button danger htmlType="submit" loading={mutation.saving}>Record refund</Button>
    </Form>}
  </details>
}

function CurrentOrder({order,draft,setDraft,updated,refresh,onBusy}:{order:Order;draft:Draft;setDraft:(v:DraftUpdate)=>void;updated:(o?:Order)=>void;refresh:()=>void;onBusy:(v:boolean)=>void}) {
  const [split,setSplit]=useState(false),[notice,setNotice]=useState('')
  const mutation=useCheckoutMutation((data:Order)=>updated(data))
  const [photoStates,setPhotoStates]=useState<Record<number,boolean>>({})
  const photoBusy=Object.values(photoStates).some(Boolean)
  const [refundBusy,setRefundBusy]=useState(false)
  useEffect(()=>{onBusy(mutation.pending||photoBusy||refundBusy);return()=>onBusy(false)},[mutation.pending,photoBusy,refundBusy])
  const pending=order.attempts.find(a=>a.status==='pending')
  const target=parseMoneyToCents(draft.target)
  const quote=target===null?null:discountQuote(order.order.subtotal_cents,order.order.source_tax_cents,target)
  const amountMatches=target===order.order.collected_cents
  const blocked=mutation.pending||photoBusy||refundBusy
  const mixedWithCard=split&&draft.includesCard
  const pricingLocked=order.received_cents>0||order.order.reduction_cents>0
  const pricingTarget=(method:string,mixed:boolean)=>moneyInput(pricingLocked?order.order.collected_cents:suggestPaymentTarget(order.order.gross_cents,method,mixed))
  const action=(name:string,body:object={})=>mutation.run(`/checkouts/${order.id}/${name}`,{expected_version:order.version,...body})
  async function received() {
    if(!pending)return
    const result=await action('complete',{attempt_id:pending.id}) as Order|undefined
    if(result&&result.remaining_cents===0)await mutation.run(`/checkouts/${order.id}/finalize`,{expected_version:result.version})
  }
  function choose(method:string) {
    const includesCard=split&&(method==='card'||draft.includesCard)
    setDraft({tender:method,includesCard,target:pricingTarget(method,includesCard)});setNotice('')
  }
  function chooseSplit(enabled:boolean) {
    const includesCard=enabled&&(draft.tender==='card'||draft.includesCard)
    setSplit(enabled);setDraft({includesCard,target:pricingTarget(draft.tender,includesCard)})
  }
  const photoAttempts=order.attempts.filter(a=>a.status!=='cancelled'&&electronic(a.tender))
  return <article className="co-order" aria-label={`Checkout ${order.reference}`}>
    <header className="co-order-heading"><div><span className="co-muted">{order.register_name||'POPCORE order'} · {order.reference}</span><h2>{order.status==='cancelled'?'Checkout cancelled':order.status==='completed'?'Checkout complete':'Current order'}</h2></div><span className={'co-status co-status-'+order.status}>{order.abandoned_reason&&order.status==='open'?'Refund due':order.status==='open'?'In progress':order.status}</span></header>
    <p className="co-cashier">Cashier <strong>{order.cashier_name}</strong><span>{order.business_date}</span></p>
    <ul className="co-items">{order.order.lines.map((line,i)=><li key={i}><span className="co-quantity">{line.quantity}×</span><span>{line.product_name_snapshot}</span></li>)}</ul>
    <div className="co-original"><span>Subtotal <strong>{formatCents(order.order.subtotal_cents)}</strong></span><span>Tax <strong>{formatCents(order.order.source_tax_cents)}</strong></span><span>Original total <strong>{formatCents(order.order.gross_cents)}</strong></span></div>
    {mutation.notice}{notice&&<Alert type="error" message={notice}/>}
    {order.can_process&&<>

      <div className="co-pricing">
        <div className="co-payable"><label htmlFor="checkout-target">Customer pays</label><div className="co-money-input"><span aria-hidden="true">$</span><input id="checkout-target" aria-describedby="checkout-target-help" inputMode="decimal" value={draft.target} disabled={blocked||draft.tender==='card'||order.received_cents>0} onChange={e=>setDraft({target:e.target.value})}/><span>CAD</span></div><p id="checkout-target-help">{order.received_cents?'Payments already received. The order total is fixed.':mixedWithCard?'Whole-dollar split target · no cash discount':draft.tender==='card'?'Use the recorded card total.':'Suggested amount · tap to adjust'}</p></div>
        {(draft.tender!=='card'||mixedWithCard)&&<div className="co-discount"><span>{mixedWithCard?'Pre-tax rounding adjustment to enter in Clover':'Pre-tax discount to enter in Clover'}</span><strong>{quote?formatCents(quote.discountCents):'—'}</strong><small>Estimate from the original subtotal and tax. Check Clover’s resulting total.</small></div>}
      </div>
      <fieldset className="co-methods" disabled={blocked}><legend>How is the customer paying?</legend><div>{methods.map(method=><button key={method.value} type="button" aria-pressed={draft.tender===method.value} onClick={()=>choose(method.value)}>{method.icon}{method.label}</button>)}</div></fieldset>
      {!quote&&<Alert type="error" showIcon message="Enter a positive amount no greater than the original total."/>}
      {quote?.warning&&<Alert type="warning" showIcon message="This discount is more than 20% of the order value. Check the amount before continuing."/>}
      {quote&&quote.predictedTotalCents!==target&&(draft.tender!=='card'||mixedWithCard)&&<p className="co-muted">Estimated total after rounding: {formatCents(quote.predictedTotalCents)}. Clover may round differently.</p>}
      {!amountMatches&&<div className="co-waiting" role="status"><strong>Waiting for confirmed Clover total</strong><p>The recorded order is {formatCents(order.order.collected_cents)}. The target above has not changed its payment total. Clover is disconnected.</p><Button type="link" disabled={blocked} onClick={()=>setDraft({target:moneyInput(order.order.collected_cents)})}>Use recorded total</Button></div>}
      {draft.tender&&mixedWithCard&&<p className="co-instruction">Confirm the whole-dollar total in Clover before the first payment, then split it between <strong>Card</strong> and the other method.</p>}
      {draft.tender&&draft.tender!=='card'&&!mixedWithCard&&<p className="co-instruction">Enter the discount in Clover, then choose <strong>{label(draft.tender)}</strong>.</p>}
      {order.remaining_cents>0&&<>
        <div className="co-payment-action">
          {pending&&pending.tender===draft.tender?<Button type={electronic(pending.tender)&&!pending.photos.length?'default':'primary'} size="large" disabled={blocked||!amountMatches||!quote} loading={mutation.saving} onClick={received}>Record {formatCents(pending.amount_cents)} received</Button>:<Button type="primary" size="large" disabled={blocked||!draft.tender||!amountMatches||!quote} loading={mutation.saving} onClick={()=>{const amount=split?parseMoneyToCents(draft.split):order.remaining_cents;if(amount===null||amount<=0||amount>order.remaining_cents){setNotice('Enter an amount within the remaining balance.');return}action('attempts',{tender:draft.tender,amount_cents:amount})}}>{pending?'Change payment method':'Continue with '+(label(draft.tender)||'payment')}</Button>}
          <small>Manual recording while Clover is disconnected. Only confirm money actually received.</small>
        </div>
        {(!pending||pending.tender!==draft.tender)&&<div className="co-split"><Checkbox disabled={blocked} checked={split} onChange={e=>chooseSplit(e.target.checked)}>Split payment</Checkbox>{split&&<><Checkbox disabled={blocked||order.received_cents>0||draft.tender==='card'} checked={draft.includesCard} onChange={e=>setDraft({includesCard:e.target.checked,target:pricingTarget(draft.tender,e.target.checked)})}>This split includes Card</Checkbox><label>Collect now ($)<Input value={draft.split} inputMode="decimal" disabled={blocked} onChange={e=>setDraft({split:e.target.value})}/></label></>}</div>}
      </>}
      {order.remaining_cents===0&&<Button type="primary" disabled={blocked} loading={mutation.saving} onClick={()=>action('finalize')}>Finish checkout</Button>}
    </>}
    {order.abandoned_reason&&<Alert type="warning" message={`${formatCents(order.refund_due_cents)} still to refund`} description={order.abandoned_reason}/>}
    {!order.can_process&&order.status==='open'&&!order.abandoned_reason&&<p className="co-muted">View only. Processing requires the cashier’s account and an assigned shift at this location today.</p>}
    {photoAttempts.map(attempt=><PhotoCapture key={attempt.id} disabled={blocked} primary={attempt.id===(photoAttempts.find(a=>a.can_upload&&!a.photos.length)?.id)} order={order} attempt={attempt} draft={draft} setDraft={setDraft} updated={refresh} onBusy={value=>setPhotoStates(old=>({...old,[attempt.id]:value}))}/>)}
    {order.attempts.some(a=>a.status==='completed')&&<section className="co-receipts"><h3>Recorded payments</h3>{order.attempts.filter(a=>a.status==='completed').map(a=><p key={a.id}><span>{label(a.tender)}</span><strong>{formatCents(a.amount_cents)}</strong></p>)}{order.refunded_cents>0&&<p><span>Refunded</span><strong>{formatCents(order.refunded_cents)}</strong></p>}</section>}
    <RefundTools order={order} disabled={blocked} updated={updated} onBusy={setRefundBusy}/>
    {!useHasRole('manager')&&order.can_manage&&order.status==='open'&&order.received_cents===0&&<details className="co-secondary"><summary>Cancel this order</summary><Button danger disabled={blocked} onClick={()=>action('cancel')}>Cancel unpaid checkout</Button></details>}
    {order.status!=='open'&&<Link className="co-next" to="/checkout">Back to current orders</Link>}
  </article>
}

export default function CheckoutPage() {
  const {id}=useParams(),[params]=useSearchParams(),navigate=useNavigate(),{user}=useAuth0()
  const role=useRole()
  const history=id==='history'||params.get('view')==='history'
  const selectedStore=useAppStore(s=>s.selectedStore),setSelectedStore=useAppStore(s=>s.setSelectedStore)
  const [access,setAccess]=useState<Access>(),[queue,setQueue]=useState<Queue>(),[order,setOrder]=useState<Order>(),[error,setError]=useState('')
  const [refresh,setRefresh]=useState(0),[date,setDate]=useState(''),[cursor,setCursor]=useState<number|null>(null),[busy,setBusy]=useState(false)
  const [queueScope,setQueueScope]=useState('')
  const day=useRef('')
  const scopeKey=[user?.sub,role,selectedStore?.id,history,date,cursor].join('|')
  const [drafts,setDrafts]=useState<Record<number,Draft>>({})
  const manual=id==='new', orderId=id&&/^\d+$/.test(id)?Number(id):null
  const chooseStore=(store:Store)=>setSelectedStore(useAppStore.getState().stores.find(s=>s.id===store.id)||{...store,color:'#4F46E5'})
  const invalidate=()=>{setAccess(undefined);setQueue(undefined);setOrder(undefined);setDrafts({})}
  useEffect(()=>{const denied=()=>{invalidate();setError('Checkout access changed. Refresh to continue.')};window.addEventListener('popcore:checkout-access-denied',denied);return()=>window.removeEventListener('popcore:checkout-access-denied',denied)},[])
  useEffect(()=>{window.dispatchEvent(new CustomEvent('popcore:checkout-busy',{detail:busy}));return()=>{window.dispatchEvent(new CustomEvent('popcore:checkout-busy',{detail:false}))}},[busy])
  useEffect(()=>{setCursor(null)},[selectedStore?.id,history,date])
  useEffect(()=>{setDrafts({});setQueue(undefined);setOrder(undefined)},[user?.sub,role,selectedStore?.id])
  useEffect(()=>{
    if(manual||busy)return
    const controller=new AbortController()
    const load=async()=>{
      try {
        const {data:scope}=await client.get<Access>('/checkouts/access',{signal:controller.signal})
        if(controller.signal.aborted)return
        if(day.current&&day.current!==scope.business_date)setDrafts({})
        day.current=scope.business_date
        setAccess(scope)
        const stores=history?scope.history_stores:scope.live_stores
        let store=stores.find(s=>s.id===selectedStore?.id)
        if(!store&&stores.length===1){chooseStore(stores[0]);return}
        if(!store){setQueue(undefined);setOrder(undefined);setError('');return}
        const {data:rows}=await client.get<Queue>('/checkouts',{params:{store_id:store.id,view:history?'history':'live',...(history&&date?{business_date:date}:{}),...(cursor?{before_id:cursor}:{})},signal:controller.signal})
        const detail=orderId?(await client.get<Order>(`/checkouts/${orderId}`,{signal:controller.signal})).data:undefined
        if(controller.signal.aborted)return
        if(detail&&detail.store_id!==store.id){setOrder(undefined);navigate(history?'/checkout/history':'/checkout',{replace:true});return}
        setQueue(rows);setQueueScope(scopeKey);setOrder(detail);setError('')
        if(detail)setDrafts(old=>old[detail.id]?old:{...old,[detail.id]:initialDraft(detail)})
      } catch(cause) {
        if(controller.signal.aborted)return
        const status=(cause as {response?:{status:number}})?.response?.status
        if(status===401||status===403)invalidate()
        setError(errorMessage(cause))
      }
    }
    load()
    return()=>controller.abort()
  },[id,history,date,cursor,selectedStore?.id,refresh,user?.sub,role,busy])
  useEffect(()=>{
    if(busy||manual)return
    const update=()=>{if(document.visibilityState==='visible')setRefresh(n=>n+1)}
    const timer=setInterval(update,15000);window.addEventListener('focus',update)
    return()=>{clearInterval(timer);window.removeEventListener('focus',update)}
  },[busy,manual])
  const stores=history?access?.history_stores:access?.live_stores
  const scoped=stores?.some(s=>s.id===selectedStore?.id)
  function select(next:Order){if(busy)return;navigate(`/checkout/${next.id}${history?'?view=history':''}`)}
  function updated(value?:Order){if(value)setOrder(value);setRefresh(n=>n+1)}
  if(manual)return <div className="co-workspace"><ManualCheckout key={`${user?.sub}|${role}`}/></div>
  return <div className="co-workspace">
    <header className="co-heading"><div><h1>{history?'Order history':'Checkout'}</h1><p>{history?(access?.role==='staff'?'Your orders, including previous shifts.':'Orders at the locations you can access today.'):'The order, the amount, the payment photo.'}</p></div><Button icon={<ReloadOutlined/>} aria-label="Refresh orders" disabled={busy} onClick={()=>setRefresh(n=>n+1)}/></header>
    <div className="co-toolbar"><span className="co-connection"><span aria-hidden="true"/>Clover disconnected</span><Link to={history?'/checkout':'/checkout/history'}>{history?'Current orders':'Order history'}</Link></div>
    {error&&<Alert type="error" showIcon message={error} action={<Button disabled={busy} onClick={()=>setRefresh(n=>n+1)}>Retry</Button>}/>}
    {!access?(!error&&<Skeleton active paragraph={{rows:4}}/>):<>
      {!!stores?.length&&(stores.length>1||history)&&<div className="co-location"><label htmlFor="checkout-store">Location</label><select id="checkout-store" className="co-select" value={scoped?selectedStore?.id:''} disabled={busy} onChange={e=>{const store=stores.find(s=>s.id===Number(e.target.value));if(store){chooseStore(store);navigate(history?'/checkout/history':'/checkout')}}}><option value="" disabled>Choose location</option>{stores.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select>{history&&<label className="co-date-filter">Order date<input type="date" disabled={busy} value={date} onChange={e=>{setDate(e.target.value);setCursor(null)}}/></label>}</div>}
      {!stores?.length?<div className="co-empty"><CameraOutlined/><h2>{history?'No order history available':'No checkout shift today'}</h2><p>{history?'Your accessible orders will appear here.':'Live orders are available at the location where you have an assigned shift today.'}</p><Link to={history?'/checkout':'/checkout/history'}>{history?'Go to checkout':'View your order history'}</Link><Link to="/schedule">Open Schedule</Link></div>:!scoped?<p className="co-muted">Choose a location to see its orders.</p>:!queue||queueScope!==scopeKey?<Skeleton active/>:<>
        {!!queue.orders.length&&<nav className={'co-order-switcher'+(history?' co-history-list':'')} aria-label={history?'Order history':'Current orders'}>{queue.orders.map(item=><button type="button" key={item.id} disabled={busy} aria-pressed={orderId===item.id} onClick={()=>select(item)}><span className="co-switch-top"><strong>{item.register_name||'POPCORE order'}</strong><b>{formatCents(item.order.collected_cents)}</b></span><span className="co-switch-items">{item.order.lines.map(l=>`${l.quantity}× ${l.product_name_snapshot}`).join(' · ')}</span><span className="co-switch-meta">{item.reference}{history?` · ${item.business_date} · ${item.cashier_name}`:''}</span></button>)}</nav>}
        {queue.next_before_id&&<Button disabled={busy} onClick={()=>setCursor(queue.next_before_id)}>Older orders</Button>}{cursor&&<Button disabled={busy} onClick={()=>setCursor(null)}>Newest orders</Button>}
        {order&&order.id===orderId&&order.store_id===selectedStore?.id&&drafts[order.id]?<CurrentOrder key={order.id} order={order} draft={drafts[order.id]} setDraft={value=>setDrafts(old=>({...old,[order.id]:typeof value==='function'?value(old[order.id]):{...old[order.id],...value}}))} updated={updated} refresh={()=>setRefresh(n=>n+1)} onBusy={setBusy}/>:!orderId&&<div className="co-empty"><CameraOutlined/><h2>{queue.orders.length?'Choose an order above':history?'No orders for this view':'Ready for your next customer'}</h2><p>{queue.orders.length?'Register, amount and items help you pick the right customer.':history?'Try another date, or return to current orders.':'Orders will appear here after Clover is connected. Automatic order sync is not active yet.'}</p>{!history&&<span className="co-muted">No customer details to re-enter once connected.</span>}</div>}
        {orderId&&order?.id!==orderId&&<Skeleton active paragraph={{rows:5}}/>}
      </>}
    </>}
    <footer className="co-footer">{access?.business_date&&<span>{access.business_date} · Toronto time</span>}<Link to="/schedule">My schedule</Link></footer>
  </div>
}
