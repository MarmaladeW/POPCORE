import { Alert, Button, Input, InputNumber, Modal, Select, Skeleton } from 'antd'
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { getStaffDiscounts, type DiscountPage, type DiscountPerson, type DiscountUse } from '../../api/staffDiscounts'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { Panel } from '../../components/pc/Panel'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { formatCents, tryParseMoneyToCents } from '../../lib/money'
import { useAppStore } from '../../store'
import { torontoDate } from '../Dashboard/todayPresentation'
import useCheckoutMutation from '../Sales/useCheckoutMutation'
import { PAYMENTS, distinctProducts, itemsText, monthTitle, paymentLabel, shiftMonth, shortDate, standing } from './staffDiscountsView'
import './staffDiscounts.css'

// 员工折扣 Staff discounts (owner, 2026-11-19): "each 正式员工 and above have 4 discount per month, we wanna keep track
// of that ... it is easier for us to track the revenue too." Staff see their own uses and what they have left (owner,
// 2026-11-26); managers see everyone, fix the 日报 lines, pick the person a line names, mark a record 不算 and add a
// use by hand. Money shows to managers only.

type Dialog = { kind:'add' } | { kind:'fix'; use:DiscountUse } | { kind:'ignore'; use:DiscountUse }
const messageOf = (cause:unknown, fallback:string) => (cause as { _serverMessage?:string })?._serverMessage || fallback
const who = (use:DiscountUse) => use.name ?? (use.written_name ? `「${use.written_name}」` : '没写名字 No name')
const paymentOptions = PAYMENTS.map(payment => ({ value:payment.code, label:[payment.zh, payment.en].filter(Boolean).join(' ') }))

export default function StaffDiscountsPage() {
  const phone = useIsPhone()
  const current = torontoDate().slice(0, 7)
  const [month, setMonth] = useState(current)
  const [refresh, setRefresh] = useState(0)
  const [result, setResult] = useState<{ key:string; data?:DiscountPage; error?:string }>()
  const [dialog, setDialog] = useState<Dialog>()
  const key = `${month}|${refresh}`
  useEffect(() => {
    const controller = new AbortController()
    getStaffDiscounts(month, controller.signal)
      .then(data => { if (!controller.signal.aborted) setResult({ key, data }) })
      .catch(cause => { if (!controller.signal.aborted) setResult({ key, error:messageOf(cause, 'The 员工折扣 page did not load.') }) })
    return () => controller.abort()
  }, [key, month])
  const loaded = result?.key === key ? result : undefined
  // Keep the last page on screen while a refresh of the same month loads.
  const data = loaded?.data ?? (loaded ? undefined : result?.data?.month === month ? result.data : undefined)
  const reload = () => setRefresh(value => value + 1)
  const manage = !!data?.can_manage
  const done = (date?:string) => { setDialog(undefined); if (date) setMonth(date.slice(0, 7)); reload() }

  const stepper = <div className="pc-day pc-sd-months" role="group" aria-label="Month">
    <button type="button" aria-label="Previous month" onClick={() => setMonth(shiftMonth(month, -1))}><Icon name="back" size={16} /></button>
    <span className="pc-day-date"><strong>{monthTitle(month)}</strong><small>{month === current ? '本月 This month' : month}</small></span>
    <button type="button" aria-label="Next month" disabled={month >= current} onClick={() => setMonth(shiftMonth(month, 1))}><Icon name="right" size={16} /></button>
  </div>

  return <div className="pc-sd">
    <PageHeader title="员工折扣" sub="Staff discounts" actions={stepper} />
    {phone && stepper}
    {loaded?.error ? <Alert type="error" showIcon message={loaded.error} action={<Button onClick={reload}>Retry</Button>} />
      : !data ? <Skeleton active paragraph={{ rows:6 }} />
      : <>
        {data.totals && <MonthTotal data={data} />}
        <div className="pc-sd-layout">
          <People data={data} />
          <Waiting data={data} onOpen={setDialog} />
          <Uses data={data} onOpen={setDialog} />
          {data.ignored.length > 0 && <Ignored data={data} onDone={() => done()} />}
        </div>
      </>}
    {manage && data && dialog?.kind === 'add' && <AddDialog data={data} onClose={() => setDialog(undefined)} onSaved={done} />}
    {manage && data && dialog?.kind === 'fix' && <FixDialog data={data} use={dialog.use} onClose={() => setDialog(undefined)} onSaved={() => done()} />}
    {manage && dialog?.kind === 'ignore' && <IgnoreDialog use={dialog.use} onClose={() => setDialog(undefined)} onSaved={() => done()} />}
  </div>
}

