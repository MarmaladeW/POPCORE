import { productLabel } from '../../lib/productLabel'
import { Alert,Button,Card,Form,Input,InputNumber,Select,Space,Spin,Typography } from 'antd'
import { useEffect,useRef,useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import OperationScanInput from '../../components/OperationScanInput'
import client from '../../api/client'
import { cancelReceipt,createReceipt,getReceipt,postReceipt,requestKey,loadGoodsProductsById,resolveGoodsBarcode,searchGoodsProducts,updateReceipt,type GoodsProduct,type InventoryLocation,type ReceiptDetail,type ReceiptLine } from '../../api/goods'
import { torontoDate } from '../Dashboard/todayPresentation'

const failure=(cause:unknown,fallback:string)=>(cause as {response?:{data?:{error?:string}}})?.response?.data?.error||fallback
const conflict=(cause:unknown)=>(cause as {response?:{status?:number}})?.response?.status===409
const rejected=(cause:unknown)=>{const status=(cause as {response?:{status?:number}})?.response?.status;return !!status&&status>=400&&status<500}
type ReceiptAction='update'|'post'|'cancel'
type ReceiptIntent={action:ReceiptAction;key:string;receiptId:number;version:number;body?:object}

export default function Receiving({products,locations,storeId}:{products:GoodsProduct[];locations:InventoryLocation[];storeId:number}){
  const[params,setParams]=useSearchParams(),resumeId=Number(params.get('receipt_id'))
  const[receipt,setReceipt]=useState<ReceiptDetail>()
  const[productOptions,setProductOptions]=useState(products)
  const[savedId,setSavedId]=useState<number>(),[pendingCreate,setPendingCreate]=useState(false)
  const[productId,setProductId]=useState<number>(),[locationId,setLocationId]=useState<number>()
  const[quantity,setQuantity]=useState(0),[expected,setExpected]=useState<number|null>(null)
  const[reference,setReference]=useState(''),[disposition,setDisposition]=useState<'saleable'|'damaged'|'hold'>('saleable')
  const[error,setError]=useState(''),[loading,setLoading]=useState(false)
  const[refreshNeeded,setRefreshNeeded]=useState(false)
  // Scans resolve after a lookup: they read the latest selection from this ref, and saving waits until none is in flight.
  const selectedProduct=useRef<number>(),[scanning,setScanning]=useState(0)
  const chooseProduct=(id?:number)=>{selectedProduct.current=id;setProductId(id)}
  const keys=useRef({create:requestKey()})
  const createIntent=useRef<{key:string;body:object}|null>(null)
  const actionIntent=useRef<ReceiptIntent|null>(null)
  const[pendingAction,setPendingAction]=useState<ReceiptAction>()
  const busy=useRef(false)
  const savedDraft=useRef('')
  const product=productOptions.find(item=>item.id===productId)

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
    chooseProduct(undefined)
    if(!Number.isInteger(requested)||requested<1){setError('Product reference is invalid.');return}
    const controller=new AbortController()
    client.get<GoodsProduct>(`/products/${requested}`,{signal:controller.signal}).then(({data})=>{
      if(controller.signal.aborted)return
      setProductOptions(current=>[...current.filter(item=>item.id!==data.id),data]);chooseProduct(data.id)
    }).catch(()=>{if(!controller.signal.aborted)setError('Unable to load this product. Search for the exact design before receiving.')})
    return()=>controller.abort()
  },[params])
  useEffect(()=>{
    const requested=Number(params.get('location_id'))
    if(!params.has('receipt_id')&&locations.some(location=>location.id===requested))setLocationId(requested)
  },[params,locations])

  async function scan(code:string){
    setScanning(count=>count+1)
    try{
      const result=await resolveGoodsBarcode(code)
      if(result.status!=='exact'){setError(result.status==='unknown'?'Unknown barcode. The receipt is unchanged.':'Barcode is ambiguous. Select the exact product.');return}
      const candidate=result.candidates[0]
      if(receipt){
        // Apply the scan to the latest state of the receipt it was scanned for, so overlapping scans and edits typed during the lookup all count.
        setReceipt(current=>{
          if(!current||current.id!==receipt.id)return current
          const matched=current.lines.some(line=>line.product_id===candidate.product_id&&line.native_unit===candidate.stock_unit)
          const added:ReceiptLine={line_no:Math.max(0,...current.lines.map(line=>line.line_no))+1,product_id:candidate.product_id,native_unit:candidate.stock_unit,expected_quantity:null,saleable_quantity:candidate.quantity_per_scan,damaged_quantity:0,hold_quantity:0,discrepancy_note:null}
          return {...current,lines:matched?current.lines.map(line=>line.product_id===candidate.product_id&&line.native_unit===candidate.stock_unit?{...line,saleable_quantity:line.saleable_quantity+candidate.quantity_per_scan}:line):[...current.lines,added]}
        })
        return
      }
      setProductOptions(current=>current.some(item=>item.id===candidate.product_id)?current:[...current,{id:candidate.product_id,sku:code,jizhanming:`Product reference ${candidate.product_id}`,stock_unit:candidate.stock_unit,identity_status:'verified'}])
      const same=selectedProduct.current===candidate.product_id
      chooseProduct(candidate.product_id);setQuantity(current=>same?current+candidate.quantity_per_scan:candidate.quantity_per_scan)
    }catch{setError('Unable to resolve the scan. The receipt is unchanged.')}
    finally{setScanning(count=>count-1)}
  }
  async function searchProducts(value:string){if(value.trim()){const results=await searchGoodsProducts(value);setProductOptions(product&&!results.some(item=>item.id===product.id)?[product,...results]:results)}}
  function draftBody(detail:ReceiptDetail){return {store_id:detail.store_id,destination_location_id:detail.destination_location_id,business_date:detail.business_date,shipment_reference:detail.shipment_reference,supplier:detail.supplier,lines:detail.lines.map(line=>({product_id:line.product_id,unit:line.native_unit,expected_quantity:line.expected_quantity,saleable_quantity:line.saleable_quantity,damaged_quantity:line.damaged_quantity,hold_quantity:line.hold_quantity,discrepancy_note:line.discrepancy_note}))}}
  async function saveNew(){if(busy.current||!product?.stock_unit||!locationId)return;busy.current=true;setLoading(true);setError('');let createdId:number|undefined;const field=`${disposition}_quantity`;const body={store_id:storeId,destination_location_id:locationId,business_date:torontoDate(),shipment_reference:reference||null,lines:[{product_id:product.id,unit:product.stock_unit,expected_quantity:expected,saleable_quantity:0,damaged_quantity:0,hold_quantity:0,[field]:quantity,discrepancy_note:expected!==null&&expected!==quantity?'Shipment quantity differs':null}]};const intent=createIntent.current||{key:keys.current.create,body};createIntent.current=intent;setPendingCreate(true);try{const result=await createReceipt(intent.body,intent.key);createdId=result.id;setSavedId(result.id);setPendingCreate(false);setParams({receipt_id:String(result.id)},{replace:true});await load(result.id);setSavedId(undefined);createIntent.current=null;keys.current.create=requestKey()}catch(cause){if(!createdId&&rejected(cause)){createIntent.current=null;keys.current.create=requestKey();setPendingCreate(false)}setError(createdId?'Receipt saved; refresh needed. No second draft will be created.':failure(cause,'Receipt result is unconfirmed. Retry sends the identical draft.'))}finally{busy.current=false;setLoading(false)}}
  async function runAction(intent:ReceiptIntent){if(busy.current)return;busy.current=true;setLoading(true);setError('');try{const result=intent.action==='update'?await updateReceipt(intent.receiptId,intent.body!,intent.key):intent.action==='post'?await postReceipt(intent.receiptId,intent.version,intent.key):await cancelReceipt(intent.receiptId,intent.version,intent.key);actionIntent.current=null;setPendingAction(undefined);try{await load(result.id)}catch{setRefreshNeeded(true);setError(`Receipt ${intent.action} saved; refresh needed before another action.`)}}catch(cause){if(rejected(cause)){actionIntent.current=null;setPendingAction(undefined)}if(conflict(cause)){try{await load(intent.receiptId)}catch{setRefreshNeeded(true);setError('Receipt changed and could not be refreshed. Retry refresh before another action.');return}}setError(failure(cause,'Receipt result is unconfirmed. Retry sends the identical request.'))}finally{busy.current=false;setLoading(false)}}
  function saveDraft(){if(!receipt)return;const intent=actionIntent.current||{action:'update' as const,key:requestKey(),receiptId:receipt.id,version:receipt.version,body:{expected_version:receipt.version,...draftBody(receipt)}};actionIntent.current=intent;setPendingAction(intent.action);void runAction(intent)}
  function transition(action:'post'|'cancel'){if(!receipt)return;const intent=actionIntent.current||{action,key:requestKey(),receiptId:receipt.id,version:receipt.version};actionIntent.current=intent;setPendingAction(intent.action);void runAction(intent)}
  const changeLine=(lineNo:number,change:Partial<ReceiptLine>)=>setReceipt(current=>current&&({...current,lines:current.lines.map(line=>line.line_no===lineNo?{...line,...change}:line)}))
  const receiptDirty=!!receipt&&JSON.stringify(draftBody(receipt))!==savedDraft.current

  if(loading&&!receipt)return <Card title="Receive shipment"><Spin/></Card>
  if(savedId&&!receipt)return <Card title={`Receipt #${savedId}`}><Alert type="warning" showIcon message="Receipt saved; refresh needed." action={<Button onClick={async()=>{setLoading(true);try{await load(savedId);setSavedId(undefined);createIntent.current=null;keys.current.create=requestKey()}catch(cause){setError(failure(cause,'Unable to refresh the saved receipt.'))}finally{setLoading(false)}}}>Retry refresh</Button>}/>{error&&<Alert type="error" showIcon message={error}/>}</Card>
  if(refreshNeeded&&receipt)return <Card title={`Receipt #${receipt.id}`}><Alert type="warning" showIcon message={error} action={<Button onClick={()=>load(receipt.id)}>Retry refresh</Button>}/></Card>
  if(params.has('receipt_id')&&!receipt)return <Card title="Receive shipment"><Alert type="error" showIcon message={error||'Unable to load this receipt.'}/></Card>
  if(receipt)return <Card className="pc-goods-card" title={<>收货单 <span>{`Receipt #${receipt.id}`}</span></>}><Space direction="vertical" style={{width:'100%'}}>{error&&<Alert type="error" showIcon message={error} action={pendingAction?<Button onClick={()=>runAction(actionIntent.current!)}>Retry identical request</Button>:undefined}/>}<Typography.Text>{locations.find(location=>location.id===receipt.destination_location_id)?.name||`Location reference ${receipt.destination_location_id}`} · {receipt.business_date}</Typography.Text><Typography.Text>{receipt.shipment_reference||'No shipment reference'}</Typography.Text><OperationScanInput onScan={scan} disabled={loading||!!pendingAction||receipt.status!=='draft'}/><div className="pc-receipt-lines">{receipt.lines.map(line=>{const locked=!!pendingAction||receipt.status!=='draft';const total=line.saleable_quantity+line.damaged_quantity+line.hold_quantity;const short=line.expected_quantity!==null&&line.expected_quantity!==total;return <section key={line.line_no} className="pc-receipt-line" aria-label={`Line ${line.line_no}`}>
    <strong className="pc-receipt-product">{productLabel(productOptions.find(item=>item.id===line.product_id)||{id:line.product_id})}</strong>
    <div className="pc-receipt-cells">
      <label><span>应到 Expected</span><InputNumber aria-label={`Line ${line.line_no} expected`} disabled={locked} min={0} precision={0} value={line.expected_quantity} onChange={value=>changeLine(line.line_no,{expected_quantity:value})}/></label>
      {([['saleable','实到 · 完好','Saleable'],['damaged','损坏','Damaged'],['hold','暂扣','Hold']] as const).map(([kind,zh,en])=><label key={kind} className={kind==='saleable'?'pc-receipt-actual':undefined}><span>{zh} {en}</span><InputNumber aria-label={`Line ${line.line_no} ${kind}`} disabled={locked} min={0} precision={0} value={line[`${kind}_quantity`]} onChange={value=>changeLine(line.line_no,{[`${kind}_quantity`]:value||0})}/></label>)}
    </div>
    {short&&<p className="pc-receipt-short"><i className="pc-dot pc-dot-warn" aria-hidden="true"/>{total<line.expected_quantity!?`少 ${line.expected_quantity!-total}`:`多 ${total-line.expected_quantity!}`} · Shipment quantity differs</p>}
    <label className="pc-receipt-note"><span>差异说明 Discrepancy</span><Input aria-label={`Line ${line.line_no} discrepancy`} disabled={locked} value={line.discrepancy_note||''} onChange={event=>changeLine(line.line_no,{discrepancy_note:event.target.value||null})}/></label>
    <p className="pc-receipt-effect"><span>{line.saleable_quantity} {line.native_unit}s saleable</span><span>{line.damaged_quantity} {line.native_unit}s damaged</span><span>{line.hold_quantity} {line.native_unit}s hold</span></p>
  </section>})}</div><Typography.Text strong>Status: {receipt.status}</Typography.Text>{receipt.status==='draft'&&<div className="pc-receipt-actions"><Button disabled={!!pendingAction||scanning>0} onClick={saveDraft}>存草稿 Save draft changes</Button><Button type="primary" disabled={!!pendingAction||receiptDirty||scanning>0} title={receiptDirty?'Save draft changes before posting':undefined} onClick={()=>transition('post')}>入库 Post receipt</Button><Button danger disabled={!!pendingAction} onClick={()=>transition('cancel')}>取消草稿 Cancel draft</Button></div>}{receipt.status==='posted'&&<Alert type="success" showIcon message="Receipt posted to inventory."/>}</Space></Card>
  return <Card className="pc-goods-card" title={<>收货 <span>Receive shipment</span></>}><Space direction="vertical" style={{width:'100%'}}>{error&&<Alert type="error" showIcon message={error}/>}<Form className="pc-receive-form" layout="vertical" disabled={pendingCreate}><div className="pc-receive-pair"><Form.Item label="单号 Shipment reference"><Input value={reference} onChange={event=>setReference(event.target.value)}/></Form.Item><Form.Item label="放到 Destination" required><Select value={locationId} onChange={setLocationId} options={locations.map(item=>({value:item.id,label:item.name}))}/></Form.Item></div></Form><OperationScanInput onScan={scan} disabled={loading||pendingCreate}/><Form className="pc-receive-form" layout="vertical" disabled={pendingCreate}><Form.Item label="产品 Product" required><Select showSearch filterOption={false} onSearch={searchProducts} optionFilterProp="label" placeholder="搜索 记账名 / SKU" value={productId} onChange={chooseProduct} options={productOptions.map(item=>({value:item.id,label:productLabel(item)}))}/></Form.Item><div className="pc-receipt-cells"><Form.Item label="应到 Expected (optional)"><InputNumber aria-label="Expected quantity" min={0} precision={0} value={expected} onChange={setExpected}/></Form.Item><Form.Item className="pc-receipt-actual" label="实到 Actual quantity"><InputNumber aria-label="Actual quantity" min={0} precision={0} value={quantity} onChange={value=>setQuantity(value||0)}/></Form.Item><Form.Item label="状况 Condition"><Select value={disposition} onChange={setDisposition} options={[['saleable','完好 saleable'],['damaged','损坏 damaged'],['hold','暂扣 hold']].map(([value,label])=>({value,label}))}/></Form.Item></div></Form><Typography.Text className="pc-receipt-effect">Effect after posting: +{quantity} {product?.stock_unit||'units'} at {locations.find(item=>item.id===locationId)?.name||'selected destination'} ({disposition}).</Typography.Text><Button className="pc-receive-save" type="primary" loading={loading} disabled={!product?.stock_unit||!locationId||quantity<1||scanning>0} onClick={saveNew}>保存草稿 Save draft for review</Button></Space></Card>
}
