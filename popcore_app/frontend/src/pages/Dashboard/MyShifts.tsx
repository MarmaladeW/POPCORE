import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Empty, Skeleton, Space, Tag, Typography } from 'antd'
import { Link } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { getMyShifts, type Shift } from '../Schedule/scheduleApi'
import { selectShiftHighlights, torontoDate } from './todayPresentation'

export default function MyShifts(){
  const {user}=useAuth0(); const [rows,setRows]=useState<Shift[]>([]); const [loading,setLoading]=useState(true);const[error,setError]=useState('');const[last,setLast]=useState('');const request=useRef(0)
  const load=useCallback(async()=>{const current=++request.current;setLoading(true);setError('');try{const data=await getMyShifts({start:torontoDate(),store_code:'ALL'});if(current!==request.current)return;setRows(data);setLast(new Date().toLocaleTimeString())}catch{if(current===request.current)setError('Unable to refresh your assigned shifts.')}finally{if(current===request.current)setLoading(false)}},[user?.sub])
  useEffect(()=>{setRows([]);setLast('');void load();const focus=()=>void load();window.addEventListener('focus',focus);return()=>{request.current++;window.removeEventListener('focus',focus)}},[load])
  const selected=selectShiftHighlights(rows,torontoDate());const shown=[...selected.today,...selected.next]
  return <Card title="My shifts / 我的班次" extra={<Link to="/schedule">View Schedule</Link>} style={{borderTop:'4px solid #6366F1'}}>{loading&&!last?<Skeleton active paragraph={{rows:2}}/>:<Space direction="vertical" style={{width:'100%'}}>{error&&<Alert type="warning" showIcon message={error} description={last&&`Showing data refreshed at ${last}`} action={<Button onClick={load}>Retry</Button>}/>} {selected.invalidCount>0&&<Alert type="error" showIcon message="Some shift data could not be read. Open Schedule or retry before relying on this list."/>}{shown.length===0&&!error&&selected.invalidCount===0?<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<><div>No shift today</div><Typography.Text type="secondary">No upcoming shifts assigned</Typography.Text></>}/>:shown.map(s=><div key={s.id} style={{display:'flex',gap:12,alignItems:'center',justifyContent:'space-between',padding:'10px 0',borderBottom:'1px solid #f0f0f0'}}><div><strong>{s.date===torontoDate()?'Today':s.date}</strong><div>{s.start_time}–{s.end_time}</div></div><div style={{textAlign:'right'}}><Tag color="blue">{s.store_code||'Store'}</Tag>{s.position&&<div>{s.position}</div>}</div></div>)}</Space>}</Card>
}