// What was paid with staff discounts this month, per store and in total (managers only).
function MonthTotal({ data }:{ data:DiscountPage }) {
  const totals = data.totals!
  const note = (records:number, missing:number) => `${records} 笔${missing ? ` · ${missing} 笔没写金额 no amount` : ''}`
  return <section className="pc-sd-month" aria-label={`员工折扣收入 ${monthTitle(data.month)}`}>
    <h2 className="pc-sd-title">员工折扣收入 <small>Paid with staff discounts · {monthTitle(data.month)}</small></h2>
    <div className="pc-figures pc-sd-figures" style={{ '--pc-sd-cells':Math.max(2, totals.stores.length + 1) } as CSSProperties}>
      <div className="pc-sd-total"><span>合计 <small>Total</small></span><strong>{formatCents(totals.amount_cents)}</strong><small>{totals.records ? note(totals.records, totals.no_amount) : '本月没有 · None this month'}</small></div>
      {totals.stores.map(store => <div key={store.store}>
        <span>{store.store} <small>{store.name}</small></span><strong>{formatCents(store.amount_cents)}</strong><small>{note(store.records, store.no_amount)}</small>
      </div>)}
    </div>
  </section>
}

function People({ data }:{ data:DiscountPage }) {
  const monthly = data.monthly_uses
  // Staff get their own row only (the server sends no one else's).
  const mine = !data.can_manage
  return <Panel zh={mine ? '我的' : '每人'} en={`${mine ? 'Mine' : 'People'} · ${monthly} a month`} label={mine ? '我的 Mine' : '每人 People'} className="pc-sd-people">
    {!data.people.length ? <p className="pc-quiet"><strong>{mine ? '你还没有员工折扣' : '还没有正式员工'}</strong>People get 4 uses a month from 正式员工 up.</p>
      : <ul className="pc-sd-rows">{data.people.map(person => <PersonRow key={person.employee_id} person={person} monthly={monthly} />)}</ul>}
  </Panel>
}

function PersonRow({ person, monthly }:{ person:DiscountPerson; monthly:number }) {
  const status = standing(person)
  return <li className={`pc-row pc-sd-person is-${status.tone}`}>
    <span className="pc-row-label">{person.rank_label ?? '—'}</span>
    <span className="pc-row-main">
      <strong>{person.name}</strong>
      <small className="pc-sd-standing"><i className={`pc-dot pc-sd-dot-${status.tone}`} aria-hidden="true" />{status.zh} {status.en}{person.active ? '' : ' · 已停用 Inactive'}</small>
    </span>
    {person.eligible && <span className="pc-sd-pips" aria-hidden="true">{Array.from({ length:Math.max(monthly, person.used) }, (_, index) =>
      <i key={index} className={index < person.used ? index >= monthly ? 'is-over' : 'is-used' : undefined} />)}</span>}
    <span className="pc-sd-count" aria-label={person.eligible ? `${person.used} of ${monthly} used` : `${person.used} used`}>
      <b>{person.used}</b>{person.eligible ? <small>/ {monthly}</small> : <small>次</small>}
    </span>
  </li>
}

