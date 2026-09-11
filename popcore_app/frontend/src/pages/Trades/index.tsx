import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Checkbox, Form, Input, InputNumber, Select, Space, Spin, Tag, Typography, message } from 'antd'
import { Link } from 'react-router-dom'
import { useAppStore } from '../../store'
import { useHasRole } from '../../auth/useRole'
import { createConditionCase, createTradeSlot, getTradeSetup, getTradeSlot, inspectTrade, listTradeSlots, openTradeSlot, sellTrade, swapTrade, type TradeSlot } from '../../api/trades'

const errText = (e: any) => e?._serverMessage || 'The trade could not be saved. Refresh the slot and try again.'

export default function TradesPage() {
  const store = useAppStore(s => s.selectedStore)
  const manager = useHasRole('manager')
  const [slots, setSlots] = useState<TradeSlot[]>([])
  const [slot, setSlot] = useState<TradeSlot | null>(null)
  const [setup, setSetup] = useState<{ floor_location_id:number; series:{series_id:number;name:string}[] } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [inspection, setInspection] = useState<number | null>(null)
  const [form] = Form.useForm()
  const request = useRef(0)

  async function load(preferred?: number) {
    if (!store?.id || store.code === 'ALL') return
    const storeId = store.id
    const current = ++request.current
    setBusy(true); setError('')
    try {
      const [rows, config] = await Promise.all([listTradeSlots(storeId), getTradeSetup(storeId)])
      if (current !== request.current) return
      setSlots(rows); setSetup(config)
      const id = preferred || slot?.slot_id || rows[0]?.slot_id
      const detail = id ? await getTradeSlot(id) : null
      if (current === request.current) setSlot(detail)
    } catch (e) { if(current===request.current)setError(errText(e)) } finally { if(current===request.current)setBusy(false) }
  }
  useEffect(() => { request.current++; setSlot(null); setSlots([]); setSetup(null); setInspection(null); void load(); return()=>{request.current++} }, [store?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  async function act(run: () => Promise<any>, success: string) {
    setBusy(true); setError('')
    try { const result = await run(); message.success(success); await load(result.slot_id || slot?.slot_id); return result }
    catch (e) { setError(errText(e)); return null } finally { setBusy(false) }
  }
  if (!store || store.code === 'ALL') return <Alert type="info" showIcon message="Choose one store" description="Trade changes require a specific authorized store." />
  const randoms = slot?.products?.filter(p => p.stock_form === 'random_box') || []
  const designs = slot?.products?.filter(p => p.stock_form === 'confirmed_design') || []
  return <Spin spinning={busy}><Space direction="vertical" size={16} style={{ width:'100%' }}>
    <div><Typography.Title level={2} style={{marginBottom:0}}>Trades</Typography.Title><Typography.Text type="secondary">Inspect, swap, sell, and explicitly replace one same-series slot.</Typography.Text></div>
    {error && <Alert type="error" showIcon message={error} action={<Button onClick={() => load()}>Refresh slot</Button>} />}
    {manager && setup && <Card title="Create a series slot"><Space wrap>
      <Select aria-label="Series" placeholder="Series" style={{minWidth:240}} options={setup.series.map(s=>({value:s.series_id,label:s.name}))} onChange={v=>form.setFieldValue('series_id',v)} />
      <Button style={{minHeight:44}} onClick={()=>act(()=>createTradeSlot({store_id:store.id,series_id:form.getFieldValue('series_id'),location_id:setup.floor_location_id}),'Trade slot created')}>Create slot</Button>
    </Space></Card>}
    <Card title="Trade slot"><Select aria-label="Trade slot" value={slot?.slot_id} placeholder="Select a slot" style={{width:'100%',maxWidth:440}} options={slots.map(s=>({value:s.slot_id,label:`Series ${s.series_id} · slot ${s.slot_id}`}))} onChange={id=>{setInspection(null);void load(id)}} />
      {slot && <Space direction="vertical" size={12} style={{display:'flex',marginTop:16}}>
        <Space wrap><Tag>Version {slot.version}</Tag><Tag color={slot.occupant ? 'green':'orange'}>{slot.occupant ? `Occupied: ${slot.occupant.design_name}`:'Empty · replacement needed'}</Tag></Space>
        {slot.occupant ? <>
          <Form layout="vertical" form={form} preserve>
            <Form.Item label="Incoming verified design" name="design_product_id" rules={[{required:true}]}><Select showSearch optionFilterProp="label" options={designs.map(p=>({value:p.product_id,label:p.name}))}/></Form.Item>
            <Form.Item label="Proof type" name="proof_kind" rules={[{required:true}]}><Select options={[{value:'legacy_sticker',label:'Legacy sticker reviewed'},{value:'original_receipt',label:'Original receipt reviewed'},{value:'prior_trade',label:'Prior recorded trade'}]}/></Form.Item>
            <Form.Item label="Proof reference" name="proof_reference" rules={[{required:true}]}><Input /></Form.Item>
            <Form.Item label="Observed condition" name="condition" rules={[{required:true}]}><Input /></Form.Item>
            <Form.Item label="Customer disclosure" name="disclosure" rules={[{required:true}]}><Input.TextArea /></Form.Item>
            <Space wrap><Form.Item name="box_checked" valuePropName="checked"><Checkbox>Box checked</Checkbox></Form.Item><Form.Item name="accessories_checked" valuePropName="checked"><Checkbox>Accessories checked</Checkbox></Form.Item></Space>
            <Button type="primary" style={{minHeight:44}} onClick={async()=>{ const v=await form.validateFields(); const r=await act(()=>inspectTrade(slot.slot_id,{...v,origin:'inspected_trade',decision:'accepted',expected_version:slot.version}),'Inspection accepted'); if(r)setInspection(r.inspection_id)}}>Record inspection</Button>
          </Form>
          {inspection && <Button type="primary" danger style={{minHeight:44}} onClick={()=>act(()=>swapTrade(slot.slot_id,{expected_version:slot.version,inspection_id:inspection,outgoing_balance_version:slot.products?.find(p=>p.product_id===slot.occupant?.design_product_id)?.balance_version,incoming_balance_version:slot.products?.find(p=>p.product_id===form.getFieldValue('design_product_id'))?.balance_version}),'Swap confirmed')}>Confirm physical swap</Button>}
          <Space wrap><InputNumber aria-label="Reviewed sale ID" placeholder="Reviewed sale ID" min={1} onChange={v=>form.setFieldValue('sale_id',v)}/><InputNumber aria-label="Sale version" placeholder="Sale version" min={1} onChange={v=>form.setFieldValue('sale_version',v)}/><Button style={{minHeight:44}} onClick={()=>act(()=>sellTrade(slot.slot_id,{expected_version:slot.version,sale_id:form.getFieldValue('sale_id'),sale_expected_version:form.getFieldValue('sale_version'),trade_balance_version:slot.products?.find(p=>p.product_id===slot.occupant?.design_product_id)?.balance_version}),'Trade unit sold; slot needs replacement')}>Sell current unit</Button></Space>
          <Space wrap><Input aria-label="Condition concern" placeholder="Condition concern" onChange={e=>form.setFieldValue('case_condition',e.target.value)} /><Button style={{minHeight:44}} onClick={async()=>{const r=await act(()=>createConditionCase({unit_id:slot.occupant_unit_id,observed_condition:form.getFieldValue('case_condition'),disclosed_condition:slot.occupant?.condition_disclosure,reason:'Recorded from trade slot'}),'Condition case created'); if(r)window.location.assign(`/trades/cases/${r.case_id}`)}}>Record condition case</Button></Space>
        </> : <Form layout="vertical" form={form} preserve>
          <Form.Item label="Random box to open" name="random_product_id" rules={[{required:true}]}><Select options={randoms.map(p=>({value:p.product_id,label:`${p.name} · ${p.quantity} ${p.stock_unit}`}))}/></Form.Item>
          <Form.Item label="Observed verified design" name="open_design_id" rules={[{required:true}]}><Select showSearch optionFilterProp="label" options={designs.map(p=>({value:p.product_id,label:p.name}))}/></Form.Item>
          <Form.Item label="Condition disclosure" name="open_disclosure" rules={[{required:true}]}><Input.TextArea /></Form.Item>
          <Button type="primary" style={{minHeight:44}} onClick={async()=>{const v=await form.validateFields();const rp=randoms.find(p=>p.product_id===v.random_product_id);const dp=designs.find(p=>p.product_id===v.open_design_id);await act(()=>openTradeSlot(slot.slot_id,{expected_version:slot.version,random_product_id:rp?.product_id,design_product_id:dp?.product_id,condition_disclosure:v.open_disclosure,random_balance_version:rp?.balance_version,trade_balance_version:dp?.balance_version}),'Replacement placed in slot')}}>Open box and place replacement</Button>
        </Form>}
      </Space>}
    </Card>
    <Typography.Text type="secondary">Condition cases are reviewed separately and never issue a refund automatically. <Link to="/sales/entry">Prepare a sale</Link></Typography.Text>
  </Space></Spin>
}
