import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Form, Input, Modal } from 'antd'
import { CameraOutlined, CheckCircleOutlined, CreditCardOutlined, DollarOutlined, UploadOutlined } from '@ant-design/icons'
import client from '../../api/client'
import { formatCents, parseMoneyToCents } from '../../lib/money'
import { useHasRole } from '../../auth/useRole'
import useCheckoutMutation from './useCheckoutMutation'
import type { CloverBlock } from './cloverOrderView'

// Shared by manual checkouts and Clover orders.
export type Store = { id:number; code:string; name:string }
export type Access = { business_date:string; role:string; live_stores:Store[]; history_stores:Store[]; cny_per_cad:string; cny_rate_source?:string|null; clover_store_id?:number|null; clover_environment?:string|null }
export type Collect = { due_cents:number; mode?:string|null; label?:string|null; rest_cents:number }
export type ManualPricing = { total_cents:number; card_cents:number; other_cents:number; can_receive:boolean; undo_attempt_ids:number[]; collect:{ pay:Collect; split_card:Collect; card:Collect }|null }
export type Photo = { id:number }
export type Attempt = { id:number; origin?:'clover'|'popcore'; tender:string; amount_cents:number; status:string; can_upload:boolean; photos:Photo[]; refundable_cents?:number; recorded_by?:string|null; cancel_reason?:string|null; provider_payment_id?:string|null; cny_fen?:number|null; cny_per_cad?:string|null }
export type Line = { product_name_snapshot:string; quantity:number; unit:string }
export type Order = { id:number; store_id:number; reference:string; register_name?:string; business_date:string; status:string; version:number; sale_id:number|null; cashier_name:string; cashier_sub:string; can_note:boolean; note:string; can_manage:boolean; can_process:boolean; can_refund:boolean; received_cents:number; remaining_cents:number; refunded_cents:number; refund_due_cents:number; abandoned_reason:string|null; attempts:Attempt[]; refunds:{id:number;amount_cents:number;reference:string;business_date:string;tender:string}[]; order:{ subtotal_cents:number;source_tax_cents:number;gross_cents:number;reduction_cents:number;collected_cents:number;lines:Line[] }; clover?:CloverBlock|null; pricing?:ManualPricing|null }
export type CloverStatus = { enabled:boolean; connected:boolean; environment?:string; writes?:boolean; fetched_at?:number; message?:string; server_ms?:number }
export type Queue = { orders:Order[]; next_before_id:number|null; clover:CloverStatus }
export type Draft = { target:string; tender:string; split:string; includesCard:boolean; sourceTotal?:number; photos?:Record<number,File>; cloverSplit?:''|'card'|'tenders' }
export type DraftUpdate = Partial<Draft> | ((draft:Draft)=>Draft)

export const methods = [{value:'cash',label:'Cash',icon:<DollarOutlined/>},{value:'card',label:'Card',icon:<CreditCardOutlined/>},{value:'e_transfer',label:'E-transfer'},{value:'wechat',label:'WeChat Pay'},{value:'alipay',label:'Alipay'}]
export const electronic = (method:string) => ['e_transfer','wechat','alipay'].includes(method)
export const moneyInput = (cents:number) => (cents/100).toFixed(2)
export const label = (method:string) => methods.find(m=>m.value===method)?.label || method
export const cnyMoney = new Intl.NumberFormat('zh-CN',{style:'currency',currency:'CNY'})
export const errorMessage = (cause:unknown) => (cause as {_serverMessage?:string})?._serverMessage || 'Unable to load checkout. Please retry.'

