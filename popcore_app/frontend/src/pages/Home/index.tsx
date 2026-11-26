import { useEffect, useState } from 'react'
import { Alert, Button, Skeleton } from 'antd'
import { Link, Navigate, useLocation } from 'react-router-dom'
import { getToday, type TodayPayload } from '../../api/today'
import { useHasRole } from '../../auth/useRole'
import PageHeader from '../../components/pc/PageHeader'
import { ListRow, Panel, SectionHeading } from '../../components/pc/Panel'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { formatCents } from '../../lib/money'
import { useAppStore } from '../../store'
import Dashboard from '../Dashboard'
import MyShifts from '../Dashboard/MyShifts'
import { TodayRows } from '../Dashboard/TodayRows'
import { mergeWork } from '../Dashboard/todayPresentation'
import PunchIn from '../Schedule/PunchIn'
import HomeTiles from './HomeTiles'
import { PinPrompt } from '../../components/people/MyPin'
import { eventNames, useStoreDay, type StoreEvent } from './storeDay'
import './Home.css'

const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
const eventPlace: Record<string, string> = { claw_prize: '娃娃机', claw_refill: '娃娃机', display_in: 'Display' }
const eventLabel: Record<string, string> = { claw_prize: '出奖', claw_refill: '补货', cash_exchange: '换现金', display_in: 'Display' }

export default function HomePage() {
  const staff = useHasRole('staff')
  const manager = useHasRole('manager')
  return manager ? <Dashboard key="manager" home /> : staff ? <StaffHome /> : <StoreComputerHome />
}

// The 查看者 Viewer is the store computer's account (owner, 2026-11-24): it opens on the orders to prepare. Until an admin
// ticks 店铺电脑 for a store in Users, it says so and offers what it can already look at.
function StoreComputerHome() {
  const stores = useAppStore(state => state.stores)
  if (!stores.length) return <div role="status" aria-live="polite">Loading page…</div>
  if (stores.some(store => store.operations)) return <Navigate to="/web-orders" replace />
  return <div className="pc-hm">
    <PageHeader roomy title="店铺电脑" sub="Store computer" />
    <Panel zh="还没有门店" en="No store yet" label="No store yet">
      <p className="pc-quiet">管理员在 员工 Users 里为这个账户勾选 店铺电脑 的门店后，这里会显示要准备的网站订单和库存。<br />
        Once an admin ticks 店铺电脑 Store computer for a store in Users, this account opens on that store's web orders and stock.</p>
    </Panel>
    <nav className="pc-hm-links" aria-label="Look up">
      <Link to="/products">产品 Products</Link>
      <Link to="/dex">图鉴 Dex</Link>
      <Link to="/watch-list">可疑人员 Watch list</Link>
    </nav>
  </div>
}

function zhDate(day: string) {
  const [year, month, date] = day.split('-').map(Number)
  return `${weekdays[new Date(Date.UTC(year, month - 1, date)).getUTCDay()]} ${month}月${date}日`
}

// Unfinished orders, receiving and counts for this person and store (the Today work lists).
function useStoreTasks(storeCode: string | undefined, day: string) {
  const [result, setResult] = useState<{ key: string; data?: TodayPayload }>()
  const key = `${storeCode}|${day}`
  useEffect(() => {
    if (!storeCode) return
    const controller = new AbortController()
    getToday(storeCode, day, controller.signal).then(data => setResult({ key, data })).catch(() => {
      if (!controller.signal.aborted) setResult({ key })
    })
    return () => controller.abort()
  }, [key, storeCode, day])
  const data = result?.key === key ? result.data : undefined
  const tasks = data ? mergeWork(data.sections).tasks : undefined
  return { data, rows: tasks?.rows || [], total: data ? tasks?.total || 0 : undefined }
}

function RecordRow({ event }: { event: StoreEvent }) {
  const place = eventPlace[event.kind]
  return <ListRow label={eventLabel[event.kind] || eventNames[event.kind] || event.kind}
    title={event.kind === 'cash_exchange' ? formatCents(event.amount_cents || 0) : `${event.product_name || 'Product'} × ${event.quantity}`}
    meta={[event.actor_name, place].filter(Boolean).join(' · ')}
    trailing={<time className="pc-row-time" dateTime={event.created_at}>{new Date(event.created_at).toLocaleTimeString('en-CA', { timeZone: 'America/Toronto', hour: '2-digit', minute: '2-digit', hour12: false })}</time>} />
}

function StaffHome() {
  const { store, day, data, error, retry } = useStoreDay()
  const phone = useIsPhone()
  const location = useLocation()
  const saved = location.state as { saved?: boolean; storeId?: number } | null
  const tasks = useStoreTasks(store?.code, day)

  const records = !store || store.code === 'ALL' ? <p className="pc-quiet">Choose one store to view its records.</p>
    : error ? <Alert role="alert" type="error" message={error} action={<Button onClick={retry}>Retry records</Button>} />
    : !data ? <div className="pc-quiet"><Skeleton active title={false} paragraph={{ rows: 2 }} /></div>
    : data.events.length === 0 ? <p className="pc-quiet"><strong>今天还没有记录</strong>No records yet · 订单在 <Link to="/checkout/history">Order history</Link></p>
    : data.events.slice(-5).reverse().map(event => <RecordRow key={event.id} event={event} />)
  const viewAll = <Link to="/summary">查看全部{phone ? '' : ' View records'}</Link>
  const nothing = tasks.total === 0

  return <div className="pc-hm">
    <PageHeader roomy title="开始工作" sub={phone ? `${zhDate(day)} · Start work` : `Start work · ${store?.name || 'Choose a store'}`} actions={<PunchIn home />} />
    {phone && <PunchIn home />}
    {saved?.saved && saved.storeId === store?.id && <p className="pc-hm-saved" role="status">已记录 Record saved</p>}
    <HomeTiles details />
    <PinPrompt />
    {phone && <Link className="pc-hm-tasks" to="/today">
      <span><strong>待处理 Store tasks</strong><small>{nothing ? '没有未完成的事 · Nothing to finish' : '未完成的订单、收货和盘点'}</small></span>
      {tasks.total !== undefined && <span className="pc-hm-tasks-count"><i className={`pc-dot pc-dot-${nothing ? 'ok' : 'warn'}`} aria-hidden="true" />{tasks.total}</span>}
    </Link>}
    <div className="pc-hm-grid">
      <div className="pc-hm-main">
        {!phone && <Panel zh={<Link to="/today">待处理</Link>} en="Store tasks" label="Store tasks" count={tasks.total === undefined ? undefined : `${tasks.total} 项`} tone={nothing ? 'ok' : 'warn'}>
          {tasks.rows.length && tasks.data ? <TodayRows rows={tasks.rows} stores={tasks.data.authorized_stores} />
            : <p className="pc-quiet">{tasks.data ? '没有未完成的事 · Nothing to finish' : 'Continue unfinished orders, receiving and counts.'}</p>}
        </Panel>}
        {phone ? <section className="pc-hm-records" aria-label="Recent records">
          <SectionHeading zh="今日记录" en="Recent records" action={viewAll} />
          <div className="pc-panel">{records}</div>
        </section>
          : <Panel zh="今日记录" en="Recent records" label="Recent records" action={viewAll}>{records}</Panel>}
      </div>
      <aside className="pc-hm-side"><MyShifts outside={phone} /></aside>
    </div>
  </div>
}
