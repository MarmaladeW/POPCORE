import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Skeleton, message } from 'antd'
import { Link, useNavigate, useParams } from 'react-router-dom'

import {
  childEntries, dexSyncEntry, getEntry, uploadDexPicture, type DexDetail, type DexEntry, type DexKind, type DexSyncEntryState,
} from '../../api/dex'
import { useHasRole } from '../../auth/useRole'
import PageHeader from '../../components/pc/PageHeader'
import { Panel } from '../../components/pc/Panel'
import { EXTRA_FIELDS, KIND_LABEL, RARITIES, kindText } from '../../lib/dexFields'
import DexEditor, { type DexEditing } from './DexEditor'
import { DexIpPick, DexIpTag, DexRows, DexSearch, DexThumb, confirmDelete, errorText, useIpOptions } from './DexParts'
import DexPriceTrend from './DexPriceTrend'
import './dex.css'

// 'series', 'design', but 'IP'
const plain = (kind: DexKind) => kind === 'ip' ? 'IP' : KIND_LABEL[kind].en.toLowerCase()
const show = (value: unknown) => Array.isArray(value) ? value.join(', ') : typeof value === 'boolean' ? (value ? '是 Yes' : '否 No') : String(value)

const SYNC_TEXT: Record<DexSyncEntryState['state'], string> = {
  sent: '已发送 Sent', waiting: '待发送 Waiting', refused: '被拒 Refused', held: '缺形式 Held: no format',
  behind: '等上级 Waiting behind a refused or held entry',
}

// Step 3: where this entry stands with popcore.ca (sent, waiting, refused with the website's reason).
function DexSyncState({ entryId, version }: { entryId: number; version: number }) {
  const [state, setState] = useState<DexSyncEntryState | null>(null)
  useEffect(() => {
    let live = true
    dexSyncEntry(entryId).then(next => { if (live) setState(next) }).catch(() => { if (live) setState(null) })
    return () => { live = false }
  }, [entryId, version])
  if (!state) return null
  return <p className="pc-dex-sync-state" aria-label="Dex sync state">
    <span>网站 popcore.ca:</span><strong>{SYNC_TEXT[state.state]}</strong>
    {state.state === 'refused' && <span>{state.code}: {state.message}</span>}
    {state.state === 'held' && <span>{state.message}</span>}
    {!state.enabled && state.state !== 'sent' && <span>(图鉴同步关 Dex sync off)</span>}
  </p>
}

