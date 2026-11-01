import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, Button, Skeleton, message } from 'antd'

import {
  checkReportStockNotice, getReportStockNotices, reasonText, retryReportStock,
  type ReportStockNotice, type ReportStockReason,
} from '../../api/reportStock'
import PageHeader from '../../components/pc/PageHeader'
import { ListRow, Panel } from '../../components/pc/Panel'
import { useAppStore } from '../../store'
import { dayTitle } from './SalesNav'
import './reportStock.css'

// The floor count's time, saved in UTC, shown as Toronto time.
function torontoTime(utc: string) {
  const time = new Date(`${utc.replace(' ', 'T')}Z`)
  return Number.isNaN(time.getTime()) ? utc
    : time.toLocaleTimeString('en-CA', { timeZone: 'America/Toronto', hour: '2-digit', minute: '2-digit', hour12: false })
}

// 日报库存核对: what a live store's daily report could not take off the floor, or may have taken
// twice, until a later save settles it or a manager checks it (owner, 2026-10-29).
export default function ReportStockPage() {
  const store = useAppStore(state => state.selectedStore)
  const code = store?.code || 'ALL'
  const [notices, setNotices] = useState<ReportStockNotice[]>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setError('')
    getReportStockNotices(code, controller.signal).then(setNotices).catch((cause: any) => {
      if (cause?.code !== 'ERR_CANCELED') setError(cause?._serverMessage || 'Unable to load report stock checks.')
    })
    return () => controller.abort()
  }, [code, attempt])

  async function run(key: string, action: () => Promise<string>) {
    setBusy(key)
    try {
      message.success(await action())
    } catch (cause: any) {
      message.error(cause?._serverMessage || 'That did not save. Try again.')
    } finally {
      setBusy('')
      setAttempt(value => value + 1)
    }
  }

  const days = new Map<string, ReportStockNotice[]>()
  for (const notice of notices ?? []) {
    const key = `${notice.store_code}|${notice.business_date}`
    days.set(key, [...(days.get(key) ?? []), notice])
  }
  const reasons = [...new Set((notices ?? []).map(notice => notice.reason))] as ReportStockReason[]

  return <div className="pc-report-stock">
    <PageHeader title="日报库存核对" sub={`Report stock checks · ${store?.name || 'All stores'}`} />
    {error ? <Alert role="alert" type="error" showIcon message={error}
      action={<Button onClick={() => setAttempt(value => value + 1)}>Retry</Button>} />
      : !notices ? <div className="pc-panel"><div className="pc-quiet"><Skeleton active /></div></div>
      : notices.length === 0 ? <p className="pc-panel pc-quiet">没有需要核对的 · Every daily report came off the floor as sold.</p>
      : <>
        <section className="pc-panel" aria-label="What each check means">
          {reasons.map(reason => <p key={reason} className="pc-quiet">
            <strong>{reasonText[reason].zh} {reasonText[reason].en}</strong>{reasonText[reason].help}
          </p>)}
        </section>
        {[...days.entries()].map(([key, rows]) => {
          const { store_code: storeCode, business_date: day } = rows[0]
          const retryable = rows.some(row => row.reason !== 'counted_same_day')
          return <Panel key={key} zh={`${dayTitle(day)} · ${storeCode}`} en={day} count={rows.length} label={`${storeCode} ${day}`}
            action={retryable ? <Button size="small" loading={busy === key} disabled={!!busy && busy !== key}
              onClick={() => run(key, async () => {
                const stock = await retryReportStock(storeCode, day)
                return `${stock?.taken ?? 0} taken off the floor`
              })}>再扣一次 Take off again</Button> : undefined}>
            {rows.map(row => <ListRow key={row.id} label={reasonText[row.reason].zh}
              title={`${row.name}${row.sku ? ` (${row.sku})` : ''} × ${row.quantity}`}
              meta={[reasonText[row.reason].en, `楼下 Floor ${row.floor}`, `楼上 Upstairs ${row.upstairs}`,
                row.counted_at ? `盘点 Counted ${torontoTime(row.counted_at)}` : null].filter(Boolean).join(' · ')}
              trailing={<span className="pc-row-actions">
                {row.reason === 'set_size_unknown' ? <Link className="pc-btn" to="/products">产品 Products</Link>
                  : <Link className="pc-btn" to={`/count?location=floor&product=${row.product_id}`}>盘点 Count</Link>}
                <Button size="small" loading={busy === `check:${row.id}`} disabled={!!busy && busy !== `check:${row.id}`}
                  aria-label={`Mark ${row.name} checked`}
                  onClick={() => run(`check:${row.id}`, async () => {
                    await checkReportStockNotice(row.id)
                    return `${row.name} checked`
                  })}>已核对 Checked</Button>
              </span>} />)}
          </Panel>
        })}
      </>}
  </div>
}
