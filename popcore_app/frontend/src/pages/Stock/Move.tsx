import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Input, Popconfirm, Skeleton } from 'antd'
import { useSearchParams } from 'react-router-dom'
import client from '../../api/client'
import { useAppStore } from '../../store'
import PageHeader from '../../components/pc/PageHeader'
import { Panel } from '../../components/pc/Panel'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { errorMessage } from '../Sales/checkoutParts'
import { Problem, ProductSearch } from '../Sales/QuickSale'
import {
  asksSets, asksSetSize, isShort, landing, moveLines, unitWord, waitingFor,
  type CheckLine, type Move, type MoveItem, type MovesDay, type Place, type Places,
} from './moveStock'
import '../Sales/QuickSale.css'
import './move.css'

const noAnswer = (cause: unknown) => !(cause as { response?: unknown })?.response
const clock = (timestamp: string) => {
  const parsed = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(timestamp) ? timestamp : `${timestamp.replace(' ', 'T')}Z`)
  return Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'America/Toronto' })
}

/** 移货 Move stock (owner, 2026-11-24: "simple down all of the moving around of stock"): every move is three answers,
 *  从哪里, 到哪里, and which goods and how many, saved in one step. It stands in for 补货, 入店, 入库, 调拨, 入 display,
 *  the claw buttons and 损耗, and for every stock line a 日报 used to carry. */