function Waiting({ data, onOpen }:{ data:DiscountPage; onOpen:(dialog:Dialog)=>void }) {
  return <Panel zh="待处理" en="Waiting for a manager" label="待处理 Waiting for a manager" className="pc-sd-waiting"
    count={data.waiting.length || undefined} tone="warn">
    {!data.waiting.length ? <p className="pc-quiet"><strong>没有待处理</strong>{data.can_manage ? 'Every 日报 line this month has its person and was read.' : 'None of your records waits for a manager.'}</p>
      : <ul className="pc-sd-rows">{data.waiting.map(use => <li key={use.id} className="pc-row pc-sd-wait">
        <span className="pc-row-label pc-sd-when"><span>{shortDate(use.date)}</span><span className="pc-tag">{use.store}</span></span>
        <span className="pc-row-main">
          <strong>{who(use)}</strong>
          {use.items.length > 0 && <small>{itemsText(use.items)}</small>}
          {use.raw_text && <small className="pc-sd-raw">日报 · {use.raw_text}</small>}
          <small className="pc-sd-standing"><i className="pc-dot pc-dot-warn" aria-hidden="true" />{[use.waiting_for === 'person' && '谁？ Pick the person', !use.readable && '看不懂 Could not read the line'].filter(Boolean).join(' · ')}</small>
        </span>
        {data.can_manage && <span className="pc-sd-actions">
          <Button onClick={() => onOpen({ kind:'fix', use })}>{use.waiting_for === 'person' ? '选人 Pick' : '修改 Fix'}</Button>
          <Button type="text" onClick={() => onOpen({ kind:'ignore', use })}>不算 <small>Ignore</small></Button>
        </span>}
      </li>)}</ul>}
  </Panel>
}

function Uses({ data, onOpen }:{ data:DiscountPage; onOpen:(dialog:Dialog)=>void }) {
  const total = data.uses.reduce((sum, use) => sum + use.uses, 0)
  // The count sits beside the add button at the right, as one group.
  return <Panel zh="本月记录" en={`Uses · ${monthTitle(data.month)}`} label="本月记录 Uses this month" className="pc-sd-uses"
    action={<span className="pc-sd-head-end">
      {data.uses.length > 0 && <span className="pc-panel-count"><i className="pc-dot pc-dot-ok" aria-hidden="true" />{total} 次</span>}
      {data.can_manage && <Button className="pc-sd-add" aria-label="手动添加 Add by hand" icon={<Icon name="plus" size={14} />} onClick={() => onOpen({ kind:'add' })}>手动添加 <small>Add by hand</small></Button>}
    </span>}>
    {!data.uses.length ? <p className="pc-quiet"><strong>{monthTitle(data.month)}还没有员工折扣</strong>{data.can_manage ? 'Uses from the 日报 and those added by hand show here.' : 'Your own uses show here.'}</p>
      : <ul className="pc-sd-rows">{data.uses.map(use => <li key={use.id} className={`pc-sd-use${data.can_manage ? ' can-manage' : ''}`}>
        <span className="pc-sd-when"><span>{shortDate(use.date)}</span><span className="pc-tag">{use.store}</span></span>
        <span className="pc-sd-who"><strong>{use.name}</strong>{use.written_name && use.name && use.written_name.toLowerCase() !== use.name.toLowerCase() && <small>日报写「{use.written_name}」</small>}</span>
        <span className="pc-sd-what">{itemsText(use.items)}</span>
        <span className="pc-sd-n"><b>{use.uses}</b> 次</span>
        <span className="pc-sd-paid">{'amount_cents' in use && <b>{use.amount_cents != null ? formatCents(use.amount_cents) : '—'}</b>}<small>{paymentLabel(use.payment) || (data.can_manage ? '' : '—')}</small></span>
        <span className="pc-sd-source">{use.source === 'report' ? '日报' : <>手动 <small>Added by hand{use.created_by_name ? ` · ${use.created_by_name}` : ''}</small></>}
          {use.edited && <small className="pc-sd-edited">已修改 Edited{use.edited_by_name ? ` · ${use.edited_by_name}` : ''}</small>}</span>
        {data.can_manage && <span className="pc-sd-actions">
          <Button type="text" onClick={() => onOpen({ kind:'fix', use })}>修改 <small>Fix</small></Button>
          <Button type="text" onClick={() => onOpen({ kind:'ignore', use })}>不算 <small>Ignore</small></Button>
        </span>}
      </li>)}</ul>}
  </Panel>
}