/** Take back a payment recorded by mistake, with the reason, before the sale is recorded. */
export function TakeBack({attempt,disabled,run}:{attempt:Attempt;disabled:boolean;run:(body:object)=>void}) {
  const [open,setOpen]=useState(false),[reason,setReason]=useState('')
  if(!open)return <Button type="link" disabled={disabled} onClick={()=>setOpen(true)}>Take back</Button>
  return <div className="co-take-back">
    <Input aria-label={`Why is the ${label(attempt.tender)} payment taken back?`} placeholder="Why? For example: gave the cash back" value={reason} maxLength={300} onChange={e=>setReason(e.target.value)}/>
    <div className="co-action-pair"><Button danger disabled={disabled||!reason.trim()} onClick={()=>run({attempt_id:attempt.id,reason:reason.trim()})}>Take back {formatCents(attempt.amount_cents)}</Button><Button disabled={disabled} onClick={()=>{setOpen(false);setReason('')}}>Keep it</Button></div>
  </div>
}

export function PhotoCapture({order,attempt,draft,setDraft,updated,onBusy,primary,disabled}:{order:Order;attempt:Attempt;primary:boolean;disabled:boolean;draft:Draft;setDraft:(v:DraftUpdate)=>void;updated:()=>void;onBusy:(v:boolean)=>void}) {
  // Take a new photo with the camera, or upload one that already exists (a screenshot, a saved picture).
  const cameraInput=useRef<HTMLInputElement>(null),uploadInput=useRef<HTMLInputElement>(null)
  const [source,setSource]=useState<'camera'|'upload'>('camera')
  const [preview,setPreview]=useState(''),[viewing,setViewing]=useState(''),[readError,setReadError]=useState('')
  const file=draft.photos?.[attempt.id]
  const viewingRequest=useRef<AbortController|null>(null)
  useEffect(()=>()=>viewingRequest.current?.abort(),[])
  const mutation=useCheckoutMutation(()=>{setDraft(old=>{const photos={...old.photos};delete photos[attempt.id];return {...old,photos}});updated()})
  useEffect(()=>{onBusy(mutation.pending);return()=>onBusy(false)},[mutation.pending])
  useEffect(()=>{if(!file){setPreview('');return}const url=URL.createObjectURL(file);setPreview(url);return()=>URL.revokeObjectURL(url)},[file])
  useEffect(()=>()=>{if(viewing)URL.revokeObjectURL(viewing)},[viewing])
  function choose(selected:File|undefined,from:'camera'|'upload') {
    if(!selected)return
    setSource(from);setDraft(old=>({...old,photos:{...old.photos,[attempt.id]:selected}}))
  }
  async function view(photo:Photo) {
    viewingRequest.current?.abort();const controller=new AbortController();viewingRequest.current=controller
    try {const response=await client.get(`/checkouts/${order.id}/evidence/${photo.id}`,{responseType:'blob',signal:controller.signal});if(controller.signal.aborted)return;setViewing(URL.createObjectURL(response.data));setReadError('')}
    catch(cause){if(controller.signal.aborted)return;setViewing('');setReadError(errorMessage(cause));const status=(cause as {response?:{status:number}})?.response?.status;if(status===401||status===403)window.dispatchEvent(new Event('popcore:checkout-access-denied'))}
  }
  return <section className="co-photo" aria-label={`Payment evidence for ${label(attempt.tender)}`}>
    <div className="co-section-title"><h3>Payment photo</h3><span>{label(attempt.tender)} · {formatCents(attempt.amount_cents)}{attempt.cny_fen!=null?` · ${cnyMoney.format(attempt.cny_fen/100)}`:''}</span></div>
    {mutation.notice}{readError&&<Alert type="error" message={readError}/>}
    {attempt.photos.length>0&&<div className="co-saved" role="status"><CheckCircleOutlined/> <strong>Photo saved</strong>{attempt.photos.map((photo,i)=><Button type="link" key={photo.id} onClick={()=>view(photo)}>View{i?' '+(i+1):''}</Button>)}</div>}
    {attempt.can_upload&&<>
      <input ref={cameraInput} className="co-file-input" type="file" aria-label="Payment evidence photo" accept="image/jpeg,image/png,image/webp" capture="environment" disabled={disabled||mutation.pending} onChange={e=>{choose(e.target.files?.[0],'camera');e.target.value=''}}/>
      <input ref={uploadInput} className="co-file-input" type="file" aria-label="Upload payment evidence" accept="image/jpeg,image/png,image/webp" disabled={disabled||mutation.pending} onChange={e=>{choose(e.target.files?.[0],'upload');e.target.value=''}}/>
      {preview?<div className="co-preview">
        <img src={preview} alt="Payment evidence preview"/>
        <p>{order.reference} · {label(attempt.tender)} · {formatCents(attempt.amount_cents)}</p>
        <div className="co-action-pair"><Button disabled={disabled||mutation.pending} onClick={()=>(source==='camera'?cameraInput:uploadInput).current?.click()}>{source==='camera'?'Retake':'Choose another'}</Button><Button type="primary" loading={mutation.saving} disabled={disabled||mutation.pending&&!mutation.saving} onClick={()=>{if(!file)return;const body=new FormData();body.append('image',file);mutation.run(`/checkouts/${order.id}/attempts/${attempt.id}/evidence`,body)}}>Use photo</Button></div>
      </div>:<div className={'co-photo-actions'+(primary&&!attempt.photos.length?' co-camera-primary':'')}>
        <Button aria-label={attempt.photos.length?'Take another photo':'Take photo'} className="co-camera" type={attempt.photos.length?'default':'primary'} icon={<CameraOutlined/>} disabled={disabled||mutation.pending} onClick={()=>cameraInput.current?.click()}>{attempt.photos.length?'Take another photo':'Take photo'}</Button>
        <Button aria-label={attempt.photos.length?'Upload another photo':'Upload photo'} className="co-camera" icon={<UploadOutlined/>} disabled={disabled||mutation.pending} onClick={()=>uploadInput.current?.click()}>{attempt.photos.length?'Upload another photo':'Upload photo'}</Button>
      </div>}
    </>}
    {!attempt.can_upload&&!attempt.photos.length&&<p className="co-muted">Photo missing. Checkout access is required to add evidence.</p>}
    <Modal title="Saved payment evidence" open={Boolean(viewing)} onCancel={()=>setViewing('')} footer={null} destroyOnClose><img src={viewing} alt="Saved payment evidence" style={{width:'100%'}}/></Modal>
  </section>
}

