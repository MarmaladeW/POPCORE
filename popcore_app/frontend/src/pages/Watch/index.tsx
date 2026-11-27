import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, Button, Skeleton, message } from 'antd'

import { checkWatchAlert, getWatch, runWatchNow, type WatchAlert, type WatchView } from '../../api/watch'
import PageHeader from '../../components/pc/PageHeader'
import { ListRow, Panel } from '../../components/pc/Panel'
import { kindLabels, kindLinks, outcomeText, splitAlerts, statusLine, torontoTime } from './watchView'
import Notifications from '../WebOrders/Notifications'
import '../WebOrders/webOrders.css'
import './watch.css'

function AlertRow({ alert, busy, onCheck }: { alert: WatchAlert; busy?: boolean; onCheck?: () => void }) {
  const kind = kindLabels[alert.kind]
  const note = [alert.note_zh, alert.note_en].filter(Boolean).join(' · ')
  const when = alert.business_date || torontoTime(alert.created_at)
  const settled = alert.status === 'checked' ? `已查看 Checked${alert.checked_by ? ` · ${alert.checked_by}` : ''}`
    : alert.status === 'resolved' ? '已自行恢复 Settled by itself' : ''
  return <div className="pc-watch-alert">
    <ListRow label={kind.zh}
      title={<>{alert.severity === 'high' && <span className="pc-watch-urgent">要紧 Urgent</span>}{alert.title_zh}</>}
      meta={[alert.title_en, alert.store, when, settled].filter(Boolean).join(' · ')}
      trailing={<span className="pc-watch-actions">
        <Link className="pc-btn" to={kindLinks[alert.kind]}>查看 Open</Link>
        {onCheck && <Button className="pc-btn" loading={busy} onClick={onCheck}>已查看 Checked</Button>}
      </span>} />
    {note && <p className="pc-watch-note"><span>Claude</span>{note}</p>}
  </div>
}

// 异常提醒: what the watcher found (owner, 2026-11-24). Managers see their stores', admins all and the watcher's status.
export default function WatchPage() {
  const [data, setData] = useState<WatchView>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(0)
  const [running, setRunning] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setError('')
    getWatch(controller.signal).then(setData).catch((cause: any) => {
      if (cause?.code !== 'ERR_CANCELED') setError(cause?._serverMessage || 'Unable to load alerts.')
    })
    return () => controller.abort()
  }, [attempt])

  async function check(id: number) {
    setBusy(id)
    try {
      await checkWatchAlert(id)
      setAttempt(value => value + 1)
    } catch (cause: any) {
      message.error(cause?._serverMessage || 'That did not save. Try again.')
    } finally { setBusy(0) }
  }

  async function runNow() {
    setRunning(true)
    try {
      const result = await runWatchNow()
      message.success(result.status.review_requested
        ? `找到 ${result.found} 条新提醒，Claude 十分钟内看完 · ${result.found} new; Claude reviews within 10 minutes`
        : `找到 ${result.found} 条新提醒 · ${result.found} new`)
      setAttempt(value => value + 1)
    } catch (cause: any) {
      message.error(cause?._serverMessage || 'The check did not run.')
    } finally { setRunning(false) }
  }

  const status = data?.status
  const { look, quiet } = splitAlerts(data?.alerts ?? [])
  return <div className="pc-watch">
    <PageHeader title="异常提醒" sub="Alerts · 经理和管理员 Managers and admins"
      actions={data?.admin && status?.enabled ? <Button className="pc-btn" loading={running} onClick={runNow}>立即检查 Check now</Button> : undefined} />
    {error && <Alert type="error" showIcon message={error} action={<Button onClick={() => setAttempt(value => value + 1)}>重试 Retry</Button>} />}
    {!data && !error && <div className="pc-panel"><div className="pc-quiet"><Skeleton active /></div></div>}
    {status && <Panel zh="监控" en="Watcher" label="Watcher status"
      action={<Link className="pc-btn" to="/settings">设置 Settings</Link>}>
      <p className="pc-watch-status">{statusLine(status)}</p>
      {status.last_review && <p className="pc-watch-status">
        上次 Claude 检查 Last review: {torontoTime(status.last_review.at)} · {outcomeText(status.last_review.outcome)}
        {status.last_review.cost_usd > 0 && ` · $${status.last_review.cost_usd.toFixed(3)}`}
      </p>}
      {(status.last_review?.summary_zh || status.last_review?.summary_en) && <p className="pc-watch-note pc-watch-summary">
        <span>Claude</span>{[status.last_review.summary_zh, status.last_review.summary_en].filter(Boolean).join(' · ')}</p>}
      {status.last_sent && <details className="pc-watch-sent">
        <summary>发给 Claude 的全部内容 Everything sent to Claude ({torontoTime(status.last_sent.at)})</summary>
        <p>只有代号和数字：S = 门店，P = 产品，A = 提醒。Only stand-in codes and numbers: S store, P product, A alert.</p>
        <pre>{JSON.stringify(status.last_sent.payload, null, 2)}</pre>
      </details>}
    </Panel>}
    {data && <Panel zh="要看" en="Needs a look" label="Open alerts" count={look.length} tone={look.length ? 'warn' : 'ok'}>
      {look.length === 0 ? <p className="pc-quiet">没有异常 · Nothing unusual</p>
        : look.map(alert => <AlertRow key={alert.id} alert={alert} busy={busy === alert.id} onCheck={() => check(alert.id)} />)}
      <p className="pc-quiet">手机通知只说有几条，不写内容。Phone notifications say only how many, never what.</p>
    </Panel>}
    <Notifications alerts />
    {quiet.length > 0 && <details className="pc-panel pc-watch-fold">
      <summary>Claude 认为正常 · Looked normal to Claude ({quiet.length})</summary>
      {quiet.map(alert => <AlertRow key={alert.id} alert={alert} busy={busy === alert.id} onCheck={() => check(alert.id)} />)}
    </details>}
    {data && data.recent.length > 0 && <details className="pc-panel pc-watch-fold">
      <summary>最近两周已处理 · Settled in the last two weeks ({data.recent.length})</summary>
      {data.recent.map(alert => <AlertRow key={alert.id} alert={alert} />)}
    </details>}
  </div>
}
