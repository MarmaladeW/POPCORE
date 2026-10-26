import { useEffect, useState } from 'react'
import { Alert, Button, Input } from 'antd'
import { CheckCircleOutlined } from '@ant-design/icons'
import Icon from '../../components/pc/Icon'
import { formatCents } from '../../lib/money'
import { convertCadCentsToCnyFen } from '../../lib/checkoutPricing'
import useCheckoutMutation from './useCheckoutMutation'
import { cnyCharged } from './checkoutHistory'
import { cardPart, centsOrNull, groupLines, POPCORE_TENDERS, reviewTitle, splitInstruction, stateTone, writebackStatus } from './cloverOrderView'
import { cnyMoney, electronic, label, methods, OrderNotes, PhotoCapture, RefundTools, TakeBack, TenderLabel, type Draft, type DraftUpdate, type Order } from './checkoutParts'

const clock = (ms:number) => new Date(ms).toLocaleTimeString('en-CA',{hour:'numeric',minute:'2-digit',timeZone:'America/Toronto'})
const off = (cents:number) => `−${formatCents(cents)}`
const tenderButtons = methods.filter(m=>(POPCORE_TENDERS as readonly string[]).includes(m.value))

/** One Clover order: Clover scans and takes card; POPCORE prices, records and completes it on Clover. */
export default function CloverOrder({order,draft,setDraft,updated,refresh,onBusy,cnyPerCad,cnyRateSource}:{order:Order;draft:Draft;setDraft:(v:DraftUpdate)=>void;updated:(o?:Order)=>void;refresh:()=>void;onBusy:(v:boolean)=>void;cnyPerCad:string;cnyRateSource?:string|null}) {
  const clover=order.clover!
  // Right after Received finishes an order labelled on Clover, the device still shows it: say what to tap there.
  const [finishedHere,setFinishedHere]=useState(false)
  const mutation=useCheckoutMutation((data:Order)=>{if(data.status==='completed'&&order.status==='open')setFinishedHere(true);updated(data)})
  const [photoStates,setPhotoStates]=useState<Record<number,boolean>>({})
  const [noteBusy,setNoteBusy]=useState(false),[refundBusy,setRefundBusy]=useState(false),[handled,setHandled]=useState('')
  const busy=mutation.pending||noteBusy||refundBusy||Object.values(photoStates).some(Boolean)
  useEffect(()=>{onBusy(busy);return()=>onBusy(false)},[busy])
  const run=(name:string,body:object={})=>mutation.run(`/checkouts/${order.id}/${name}`,body)
  const open=order.status==='open'&&!order.abandoned_reason
  const collect=clover.collect, total=clover.total_cents
  const cardLanded=clover.clover_cents>0, popcoreIn=clover.popcore_cents>0
  // A split the cashier started stays a split after a refresh or a reload.
  const splitMode=draft.cloverSplit??(clover.plan==='split_card'?'card':'')
  const plan=splitMode==='card'?'split_card':'pay'
  const quote=collect?(plan==='split_card'?collect.split_card:collect.pay):null
  const typedCard=collect&&splitMode==='card'&&!cardLanded&&!popcoreIn?cardPart(draft.split,collect.split_card.due_cents):null
  let amount:number|null=null
  if(collect&&quote){
    if(splitMode==='card')amount=cardLanded?quote.rest_cents:popcoreIn?null:typedCard?.cents?collect.split_card.due_cents-typedCard.cents:null
    else if(splitMode==='tenders'){const part=centsOrNull(draft.split);amount=part!==null&&part<=quote.rest_cents?part:null}
    else amount=quote.rest_cents
  }
  const tender=draft.tender, cny=tender==='wechat'||tender==='alipay'
  const fen=cny&&amount?convertCadCentsToCnyFen(amount,cnyPerCad):null
  const ready=!busy&&!!quote&&POPCORE_TENDERS.includes(tender as typeof POPCORE_TENDERS[number])&&!!amount&&(!cny||fen!==null)
  const receive=()=>{if(ready&&quote&&amount)run('receive',{tender,amount_cents:amount,plan,expected_version:order.version,expected_due_cents:quote.due_cents,...(cny?{cny_fen:fen,cny_per_cad:cnyPerCad}:{})})}
  const chooseSplit=(mode:''|'card'|'tenders')=>setDraft({cloverSplit:mode,split:'',tender:mode&&tender==='card'?'':tender})
  const status=writebackStatus(clover,order.status)
  const photoAttempts=order.attempts.filter(a=>a.status==='completed'&&a.origin==='popcore'&&electronic(a.tender))
  const payments=order.attempts.filter(a=>a.status==='completed')
  const saving=total!==null&&quote&&quote.due_cents<total?quote:null
  const tenderPicker=(withPrices:boolean)=><fieldset className="co-methods" disabled={busy}><legend>{withPrices?'付款方式 How is the customer paying?':'收余款 Collect the rest by'}</legend><div>
    {tenderButtons.flatMap((m,i)=>[<button key={m.value} type="button" aria-pressed={tender===m.value} onClick={()=>setDraft({tender:m.value})}><TenderLabel method={m} amount={withPrices&&quote?formatCents(quote.rest_cents):undefined}/></button>,
      ...(i===0&&withPrices&&collect?[<button key="card" type="button" aria-pressed={tender==='card'} disabled={clover.card_blocked} onClick={()=>setDraft({tender:'card'})}><TenderLabel method={methods[1]} amount={formatCents(collect.card.rest_cents)}/></button>]:[])])}
    {withPrices&&<button type="button" aria-label="Split payment" onClick={()=>chooseSplit('card')}><span className="co-method-zh">拆单</span> <span className="co-method-en">Split w/ card</span><small>—</small></button>}
  </div>{clover.card_blocked&&withPrices&&<p className="co-muted">Card is off: a cash price is already entered on this Clover order.</p>}</fieldset>
  const received=<>{cny&&amount&&<section className="co-cny" aria-label="Chinese yuan collection amount"><span>{label(tender)} · CNY to collect</span>
      {fen!==null?<><strong>{cnyMoney.format(fen/100)}</strong><small>{formatCents(amount)} CAD at 1 CAD = {cnyPerCad} CNY{cnyRateSource?` (${cnyRateSource})`:''}. Check the amount the payment app shows.</small></>:<p>Ask an admin to set the CNY rate in Settings.</p>}</section>}
    {electronic(tender)&&amount&&<p className="co-muted">Check that the money arrived before tapping Received. Take the payment photo next.</p>}
    {tender&&tender!=='card'&&amount!==null&&<div className="co-payment-action"><Button type="primary" size="large" icon={<Icon name="check" size={18}/>} disabled={!ready} loading={mutation.saving} onClick={receive}>已收 {formatCents(amount)} · Received</Button><small>Only confirm money actually received. POPCORE then marks the Clover order paid.</small></div>}</>

  return <article className="co-order co-clover" aria-label={`Checkout ${order.reference}`}>
    <header className="co-order-heading"><div>
      <span className="co-muted">Clover order · {clock(clover.created_ms)}{clover.employee_name?` · ${clover.employee_name}`:''}</span>
      <h2 className="co-clover-id">{clover.order_id}</h2>
      <span className="co-muted">{clover.started_in_popcore?order.reference:clover.popcore_reference} · {order.status==='completed'?'Sale recorded':order.status==='cancelled'?'Cancelled':order.abandoned_reason?'Refund due':'In progress'} · {clover.register?'Not picked up yet':order.cashier_name}</span>
    </div><span className={'co-status co-clover-'+stateTone(clover.state)} aria-label={`Clover: ${clover.state_label}`}>{clover.state_label}</span></header>
    {mutation.notice}
    {clover.can_claim&&<div className="co-claim"><Button type="primary" disabled={busy} loading={mutation.saving} onClick={()=>run('claim')}>Pick up this order</Button></div>}
    {clover.review&&<Alert type="warning" showIcon message={reviewTitle(clover.review.reason)} description={clover.review.detail}/>}
    {open&&!clover.fresh&&<Alert type="info" showIcon message="Waiting for Clover" description={`Last read from Clover at ${new Date(clover.seen_at*1000).toLocaleTimeString()}. Amounts update when Clover answers.`}/>}
    {open&&clover.source==='device'&&<Alert type="info" showIcon message="Items from the Clover device" description="Card payments show here once Clover’s cloud confirms them."/>}
    {clover.delete_pending&&<Alert type="warning" showIcon message="Still open on Clover" description="Cancelled in POPCORE. POPCORE deletes it on Clover as soon as Clover answers." action={clover.can_retry&&<Button disabled={busy} onClick={()=>run('clover-retry')}>Retry</Button>}/>}
    <ul className="co-items">{groupLines(clover.lines).map(line=><li key={line.key}><span className="co-quantity">{line.quantity}×</span><span className="co-line-name">{line.name}{line.mapped?line.product_name&&line.product_name!==line.name&&<small className="co-muted"> · {line.product_name}</small>:<small className="co-unmapped"> · Not matched to a POPCORE product</small>}</span><span className="co-line-price">{formatCents(line.unit_price_cents)}</span></li>)}</ul>
    {!!clover.discounts.length&&<ul className="co-clover-discounts">{clover.discounts.map((d,i)=><li key={i}><span>{d.name}{d.popcore&&<small className="co-muted"> · added by POPCORE</small>}</span><span>{d.percentage!=null?`−${d.percentage}%`:formatCents(d.amount_cents)}</span></li>)}</ul>}
    <dl className="co-original"><div><dt>Clover total</dt><dd>{total===null?'Pending':formatCents(total)}</dd></div>{order.status==='completed'&&clover.due_cents!==null&&<div><dt>Customer paid</dt><dd>{formatCents(clover.due_cents)}</dd></div>}</dl>

    {clover.can_receive&&collect&&quote&&<section className="co-clover-pay" aria-label="Payment">
      {splitMode===''&&<>
        {tenderPicker(true)}
        {tender==='card'?<p className="co-instruction">Take <strong>{formatCents(collect.card.rest_cents)}</strong> by card on Clover: {popcoreIn||cardLanded?splitInstruction(collect.card.rest_cents):clover.started_in_popcore?<>Orders → <strong>{order.reference}</strong> → Add Payment → Card</>:'Pay → Card'}. POPCORE records the sale when Clover confirms it.</p>
          :tender&&<div className="co-collect"><span>应收 Collect</span><strong>{formatCents(quote.rest_cents)}</strong>{saving&&<small>{saving.label} {off(total!-saving.due_cents)} · Clover total {formatCents(total)}</small>}</div>}
        {received}
      </>}
      {splitMode==='card'&&<>
        <div className="co-section-title"><h3>Split with card</h3><span>{formatCents(collect.split_card.due_cents)}{collect.split_card.label&&total!==null&&collect.split_card.due_cents<total?` · ${collect.split_card.label} ${off(total-collect.split_card.due_cents)}`:''}</span></div>
        {cardLanded?<p className="co-saved" role="status"><CheckCircleOutlined/> Card {formatCents(clover.clover_cents)} received on Clover</p>
          :popcoreIn?<p className="co-instruction">Now take <strong>{formatCents(collect.card.rest_cents)}</strong> by card on Clover: {splitInstruction(collect.card.rest_cents)}. POPCORE finishes the order when Clover confirms it.</p>
          :<>
            <label className="co-card-part">Card part (CAD)<Input inputMode="decimal" value={draft.split} disabled={busy} onChange={e=>setDraft({split:e.target.value})}/></label>
            {typedCard?.error&&<p className="co-unmapped" role="alert">{typedCard.error}</p>}
            {typedCard?.cents&&<p className="co-instruction">1. On Clover: <strong>{splitInstruction(typedCard.cents)}</strong></p>}
          </>}
        {amount!==null&&<>{tenderPicker(false)}<div className="co-collect"><span>{cardLanded?'Collect the rest':'2. Collect here'}</span><strong>{formatCents(amount)}</strong></div>{received}</>}
        <div className="co-action-pair"><Button type="link" disabled={busy} onClick={()=>chooseSplit('tenders')}>Split without card</Button><Button type="link" disabled={busy} onClick={()=>chooseSplit('')}>{popcoreIn?'Customer pays the rest another way':'No split'}</Button></div>
      </>}
      {splitMode==='tenders'&&<>
        <div className="co-section-title"><h3>Split between tenders</h3><span>{formatCents(quote.rest_cents)} left{saving?` · ${saving.label} ${off(total!-saving.due_cents)}`:''}</span></div>
        <label className="co-card-part">Collect now (CAD)<Input inputMode="decimal" value={draft.split} disabled={busy} onChange={e=>setDraft({split:e.target.value})}/></label>
        {centsOrNull(draft.split)!==null&&amount===null&&<p className="co-unmapped" role="alert">Enter at most {formatCents(quote.rest_cents)}.</p>}
        {tenderPicker(false)}{received}
        <div className="co-action-pair"><Button type="link" disabled={busy} onClick={()=>chooseSplit('card')}>Split with card</Button><Button type="link" disabled={busy} onClick={()=>chooseSplit('')}>No split</Button></div>
      </>}
    </section>}

    {open&&!clover.can_receive&&!clover.can_claim&&<p className="co-muted">{clover.review?'A manager resolves this order before more money is recorded.':total===null?'Waiting for Clover’s total.':`View only. ${clover.register?'Recording payments needs an assigned shift at this location today.':`${order.cashier_name} has this order.`}`}</p>}
    {!!payments.length&&<section className="co-receipts"><h3>Payments</h3>{payments.map(a=><div key={a.id} className="co-receipt"><p><span>{label(a.tender)}<small className="co-muted"> · {a.origin==='clover'?'on Clover':clover.writeback.mode==='pay'&&clover.writeback.state==='done'?'recorded in POPCORE · on Clover':'recorded in POPCORE'}</small></span><strong>{formatCents(a.amount_cents)}</strong></p>{cnyCharged(a)&&<small className="co-receipt-cny">{cnyCharged(a)}</small>}{clover.undo_attempt_ids.includes(a.id)&&<TakeBack attempt={a} disabled={busy} run={body=>run('undo',body)}/>}</div>)}</section>}
    {photoAttempts.map(attempt=><PhotoCapture key={attempt.id} disabled={busy} primary={attempt.id===photoAttempts.find(a=>a.can_upload&&!a.photos.length)?.id} order={order} attempt={attempt} draft={draft} setDraft={setDraft} updated={refresh} onBusy={value=>setPhotoStates(old=>({...old,[attempt.id]:value}))}/>)}
    {finishedHere&&!clover.started_in_popcore&&clover.writeback.mode==='label'&&!['PAID','DELETED'].includes(clover.state)&&<p className="co-instruction">On Clover: {clover.clover_cents?<>tap <strong>Pay more later</strong></>:<>tap <strong>Review Order</strong>, then <strong>Save</strong></>} to clear the screen. The order stays open there, labelled paid.</p>}
    {status&&(status.tone==='success'?<p className="co-saved co-clover-done" role="status"><CheckCircleOutlined/> <strong>{status.title}</strong><span>{status.detail}</span></p>
      :<Alert type={status.tone} showIcon message={status.title} description={status.detail} action={clover.can_retry&&<Button disabled={busy} loading={mutation.saving} onClick={()=>run('clover-retry')}>Retry</Button>}/>)}
    {clover.can_mark_handled&&<details className="co-secondary"><summary>Fixed on the Clover device instead</summary><p>Only when the Clover order now matches this sale.</p><Input aria-label="What was fixed on Clover" value={handled} maxLength={200} onChange={e=>setHandled(e.target.value)}/><Button disabled={busy||!handled.trim()} onClick={()=>run('clover-handled',{reason:handled.trim()})}>Mark handled</Button></details>}
    {clover.can_release&&<Button className="co-release" type="link" disabled={busy} onClick={()=>run('release')}>Release order</Button>}
    <OrderNotes order={order} updated={updated} disabled={busy} onBusy={setNoteBusy}/>
    {order.received_cents>0&&<RefundTools order={order} disabled={busy} updated={updated} onBusy={setRefundBusy}/>}
    {clover.can_cancel&&<details className="co-secondary"><summary>Cancel this order</summary><p>The unpaid order is deleted on Clover too.</p>
      <div className="co-action-pair"><Button danger disabled={busy} loading={mutation.saving} onClick={()=>run('cancel')}>Cancel order</Button>{clover.can_cancel_local&&<Button disabled={busy} onClick={()=>run('cancel',{local:true})}>Cancel in POPCORE only</Button>}</div>
      {clover.can_cancel_local&&<small className="co-muted">Use “POPCORE only” when Clover cannot be reached. POPCORE deletes it on Clover later.</small>}</details>}
  </article>
}
