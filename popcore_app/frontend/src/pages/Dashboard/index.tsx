import { useEffect, useRef, useState } from 'react'
import { Alert, Button, List, Skeleton, Tag, Typography } from 'antd'
import { Link } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'

import { getToday, type TodayPayload, type TodayRow, type TodaySection } from '../../api/today'
import { useHasRole, useRole } from '../../auth/useRole'
import { useAppStore } from '../../store'
import MyShifts from './MyShifts'
import PunchIn from '../Schedule/PunchIn'
import { torontoDate } from './todayPresentation'

const sectionLabels: Record<string, string> = {
  my_work: 'My work', operations: 'Store work', financial: 'Sales and tender checks',
  catalog: 'Catalog notices', inventory: 'Inventory notices',
}
const typeLabels: Record<string, string> = {
  sale: 'Sale', financial_sale: 'Sale', allocation_exception: 'Sale',
  payment_evidence: 'Payment evidence', payment_exception: 'Payment', receipt: 'Receipt',
  count: 'Count', closing: 'Closing', cash_variance: 'Closing', delivery: 'Transfer',
  restock: 'Restock', condition_case: 'Condition case', catalog_identity: 'Product',
  inventory_notice: 'Product',
}
const statusLabels: Record<string, string> = {
  draft: 'Draft', posted: 'Recorded', pending: 'Pending', submitted: 'Awaiting review',
  returned: 'Returned for changes', active: 'In transit', planned: 'Planned',
  evidence_pending: 'Evidence awaiting review', unresolved: 'Identity needs review',
  out_of_stock: 'Out of stock', variance: 'Variance needs review',
  allocated: 'Stock allocated', verified: 'Verified', recorded: 'Awaiting verification',
}

function rowTitle(row: TodayRow) {
  if (row.label) return row.label
  return `${typeLabels[row.type] || row.type.replaceAll('_', ' ')} #${row.source_id}`
}

function actionLabel(row: TodayRow) {
  const label = typeLabels[row.type] || 'item'
  const verbs: Record<string, string> = {
    sale: 'Resume', financial_sale: 'Review', allocation_exception: 'Resolve',
    payment_evidence: 'Add evidence to', payment_exception: 'Review', receipt: 'Resume',
    count: row.status === 'submitted' ? 'Review' : 'Resume', closing: 'Continue',
    cash_variance: 'Review', delivery: 'Continue', restock: 'Continue',
    condition_case: 'Review', catalog_identity: 'Review', inventory_notice: 'View',
  }
  return `${verbs[row.type] || 'Open'} ${label.toLowerCase()} #${row.source_id}`
}

function Section({ name, section, stores }: {
  name: string
  section: TodaySection
  stores: TodayPayload['authorized_stores']
}) {
  const storeNames = new Map(stores.map(store => [store.id, store.name || store.code]))
  return <section className="pc-today-section" aria-labelledby={`today-${name}`}>
    <div className="pc-section-heading">
      <Typography.Title id={`today-${name}`} level={4}>{sectionLabels[name] || name}</Typography.Title>
      <span>{section.total}</span>
    </div>
    {section.rows.length === 0
      ? <p className="pc-quiet-empty">No current items.</p>
      : <List dataSource={section.rows} renderItem={row => <List.Item
          actions={[<Link key="open" to={row.link}>{actionLabel(row)}</Link>]}
        >
          <List.Item.Meta
            title={<span className="pc-work-title"><span>{rowTitle(row)}</span><Tag>{statusLabels[row.status] || row.status.replaceAll('_', ' ')}</Tag></span>}
            description={[
              row.store_id ? (storeNames.get(row.store_id) || `Store reference ${row.store_id}`) : null,
              row.updated_at,
            ].filter(Boolean).join(' · ')}
          />
        </List.Item>} />}
  </section>
}

export default function DashboardPage() {
  const { user } = useAuth0()
  const role = useRole()
  const staff = useHasRole('staff')
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
    getToday(store.code, torontoDate(), controller.signal).then(value => {
      if (current === request.current) { setData(value); setLast(new Date().toLocaleTimeString()) }
    }).catch((cause: any) => {
      if (current === request.current && cause?.code !== 'ERR_CANCELED') {
        setError(cause?._serverMessage || 'Unable to load Today.')
      }
    }).finally(() => { if (current === request.current) setLoading(false) })
    return () => { request.current++; controller.abort() }
  }, [attempt, role, store?.code, user?.sub])

  const order = ['my_work', 'operations', 'financial', 'catalog', 'inventory']
  return <div className="pc-page pc-today">
    <header className="pc-page-heading">
      <Typography.Title level={2}>Today / 今日</Typography.Title>
      <Typography.Text type="secondary">{torontoDate()} · {store?.name || 'Choose a store'}</Typography.Text>
    </header>
    <PunchIn home />
    {staff && <MyShifts key={`${user?.sub}:${role}`} />}
    {loading && !data ? <div className="pc-loading-panel"><Skeleton active /></div>
      : error && !data ? <Alert role="alert" type="error" showIcon message={error} action={<Button onClick={() => setAttempt(value => value + 1)}>Retry</Button>} />
      : <>
        {error && <Alert type="warning" showIcon message="Today could not refresh"
          description={last && `Showing data refreshed at ${last}`}
          action={<Button onClick={() => setAttempt(value => value + 1)}>Retry</Button>} />}
        {data?.store_ids.length === 0 && <Alert type="info" showIcon message="No inventory store access"
          description="Your permitted catalog notices and personal schedule remain available." />}
        {data && Object.entries(data.sections)
          .sort(([a], [b]) => order.indexOf(a) - order.indexOf(b))
          .map(([name, section]) => <Section key={name} name={name} section={section} stores={data.authorized_stores} />)}
      </>}
  </div>
}
