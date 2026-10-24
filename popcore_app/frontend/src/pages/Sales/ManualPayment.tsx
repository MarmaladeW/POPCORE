import { Button, Input } from 'antd'
import { formatCents } from '../../lib/money'
import { convertCadCentsToCnyFen } from '../../lib/checkoutPricing'
import { centsOrNull } from './cloverOrderView'
import { cnyMoney, electronic, label, methods, TenderLabel, type Collect, type Draft, type DraftUpdate, type Order } from './checkoutParts'
import Icon from '../../components/pc/Icon'

const off = (cents:number) => `−${formatCents(cents)}`

/** Paying a manual order the way a Clover order is paid: the server prices every tender, the cashier
 *  taps it, collects and confirms. Card is recorded here, because a manual order has no Clover order. */
export default function ManualPayment({order,draft,setDraft,busy,saving,run,cnyPerCad,cnyRateSource}:{order:Order;draft:Draft;setDraft:(v:DraftUpdate)=>void;busy:boolean;saving:boolean;run:(name:string,body:object)=>void;cnyPerCad:string;cnyRateSource?:string|null}) {
  const pricing=order.pricing!, collect=pricing.collect!, total=pricing.total_cents
  const splitMode=draft.cloverSplit||''
  const tender=draft.tender
  const plan=splitMode==='card'?'split_card':'pay'
  const quote:Collect=splitMode==='card'?collect.split_card:tender==='card'?collect.card:collect.pay
  const part=splitMode?centsOrNull(draft.split):null
  const amount=splitMode?(part!==null&&part<=quote.rest_cents?part:null):quote.rest_cents
  const cny=tender==='wechat'||tender==='alipay'
  const fen=cny&&amount?convertCadCentsToCnyFen(amount,cnyPerCad):null
  const ready=!busy&&!!tender&&!!amount&&(!cny||fen!==null)&&!(splitMode==='tenders'&&tender==='card')
  const discountText=quote.label&&quote.due_cents<total?`${quote.label} ${off(total-quote.due_cents)}`:''
  const chooseSplit=(mode:''|'card'|'tenders')=>setDraft({cloverSplit:mode,split:'',tender:mode==='tenders'&&tender==='card'?'':tender})
  const receive=()=>{if(ready&&amount)run('receive',{tender,amount_cents:amount,plan,expected_due_cents:quote.due_cents,...(cny?{cny_fen:fen,cny_per_cad:cnyPerCad}:{})})}
  const picker=(withPrices:boolean,withCard:boolean)=><fieldset className="co-methods" disabled={busy}><legend>{withPrices?'付款方式 How is the customer paying?':'付款方式 Paid by'}</legend><div>
    {methods.filter(m=>withCard||m.value!=='card').map(m=><button key={m.value} type="button" aria-pressed={tender===m.value} onClick={()=>setDraft({tender:m.value})}><TenderLabel method={m} amount={withPrices?formatCents(m.value==='card'?collect.card.rest_cents:collect.pay.rest_cents):undefined}/></button>)}
    {withPrices&&<button type="button" aria-label="Split payment" onClick={()=>chooseSplit('card')}><span className="co-method-zh">拆单</span> <span className="co-method-en">Split w/ card</span><small>—</small></button>}
  </div></fieldset>
  const confirm=<>{cny&&amount&&<section className="co-cny" aria-label="Chinese yuan collection amount"><span>{label(tender)} · CNY to collect</span>
      {fen!==null?<><strong>{cnyMoney.format(fen/100)}</strong><small>{formatCents(amount)} CAD at 1 CAD = {cnyPerCad} CNY{cnyRateSource?` (${cnyRateSource})`:''}. Check the amount the payment app shows.</small></>:<p>No CNY rate is available. Ask an admin to set one in Settings.</p>}</section>}
    {tender==='card'&&amount&&<p className="co-instruction">Take the card payment on the card machine first, then tap Received.</p>}
    {electronic(tender)&&amount&&<p className="co-muted">Check that the money arrived before tapping Received. Take or upload the payment photo next.</p>}
    {tender&&amount!==null&&<div className="co-payment-action"><Button type="primary" size="large" icon={<Icon name="check" size={18}/>} disabled={!ready} loading={saving} onClick={receive}>已收 {formatCents(amount)} · Received</Button><small>Only confirm money actually received.</small></div>}</>
  return <section className="co-clover-pay co-clover" aria-label="Payment">
    {!splitMode&&<>
      {picker(true,true)}
      {tender&&<div className="co-collect"><span>应收 Collect</span><strong>{formatCents(quote.rest_cents)}</strong>{(discountText||quote.rest_cents!==total)&&<small>{[discountText,`Order total ${formatCents(total)}`].filter(Boolean).join(' · ')}</small>}</div>}
      {confirm}
    </>}
    {splitMode&&<>
      <div className="co-section-title"><h3>{splitMode==='card'?'Split with card':'Split without card'}</h3><span>{formatCents(quote.due_cents)}{discountText?` · ${discountText}`:''} · {formatCents(quote.rest_cents)} left</span></div>
      <label className="co-card-part">Collect now (CAD)<Input inputMode="decimal" value={draft.split} disabled={busy} onChange={e=>setDraft({split:e.target.value})}/></label>
      {centsOrNull(draft.split)!==null&&amount===null&&<p className="co-unmapped" role="alert">Enter at most {formatCents(quote.rest_cents)}.</p>}
      {picker(false,splitMode==='card')}{confirm}
      <div className="co-action-pair"><Button type="link" disabled={busy} onClick={()=>chooseSplit(splitMode==='card'?'tenders':'card')}>{splitMode==='card'?'Split without card':'Split with card'}</Button><Button type="link" disabled={busy} onClick={()=>chooseSplit('')}>No split</Button></div>
    </>}
  </section>
}
