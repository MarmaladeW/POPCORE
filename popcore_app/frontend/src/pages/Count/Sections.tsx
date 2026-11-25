import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Alert, Button, Input, Modal, Select, Skeleton } from 'antd'
import PageHeader from '../../components/pc/PageHeader'
import { Panel } from '../../components/pc/Panel'
import { useAppStore } from '../../store'
import {
  getCounters, getSectionCountHistory, getSections, getSectionsProposal, saveSections, startSectionCount,
  type CountSection, type Employee, type SectionCountHistoryRow, type SectionsOverview,
} from '../../api/countSections'
import {
  KIND_NAMES, KINDS, draftFrom, draftFromProposal, dueLabel, groupsLine, lastCounted, linesText, moveGroup, orderSections,
  sectionOfGroup, setTakesNew, shortDay, takesNew, type DraftSection,
} from './sectionsView'
import { MarkFlag } from './CountMarks'
import './count.css'

// 分区盘点 Section count (owner, 2026-11-20): one section every two weeks, round the whole store; an
// employee counts and the manager enters it here. Six sections of about the same size to start with.
type Failure = { _serverMessage?: string; response?: { data?: { code?: string; section_count_id?: number } } }
const messageOf = (cause: unknown, fallback: string) => (cause as Failure)?._serverMessage || fallback

export default function SectionsPage() {
  const store = useAppStore(state => state.selectedStore)
  const storeCode = store && store.code !== 'ALL' ? store.code : ''
  const navigate = useNavigate()
  const [overview, setOverview] = useState<SectionsOverview | null>(null)
  const [history, setHistory] = useState<SectionCountHistoryRow[]>([])
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [draft, setDraft] = useState<DraftSection[] | null>(null)
  const [starting, setStarting] = useState<CountSection | null>(null)

  useEffect(() => {
    if (!storeCode) return
    const controller = new AbortController()
    setError('')
    setOverview(null)
    Promise.all([getSections(storeCode, controller.signal), getSectionCountHistory(storeCode, controller.signal)])
      .then(async ([data, counts]) => {
        setOverview(data)
        setHistory(counts)
        // No sections yet: start from the suggestion of six even ones.
        if (!data.sections.length) setDraft(draftFromProposal(await getSectionsProposal(storeCode, controller.signal), []))
      })
      .catch(cause => { if (!controller.signal.aborted) setError(messageOf(cause, 'Unable to load the sections.')) })
    return () => controller.abort()
  }, [storeCode, reload])

  const ordered = useMemo(() => overview ? orderSections(overview.sections) : [], [overview])
  const next = overview?.next_up
  const nextSection = overview?.sections.find(section => section.id === next?.section_id)

  return <div className="pc-count pc-sections">
    <PageHeader title="分区盘点" sub={`Section count${store?.name && storeCode ? ` · ${store.name}` : ''}`}
      back={{ label: '盘点', to: '/count' }} />
    {!storeCode ? <Alert type="info" showIcon message="Choose one store first." />
      : error ? <Alert type="error" showIcon message={error} action={<Button onClick={() => setReload(n => n + 1)}>Retry</Button>} />
      : !overview ? <Skeleton active paragraph={{ rows: 4 }} />
      : draft ? <SectionEditor overview={overview} draft={draft} setDraft={setDraft} storeCode={storeCode}
          onSaved={() => { setDraft(null); setReload(n => n + 1) }} onCancel={overview.sections.length ? () => setDraft(null) : undefined} />
      : <>
        {next && nextSection && <section className={`pc-sections-next${next.overdue ? ' is-overdue' : ''}`} aria-label="Next up">
          <div>
            <small>下一个 Next up</small>
            <strong>{next.name}</strong>
            <span>{dueLabel(next, overview.today)} · {lastCounted(nextSection)}</span>
          </div>
          {nextSection.open_count
            ? <Link className="pc-btn pc-btn-ink" to={`/count/sections/${nextSection.open_count.id}`}>继续 Continue</Link>
            : <Button type="primary" onClick={() => setStarting(nextSection)}>开始 Start</Button>}
        </section>}
        {overview.marks > 0 && <p className="pc-count-quiet"><MarkFlag>{overview.marks}</MarkFlag>{' '}
          下一次分区盘点会先点 · The next section count you start lists {overview.marks === 1 ? 'it' : 'them'} first, from any section.</p>}
        <Panel zh="分区" en={`Sections · ${overview.lines} lines`} count={overview.sections.length} tone="ok" label="Sections"
          action={<Button size="small" onClick={() => setDraft(draftFrom(overview.sections))}>编辑分区 Edit</Button>}>
          {overview.outside > 0 && <Alert type="warning" showIcon
            message={`${overview.outside} lines are in no section. Edit the sections and pick where they go.`} />}
          <ul className="pc-sections-list">
            {ordered.map(section => <li key={section.id}>
              <span className="pc-sections-lines">{section.lines}<small>lines</small></span>
              <span className="pc-count-result-text">
                <strong>{section.name}</strong>
                {(section.uneven || section.groups.some(group => group.new)) && <span className="pc-sections-flags">
                  {section.uneven && <span className="pc-sections-flag">不均 Uneven</span>}
                  {section.groups.some(group => group.new) && <span className="pc-sections-flag is-new">新 New IP</span>}
                </span>}
                <small>{groupsLine(section)}</small>
                <small>{section.open_count ? `正在点 Counting · ${section.open_count.counter_name}` : lastCounted(section)}</small>
              </span>
              {section.open_count
                ? <Link className="pc-btn" to={`/count/sections/${section.open_count.id}`}>继续 Continue</Link>
                : <Button onClick={() => setStarting(section)}>开始 Start</Button>}
            </li>)}
          </ul>
          {overview.sections.some(section => section.uneven) && <p className="pc-count-quiet">
            One section has grown well past the others. Edit the sections and press 重新分配 Suggest again to even them out.</p>}
        </Panel>
        {history.length > 0 && <Panel zh="最近" en="Recent section counts" label="Recent section counts">
          <ul className="pc-sections-history">
            {history.map(row => <li key={row.id}><Link to={`/count/sections/${row.id}`}>
              <span>{row.section_name}<small>{shortDay(row.business_date)} · {row.counter_name} · {linesText(row.lines)}</small></span>
              <span>{row.finished_at ? '完成 Done' : '正在点 Counting'}</span>
            </Link></li>)}
          </ul>
        </Panel>}
      </>}
    {starting && <StartCount section={starting} storeCode={storeCode} onClose={() => setStarting(null)}
      onStarted={id => navigate(`/count/sections/${id}`)} />}
  </div>
}