function Ignored({ data, onDone }:{ data:DiscountPage; onDone:()=>void }) {
  const [busy, setBusy] = useState<number>()
  const mutation = useCheckoutMutation(() => { setBusy(undefined); onDone() })
  const locked = mutation.saving || mutation.pending
  return <Panel zh="不算" en="Ignored" label="不算 Ignored" className="pc-sd-ignored">
    {mutation.notice && <div className="pc-sd-panel-notice">{mutation.notice}</div>}
    <ul className="pc-sd-rows">{data.ignored.map(use => <li key={use.id} className="pc-row pc-sd-wait">
      <span className="pc-row-label pc-sd-when"><span>{shortDate(use.date)}</span><span className="pc-tag">{use.store}</span></span>
      <span className="pc-row-main">
        <strong>{who(use)}</strong>
        <small>{[itemsText(use.items), use.raw_text && !use.items.length ? use.raw_text : ''].filter(Boolean).join(' · ')}</small>
        <small className="pc-sd-standing"><i className="pc-dot pc-sd-dot-none" aria-hidden="true" />不算 · {use.ignored_reason}{use.edited_by_name ? ` · ${use.edited_by_name}` : ''}</small>
      </span>
      {data.can_manage && <span className="pc-sd-actions">
        <Button disabled={locked} loading={mutation.saving && busy === use.id}
          onClick={() => { setBusy(use.id); void mutation.run(`/staff-discounts/${use.id}/restore`, {}) }}>算回 <small>Count again</small></Button>
      </span>}
    </li>)}</ul>
  </Panel>
}

function PersonPicker({ data, value, onChange, id }:{ data:DiscountPage; value?:number; onChange:(value:number)=>void; id:string }) {
  return <Select id={id} showSearch value={value} placeholder="选员工 Choose a person" optionFilterProp="label" onChange={onChange}
    options={(data.employees ?? []).map(person => ({ value:person.employee_id, label:person.rank_label ? `${person.name} · ${person.rank_label}` : person.name }))} />
}

function Field({ zh, en, id, children }:{ zh:string; en:string; id?:string; children:ReactNode }) {
  return <div className="pc-sd-field"><label htmlFor={id}>{zh} <small>{en}</small></label>{children}</div>
}

function Footer({ saving, valid, label, onClose, onSave }:{ saving:boolean; valid:boolean; label:string; onClose:()=>void; onSave:()=>void }) {
  return <div className="pc-sd-dialog-actions">
    <Button disabled={saving} onClick={onClose}>取消 Cancel</Button>
    <Button type="primary" disabled={!valid || saving} loading={saving} onClick={onSave}>{label}</Button>
  </div>
}

