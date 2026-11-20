import { Alert, Button, Image, Input, Popconfirm, Skeleton, message } from 'antd'
import { useEffect, useRef, useState } from 'react'
import {
  addWatchPhoto, addWatchRecord, deleteWatchRecord, getWatchList, getWatchPhoto, removeWatchPhoto, reopenWatchRecord,
  resolveWatchRecord, updateWatchRecord, type WatchListPage, type WatchPhoto, type WatchRecord,
} from '../../api/watchList'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { Panel } from '../../components/pc/Panel'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { useAppStore } from '../../store'
import './watchList.css'

// 可疑人员 Watch list (owner, 2026-11-19: "upload images of stealers, so everyone can recognize the people that stole from
// the store, we can write own information about the stealer, and upload image of them, and we can mark if it is resolved
// or not, the stealer information also something showing in the store computer"). Everyone signed in reads it, the
// store computer's viewer account included; managers and admins add, edit, resolve and delete on this same page.

const REFRESH_MS = 5 * 60_000  // the store computer keeps the page open
const MAX_BYTES = 10 * 1024 * 1024
const TYPES = ['image/jpeg', 'image/png', 'image/webp']
const errorText = (cause:unknown, fallback:string) => (cause as { _serverMessage?:string })?._serverMessage || fallback
// SQLite keeps UTC times; the stores' day is Toronto's.
const torontoDay = (utc:string|null) => utc ? new Date(`${utc.replace(' ', 'T')}Z`).toLocaleDateString('en-CA', { timeZone:'America/Toronto' }) : ''
const byName = (name:string|null) => name ? ` · ${name}` : ''

export default function WatchListPage() {
  const phone = useIsPhone()
  const [refresh, setRefresh] = useState(0)
  const [result, setResult] = useState<{ data?:WatchListPage; error?:string }>()
  const [adding, setAdding] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    getWatchList(controller.signal)
      .then(data => { if (!controller.signal.aborted) setResult({ data }) })
      // A failed refresh keeps the list on screen.
      .catch(cause => { if (!controller.signal.aborted) setResult(current => current?.data ? current : { error:errorText(cause, 'The watch list did not load.') }) })
    return () => controller.abort()
  }, [refresh])
  useEffect(() => {
    const timer = window.setInterval(() => setRefresh(value => value + 1), REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [])
  const reload = () => setRefresh(value => value + 1)
  const data = result?.data
  const open = data?.records.filter(record => record.status === 'open') ?? []
  const resolved = data?.records.filter(record => record.status === 'resolved') ?? []
  const add = data?.can_edit && !adding ? <Button type="primary" icon={<Icon name="plus" size={16} />} onClick={() => setAdding(true)}>
    {phone ? <>添加<span className="pc-sr"> Add a record</span></> : '添加记录 Add a record'}
  </Button> : undefined

  return <div className="pc-watch">
    <PageHeader title="可疑人员" sub="Watch list" actions={add} phoneAction={add} />
    <p className="pc-watch-privacy"><Icon name="eye" size={16} />
      <span>只用于在店里认人，不要在 POPCORE 以外分享。<small>Only for recognising people in the stores; do not share outside POPCORE.</small></span></p>
    {!data ? (result?.error ? <Alert type="error" showIcon message={result.error} action={<Button onClick={reload}>Retry</Button>} />
      : <Skeleton active paragraph={{ rows:4 }} />)
      : <>
        {adding && <RecordForm page={data} onSaved={() => { setAdding(false); reload() }} onCancel={() => setAdding(false)} />}
        <Panel zh="未解决" en="Open" count={open.length} tone={open.length ? 'out' : 'ok'} label="未解决 Open" className="pc-watch-panel">
          {open.length ? <div className="pc-watch-list">{open.map(record => <RecordCard key={record.id} record={record} page={data} onChanged={reload} />)}</div>
            : <p className="pc-quiet"><strong>没有未解决的记录</strong>No open records.</p>}
        </Panel>
        {resolved.length > 0 && <details className="pc-watch-resolved">
          <summary>
            <span className="pc-watch-resolved-title">已解决 <small>Resolved</small></span>
            <span className="pc-panel-count"><i className="pc-dot pc-dot-ok" aria-hidden="true" />{resolved.length}<Icon name="down" size={14} className="pc-watch-chevron" /></span>
          </summary>
          <div className="pc-watch-list">{resolved.map(record => <RecordCard key={record.id} record={record} page={data} onChanged={reload} />)}</div>
        </details>}
      </>}
  </div>
}

function usePhotoUrl(photoId:number) {
  const [url, setUrl] = useState<string>()
  useEffect(() => {
    const controller = new AbortController()
    let objectUrl = ''
    getWatchPhoto(photoId, controller.signal)
      .then(blob => { if (!controller.signal.aborted) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl) } })
      .catch(() => { if (!controller.signal.aborted) setUrl('failed') })
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [photoId])
  return url
}

