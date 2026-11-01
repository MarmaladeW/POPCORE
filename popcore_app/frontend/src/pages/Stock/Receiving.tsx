import { productLabel } from '../../lib/productLabel'
import { Alert,Button,Card,Form,Input,InputNumber,Select,Space,Spin,Typography } from 'antd'
import { useEffect,useRef,useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import OperationScanInput from '../../components/OperationScanInput'
import GoodsProductPicker from '../../components/GoodsProductPicker'
import client from '../../api/client'
import { cancelReceipt,createReceipt,getReceipt,postReceipt,requestKey,loadGoodsProductsById,resolveGoodsBarcode,updateReceipt,type GoodsProduct,type InventoryLocation,type ReceiptDetail,type ReceiptLine } from '../../api/goods'
import { addReceiptLine,receiptLineBody,receiptLineTotal,unitTotals,unreceivable } from '../../lib/goodsLines'
import { torontoDate } from '../Dashboard/todayPresentation'

const failure=(cause:unknown,fallback:string)=>(cause as {response?:{data?:{error?:string}}})?.response?.data?.error||fallback
const conflict=(cause:unknown)=>(cause as {response?:{status?:number}})?.response?.status===409
const rejected=(cause:unknown)=>{const status=(cause as {response?:{status?:number}})?.response?.status;return !!status&&status>=400&&status<500}
const effect=(lines:ReceiptLine[])=>unitTotals(lines.map(line=>({unit:line.native_unit,quantity:receiptLineTotal(line)})))||'nothing yet'
type ReceiptAction='update'|'post'|'cancel'
type ReceiptIntent={action:ReceiptAction;key:string;receiptId:number;version:number;body?:object}

function ReceiptLines({lines,products,locked,onChange,onRemove}:{lines:ReceiptLine[];products:GoodsProduct[];locked:boolean;onChange:(lineNo:number,change:Partial<ReceiptLine>)=>void;onRemove?:(lineNo:number)=>void}){
  return <div className="pc-receipt-lines">{lines.map(line=>{const total=receiptLineTotal(line);const short=line.expected_quantity!==null&&line.expected_quantity!==total;return <section key={line.line_no} className="pc-receipt-line" aria-label={`Line ${line.line_no}`}>
    <div className="pc-receipt-line-head"><strong className="pc-receipt-product">{productLabel(products.find(item=>item.id===line.product_id)||{id:line.product_id})}</strong>{onRemove&&<Button size="small" type="text" disabled={locked} aria-label={`Remove line ${line.line_no}`} onClick={()=>onRemove(line.line_no)}>移除 Remove</Button>}</div>
    <div className="pc-receipt-cells">
      <label><span>应到 Expected</span><InputNumber aria-label={`Line ${line.line_no} expected`} disabled={locked} min={0} precision={0} value={line.expected_quantity} onChange={value=>onChange(line.line_no,{expected_quantity:value})}/></label>
      {([['saleable','实到 · 完好','Saleable'],['damaged','损坏','Damaged'],['hold','暂扣','Hold']] as const).map(([kind,zh,en])=><label key={kind} className={kind==='saleable'?'pc-receipt-actual':undefined}><span>{zh} {en}</span><InputNumber aria-label={`Line ${line.line_no} ${kind}`} disabled={locked} min={0} precision={0} value={line[`${kind}_quantity`]} onChange={value=>onChange(line.line_no,{[`${kind}_quantity`]:value||0})}/></label>)}
    </div>
    {short&&<p className="pc-receipt-short"><i className="pc-dot pc-dot-warn" aria-hidden="true"/>{total<line.expected_quantity!?`少 ${line.expected_quantity!-total}`:`多 ${total-line.expected_quantity!}`} · Shipment quantity differs</p>}
    <label className="pc-receipt-note"><span>差异说明 Discrepancy</span><Input aria-label={`Line ${line.line_no} discrepancy`} disabled={locked} value={line.discrepancy_note||''} onChange={event=>onChange(line.line_no,{discrepancy_note:event.target.value||null})}/></label>
    <p className="pc-receipt-effect"><span>{line.saleable_quantity} {line.native_unit}s saleable</span><span>{line.damaged_quantity} {line.native_unit}s damaged</span><span>{line.hold_quantity} {line.native_unit}s hold</span></p>
  </section>})}</div>
}

export default function Receiving({products,locations,storeId}:{products:GoodsProduct[];locations:InventoryLocation[];storeId:number}){
  const[params,setParams]=useSearchParams(),resumeId=Number(params.get('receipt_id'))
  const[receipt,setReceipt]=useState<ReceiptDetail>()
  const[productOptions,setProductOptions]=useState(products)
  const[savedId,setSavedId]=useState<number>(),[pendingCreate,setPendingCreate]=useState(false)
  // The new receipt's lines, in the same shape as a saved draft's.
  const[lines,setLines]=useState<ReceiptLine[]>([]),[locationId,setLocationId]=useState<number>()
  const[reference,setReference]=useState('')
  const[error,setError]=useState(''),[loading,setLoading]=useState(false)
  const[refreshNeeded,setRefreshNeeded]=useState(false)
  // Scans resolve after a lookup and apply to the latest lines; saving waits until none is in flight.
  const[scanning,setScanning]=useState(0)
  const keys=useRef({create:requestKey()})
  const createIntent=useRef<{key:string;body:object}|null>(null)
  const actionIntent=useRef<ReceiptIntent|null>(null)
  const[pendingAction,setPendingAction]=useState<ReceiptAction>()
  const busy=useRef(false)
  const savedDraft=useRef('')

  async function load(id:number,signal?:AbortSignal){
    const detail=await getReceipt(id,signal)
    const resolved=await loadGoodsProductsById(productOptions,detail.lines.map(line=>line.product_id),signal)
    if(signal?.aborted)return
    setProductOptions(resolved)
    savedDraft.current=JSON.stringify(draftBody(detail));setReceipt(detail);setLocationId(detail.destination_location_id);setReference(detail.shipment_reference||'');setRefreshNeeded(false)
  }
  useEffect(()=>{setReceipt(undefined);setError('');if(!params.has('receipt_id'))return;if(!Number.isInteger(resumeId)||resumeId<1){setError('Receipt reference is invalid.');return}const controller=new AbortController();setLoading(true);load(resumeId,controller.signal).catch(cause=>{if(!controller.signal.aborted)setError(failure(cause,'Unable to load this receipt.'))}).finally(()=>{if(!controller.signal.aborted)setLoading(false)});return()=>controller.abort()},[params,resumeId])
  useEffect(()=>setProductOptions(products),[products])
  useEffect(()=>{
    if(params.has('receipt_id')||!params.has('product_id'))return
    const requested=Number(params.get('product_id'))
    setLines([])
    if(!Number.isInteger(requested)||requested<1){setError('Product reference is invalid.');return}
    const controller=new AbortController()
    client.get<GoodsProduct>(`/products/${requested}`,{signal:controller.signal}).then(({data})=>{
      if(controller.signal.aborted)return
      const reason=unreceivable(data)
      if(reason){setError(`${productLabel(data)} cannot be received: ${reason}.`);return}
      setProductOptions(current=>[...current.filter(item=>item.id!==data.id),data]);setLines(current=>addReceiptLine(current,data.id,data.stock_unit!,0))
    }).catch(()=>{if(!controller.signal.aborted)setError('Unable to load this product. Search for the exact design before receiving.')})
    return()=>controller.abort()
  },[params])
  useEffect(()=>{
    const requested=Number(params.get('location_id'))
    if(!params.has('receipt_id')&&locations.some(location=>location.id===requested))setLocationId(requested)
  },[params,locations])

  // Changes go to the open draft, or to the new receipt's lines before it is saved.
  function editLines(apply:(current:ReceiptLine[])=>ReceiptLine[]){
    if(receipt)setReceipt(current=>current&&current.id===receipt.id?{...current,lines:apply(current.lines)}:current)
    else setLines(apply)
  }
  async function scan(code:string){
    setScanning(count=>count+1)
    try{
      const result=await resolveGoodsBarcode(code)
      if(result.status!=='exact'){setError(result.status==='unknown'?'Unknown barcode. The receipt is unchanged.':'Barcode is ambiguous. Select the exact product.');return}
      const candidate=result.candidates[0]
      setProductOptions(current=>current.some(item=>item.id===candidate.product_id)?current:[...current,{id:candidate.product_id,sku:code,jizhanming:`Product reference ${candidate.product_id}`,stock_unit:candidate.stock_unit,identity_status:'verified'}])
      // Applied to the latest lines, so overlapping scans and edits typed during the lookup all count.
      editLines(current=>addReceiptLine(current,candidate.product_id,candidate.stock_unit,candidate.quantity_per_scan))
    }catch{setError('Unable to resolve the scan. The receipt is unchanged.')}
    finally{setScanning(count=>count-1)}
  }
  function pick(product:GoodsProduct){
    setProductOptions(current=>current.some(item=>item.id===product.id)?current:[...current,product])
    editLines(current=>addReceiptLine(current,product.id,product.stock_unit!,1))
  }
  const changeLine=(lineNo:number,change:Partial<ReceiptLine>)=>editLines(current=>current.map(line=>line.line_no===lineNo?{...line,...change}:line))
  const removeLine=(lineNo:number)=>editLines(current=>current.filter(line=>line.line_no!==lineNo))
  function draftBody(detail:ReceiptDetail){return {store_id:detail.store_id,destination_location_id:detail.destination_location_id,business_date:detail.business_date,shipment_reference:detail.shipment_reference,supplier:detail.supplier,lines:detail.lines.map(receiptLineBody)}}
  // A known shortfall needs a note: the new receipt fills a plain one, which the saved draft can refine.
  async function saveNew(){if(busy.current||!lines.length||!locationId)return;busy.current=true;setLoading(true);setError('');let createdId:number|undefined;const body={store_id:storeId,destination_location_id:locationId,business_date:torontoDate(),shipment_reference:reference||null,lines:lines.map(line=>({...receiptLineBody(line),discrepancy_note:line.discrepancy_note||(line.expected_quantity!==null&&line.expected_quantity!==receiptLineTotal(line)?'Shipment quantity differs':null)}))};const intent=createIntent.current||{key:keys.current.create,body};createIntent.current=intent;setPendingCreate(true);try{const result=await createReceipt(intent.body,intent.key);createdId=result.id;setSavedId(result.id);setPendingCreate(false);setLines([]);setParams({receipt_id:String(result.id)},{replace:true});await load(result.id);setSavedId(undefined);createIntent.current=null;keys.current.create=requestKey()}catch(cause){if(!createdId&&rejected(cause)){createIntent.current=null;keys.current.create=requestKey();setPendingCreate(false)}setError(createdId?'Receipt saved; refresh needed. No second draft will be created.':failure(cause,'Receipt result is unconfirmed. Retry sends the identical draft.'))}finally{busy.current=false;setLoading(false)}}
  async function runAction(intent:ReceiptIntent){if(busy.current)return;busy.current=true;setLoading(true);setError('');try{const result=intent.action==='update'?await updateReceipt(intent.receiptId,intent.body!,intent.key):intent.action==='post'?await postReceipt(intent.receiptId,intent.version,intent.key):await cancelReceipt(intent.receiptId,intent.version,intent.key);actionIntent.current=null;setPendingAction(undefined);try{await load(result.id)}catch{setRefreshNeeded(true);setError(`Receipt ${intent.action} saved; refresh needed before another action.`)}}catch(cause){if(rejected(cause)){actionIntent.current=null;setPendingAction(undefined)}if(conflict(cause)){try{await load(intent.receiptId)}catch{setRefreshNeeded(true);setError('Receipt changed and could not be refreshed. Retry refresh before another action.');return}}setError(failure(cause,'Receipt result is unconfirmed. Retry sends the identical request.'))}finally{busy.current=false;setLoading(false)}}
  function saveDraft(){if(!receipt)return;const intent=actionIntent.current||{action:'update' as const,key:requestKey(),receiptId:receipt.id,version:receipt.version,body:{expected_version:receipt.version,...draftBody(receipt)}};actionIntent.current=intent;setPendingAction(intent.action);void runAction(intent)}
  function transition(action:'post'|'cancel'){if(!receipt)return;const intent=actionIntent.current||{action,key:requestKey(),receiptId:receipt.id,version:receipt.version};actionIntent.current=intent;setPendingAction(intent.action);void runAction(intent)}
  const receiptDirty=!!receipt&&JSON.stringify(draftBody(receipt))!==savedDraft.current
  const emptyLine=(receipt?.lines||lines).some(line=>receiptLineTotal(line)<1)

  if(loading&&!receipt)return <Card title="Receive shipment"><Spin/></Card>
  if(savedId&&!receipt)return <Card title={`Receipt #${savedId}`}><Alert type="warning" showIcon message="Receipt saved; refresh needed." action={<Button onClick={async()=>{setLoading(true);try{await load(savedId);setSavedId(undefined);createIntent.current=null;keys.current.create=requestKey()}catch(cause){setError(failure(cause,'Unable to refresh the saved receipt.'))}finally{setLoading(false)}}}>Retry refresh</Button>}/>{error&&<Alert type="error" showIcon message={error}/>}</Card>
  if(refreshNeeded&&receipt)return <Card title={`Receipt #${receipt.id}`}><Alert type="warning" showIcon message={error} action={<Button onClick={()=>load(receipt.id)}>Retry refresh</Button>}/></Card>
  if(params.has('receipt_id')&&!receipt)return <Card title="Receive shipment"><Alert type="error" showIcon message={error||'Unable to load this receipt.'}/></Card>
  if(receipt){const draft=receipt.status==='draft',locked=!!pendingAction||!draft,location=locations.find(item=>item.id===receipt.destination_location_id)?.name||`Location reference ${receipt.destination_location_id}`;return <Card className="pc-goods-card" title={<>收货单 <span>{`Receipt #${receipt.id}`}</span></>}><Space direction="vertical" style={{width:'100%'}}>{error&&<Alert type="error" showIcon message={error} action={pendingAction?<Button onClick={()=>runAction(actionIntent.current!)}>Retry identical request</Button>:undefined}/>}<Typography.Text>{location} · {receipt.business_date}</Typography.Text><Typography.Text>{receipt.shipment_reference||'No shipment reference'}</Typography.Text><OperationScanInput onScan={scan} disabled={loading||locked}/>{draft&&<GoodsProductPicker products={products} onPick={pick} disabled={locked}/>}<ReceiptLines lines={receipt.lines} products={productOptions} locked={locked} onChange={changeLine} onRemove={draft&&receipt.lines.length>1?removeLine:undefined}/>{draft&&<Typography.Text className="pc-receipt-effect">Effect after posting: {effect(receipt.lines)} at {location}.</Typography.Text>}<Typography.Text strong>Status: {receipt.status}</Typography.Text>{draft&&<div className="pc-receipt-actions"><Button disabled={!!pendingAction||scanning>0||emptyLine} title={emptyLine?'Each product needs a quantity of at least 1':undefined} onClick={saveDraft}>存草稿 Save draft changes</Button><Button type="primary" disabled={!!pendingAction||receiptDirty||scanning>0} title={receiptDirty?'Save draft changes before posting':undefined} onClick={()=>transition('post')}>入库 Post receipt</Button><Button danger disabled={!!pendingAction} onClick={()=>transition('cancel')}>取消草稿 Cancel draft</Button></div>}{receipt.status==='posted'&&<Alert type="success" showIcon message="Receipt posted to inventory."/>}</Space></Card>}
  return <Card className="pc-goods-card" title={<>收货 <span>Receive shipment</span></>}><Space direction="vertical" style={{width:'100%'}}>{error&&<Alert type="error" showIcon message={error}/>}<Form className="pc-receive-form" layout="vertical" disabled={pendingCreate}><div className="pc-receive-pair"><Form.Item label="单号 Shipment reference"><Input value={reference} onChange={event=>setReference(event.target.value)}/></Form.Item><Form.Item label="放到 Destination" required><Select value={locationId} onChange={setLocationId} options={locations.map(item=>({value:item.id,label:item.name}))}/></Form.Item></div></Form><OperationScanInput onScan={scan} disabled={loading||pendingCreate}/><GoodsProductPicker products={products} onPick={pick} disabled={pendingCreate}/>{lines.length?<ReceiptLines lines={lines} products={productOptions} locked={pendingCreate} onChange={changeLine} onRemove={removeLine}/>:<Typography.Text type="secondary">还没有产品，扫码或搜索加入。 No products yet: scan or search to add each product in this shipment.</Typography.Text>}<Typography.Text className="pc-receipt-effect">Effect after posting: {effect(lines)} at {locations.find(item=>item.id===locationId)?.name||'selected destination'}.</Typography.Text><Button className="pc-receive-save" type="primary" loading={loading} disabled={!lines.length||!locationId||emptyLine||scanning>0} title={emptyLine?'Each product needs a quantity of at least 1':undefined} onClick={saveNew}>保存草稿 Save draft for review</Button></Space></Card>
}
