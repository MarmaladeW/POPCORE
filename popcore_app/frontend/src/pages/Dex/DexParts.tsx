import { useEffect, useRef, useState } from 'react'
import { AutoComplete, Button, Input, Modal, message } from 'antd'
import { Link, useNavigate } from 'react-router-dom'

import { deleteEntry, getRemoval, reorderEntries, searchEntries, type DexEntry, type DexKind, type DexSearchHit } from '../../api/dex'
import { KIND_LABEL, kindText } from '../../lib/dexFields'

export const errorText = (cause: any, fallback = 'That did not save. Try again.') => cause?._serverMessage || fallback

// The entry's picture from the website, by address (no upload in step 1); a letter when it has none.
export function DexThumb({ entry, size = 40 }: { entry: Pick<DexEntry, 'name' | 'thumb_url' | 'picture_url'>; size?: number }) {
  const src = entry.thumb_url || entry.picture_url
  const [broken, setBroken] = useState(false)
  return <span className="pc-dex-thumb" style={{ width: size, height: size }} aria-hidden="true">
    {src && !broken ? <img src={src} alt="" loading="lazy" onError={() => setBroken(true)} />
      : <span>{entry.name.trim().charAt(0).toUpperCase() || '·'}</span>}
  </span>
}

// One child row: thumbnail, name, how many sit under it, the linked POPCORE ID; managers move it up or down.
export function DexRow({ entry, onUp, onDown, busy }: {
  entry: DexEntry; onUp?: () => void; onDown?: () => void; busy?: boolean
}) {
  const meta = [entry.child_count ? `${entry.child_count} 项 inside` : '',
    entry.product_sku ? `POPCORE ${entry.product_sku}` : ''].filter(Boolean).join(' · ')
  return <div className="pc-row pc-dex-row">
    <DexThumb entry={entry} />
    <Link className="pc-row-main" to={`/dex/${entry.id}`}><strong>{entry.name}</strong>{meta && <small>{meta}</small>}</Link>
    {(onUp || onDown) && <span className="pc-dex-move">
      <Button size="small" aria-label={`Move ${entry.name} up`} disabled={!onUp || busy} onClick={onUp}>↑</Button>
      <Button size="small" aria-label={`Move ${entry.name} down`} disabled={!onDown || busy} onClick={onDown}>↓</Button>
    </span>}
  </div>
}

// A group of siblings in order, with move up / down for managers (one reorder call per move).
export function DexRows({ rows, parentId, canWrite, onChanged }: {
  rows: DexEntry[]; parentId: number | null; canWrite: boolean; onChanged: (rows: DexEntry[]) => void
}) {
  const [busy, setBusy] = useState(false)
  async function move(index: number, by: number) {
    const ids = rows.map(row => row.id)
    ;[ids[index], ids[index + by]] = [ids[index + by], ids[index]]
    setBusy(true)
    try {
      await reorderEntries(parentId, ids)
      onChanged(ids.map(id => rows.find(row => row.id === id)!))
    } catch (cause) {
      message.error(errorText(cause))
    } finally {
      setBusy(false)
    }
  }
  return <>{rows.map((row, index) => <DexRow key={row.id} entry={row} busy={busy}
    onUp={canWrite && index > 0 ? () => move(index, -1) : undefined}
    onDown={canWrite && index < rows.length - 1 ? () => move(index, 1) : undefined} />)}</>
}

// Search the book by name and jump to an entry.
export function DexSearch() {
  const navigate = useNavigate()
  const [text, setText] = useState('')
  const [hits, setHits] = useState<DexSearchHit[]>([])
  const asked = useRef(0)
  useEffect(() => {
    const query = text.trim()
    if (!query) { setHits([]); return }
    const ask = ++asked.current
    const timer = window.setTimeout(() => {
      searchEntries(query).then(found => { if (ask === asked.current) setHits(found) }).catch(() => {})
    }, 200)
    return () => window.clearTimeout(timer)
  }, [text])
  return <AutoComplete className="pc-dex-search" value={text} onChange={setText} popupMatchSelectWidth
    onSelect={(value: string) => { setText(''); navigate(`/dex/${value}`) }}
    options={hits.map(hit => ({
      value: String(hit.id),
      label: <span className="pc-dex-hit"><strong>{hit.name}</strong>
        <small>{KIND_LABEL[hit.kind].zh} · {hit.path.map(step => step.name).join(' › ') || '图鉴'}</small></span>,
    }))}
    notFoundContent={text.trim() ? '没有找到 Nothing found' : null}>
    <Input.Search placeholder="搜索图鉴 Search the book" aria-label="Search the book" allowClear />
  </AutoComplete>
}

// Ask what a delete would remove, then delete; refuse plainly when it cannot be removed.
export async function confirmDelete(entry: Pick<DexEntry, 'id' | 'name' | 'kind'>, onDone: () => void) {
  let plan
  try {
    plan = await getRemoval(entry.id)
  } catch (cause) {
    message.error(errorText(cause, 'Unable to check what this removes.'))
    return
  }
  if (plan.linked.length || plan.used_by.length) {
    Modal.warning({
      title: `不能删除 Cannot delete “${entry.name}”`,
      content: plan.linked.length
        ? `${plan.linked.map(row => `${row.name} (${row.sku})`).join(', ')} ${plan.linked.length > 1 ? 'are' : 'is'} linked to a POPCORE item. Unlink first.`
        : `${plan.used_by.map(row => row.name).join(', ')} name${plan.used_by.length > 1 ? '' : 's'} it as partner or character. Change that first.`,
    })
    return
  }
  // The counts include the entry itself.
  const others = (Object.entries(plan.counts) as [DexKind, number][])
    .map(([kind, count]) => [kind, kind === entry.kind ? count - 1 : count] as const)
    .filter(([, count]) => count > 0).map(([kind, count]) => `${count} ${kindText(kind)}`)
  Modal.confirm({
    title: `删除 Delete “${entry.name}”?`,
    content: others.length ? `This also removes everything under it: ${others.join(', ')}.` : 'Nothing sits under it.',
    okText: '删除 Delete', okButtonProps: { danger: true }, cancelText: '取消 Cancel',
    onOk: async () => {
      try {
        await deleteEntry(entry.id)
        message.success(`Deleted ${entry.name}`)
        onDone()
      } catch (cause) {
        message.error(errorText(cause))
        throw cause
      }
    },
  })
}
