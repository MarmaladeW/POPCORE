import { memo, useCallback, useEffect, useMemo, useRef, useState, type ClipboardEvent } from 'react'
import { Alert, Button, Checkbox, Input, Modal, Segmented, Select, Skeleton, message } from 'antd'

import {
  checkDesignPrices, getDesignPrices, saveDesignPrices, type DesignBox, type DesignEffect, type DesignPriceChange,
  type DesignPriceList, type DesignPriceRow, type DesignPriceSummary, type PriceLink,
} from '../../api/designPrices'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { ProductsTabs } from './ProductsNav'
import { adjust, money, parseMoney, percent, placePaste, plainMoney, readPaste } from './designPriceMath'
import './products.css'
import './designPrices.css'

type Filter = 'all' | 'unset' | 'today' | 'live' | 'edited'
type Kind = 'all' | 'regular' | 'secret'
const PAGE = 40 // boxes shown at once; "Show more" adds the next ones
const filters: Array<{ value: Filter; zh: string; en: string }> = [
  { value: 'all', zh: '全部', en: 'All' },
  { value: 'unset', zh: '未定价', en: 'No price set yet' },
  { value: 'today', zh: '今天改过', en: 'Changed today' },
  { value: 'live', zh: '网站在售', en: 'Live on the website' },
  { value: 'edited', zh: '已改未存', en: 'Edited, not saved' },
]
const fold = (text: string | null | undefined) => (text || '').normalize('NFKC').toLocaleLowerCase()
const day = (utc: string | null) => {
  const time = utc ? new Date(`${utc.replace(' ', 'T')}Z`) : null
  return time && !Number.isNaN(time.getTime())
    ? time.toLocaleDateString('en-CA', { timeZone: 'America/Toronto', month: 'short', day: 'numeric' }) : ''
}
const failure = (cause: unknown) => (cause as { _serverMessage?: string })?._serverMessage

type Edit = { cents: number | null; invalid: boolean }
// A typed price counts once it is a price, and it is new, or it is the design's first set price (a design that
// was never set carries its box's price: typing that same price makes it the design's own).
const changeOf = (design: DesignPriceRow, edit?: Edit) =>
  !!edit && edit.cents !== null && (edit.cents !== design.price_cents || design.state !== 'set')

// What the website does with a typed price, as far as the page can tell; the summary before saving asks the
// server, which judges it exactly as the website sync will.
function hint(design: DesignPriceRow, edit: Edit | undefined, look: number): { text: string; tone?: 'warn' | 'out' } | null {
  if (!edit) return null
  if (edit.invalid) return { text: '不是价格 · Not a price', tone: 'out' }
  if (!changeOf(design, edit)) return { text: '没有变化 · No change' }
  if (!design.web.length) return { text: '只改 POPCORE · Not on the website' }
  const item = design.web[0], cents = edit.cents!
  if (item.regular_cents === cents) return { text: '网站已是此价 · The website has it' }
  if (item.base_cents === null) return { text: '首次定价 · First price' }
  const change = percent(item.base_cents, cents)
  return Math.abs(change) > look
    ? { text: `${change > 0 ? '+' : ''}${change}% · 需经理确认 Waits for an OK`, tone: 'warn' }
    : { text: `${change > 0 ? '+' : ''}${change}%` }
}

function popcoreNote(design: DesignPriceRow, box: DesignBox) {
  if (design.state === 'set') return `已定价 Set ${day(design.set_at)}`
  if (design.state === 'elsewhere') return '他处改过 · Changed elsewhere: not sent'
  return design.price_cents !== null && design.price_cents === box.price_cents ? '盒价 · Box price, not its own' : '未定价 · Not set'
}

function linkText(link: PriceLink) {
  if (!link.configured) return 'The website link is not set up on the server yet. Saved prices stay in POPCORE and go to popcore.ca once it is.'
  if (!link.enabled) return '网站同步关 · The website sync is off. Saved prices go to popcore.ca once an admin turns it on (网站 Website).'
  return null
}

