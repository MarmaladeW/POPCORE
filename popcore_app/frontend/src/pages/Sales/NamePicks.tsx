import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Alert, Button, Input, Popconfirm, Select, Skeleton, message } from 'antd'

import client from '../../api/client'
import {
  forgetPick, getNamePicks, getPastedDay, ignoreName, savePick,
  type NameStatus, type PastedDay, type PastedName, type PickProduct, type RememberedPick,
} from '../../api/namePicks'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { Panel } from '../../components/pc/Panel'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { productLabel } from '../../lib/productLabel'
import { useAppStore } from '../../store'
import { stockLineEffect } from './dailyReportReview'
import { bySection, dayCounts, firstDay, placeText, quantityText, SECTION_TEXT, STATUS_TEXT, writtenName } from './namePicksView'
import { dayTitle } from './SalesNav'
import './historicalReports.css'
import './namePicks.css'

type Run = (key: string, action: () => Promise<unknown>, done: string) => Promise<boolean>

function StatusLabel({ status }: { status: NameStatus }) {
  const text = STATUS_TEXT[status]
  return <span className="pc-pick-status" data-tone={text.tone} title={text.en}>
    <i className={`pc-dot pc-dot-${text.tone}`} aria-hidden="true" />{text.zh}
  </span>
}

const productText = (product?: PickProduct | null) => product ? `${productLabel(product)}${product.sku ? ` · ${product.sku}` : ''}` : ''

/** Choose a product: the remembered and guessed ones first, any other by typing (记账名, name or SKU). */
function ProductChooser({ seeds, value, onChange, label, disabled }: {
  seeds: (PickProduct | null | undefined)[]; value: PickProduct | null; onChange: (product: PickProduct) => void
  label: string; disabled?: boolean
}) {
  const [found, setFound] = useState<PickProduct[]>([])
  const request = useRef(0)
  async function search(query: string) {
    const ticket = ++request.current
    if (!query.trim()) { setFound([]); return }
    try {
      const { data } = await client.get<PickProduct[]>('/products/search', { params: { q: query, limit: 8 } })
      if (ticket === request.current) setFound(data)
    } catch {
      if (ticket === request.current) setFound([])
    }
  }
  const options: PickProduct[] = []
  for (const product of [value, ...seeds, ...found]) {
    if (product && !options.some(option => option.id === product.id)) options.push(product)
  }
  return <Select showSearch className="pc-pick-chooser" aria-label={label} disabled={disabled} filterOption={false}
    placeholder="选产品，或打字搜索 Choose or search" value={value?.id} onSearch={search} popupMatchSelectWidth={false}
    notFoundContent={null} options={options.map(product => ({ value: product.id, label: productText(product) }))}
    onChange={(id: number) => { const product = options.find(option => option.id === id); if (product) onChange(product) }} />
}

