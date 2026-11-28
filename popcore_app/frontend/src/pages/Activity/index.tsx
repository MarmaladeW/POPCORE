import { Alert, Button, Select, Skeleton } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { getActivity, type ActivityEntry, type ActivityPage } from '../../api/activity'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { activityTime, activityWho, shiftDay } from '../../lib/activityView'
import { torontoDate } from '../Dashboard/todayPresentation'
import { useAppStore } from '../../store'
import ActivityDetailSheet from './ActivityDetail'
import './activity.css'

// 动态 Activity (owner, 2026-11-20: "in WeChat it is super easy to check who did what"): every change anyone made
// at the store, newest first, one line each: time, who, what. Tap a line to see exactly what it changed
// (owner, 2026-11-28); the sheet links on to the record.
export function ActivityRow({ entry, showStore, onOpen }: { entry: ActivityEntry; showStore?: boolean; onOpen?: (id: number) => void }) {
  const body = <>
    <time className="pc-act-time" dateTime={entry.at}>{activityTime(entry)}</time>
    <span className="pc-act-main">
      <strong>{activityWho(entry)}{showStore && entry.store_code && <span className="pc-act-store">{entry.store_code}</span>}</strong>
      <span>{entry.summary}</span>
      {entry.detail && <small>{entry.detail}</small>}
    </span>
  </>
  if (onOpen) return <button type="button" className="pc-act-row" aria-haspopup="dialog" onClick={() => onOpen(entry.id)}>{body}</button>
  return entry.link ? <Link className="pc-act-row" to={entry.link}>{body}</Link> : <div className="pc-act-row">{body}</div>
}

export default function ActivityPage() {
  const store = useAppStore(state => state.selectedStore)
  const code = store?.code || 'ALL'
  const [day, setDay] = useState(torontoDate())
  const [person, setPerson] = useState<string>()
  const [kind, setKind] = useState<string>()
  const [page, setPage] = useState<ActivityPage>()
  const [older, setOlder] = useState<ActivityEntry[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [reload, setReload] = useState(0)
  // The open line is in the address, so a phone's back closes the sheet.
  const [params, setParams] = useSearchParams()
  const opened = Number(params.get('entry')) || null
  const open = (id: number) => setParams(current => { const next = new URLSearchParams(current); next.set('entry', String(id)); return next })
  const close = () => setParams(current => { const next = new URLSearchParams(current); next.delete('entry'); return next }, { replace: true })

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError(''); setOlder([])
    getActivity({ store_code: code, date: day, person, kind }, controller.signal)
      .then(setPage)
      .catch(cause => { if (!controller.signal.aborted) setError(cause?._serverMessage || cause?.response?.data?.error || '动态没有加载 Activity did not load') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [code, day, person, kind, reload])

  const entries = useMemo(() => [...(page?.entries ?? []), ...older], [page, older])
  const lastId = older.length ? older[older.length - 1].id : page?.next_before_id
  async function more() {
    if (!page) return
    const next = await getActivity({ store_code: code, date: day, person, kind, before_id: older.length ? older[older.length - 1].id : (page.entries[page.entries.length - 1]?.id) })
    setOlder(rows => [...rows, ...next.entries])
    if (!next.next_before_id) setPage(current => current && { ...current, next_before_id: null })
  }
  const today = torontoDate()

  return <div className="pc-act">
    <PageHeader title="动态" sub={`Activity · ${store?.name || 'All stores'}`}
      actions={<Button icon={<Icon name="refresh" size={18} />} aria-label="Refresh activity" onClick={() => setReload(n => n + 1)}>刷新</Button>} />
    <div className="pc-act-filters">
      <span className="pc-act-day">
        <Button aria-label="Previous day" onClick={() => setDay(shiftDay(day, -1))}>‹</Button>
        <input type="date" aria-label="Day" value={day} max={today} onChange={event => event.target.value && setDay(event.target.value)} />
        <Button aria-label="Next day" disabled={day >= today} onClick={() => setDay(shiftDay(day, 1))}>›</Button>
        {day !== today && <Button type="link" onClick={() => setDay(today)}>今天 Today</Button>}
      </span>
      <Select allowClear placeholder="所有人 Everyone" aria-label="Person" value={person} onChange={setPerson} style={{ minWidth: 180 }}
        options={(page?.people ?? []).map(entry => ({ value: entry.sub, label: entry.name || '没有员工资料 No staff profile' }))} />
    </div>
    <div className="pc-act-chips" role="group" aria-label="Kind">
      <button type="button" aria-pressed={!kind} onClick={() => setKind(undefined)}>全部 All</button>
      {(page?.kinds ?? []).map(([key, label]) =>
        <button key={key} type="button" aria-pressed={kind === key} onClick={() => setKind(kind === key ? undefined : key)}>{label}</button>)}
    </div>
    {error && <Alert type="error" showIcon message={error} action={<Button onClick={() => setReload(n => n + 1)}>重试 Retry</Button>} />}
    {loading ? <Skeleton active paragraph={{ rows: 6 }} />
      : !entries.length ? <p className="pc-quiet"><strong>这一天还没有动态</strong> Nothing recorded on this day{person || kind ? ' for this filter' : ''}.</p>
      : <div className="pc-act-list" aria-label="Activity">{entries.map(entry => <ActivityRow key={entry.id} entry={entry} showStore={code === 'ALL'} onOpen={open} />)}</div>}
    {!loading && lastId && page?.next_before_id && <Button className="pc-act-more" onClick={more}>更早的 Older</Button>}
    <ActivityDetailSheet entryId={opened} onClose={close} />
  </div>
}