function Photo({ photo, alt, preview = true }:{ photo:WatchPhoto; alt:string; preview?:boolean }) {
  const url = usePhotoUrl(photo.id)
  if (url === 'failed') return <span className="pc-watch-photo is-missing" role="img" aria-label={`${alt}: unavailable`}>照片打不开<small>Photo unavailable</small></span>
  if (!url) return <span className="pc-watch-photo is-loading" aria-hidden="true" />
  return <Image src={url} alt={alt} rootClassName="pc-watch-photo" preview={preview ? { mask:<span className="pc-watch-zoom">放大 View</span> } : false} />
}

// Thumbnails; a click opens one large, with the record's other photos a swipe or arrow away.
function Photos({ photos, label }:{ photos:WatchPhoto[]; label:string }) {
  return <Image.PreviewGroup>
    <div className="pc-watch-photos">
      {photos.map((photo, index) => <Photo key={photo.id} photo={photo} alt={`${label} · 照片 ${index + 1} Photo ${index + 1}`} />)}
    </div>
  </Image.PreviewGroup>
}

function RecordCard({ record, page, onChanged }:{ record:WatchRecord; page:WatchListPage; onChanged:()=>void }) {
  const [mode, setMode] = useState<'view'|'edit'|'resolve'>('view')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState('')
  const [problem, setProblem] = useState('')
  const open = record.status === 'open'
  const where = `${record.seen_on}${record.store_code ? ` · ${record.store_code}` : ''}`
  async function act(name:string, work:()=>Promise<unknown>, done:string) {
    setBusy(name); setProblem('')
    try { await work(); message.success(done); setMode('view'); setNote(''); onChanged() }
    catch (cause) { setProblem(errorText(cause, 'Not saved. Check the connection and try again.')) }
    finally { setBusy('') }
  }
  if (mode === 'edit') return <RecordForm page={page} record={record} onSaved={() => { setMode('view'); onChanged() }} onCancel={() => setMode('view')} />
  return <article className={open ? 'pc-watch-card' : 'pc-watch-card is-resolved'} aria-label={`可疑人员 ${where}`}>
    <Photos photos={record.photos} label={`可疑人员 ${where}`} />
    <div className="pc-watch-body">
      <p className="pc-watch-status">
        <span><i className={open ? 'pc-dot pc-dot-out' : 'pc-dot pc-dot-ok'} aria-hidden="true" />{open ? '未解决 Open' : '已解决 Resolved'}</span>
        <span>见到 Seen {record.seen_on}</span>
        <span>{record.store_code ? `${record.store_code} ${record.store_name ?? ''}`.trim() : '门店未填 No store given'}</span>
      </p>
      <p className="pc-watch-info">{record.info}</p>
      {!open && <p className="pc-watch-resolution">已解决 Resolved {torontoDay(record.resolved_at)}{byName(record.resolved_by_name)}
        {record.resolve_note && <span>{record.resolve_note}</span>}</p>}
      <p className="pc-watch-meta">添加 Added {torontoDay(record.created_at)}{byName(record.created_by_name)}
        {record.updated_at && <> · 修改 Edited {torontoDay(record.updated_at)}{byName(record.updated_by_name)}</>}</p>
      {problem && <Alert type="error" showIcon message={problem} />}
      {page.can_edit && (mode === 'resolve' ? <div className="pc-watch-resolve">
        <label>
          <span>怎么解决的 <small>How it was resolved, optional</small></span>
          <Input.TextArea rows={2} maxLength={500} value={note} disabled={!!busy} aria-label="怎么解决的 How it was resolved"
            onChange={event => setNote(event.target.value)} placeholder="e.g. paid for the items, police report 4471" />
        </label>
        <div className="pc-watch-actions">
          <Button type="primary" loading={busy === 'resolve'} onClick={() => void act('resolve', () => resolveWatchRecord(record.id, note.trim()), '已解决 Marked resolved')}>
            确认已解决 <small>Mark resolved</small></Button>
          <Button disabled={!!busy} onClick={() => { setMode('view'); setNote('') }}>取消 <small>Cancel</small></Button>
        </div>
      </div> : <div className="pc-watch-actions">
        {open ? <Button disabled={!!busy} onClick={() => setMode('resolve')}><Icon name="check" size={16} />标记已解决 <small>Mark resolved</small></Button>
          : <Popconfirm title="Open this record again?" description="It moves back to 未解决 Open." okText="重新打开 Reopen" cancelText="Keep"
              onConfirm={() => act('reopen', () => reopenWatchRecord(record.id), '已重新打开 Reopened')}>
            <Button loading={busy === 'reopen'} disabled={!!busy}>重新打开 <small>Reopen</small></Button>
          </Popconfirm>}
        <Button disabled={!!busy} onClick={() => setMode('edit')}>编辑 <small>Edit</small></Button>
        <Popconfirm title="Delete this record and its photos?" description="They are removed for good; this cannot be undone." okText="删除 Delete"
          cancelText="Keep" okButtonProps={{ danger:true }} onConfirm={() => act('delete', () => deleteWatchRecord(record.id), '已删除 Deleted')}>
          <Button danger loading={busy === 'delete'} disabled={!!busy}>删除 <small>Delete</small></Button>
        </Popconfirm>
      </div>)}
    </div>
  </article>
}