/** One name of a pasted report, answered where the report wrote it. */
function NameRow({ name, storeCode, busy, run }: { name: PastedName; storeCode: string; busy: string; run: Run }) {
  const remembered = name.pick?.products ?? []
  const start = name.status === 'disputed' ? null : remembered[0] ?? name.guess ?? null
  const [chosen, setChosen] = useState<PickProduct | null>(start)
  const [editing, setEditing] = useState(false)
  useEffect(() => { setChosen(start); setEditing(false) }, [name.status, start?.id])  // eslint-disable-line react-hooks/exhaustive-deps
  const key = `${name.name_key}|${name.note_key}|${name.section}|${name.display}`
  const replaces = remembered.map(product => product.id)
  const changing = !!chosen && replaces.length > 0 && (name.status === 'disputed' || !replaces.includes(chosen.id))
  const quiet = (name.status === 'remembered' || name.status === 'ignored') && !editing
  const here = placeText({ store_code: storeCode, section: name.section, display: name.display })
  const effect = stockLineEffect({ section: name.section, to_display: name.display, from_display: name.display })
  const others = (name.pick?.places ?? []).map(placeText)

  const hint: { text: string; tone?: 'warn' | 'out' } | null = changing
    ? { text: `替换记住的 Replaces ${remembered.map(productText).join(' / ')}`, tone: 'warn' }
    : name.status === 'open' ? { text: name.guess ? 'POPCORE 猜的，选了才算 A guess: nothing is saved until you choose' : '没找到 No match: type to search' }
    : name.status === 'confirm' ? { text: `记住于 Remembered at ${others.join(', ') || '名称记忆'} · 这里确认一次 confirm it once for ${here}` }
    : name.status === 'stale' ? { text: '记住后产品资料改过 The product changed since it was picked', tone: 'warn' }
    : name.status === 'disputed' ? { text: '一份日报选了两个产品 One report picked two products: choose one', tone: 'out' }
    : name.status === 'remembered' ? { text: `自动确认 Confirms by itself at ${others.join(', ')}` }
    : name.status === 'ignored' ? { text: '已忽略：粘贴时仍会问 Set aside here; a paste still asks' } : null

  const save = async () => {
    if (chosen && await run(key, () => savePick({
      raw_name: name.raw_name, note: name.note, product_id: chosen.id, replaces,
      store_code: storeCode, section: name.section, display: name.display,
    }), `${writtenName(name)} → ${productLabel(chosen)}`)) setEditing(false)
  }
  const action = changing ? '替换 Replace' : name.status === 'confirm' || name.status === 'stale' ? '确认 Confirm' : '记住 Remember'
  const disabled = !!busy && busy !== key

  return <div className="pc-pick-row" data-quiet={quiet || undefined}>
    <StatusLabel status={name.status} />
    <div className="pc-pick-written">
      <strong>{writtenName(name)}</strong>
      <small>{[STATUS_TEXT[name.status].en, quantityText(name), effect ?? (name.display ? 'Display' : null)].filter(Boolean).join(' · ')}</small>
    </div>
    <div className="pc-pick-decision">
      {quiet
        ? <span className="pc-pick-product">{name.status === 'ignored' ? '—' : productText(remembered[0])}</span>
        : <ProductChooser label={`Product for ${writtenName(name)}`} value={chosen} onChange={setChosen}
            seeds={[...remembered, name.guess]} disabled={disabled} />}
      {hint && <small className="pc-pick-hint" data-tone={hint.tone}>{hint.text}</small>}
    </div>
    <div className="pc-pick-actions">
      {name.status === 'ignored' ? <Button loading={busy === key} disabled={disabled}
          onClick={() => run(key, () => ignoreName({ name_key: name.name_key, note_key: name.note_key, ignored: false }), `${writtenName(name)}: 恢复 Back on the list`)}>恢复 Restore</Button>
        : quiet ? <Button disabled={disabled} onClick={() => setEditing(true)}>改 Change</Button>
        : <>
          <Button type="primary" loading={busy === key} disabled={disabled || !chosen} onClick={save}>{action}</Button>
          {editing && <Button disabled={disabled} onClick={() => { setEditing(false); setChosen(start) }}>取消 Cancel</Button>}
          {name.status === 'open' && <Button disabled={disabled}
            onClick={() => run(key, () => ignoreName({ name_key: name.name_key, note_key: name.note_key, ignored: true }), `${writtenName(name)}: 已忽略 Set aside`)}>忽略 Ignore</Button>}
        </>}
    </div>
  </div>
}

function DayList({ days, current, onOpen }: { days: PastedDay[]; current?: PastedDay; onOpen: (day: PastedDay) => void }) {
  return <section className="pc-panel pc-hist-days pc-pick-days" aria-label="Pasted reports">
    <header className="pc-panel-head"><h2><span>粘贴过的日报</span> <small>Pasted reports</small></h2></header>
    <ul>{days.map(day => {
      const counts = dayCounts(day)
      const open = day === current
      return <li key={`${day.store_code}|${day.date}`} className={open ? 'pc-hist-day-current' : undefined}>
        <button type="button" aria-current={open ? 'true' : undefined} onClick={() => onOpen(day)}>
          <span><strong>{day.store_code} · {dayTitle(day.date)}</strong><small>{day.date} · {day.saved ? '已保存 Saved' : '未保存 Not saved'}</small></span>
          <b className="pc-pick-day-counts">{counts.toAnswer
            ? <><i className="pc-dot pc-dot-warn" aria-hidden="true" />待选 {counts.toAnswer}<span>· 记住 {counts.remembered}</span></>
            : <><i className="pc-dot pc-dot-ok" aria-hidden="true" />记住 {counts.remembered}</>}</b>
        </button>
      </li>
    })}</ul>
  </section>
}

