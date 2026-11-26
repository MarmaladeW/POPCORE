import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Input, Segmented, Select, Skeleton, message } from 'antd'

import {
  getPopmartPrices, linkPopmart, readPopmartNow, unlinkPopmart,
  type PopmartPriceList, type PopmartRow, type PopmartSide, type PopmartStatus,
} from '../../api/popmartPrices'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { ProductsTabs } from './ProductsNav'
import './products.css'
import './designPrices.css'
import './popmartPrices.css'

// POP MART 价格 POP MART prices (owner, 2026-11-25, popcore-work#91): POPCORE's price beside POP MART Canada's, with
// POP MART US in US dollars for reference. Read every Monday at 6:00 AM, or now. Nothing here changes a price.

type Filter = 'all' | 'higher' | 'lower' | 'same' | 'missing' | 'no_price'
type Kind = 'all' | PopmartRow['kind']
const PAGE = 60
const filters: Array<{ value: Filter; zh: string; en: string; has: PopmartStatus[] }> = [
  { value: 'all', zh: '全部', en: 'All', has: [] },
  { value: 'higher', zh: '我们更贵', en: 'We are higher', has: ['higher'] },
  { value: 'lower', zh: '我们更便宜', en: 'We are lower', has: ['lower'] },
  { value: 'same', zh: '同价', en: 'Same price', has: ['same'] },
  { value: 'missing', zh: 'POP MART 没有', en: 'Not at POP MART CA', has: ['not_found', 'no_option'] },
  { value: 'no_price', zh: '没有价格', en: 'No POPCORE price', has: ['no_price'] },
]
const kinds: Record<PopmartRow['kind'], string> = { box: '盲盒 Box', set: '整端 Whole Set', item: '单品 Item' }
const fold = (text: string | null | undefined) => (text || '').normalize('NFKC').toLocaleLowerCase()
const money = (cents: number | null | undefined, currency = 'CAD') =>
  cents === null || cents === undefined ? '' : `${currency === 'USD' ? 'US$' : '$'}${(cents / 100).toFixed(2)}`
