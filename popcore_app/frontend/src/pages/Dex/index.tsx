import { useEffect, useState } from 'react'
import { Alert, Button, Skeleton } from 'antd'
import { Link, useNavigate } from 'react-router-dom'

import { topEntries, unlinkedItems, type DexDetail, type DexEntry } from '../../api/dex'
import { useHasRole } from '../../auth/useRole'
import PageHeader from '../../components/pc/PageHeader'
import { Panel } from '../../components/pc/Panel'
import DexEditor, { type DexEditing } from './DexEditor'
import { DexRows, DexSearch, errorText } from './DexParts'
import './dex.css'

// 图鉴 Dex: the book of companies and partners, their IPs, characters, series, products and designs
// (owner, 2026-11-12: "The Dex itself lives on POPCORE"). Anyone signed in reads it; managers write it.
export default function DexHome() {
  const manager = useHasRole('manager')
  const navigate = useNavigate()
  const [companies, setCompanies] = useState<DexEntry[]>()
  const [partners, setPartners] = useState<DexEntry[]>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [editing, setEditing] = useState<DexEditing | null>(null)
  const [unlinked, setUnlinked] = useState<number>()

  useEffect(() => {
    let live = true
    Promise.all([topEntries('company'), topEntries('partner')]).then(([nextCompanies, nextPartners]) => {
      if (!live) return
      setCompanies(nextCompanies); setPartners(nextPartners); setError('')
    }).catch(cause => { if (live) setError(errorText(cause, 'Unable to load the book.')) })
    unlinkedItems('', 1, 1).then(page => { if (live) setUnlinked(page.total) }).catch(() => {})
    return () => { live = false }
  }, [attempt])

  const header = <PageHeader title="图鉴" sub="Dex · the book" />
  if (error) return <div className="pc-dex">{header}
    <Alert role="alert" type="error" showIcon message={error} action={<Button onClick={() => setAttempt(n => n + 1)}>Retry</Button>} />
  </div>
  if (!companies || !partners) return <div className="pc-dex">{header}<div className="pc-panel"><div className="pc-quiet"><Skeleton active /></div></div></div>

  const add = (kind: 'company' | 'partner') => setEditing({ mode: 'create', kind, parent: null })
  const empty = companies.length === 0 && partners.length === 0
  return <div className="pc-dex">
    {header}
    {!empty && <DexSearch />}
    <Link className="pc-panel pc-dex-unlinked" to="/dex/unlinked" aria-label="Not in the book yet">
      <span><strong>不在图鉴里 Not in the book yet</strong>
        <small>POPCORE items no book entry is linked to{manager ? '; put them in the book from here' : ''}</small></span>
      <span className="pc-panel-count"><i className="pc-dot pc-dot-warn" aria-hidden="true" />{unlinked ?? '…'}</span>
    </Link>
    {empty && <section className="pc-dex-empty" aria-label="Empty book">
      <h2>图鉴还是空的 <small>The book is empty</small></h2>
      <p>先加一个公司，比如 POP MART，再在它下面加 IP、系列、产品和款。迪士尼这样的外部名字放在联名里。</p>
      <p>Start with a company, such as POP MART, then add its IPs, series, products and designs under it.
        Outside names such as Disney go under Partners.</p>
      {manager ? <span className="pc-dex-actions">
        <Button type="primary" onClick={() => add('company')}>添加公司 Add a company</Button>
        <Button onClick={() => add('partner')}>添加联名 Add a partner</Button>
      </span>
        : <p className="pc-dex-note">A manager adds the first company.</p>}
    </section>}
    {!empty && <>
    <Panel zh="公司" en="Companies" label="Companies" count={companies.length} tone="ok"
      action={manager && <Button size="small" onClick={() => add('company')}>添加 Add</Button>}>
      {companies.length ? <DexRows rows={companies} parentId={null} canWrite={manager} onChanged={setCompanies} />
        : <p className="pc-quiet">No companies yet.</p>}
    </Panel>
    <Panel zh="联名" en="Partners" label="Partners" count={partners.length} tone="ok"
      action={manager && <Button size="small" onClick={() => add('partner')}>添加 Add</Button>}>
      {partners.length ? <DexRows rows={partners} parentId={null} canWrite={manager} onChanged={setPartners} />
        : <p className="pc-quiet">No partners yet. A partner is an outside name on a series or product (Disney, Harry Potter…).</p>}
    </Panel>
    </>}
    <DexEditor editing={editing} onClose={() => setEditing(null)}
      onSaved={(entry: DexDetail) => { setEditing(null); navigate(`/dex/${entry.id}`) }} />
  </div>
}
