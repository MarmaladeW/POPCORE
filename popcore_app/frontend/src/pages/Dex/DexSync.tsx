import { useEffect, useState } from 'react'
import { Alert, Button, Input, Modal, Popconfirm, Skeleton, message } from 'antd'
import { Link } from 'react-router-dom'

import {
  dexSyncPreview, dexSyncStatus, retryDexSync, switchDexSync, type DexSyncPreview, type DexSyncStatus,
} from '../../api/dex'
import { useHasRole } from '../../auth/useRole'
import PageHeader from '../../components/pc/PageHeader'
import { ListRow, Panel } from '../../components/pc/Panel'
import { KIND_LABEL } from '../../lib/dexFields'
import { errorText } from './DexParts'
import './dex.css'

// Times are saved in UTC and shown as Toronto time.
export function when(utc: string | null) {
  if (!utc) return '—'
  const time = new Date(`${utc.replace(' ', 'T').replace(/Z$/, '')}Z`)
  return Number.isNaN(time.getTime()) ? utc
    : time.toLocaleString('en-CA', { timeZone: 'America/Toronto', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
}

const kindName = (kind: keyof typeof KIND_LABEL) => `${KIND_LABEL[kind].zh} ${KIND_LABEL[kind].en}`

// 图鉴同步 Dex sync (owner, 2026-11-14: "yes build the POPCORE -> popcore.ca sync"): POPCORE sends the book to
// popcore.ca behind its own switch, apart from the stock sync's. Managers see it and retry refused entries; only an
// admin turns it on, after seeing what the first send does.
export default function DexSyncPage() {
  const admin = useHasRole('admin')
  const [status, setStatus] = useState<DexSyncStatus>()
  const [preview, setPreview] = useState<DexSyncPreview>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState('')
  const [turningOn, setTurningOn] = useState(false)
  const [typed, setTyped] = useState('')
  const [switchError, setSwitchError] = useState('')
  const [showAll, setShowAll] = useState(false)

  useEffect(() => {
    let live = true
    const load = () => Promise.all([dexSyncStatus(), dexSyncPreview()]).then(([next, nextPreview]) => {
      if (live) { setStatus(next); setPreview(nextPreview); setError('') }
    }).catch(cause => { if (live) setError(errorText(cause, 'Unable to load the Dex sync.')) })
    load()
    // The sync runs every 30 seconds on the server; keep the page in step while it is open.
    const timer = window.setInterval(load, 15000)
    return () => { live = false; window.clearInterval(timer) }
  }, [attempt])

  async function run(key: string, action: () => Promise<string>) {
    setBusy(key)
    try {
      message.success(await action())
    } catch (cause) {
      message.error(errorText(cause))
    } finally {
      setBusy('')
      setAttempt(n => n + 1)
    }
  }

  async function turnOn() {
    if (!preview) return
    setBusy('switch'); setSwitchError('')
    try {
      await switchDexSync(true, preview.delete ? Number(typed.trim()) : undefined)
      setTurningOn(false)
      message.success('图鉴同步已开启 · Dex sync is on')
    } catch (cause) {
      setSwitchError(errorText(cause, 'It did not turn on. Try again.'))
    } finally {
      setBusy('')
      setAttempt(n => n + 1)
    }
  }

  const header = <PageHeader title="图鉴同步" sub="Dex sync · popcore.ca" back={{ label: '图鉴 Dex', to: '/dex' }} />
  if (error) return <div className="pc-dex">{header}
    <Alert role="alert" type="error" showIcon message={error} action={<Button onClick={() => setAttempt(n => n + 1)}>Retry</Button>} />
  </div>
  if (!status || !preview) return <div className="pc-dex">{header}<div className="pc-panel"><div className="pc-quiet"><Skeleton active /></div></div></div>

  const counts = status.counts
  const missing = status.website_check && !status.website_check.ok ? status.website_check : null
  const deletes = showAll ? preview.deletes : preview.deletes.slice(0, 30)
  const typedOk = !preview.delete || typed.trim() === String(preview.delete)

  return <div className="pc-dex pc-dex-sync">
    {header}
    <Panel zh="图鉴同步" en="Dex sync" label="Dex sync" tone={status.enabled ? 'ok' : 'warn'} count={status.enabled ? '开 On' : '关 Off'}
      action={admin && status.configured ? (status.enabled
        ? <Popconfirm title="Turn the Dex sync off?" description="The website keeps the book as it last had it. Nothing more is sent until it is on again."
          okText="Turn off" onConfirm={() => run('switch', async () => { await switchDexSync(false); return '图鉴同步已关闭 · Dex sync is off' })}>
          <Button size="small" danger loading={busy === 'switch'} disabled={!!busy}>关闭 Turn off</Button>
        </Popconfirm>
        : <Button size="small" type="primary" disabled={!!busy} onClick={() => { setTyped(''); setSwitchError(''); setTurningOn(true) }}>
          开启 Turn on</Button>) : undefined}>
      {!status.configured ? <p className="pc-quiet">{status.settings_problem || 'No website key is set on the server yet.'}</p> : <>
        {missing && <Alert type="warning" showIcon message="网站这边还没有 · The website’s half of the book sync is not there yet"
          description={`${missing.code === 'website_half_missing' ? 'popcore.ca does not answer its book route yet.' : missing.message} Checked ${when(missing.at)} when an admin tried to turn it on. Nothing is sent until popcore.ca has it.`} />}
        {status.last_error && <Alert type="error" showIcon message={status.last_error.message}
          description={`上次失败 Last failed ${when(status.last_error.at)}. The next pass tries again; nothing from that call was marked sent.`} />}
        <ListRow label="网站" title={status.url} meta="The book goes to the website’s own book route, with the stock sync’s key. The stock sync’s switch is separate." />
        <ListRow label="状态" title={status.enabled ? `开 On since ${when(status.switched_at)}` : '关 Off: nothing is sent to the website'}
          meta={`上次运行 Last run ${when(status.last_run)} · 上次发送 Last sent ${when(status.last_sent_at)}${status.last_sent_count ? ` (${status.last_sent_count})` : ''}${status.enabled && !status.first_send_done ? ' · 首次发送中 First full send in progress (quiet: no follower notices)' : ''}`} />
        {!admin && !status.enabled && <p className="pc-quiet">An admin turns the Dex sync on.</p>}
      </>}
    </Panel>

    <Panel zh="条目" en="Entries" label="Entries" count={counts.waiting + counts.behind + counts.refused + counts.held}
      tone={counts.refused || counts.held ? 'warn' : 'ok'}>
      <div className="pc-dex-sync-counts">
        <div><strong>{counts.waiting}</strong><span>待发送 Waiting</span>{counts.deletes > 0 && <small>{counts.deletes} 删除 deletes</small>}</div>
        <div><strong>{counts.sent}</strong><span>已发送 Sent</span></div>
        <div><strong>{counts.refused}</strong><span>被拒 Refused</span></div>
        <div><strong>{counts.held}</strong><span>缺形式 Held: no format</span></div>
        <div><strong>{counts.behind}</strong><span>等上级 Behind a refused or held entry</span></div>
      </div>
      {status.deletes_held && <p className="pc-quiet">删除先等着 · Deletes wait until the website has taken every other entry, so nothing the
        book moved is deleted with its old place. Settle the refused and held entries first.</p>}
    </Panel>

    {status.held.length > 0 && <Panel zh="缺形式" en="Held: no format" label="Held: no format" count={counts.held} tone="warn">
      <p className="pc-quiet">POPCORE does not send a product without a format (形式) from the website's list, and what sits under it waits
        too. Open one, pick its format in the editor, and it goes with the next pass.</p>
      {status.held.map(row => <ListRow key={row.id} label={KIND_LABEL[row.kind].zh} title={<Link to={`/dex/${row.id}`}>{row.name}</Link>}
        meta={row.place.join(' › ')} />)}
    </Panel>}

    {status.refused.length > 0 && <Panel zh="网站拒收" en="Refused by the website" label="Refused by the website" count={counts.refused} tone="warn"
      action={<Button size="small" loading={busy === 'retry:all'} disabled={!!busy}
        onClick={() => run('retry:all', async () => { const done = await retryDexSync('all'); return `${done.retried} go again with the next pass` })}>
        全部重试 Retry all</Button>}>
      <p className="pc-quiet">The website did not take these. They are not sent again until they change or a manager retries. Fix the entry in the
        book if the reason says so; what sits under one waits for it.</p>
      {status.refused.map(row => <ListRow key={row.id} label={row.deleted ? '删除' : KIND_LABEL[row.kind].zh}
        title={row.deleted ? row.name : <Link to={`/dex/${row.id}`}>{row.name}</Link>}
        meta={`${row.place.length ? `${row.place.join(' › ')} · ` : ''}${row.deleted ? 'delete · ' : ''}${row.code}: ${row.message}`}
        trailing={<Button size="small" aria-label={`Retry ${row.name}`} loading={busy === `retry:${row.id}`} disabled={!!busy}
          onClick={() => run(`retry:${row.id}`, async () => { await retryDexSync(row.id); return `${row.name} goes again with the next pass` })}>
          重试 Retry</Button>} />)}
    </Panel>}

    {!status.first_send_done && <Panel zh={status.enabled ? '首次发送' : '开启后会做'} en={status.enabled ? 'First send' : 'What turning on does'}
      label="First send" count={preview.create + preview.update + preview.delete} tone={preview.delete ? 'warn' : 'ok'}>
      <p className="pc-quiet">The first send is quiet: the website sends no follower notices for it. Then each change goes within a minute.</p>
      <div className="pc-dex-sync-counts">
        <div><strong>{preview.create}</strong><span>新建 Created on the website</span></div>
        <div><strong>{preview.update}</strong><span>更新 Updated</span></div>
        <div><strong>{preview.delete}</strong><span>删除 Deleted on the website</span></div>
      </div>
      {preview.held > 0 && <p className="pc-quiet">{preview.held} of these products have no format yet: they wait, with what sits under them,
        until a manager picks one (see Held: no format).</p>}
      {preview.removed_quietly > 0 && <p className="pc-quiet">{preview.removed_quietly} deleted entries the website never had are removed without sending.</p>}
      {preview.deletes.length > 0 && <section aria-label="Deletes">
        <h3 className="pc-dex-sync-sub">删除 Deletes on popcore.ca <small>with their pages, marks and follows; listings are unlinked, not deleted</small></h3>
        {deletes.map(row => <ListRow key={row.id} label={KIND_LABEL[row.kind].zh} title={row.name}
          meta={`${kindName(row.kind)}${row.place.length ? ` · ${row.place.join(' › ')}` : ''}${row.website_id ? ` · website #${row.website_id}` : ''}`} />)}
        {preview.deletes.length > 30 && <Button type="link" onClick={() => setShowAll(value => !value)}>
          {showAll ? 'Show fewer' : `Show all ${preview.deletes.length}`}</Button>}
      </section>}
    </Panel>}

    <Modal open={turningOn} title="开启图鉴同步 Turn the Dex sync on" okText="开启 Turn on" cancelText="取消 Cancel"
      okButtonProps={{ disabled: !typedOk, loading: busy === 'switch' }} onCancel={() => setTurningOn(false)} onOk={turnOn}>
      <p>POPCORE first asks popcore.ca’s book route. Then the first send creates {preview.create}, updates {preview.update}
        {preview.delete ? ` and deletes ${preview.delete}` : ''} entries on the website, quietly.</p>
      {preview.delete > 0 && <>
        <p>Deletes remove those Dex pages with their marks and follows. Type <strong>{preview.delete}</strong> to confirm.</p>
        <Input aria-label="Number of deletes" inputMode="numeric" value={typed} onChange={event => setTyped(event.target.value)} placeholder={String(preview.delete)} />
      </>}
      {switchError && <Alert className="pc-dex-sync-alert" type="warning" showIcon message={switchError} />}
    </Modal>
  </div>
}