function DayPanel({ day, names, busy, run }: { day: PastedDay; names?: PastedName[]; busy: string; run: Run }) {
  const counts = dayCounts(day)
  return <Panel className="pc-pick-day" zh={`${day.store_code} · ${dayTitle(day.date)} 日报`} label={`${day.store_code} ${day.date} names`}
    en={`${day.date} · ${counts.toAnswer} to answer · ${counts.remembered} remembered${day.saved ? '' : ' · 未保存 not saved'}`}
    count={counts.toAnswer ? `待选 ${counts.toAnswer}` : '都答了 All answered'} tone={counts.toAnswer ? 'warn' : 'ok'}>
    {!names ? <div className="pc-quiet"><Skeleton active /></div>
      : names.length === 0 ? <p className="pc-quiet"><strong>这份日报没有要选的名字</strong>Every line matched a catalogue name exactly.</p>
      : bySection(names).map(group => <div key={group.section} role="group" aria-label={SECTION_TEXT[group.section] ?? group.section}>
        <h3 className="pc-pick-section">{SECTION_TEXT[group.section] ?? group.section}<span>{group.names.length}</span></h3>
        {group.names.map(name => <NameRow key={`${name.name_key}|${name.note_key}|${name.display}`} name={name} storeCode={day.store_code} busy={busy} run={run} />)}
      </div>)}
  </Panel>
}

function AddPick({ busy, run }: { busy: string; run: Run }) {
  const [written, setWritten] = useState('')
  const [note, setNote] = useState('')
  const [product, setProduct] = useState<PickProduct | null>(null)
  const ready = !!written.trim() && !!product
  const add = async () => {
    if (!ready) return
    if (await run('add', () => savePick({ raw_name: written.trim(), note: note.trim(), product_id: product!.id, replaces: [] }),
      `${writtenName({ raw_name: written.trim(), note: note.trim() })} → ${productLabel(product!)}`)) {
      setWritten(''); setNote(''); setProduct(null)
    }
  }
  return <section className="pc-panel pc-pick-add" aria-label="Add a name">
    <header className="pc-panel-head"><h2><span>加一个名字</span> <small>Add a name</small></h2></header>
    <div className="pc-pick-add-body">
      <label>报告里写的 <small>Name as written</small><Input value={written} onChange={event => setWritten(event.target.value)} placeholder="smiski s1" /></label>
      <label>备注 <small>Note, if written</small><Input value={note} onChange={event => setNote(event.target.value)} placeholder="秘密" /></label>
      <label>产品 <small>Product</small><ProductChooser label="Product for the new name" value={product} onChange={setProduct} seeds={[]} /></label>
      <Button type="primary" loading={busy === 'add'} disabled={!ready || (!!busy && busy !== 'add')} onClick={add}>记住 Remember</Button>
    </div>
    <p className="pc-quiet">下次粘贴时，每个门店和分区先问一次，已经选好。 The next paste asks once per store and section, already picked.</p>
  </section>
}

