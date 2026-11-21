import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Alert, Button, Input, Select, Skeleton } from 'antd'
import PageHeader from '../../components/pc/PageHeader'
import { Panel } from '../../components/pc/Panel'
import InventoryPhoto from '../Stock/InventoryPhoto'
import { getCountScope, searchCount, type CountLocation, type CountResult, type CountRole } from '../../api/counting'
import {
  finishSectionCount, getSectionCount, getSectionCountSummary,
  type SectionCountSummary, type SectionCountView, type SummaryRow,
} from '../../api/countSections'
import { CountCardPanel } from './index'
import { linesText, nextCard, placeName, shortDay } from './sectionsView'
import '../Stock/seriesInventory.css'
import './count.css'

// One section count: its cards place by place; the manager opens each, enters what the counter found
// on 盘点's card (only this section's items of it), and Next goes on to the next card not yet entered.
type Failure = { _serverMessage?: string }
const messageOf = (cause: unknown, fallback: string) => (cause as Failure)?._serverMessage || fallback
type Open = { placeId: number; place: string; productId: number; roles: CountRole[] | null }

export default function SectionCountPage() {
  const id = Number(useParams().id) || 0
  const [view, setView] = useState<SectionCountView | null>(null)
  const [summary, setSummary] = useState<SectionCountSummary | null>(null)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [open, setOpen] = useState<Open | null>(null)
  const [finishing, setFinishing] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    setError('')
    getSectionCount(id, controller.signal).then(async data => {
      setView(data)
      if (data.finished_at) setSummary(await getSectionCountSummary(id, controller.signal))
    }).catch(cause => { if (!controller.signal.aborted) setError(messageOf(cause, 'Unable to load this section count.')) })
    return () => controller.abort()
  }, [id, reload])

  const goNext = async () => {
    const current = open
    try {
      const fresh = await getSectionCount(id)
      setView(fresh)
      const upcoming = nextCard(fresh, current?.placeId ?? null, current?.productId ?? null)
      setOpen(upcoming ? { placeId: upcoming.place.id, place: upcoming.place.code, productId: upcoming.card.product_id,
        roles: upcoming.card.roles } : null)
    } catch (cause) { setOpen(null); setError(messageOf(cause, 'Unable to load this section count.')) }
    window.scrollTo({ top: 0 })
  }

  const finish = async () => {
    if (!view) return
    const left = view.lines - view.counted
    if (left > 0 && !window.confirm(`${linesText(left)} not entered yet. Finish anyway? They show as not counted.`)) return
    setFinishing(true)
    setError('')
    try { setSummary(await finishSectionCount(view.id)); setOpen(null) }
    catch (cause) { setError(messageOf(cause, 'Could not finish. Try again.')) } finally { setFinishing(false) }
  }

  const title = view?.section.name ?? '分区盘点'
  return <div className="pc-count pc-sections">
    <PageHeader title={title} sub={view ? `${view.counter} counting · ${shortDay(view.business_date)}` : 'Section count'}
      back={open ? undefined : { label: '分区', to: '/count/sections' }} />
    {error && <Alert type="error" showIcon message={error} action={<Button onClick={() => setReload(n => n + 1)}>Retry</Button>} />}
    {!view ? !error && <Skeleton active paragraph={{ rows: 4 }} />
      : summary ? <SummaryView summary={summary} />
      : open ? <>
        <Button className="pc-sections-back" onClick={() => { setOpen(null); setReload(n => n + 1) }}>‹ 列表 List · {view.counted}/{view.lines}</Button>
        <CountCardPanel key={`${open.placeId}:${open.productId}`} storeCode={view.store.code} location={open.place}
          productId={open.productId} live refreshScope={() => undefined} onNext={() => void goNext()}
          roles={open.roles} sectionCountId={view.id} nextLabel="下一个 Next card" />
      </>
      : <>
        <p className="pc-sections-progress" aria-live="polite">
          <strong>已点 {view.counted} / {view.lines}</strong> lines · {view.counter} counts, you enter
        </p>
        {view.places.map(place => <Panel key={place.id} zh={placeName(place.code, place.name)}
          count={`${place.cards.reduce((sum, card) => sum + card.counted, 0)}/${place.cards.reduce((sum, card) => sum + card.lines, 0)}`}
          tone="ok" label={placeName(place.code, place.name)}>
          <ul className="pc-count-results">
            {place.cards.map(card => <li key={card.product_id}>
              <button type="button" onClick={() => setOpen({ placeId: place.id, place: place.code, productId: card.product_id, roles: card.roles })}>
                <InventoryPhoto filename={card.image} name={card.title} />
                <span className="pc-count-result-text"><strong>{card.title}</strong>
                  <small>{[card.sku, card.added ? '架上找到 Found on the shelf' : null].filter(Boolean).join(' · ')}</small></span>
                <span className={`pc-count-done${card.counted >= card.lines ? ' is-done' : ''}`}>
                  {card.counted >= card.lines ? '已点 Done' : card.counted ? `${card.counted}/${card.lines}` : '未点'}</span>
              </button>
            </li>)}
          </ul>
        </Panel>)}
        <FoundOnShelf storeCode={view.store.code} onOpen={(placeId, place, productId) => setOpen({ placeId, place, productId, roles: null })} />
        <div className="pc-count-actions pc-sections-finish">
          <Button type="primary" loading={finishing} onClick={() => void finish()}>完成 Finish · {view.counted}/{view.lines}</Button>
        </div>
      </>}
  </div>
}