export default function MoveStockPage() {
  const phone = useIsPhone()
  const store = useAppStore(state => state.selectedStore)
  const [params] = useSearchParams()
  const [places, setPlaces] = useState<Places>(), [loadError, setLoadError] = useState('')
  const [from, setFrom] = useState(''), [to, setTo] = useState(''), [reason, setReason] = useState(''), [note, setNote] = useState('')
  const [items, setItems] = useState<MoveItem[]>([]), [checked, setChecked] = useState<CheckLine[]>([])
  const [saving, setSaving] = useState(false), [error, setError] = useState(''), [saved, setSaved] = useState<Move>()
  const [day, setDay] = useState<MovesDay>(), [refresh, setRefresh] = useState(0)
  const saveKey = useRef({ body: '', key: '' })
  const storeId = store && store.code !== 'ALL' ? store.id : undefined

  useEffect(() => {
    setPlaces(undefined); setItems([]); setSaved(undefined); setLoadError('')
    if (!storeId) return
    const controller = new AbortController()
    client.get<Places>('/stock-moves/places', { params: { store_id: storeId }, signal: controller.signal, timeout: 15000 })
      .then(({ data }) => { setPlaces(data); setFrom(data.usual.from ?? ''); setTo(data.usual.to ?? '') })
      .catch(cause => { if (!controller.signal.aborted) setLoadError(errorMessage(cause)) })
    return () => controller.abort()
  }, [storeId])
  useEffect(() => {
    if (!storeId) { setDay(undefined); return }
    const controller = new AbortController()
    client.get<MovesDay>('/stock-moves', { params: { store_id: storeId }, signal: controller.signal, timeout: 15000 })
      .then(({ data }) => setDay(data)).catch(() => {})
    return () => controller.abort()
  }, [storeId, refresh])

  async function add(product: { id: number; name: string; sku: string }) {
    if (!storeId) return
    setSaved(undefined); setError('')
    try {
      const { data } = await client.get<{ product_id: number; name: string; sku: string; unit: string | null; per_set: number | null; can_have_set: boolean; movable: boolean }>(
        `/stock-moves/products/${product.id}`, { params: { store_id: storeId }, timeout: 15000 })
      if (!data.movable) { setError(`「${data.name}」不计库存或还没核对，不用移。`); return }
      setItems(old => old.some(item => item.productId === data.product_id) ? old
        : [...old, { productId: data.product_id, name: data.name, sku: data.sku, unit: data.unit, perSet: data.per_set, canHaveSet: data.can_have_set, sets: '', units: '', setSize: '' }])
    } catch (cause) { setError(errorMessage(cause)) }
  }
  // A product opened from 库存 comes already on the list.
  const asked = params.get('product')
  useEffect(() => { if (asked && places && /^\d+$/.test(asked)) add({ id: Number(asked), name: '', sku: '' }) }, [asked, !!places])

  const lines = useMemo(() => moveLines(items, from, to), [items, from, to])
  const waiting = waitingFor(from, to, reason, note, items)
  const body = JSON.stringify({ store_id: storeId, from, to, lines, ...(to === 'gone' ? { reason, note: note.trim() } : {}) })

  // The server says where POPCORE holds fewer than are being moved, before anything is saved.
  useEffect(() => {
    setChecked([])
    if (!storeId || !from || !to || from === to || !lines.length) return
    const controller = new AbortController()
    const timer = setTimeout(() => client.post<{ lines: CheckLine[] }>('/stock-moves/check', { store_id: storeId, from, to, lines, reason: reason || 'other', note: 'check' },
      { signal: controller.signal, timeout: 15000 }).then(({ data }) => setChecked(data.lines)).catch(() => {}), 250)
    return () => { controller.abort(); clearTimeout(timer) }
  }, [storeId, from, to, JSON.stringify(lines)])

  async function save() {
    if (waiting || !storeId) return
    if (saveKey.current.body !== body || !saveKey.current.key) saveKey.current = { body, key: crypto.randomUUID() }
    setSaving(true); setError('')
    try {
      const { data } = await client.post<Move>('/stock-moves', JSON.parse(body), { headers: { 'Idempotency-Key': saveKey.current.key }, timeout: 30000 })
      saveKey.current = { body: '', key: '' }
      setSaved(data); setItems([]); setReason(''); setNote(''); setRefresh(n => n + 1)
    } catch (cause) {
      if (!noAnswer(cause)) saveKey.current = { body: '', key: '' }
      setError(noAnswer(cause) ? '网络断了。先看下面 今天 有没有这一笔；没有就再按一次保存，不会记两次。' : errorMessage(cause))
      setRefresh(n => n + 1)
    } finally { setSaving(false) }
  }

  const header = <PageHeader title="移货" sub={storeId ? store!.name : 'Move stock'} />
  if (!storeId) return <div className="qs-page mv-page">{header}<p className="qs-quiet">先在右上角选一家店。</p></div>
  if (loadError) return <div className="qs-page mv-page">{header}<Problem>{loadError}</Problem></div>
  if (!places) return <div className="qs-page mv-page">{header}<Skeleton active paragraph={{ rows: 6 }} /></div>
  const name = (list: Place[], code: string) => list.find(place => place.code === code)?.name ?? ''
  const checkedLine = (productId: number) => checked.find(line => line.product_id === productId)
  const opened = (productId: number) => checked.find(line => line.product_id === productId)?.opened ?? 0

  return <div className="qs-page mv-page">
    {header}
    <div className="qs-columns">
      <div className="qs-main">
        {saved && <section className="qs-saved" role="status" aria-label="Saved">
          <p className="qs-saved-head"><i className="pc-dot pc-dot-ok" aria-hidden="true" /><strong>已移好</strong><span>{saved.from_name} → {saved.to_name}</span></p>
          <p className="qs-quiet">{saved.lines.map(line => line.text).join('、')}</p>
          {saved.lines.some(isShort) && <p className="qs-quiet mv-short"><i className="pc-dot pc-dot-warn" aria-hidden="true" />电脑里{saved.from_name}不够的也算移过去了，{saved.from_name}有空盘点一下</p>}
          {saved.can_undo && <div className="qs-saved-actions"><UndoMove move={saved} done={() => { setSaved(undefined); setRefresh(n => n + 1) }} /></div>}
        </section>}
        <section className="pc-panel mv-form" aria-label="Move stock">
          <Chips label="从哪里" places={places.from} value={from} onPick={code => { setFrom(code); if (code === to) setTo(''); if (code !== 'claw' && reason === 'claw_prize') setReason(''); setSaved(undefined) }} />
          <Chips label="到哪里" places={places.to} value={to} onPick={code => { setTo(code); if (code === from) setFrom(''); setSaved(undefined) }} />
          {to === 'gone' && <>
            {/* A prize comes out of the claw machine only; the server refuses it from anywhere else. */}
            <Chips label="为什么" places={places.reasons.filter(entry => entry.code !== 'claw_prize' || from === 'claw')} value={reason} onPick={setReason} />
            {reason === 'other' && <Input aria-label="What happened" placeholder="写一下原因" value={note} maxLength={300} onChange={event => setNote(event.target.value)} />}
          </>}
          <ProductSearch phone={phone} onPick={add} />
          {items.length > 0 && <ul className="mv-lines" aria-label="Goods">
            {items.map((item, index) => {
              const change = (value: Partial<MoveItem>) => setItems(old => old.map((entry, at) => at === index ? { ...entry, ...value } : entry))
              const line = checkedLine(item.productId), short = isShort(line)
              // 少 3, or just 不够 where this account does not see the place's numbers.
              const by = line?.missing != null ? `少 ${line.missing}` : '不够 Not enough'
              return <li key={item.productId}>
                <div className="mv-line-head">
                  <strong>{item.name}</strong>
                  <button type="button" className="qs-link" aria-label={`Remove ${item.name}`} onClick={() => setItems(old => old.filter((_, at) => at !== index))}>移除</button>
                </div>
                <div className="mv-amounts">
                  {asksSets(from, item) && <label><Input inputMode="numeric" aria-label={`${item.name} sets`} value={item.sets} onChange={event => change({ sets: event.target.value.replace(/\D/g, '') })} />端</label>}
                  <label><Input inputMode="numeric" aria-label={`${item.name} ${item.unit === 'box' ? 'boxes' : 'pieces'}`} value={item.units} onChange={event => change({ units: event.target.value.replace(/\D/g, '') })} />{unitWord(item.unit)}</label>
                  {asksSetSize(from, to, item) && <label>每端<Input inputMode="numeric" aria-label={`${item.name} boxes per set`} value={item.setSize} onChange={event => change({ setSize: event.target.value.replace(/\D/g, '') })} />盒</label>}
                  {landing(item, from) && <span className="mv-landing">{landing(item, from)}</span>}
                </div>
                {opened(item.productId) > 0 && <p className="qs-quiet">会拆开 {opened(item.productId)} 端，没移走的散盒留在{name(places.from, from)}</p>}
                {short && <p className="qs-quiet mv-short"><i className="pc-dot pc-dot-warn" aria-hidden="true" />{to === 'gone'
                  ? (line?.missing != null ? `电脑里${name(places.from, from)}只有这么多，少算 ${line.missing}` : `电脑里${name(places.from, from)}不够 Not enough`)
                  : `电脑里${name(places.from, from)}${by}，照样移过去`}</p>}
              </li>
            })}
          </ul>}
          <div className="qs-paybar">
            {error && <Problem>{error}</Problem>}
            <Button type="primary" block size="large" className="qs-save" loading={saving} disabled={!!waiting} onClick={save}>
              {waiting || `保存 · ${name(places.from, from)} → ${name(places.to, to)}`}
            </Button>
          </div>
        </section>
      </div>
      <div className="qs-side">
        <Panel zh="今天" en={day?.moves.some(move => !move.undone) ? `${day.moves.filter(move => !move.undone).length} 次` : undefined} label="Today's moves">
          {!day ? <Skeleton active paragraph={{ rows: 2 }} /> : !day.moves.length ? <p className="qs-quiet qs-empty">今天还没有移货。</p>
            : <ol className="qs-day" aria-label="Moves today">
              {day.moves.map(move => <li key={move.id} className={move.undone ? 'mv-move qs-undone' : 'mv-move'}>
                <span className="qs-day-main"><strong>{move.lines.map(line => line.text).join('、')}</strong>
                  <small>{clock(move.created_at)} · {move.by} · {move.from_name} → {move.to_name}{move.reason ? ` · ${move.reason}` : ''}{move.note ? ` · ${move.note}` : ''}</small>
                  {move.undone && <small className="qs-flag-undone">已撤销 · {move.undone.by}</small>}</span>
                {move.can_undo && <UndoMove move={move} done={() => setRefresh(n => n + 1)} />}
              </li>)}
            </ol>}
        </Panel>
      </div>
    </div>
  </div>
}