type Picked = { key:number; file:File; url:string }

// Add a record, or edit one: its information, store, date seen and photos. Photos upload one per request after the
// record is saved, so one slow or refused photo never loses the others.
function RecordForm({ page, record, onSaved, onCancel }:{ page:WatchListPage; record?:WatchRecord; onSaved:()=>void; onCancel:()=>void }) {
  const selected = useAppStore(state => state.selectedStore?.code)
  const [info, setInfo] = useState(record?.info ?? '')
  const [storeCode, setStoreCode] = useState<string|null>(record ? record.store_code
    : page.stores.some(store => store.code === selected) ? selected! : null)
  const [seenOn, setSeenOn] = useState(record?.seen_on ?? page.today)
  const [removed, setRemoved] = useState<number[]>([])
  const [picked, setPicked] = useState<Picked[]>([])
  const [problem, setProblem] = useState('')
  const [saving, setSaving] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const nextKey = useRef(0)
  const urls = useRef<string[]>([])
  useEffect(() => () => urls.current.forEach(url => URL.revokeObjectURL(url)), [])

  const kept = (record?.photos.length ?? 0) - removed.length
  const total = kept + picked.length
  const valid = !!info.trim() && !!seenOn && seenOn <= page.today && total >= 1 && total <= page.max_photos

  function choose(list:FileList|null) {
    const files = Array.from(list ?? [])
    const refused = files.filter(file => file.size > MAX_BYTES || (file.type !== '' && !TYPES.includes(file.type)))
    const room = Math.max(0, page.max_photos - total)
    const taken = files.filter(file => !refused.includes(file)).slice(0, room)
    const notes = refused.map(file => `${file.name}: ${file.size > MAX_BYTES ? 'larger than 10 MB' : 'not a JPEG, PNG or WebP photo'}`)
    if (files.length - refused.length > taken.length) notes.push(`A record holds at most ${page.max_photos} photos`)
    setProblem(notes.join(' · '))
    setPicked(current => [...current, ...taken.map(file => {
      const url = URL.createObjectURL(file)
      urls.current.push(url)
      return { key:nextKey.current++, file, url }
    })])
  }

  async function save() {
    if (!valid || saving) return
    setProblem('')
    const fields = { info:info.trim(), store_code:storeCode, seen_on:seenOn }
    const failed:string[] = []
    let recordId = record?.id
    let uploads = picked
    try {
      setSaving('Saving…')
      if (!record) {
        recordId = (await addWatchRecord(fields, picked[0].file)).id
        uploads = picked.slice(1)
      } else if (fields.info !== record.info || fields.store_code !== record.store_code || fields.seen_on !== record.seen_on) {
        await updateWatchRecord(record.id, fields)
      }
    } catch (cause) {
      setSaving(''); setProblem(errorText(cause, 'Not saved. Check the connection and try again.')); return
    }
    for (const [index, item] of uploads.entries()) {
      setSaving(`Uploading photo ${index + 1} of ${uploads.length}…`)
      try { await addWatchPhoto(recordId!, item.file) }
      catch (cause) { failed.push(`${item.file.name}: ${errorText(cause, 'did not upload')}`) }
    }
    for (const photoId of removed) {
      try { await removeWatchPhoto(recordId!, photoId) }
      catch (cause) { failed.push(errorText(cause, 'A photo was not removed')) }
    }
    setSaving('')
    if (failed.length) message.warning({ content:`Saved, but not every photo: ${failed.join(' · ')}. Try them again from 编辑 Edit.`, duration:10 })
    else message.success(record ? '已保存 Saved' : '已添加 Added')
    onSaved()
  }

  const stores = [{ code:null, zh:'不填', en:'Not given' }, ...page.stores.map(store => ({ code:store.code, zh:store.code, en:store.name }))]
  const busy = !!saving
  return <section className="pc-watch-form" aria-label={record ? '编辑记录 Edit record' : '添加记录 Add a record'}>
    <h2 className="pc-watch-form-title">{record ? <>编辑记录 <small>Edit record</small></> : <>添加记录 <small>Add a record</small></>}</h2>
    <fieldset className="pc-watch-field" disabled={busy}>
      <legend>照片 <small>Photos · JPEG, PNG or WebP, up to 10 MB each, at most {page.max_photos}</small></legend>
      <div className="pc-watch-photos pc-watch-photos-edit">
        {record?.photos.map((photo, index) => {
          const gone = removed.includes(photo.id)
          return <div key={photo.id} className={gone ? 'pc-watch-pick is-removed' : 'pc-watch-pick'}>
            <Photo photo={photo} alt={`照片 ${index + 1} Photo ${index + 1}`} preview={false} />
            <button type="button" aria-pressed={gone} onClick={() => setRemoved(current => gone ? current.filter(id => id !== photo.id) : [...current, photo.id])}>
              {gone ? '撤销 Keep' : '移除 Remove'}</button>
          </div>
        })}
        {picked.map((item, index) => <div key={item.key} className="pc-watch-pick is-new">
          <img src={item.url} alt={`新照片 ${index + 1} New photo ${index + 1}`} />
          <button type="button" onClick={() => setPicked(current => current.filter(other => other.key !== item.key))}>移除 Remove</button>
        </div>)}
        {total < page.max_photos && <button type="button" className="pc-watch-pick-add" onClick={() => input.current?.click()}>
          <Icon name="camera" size={20} /><span>添加照片</span><small>Add photos</small></button>}
      </div>
      <input ref={input} className="pc-watch-file" type="file" multiple accept={TYPES.join(',')} aria-label="添加照片 Add photos"
        onChange={event => { choose(event.target.files); event.target.value = '' }} />
      {total < 1 && <p className="pc-watch-hint">A record needs at least one photo.</p>}
    </fieldset>
    <label className="pc-watch-field">
      <span>情况 <small>Looks, clothes, what was taken, when, anything else</small></span>
      <Input.TextArea rows={4} maxLength={2000} value={info} disabled={busy} aria-label="情况 Information"
        onChange={event => setInfo(event.target.value)} placeholder="e.g. tall, grey hoodie and black cap; took two Labubu boxes from the front table around 3 pm" />
    </label>
    <div className="pc-watch-row">
      <fieldset className="pc-watch-field" disabled={busy}>
        <legend>门店 <small>Store</small></legend>
        <div className="pc-watch-choices">{stores.map(store => <button key={store.code ?? 'none'} type="button" aria-pressed={storeCode === store.code}
          onClick={() => setStoreCode(store.code)}>{store.zh} <small>{store.en}</small></button>)}</div>
      </fieldset>
      <label className="pc-watch-field pc-watch-date">
        <span>见到日期 <small>Date seen</small></span>
        <Input type="date" value={seenOn} max={page.today} disabled={busy} aria-label="见到日期 Date seen" onChange={event => setSeenOn(event.target.value)} />
      </label>
    </div>
    {problem && <Alert type="error" showIcon message={problem} />}
    <div className="pc-watch-actions">
      <Button type="primary" disabled={!valid || busy} loading={busy} onClick={() => void save()}>{record ? '保存 Save' : '添加 Add'}</Button>
      <Button disabled={busy} onClick={onCancel}>取消 <small>Cancel</small></Button>
      {saving && <span className="pc-watch-progress" role="status">{saving}</span>}
    </div>
  </section>
}