// Something on the shelf the list does not have (POPCORE shows none there): search it and count it here.
function FoundOnShelf({ storeCode, onOpen }: { storeCode: string; onOpen: (placeId: number, place: string, productId: number) => void }) {
  const [places, setPlaces] = useState<CountLocation[]>([])
  const [place, setPlace] = useState('')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<CountResult[] | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    getCountScope(storeCode, controller.signal).then(scope => { setPlaces(scope.locations); setPlace(scope.locations[0]?.code ?? '') })
      .catch(() => undefined)
    return () => controller.abort()
  }, [storeCode])
  useEffect(() => {
    const text = query.trim()
    if (!text || !place) { setResults(null); return }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      searchCount(storeCode, place, text, controller.signal).then(setResults).catch(() => undefined)
    }, 250)
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [storeCode, place, query])
  const location = places.find(item => item.code === place)
  return <Panel zh="架上找到" en="Found on the shelf" label="Found on the shelf">
    <p className="pc-count-quiet">Something here that the list does not show? Find it and count it into this section count.</p>
    <div className="pc-sections-find">
      <Select aria-label="Where it was found" value={place || undefined} onChange={setPlace}
        options={places.map(item => ({ value: item.code, label: placeName(item.code, item.name) }))} />
      <Input allowClear value={query} onChange={event => setQuery(event.target.value)} aria-label="Search a product found on the shelf"
        placeholder="搜索 Search name / 记账名 / SKU" />
    </div>
    {results && location && (results.length ? <ul className="pc-count-results">
      {results.map(result => <li key={result.product_id}>
        <button type="button" onClick={() => { setQuery(''); onOpen(location.id, location.code, result.product_id) }}>
          <InventoryPhoto filename={result.image} name={result.title} />
          <span className="pc-count-result-text"><strong>{result.title}</strong><small>{result.sku}</small></span>
        </button>
      </li>)}
    </ul> : <p className="pc-count-quiet">No product matches.</p>)}
  </Panel>
}

function SummaryView({ summary }: { summary: SectionCountSummary }) {
  const differences = summary.rows.filter(row => row.difference)
  const same = summary.rows.filter(row => row.difference === 0)
  const missing = summary.rows.filter(row => row.counted == null)
  return <>
    <section className="pc-sections-summary" aria-label="Summary">
      <p><strong>{summary.section.name}</strong> · {summary.counter} counted · {summary.finished_by ?? summary.started_by} entered
        {summary.finished_at ? ` · finished ${shortDay(summary.business_date)}` : ''}</p>
      <dl>
        <div><dt>已点 Counted</dt><dd>{summary.counted}/{summary.lines}</dd></div>
        <div><dt>多 More</dt><dd className="is-more">+{summary.more}</dd></div>
        <div><dt>少 Less</dt><dd className="is-less">−{summary.less}</dd></div>
        <div><dt>价值 Value</dt><dd className={summary.value < 0 ? 'is-less' : summary.value > 0 ? 'is-more' : ''}>
          {summary.value < 0 ? '−' : summary.value > 0 ? '+' : ''}${Math.abs(summary.value).toFixed(2)}</dd></div>
      </dl>
    </section>
    <SummaryRows zh="差异" en="Differences" rows={differences} empty="没有差异 · Every count matched POPCORE." />
    {missing.length > 0 && <SummaryRows zh="没点" en="Not counted" rows={missing} />}
    {same.length > 0 && <SummaryRows zh="没变" en="Matched" rows={same} />}
  </>
}

function SummaryRows({ zh, en, rows, empty }: { zh: string; en: string; rows: SummaryRow[]; empty?: string }) {
  return <Panel zh={zh} en={en} count={rows.length} tone={zh === '差异' && rows.length ? 'warn' : 'ok'} label={en}>
    {!rows.length ? <p className="pc-count-quiet">{empty}</p> : <ul className="pc-sections-rows">
      {rows.map(row => <li key={`${row.place}:${row.product_id}`}>
        <span><strong>{row.name}</strong><small>{[row.sku, placeName(row.place, row.place_name), row.added ? 'found on the shelf' : null,
          row.entered_by ? `entered by ${row.entered_by}` : null].filter(Boolean).join(' · ')}</small></span>
        <span className="pc-sections-figure">{row.counted == null ? `POPCORE ${row.expected}` : `${row.expected} → ${row.counted}`}
          {row.difference ? <em className={row.difference > 0 ? 'is-more' : 'is-less'}>{row.difference > 0 ? '+' : ''}{row.difference}</em> : null}
        </span>
      </li>)}
    </ul>}
  </Panel>
}