function PickRow({ pick, busy, run }: { pick: RememberedPick; busy: string; run: Run }) {
  const [editing, setEditing] = useState(false)
  const [chosen, setChosen] = useState<PickProduct | null>(null)
  const key = `pick|${pick.name_key}|${pick.note_key}`
  const replaces = pick.products.map(product => product.id)
  const status: NameStatus = pick.state === 'ok' ? 'remembered' : pick.state
  const disabled = !!busy && busy !== key
  const where = pick.places.length ? `自动确认 Confirms by itself at ${pick.places.map(placeText).join(', ')}`
    : '还没在日报里确认过 Not confirmed on a paste yet: asked once per store and section'
  return <div className="pc-pick-row pc-pick-row-all">
    <StatusLabel status={status} />
    <div className="pc-pick-written">
      <strong>{writtenName(pick)}</strong>
      <small>{[STATUS_TEXT[status].en, pick.picked_at ? `选于 Picked ${pick.picked_at.slice(0, 10)}` : '名称记忆之前 Before name picks'].join(' · ')}</small>
    </div>
    <div className="pc-pick-decision">
      {editing ? <ProductChooser label={`New product for ${writtenName(pick)}`} value={chosen} onChange={setChosen} seeds={pick.products} disabled={disabled} />
        : <span className="pc-pick-product">{pick.products.map(productText).join(' / ')}</span>}
      <small className="pc-pick-hint" data-tone={pick.state === 'disputed' ? 'out' : pick.state === 'stale' ? 'warn' : undefined}>
        {editing && chosen && !(replaces.length === 1 && replaces[0] === chosen.id) ? `替换 Replaces ${pick.products.map(productText).join(' / ')}; 下次粘贴每个分区再确认 the next paste confirms it again per section`
          : pick.state === 'disputed' ? '两个产品：改成一个 Two products: change it to one'
          : pick.state === 'stale' ? '记住后产品资料改过 The product changed since it was picked' : where}
      </small>
    </div>
    <div className="pc-pick-actions">
      {editing ? <>
        <Button type="primary" loading={busy === key} disabled={disabled || !chosen}
          onClick={async () => {
            if (chosen && await run(key, () => savePick({ name_key: pick.name_key, note_key: pick.note_key, raw_name: pick.raw_name,
              note: pick.note, product_id: chosen.id, replaces }), `${writtenName(pick)} → ${productLabel(chosen)}`)) setEditing(false)
          }}>替换 Replace</Button>
        <Button disabled={disabled} onClick={() => { setEditing(false); setChosen(null) }}>取消 Cancel</Button>
      </> : <>
        <Button disabled={disabled} onClick={() => { setEditing(true); setChosen(pick.state === 'disputed' ? null : pick.products[0]) }}>改 Change</Button>
        <Popconfirm title={`忘记 Forget “${writtenName(pick)}”?`} description="下次粘贴会重新匹配这个名字。 The next paste matches it as new."
          okText="忘记 Forget" okButtonProps={{ danger: true }} cancelText="取消 Cancel" onConfirm={() => run(key, () => forgetPick({ name_key: pick.name_key, note_key: pick.note_key, replaces }), `${writtenName(pick)}: 忘记了 Forgotten`)}>
          <Button loading={busy === key} disabled={disabled}>忘记 Forget</Button>
        </Popconfirm>
      </>}
    </div>
  </div>
}