// One entry of the 图鉴 Dex: where it sits, what it is, and what sits under it, grouped by kind.
export default function DexEntryPage() {
  const id = Number(useParams().id)
  const manager = useHasRole('manager')
  const navigate = useNavigate()
  const [entry, setEntry] = useState<DexDetail>()
  const [rows, setRows] = useState<DexEntry[]>([])
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [editing, setEditing] = useState<DexEditing | null>(null)
  const [uploading, setUploading] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  async function upload(file: File | undefined) {
    if (!file) return
    setUploading(true)
    try {
      await uploadDexPicture(id, file)
      message.success('图片已上传 · Picture uploaded to popcore.ca')
      setAttempt(n => n + 1)
    } catch (cause) {
      message.error(errorText(cause, 'The picture did not upload. Try again.'))
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  useEffect(() => {
    let live = true
    setEntry(undefined); setError('')
    Promise.all([getEntry(id), childEntries(id)]).then(([next, children]) => {
      if (live) { setEntry(next); setRows(children) }
    }).catch(cause => {
      if (live) setError(cause?.response?.status === 404 ? 'This entry is not in the book (it may have been deleted).' : errorText(cause, 'Unable to load this entry.'))
    })
    return () => { live = false }
  }, [id, attempt])

  // Contract 1.3: with no IP above (a series with several IPs under its company), each product and design row
  // names its own IP; managers set it right in the row.
  const rowsNameIp = !!entry && !entry.ip_above && entry.allowed_children.some(kind => kind === 'product' || kind === 'design')
  const ipOptions = useIpOptions(manager && rowsNameIp)

  const parent = entry?.path[entry.path.length - 1]
  const back = { label: parent ? parent.name : '图鉴 Dex', to: parent ? `/dex/${parent.id}` : '/dex' }
  if (error) return <div className="pc-dex">
    <PageHeader title="图鉴" sub="Dex" back={{ label: '图鉴 Dex', to: '/dex' }} />
    <Alert role="alert" type="error" showIcon message={error}
      action={<span className="pc-dex-actions"><Button onClick={() => setAttempt(n => n + 1)}>Retry</Button><Link to="/dex">图鉴 Dex</Link></span>} />
  </div>
  if (!entry) return <div className="pc-dex"><PageHeader title="图鉴" sub="Dex" /><div className="pc-panel"><div className="pc-quiet"><Skeleton active /></div></div></div>

  const facts: [string, string][] = []
  if (entry.blind_box !== null && entry.kind === 'product') facts.push(['盲盒 Blind box', entry.blind_box ? '是 Yes' : '否 No'])
  if (entry.rarity) facts.push(['稀有度 Rarity', RARITIES.find(r => r.value === entry.rarity)?.label ?? entry.rarity])
  if (entry.partner) facts.push(['联名 Partner', entry.partner.name])
  if (entry.character) facts.push(['角色 Character', entry.character.name])
  if (entry.product) facts.push(['POPCORE', `${entry.product.sku} · ${entry.product.name}`])
  for (const field of EXTRA_FIELDS[entry.kind]) {
    const value = entry.extras[field.key]
    if (value !== null && value !== undefined && value !== '') facts.push([field.label, show(value)])
  }
  const titled = (entry.kind === 'product' || entry.kind === 'design') && entry.full_name !== entry.name
  const groups = entry.allowed_children.map(kind => ({ kind, rows: rows.filter(row => row.kind === kind) }))
  const setGroup = (kind: DexKind, next: DexEntry[]) => setRows(current => [...current.filter(row => row.kind !== kind), ...next])
  const setRow = (saved: DexEntry) => setRows(current => current.map(row => row.id === saved.id ? saved : row))
  const ipPick = (kind: DexKind) => manager && rowsNameIp && (kind === 'product' || kind === 'design')
    ? (row: DexEntry) => <DexIpPick entry={row} options={ipOptions} onSaved={setRow} /> : undefined

  return <div className="pc-dex">
    <PageHeader title={entry.name} sub={kindText(entry.kind)} back={back} />
    <nav className="pc-dex-crumbs" aria-label="Breadcrumb">
      <Link to="/dex">图鉴</Link>
      {entry.path.map(step => <span key={step.id}><span aria-hidden="true">›</span><Link to={`/dex/${step.id}`}>{step.name}</Link></span>)}
      <span><span aria-hidden="true">›</span><strong aria-current="page">{entry.name}</strong></span>
    </nav>
    <section className="pc-panel pc-dex-card" aria-label="Entry">
      <DexThumb entry={entry} size={96} />
      <div className="pc-dex-card-main">
        {/* A product's and a design's title is its full name (put together, never typed); its own name beneath. */}
        {titled ? <div className="pc-dex-title">
          <h2>{entry.full_name}</h2>
          <p className="pc-dex-own">{entry.name} <small>{kindText(entry.kind)}</small></p>
        </div> : <h2>{entry.name} <small>{kindText(entry.kind)}</small></h2>}
        {entry.ip && <DexIpTag tag={entry.ip} />}
        {facts.length ? <dl>{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
          : <p className="pc-dex-note">{manager ? 'No details yet. 编辑 Edit adds them.' : 'No details yet.'}</p>}
        {/* Where it stands with popcore.ca is a manager's to see (owner, 2026-11-26). */}
        {manager && <DexSyncState entryId={entry.id} version={entry.version} />}
        {manager && <span className="pc-dex-actions">
          <Button onClick={() => setEditing({ mode: 'edit', entry })}>编辑 Edit</Button>
          <Button loading={uploading} onClick={() => fileInput.current?.click()}>上传图片 Upload picture</Button>
          <input ref={fileInput} className="pc-dex-file" type="file" aria-label="Picture file"
            accept="image/jpeg,image/png,image/webp,image/avif,image/gif" onChange={event => upload(event.target.files?.[0])} />
          <Button danger onClick={() => confirmDelete(entry, () => navigate(back.to))}>删除 Delete</Button>
        </span>}
      </div>
    </section>
    {(entry.kind === 'design' || entry.kind === 'product') && <DexPriceTrend entryId={entry.id} design={entry.kind === 'design'}
      linked={entry.product_id !== null} productId={entry.product_id} canEdit={manager}
      hasWebsite={Array.isArray(entry.extras.market_prices) && entry.extras.market_prices.length > 0} />}
    {entry.kind === 'ip' && entry.named_by.length > 0 && <Panel zh="也出现在" en="Also in" label="Also in"
      count={entry.named_by.length} tone="ok">
      {entry.named_by.map(hit => <Link key={hit.id} className="pc-row pc-dex-row pc-dex-also" to={`/dex/${hit.id}`}>
        <DexThumb entry={hit} />
        <span className="pc-row-main"><strong>{hit.full_name}</strong>
          <small>{KIND_LABEL[hit.kind].zh} {KIND_LABEL[hit.kind].en} · {hit.path.map(step => step.name).join(' › ')}</small></span>
      </Link>)}
    </Panel>}
    {groups.length > 0 && <DexSearch />}
    {groups.map(group => <Panel key={group.kind} zh={KIND_LABEL[group.kind].zh} en={KIND_LABEL[group.kind].en === KIND_LABEL[group.kind].zh ? undefined : KIND_LABEL[group.kind].en}
      label={`${KIND_LABEL[group.kind].en} here`} count={group.rows.length} tone="ok"
      action={manager && <Button size="small" onClick={() => setEditing({ mode: 'create', kind: group.kind, parent: entry })}>
        添加 Add {plain(group.kind)}</Button>}>
      {group.kind === 'series' && group.rows.length > 1 && <p className="pc-quiet">按发售日期，新的在前 · Listed by release date, newest
        first. A series with no release date comes after them, by name.</p>}
      {group.rows.length ? <DexRows rows={group.rows} parentId={entry.id} canWrite={manager} onChanged={next => setGroup(group.kind, next)}
        extra={ipPick(group.kind)} />
        : <p className="pc-quiet">No {plain(group.kind)} here yet.</p>}
    </Panel>)}
    <DexEditor editing={editing} onClose={() => setEditing(null)} onSaved={(saved, created) => {
      setEditing(null)
      if (created) navigate(`/dex/${saved.id}`)
      else setAttempt(n => n + 1)
    }} />
  </div>
}
