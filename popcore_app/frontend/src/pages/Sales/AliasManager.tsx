import { useState, useEffect, useCallback, useRef } from 'react'
import { Table, Button, Space, Tag, Popconfirm, AutoComplete, Input, Typography, Alert } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import client from '../../api/client'
import RoleGuard from '../../components/RoleGuard'
import { productLabel } from '../../lib/productLabel'

const { Text } = Typography
interface AliasRow {
  id:number; alias:string; created_by:string|null; product_id:number; jizhanming:string; sku:string
  series_name?:string; design_name?:string; stock_form?:string
}
interface ProductOption {value:string;label:string;product:{id:number}}

export default function AliasManager({onBusyChange}:{onBusyChange?:(busy:boolean)=>void}) {
  const [aliases,setAliases] = useState<AliasRow[]>([])
  const [loading,setLoading] = useState(false)
  const [loadError,setLoadError] = useState('')
  const [error,setError] = useState('')
  const [aliasInput,setAliasInput] = useState('')
  const [productOpts,setProductOpts] = useState<ProductOption[]>([])
  const [selectedPid,setSelectedPid] = useState<number|null>(null)
  const [productSearch,setProductSearch] = useState('')
  const [saving,setSaving] = useState(false)
  const [denied,setDenied] = useState(false)
  const mounted = useRef(true), searchRequest = useRef(0), loadRequest = useRef(0), savingRef = useRef(false)

  const load = useCallback(async () => {
    const request = ++loadRequest.current
    setLoading(true); setLoadError('')
    try {
      const response = await client.get('/products/aliases')
      if (mounted.current && request === loadRequest.current) setAliases(response.data)
    } catch (cause:any) {
      if (mounted.current && request === loadRequest.current) setLoadError(cause?._serverMessage || 'Unable to load saved name mappings.')
    } finally { if (mounted.current && request === loadRequest.current) setLoading(false) }
  }, [])

  useEffect(() => {
    mounted.current = true; load()
    return () => { mounted.current = false; searchRequest.current += 1; loadRequest.current += 1; window.dispatchEvent(new CustomEvent('popcore:checkout-busy',{detail:false})) }
  }, [load])

  async function searchProducts(query:string) {
    setProductSearch(query); setSelectedPid(null); setProductOpts([])
    const request = ++searchRequest.current
    if (!query.trim()) return
    try {
      const response = await client.get('/products/search',{params:{q:query,limit:10}})
      if (mounted.current && request === searchRequest.current) setProductOpts(response.data.map((product:any) => ({value:String(product.id),label:`${productLabel(product)} (${product.sku})`,product})))
    } catch { if (mounted.current && request === searchRequest.current) setError('Unable to search products. Try again.') }
  }

  async function mutate(action:()=>Promise<unknown>,afterSave?:()=>void) {
    if (savingRef.current || denied) return
    savingRef.current = true; setSaving(true); setError(''); onBusyChange?.(true)
    window.dispatchEvent(new CustomEvent('popcore:checkout-busy',{detail:true}))
    try { await action(); if (mounted.current) { afterSave?.(); load() } }
    catch (cause:any) { if (mounted.current) { setError(cause?._serverMessage || 'Unable to save the name mapping. Your entries are still here.'); if (cause?.response?.status===403) setDenied(true) } }
    finally { if (mounted.current) { savingRef.current=false;setSaving(false);onBusyChange?.(false);window.dispatchEvent(new CustomEvent('popcore:checkout-busy',{detail:false})) } }
  }

  async function handleAdd() {
    const alias=aliasInput.trim(), productId=selectedPid
    if (!alias || !productId) return
    await mutate(() => client.post('/products/aliases',{product_id:productId,alias}),() => {
      setAliasInput('');setProductSearch('');setSelectedPid(null);setProductOpts([]);searchRequest.current+=1
    })
  }

  const columns = [
    {title:'Staff name',dataIndex:'alias',width:180,render:(value:string)=><Text>{value}</Text>},
    {title:'Mapped product',key:'product',render:(_:unknown,row:AliasRow)=><Space wrap size={4}><span>{productLabel({...row,id:row.product_id})}</span><Tag>{row.sku}</Tag></Space>},
    {title:'Source',dataIndex:'created_by',width:110,render:(value:string|null)=><Tag>{value==='system_seed'?'Preset':'Staff saved'}</Tag>},
    {title:'',key:'delete',width:60,render:(_:unknown,row:AliasRow)=><Popconfirm title={`Delete mapping for "${row.alias}"?`} onConfirm={()=>mutate(()=>client.delete(`/products/${row.product_id}/aliases/${row.id}`))}><Button aria-label={`Delete mapping for ${row.alias}`} disabled={saving||denied} type="text" danger icon={<DeleteOutlined/>}/></Popconfirm>},
  ]

  return <RoleGuard minRole="manager"><div>
    <Typography.Title level={4}>Saved name mappings</Typography.Title>
    <p>Choose the exact product for a name staff type in pasted reports. This mapping applies across stores to future matching. Use Review past names to review existing report rows.</p>
    {(error||loadError)&&<Alert role="alert" type="error" showIcon message={error||loadError} action={loadError?<Button onClick={load} disabled={saving}>Retry name mappings</Button>:undefined} style={{marginBottom:12}}/>}
    <div style={{background:'#f9fafb',borderRadius:8,padding:12,marginBottom:16,display:'flex',flexWrap:'wrap',alignItems:'flex-end',gap:10}}>
      <label style={{maxWidth:'100%'}}>Staff name<Input aria-label="Staff name" value={aliasInput} disabled={saving||denied} onChange={event=>setAliasInput(event.target.value)} placeholder="e.g. smiski hipper" style={{width:180,maxWidth:'100%'}} onPressEnter={handleAdd}/></label>
      <label style={{maxWidth:'100%'}}>Mapped product<AutoComplete aria-label="Mapped product" value={productSearch} disabled={saving||denied} options={productOpts} onSearch={searchProducts} placeholder="Search exact product..." style={{display:'block',width:250,maxWidth:'100%'}} onSelect={(_:string,option:any)=>{searchRequest.current+=1;setSelectedPid(option.product.id);setProductSearch(option.label);setProductOpts([])}}/></label>
      <Button type="primary" aria-label="Save name mapping" icon={<PlusOutlined/>} loading={saving} disabled={saving||denied||!aliasInput.trim()||!selectedPid} onClick={handleAdd}>Save name mapping</Button>
    </div>
    <Table rowKey="id" size="small" loading={loading} dataSource={aliases} columns={columns} scroll={{x:500}} pagination={{pageSize:20,showTotal:total=>`${total} mappings`}}/>
  </div></RoleGuard>
}