// 确认款价格 Design prices: the owner prices confirmed designs "based on the market price of the day" and sets them
// here in bulk (owner, 2026-11-12). A design starts with its box's price; only a price set here or in the design's
// editor goes to popcore.ca. A jump of more than 30% from a design's own website price waits for a manager's OK on
// 网站 Website; a first price (a draft at its box's price, or none) does not.
export default function DesignPrices() {
  const [data, setData] = useState<DesignPriceList>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [drafts, setDrafts] = useState<Record<number, string>>({})
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set())
  const [query, setQuery] = useState('')
  const [ip, setIp] = useState<string>()
  const [filter, setFilter] = useState<Filter>('all')
  const [kind, setKind] = useState<Kind>('all')
  const [shown, setShown] = useState(PAGE)
  const [setTo, setSetTo] = useState('')
  const [by, setBy] = useState('')
  const [unit, setUnit] = useState<'%' | '$'>('%')
  const [review, setReview] = useState<{ key: string; summary?: DesignPriceSummary; error?: string; saving?: boolean }>()

  useEffect(() => {
    let live = true
    setError('')
    getDesignPrices().then(next => { if (live) setData(next) })
      .catch(cause => { if (live) setError(failure(cause) || 'Unable to load design prices. Please retry.') })
    return () => { live = false }
  }, [attempt])

  // Unsaved prices are kept from a reload or a closed tab by the browser's own question.
  const dirty = Object.keys(drafts).length > 0
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const edits = useMemo(() => {
    const next: Record<number, Edit> = {}
    for (const [id, text] of Object.entries(drafts)) {
      const cents = parseMoney(text)
      next[Number(id)] = { cents, invalid: cents === null }
    }
    return next
  }, [drafts])
  const byId = useMemo(() => new Map((data?.designs || []).map(design => [design.id, design])), [data])
  const changes = useMemo(() => Object.entries(edits).flatMap(([id, edit]) => {
    const design = byId.get(Number(id))
    return design && changeOf(design, edit) ? [{ product_id: design.id, cents: edit.cents!, expected_cents: design.price_cents }] : []
  }) as DesignPriceChange[], [edits, byId])
  const invalid = Object.values(edits).filter(edit => edit.invalid).length

  // Boxes, each with the designs this filter shows.
  const groups = useMemo(() => {
    if (!data) return []
    const needle = fold(query.trim())
    const designs = new Map<number | null, DesignPriceRow[]>()
    for (const design of data.designs) designs.set(design.box_id, [...(designs.get(design.box_id) || []), design])
    return data.boxes.flatMap(box => {
      if (ip && box.ip !== ip) return []
      const boxHit = !needle || [box.name, box.full_name, box.sku, box.ip, box.brand].some(text => fold(text).includes(needle))
      const rows = (designs.get(box.id) || []).filter(design =>
        (boxHit || fold(design.name).includes(needle) || fold(design.sku).includes(needle))
        && (kind === 'all' || design.secret === (kind === 'secret'))
        && (filter === 'all' || (filter === 'unset' && design.state !== 'set') || (filter === 'today' && design.changed_today)
          || (filter === 'live' && design.web.some(item => item.published)) || (filter === 'edited' && design.id in drafts)))
      return rows.length ? [{ box, rows }] : []
    })
  }, [data, query, ip, kind, filter, drafts])
  const visible = useMemo(() => groups.slice(0, shown), [groups, shown])
  const order = useMemo(() => visible.flatMap(group => group.rows), [visible])
  const ids = useMemo(() => new Set((data?.designs || []).map(design => design.sku.toUpperCase())), [data])
  // The rows keep the same callbacks while typing, so only the edited box draws again.
  const shownRef = useRef({ order, ids })
  shownRef.current = { order, ids }
  const ips = useMemo(() => {
    const counts = new Map<string, number>()
    for (const box of data?.boxes || []) if (box.ip) counts.set(box.ip, (counts.get(box.ip) || 0) + 1)
    return [...counts].sort((a, b) => a[0].localeCompare(b[0])).map(([value, count]) => ({ value, label: `${value} (${count})` }))
  }, [data])

  const type = useCallback((id: number, text: string) => setDrafts(current => {
    const next = { ...current }
    if (text.trim()) next[id] = text
    else delete next[id]
    return next
  }), [])
  const paste = useCallback((event: ClipboardEvent<HTMLInputElement>, id: number) => {
    const text = event.clipboardData.getData('text')
    if (!/[\n\t]/.test(text.trim())) return // one value: the input takes it as typed
    event.preventDefault()
    const { order, ids } = shownRef.current
    const { placed, missed, byId: matched } = placePaste(readPaste(text, ids), order, order.findIndex(row => row.id === id))
    setDrafts(current => ({ ...current, ...placed }))
    const count = Object.keys(placed).length
    message.info(`已粘贴 ${count} 个价格 · ${count} prices pasted${matched ? ' by POPCORE ID' : ' down the column'}`
      + (missed ? `. ${missed} ${matched ? 'name a design not shown here' : 'did not fit below'}: left out.` : '.'))
  }, [])
  const toggle = useCallback((rows: DesignPriceRow[], on: boolean) => setSelected(current => {
    const next = new Set(current)
    for (const row of rows) {
      if (on) next.add(row.id)
      else next.delete(row.id)
    }
    return next
  }), [])
  const chosen = () => order.filter(row => selected.has(row.id)).concat(
    (data?.designs || []).filter(row => selected.has(row.id) && !order.includes(row)))

  function applySet() {
    const cents = parseMoney(setTo)
    if (cents === null) { message.error('Type a price, such as 45.99'); return }
    const rows = chosen()
    setDrafts(current => ({ ...current, ...Object.fromEntries(rows.map(row => [row.id, plainMoney(cents)])) }))
    message.success(`${rows.length} 款设为 ${money(cents)} · ${rows.length} designs set to ${money(cents)}`)
  }
  function applyBy() {
    const amount = Number(by.trim().replace(/^\+/, '').replace(/[%$]/g, ''))
    if (!by.trim() || !Number.isFinite(amount) || amount === 0) { message.error(unit === '%' ? 'Type a percentage, such as 10 or -15' : 'Type an amount, such as 5 or -2.50'); return }
    const next: Record<number, string> = {}
    let skipped = 0
    for (const row of chosen()) {
      // From the price typed for it, else from POPCORE's price now.
      const from = edits[row.id]?.cents ?? row.price_cents
      const cents = from === null ? null : adjust(from, amount, unit)
      if (cents === null) skipped += 1
      else next[row.id] = plainMoney(cents)
    }
    setDrafts(current => ({ ...current, ...next }))
    const count = Object.keys(next).length
    message.success(`${count} 款已调整 · ${count} designs ${amount > 0 ? 'raised' : 'lowered'} by ${unit === '$' ? money(Math.round(Math.abs(amount) * 100)) : `${Math.abs(amount)}%`}`
      + (skipped ? `. ${skipped} left out: no price, or $0 or less.` : ''))
  }

  async function openReview() {
    const key = crypto.randomUUID()
    setReview({ key })
    try {
      setReview({ key, summary: await checkDesignPrices(changes) })
    } catch (cause) {
      setReview({ key, error: failure(cause) || 'Unable to check these prices. Try again.' })
    }
  }
  async function save() {
    if (!review?.summary) return
    setReview({ ...review, saving: true })
    try {
      const result = await saveDesignPrices(changes, review.key)
      message.success(`已保存 ${result.saved} 个价格 · ${result.saved} prices saved`)
      setReview(undefined)
      setDrafts({})
      setSelected(new Set())
      setAttempt(value => value + 1)
    } catch (cause) {
      setReview({ ...review, saving: false, error: failure(cause) || 'Nothing was saved. Try again.' })
    }
  }

  const header = <>
    <PageHeader title="产品" sub={`Products · 确认款价格 Design prices${data ? ` · ${data.designs.length} 款` : ''}`} />
    <ProductsTabs />
  </>
  if (error) return <div className="pc-dp">{header}
    <Alert role="alert" type="error" showIcon message={error} action={<Button onClick={() => setAttempt(value => value + 1)}>Retry</Button>} />
  </div>
  if (!data) return <div className="pc-dp">{header}<Skeleton active /></div>

  const set = data.designs.filter(design => design.state === 'set').length
  const link = linkText(data.website)
  const allShown = order.length > 0 && order.every(row => selected.has(row.id))
  return <div className="pc-dp">
    {header}
    {link ? <p className="pc-notice">{link}</p>
      : <p className="pc-dp-lead">保存后一分钟内同步到网站 · Saved prices go to popcore.ca within a minute. A change of more than {data.look_pct}% from a design’s own website price waits for a manager’s OK on 网站 Website; a first price does not.</p>}
    <div className="pc-figures pc-figures-4">
      <div><span>确认款 Confirmed designs</span><strong>{data.designs.length}</strong><small>{data.boxes.length} 个产品 products</small></div>
      <div><span>已定价 Price set</span><strong>{set}</strong><small>These go to the website</small></div>
      <div><span>未定价 No price set yet</span><strong>{data.designs.length - set}</strong><small>The website keeps its price</small></div>
      <div><span>今天改过 Changed today</span><strong>{data.designs.filter(design => design.changed_today).length}</strong><small>{data.today}</small></div>
    </div>

    <div className="pc-dp-tools">
      <Input allowClear prefix={<Icon name="search" size={16} />} placeholder="搜索产品或款式 Search boxes, designs, IDs" aria-label="Search designs"
        value={query} onChange={event => { setQuery(event.target.value); setShown(PAGE) }} />
      <Select allowClear showSearch placeholder="全部 IP · All IPs" aria-label="IP" value={ip} options={ips}
        onChange={value => { setIp(value); setShown(PAGE) }} popupMatchSelectWidth={false} />
      <Segmented<Kind> aria-label="款式 Kind" value={kind} onChange={value => { setKind(value); setShown(PAGE) }}
        options={[{ value: 'all', label: '全部款' }, { value: 'regular', label: '常规 Regular' }, { value: 'secret', label: '隐藏 Secret' }]} />
    </div>
    <div className="pc-dp-chips" role="group" aria-label="Show">
      {filters.map(item => <button key={item.value} type="button" className="pc-chip" aria-pressed={filter === item.value}
        onClick={() => { setFilter(item.value); setShown(PAGE) }}>{item.zh} <span>{item.en}</span></button>)}
    </div>

    {selected.size > 0 && <div className="pc-prod-selection pc-dp-bulk" role="group" aria-label="Change the selected designs">
      <span>已选 {selected.size} 款 · {selected.size} selected</span>
      <label>设为 Set to <Input size="small" aria-label="Set the selected to" placeholder="45.99" inputMode="decimal" value={setTo}
        onChange={event => setSetTo(event.target.value)} onPressEnter={applySet} /></label>
      <Button size="small" onClick={applySet}>应用 Apply</Button>
      <label>加减 Raise/lower <Input size="small" aria-label="Raise or lower the selected by" placeholder={unit === '%' ? '10 or -15' : '5 or -2.50'}
        inputMode="decimal" value={by} onChange={event => setBy(event.target.value)} onPressEnter={applyBy} /></label>
      <Segmented<'%' | '$'> size="small" aria-label="By" value={unit} onChange={setUnit} options={['%', '$']} />
      <Button size="small" onClick={applyBy}>应用 Apply</Button>
      <Button size="small" onClick={() => setSelected(new Set())}>取消选择 Clear</Button>
    </div>}

    <div className="pc-panel pc-dp-list">
      <table className="pc-dp-table">
        <thead><tr>
          <th className="pc-dp-pick"><Checkbox aria-label="Select every design shown" checked={allShown}
            indeterminate={!allShown && order.some(row => selected.has(row.id))} onChange={event => toggle(order, event.target.checked)} /></th>
          <th>款式 Design</th><th className="pc-dp-num">现价 POPCORE</th><th className="pc-dp-num">网站 Website</th>
          <th>新价 New price</th><th>变化 Change</th>
        </tr></thead>
        {visible.map(group => <BoxGroup key={group.box.id ?? 'none'} box={group.box} rows={group.rows} drafts={drafts} edits={edits}
          selected={selected} look={data.look_pct} onType={type} onPaste={paste} onToggle={toggle} />)}
      </table>
      {!groups.length && <p className="pc-quiet">没有 · No designs match.</p>}
      {groups.length > shown && <Button type="link" onClick={() => setShown(value => value + PAGE)}>
        显示更多 Show {Math.min(PAGE, groups.length - shown)} more products ({groups.length - shown} not shown)</Button>}
    </div>

    <div className="pc-dp-save" role="region" aria-label="Unsaved prices">
      <span><strong>{changes.length} 处改动 · {changes.length} changes</strong>
        {invalid > 0 && <small className="pc-dp-out">{invalid} 不是价格 · {invalid} are not prices: fix or clear them</small>}
        {!invalid && changes.length > 0 && <small>Nothing is saved until you review and save.</small>}</span>
      <Button disabled={!dirty} onClick={() => setDrafts({})}>清除 Clear</Button>
      <Button type="primary" disabled={!changes.length || invalid > 0} onClick={openReview}>检查并保存 Review &amp; save</Button>
    </div>

    <Modal open={!!review} width={720} title="确认价格 · Review the new prices" onCancel={() => !review?.saving && setReview(undefined)}
      okText={review?.summary ? `保存 Save ${review.summary.counts.changes}` : 'Save'} cancelText="返回修改 Back"
      okButtonProps={{ disabled: !review?.summary || !review.summary.counts.changes, loading: review?.saving }} onOk={save}>
      {review?.error && <Alert role="alert" type="error" showIcon message={review.error}
        action={<Button size="small" onClick={() => { setReview(undefined); setAttempt(value => value + 1) }}>Reload</Button>} />}
      {!review?.summary && !review?.error && <Skeleton active />}
      {review?.summary && <Summary summary={review.summary} look={data.look_pct} />}
    </Modal>
  </div>
}

