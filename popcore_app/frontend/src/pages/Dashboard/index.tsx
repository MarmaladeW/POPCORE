import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Empty, List, Skeleton, Space, Tag, Typography } from 'antd'
import { Link } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { getToday, type TodayPayload, type TodaySection } from '../../api/today'
import { useAppStore } from '../../store'
import { useHasRole, useRole } from '../../auth/useRole'
import MyShifts from './MyShifts'
import { torontoDate } from './todayPresentation'

const labels:Record<string,string>={my_work:'My work',operations:'Store work',financial:'Sales and tender checks',catalog:'Catalog notices'}
function Section({name,section}:{name:string;section:TodaySection}){
  return <Card title={`${labels[name]||name} (${section.total})`}><List dataSource={section.rows} locale={{emptyText:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Nothing needs attention"/>}} renderItem={row=><List.Item actions={[<Link key="open" to={row.link}>Open</Link>]}><List.Item.Meta title={<Space wrap><span>{row.label||`${row.type.replaceAll('_',' ')} ${row.source_id}`}</span><Tag>{row.status}</Tag></Space>} description={row.store_id?`Store ${row.store_id}${row.updated_at?` · ${row.updated_at}`:''}`:row.updated_at||''}/></List.Item>}/></Card>
}
export default function DashboardPage(){
  const {user}=useAuth0();const role=useRole();const staff=useHasRole('staff');const store=useAppStore(s=>s.selectedStore)
  const [data,setData]=useState<TodayPayload>();const [loading,setLoading]=useState(true);const [error,setError]=useState('');const[last,setLast]=useState('');const[attempt,setAttempt]=useState(0);const request=useRef(0)
  useEffect(()=>{const controller=new AbortController();const current=++request.current;setData(undefined);setLast('');setLoading(true);setError('');if(!store?.code){setLoading(false);return}getToday(store.code,torontoDate(),controller.signal).then(value=>{if(current===request.current){setData(value);setLast(new Date().toLocaleTimeString())}}).catch((e:any)=>{if(current===request.current&&e?.code!=='ERR_CANCELED')setError(e?._serverMessage||'Unable to load Today.')} ).finally(()=>{if(current===request.current)setLoading(false)});return()=>{request.current++;controller.abort()}},[attempt,role,store?.code,user?.sub])
  return <Space direction="vertical" size={16} style={{width:'100%'}}>
    <div><Typography.Title level={2} style={{marginBottom:0}}>Today / 今日</Typography.Title><Typography.Text type="secondary">{torontoDate()} · {store?.name||'Choose a store'}</Typography.Text></div>
    {staff&&<MyShifts key={`${user?.sub}:${role}`}/>}
    {loading&&!data?<Card><Skeleton active/></Card>:error&&!data?<Alert role="alert" type="error" showIcon message={error} action={<Button onClick={()=>setAttempt(n=>n+1)}>Retry</Button>}/>:<>
      {error&&<Alert type="warning" showIcon message="Today could not refresh"
        description={last&&`Showing data refreshed at ${last}`} action={<Button onClick={()=>setAttempt(n=>n+1)}>Retry</Button>}/>}
      {data&&data.store_ids.length===0&&<Alert type="info" showIcon message="No inventory store access" description="Your permitted catalog notices and personal schedule remain available."/>}
      {data&&Object.entries(data.sections).sort(([a],[b])=>['my_work','operations','financial','catalog'].indexOf(a)-['my_work','operations','financial','catalog'].indexOf(b)).map(([name,section])=><Section key={name} name={name} section={section}/>)}
    </>}
  </Space>
}