function FixDialog({ data, use, onClose, onSaved }:{ data:DiscountPage; use:DiscountUse; onClose:()=>void; onSaved:()=>void }) {
  const pick = use.waiting_for === 'person'
  const [person, setPerson] = useState(use.employee_id ?? undefined)
  const [uses, setUses] = useState<number|null>(use.uses)
  const [amount, setAmount] = useState(use.amount_cents != null ? (use.amount_cents / 100).toFixed(2) : '')
  const [payment, setPayment] = useState(use.payment ?? undefined)
  const mutation = useCheckoutMutation(onSaved)
  const cents = tryParseMoneyToCents(amount)
  const amountOk = !amount.trim() || cents != null
  const usesOk = uses != null && Number.isSafeInteger(uses) && uses >= 0 && uses <= 100
  const valid = !!person && usesOk && amountOk
  function save() {
    if (!valid) return
    const body:Record<string, unknown> = {}
    if (person !== use.employee_id) body.employee_id = person
    if (uses !== use.uses || !use.readable) body.uses = uses  // a manager's count reads a line that could not be read
    if (cents !== (use.amount_cents ?? null)) body.amount = amount.trim() || null
    if ((payment ?? null) !== use.payment) body.payment = payment ?? null
    if (!Object.keys(body).length) { onClose(); return }
    void mutation.run(`/staff-discounts/${use.id}/fix`, body)
  }
  return <Modal open title={pick ? '选人 Pick the person' : '修改 Fix this record'} onCancel={() => !mutation.saving && onClose()} footer={null} width={480}>
    <div className="pc-sd-dialog">
      <p className="pc-sd-dialog-what"><strong>{shortDate(use.date)} · {use.store} · {who(use)}</strong>{use.items.length > 0 && <span>{itemsText(use.items)}</span>}
        {use.raw_text && <small>日报 · {use.raw_text}</small>}</p>
      {mutation.notice}
      <Field zh="员工" en="Person" id="pc-sd-fix-person"><PersonPicker id="pc-sd-fix-person" data={data} value={person} onChange={setPerson} /></Field>
      {pick && use.written_name && <p className="pc-sd-hint">以后「{use.written_name}」都算这个人 · From now on “{use.written_name}” means this person.</p>}
      <div className="pc-sd-field-pair">
        <Field zh="算几次" en="Uses counted" id="pc-sd-fix-uses">
          <InputNumber id="pc-sd-fix-uses" min={0} max={100} precision={0} value={uses} inputMode="numeric" onChange={setUses} />
        </Field>
        <Field zh="金额" en="Amount paid" id="pc-sd-fix-amount">
          <Input id="pc-sd-fix-amount" prefix="$" inputMode="decimal" value={amount} status={amountOk ? undefined : 'error'} onChange={event => setAmount(event.target.value)} />
        </Field>
      </div>
      <Field zh="付款" en="Payment" id="pc-sd-fix-payment">
        <Select id="pc-sd-fix-payment" allowClear placeholder="—" value={payment} options={paymentOptions} onChange={value => setPayment(value)} />
      </Field>
      <p className="pc-sd-hint">一件商品算一次，十个同样的也是一次 · One use is one product, however many pieces.</p>
      <Footer saving={mutation.saving} valid={valid} label="保存 Save" onClose={onClose} onSave={save} />
    </div>
  </Modal>
}

function IgnoreDialog({ use, onClose, onSaved }:{ use:DiscountUse; onClose:()=>void; onSaved:()=>void }) {
  const [reason, setReason] = useState('')
  const mutation = useCheckoutMutation(onSaved)
  const valid = !!reason.trim()
  return <Modal open title="不算 Ignore this record" onCancel={() => !mutation.saving && onClose()} footer={null} width={440}>
    <div className="pc-sd-dialog">
      <p className="pc-sd-dialog-what"><strong>{shortDate(use.date)} · {use.store} · {who(use)}</strong>{use.items.length > 0 && <span>{itemsText(use.items)}</span>}</p>
      {mutation.notice}
      <Field zh="为什么不算" en="Reason" id="pc-sd-ignore-reason">
        <Input.TextArea id="pc-sd-ignore-reason" rows={2} maxLength={300} value={reason} onChange={event => setReason(event.target.value)}
          placeholder="e.g. 重复了 written twice" />
      </Field>
      <p className="pc-sd-hint">It stays on the page and counts nothing. 算回 Count again undoes it.</p>
      <Footer saving={mutation.saving} valid={valid} label="不算 Ignore" onClose={onClose}
        onSave={() => { if (valid) void mutation.run(`/staff-discounts/${use.id}/ignore`, { reason:reason.trim() }) }} />
    </div>
  </Modal>
}

