import { useEffect,useMemo,useState } from 'react'
import { Alert,Button,Card,Empty,Select,Space,Table,Typography } from 'antd'
import { Link,useSearchParams } from 'react-router-dom'
import { downloadReport,getReport,type ReportName,type ReportResult } from '../../api/reports'
import { formatCents } from '../../lib/money'
import { useAppStore } from '../../store'
import { reportDefinitions,type ReportColumn } from './definitions'

const names=Object.keys(reportDefinitions) as ReportName[]
const isReportName=(value:string|null):value is ReportName=>!!value&&names.includes(value as ReportName)
const cleanDate=(value:string|null)=>value&&/^\d{4}-\d{2}-\d{2}$/.test(value)?value:''
const readable=(value:unknown)=>value==null||value===''?'Unknown':String(value).replaceAll('_',' ')

export default function ReportsPage(){
  const selectedStore=useAppStore(s=>s.selectedStore)
  const stores=useAppStore(s=>s.stores)
  const[params,setParams]=useSearchParams()
  const name=isReportName(params.get('report'))?params.get('report') as ReportName:'inventory'
  const definition=reportDefinitions[name]
  const from=cleanDate(params.get('from')),to=cleanDate(params.get('to'))
  const requestedPage=Number(params.get('page')||1)
  const page=Number.isInteger(requestedPage)&&requestedPage>0?requestedPage:1
  const[data,setData]=useState<ReportResult>()
  const[error,setError]=useState('')
  const[loading,setLoading]=useState(false)
  const[exporting,setExporting]=useState(false)
  const dateError=definition.dated&&from&&to&&from>to?'From date must not be after to date.':''

  useEffect(()=>{
    if(!selectedStore?.code||dateError){setData(undefined);return}
    const controller=new AbortController()
    setData(undefined);setLoading(true);setError('')
    const query:Record<string,string|number>={store_code:selectedStore.code,page,page_size:50}
    if(definition.dated){if(from)query.from=from;if(to)query.to=to}
    getReport(name,query,controller.signal).then(setData).catch((reason:any)=>{
      if(reason?.code!=='ERR_CANCELED')setError(reason?.response?.status===403?'You do not have access to this report.':reason?._serverMessage||reason?.response?.data?.error||'Unable to load report.')
    }).finally(()=>{if(!controller.signal.aborted)setLoading(false)})
    return()=>controller.abort()
  },[dateError,definition.dated,from,name,page,selectedStore?.code,to])

  const storeNames=useMemo(()=>new Map(stores.map(store=>[store.id,store.name])),[stores])
  const render=(column:ReportColumn,value:unknown,row:Record<string,unknown>)=>{
    if(column.format==='store')return storeNames.get(Number(value))||`Store reference ${readable(value)}`
    if(column.format==='money')return formatCents(value==null?null:Number(value))
    if(column.format==='quantity')return `${readable(value)} ${readable(row[column.unitKey!])}`
    if(column.format==='identity')return Number(value)===1?'Verified':'Incomplete'
    if(column.format==='product')return `Product reference ${readable(value)}`
    if(column.format==='movement')return value==null?'—':`Location reference ${value}`
    if(column.format==='receipt')return <Link to={`/goods/receiving?receipt_id=${value}`}>Receipt #{String(value)}</Link>
    if(column.format==='sale')return <Link to={`/sales/documents/${row.sale_id}`}>Evidence #{String(value)}</Link>
    if(column.format==='closing')return <Link to={`/closing?closing_id=${value}`}>Closing #{String(value)}</Link>
    if(column.format==='count')return <Link to={`/goods/counts?count_id=${value}`}>Count #{String(value)}</Link>
    return readable(value)
  }
  const columns=definition.columns.map(column=>({title:column.label,dataIndex:column.key,key:column.key,render:(value:unknown,row:Record<string,unknown>)=>render(column,value,row)}))
  const update=(changes:Record<string,string>)=>{const next=new URLSearchParams(params);Object.entries(changes).forEach(([key,value])=>value?next.set(key,value):next.delete(key));setParams(next)}
  const chooseReport=(report:ReportName)=>{const next=new URLSearchParams();next.set('report',report);setParams(next)}

  async function exportCsv(){
    if(!selectedStore)return
    setExporting(true);setError('')
    const query:Record<string,string|number>={store_code:selectedStore.code}
    if(definition.dated){if(from)query.from=from;if(to)query.to=to}
    try{await downloadReport(name,query)}catch(reason:any){setError(reason?._serverMessage==='Export exceeds 500 rows'?'Export exceeds 500 rows. Narrow the store or date filters, then try again.':reason?._serverMessage||'Unable to export this report.')}
    finally{setExporting(false)}
  }

  return <Space direction="vertical" style={{width:'100%'}} size={16}>
    <div><Typography.Text type="secondary">Operational reports</Typography.Text><Typography.Title level={2} style={{margin:'2px 0 0'}}>{definition.label} report</Typography.Title><Typography.Text type="secondary">{definition.dated?'Authoritative records within the selected scope.':'Current inventory; date filters do not apply.'}</Typography.Text></div>
    <Card><Space wrap>
      <Select aria-label="Report" value={name} options={names.map(value=>({value,label:reportDefinitions[value].label}))} onChange={chooseReport} style={{minWidth:220}}/>
      {definition.dated&&<>
        <label>From <input aria-label="From date" type="date" value={from} onChange={event=>update({from:event.target.value,page:'1'})}/></label>
        <label>To <input aria-label="To date" type="date" value={to} onChange={event=>update({to:event.target.value,page:'1'})}/></label>
      </>}
      <Button loading={exporting} disabled={!!dateError} onClick={exportCsv}>Export CSV</Button>
    </Space></Card>
    {(dateError||error)&&<Alert type="error" showIcon message={dateError||error}/>}
    {data?.known_gross_cents!=null&&<Typography.Text strong>Known gross: {formatCents(data.known_gross_cents)}</Typography.Text>}
    {data&&!data.gross_complete&&!!data.incomplete_count&&<Alert type="warning" showIcon message={`${data.incomplete_count} ${data.incomplete_count===1?'sale has':'sales have'} an unknown gross total.`}/>}
    <Card>{!loading&&data?.items.length===0?<Empty description="No report rows in this scope"/>:<Table loading={loading} rowKey={(row,index)=>String(row.id||row.closing_id||row.count_id||`${page}-${index}`)} dataSource={data?.items||[]} columns={columns} scroll={{x:true}} pagination={data?{current:page,pageSize:50,total:data.total_rows,showSizeChanger:false,onChange:next=>update({page:String(next)})}:false}/>}<Typography.Text type="secondary">{data?.total_rows??0} rows</Typography.Text></Card>
  </Space>
}
