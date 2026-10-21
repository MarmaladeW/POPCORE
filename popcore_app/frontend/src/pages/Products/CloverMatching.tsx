import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, Button, Input, Modal, Select, Skeleton, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  applyCloverMatches, getCloverMatches, updateCloverMatch,
  type ApplyResult, type CloverMatch, type CloverMatches, type MatchStatus,
} from '../../api/cloverMatches'
import { useIsMobile } from '../../hooks/useIsMobile'
import './cloverMatching.css'

const failure = (cause: unknown, fallback: string) => (cause as { _serverMessage?: string })?._serverMessage || fallback
const money = (price: number | null) => price == null ? '' : `$${price.toFixed(2)}`
const STATUS_LABEL: Record<MatchStatus, string> = {
  undecided: 'No decision', ready: 'Ready to apply', keep: 'Keep old product', applied: 'Applied', missing: 'Old product gone',
}
const STATUS_COLOR: Record<MatchStatus, string> = { undecided: 'default', ready: 'blue', keep: 'purple', applied: 'green', missing: 'red' }
const CONFIDENCE_COLOR = { high: 'green', medium: 'gold', low: 'red' } as const
const SOURCE_LABEL: Record<string, string> = { auto: 'Auto-match', owner: 'Your sheet pick', claude: 'Claude', edited: 'Edited here', sheet: 'Sheet' }
type StatusFilter = MatchStatus | 'open' | 'all'

const decisionValue = (row: CloverMatch) => row.decision === 'merge' ? `p:${row.target_product_id}` : row.decision ?? undefined

