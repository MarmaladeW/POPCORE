import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Alert, Button, Checkbox, Input, Skeleton } from 'antd'
import { CloseOutlined } from '@ant-design/icons'
import { getCountScope, type CountLocation } from '../../api/counting'
import { getCountMarks, markForCount, removeCountMark, type CountMark } from '../../api/countMarks'
import { placeName as countPlaceName } from './sectionsView'
import './countMarks.css'

// 待盘点 Count next time (owner, 2026-11-22): a manager marks a product at places of one store, saying why;
// the next count there lists it first. Used on 日报库存核对 notices and in a product's stock details.
type Failure = { _serverMessage?: string }
const messageOf = (cause: unknown, fallback: string) => (cause as Failure)?._serverMessage || fallback

// "11/22" in the device's time zone; the server keeps UTC.
export function markDay(utc: string) {
  const when = new Date(`${utc.replace(' ', 'T')}Z`)
  return Number.isNaN(when.getTime()) ? utc.slice(5, 10) : `${when.getMonth() + 1}/${when.getDate()}`
}

// What a mark says in one line: why, who and when.
export const markNote = (mark: Pick<CountMark, 'reason' | 'marked_by' | 'marked_at'>) =>
  `${mark.reason} · ${mark.marked_by} ${markDay(mark.marked_at)}`

// The amber dot and 待盘点 that the count screens use for "to count".
export function MarkFlag({ children }: { children?: ReactNode }) {
  return <span className="pc-mark-flag">待盘点{children ? <> {children}</> : null}</span>
}

// Open marks of one product: why, who and when once, then each place (a manager can take one off).
export function MarkedPlaces({ marks, nameOf = countPlaceName, onRemoved }: {
  marks: CountMark[]; nameOf?: (code: string) => string; onRemoved?: (marks: CountMark[]) => void
}) {
  const [busy, setBusy] = useState(0)
  const [error, setError] = useState('')
  if (!marks.length) return null
  const groups = new Map<string, CountMark[]>()
  marks.forEach(mark => groups.set(markNote(mark), [...(groups.get(markNote(mark)) ?? []), mark]))
  const remove = async (mark: CountMark) => {
    setBusy(mark.id)
    setError('')
    try { onRemoved?.((await removeCountMark(mark.id)).marks) } catch (cause) {
      setError(messageOf(cause, 'Could not take the mark off. Try again.'))
    } finally { setBusy(0) }
  }
  return <div className="pc-marked">
    <ul aria-label="待盘点 Count next time">
      {[...groups.entries()].map(([note, group]) => <li key={group[0].id}>
        <MarkFlag />{' '}
        {group.map(mark => <span key={mark.id} className={`pc-marked-place${onRemoved ? ' can-remove' : ''}`}>
          {nameOf(mark.place)}
          {onRemoved && <Button size="small" type="text" icon={<CloseOutlined />} loading={busy === mark.id}
            disabled={Boolean(busy) && busy !== mark.id} aria-label={`取消 Unmark ${nameOf(mark.place)}`}
            title="取消 Unmark" onClick={() => void remove(mark)} />}
        </span>)}{' '}
        <small>{note}</small>
      </li>)}
    </ul>
    {error && <Alert type="error" showIcon message={error} />}
  </div>
}

// Pick the places and say why. Places already marked are left out; whole sets are never counted on the floor.
export function MarkForm({ storeCode, productId, productName, sealedSet, preset, reason: presetReason, noticeId, marked,
  nameOf = countPlaceName, onMarked, onCancel }: {
  storeCode: string; productId: number; productName: string; sealedSet?: boolean; preset: string[]; reason?: string
  noticeId?: number; marked: string[]; nameOf?: (code: string) => string
  onMarked: (marks: CountMark[]) => void; onCancel: () => void
}) {
  const [places, setPlaces] = useState<CountLocation[] | null>(null)
  const [picked, setPicked] = useState<string[]>([])
  const [reason, setReason] = useState(presetReason ?? '')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  // A places answer that comes after the manager ticked something keeps what they ticked.
  const touched = useRef(false)
  useEffect(() => {
    const controller = new AbortController()
    getCountScope(storeCode, controller.signal).then(scope => {
      const open = scope.locations.filter(place => !marked.includes(place.code) && !(sealedSet && place.code === 'floor'))
      setPlaces(open)
      if (!touched.current) setPicked(preset.filter(code => open.some(place => place.code === code)))
    }).catch(cause => { if (!controller.signal.aborted) setError(messageOf(cause, 'Unable to load this store’s places.')) })
    return () => controller.abort()
  }, [storeCode]) // the places are read once per form
  const save = async () => {
    if (!picked.length) { setError('Pick where to count it.'); return }
    if (!reason.trim()) { setError('Say why it needs counting.'); return }
    setSaving(true)
    setError('')
    try {
      onMarked((await markForCount({ store_code: storeCode, product_id: productId, locations: picked, reason: reason.trim(),
        ...(noticeId ? { notice_id: noticeId } : {}) })).marks)
    } catch (cause) { setError(messageOf(cause, 'Could not mark it. Try again.')) } finally { setSaving(false) }
  }
  const id = `mark-${noticeId ?? 'product'}-${productId}`
  return <form className="pc-mark-form" aria-label={`下次盘点 Count ${productName} next time`}
    onSubmit={event => { event.preventDefault(); void save() }}>
    {!places ? (error ? null : <Skeleton active title={false} paragraph={{ rows: 2 }} />)
      : !places.length ? <p className="pc-count-quiet">Every place here is already marked.</p>
      : <>
        <fieldset>
          <legend>在哪里点 Where to count</legend>
          <Checkbox.Group value={picked} onChange={values => { touched.current = true; setPicked(values as string[]) }}
            options={places.map(place => ({ value: place.code, label: nameOf(place.code) || place.name }))} />
        </fieldset>
        <label htmlFor={`${id}-reason`}>为什么 Why</label>
        <Input id={`${id}-reason`} value={reason} maxLength={200} onChange={event => setReason(event.target.value)}
          placeholder="例如 架上和库存对不上 Shelf and stock don’t match" />
      </>}
    {error && <Alert type="error" showIcon message={error} />}
    <div className="pc-mark-form-actions">
      <Button onClick={onCancel}>取消 Cancel</Button>
      {places && places.length > 0 && <Button type="primary" htmlType="submit" loading={saving}>标记 Mark</Button>}
    </div>
  </form>
}

// A product's stock details (库存): its open marks at this store, and a manager's 下次盘点 button.
export function ProductCountMarks({ storeCode, productId, productName, sealedSet }: {
  storeCode: string; productId: number; productName: string; sealedSet?: boolean
}) {
  const [marks, setMarks] = useState<CountMark[] | null>(null)
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    getCountMarks(storeCode, productId, controller.signal).then(setMarks)
      .catch(cause => { if (!controller.signal.aborted) setError(messageOf(cause, 'Unable to load what is marked to count.')) })
    return () => controller.abort()
  }, [storeCode, productId])
  if (error) return <Alert type="error" showIcon message={error} />
  if (!marks) return null
  return <section className="pc-product-marks" aria-label="待盘点 Count next time">
    <MarkedPlaces marks={marks} onRemoved={setMarks} />
    {open ? <MarkForm storeCode={storeCode} productId={productId} productName={productName} sealedSet={sealedSet} preset={[]}
        marked={marks.map(mark => mark.place)} onCancel={() => setOpen(false)}
        onMarked={next => { setMarks(next); setOpen(false) }} />
      : <Button onClick={() => setOpen(true)}>下次盘点 Count next time</Button>}
  </section>
}