export function OrderNotes({order,updated,onBusy,disabled}:{order:Order;updated:(o?:Order)=>void;onBusy:(v:boolean)=>void;disabled:boolean}) {
  const [note,setNote]=useState(order.note||''),[base,setBase]=useState(order.note||''),[dirty,setDirty]=useState(false)
  const mutation=useCheckoutMutation((value:Order)=>{setNote(value.note);setBase(value.note);setDirty(false);updated(value)})
  useEffect(()=>{if(!dirty){setNote(order.note||'');setBase(order.note||'')}},[order.note,dirty])
  useEffect(()=>{onBusy(mutation.pending);return()=>onBusy(false)},[mutation.pending])
  return <section className="co-note" aria-label="Order note">
    <h3>Order note</h3>
    {order.can_note?<>
      <Input.TextArea aria-label="Order note" value={note} rows={3} maxLength={2000} showCount
        placeholder="Special payment, pickup, or customer circumstances" disabled={disabled}
        onChange={e=>{setNote(e.target.value);setDirty(e.target.value!==base)}}/>
      <div className="co-note-actions"><Button type="primary" disabled={disabled||!dirty} loading={mutation.saving}
        onClick={()=>mutation.run(`/checkouts/${order.id}/notes`,{note,expected_note:base})}>Save note</Button>
        {dirty&&<Button disabled={disabled} onClick={()=>{setNote(order.note||'');setBase(order.note||'');setDirty(false)}}>Discard changes</Button>}</div>
      {mutation.notice}
    </>:<p>{order.note||'No note added.'}</p>}
  </section>
}

export function RefundTools({order,updated,onBusy,disabled}:{order:Order;updated:(o?:Order)=>void;onBusy:(v:boolean)=>void;disabled:boolean}) {
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