// 名称记忆 Name picks (owner, 2026-11-03): the paste, answered. Each pasted report's names sit under its own
// section headings; a pick confirms a line by itself only at the same store and section.
export default function NamePicksPage() {
  const store = useAppStore(state => state.selectedStore)
  const code = store?.code || 'ALL'
  const phone = useIsPhone()
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'all' ? 'all' : 'days'
  const [data, setData] = useState<{ picks: RememberedPick[]; days: PastedDay[] }>()
  const [names, setNames] = useState<PastedName[]>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [query, setQuery] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setError('')
    getNamePicks(code, controller.signal).then(setData).catch((cause: any) => {
      if (cause?.code !== 'ERR_CANCELED') setError(cause?._serverMessage || 'Unable to load name picks.')
    })
    return () => controller.abort()
  }, [code, attempt])

  const days = data?.days ?? []
  const asked = days.find(day => day.store_code === params.get('store') && day.date === params.get('date'))
  const current = asked ?? (phone ? undefined : firstDay(days))
  useEffect(() => {
    if (!current) { setNames(undefined); return }
    const controller = new AbortController()
    getPastedDay(current.store_code, current.date, controller.signal).then(setNames).catch((cause: any) => {
      if (cause?.code !== 'ERR_CANCELED') setError(cause?._serverMessage || 'Unable to load that report’s names.')
    })
    return () => controller.abort()
  }, [current?.store_code, current?.date, attempt])  // eslint-disable-line react-hooks/exhaustive-deps

  const run: Run = async (key, action, done) => {
    setBusy(key)
    try {
      await action()
      message.success(done)
      return true
    } catch (cause: any) {
      message.error(cause?.response?.data?.code === 'pick_changed'
        ? '这个名字刚被改过，已刷新 This name changed meanwhile; refreshed. Check it and try again.'
        : cause?._serverMessage || '没保存 That did not save. Try again.')
      return false
    } finally {
      setBusy('')
      setAttempt(value => value + 1)
    }
  }

  const open = (day: PastedDay) => setParams({ store: day.store_code, date: day.date })
  const showDay = phone && !!asked
  const terms = query.trim().toLowerCase()
  const picks = (data?.picks ?? []).filter(pick => !terms || [writtenName(pick), ...pick.products.map(productText)]
    .some(text => text.toLowerCase().includes(terms)))

  return <div className="pc-picks">
    <PageHeader title="名称记忆" sub={`Name picks · ${store?.name || 'All stores'}`}
      back={showDay ? { label: '日报 Pastes', to: '/sales/name-picks' } : { label: '历史日报', to: '/sales' }} />
    {!phone && <Link className="pc-hist-back" to="/sales"><Icon name="back" size={14} />历史日报 Historical reports</Link>}
    {!showDay && <>
      <p className="pc-picks-intro">告诉 POPCORE 一次，日报里的名字是哪个产品。同一家店、同一个分区再出现时自动确认；换了店或分区，粘贴时先问一次，已经选好。
        Tell POPCORE once which product a report name means. It confirms by itself at the same store and section; anywhere else the paste asks once, already picked.</p>
      <nav className="pc-page-tabs" aria-label="Name picks views">
        <Link to="/sales/name-picks" aria-current={tab === 'days' ? 'page' : undefined}>按日报 <span>By report</span></Link>
        <Link to="/sales/name-picks?tab=all" aria-current={tab === 'all' ? 'page' : undefined}>记住的 <span>All picks</span>{data ? ` · ${data.picks.length}` : ''}</Link>
      </nav>
    </>}
    {error && <Alert role="alert" type="error" showIcon message={error} action={<Button onClick={() => setAttempt(value => value + 1)}>Retry</Button>} />}
    {!data ? !error && <div className="pc-panel"><div className="pc-quiet"><Skeleton active /></div></div>
      : tab === 'all' ? <div className="pc-picks-all">
        <AddPick busy={busy} run={run} />
        <Input.Search className="pc-picks-search" allowClear placeholder="搜索名字或产品 Search a name or product" aria-label="Search remembered names"
          value={query} onChange={event => setQuery(event.target.value)} />
        <Panel zh="记住的名字" en={`Remembered names · ${picks.length}`} label="Remembered names">
          {picks.length ? picks.map(pick => <PickRow key={`${pick.name_key}|${pick.note_key}`} pick={pick} busy={busy} run={run} />)
            : <p className="pc-quiet"><strong>{terms ? '没有找到 Nothing matches' : '还没有记住的名字'}</strong>
              {terms ? 'Try the name as the report wrote it, or a product’s 记账名 or SKU.' : 'Names picked in a paste or added here show up here.'}</p>}
        </Panel>
      </div>
      : days.length === 0 ? <p className="pc-panel pc-quiet"><strong>还没有粘贴过的日报</strong>
        A report’s names show up here after it is pasted in <Link to="/sales">历史日报 → 导入日报 Import report</Link>.</p>
      : showDay && current ? <DayPanel day={current} names={names} busy={busy} run={run} />
      : <div className="pc-picks-grid">
        <DayList days={days} current={current} onOpen={open} />
        {current && !phone && <DayPanel day={current} names={names} busy={busy} run={run} />}
      </div>}
  </div>
}
