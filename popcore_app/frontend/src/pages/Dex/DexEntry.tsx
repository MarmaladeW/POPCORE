import { useEffect, useState } from 'react'
import { Alert, Button, Skeleton } from 'antd'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { childEntries, getEntry, type DexDetail, type DexEntry, type DexKind } from '../../api/dex'
import { useHasRole } from '../../auth/useRole'
import PageHeader from '../../components/pc/PageHeader'
import { Panel } from '../../components/pc/Panel'
import { EXTRA_FIELDS, KIND_LABEL, RARITIES, kindText } from '../../lib/dexFields'
import DexEditor, { type DexEditing } from './DexEditor'
import { DexRows, DexSearch, DexThumb, confirmDelete, errorText } from './DexParts'
import './dex.css'

// 'series', 'design', but 'IP'
const plain = (kind: DexKind) => kind === 'ip' ? 'IP' : KIND_LABEL[kind].en.toLowerCase()
const show = (value: unknown) => Array.isArray(value) ? value.join(', ') : typeof value === 'boolean' ? (value ? '是 Yes' : '否 No') : String(value)

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
  const groups = entry.allowed_children.map(kind => ({ kind, rows: rows.filter(row => row.kind === kind) }))
  const setGroup = (kind: DexKind, next: DexEntry[]) => setRows(current => [...current.filter(row => row.kind !== kind), ...next])

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
        <h2>{entry.name} <small>{kindText(entry.kind)}</small></h2>
        {facts.length ? <dl>{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
          : <p className="pc-dex-note">{manager ? 'No details yet. 编辑 Edit adds them.' : 'No details yet.'}</p>}
        {manager && <span className="pc-dex-actions">
          <Button onClick={() => setEditing({ mode: 'edit', entry })}>编辑 Edit</Button>
          <Button danger onClick={() => confirmDelete(entry, () => navigate(back.to))}>删除 Delete</Button>
        </span>}
      </div>
    </section>
    {groups.length > 0 && <DexSearch />}
    {groups.map(group => <Panel key={group.kind} zh={KIND_LABEL[group.kind].zh} en={KIND_LABEL[group.kind].en === KIND_LABEL[group.kind].zh ? undefined : KIND_LABEL[group.kind].en}
      label={`${KIND_LABEL[group.kind].en} here`} count={group.rows.length} tone="ok"
      action={manager && <Button size="small" onClick={() => setEditing({ mode: 'create', kind: group.kind, parent: entry })}>
        添加 Add {plain(group.kind)}</Button>}>
      {group.rows.length ? <DexRows rows={group.rows} parentId={entry.id} canWrite={manager} onChanged={next => setGroup(group.kind, next)} />
        : <p className="pc-quiet">No {plain(group.kind)} here yet.</p>}
    </Panel>)}
    <DexEditor editing={editing} onClose={() => setEditing(null)} onSaved={(saved, created) => {
      setEditing(null)
      if (created) navigate(`/dex/${saved.id}`)
      else setAttempt(n => n + 1)
    }} />
  </div>
}
