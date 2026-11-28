import { Alert, Button, Drawer, Skeleton } from 'antd'
import { Fragment, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { getActivityEntry, type ActivityDetail, type ChangedRecord, type StockEnd, type StockLine } from '../../api/activity'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { activityTime, activityWho } from '../../lib/activityView'

// One 动态 line opened (owner, 2026-11-28: "admin should be able to click into there and see exactly what changed"):
// who, when and where, then only what moved: each record's changed fields before → after, and the stock it moved with
// each place's count before → after. A line from before 2026-11-28 kept no details and says so.

const TAGS: Partial<Record<ChangedRecord['op'], string>> = { add: '新 New', remove: '删除 Removed' }

function Value({ value }: { value: string | null }) {
  return value == null ? <span className="pc-actd-empty">—<span className="pc-sr">空 Empty</span></span> : <>{value}</>
}

function Record({ record }: { record: ChangedRecord }) {
  return <section className="pc-actd-record" aria-label={record.label}>
    <h3>{record.label}{TAGS[record.op] && <span className={`pc-actd-tag pc-actd-tag-${record.op}`}>{TAGS[record.op]}</span>}</h3>
    <dl>
      {record.fields.map(field => <Fragment key={field.label}>
        <dt>{field.label}</dt>
        <dd>
          {record.op === 'add' ? <Value value={field.after} />
            : record.op === 'remove' ? <span className="pc-actd-before"><Value value={field.before} /></span>
              : <>
                <span className="pc-actd-before"><Value value={field.before} /></span>
                <span className="pc-actd-arrow" aria-hidden="true">→</span><span className="pc-sr">改为 to</span>
                <span className="pc-actd-after"><Value value={field.after} /></span>
              </>}
        </dd>
      </Fragment>)}
    </dl>
  </section>
}

function End({ word, end, into }: { word: string; end: StockEnd; into?: string }) {
  const counted = end.before != null && end.after != null
  return <div className="pc-actd-end">
    <span className="pc-actd-end-word">{word}</span>
    <span className="pc-actd-place">{end.place}{into && <small>{into}</small>}</span>
    {counted && <span className="pc-actd-count">{end.before}<span aria-hidden="true"> → </span><span className="pc-sr"> 改为 to </span>{end.after}</span>}
  </div>
}

function Stock({ lines, more }: { lines: StockLine[]; more?: number }) {
  // A reason heads its lines only when the change has more than one (a 日报's 入店 next to its sales).
  const reasons = new Set(lines.map(line => line.why || ''))
  const grouped = reasons.size > 1
  return <section className="pc-actd-stock" aria-label="库存 Stock">
    <h3>库存 Stock<small>{lines.length + (more ?? 0)} 行 lines</small></h3>
    <ol>
      {lines.map((line, index) => <Fragment key={index}>
        {grouped && line.why !== lines[index - 1]?.why && <li className="pc-actd-why">{line.why || '—'}</li>}
        <li className="pc-actd-line">
          <div className="pc-actd-product"><strong>{line.product}</strong><span>×{line.qty}</span></div>
          <End word="从" end={line.from} />
          <End word="到" end={line.to} into={line.into} />
        </li>
      </Fragment>)}
    </ol>
    {!!more && <p className="pc-actd-more">还有 {more} 行没有列出 · {more} more lines not listed</p>}
  </section>
}

export default function ActivityDetailSheet({ entryId, onClose }: { entryId: number | null; onClose: () => void }) {
  const phone = useIsPhone()
  const [entry, setEntry] = useState<ActivityDetail>()
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)

  useEffect(() => {
    if (entryId == null) return
    const controller = new AbortController()
    setEntry(undefined); setError('')
    getActivityEntry(entryId, controller.signal).then(setEntry)
      .catch(cause => { if (!controller.signal.aborted) setError(cause?._serverMessage || cause?.response?.data?.error || '这条动态没有加载 This line did not load') })
    return () => controller.abort()
  }, [entryId, reload])

  const shown = entry && entry.id === entryId ? entry : undefined
  const changes = shown?.changes
  const records = changes?.records ?? []
  const stock = changes?.stock ?? []
  return <Drawer
    open={entryId != null}
    onClose={onClose}
    placement={phone ? 'bottom' : 'right'}
    width={phone ? undefined : 'min(100%, 480px)'}
    height={phone ? 'auto' : undefined}
    rootClassName="pc-actd"
    title={shown ? <span className="pc-actd-title">{shown.summary}</span> : '动态 Activity'}
    footer={shown?.link ? <Link className="pc-actd-open" to={shown.link} onClick={onClose}>打开 Open</Link> : null}
  >
    {error ? <Alert type="error" showIcon message={error} action={<Button onClick={() => setReload(n => n + 1)}>重试 Retry</Button>} />
      : !shown ? <Skeleton active paragraph={{ rows: 5 }} />
        : <>
          <p className="pc-actd-meta">
            <strong>{activityWho(shown)}</strong>
            {shown.store_code && <span>{shown.store_code}</span>}
            <time dateTime={shown.at}>{shown.business_date} {activityTime(shown)}</time>
          </p>
          {/* The line's few words only when nothing more was kept: the details below say it in full. */}
          {shown.detail && !changes && <p className="pc-actd-detail">{shown.detail}</p>}
          {!changes ? <p className="pc-actd-none"><strong>这条没有保存改动细节</strong>No change details were kept for this line.</p>
            : <>
              {records.map((record, index) => <Record key={`${record.type}:${record.label}:${index}`} record={record} />)}
              {!!changes.records_more && <p className="pc-actd-more">还有 {changes.records_more} 项没有列出 · {changes.records_more} more not listed</p>}
              {stock.length > 0 && <Stock lines={stock} more={changes.stock_more} />}
            </>}
        </>}
  </Drawer>
}
