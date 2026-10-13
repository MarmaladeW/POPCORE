import { Alert, Button, Drawer, Skeleton, Space } from 'antd'
import { useEffect, useRef, useState } from 'react'
import client from '../../api/client'
import type { SeriesBalance, SeriesInventoryResult, SeriesProduct } from '../../api/inventorySeries'
import useCheckoutMutation from '../Sales/useCheckoutMutation'
import { torontoDate } from '../Dashboard/todayPresentation'
import './seriesInventory.css'

interface Conversion { id:number; target_product_id:number; output_per_input:number; version:number }
interface Identity {
  id:number; series_id:number|null; stock_form:string|null; stock_unit:string|null
  identity_status:string; conversions:Conversion[]
}
interface Props {
  kind:'move'|'open_set'; product:SeriesProduct; data:SeriesInventoryResult
  onClose:()=>void; onSaved:()=>void
}
const units = (count:number, unit:string) => `${count} ${unit==='box' ? count===1?'box':'boxes' : `${unit}${count===1?'':'s'}`}`
const balanceAt = (product:SeriesProduct|undefined, location:number|undefined) => product?.balances.find(row=>row.location_id===location&&row.disposition==='saleable')
const trusted = (balance:SeriesBalance|undefined):balance is SeriesBalance & {quantity:number;version:number} => !!balance
  && balance.quantity!=null && Number.isSafeInteger(balance.quantity) && balance.quantity>=0
  && balance.version!=null && Number.isSafeInteger(balance.version) && balance.version>=0

