import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, Button, Skeleton, message } from 'antd'

import {
  checkReportStockNotice, getReportStockNotices, reasonText, retryReportStock, whereText,
  type ReportStockNotice, type ReportStockReason,
} from '../../api/reportStock'
import PageHeader from '../../components/pc/PageHeader'
import { ListRow, Panel } from '../../components/pc/Panel'
import { useAppStore } from '../../store'
import { MarkForm, MarkedPlaces, markDay } from '../Count/CountMarks'
import { dayTitle } from './SalesNav'
import './reportStock.css'

// The floor count's time, saved in UTC, shown as Toronto time.
function torontoTime(utc: string) {
  const time = new Date(`${utc.replace(' ', 'T')}Z`)
  return Number.isNaN(time.getTime()) ? utc
    : time.toLocaleTimeString('en-CA', { timeZone: 'America/Toronto', hour: '2-digit', minute: '2-digit', hour12: false })
}

// 下次盘点: where to count a notice's product. The place it was short, and upstairs or the warehouse when they
// hold some (stock may have come down without a line, as Eevee at DT on 2026-11-20).
function presetPlaces(row: ReportStockNotice) {
  const short = row.reason === 'move_short' ? (row.where ?? []).filter(code => code in (row.stock ?? {})) : ['floor']
  const back = ['upstairs', 'warehouse'].filter(code => (row.stock?.[code] ?? 0) > 0)
  return [...new Set([...short, ...back])]
}
const presetReason = (row: ReportStockNotice) => {
  const [, month, day] = row.business_date.split('-')
  return `日报 ${Number(month)}/${Number(day)} ${reasonText[row.reason].zh} × ${row.quantity}`
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
  const [marking, setMarking] = useState(0)

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
                const opened = (stock?.sets_opened ?? []).filter(row => row.sets > 0)
                return `${stock?.taken ?? 0} taken off the floor${opened.length ? ` · ${opened.map(row => row.text).join('; ')}` : ''}`
              })}>再扣一次 Take off again</Button> : undefined}>
            {rows.map(row => <div key={row.id} className="pc-stock-notice"><ListRow label={reasonText[row.reason].zh}
              title={`${row.name}${row.sku ? ` (${row.sku})` : ''} × ${row.quantity}`}
              meta={[reasonText[row.reason].en,
                ...(row.reason === 'move_short' && row.where?.length
                  ? row.where.map(code => code in (row.stock ?? {}) ? `${whereText(code)} ${row.stock![code]}` : whereText(code))
                  : [`楼下 Floor ${row.floor}`, `楼上 Upstairs ${row.upstairs}`]),
                row.counted_at ? `盘点 Counted ${torontoTime(row.counted_at)}` : null].filter(Boolean).join(' · ')}
              trailing={<span className="pc-row-actions">
                {row.reason === 'set_size_unknown' ? <Link className="pc-btn" to="/products">产品 Products</Link>
                  : <Link className="pc-btn" to={`/count?location=${row.where?.find(code => code in (row.stock ?? {})) ?? 'floor'}&product=${row.product_id}`}>盘点 Count</Link>}
                {marking !== row.id && <Button size="small" onClick={() => setMarking(row.id)}>下次盘点 Count next time</Button>}
                <Button size="small" loading={busy === `check:${row.id}`} disabled={!!busy && busy !== `check:${row.id}`}
                  aria-label={`Mark ${row.name} checked`}
                  onClick={() => run(`check:${row.id}`, async () => {
                    await checkReportStockNotice(row.id)
                    return `${row.name} checked`
                  })}>已核对 Checked</Button>
              </span>} />
              {(row.marks?.length || row.counted_marks?.length || marking === row.id) ? <div className="pc-stock-notice-marks">
                {row.counted_marks?.map(mark => <p key={mark.id} className="pc-stock-notice-counted">
                  <span className="pc-mark-flag is-done">已点 Counted {whereText(mark.place)}</span>
                  <small>{mark.closed_by} {mark.closed_at ? markDay(mark.closed_at) : ''} · 看过再按 已核对 Check it, then press 已核对</small>
                </p>)}
                <MarkedPlaces marks={row.marks ?? []} nameOf={code => whereText(code)} onRemoved={() => setAttempt(value => value + 1)} />
                {marking === row.id && <MarkForm storeCode={storeCode} productId={row.product_id} productName={row.name}
                  sealedSet={row.unit === 'set'} preset={presetPlaces(row)} reason={presetReason(row)} noticeId={row.id}
                  marked={(row.marks ?? []).map(mark => mark.place)} nameOf={code => whereText(code)}
                  onCancel={() => setMarking(0)}
                  onMarked={() => { setMarking(0); message.success(`${row.name} 待盘点 · marked to count next time`); setAttempt(value => value + 1) }} />}
              </div> : null}
            </div>)}
          </Panel>
        })}
      </>}
  </div>
}
