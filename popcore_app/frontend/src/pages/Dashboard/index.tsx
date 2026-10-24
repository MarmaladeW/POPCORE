import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Skeleton } from 'antd'
import { Link, useLocation } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'

import { getToday, type TodayPayload } from '../../api/today'
import { useHasRole, useRole } from '../../auth/useRole'
import { useAppStore } from '../../store'
import PageHeader from '../../components/pc/PageHeader'
import { ListRow, Panel } from '../../components/pc/Panel'
import { useIsPhone } from '../../components/pc/useIsPhone'
import HomeTiles from '../Home/HomeTiles'
import MyShifts from './MyShifts'
import PunchIn from '../Schedule/PunchIn'
import { sectionLabels, TodayRows } from './TodayRows'
import { mergeWork, torontoDate } from './todayPresentation'
import '../Home/Home.css'

// Today's numbers on the manager home: only counts the Today payload already carries.
// The catalog list stops at 51 on the server, so 51 means "more than 50".
const shownTotal = (name: string, total: number) => name === 'catalog' && total > 50 ? '50+' : total

// On the manager home, long notice lists collapse to one summary row (Figma 产品资料 panel).
const summaries: Record<string, { title: (total: string | number) => string; meta: string; action: string; to: string }> = {
  catalog: { title: total => `${total} 个产品还没审核`, meta: 'Products need identity review before counting', action: '去审核 Review', to: '/products' },
  inventory: { title: total => `${total} 条缺货提醒`, meta: 'Out-of-stock notices (product and location)', action: '查看 View', to: '/stock' },
}

const strip: Array<{ key: string; zh: string; en: string; tone: 'warn' | 'out' }> = [
  { key: 'financial', zh: '待核对收款', en: 'Tender checks', tone: 'warn' },
  { key: 'catalog', zh: '待审核产品', en: 'Products to review', tone: 'warn' },
  { key: 'inventory', zh: '缺货', en: 'Out of stock', tone: 'out' },
]

export default function DashboardPage({ home = false }: { home?: boolean }) {
  const location = useLocation()
  const saved = location.state as { saved?: boolean; storeId?: number } | null
  const { user } = useAuth0()
  const role = useRole()
  const staff = useHasRole('staff')
  const manager = useHasRole('manager')
  const store = useAppStore(state => state.selectedStore)
  const [data, setData] = useState<TodayPayload>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [last, setLast] = useState('')
  const [attempt, setAttempt] = useState(0)
  const request = useRef(0)

  useEffect(() => {
    const controller = new AbortController()
    const current = ++request.current
    setData(undefined); setLast(''); setLoading(true); setError('')
    if (!store?.code) { setLoading(false); return () => controller.abort() }
    // A store this person cannot open shows only what is not tied to a store (catalog notices), never an access error.
    const storeIndependent = (value: TodayPayload): TodayPayload => ({ ...value, sections: value.sections.catalog ? { catalog: value.sections.catalog } : {} })
    getToday(store.code, torontoDate(), controller.signal)
      .catch((cause: any) => cause?.response?.status === 403 && store.code !== 'ALL'
        ? getToday('ALL', torontoDate(), controller.signal).then(storeIndependent)
        : Promise.reject(cause))
      .then(value => {
      if (current === request.current) { setData(value); setLast(new Date().toLocaleTimeString()) }
    }).catch((cause: any) => {
      if (current === request.current && cause?.response?.status === 403) { setData({ business_date: torontoDate(), generated_at: '', role, scope: store.code, store_ids: [], authorized_stores: [], sections: {} }); return }
      if (current === request.current && cause?.code !== 'ERR_CANCELED') {
        setError(cause?._serverMessage || 'Unable to load Today.')
      }
    }).finally(() => { if (current === request.current) setLoading(false) })
    return () => { request.current++; controller.abort() }
  }, [attempt, role, store?.code, user?.sub])

  const phone = useIsPhone()
  const order = home ? ['financial', 'tasks', 'catalog', 'inventory'] : ['tasks', 'financial', 'catalog', 'inventory']
  const insights = home && manager ? <Link className="pc-btn" to="/reports">报表 Insights &amp; reports</Link> : null
  const sections = data ? Object.entries(mergeWork(data.sections))
    .filter(([, section]) => !home || section.total > 0)
    .sort(([a], [b]) => order.indexOf(a) - order.indexOf(b)) : []
  return <div className={home ? 'pc-hm' : 'pc-hm pc-today'}>
    <PageHeader
      title={home ? '门店概览' : 'Today / 今日'}
      sub={home ? `Store overview · ${store?.name || 'Choose a store'}` : `${torontoDate()} · ${store?.name || 'Choose a store'}`}
      actions={<><PunchIn home />{insights}</>}
    />
    {phone && <PunchIn home />}
    {home && saved?.saved && saved.storeId === store?.id && <p className="pc-hm-saved" role="status">已记录 Record saved</p>}
    {home && <HomeTiles />}
    {home && manager && data && <div className="pc-hm-strip">
      {strip.filter(cell => data.sections[cell.key]).map(cell => {
        const total = data.sections[cell.key].total
        return <div key={cell.key}><span>{cell.zh} {cell.en}</span><strong className={total > 0 ? `pc-hm-count-${cell.tone}` : undefined}>{shownTotal(cell.key, total)}</strong></div>
      })}
    </div>}
    <div className="pc-hm-grid pc-hm-shifts-first">
      <div className="pc-hm-main">
        {loading && !data ? <div className="pc-panel"><div className="pc-quiet"><Skeleton active /></div></div>
          : error && !data ? <Alert role="alert" type="error" showIcon message={error} action={<Button onClick={() => setAttempt(value => value + 1)}>Retry</Button>} />
          : <>
            {error && <Alert type="warning" showIcon message="Today could not refresh"
              description={last && `Showing data refreshed at ${last}`}
              action={<Button onClick={() => setAttempt(value => value + 1)}>Retry</Button>} />}
            {home && data && sections.length === 0 && <p className="pc-panel pc-quiet">No current items to review. Open Daily summary to check today's recorded activity.</p>}
            {!home && data && sections.length === 0 && <p className="pc-panel pc-quiet">没有需要处理的事 · Nothing to review</p>}
            {data && sections.map(([name, section]) => <Panel key={name} label={sectionLabels[name]?.en || name}
              zh={sectionLabels[name]?.zh || name} en={sectionLabels[name]?.en} count={shownTotal(name, section.total)} tone={section.total > 0 ? 'warn' : 'ok'}>
              {section.rows.length === 0 ? <p className="pc-quiet">No current items.</p>
                : home && summaries[name] ? <ListRow title={summaries[name].title(shownTotal(name, section.total))} meta={summaries[name].meta}
                  trailing={<Link className="pc-btn" to={summaries[name].to}>{summaries[name].action}</Link>} />
                : <TodayRows rows={section.rows} stores={data.authorized_stores} />}
            </Panel>)}
          </>}
        {home && manager && <nav className="pc-hm-links" aria-label="Manager review">
          {phone && <Link to="/reports">报表 Insights &amp; reports</Link>}
          <Link to="/sales/matching">核对旧销售名称 Review past sales names</Link>
        </nav>}
      </div>
      {staff && <aside className="pc-hm-side"><MyShifts key={`${user?.sub}:${role}`} outside={phone} /></aside>}
    </div>
  </div>
}