const BoxGroup = memo(function BoxGroup({ box, rows, drafts, edits, selected, look, onType, onPaste, onToggle }: {
  box: DesignBox; rows: DesignPriceRow[]; drafts: Record<number, string>; edits: Record<number, Edit>
  selected: ReadonlySet<number>; look: number; onType: (id: number, text: string) => void
  onPaste: (event: ClipboardEvent<HTMLInputElement>, id: number) => void; onToggle: (rows: DesignPriceRow[], on: boolean) => void
}) {
  const all = rows.every(row => selected.has(row.id)), some = rows.some(row => selected.has(row.id))
  const secrets = rows.filter(row => row.secret), regular = rows.filter(row => !row.secret)
  return <tbody>
    <tr className="pc-dp-box">
      <th className="pc-dp-pick"><Checkbox aria-label={`Select every design of ${box.name}`} checked={all} indeterminate={!all && some}
        onChange={event => onToggle(rows, event.target.checked)} /></th>
      <th colSpan={5} scope="rowgroup"><span className="pc-dp-box-name"><strong>{box.name}</strong>
        <small>{[box.sku, box.ip, box.price_cents !== null ? `盒价 Box ${money(box.price_cents)}` : null, `${rows.length} 款`].filter(Boolean).join(' · ')}</small></span>
        {secrets.length > 0 && regular.length > 0 && <span className="pc-dp-box-picks">
          <Button size="small" type="text" onClick={() => { onToggle(rows, false); onToggle(regular, true) }}>选常规 Regular ({regular.length})</Button>
          <Button size="small" type="text" onClick={() => { onToggle(rows, false); onToggle(secrets, true) }}>选隐藏 Secret ({secrets.length})</Button>
        </span>}
      </th>
    </tr>
    {rows.map(row => {
      const edit = edits[row.id], note = hint(row, edit, look)
      return <tr key={row.id} className={row.id in drafts ? 'pc-dp-edited' : undefined}>
        <td className="pc-dp-pick"><Checkbox aria-label={`Select ${row.name}`} checked={selected.has(row.id)} onChange={event => onToggle([row], event.target.checked)} /></td>
        <td className="pc-dp-design"><strong>{row.name}{row.secret && <span className="pc-prod-secret">隐藏</span>}</strong><small>{row.sku}</small></td>
        <td className="pc-dp-num pc-dp-popcore"><strong>{money(row.price_cents) || '—'}</strong>
          <small className={row.state === 'elsewhere' ? 'pc-dp-warn' : undefined}>{popcoreNote(row, box)}</small></td>
        <td className="pc-dp-num pc-dp-web">{row.web.length ? row.web.map(item => <span key={item.wc_id}>
          <strong>{money(item.regular_cents) || '无价 No price'}</strong>
          <small>{item.published ? '在售 Live' : '草稿 Draft'}{item.sale_cents !== null ? ` · sale ${money(item.sale_cents)}` : ''}</small></span>)
          : <small>不在网站 · Not on the website</small>}</td>
        <td className="pc-dp-input"><Input aria-label={`New price for ${row.sku} ${row.name}`} inputMode="decimal" placeholder={money(row.price_cents) || '$'}
          status={edit?.invalid ? 'error' : undefined} value={drafts[row.id] ?? ''} onChange={event => onType(row.id, event.target.value)}
          onPaste={event => onPaste(event, row.id)} /></td>
        <td className="pc-dp-hint">{note && <small className={note.tone ? `pc-dp-${note.tone}` : undefined}>{note.text}</small>}</td>
      </tr>
    })}
  </tbody>
}, (before, after) => before.box === after.box && before.rows === after.rows && before.look === after.look
  && before.onType === after.onType && before.onPaste === after.onPaste && before.onToggle === after.onToggle
  && before.rows.every(row => before.drafts[row.id] === after.drafts[row.id] && before.selected.has(row.id) === after.selected.has(row.id)))