function AddDialog({ data, onClose, onSaved }:{ data:DiscountPage; onClose:()=>void; onSaved:(date:string)=>void }) {
  const selected = useAppStore(state => state.selectedStore)
  const stores = data.stores ?? []
  const [person, setPerson] = useState<number>()
  const [date, setDate] = useState(data.today)
  const [store, setStore] = useState(stores.find(item => item.code === selected?.code)?.code ?? stores[0]?.code)
  const [rows, setRows] = useState<Array<{ name:string; qty:number|null }>>([{ name:'', qty:1 }])
  const [amount, setAmount] = useState('')
  const [payment, setPayment] = useState<string>()
  const mutation = useCheckoutMutation(() => onSaved(date))
  const cents = tryParseMoneyToCents(amount)
  const amountOk = !amount.trim() || cents != null
  const filled = rows.filter(row => row.name.trim())
  const rowsOk = filled.length > 0 && filled.every(row => row.qty != null && Number.isSafeInteger(row.qty) && row.qty >= 1)
  const valid = !!person && /^\d{4}-\d{2}-\d{2}$/.test(date) && !!store && rowsOk && amountOk
  const count = distinctProducts(filled)
  const setRow = (index:number, change:Partial<{ name:string; qty:number|null }>) =>
    setRows(current => current.map((row, at) => at === index ? { ...row, ...change } : row))
  function save() {
    if (!valid) return
    void mutation.run('/staff-discounts', { employee_id:person, date, store, amount:amount.trim() || null, payment:payment ?? null,
      items:filled.map(row => ({ name:row.name.trim(), qty:row.qty })) })
  }
  return <Modal open title="手动添加 Add a use by hand" onCancel={() => !mutation.saving && onClose()} footer={null} width={520}>
    <div className="pc-sd-dialog">
      {mutation.notice}
      <Field zh="员工" en="Person" id="pc-sd-add-person"><PersonPicker id="pc-sd-add-person" data={data} value={person} onChange={setPerson} /></Field>
      <div className="pc-sd-field-pair">
        <Field zh="日期" en="Date" id="pc-sd-add-date"><Input id="pc-sd-add-date" type="date" value={date} max={data.today} onChange={event => setDate(event.target.value)} /></Field>
        <Field zh="门店" en="Store" id="pc-sd-add-store">
          <Select id="pc-sd-add-store" value={store} onChange={setStore} options={stores.map(item => ({ value:item.code, label:`${item.code} · ${item.name}` }))} />
        </Field>
      </div>
      <fieldset className="pc-sd-products">
        <legend>商品 <small>Products · {count === 1 ? '1 use' : `${count} uses`}</small></legend>
        {rows.map((row, index) => <div key={index} className="pc-sd-product">
          <Input aria-label={`Product ${index + 1}`} placeholder="名称 / 记账名" value={row.name} onChange={event => setRow(index, { name:event.target.value })} />
          <InputNumber aria-label={`Quantity of product ${index + 1}`} min={1} max={9999} precision={0} value={row.qty} inputMode="numeric" onChange={qty => setRow(index, { qty })} />
          <Button aria-label={`Remove product ${index + 1}`} icon={<Icon name="minus" size={14} />} disabled={rows.length === 1}
            onClick={() => setRows(current => current.filter((_, at) => at !== index))} />
        </div>)}
        <Button type="dashed" icon={<Icon name="plus" size={14} />} disabled={rows.length >= 30} onClick={() => setRows(current => [...current, { name:'', qty:1 }])}>再加一件 <small>Another product</small></Button>
      </fieldset>
      <div className="pc-sd-field-pair">
        <Field zh="金额" en="Amount paid" id="pc-sd-add-amount">
          <Input id="pc-sd-add-amount" prefix="$" inputMode="decimal" value={amount} status={amountOk ? undefined : 'error'} onChange={event => setAmount(event.target.value)} />
        </Field>
        <Field zh="付款" en="Payment" id="pc-sd-add-payment">
          <Select id="pc-sd-add-payment" allowClear placeholder="—" value={payment} options={paymentOptions} onChange={value => setPayment(value)} />
        </Field>
      </div>
      <p className="pc-sd-hint">一件商品算一次，十个同样的也是一次 · One use is one product, however many pieces.</p>
      <Footer saving={mutation.saving} valid={valid} label="添加 Add" onClose={onClose} onSave={save} />
    </div>
  </Modal>
}
