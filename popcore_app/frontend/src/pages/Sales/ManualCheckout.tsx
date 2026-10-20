import { productLabel } from '../../lib/productLabel'
import {useEffect,useRef,useState} from 'react'
import {Alert,Button,Form,Input,InputNumber,Select,Space,Typography} from 'antd'
import {Link,useBeforeUnload,useNavigate} from 'react-router-dom'
import {searchGoodsProducts,type GoodsProduct} from '../../api/goods'
import {formatCents,parseMoneyToCents} from '../../lib/money'
import {HST_PERCENT,hstCents} from '../../lib/checkoutPricing'
import {useAppStore} from '../../store'
import {torontoDate} from '../Dashboard/todayPresentation'
import useCheckoutMutation from './useCheckoutMutation'
const {Title,Paragraph}=Typography
export default function ManualCheckout({allowedStoreIds}:{allowedStoreIds:number[]}) {
  const selectedStore = useAppStore(state => state.selectedStore), setSelectedStore = useAppStore(state => state.setSelectedStore), navigate = useNavigate()
  const [store,setScope] = useState(selectedStore), [dirty,setDirty] = useState(false)
  const [form] = Form.useForm(), [products,setProducts] = useState<GoodsProduct[]>([]), [searchError,setSearchError] = useState('')
  const searchVersion = useRef(0)
  // Tax follows the item prices at 13% HST until someone types the agreed tax by hand.
  const [taxByHand,setTaxByHand] = useState(false)
  const watchedLines = Form.useWatch('lines',form) as {quantity?:number;price?:string}[]|undefined
  const watchedTax = Form.useWatch('tax',form) as string|undefined, watchedReduction = Form.useWatch('reduction',form) as string|undefined
  const money = (value:string|undefined) => { try { return parseMoneyToCents(value ?? '') } catch { return null } }
  const lineCents = (watchedLines??[]).map(line => { const price = money(line?.price); return Number.isSafeInteger(line?.quantity) && price !== null ? line!.quantity! * price : null })
  const subtotal = lineCents.length && lineCents.every(cents => cents !== null) ? (lineCents as number[]).reduce((sum,cents)=>sum+cents,0) : null
  const taxNow = money(watchedTax), reductionNow = money(watchedReduction) ?? 0
  useEffect(() => { if (!taxByHand && subtotal !== null && Number.isSafeInteger(subtotal)) form.setFieldValue('tax',(hstCents(subtotal)/100).toFixed(2)) },[subtotal,taxByHand,form])
  const mutation = useCheckoutMutation(data => navigate(`/checkout/${data.id}`,{replace:true}))
  async function search(query:string) {
    const version = ++searchVersion.current
    try { const rows = await searchGoodsProducts(query); if (version === searchVersion.current) { setProducts(old => [...new Map([...old,...rows].map(p=>[p.id,p])).values()]); setSearchError('') } }
    catch { if (version === searchVersion.current) setSearchError('Unable to search products. Try again.') }
  }
  useEffect(() => { search('') },[])
  useBeforeUnload(event=>{if(dirty)event.preventDefault()})
  useEffect(()=> {
    if (selectedStore?.id === store?.id) return
    if (mutation.pending || dirty) {
      if (store) setSelectedStore(store)
      setSearchError('Finish this checkout or reset the form before changing stores.')
    } else setScope(selectedStore)
  },[selectedStore,store,mutation.pending,dirty,setSelectedStore])
  if (!store?.id || !allowedStoreIds.includes(store.id)) return <><Link to="/checkout">Back to checkouts</Link><Alert type="info" message="Choose a store where you have checkout access today." /></>
  // Choosing an item fills in its catalog price, as Clover does; it can still be changed.
  function fillPrice(line:number,id:number) {
    const price = Number(products.find(product => product.id === id)?.price)
    if (Number.isFinite(price) && price > 0) form.setFieldValue(['lines',line,'price'],(Math.round(price*100)/100).toFixed(2))
  }
  function submit(values:any) {
    try {
      const cents = (value:string) => { const n = parseMoneyToCents(value); if (n === null || n < 0) throw new Error('Enter a nonnegative amount in every money field.'); return n }
      const lines = values.lines.map((line:any) => ({ product_id:line.product_id, unit:products.find(p=>p.id===line.product_id)?.stock_unit, quantity:line.quantity, unit_price_cents:cents(line.price) }))
      const subtotal = lines.reduce((sum:number,line:any)=>sum+line.quantity*line.unit_price_cents,0)
      const tax = cents(values.tax), reduction = cents(values.reduction), due = subtotal+tax-reduction
      if (![subtotal,tax,reduction,due].every(Number.isSafeInteger) || due <= 0) throw new Error('The amount due must be positive and within the supported range.')
      mutation.run('/checkouts',{store_id:store!.id,business_date:values.date,reference:values.reference?.trim()||undefined,lines,subtotal_cents:subtotal,source_tax_cents:tax,gross_cents:subtotal+tax,reduction_cents:reduction,rounding_cents:0,collected_cents:due})
    } catch (cause) { setSearchError((cause as Error).message) }
  }
  return <>
    <Link to="/checkout">Back to checkouts</Link><Title level={3}>New manual checkout · {store.name}</Title>
    <Paragraph>For a sale that is not on Clover, such as a phone order or an item another store is sending. Enter the agreed prices and tax. Saving starts a pending checkout; record payment after money is received. Stock moves when checkout is completed.</Paragraph>
    {mutation.notice}{searchError && <Alert type="error" message={searchError} />}
    <Form form={form} layout="vertical" onFinish={submit} onValuesChange={changed=>{setDirty(true);if('tax' in changed)setTaxByHand(true)}} disabled={mutation.pending} initialValues={{date:torontoDate(),tax:'0.00',reduction:'0.00',lines:[{quantity:1}]}}>
      <Form.Item name="reference" label="Order reference (optional)" extra="Leave blank when there is none; POPCORE numbers it, for example MK-20261020-001." rules={[{max:120}]}><Input autoComplete="off" /></Form.Item>
      <Form.Item name="date" label="Business date" rules={[{required:true}]}><Input type="date" /></Form.Item>
      <Form.List name="lines">{(fields,{add,remove}) => <>
        {fields.map(field => <section key={field.key} style={{borderBottom:'1px solid #d9d9d9',marginBottom:20}}>
          <Form.Item name={[field.name,'product_id']} label={`Item ${field.name+1}`} rules={[{required:true}]}><Select showSearch filterOption={false} onSearch={search} options={products.map(p=>({value:p.id,label:productLabel(p)}))} onChange={(id:number)=>fillPrice(field.name,id)} /></Form.Item>
          <Space wrap align="start"><Form.Item name={[field.name,'quantity']} label="Quantity" rules={[{required:true}]}><InputNumber min={1} precision={0} /></Form.Item><Form.Item name={[field.name,'price']} label="Unit price ($)" rules={[{required:true}]}><Input inputMode="decimal" /></Form.Item></Space>
          {fields.length>1 && <Button type="text" danger onClick={()=>remove(field.name)}>Remove item {field.name+1}</Button>}
        </section>)}
        <Button onClick={()=>add({quantity:1})} style={{marginBottom:20}}>Add item</Button>
      </>}</Form.List>
      <Form.Item name="tax" label="Agreed tax total ($)" extra={taxByHand?<>Entered by hand. <Button type="link" style={{padding:0}} onClick={()=>setTaxByHand(false)}>Use {HST_PERCENT}% HST</Button></>:`${HST_PERCENT}% HST of the item prices, filled in for you. Change it if the agreed tax is different.`} rules={[{required:true}]}><Input inputMode="decimal" /></Form.Item>
      <Form.Item name="reduction" label="Discount / reduction ($)" rules={[{required:true}]}><Input inputMode="decimal" /></Form.Item>
      {subtotal!==null&&taxNow!==null&&<Paragraph strong>Subtotal {formatCents(subtotal)} · Tax {formatCents(taxNow)}{reductionNow?` · Discount −${formatCents(reductionNow)}`:''} · Customer pays {formatCents(subtotal+taxNow-reductionNow)}</Paragraph>}
      <Button type="primary" htmlType="submit" loading={mutation.saving}>Create pending checkout</Button>
      <Button disabled={mutation.pending} onClick={()=>{form.resetFields();setDirty(false);setTaxByHand(false);setSearchError('')}} style={{marginLeft:8}}>Reset form</Button>
    </Form>
  </>
}