function StartCount({ section, storeCode, onClose, onStarted }: {
  section: CountSection; storeCode: string; onClose: () => void; onStarted: (id: number) => void
}) {
  const [employees, setEmployees] = useState<Employee[] | null>(null)
  const [counter, setCounter] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    getCounters(storeCode, controller.signal).then(setEmployees)
      .catch(cause => { if (!controller.signal.aborted) setError(messageOf(cause, 'Unable to load the staff list.')) })
    return () => controller.abort()
  }, [storeCode])
  const start = async () => {
    if (!counter) { setError('Pick who is counting.'); return }
    setBusy(true)
    setError('')
    try {
      onStarted((await startSectionCount(storeCode, section.id, counter)).id)
    } catch (cause) {
      const open = (cause as Failure).response?.data
      if (open?.code === 'section_count_open' && open.section_count_id) onStarted(open.section_count_id)
      else setError(messageOf(cause, 'Could not start the count. Try again.'))
    } finally { setBusy(false) }
  }
  return <Modal open title={`开始 Start · ${section.name}`} onCancel={onClose} onOk={() => void start()} okText="开始 Start"
    cancelText="取消 Cancel" confirmLoading={busy}>
    <p className="pc-count-quiet">{linesText(section.lines)} · {groupsLine(section, 6)}</p>
    <label className="pc-sections-field">
      <span>谁在点 Who is counting?</span>
      <Select aria-label="Who is counting" showSearch optionFilterProp="label" loading={!employees} value={counter ?? undefined}
        placeholder="Pick the counter" onChange={value => setCounter(value)}
        options={(employees ?? []).map(person => ({ value: person.employee_id, label: person.name }))} />
    </label>
    <p className="pc-count-quiet">You enter what they count. The list is taken now, so it stays the same while stock changes.</p>
    {error && <Alert type="error" showIcon message={error} />}
  </Modal>
}