const when = (utc: string | null | undefined) => {
  const time = utc ? new Date(`${utc.replace(' ', 'T')}Z`) : null
  return time && !Number.isNaN(time.getTime())
    ? time.toLocaleString('en-CA', { timeZone: 'America/Toronto', weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : ''
}
const failure = (cause: unknown) => (cause as { _serverMessage?: string })?._serverMessage

function Stock({ side }: { side: PopmartSide }) {
  if (side.in_stock === null) return null
  return <span className={side.in_stock ? 'pc-pm-stock' : 'pc-pm-stock pc-pm-out'}>{side.in_stock ? '有货 In stock' : '售罄 Sold out'}</span>
}

function Side({ side, kind }: { side: PopmartSide | null; kind: PopmartRow['kind'] }) {
  if (!side) return <small>没有 · Not found</small>
  if (side.price_cents === null) return <span><small>{kind === 'set' ? '不卖整端 · No Whole Set' : '没有单盒 · No single box'}</small>
    <a className="pc-pm-name" href={side.url} target="_blank" rel="noreferrer">{side.title}</a></span>
  return <span>
    <strong>{money(side.price_cents, side.currency)}{side.sale_cents !== null && <small className="pc-pm-sale">sale · was {money(side.regular_cents, side.currency)}</small>}</strong>
    <small><Stock side={side} />{side.option ? ` · ${side.option}` : ''}</small>
    <a className="pc-pm-name" href={side.url} target="_blank" rel="noreferrer">{side.title}</a>
  </span>
}

function Difference({ row }: { row: PopmartRow }) {
  if (row.status === 'no_price') return <small>POPCORE 没有价格 · No POPCORE price</small>
  if (row.diff_cents === null) return null
  if (row.diff_cents === 0) return <small>同价 · Same</small>
  const percent = row.ca?.price_cents ? Math.round((row.diff_cents / row.ca.price_cents) * 100) : null
  return <span className={row.diff_cents > 0 ? 'pc-dp-warn' : 'pc-pm-lower'}>
    <strong>{row.diff_cents > 0 ? '+' : '−'}{money(Math.abs(row.diff_cents))}</strong>
    <small>{row.diff_cents > 0 ? '我们更贵 We are higher' : '我们更便宜 We are lower'}{percent ? ` · ${Math.abs(percent)}%` : ''}</small>
  </span>
}

export default function PopmartPrices() {
  const [data, setData] = useState<PopmartPriceList>()
  const [error, setError] = useState<string>()
  const [attempt, setAttempt] = useState(0)
  const [filter, setFilter] = useState<Filter>('all')
  const [kind, setKind] = useState<Kind>('all')
  const [query, setQuery] = useState('')
  const [shown, setShown] = useState(PAGE)
  const [linking, setLinking] = useState<number>()
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    getPopmartPrices().then(result => { if (live) { setData(result); setError(undefined) } })
      .catch(cause => { if (live) setError(failure(cause) || 'Unable to load POP MART prices. Try again.') })
    return () => { live = false }
  }, [attempt])

  // While a read is asked for or running, look again every 10 seconds until it is done.
  const waiting = !!data && (data.requested || data.running)
  useEffect(() => {
    if (!waiting) return
    const timer = window.setTimeout(() => setAttempt(value => value + 1), 10_000)
    return () => window.clearTimeout(timer)
  }, [waiting, data])

  const rows = useMemo(() => {
    if (!data) return []
    const want = filters.find(item => item.value === filter)!.has, text = fold(query.trim())
    return data.rows.filter(row => (!want.length || want.includes(row.status)) && (kind === 'all' || row.kind === kind)
      && (!text || fold(`${row.name} ${row.sku} ${row.ca?.title ?? ''} ${row.us?.title ?? ''}`).includes(text)))
  }, [data, filter, kind, query])
  const titles = useMemo(() => (data?.titles ?? []).map(title => ({ value: title, label: title })), [data])

  async function readNow() {
    setBusy(true)
    try {
      await readPopmartNow()
      message.success('一分钟内开始读取 · The read starts within a minute and takes about two')
      setAttempt(value => value + 1)
    } catch (cause) {
      message.error(failure(cause) || 'Could not ask for a read. Try again.')
    } finally { setBusy(false) }
  }
  async function link(row: PopmartRow, title: string) {
    setBusy(true)
    try {
      await linkPopmart(row.product_id, title)
      setLinking(undefined)
      setAttempt(value => value + 1)
    } catch (cause) {
      message.error(failure(cause) || 'Not linked. Try again.')
    } finally { setBusy(false) }
  }
  async function unlink(row: PopmartRow) {
    setBusy(true)
    try {
      await unlinkPopmart(row.product_id)
      setAttempt(value => value + 1)
    } catch (cause) {
      message.error(failure(cause) || 'Not unlinked. Try again.')
    } finally { setBusy(false) }
  }

  const header = <>
    <PageHeader title="产品" sub="Products · POP MART 价格 POP MART prices"
      actions={data && <Button disabled={waiting || busy} loading={waiting} onClick={readNow}>{waiting ? '读取中 Reading…' : '现在读取 Read now'}</Button>}
      phoneAction={data && <Button size="small" disabled={waiting || busy} loading={waiting} onClick={readNow}>{waiting ? '读取中 Reading…' : '现在读取 Read now'}</Button>} />
    <ProductsTabs />
  </>
  if (error) return <div className="pc-dp">{header}
    <Alert role="alert" type="error" showIcon message={error} action={<Button onClick={() => setAttempt(value => value + 1)}>Retry</Button>} />
  </div>
  if (!data) return <div className="pc-dp">{header}<Skeleton active /></div>

  const { counts, reads } = data
  const failed = (['ca', 'us'] as const).filter(region => reads[region].last?.status === 'error')
  const never = !reads.ca.good_at && !reads.us.good_at
  return <div className="pc-dp pc-pm">
    {header}
    <p className="pc-dp-lead">每周一早上 6:00 读取 POP MART 加拿大和美国 · POPCORE reads POP MART Canada and US every Monday at 6:00 AM
      (next: {when(data.next_read)}). Nothing here changes a price or the website.
      {(['ca', 'us'] as const).map(region => reads[region].good_at && <span key={region}> · {region.toUpperCase()}: {reads[region].products.toLocaleString('en-CA')} products, read {when(reads[region].good_at)}</span>)}</p>
    {failed.map(region => <p key={region} className="pc-notice" role="status">{region.toUpperCase()} 读取失败 · The last {region.toUpperCase()} read failed
      ({when(reads[region].last!.started_at)}): {reads[region].last!.error} {reads[region].good_at ? 'The earlier list is kept.' : ''}</p>)}
    {never && !waiting && <p className="pc-notice">还没有读取 · POP MART has not been read yet. Press Read now, or wait for Monday.</p>}

    <div className="pc-figures pc-figures-4">
      <div><span>我们更贵 We are higher</span><strong className={counts.higher ? 'pc-figure-warn' : undefined}>{counts.higher}</strong><small>than POP MART CA</small></div>
      <div><span>我们更便宜 We are lower</span><strong>{counts.lower}</strong><small>than POP MART CA</small></div>
      <div><span>同价 Same price</span><strong>{counts.same}</strong><small>{counts.no_price ? `${counts.no_price} with no POPCORE price` : 'to the cent'}</small></div>
      <div><span>POP MART 没有 Not at POP MART CA</span><strong>{counts.not_found + counts.no_option}</strong><small>{counts.no_option ? `${counts.no_option} found without that option` : 'or a different name: link it'}</small></div>
    </div>

    <div className="pc-dp-tools pc-pm-tools">
      <Input allowClear prefix={<Icon name="search" size={16} />} placeholder="搜索 Search products, IDs, POP MART names" aria-label="Search"
        value={query} onChange={event => { setQuery(event.target.value); setShown(PAGE) }} />
      <Segmented<Kind> aria-label="种类 Kind" value={kind} onChange={value => { setKind(value); setShown(PAGE) }}
        options={[{ value: 'all', label: '全部' }, { value: 'box', label: '盲盒 Box' }, { value: 'set', label: '整端 Set' }, { value: 'item', label: '单品 Item' }]} />
    </div>
    <div className="pc-dp-chips" role="group" aria-label="Show">
      {filters.filter(item => item.value !== 'no_price' || counts.no_price > 0).map(item => <button key={item.value} type="button" className="pc-chip"
        aria-pressed={filter === item.value} onClick={() => { setFilter(item.value); setShown(PAGE) }}>{item.zh} <span>{item.en}</span></button>)}
    </div>

    <div className="pc-panel pc-dp-list">
      <table className="pc-dp-table pc-pm-table">
        <thead><tr>
          <th>产品 Product</th><th className="pc-dp-num">POPCORE</th><th>POP MART CA</th><th>差价 Difference</th><th>POP MART US</th>
        </tr></thead>
        <tbody>
          {rows.slice(0, shown).map(row => <tr key={row.product_id}>
            <td className="pc-pm-product"><strong>{row.name}</strong>
              <small>{row.sku} · {kinds[row.kind]}{row.kind === 'set' ? ' · follows its box' : ''}</small>
              {row.linked && <small>手动对应 Linked by hand · <Button type="link" size="small" disabled={busy} onClick={() => unlink(row)}>取消 Unlink</Button></small>}</td>
            <td className="pc-dp-num" data-label="POPCORE"><strong>{money(row.our_cents) || '—'}</strong></td>
            <td className="pc-pm-side" data-label="POP MART CA">
              <Side side={row.ca} kind={row.kind} />
              {row.status === 'not_found' && row.kind !== 'set' && <div className="pc-pm-link">
                {row.suggestions.map(title => <Button key={title} size="small" disabled={busy} onClick={() => link(row, title)}>是这个吗 Is it: {title}?</Button>)}
                {linking === row.product_id
                  ? <Select showSearch autoFocus size="small" placeholder="POP MART 名称 · Pick POP MART's name" options={titles} disabled={busy}
                      optionFilterProp="label" popupMatchSelectWidth={false} onChange={title => link(row, title)} onBlur={() => setLinking(undefined)} />
                  : <Button type="link" size="small" onClick={() => setLinking(row.product_id)}>对应 Link to a POP MART name…</Button>}
              </div>}
            </td>
            <td className="pc-pm-diff" data-label="差价 Difference"><Difference row={row} /></td>
            <td className="pc-pm-side pc-pm-us" data-label="POP MART US">{row.us ? <Side side={row.us} kind={row.kind} /> : <small>没有 · Not found</small>}</td>
          </tr>)}
        </tbody>
      </table>
      {!rows.length && <p className="pc-quiet">没有 · No products match.</p>}
      {rows.length > shown && <Button type="link" onClick={() => setShown(value => value + PAGE)}>
        显示更多 Show {Math.min(PAGE, rows.length - shown)} more ({rows.length - shown} not shown)</Button>}
    </div>
  </div>
}
