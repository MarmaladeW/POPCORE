import {useEffect,useRef,useState} from 'react'
import {Alert,Button,Form,Input,InputNumber,Select,Space,Typography} from 'antd'
import {Link,useBeforeUnload,useNavigate} from 'react-router-dom'
import {searchGoodsProducts,type GoodsProduct} from '../../api/goods'
import {parseMoneyToCents} from '../../lib/money'
import {useAppStore} from '../../store'
import {torontoDate} from '../Dashboard/todayPresentation'
import useCheckoutMutation from './useCheckoutMutation'
const {Title,Paragraph}=Typography
export default function ManualCheckout() {
  const selectedStore = useAppStore(state => state.selectedStore), setSelectedStore = useAppStore(state => state.setSelectedStore), navigate = useNavigate()
  const [store,setScope] = useState(selectedStore), [dirty,setDirty] = useState(false)
  const [form] = Form.useForm(), [products,setProducts] = useState<GoodsProduct[]>([]), [searchError,setSearchError] = useState('')
  const searchVersion = useRef(0)
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
  if (!store?.id || store.code === 'ALL') return <Alert type="info" message="Choose one store to begin a checkout." />
  function submit(values:any) {
    try {
      const cents = (value:string) => { const n = parseMoneyToCents(value); if (n === null || n < 0) throw new Error('Enter a nonnegative amount in every money field.'); return n }
      const lines = values.lines.map((line:any) => ({ product_id:line.product_id, unit:products.find(p=>p.id===line.product_id)?.stock_unit, quantity:line.quantity, unit_price_cents:cents(line.price) }))
      const subtotal = lines.reduce((sum:number,line:any)=>sum+line.quantity*line.unit_price_cents,0)
      const tax = cents(values.tax), reduction = cents(values.reduction), due = subtotal+tax-reduction
      if (![subtotal,tax,reduction,due].every(Number.isSafeInteger) || due <= 0) throw new Error('The amount due must be positive and within the supported range.')
      mutation.run('/checkouts',{store_id:store!.id,business_date:values.date,reference:values.reference,lines,subtotal_cents:subtotal,source_tax_cents:tax,gross_cents:subtotal+tax,reduction_cents:reduction,rounding_cents:0,collected_cents:due})
    } catch (cause) { setSearchError((cause as Error).message) }
  }
  return <>
    <Link to="/checkout">Back to checkouts</Link><Title level={3}>New checkout · {store.name}</Title>
    <Paragraph>Enter the agreed prices and tax. Saving starts a pending checkout; stock moves when the sale is recorded.</Paragraph>
    {mutation.notice}{searchError && <Alert type="error" message={searchError} />}
    <Form form={form} layout="vertical" onFinish={submit} onValuesChange={()=>setDirty(true)} disabled={mutation.pending} initialValues={{date:torontoDate(),tax:'0.00',reduction:'0.00',lines:[{quantity:1}]}}>
      <Form.Item name="reference" label="Order reference" rules={[{required:true,whitespace:true,max:120}]}><Input autoComplete="off" /></Form.Item>
      <Form.Item name="date" label="Business date" rules={[{required:true}]}><Input type="date" /></Form.Item>
      <Form.List name="lines">{(fields,{add,remove}) => <>
        {fields.map(field => <section key={field.key} style={{borderBottom:'1px solid #d9d9d9',marginBottom:20}}>
          <Form.Item name={[field.name,'product_id']} label={`Item ${field.name+1}`} rules={[{required:true}]}><Select showSearch filterOption={false} onSearch={search} options={products.map(p=>({value:p.id,label:p.jizhanming || p.sku}))} /></Form.Item>
          <Space wrap align="start"><Form.Item name={[field.name,'quantity']} label="Quantity" rules={[{required:true}]}><InputNumber min={1} precision={0} /></Form.Item><Form.Item name={[field.name,'price']} label="Unit price ($)" rules={[{required:true}]}><Input inputMode="decimal" /></Form.Item></Space>
          {fields.length>1 && <Button type="text" danger onClick={()=>remove(field.name)}>Remove item {field.name+1}</Button>}
        </section>)}
        <Button onClick={()=>add({quantity:1})} style={{marginBottom:20}}>Add item</Button>
      </>}</Form.List>
      <Form.Item name="tax" label="Agreed tax total ($)" rules={[{required:true}]}><Input inputMode="decimal" /></Form.Item>
      <Form.Item name="reduction" label="Discount / reduction ($)" rules={[{required:true}]}><Input inputMode="decimal" /></Form.Item>
      <Button type="primary" htmlType="submit" loading={mutation.saving}>Create pending checkout</Button>
      <Button disabled={mutation.pending} onClick={()=>{form.resetFields();setDirty(false);setSearchError('')}} style={{marginLeft:8}}>Reset form</Button>
    </Form>
  </>
}