function SectionEditor({ overview, draft, setDraft, storeCode, onSaved, onCancel }: {
  overview: SectionsOverview; draft: DraftSection[]; setDraft: (draft: DraftSection[]) => void; storeCode: string
  onSaved: () => void; onCancel?: () => void
}) {
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const sizes = draft.map((_, index) => overview.groups
    .filter(group => sectionOfGroup(draft, group) === index).reduce((sum, group) => sum + group.lines, 0))
  const average = sizes.length ? sizes.reduce((a, b) => a + b, 0) / sizes.length : 0
  const options = draft.map((section, index) => ({ value: index, label: section.name || `Section ${index + 1}` }))
  const suggest = async () => {
    setBusy('suggest')
    setError('')
    try { setDraft(draftFromProposal(await getSectionsProposal(storeCode), overview.sections)) }
    catch (cause) { setError(messageOf(cause, 'Could not suggest sections.')) } finally { setBusy('') }
  }
  const save = async () => {
    if (KINDS.some(kind => takesNew(draft, kind) < 0)) { setError('Pick a section for new products of every kind.'); return }
    setBusy('save')
    setError('')
    try { await saveSections(storeCode, overview.version, draft); onSaved() }
    catch (cause) { setError(messageOf(cause, 'Could not save the sections. Try again.')) } finally { setBusy('') }
  }
  return <section className="pc-sections-editor" aria-label="Edit sections">
    <Panel zh={overview.sections.length ? '编辑分区' : '建议分区'} en={`${draft.length} sections · ${overview.lines} lines`}
      label="Section names">
      <div className="pc-sections-suggest">
        <span className="pc-count-quiet">{overview.sections.length ? 'Rename sections or move IPs below.'
          : 'Six sections of about the same size, one counted every two weeks. Rename them or move IPs, then save.'}</span>
        <Button loading={busy === 'suggest'} onClick={() => void suggest()}>重新分配 Suggest again</Button>
      </div>
      <ul className="pc-sections-names">
        {draft.map((section, index) => <li key={index}>
          <Input aria-label={`Name of section ${index + 1}`} value={section.name} maxLength={60}
            onChange={event => setDraft(draft.map((item, at) => at === index ? { ...item, name: event.target.value } : item))} />
          <span className={`pc-sections-size${average && sizes[index] > average * 1.25 ? ' is-uneven' : ''}`}>{sizes[index]}</span>
        </li>)}
      </ul>
    </Panel>
    {KINDS.map(kind => {
      const groups = overview.groups.filter(group => group.kind === kind)
      return <Panel key={kind} zh={KIND_NAMES[kind].split(' ')[0]} en={KIND_NAMES[kind].split(' ').slice(1).join(' ')}
        count={groups.reduce((sum, group) => sum + group.lines, 0)} tone="ok" label={KIND_NAMES[kind]}>
        <label className="pc-sections-field pc-sections-new">
          <span>新品 New IPs go to</span>
          <Select aria-label={`New ${KIND_NAMES[kind]} go to`} value={takesNew(draft, kind) >= 0 ? takesNew(draft, kind) : undefined}
            placeholder="Pick a section" options={options} onChange={value => setDraft(setTakesNew(draft, kind, value))} />
        </label>
        <ul className="pc-sections-groups">
          {groups.map(group => <li key={group.group_key}>
            <span>{group.name}<small>{linesText(group.lines)}</small></span>
            <Select aria-label={`Section for ${group.name} (${KIND_NAMES[kind]})`} value={sectionOfGroup(draft, group)} options={options}
              onChange={value => setDraft(moveGroup(draft, { kind: group.kind, group_key: group.group_key }, value))} />
          </li>)}
        </ul>
      </Panel>
    })}
    {error && <Alert type="error" showIcon message={error} />}
    <div className="pc-count-actions">
      {onCancel && <Button onClick={onCancel}>取消 Cancel</Button>}
      <Button type="primary" loading={busy === 'save'} onClick={() => void save()}>保存分区 Save sections</Button>
    </div>
  </section>
}
