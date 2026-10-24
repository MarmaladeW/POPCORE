import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Skeleton } from 'antd'
import { Link } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { ListRow, Panel, SectionHeading, Tag } from '../../components/pc/Panel'
import { getMyShifts, type Shift } from '../Schedule/scheduleApi'
import { selectShiftHighlights, torontoDate } from './todayPresentation'

const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

function dayLabel(date: string, today: string) {
  if (date === today) return '今天'
  const days = (Date.parse(`${date}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000
  const [, month, day] = date.split('-')
  return days > 0 && days < 7 ? weekdays[new Date(`${date}T00:00:00Z`).getUTCDay()] : `${month}/${day}`
}

function monthDay(date: string) {
  const [, month, day] = date.split('-').map(Number)
  return `${month}月${day}日`
}

export default function MyShifts({ outside = false }: { outside?: boolean }) {
  const { user } = useAuth0(); const [rows, setRows] = useState<Shift[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [last, setLast] = useState(''); const request = useRef(0)
  const load = useCallback(async () => { const current = ++request.current; setLoading(true); setError(''); try { const data = await getMyShifts({ start: torontoDate(), store_code: 'ALL' }); if (current !== request.current) return; setRows(data); setLast(new Date().toLocaleTimeString()) } catch { if (current === request.current) setError('Unable to refresh your assigned shifts.') } finally { if (current === request.current) setLoading(false) } }, [user?.sub])
  useEffect(() => { setRows([]); setLast(''); void load(); const focus = () => void load(); window.addEventListener('focus', focus); return () => { request.current++; window.removeEventListener('focus', focus) } }, [load])
  const today = torontoDate()
  const selected = selectShiftHighlights(rows, today); const shown = [...selected.today, ...selected.next]
  const link = <Link to="/schedule">排班表</Link>
  const content = <>
    {loading && !last ? <div className="pc-quiet"><Skeleton active title={false} paragraph={{ rows: 2 }} /></div> : <>
      {error && <Alert type="warning" showIcon message={error} description={last && `Showing data refreshed at ${last}`} action={<Button onClick={load}>Retry</Button>} />}
      {selected.invalidCount > 0 && <Alert type="error" showIcon message="Some shift data could not be read. Open Schedule or retry before relying on this list." />}
      {shown.length === 0 && !error && selected.invalidCount === 0
        ? <p className="pc-quiet"><strong>今天没有班次 · <span>No shift today</span></strong><span>No upcoming shifts assigned</span></p>
        : shown.map(shift => <ListRow key={shift.id} label={dayLabel(shift.date, today)} title={`${shift.start_time}–${shift.end_time}`}
          meta={shift.date === today ? shift.position || '' : monthDay(shift.date)} trailing={<Tag>{shift.store_code || 'Store'}</Tag>} />)}
    </>}
  </>
  // Phone lists put the heading above the box; desktop panels carry it inside.
  if (outside) return <section className="pc-my-shifts" aria-label="My shifts"><SectionHeading zh="我的班次" en="My shifts" action={link} /><div className="pc-panel">{content}</div></section>
  return <Panel zh="我的班次" en="My shifts" label="My shifts" className="pc-my-shifts" action={link}>{content}</Panel>
}