/** One question, answered by tapping a place. Picking the place the other question holds clears it there. */
function Chips({ label, places, value, onPick }: { label: string; places: Place[]; value: string; onPick: (code: string) => void }) {
  return <div className="mv-chips" role="radiogroup" aria-label={label}>
    <span>{label}</span>
    <div>{places.map(place => <button key={place.code} type="button" role="radio" aria-checked={value === place.code}
      className={place.code === 'new' || place.code === 'gone' ? 'mv-outside' : undefined}
      onClick={() => onPick(place.code)}>{place.name}</button>)}</div>
  </div>
}

/** 撤销: put a move back while the goods are still where it left them. */
function UndoMove({ move, done }: { move: Move; done: () => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const key = useRef('')
  async function undo() {
    key.current = key.current || crypto.randomUUID()
    setBusy(true); setError('')
    try {
      await client.post(`/stock-moves/${move.id}/undo`, {}, { headers: { 'Idempotency-Key': key.current }, timeout: 30000 })
      key.current = ''; done()
    } catch (cause) { if (!noAnswer(cause)) key.current = ''; setError(errorMessage(cause)) } finally { setBusy(false) }
  }
  return <>
    <Popconfirm title="撤销这次移货？" description="货会算回原来的地方。" okText="撤销" cancelText="不了" onConfirm={undo} okButtonProps={{ danger: true }}>
      <button type="button" className="qs-link" disabled={busy}>{busy ? '撤销中…' : '撤销'}</button>
    </Popconfirm>
    {error && <Problem>{error}</Problem>}
  </>
}