function effectText(item: DesignEffect, look: number) {
  const from = item.regular_cents
  switch (item.state) {
    case 'held': return `网站 ${money(from)} → ${money(item.price)}: ${item.reasons.map(reason =>
      reason.code === 'big_change' ? `${reason.percent > 0 ? '+' : ''}${reason.percent}%, more than ${look}%`
        : reason.code === 'sale_stops' ? `its sale ${money(reason.sale_cents)} would end`
          : reason.code === 'sale_starts' ? `its sale ${money(reason.sale_cents)} would start again` : 'goes with the other option').join('; ')}`
    case 'send': {
      if (item.first) return `网站 ${from ? `${money(from)} (${item.published ? 'live' : 'draft'})` : 'no price'} → ${money(item.price)}: first price`
      const change = from && item.price ? percent(from, item.price) : 0
      return `网站 ${money(from)} → ${money(item.price)} (${change > 0 ? '+' : ''}${change}%)`
    }
    case 'daily': return `网站 ${money(from)} → ${money(item.price)} after the Daily Shop’s reset`
    case 'same': return `网站 already ${money(item.price)}`
    default: return '网站 keeps its price'
  }
}

// The summary before saving: the server applies the change, judges it as the website sync will, and undoes it.
function Summary({ summary, look }: { summary: DesignPriceSummary; look: number }) {
  const { counts } = summary
  const held = summary.changes.filter(change => change.website.some(item => item.state === 'held'))
  const first = summary.changes.filter(change => !held.includes(change) && change.website.some(item => item.first && item.state !== 'same'))
  const offline = summary.changes.filter(change => !change.website.length)
  const rest = summary.changes.filter(change => !held.includes(change) && !first.includes(change) && !offline.includes(change))
  const line = (change: DesignPriceSummary['changes'][number]) => <li key={change.product_id}>
    <strong>{change.sku} {change.box_name ? `${change.box_name} · ` : ''}{change.name}</strong>
    <small>POPCORE {money(change.old_cents) || 'no price'}{change.first ? (change.old_cents !== null && change.old_cents === change.box_cents
      ? ' (box price, not its own)' : ' (never set)') : ''} → {money(change.new_cents)}
      {change.website.map(item => ` · ${effectText(item, look)}`).join('')}</small>
  </li>
  const link = linkText(summary.website)
  return <div className="pc-dp-summary">
    <div className="pc-figures pc-figures-4">
      <div><span>改价 Changes</span><strong>{counts.changes}</strong><small>{counts.first} never had a price of their own</small></div>
      <div><span>首次定价 First prices</span><strong>{first.length}</strong><small>No OK needed</small></div>
      <div><span>直接同步 Go to popcore.ca</span><strong>{counts.send + counts.daily}</strong><small>{counts.same ? `${counts.same} already match` : 'With the next write'}</small></div>
      <div><span>等经理确认 Wait for an OK</span><strong className={counts.held ? 'pc-figure-warn' : undefined}>{counts.held}</strong><small>On 网站 Website</small></div>
    </div>
    {link && <p className="pc-notice">{link}</p>}
    {held.length > 0 && <section><h3>等经理确认 · Wait for a manager’s OK <small>more than {look}% from the price customers see, or a sale would change</small></h3><ul>{held.map(line)}</ul></section>}
    {first.length > 0 && <section><h3>首次定价 · First prices <small>the website had only the box’s price, a draft’s price, or none</small></h3><ul>{first.map(line)}</ul></section>}
    {rest.length > 0 && <section><h3>其他改动 · Other changes</h3><ul>{rest.map(line)}</ul></section>}
    {offline.length > 0 && <section><h3>不在网站 · Not on the website <small>POPCORE’s price only</small></h3><ul>{offline.map(line)}</ul></section>}
    <p className="pc-quiet">The new price is also POPCORE’s price for the design: what the shop charges and what stock value counts.</p>
  </div>
}