export default function CloverMatching() {
  const [data, setData] = useState<CloverMatches>(), [error, setError] = useState(''), [retry, setRetry] = useState(0)
  const [query, setQuery] = useState(''), [status, setStatus] = useState<StatusFilter>('open'), [confidence, setConfidence] = useState<string>()
  const [selected, setSelected] = useState<number[]>([]), [saving, setSaving] = useState<number>(), [rowError, setRowError] = useState('')
  const [applying, setApplying] = useState(false), [results, setResults] = useState<ApplyResult[]>()
  const isMobile = useIsMobile()

  useEffect(() => {
    const controller = new AbortController()
    setError('')
    getCloverMatches(controller.signal)
      .then(next => { if (!controller.signal.aborted) setData(next) })
      .catch(cause => { if (!controller.signal.aborted) setError(failure(cause, 'Unable to load the Clover matches.')) })
    return () => controller.abort()
  }, [retry])

  const options = useMemo(() => [
    { label: 'Decision', options: [
      { value: 'keep', label: 'Keep old product (no Clover item yet)' },
      { value: 'delete', label: 'Delete old product (only if no history)' },
    ] },
    { label: 'Merge into Clover item', options: (data?.clover_products ?? []).map(product => ({
      value: `p:${product.id}`, label: `${product.name}${product.price != null ? ` · ${money(product.price)}` : ''}`,
    })) },
  ], [data])

  const counts = useMemo(() => {
    const result: Record<string, number> = {}
    data?.items.forEach(item => { result[item.status] = (result[item.status] ?? 0) + 1 })
    return result
  }, [data])

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return (data?.items ?? []).filter(item =>
      (status === 'all' || (status === 'open' ? ['undecided', 'ready', 'keep'].includes(item.status) : item.status === status))
      && (!confidence || (confidence === 'edited' ? item.source === 'edited' : item.confidence === confidence && item.source !== 'edited'))
      && (!needle || [item.legacy_jizhanming, item.legacy_name, item.legacy_sku, item.target_name, item.note]
        .some(text => text?.toLowerCase().includes(needle))))
  }, [data, query, status, confidence])

  const replace = (item: CloverMatch) => setData(current => current && { ...current, items: current.items.map(row => row.legacy_product_id === item.legacy_product_id ? item : row) })

  async function change(row: CloverMatch, value: string | undefined) {
    const decision = !value ? null : value.startsWith('p:') ? 'merge' : value as 'keep' | 'delete'
    const target = value?.startsWith('p:') ? Number(value.slice(2)) : null
    setSaving(row.legacy_product_id); setRowError('')
    try {
      replace(await updateCloverMatch(row.legacy_product_id, decision, target))
      if (decision !== 'merge' && decision !== 'delete') setSelected(ids => ids.filter(id => id !== row.legacy_product_id))
    } catch (cause) {
      setRowError(`${row.legacy_jizhanming || row.legacy_name}: ${failure(cause, 'The change was not saved.')}`)
    } finally { setSaving(undefined) }
  }

  function confirmApply() {
    const chosen = (data?.items ?? []).filter(item => selected.includes(item.legacy_product_id) && item.status === 'ready')
    const merges = chosen.filter(item => item.decision === 'merge').length, deletes = chosen.length - merges
    Modal.confirm({
      title: `Apply ${chosen.length} decision${chosen.length === 1 ? '' : 's'}?`,
      content: <div className="pc-clover-confirm">
        <p>{merges} old product{merges === 1 ? '' : 's'} will merge into Clover items: sales, stock and history move over, and the 记账名 becomes a search alias. {deletes} unused old product{deletes === 1 ? '' : 's'} will be deleted.</p>
        <p><strong>This cannot be undone from the app.</strong> A product that cannot merge safely, for example because both products hold stock at the same store, is refused and stays open.</p>
      </div>,
      okText: 'Apply decisions', cancelText: 'Go back', width: 520,
      onOk: () => apply(chosen.map(item => item.legacy_product_id)),
    })
  }

  async function apply(ids: number[]) {
    setApplying(true); setResults(undefined)
    try {
      const outcome = await applyCloverMatches(ids)
      setResults(outcome); setSelected([]); setRetry(value => value + 1)
    } catch (cause) {
      setRowError(failure(cause, 'Applying failed. Nothing was reported as applied; reload to check.'))
    } finally { setApplying(false) }
  }

  const legacyCell = (row: CloverMatch) => <div className="pc-clover-legacy">
    <strong>{row.legacy_jizhanming || row.legacy_name || 'No name'}</strong>
    {row.legacy_name && row.legacy_name !== row.legacy_jizhanming && <span>{row.legacy_name}</span>}
    <small>{[row.legacy_sku, money(row.legacy_price), row.references != null && `${row.references} sales/stock records`].filter(Boolean).join(' · ')}</small>
    <span><Tag color={STATUS_COLOR[row.status]}>{STATUS_LABEL[row.status]}</Tag></span>
  </div>
  const decisionCell = (row: CloverMatch) => <div className="pc-clover-decision">
    {row.status === 'applied'
      ? <span className="pc-clover-outcome">{row.applied_outcome}</span>
      : <Select aria-label={`Decision for ${row.legacy_jizhanming || row.legacy_name}`} className="pc-clover-select" showSearch allowClear
          optionFilterProp="label" placeholder="Choose a Clover item, keep or delete" value={decisionValue(row)} options={options}
          disabled={row.status === 'missing' || applying} loading={saving === row.legacy_product_id}
          onChange={value => change(row, value)} popupMatchSelectWidth={false} virtual />}
    <div className="pc-clover-why">
      <span>
        <Tag>{SOURCE_LABEL[row.source] ?? row.source}</Tag>
        {row.confidence && row.source !== 'edited' && <Tag color={CONFIDENCE_COLOR[row.confidence]}>{row.confidence}</Tag>}
      </span>
      {row.note && <small>{row.note}</small>}
    </div>
  </div>
  const columns: ColumnsType<CloverMatch> = isMobile
    ? [{ title: 'Old product and decision', key: 'all', render: (_, row) => <div className="pc-clover-stack">{legacyCell(row)}{decisionCell(row)}</div> }]
    : [
        { title: 'Old product', key: 'legacy', width: 300, render: (_, row) => legacyCell(row) },
        { title: 'Decision', key: 'decision', render: (_, row) => decisionCell(row) },
      ]

  const refused = results?.filter(result => result.status === 'refused') ?? []
  const nameOf = (id: number) => { const item = data?.items.find(row => row.legacy_product_id === id); return item?.legacy_jizhanming || item?.legacy_name || `#${id}` }

  return <main className="pc-clover-matching">
    <header>
      <Link to="/products">Products</Link>
      <h1>Clover matching</h1>
      <p>Match each old product (记账名) to its Clover item. Changes save as you make them; nothing merges until you select rows and apply them. A good habit: apply one series right before counting it.</p>
    </header>
    {error ? <Alert type="error" message={error} action={<Button onClick={() => setRetry(value => value + 1)}>Retry</Button>} />
      : !data ? <Skeleton active paragraph={{ rows: 8 }} /> : <>
        <nav className="pc-clover-counts" aria-label="Filter by status">
          {(['open', 'undecided', 'ready', 'keep', 'applied', 'missing', 'all'] as StatusFilter[]).map(key => {
            const count = key === 'all' ? data.items.length : key === 'open' ? (counts.undecided ?? 0) + (counts.ready ?? 0) + (counts.keep ?? 0) : counts[key] ?? 0
            if (key === 'missing' && !count) return null
            return <Button key={key} type={status === key ? 'primary' : 'default'} onClick={() => setStatus(key)}>
              {key === 'open' ? 'Open' : key === 'all' ? 'All' : STATUS_LABEL[key]} · {count}
            </Button>
          })}
        </nav>
        {data.unlisted_legacy > 0 && <Alert type="info" showIcon message={`${data.unlisted_legacy} old product${data.unlisted_legacy === 1 ? ' is' : 's are'} not in this review yet.`} />}
        <div className="pc-clover-toolbar">
          <Input.Search aria-label="Search old or Clover names" allowClear placeholder="Search 记账名, old name, SKU or Clover item" value={query} onChange={event => setQuery(event.target.value)} />
          <Select aria-label="Filter by confidence" allowClear placeholder="Any confidence" value={confidence} onChange={setConfidence} options={[
            { value: 'medium', label: 'Medium: please check' }, { value: 'low', label: 'Low' }, { value: 'high', label: 'High' }, { value: 'edited', label: 'Edited here' },
          ]} />
          <Button type="primary" disabled={!selected.length || applying} loading={applying} onClick={confirmApply}>Apply selected · {selected.length}</Button>
        </div>
        {rowError && <Alert type="error" showIcon closable message={rowError} onClose={() => setRowError('')} />}
        {results && <Alert type={refused.length ? 'warning' : 'success'} showIcon closable onClose={() => setResults(undefined)}
          message={`${results.length - refused.length} applied${refused.length ? `, ${refused.length} refused and left open` : ''}.`}
          description={refused.length ? <ul className="pc-clover-refused">{refused.map(result => <li key={result.legacy_product_id}><strong>{nameOf(result.legacy_product_id)}</strong>: {result.message}</li>)}</ul> : undefined} />}
        <Table<CloverMatch> rowKey="legacy_product_id" columns={columns} dataSource={rows} size="middle"
          pagination={{ defaultPageSize: 50, showSizeChanger: true, pageSizeOptions: [25, 50, 100, 200], showTotal: total => `${total} products` }}
          rowSelection={{
            selectedRowKeys: selected, preserveSelectedRowKeys: true,
            onChange: keys => setSelected(keys as number[]),
            getCheckboxProps: row => ({ disabled: row.status !== 'ready', 'aria-label': `Select ${row.legacy_jizhanming || row.legacy_name}` }),
          }} />
      </>}
  </main>
}
