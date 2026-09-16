import { Alert, Button, Skeleton } from 'antd'
import { ArrowRightOutlined, CameraOutlined, InboxOutlined, GiftOutlined, FileTextOutlined } from '@ant-design/icons'
import { Link, useLocation } from 'react-router-dom'
import { useHasRole } from '../../auth/useRole'
import { formatCents } from '../../lib/money'
import Dashboard from '../Dashboard'
import PunchIn from '../Schedule/PunchIn'
import MyShifts from '../Dashboard/MyShifts'
import { eventNames, useStoreDay } from './storeDay'
import './Home.css'

export default function HomePage() {
  return useHasRole('staff') ? <StaffHome /> : <Dashboard />
}

function StaffHome() {
  const { store, day, data, error, retry } = useStoreDay()
  const location = useLocation()
  const saved = location.state as { saved?: boolean; storeId?: number } | null
  return <div className="pc-home">
    <header className="pc-home-heading"><h1>开始工作</h1><p>{store?.name || 'Choose a store'} · {day}</p></header>
    <PunchIn home />
    {saved?.saved && saved.storeId === store?.id && <p className="pc-home-saved" role="status">Record saved / 已记录</p>}
    <nav className="pc-home-actions" aria-label="Store actions">
      <Link className="pc-home-checkout" to="/checkout"><CameraOutlined aria-hidden="true" /><span><strong>买单</strong><span>Checkout</span><small>订单 · 折扣 · 收款凭证</small></span><ArrowRightOutlined aria-hidden="true" /></Link>
      <Link to="/incoming"><InboxOutlined aria-hidden="true" /><span><strong>入店</strong><span>Receive goods</span><small>收货 · 调入 · Display</small></span><ArrowRightOutlined aria-hidden="true" /></Link>
      <Link to="/claw"><GiftOutlined aria-hidden="true" /><span><strong>娃娃机</strong><span>Claw machine</span><small>出奖 · 补货 · 换现金</small></span><ArrowRightOutlined aria-hidden="true" /></Link>
      <Link to="/summary"><FileTextOutlined aria-hidden="true" /><span><strong>汇总</strong><span>Daily summary</span><small>当日记录 · 对账 · 历史汇总</small></span><ArrowRightOutlined aria-hidden="true" /></Link>
    </nav>
    <section className="pc-home-records" aria-label="Recent records">
      <div className="pc-home-section-heading"><h2>今日记录 <small>Recent records</small></h2><Link to="/summary">View records</Link></div>
      {!store || store.code === 'ALL' ? <p>Choose one store to view its records.</p>
        : error ? <Alert role="alert" type="error" message={error} action={<Button onClick={retry}>Retry records</Button>} />
        : !data ? <Skeleton active paragraph={{ rows: 2 }} />
        : data.events.length === 0 ? <p className="pc-home-muted">No events recorded yet. Saved orders are available in <Link to="/checkout/history">Order history</Link>.</p>
        : <ul>{data.events.slice(-5).reverse().map(event => <li key={event.id}>
          <div><strong>{eventNames[event.kind] || event.kind}</strong><span>{event.kind === 'cash_exchange' ? formatCents(event.amount_cents || 0) : `${event.product_name || 'Product'} × ${event.quantity}`}</span></div>
          <small>{event.actor_name} · {new Date(event.created_at).toLocaleTimeString('en-CA', { timeZone: 'America/Toronto', hour: '2-digit', minute: '2-digit' })}</small>
        </li>)}</ul>}
    </section>
    <MyShifts />
  </div>
}