export default function SeriesStockAction({kind,product,data,onClose,onSaved}:Props) {
  const [fromId,setFromId] = useState<number>(), [toId,setToId] = useState<number>()
  const [quantity,setQuantity] = useState('1'), [reason,setReason] = useState('')
  const [conversionId,setConversionId] = useState<number>(), [purpose,setPurpose] = useState('')
  const [identity,setIdentity] = useState<Identity>(), [loadError,setLoadError] = useState(''), [reload,setReload] = useState(0)
  const [denied,setDenied] = useState(false)
  const feedback = useRef<HTMLDivElement>(null)
  const mutation = useCheckoutMutation(()=>onSaved())
  useEffect(()=>{if(mutation.error)feedback.current?.focus()},[mutation.error])
  const locked = mutation.saving || mutation.pending
  const opening = kind==='open_set'
  useEffect(()=>{
    window.dispatchEvent(new CustomEvent('popcore:checkout-busy',{detail:locked}))
    return ()=>{window.dispatchEvent(new CustomEvent('popcore:checkout-busy',{detail:false}))}
  },[locked])
  useEffect(()=>{
    const reject = ()=>setDenied(true)
    window.addEventListener('popcore:checkout-access-denied',reject)
    return ()=>window.removeEventListener('popcore:checkout-access-denied',reject)
  },[])
  useEffect(()=>{
    if (!opening)return
    const controller = new AbortController()
    setIdentity(undefined);setLoadError('');setConversionId(undefined)
    client.get<Identity>(`/products/${product.id}/inventory-identity`,{signal:controller.signal})
      .then(response=>{if(!controller.signal.aborted)setIdentity(response.data)})
      .catch(cause=>{if(!controller.signal.aborted)setLoadError(cause?._serverMessage || 'Unable to load reviewed conversions.')})
    return ()=>controller.abort()
  },[opening,product.id,reload])

  const locations = data.locations.filter(location=>location.opening_verified)
  const from = locations.find(location=>location.id===fromId)
  const to = opening ? from : locations.find(location=>location.id===toId)
  const source = balanceAt(product,fromId)
  const products = data.series.flatMap(series=>series.products)
  const conversions = identity?.id===product.id && identity.stock_form==='sealed_set' && identity.stock_unit==='set'
    && identity.identity_status==='verified' && identity.series_id===product.series_id ? identity.conversions.filter(conversion=>{
      const target = products.find(candidate=>candidate.id===conversion.target_product_id)
      return Number.isSafeInteger(conversion.id) && conversion.id>0 && Number.isSafeInteger(conversion.output_per_input) && conversion.output_per_input>0
        && Number.isSafeInteger(conversion.version) && conversion.version>0 && target?.identity_status==='verified'
        && target.stock_form==='random_box' && target.stock_unit==='box' && target.series_id===product.series_id
    }) : []
  const conversion = conversions.find(item=>item.id===conversionId)
  const targetProduct = opening ? products.find(candidate=>candidate.id===conversion?.target_product_id) : product
  const target = balanceAt(targetProduct,to?.id)
  const retained = product.open_sets?.filter(item=>item.location_id===fromId).reduce((sum,item)=>sum+item.remaining_qty,0)
  const loose = product.stock_form==='random_box' ? retained==null ? null : Math.max(0,(source?.quantity||0)-retained) : source?.quantity
  const available = opening ? source?.quantity : loose
  const count = Number(quantity)
  const singleStore = new Set(data.locations.map(location=>location.store_id)).size===1
  const unit = product.stock_unit || 'unit'
  const formUnits:Record<string,string> = {sealed_set:'set',random_box:'box',confirmed_design:'piece',ordinary:'piece'}
  const validIdentity = product.identity_status==='verified' && formUnits[product.stock_form||'']===product.stock_unit
  const ready = !denied && data.mode==='authoritative' && singleStore && validIdentity && !!from && !!to
    && from.store_id===to.store_id && trusted(source) && trusted(target) && available!=null
    && Number.isSafeInteger(count) && count>0 && count<=available && !!reason.trim()
    && (opening ? product.stock_form==='sealed_set' && !!conversion && Number.isSafeInteger(count*conversion.output_per_input)
      && ['customer_tray','replenishment'].includes(purpose) : from.id!==to.id)

  function save() {
    if (!ready || !from || !to || !trusted(source) || !trusted(target))return
    void mutation.run('/inventory/commands',{
      kind,business_date:torontoDate(),reason:reason.trim(),lines:[{
        product_id:product.id,unit:product.stock_unit,quantity:count,
        from_location_id:from.id,to_location_id:to.id,
        from_disposition:'saleable',to_disposition:'saleable',
        expected_versions:{from:source.version,to:target.version},
        ...(opening&&conversion ? {conversion_id:conversion.id,conversion_factor:conversion.output_per_input,purpose} : {}),
      }],
    })
  }
  const close = ()=>{if(!locked)onClose()}
  return <Drawer rootClassName="pc-inventory-drawer" title={opening?'Open a sealed set':'Move stock'} width="min(100%, 560px)" open
    onClose={close} keyboard={!locked} maskClosable={!locked} closable={!locked}
    footer={<Space wrap><Button type="primary" aria-label={opening?'Confirm opening':'Confirm move'} disabled={locked||!ready} loading={mutation.saving} onClick={save}>{opening?'Confirm opening':'Confirm move'}</Button><Button disabled={locked} onClick={close}>Cancel</Button></Space>}>
    <div className="pc-series-form">
      <div ref={feedback} tabIndex={-1}>{mutation.notice}</div>
      <p><strong>{product.name}</strong><br/>{opening?'Open the selected sealed sets into unidentified boxes using their reviewed conversion.':'Move saleable stock between reviewed locations in this store.'}</p>
      {(data.mode!=='authoritative'||!singleStore||!validIdentity)&&<Alert type="warning" showIcon message="Select one store with verified product identity and reviewed opening stock before continuing."/>}
      {denied&&<p>Access was denied. Close this drawer and check your store access before continuing.</p>}
      {mutation.error&&!mutation.pending&&!denied&&<p>Close this drawer and refresh inventory if stock has changed before trying again.</p>}
      {opening&&loadError&&<Alert type="error" message={loadError} action={<Button disabled={locked} onClick={()=>setReload(value=>value+1)}>Retry conversion list</Button>}/>}
      {opening&&!identity&&!loadError&&<Skeleton active paragraph={{rows:2}}/>}
      {opening&&identity&&!conversions.length&&<Alert type="warning" message="No reviewed conversion is available for a verified unidentified-box product in this series. Ask a manager to review the product conversion."/>}
      <fieldset disabled={locked||denied}>
        {opening&&<label>Reviewed conversion<select aria-label="Reviewed conversion" value={conversionId||''} onChange={event=>setConversionId(Number(event.target.value)||undefined)} disabled={!conversions.length}>
          <option value="">Choose the reviewed pack conversion</option>{conversions.map(item=><option key={item.id} value={item.id}>1 set → {units(item.output_per_input,'box')} · {products.find(candidate=>candidate.id===item.target_product_id)?.name} · Review {item.version}</option>)}
        </select></label>}
        <label>{opening?'Location':'From location'}<select aria-label={opening?'Location':'From location'} value={fromId||''} onChange={event=>setFromId(Number(event.target.value)||undefined)}>
          <option value="">Choose a reviewed location</option>{locations.map(location=><option key={location.id} value={location.id}>{location.name}</option>)}
        </select></label>
        {!opening&&<label>To location<select aria-label="To location" value={toId||''} onChange={event=>setToId(Number(event.target.value)||undefined)}>
          <option value="">Choose the destination</option>{locations.filter(location=>location.id!==fromId).map(location=><option key={location.id} value={location.id}>{location.name}</option>)}
        </select></label>}
        {opening&&<label>Purpose<select aria-label="Purpose" value={purpose} onChange={event=>setPurpose(event.target.value)}>
          <option value="">Choose why the set is being opened</option><option value="customer_tray">Customer tray</option><option value="replenishment">Replenish loose boxes</option>
        </select></label>}
        <label>{opening?'Sets to open':'Quantity to move'}<input aria-label={opening?'Sets to open':'Quantity to move'} type="number" min="1" step="1" max={available??undefined} value={quantity} onChange={event=>setQuantity(event.target.value)}/></label>
        <label>Reason<textarea aria-label="Reason" rows={3} value={reason} maxLength={1000} onChange={event=>setReason(event.target.value)}/></label>
      </fieldset>
      {from&&<p>{available==null?'Quantity has not been verified.':`${units(available,unit)} ${!opening&&product.stock_form==='random_box'?'loose and ':''}available at ${from.name}.`}</p>}
      {!opening&&product.stock_form==='random_box'&&<p>Retained opened sets stay tracked separately. Use the goods transfer workflow to move those sets.</p>}
      {Number.isSafeInteger(count)&&count>0&&from&&to&&(!opening||conversion)&&<p className="pc-series-effect" aria-live="polite">{opening&&conversion ? `−${units(count,'set')} → +${units(count*conversion.output_per_input,'box')}` : `−${units(count,unit)} at ${from.name} → +${units(count,unit)} at ${to.name}`}</p>}
      {opening&&conversion&&from&&<p>{targetProduct?.name} will remain unidentified and tracked at {from.name}.</p>}
    </div>
  </Drawer>
}
