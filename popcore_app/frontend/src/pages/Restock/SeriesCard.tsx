import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Skeleton, message } from 'antd'
import Icon from '../../components/pc/Icon'
import InventoryPhoto from '../Stock/InventoryPhoto'
import QtyStepper from './QtyStepper'
import { getSeries, partLabel, saveSeries, type SeriesCardData, type SeriesItem } from './series'

const UNITS: Record<string, string> = { set: '端', box: '盒', piece: '个' }
type Failure = { _serverMessage?: string }
const messageOf = (cause: unknown, fallback: string) => (cause as Failure)?._serverMessage || fallback

// 楼上 is what the picker takes from; 楼面 is what is out already. Without 库存查看 only in / out.
function StockLine({ item, asked }: { item: SeriesItem; asked: number }) {
  const unit = item.stock_unit ? UNITS[item.stock_unit] : ''
  if (item.stock_hidden) {
    return <span className="pc-rq-stock">
      <span className={`pc-rq-status${item.out_of_stock ? ' is-out' : ''}`}>
        <i className={`pc-dot pc-dot-${item.out_of_stock ? 'out' : 'ok'}`} aria-hidden="true" />
        {item.out_of_stock ? '楼上缺货 Out upstairs' : '楼上有货 In stock'}
      </span>
    </span>
  }
  const upstairs = item.upstairs_qty ?? 0
  const short = asked > upstairs
  return <span className="pc-rq-stock">
    <span className={`pc-rq-kraft${upstairs <= 0 ? ' is-empty' : ''}`}>楼上 <b>{upstairs}</b>{unit}</span>
    {item.role !== 'set' && <span>楼面 {item.instore_qty ?? 0}</span>}
    {short && <span className="pc-rq-status is-warn"><i className="pc-dot pc-dot-warn" aria-hidden="true" />
      {upstairs > 0 ? `只有 ${upstairs}` : '楼上没有'}</span>}
  </span>
}

export default function SeriesCard({ sessionId, productId, onClose }: {
  sessionId: number; productId: number; onClose: (saved: boolean) => void
}) {
  const [card, setCard] = useState<SeriesCardData | null>(null)
  const [values, setValues] = useState<Record<number, number>>({})
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [saving, setSaving] = useState(false)
  const top = useRef<HTMLElement>(null)

  useEffect(() => {
    const controller = new AbortController()
    setError('')
    getSeries(sessionId, productId, controller.signal).then(data => {
      setCard(data)
      setValues(Object.fromEntries(data.items.map(item => [item.product_id, item.requested_qty])))
    }).catch(cause => { if (!controller.signal.aborted) setError(messageOf(cause, 'Unable to load this series.')) })
    return () => controller.abort()
  }, [sessionId, productId, reload])

  // The card opens where the list was: bring its head into view on a phone.
  useEffect(() => { if (card) top.current?.scrollIntoView({ block: 'start' }) }, [Boolean(card)])

  if (!card) {
    return <section className="pc-rq-card" aria-busy={!error}>
      {error ? <Alert type="error" showIcon message={error} action={<Button onClick={() => setReload(n => n + 1)}>重试 Retry</Button>} />
        : <Skeleton active title={false} paragraph={{ rows: 5 }} />}
    </section>
  }

  const value = (item: SeriesItem) => values[item.product_id] ?? 0
  // Only what changed is sent, so another phone's lines on this series are kept.
  const entries = card.items.filter(item => value(item) !== item.requested_qty)
    .map(item => ({ product_id: item.product_id, requested_qty: value(item) }))
  const before = card.items.some(item => item.requested_qty > 0)
  const after = card.items.some(item => value(item) > 0)
  const sum = (role: string) => card.items.filter(item => item.role === role).reduce((total, item) => total + value(item), 0)
  const sets = card.items.filter(item => item.role === 'set')
  const heads = card.items.filter(item => item.role === 'box' || item.role === 'item')
  const designs = card.items.filter(item => item.role === 'design')
  const totals = [
    sets.length > 0 && sum('set') > 0 && `${sum('set')} 端`,
    heads.map(item => value(item) > 0 && `${value(item)} ${item.stock_unit ? UNITS[item.stock_unit] : ''}`.trim()).find(Boolean),
    designs.length > 0 && sum('design') > 0 && `确认款 ${sum('design')} ${UNITS[designs[0].stock_unit || 'box'] || ''}`.trim(),
  ].filter(Boolean)

  const save = async () => {
    if (!entries.length) { onClose(false); return }
    setSaving(true)
    setError('')
    try {
      await saveSeries(sessionId, card.product_id, entries)
      message.success(after ? `已加入补货单 · ${card.title}` : `已移出补货单 · ${card.title}`)
      onClose(true)
    } catch (cause) {
      setError(messageOf(cause, 'Could not save. Check the connection and press it again.'))
      setSaving(false)
    }
  }

  const row = (item: SeriesItem, label: string, note?: string) => <li key={item.product_id} className="pc-rq-line">
    <span className="pc-rq-line-text">
      <span className="pc-rq-line-name">{label}</span>
      {note && <small>{note}</small>}
      <StockLine item={item} asked={value(item)} />
    </span>
    <QtyStepper value={value(item)} label={label} unit={item.stock_unit ? UNITS[item.stock_unit] : undefined}
      onChange={next => setValues(current => ({ ...current, [item.product_id]: next }))} />
  </li>

  return <section ref={top} className="pc-rq-card" aria-label={`补货 ${card.title}`}>
    <header className="pc-rq-card-head">
      <button type="button" className="pc-rq-back" onClick={() => onClose(false)}><Icon name="back" size={14} />返回 Back</button>
      <div className="pc-rq-card-title">
        <InventoryPhoto filename={card.image} name={card.title} />
        <span>
          <h3>{card.title}</h3>
          {(card.sku || card.ip_series) && <small>{[card.sku, card.ip_series].filter(Boolean).join(' · ')}</small>}
        </span>
      </div>
    </header>

    <ul className="pc-rq-lines">
      {sets.map(item => row(item, '整端 Whole sets', item.boxes_per_set ? `每端 ${item.boxes_per_set} 盒` : undefined))}
      {card.no_whole_set && <li className="pc-rq-line is-quiet">
        <span className="pc-rq-line-text">
          <span className="pc-rq-line-name">整端 Whole sets</span>
          <small>楼上还没有整端记录 · No whole sets recorded. 盘点 upstairs adds them.</small>
        </span>
      </li>}
      {heads.map(item => row(item, partLabel(item.role)))}
    </ul>

    {designs.length > 0 && <>
      <h4 className="pc-rq-sub">确认款 Designs <small>{designs.length} 款</small></h4>
      <ul className="pc-rq-lines">{designs.map(item => row(item, partLabel('design', item.design_name, item.name)))}</ul>
    </>}

    {error && <Alert type="error" showIcon message={error} />}
    <footer className="pc-rq-bar">
      <span className="pc-rq-bar-text" aria-live="polite">
        {totals.length ? <><strong>{totals.join(' + ')}</strong><small>{before ? '保存后更新补货单' : '加入后可再修改'}</small></>
          : <><strong>{before ? '全部为 0' : '还没选数量'}</strong><small>{before ? 'Saving takes this series off the sheet' : 'Choose how many to ask for'}</small></>}
      </span>
      <Button type="primary" size="large" loading={saving} disabled={!before && !after} danger={before && !after}
        onClick={() => void save()}>
        {before ? (after ? '保存 Save' : '移出 Remove') : '加入 Add'}
      </Button>
    </footer>
  </section>
}
