import { useState, useEffect, useRef, type ReactNode } from 'react'
import {
  Popconfirm, ColorPicker, Form, Input, Select, Segmented,
  Switch, message, Checkbox, Alert, DatePicker,
} from 'antd'
import dayjs from 'dayjs'
import { ChevronLeft, ChevronRight, Pipette, Plus, RefreshCw, Search } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { useHasRole } from '../../auth/useRole'
import { useIsMobile } from '../../hooks/useIsMobile'
import PageHeader from '../../components/pc/PageHeader'
import './users.css'
import { EMPLOYEE_PALETTE } from '../../lib/palette'
import client from '../../api/client'
import { useAppStore } from '../../store'
import {
  getEmployeeStores,
  renameEmployee,
  setEmployeeColor,
  setEmployeeStatus,
  setEmployeeSchedulable,
  setEmployeeStores,
  type EmployeeStatus,
} from '../Schedule/scheduleApi'
import {
  isEyeDropperCancellation,
  normalizeEmployeeColor,
  pickScreenColor,
  persistEmployeeSetting,
  updateEmployeeSetting,
} from '../Schedule/employeeScheduling'
import { isRegularOn } from '../Schedule/monthlyDetail'
import RegularMark from '../Schedule/RegularMark'
import TrainingMark from '../Schedule/TrainingMark'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog'

interface User {
  id: string
  username: string
  role: string
  is_active: number
  created_at: string
  last_login: string
}

interface StoreGrants {
  stores: { id: number; code: string; name: string }[]
  grants: { auth0_sub: string; store_id: number }[]
}

type StoreGrantState = ReturnType<typeof useStoreGrants>

// One per-store grant list an admin edits here; each change saves at once.
function useStoreGrants(path: string, enabled: boolean, viewer: string | undefined, name: string) {
  const [data, setData] = useState<StoreGrants>()
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const request = useRef(0)
  const busy = useRef(false)

  async function load() {
    const current = ++request.current
    setData(undefined)
    setError('')
    try {
      const response = await client.get<StoreGrants>(path)
      if (current === request.current) setData(response.data)
    } catch (err: any) {
      if (current === request.current) setError(err?.response?.data?.error ?? `Unable to load ${name.toLowerCase()}.`)
    }
  }

  useEffect(() => {
    if (enabled) void load()
    return () => { ++request.current }
  }, [enabled, viewer]) // eslint-disable-line react-hooks/exhaustive-deps

  async function change(subject: string, storeId: number, granted: boolean) {
    if (!enabled || !data || busy.current) return
    busy.current = true
    setSaving(true)
    const current = ++request.current
    const body = { auth0_sub: subject, store_id: storeId }
    try {
      if (granted) await client.post(path, body)
      else await client.delete(path, { data: body })
      if (current !== request.current) return
      setData(previous => previous && ({ ...previous, grants: [
        ...previous.grants.filter(grant => grant.auth0_sub !== subject || grant.store_id !== storeId),
        ...(granted ? [body] : []),
      ] }))
      message.success(`${name} ${granted ? 'granted' : 'revoked'}`)
    } catch (err: any) {
      if (current !== request.current) return
      message.error(err?.response?.data?.error ?? 'Could not confirm the access change. Reloading saved permissions.')
      await load()
    } finally {
      busy.current = false
      setSaving(false)
    }
  }

  return { data, error, saving, load, change }
}

interface FutureShift {
  id: number
  date: string
  start_time: string
  end_time: string
  store_code: string
  store_name: string
}

const ROLE_OPTIONS = [
  { value: 'viewer',  label: '查看者' },
  { value: 'staff',   label: '店员' },
  { value: 'manager', label: '经理' },
  { value: 'admin',   label: '管理员' },
]
const roleLabel = (role: string) => ROLE_OPTIONS.find(o => o.value === role)?.label ?? role

/** Full-spectrum picker with the curated calendar colors kept as shortcuts. */
function EmployeeColorPicker({ value, onChange, disabled = false }: {
  value: string
  onChange: (color: string) => void
  disabled?: boolean
}) {
  const [picking, setPicking] = useState(false)

  const handleScreenPick = async () => {
    type EyeDropperConstructor = new () => {
      open: () => Promise<{ sRGBHex: string }>
    }
    const EyeDropper = (window as typeof window & {
      EyeDropper?: EyeDropperConstructor
    }).EyeDropper

    if (!EyeDropper) {
      message.warning('此浏览器不支持屏幕取色，请使用最新版 Chrome 或 Edge')
      return
    }

    setPicking(true)
    try {
      const color = await pickScreenColor(() => new EyeDropper().open())
      onChange(color)
    } catch (error) {
      if (!isEyeDropperCancellation(error)) {
        message.error('屏幕取色失败，请重试')
      }
    } finally {
      setPicking(false)
    }
  }

  return (
    <div className="inline-flex items-center gap-1">
      <ColorPicker
        value={value || '#6366f1'}
        disabled={disabled || picking}
        disabledAlpha
        showText={(color) => normalizeEmployeeColor(color.toHexString())}
        presets={[{ label: '常用颜色 / Presets', colors: EMPLOYEE_PALETTE }]}
        onChangeComplete={(color) => onChange(normalizeEmployeeColor(color.toHexString()))}
      />
      <Button
        type="button"
        variant="outline"
        size="icon"
        className="h-9 w-9 shrink-0"
        disabled={disabled || picking}
        title="从屏幕取色 / Pick color from screen"
        aria-label="从屏幕取色 / Pick color from screen"
        onClick={handleScreenPick}
      >
        <Pipette className="h-3.5 w-3.5" />
      </Button>
    </div>
  )
}

interface EmpStoreEntry {
  empId:  number
  name:   string
  stores: string[]
  color:  string
  isSchedulable: number
  regularSince: string | null
  isTraining: boolean
}

interface StoreRow { code: string; name: string }

/** Delete confirmation listing the shifts still assigned from today on. Deleting keeps
 *  them on the schedule, so the manager can see what to reassign before confirming. */
function DeleteUserConfirm({ user, disabled, onConfirm }: {
  user: User
  disabled: boolean
  onConfirm: () => void
}) {
  const [shifts, setShifts] = useState<FutureShift[]>()
  const [error, setError] = useState('')
  const request = useRef(0)

  function loadShifts() {
    const current = ++request.current
    setShifts(undefined)
    setError('')
    client.get<{ shifts: FutureShift[] }>(`/users/${encodeURIComponent(user.id)}/future-shifts`)
      .then(r => { if (current === request.current) setShifts(r.data.shifts) })
      .catch((err: any) => {
        if (current === request.current) setError(err?.response?.data?.error ?? 'Could not load future shifts')
      })
  }

  const description = error ? <span role="alert">{error}</span>
    : !shifts ? <span>正在查看未来班次… Checking future shifts…</span>
    : !shifts.length ? <span>没有未来班次 · No future shifts are assigned.</span>
    : (
      <div className="pc-users-future-shifts">
        <p>这些班次会留在排班表上，请重新安排 · These shifts stay on the schedule so you can reassign them:</p>
        <ul aria-label={`${user.username} future shifts`}>
          {shifts.map(s => (
            <li key={s.id}>{s.date} · {s.store_code || '—'} · {s.start_time}–{s.end_time}</li>
          ))}
        </ul>
      </div>
    )

  return (
    <Popconfirm
      title={`删除用户 ${user.username}？`}
      description={description}
      // Anchored to the button's left edge: the detail's footer starts there.
      placement="topLeft"
      disabled={disabled}
      onOpenChange={open => { if (open) loadShifts() }}
      onConfirm={onConfirm}
      okButtonProps={{ danger: true, disabled: !shifts && !error }}
    >
      <Button variant="outline" className="pc-users-delete" disabled={disabled}>删除</Button>
    </Popconfirm>
  )
}

/** One person in the list: what you need to find them and spot what's off. */
function PersonRow({ user, entry, isSelf, selected, chevron, today, onSelect }: {
  user: User
  entry?: EmpStoreEntry
  isSelf: boolean
  selected: boolean
  chevron: boolean
  today: string
  onSelect: () => void
}) {
  const name = entry?.name || user.username
  const meta = [
    name !== user.username ? user.username : null,
    !entry ? '首次登录后排班' : entry.stores.length ? `排班 ${entry.stores.join(' ')}` : '未分配门店',
    user.is_active !== 1 ? '已停用' : null,
    user.is_active === 1 && entry?.isSchedulable === 0 ? '不排班' : null,
    !user.last_login ? '从未登录' : null,
  ].filter(Boolean).join(' · ')
  return <li>
    <button type="button" className="pc-person" aria-current={selected || undefined} onClick={onSelect}
      data-off={user.is_active !== 1 || undefined}>
      <span className="pc-person-dot" style={entry ? { background: entry.color } : undefined} aria-hidden="true" />
      <span className="pc-person-main">
        <strong>{name}{isSelf && <span className="pc-users-me">我</span>}</strong>
        <small>{meta}</small>
      </span>
      {isRegularOn(entry?.regularSince, today) && <RegularMark since={entry?.regularSince} />}
      {entry?.isTraining && <TrainingMark />}
      <span className="pc-role">{roleLabel(user.role)}</span>
      {chevron && <ChevronRight className="pc-person-chevron" aria-hidden="true" />}
    </button>
  </li>
}

function Group({ zh, en, children, hint }: { zh: string; en: string; children: ReactNode; hint?: ReactNode }) {
  return <section className="pc-ud-group" aria-label={`${zh} ${en}`}>
    <h3><span>{zh}</span> <small>{en}</small></h3>
    {children}
    {hint && <p className="pc-ud-hint">{hint}</p>}
  </section>
}

function Field({ label, en, children, hint }: { label: string; en?: string; children: ReactNode; hint?: ReactNode }) {
  return <div className="pc-ud-row">
    <span className="pc-ud-label">{label}{en && <small>{en}</small>}</span>
    <div className="pc-ud-control">{children}{hint && <p className="pc-ud-hint">{hint}</p>}</div>
  </div>
}

/** Everything for one person, in four labelled groups. Each change saves on its own. */
function UserDetail({
  user, entry, isSelf, isMobile, showName, stores, operations, view, accessSaving, today,
  savingColor, savingSchedulable, savingStatus,
  onRename, onRole, onPassword, onToggleActive, onDelete,
  onStores, onColor, onSchedulable, onStatus,
}: {
  user: User
  entry?: EmpStoreEntry
  isSelf: boolean
  isMobile: boolean
  /** Off on the phone page, whose top bar already carries the name. */
  showName: boolean
  stores: StoreRow[]
  operations: StoreGrantState
  view: StoreGrantState
  accessSaving: boolean
  today: string
  savingColor: boolean
  savingSchedulable: boolean
  savingStatus: boolean
  onRename: (name: string) => Promise<void>
  onRole: (role: string) => Promise<void>
  onPassword: (password: string) => Promise<boolean>
  onToggleActive: () => void
  onDelete: () => void
  onStores: (codes: string[]) => void
  onColor: (color: string) => void
  onSchedulable: (enabled: boolean) => void
  onStatus: (status: EmployeeStatus, regularSince?: string) => void
}) {
  const name = entry?.name || user.username
  const [nameDraft, setNameDraft] = useState(entry?.name ?? '')
  const [savingName, setSavingName] = useState(false)
  const [roleSaving, setRoleSaving] = useState(false)
  const [passwordOpen, setPasswordOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [savingPassword, setSavingPassword] = useState(false)
  useEffect(() => { setNameDraft(entry?.name ?? '') }, [entry?.name, user.id])
  useEffect(() => { setPasswordOpen(false); setPassword('') }, [user.id])

  const nameDirty = !!entry && nameDraft.trim() !== '' && nameDraft.trim() !== entry.name
  async function saveName() {
    if (!nameDirty) return
    setSavingName(true)
    try { await onRename(nameDraft.trim()) } finally { setSavingName(false) }
  }
  async function saveRole(role: string) {
    setRoleSaving(true)
    try { await onRole(role) } finally { setRoleSaving(false) }
  }
  async function savePassword() {
    if (password.length < 8) { message.error('密码至少8位 · At least 8 characters'); return }
    setSavingPassword(true)
    try {
      if (await onPassword(password)) { setPasswordOpen(false); setPassword('') }
    } finally { setSavingPassword(false) }
  }

  const grantCell = (grants: StoreGrantState, code: string, label: string) => {
    if (!grants.data) return <span className="pc-ud-na" title={grants.error ? 'Access unavailable' : 'Loading access…'}>{grants.error ? '—' : '…'}</span>
    const store = grants.data.stores.find(s => s.code === code)
    if (!store) return <span className="pc-ud-na" title="Not configured for this store">—</span>
    return <Checkbox aria-label={`${user.username}: ${store.code} ${label}`} title={store.name}
      disabled={accessSaving}
      checked={grants.data.grants.some(grant => grant.auth0_sub === user.id && grant.store_id === store.id)}
      onChange={event => void grants.change(user.id, store.id, event.target.checked)} />
  }
  // Managers and admins always see stock, so only other roles are picked.
  const alwaysSeesStock = user.role === 'manager' || user.role === 'admin'
  const regularNow = isRegularOn(entry?.regularSince, today)
  const status: EmployeeStatus = entry?.isTraining ? 'training' : entry?.regularSince ? 'regular' : 'none'
  const marks = <>{regularNow && <RegularMark since={entry?.regularSince} />}{entry?.isTraining && <TrainingMark />}</>

  return <div className="pc-ud">
    <header className="pc-ud-head">
      <span className="pc-person-dot pc-ud-dot" style={entry ? { background: entry.color } : undefined} aria-hidden="true" />
      <div className="pc-ud-title">
        {showName && <h2>{name}{isSelf && <span className="pc-users-me">我</span>}{marks}</h2>}
        <p>{!showName && marks}{user.username} · {roleLabel(user.role)} · {user.is_active === 1 ? '启用' : '已停用'}</p>
      </div>
    </header>

    <Group zh="资料" en="Profile">
      <Field label="显示名" en="Display name" hint={entry ? '排班日历里显示的名字 · Shown on the schedule' : undefined}>
        {entry
          ? <div className="pc-ud-inline">
            <Input aria-label="显示名 Display name" value={nameDraft} maxLength={120} placeholder="e.g. Jessi"
              onChange={event => setNameDraft(event.target.value)} onPressEnter={() => void saveName()} />
            {nameDirty && <Button onClick={() => void saveName()} disabled={savingName}>保存</Button>}
          </div>
          : <span className="pc-ud-muted">首次登录后可设置 · After their first sign-in</span>}
      </Field>
      <Field label="角色" en="Role" hint={isSelf ? '不能修改自己的角色（防止失去管理员权限）' : undefined}>
        <Segmented block aria-label="角色 Role" size={isMobile ? 'large' : 'middle'} value={user.role}
          disabled={isSelf || roleSaving} options={ROLE_OPTIONS} onChange={value => void saveRole(String(value))} />
      </Field>
      {entry && <Field label="颜色" en="Colour">
        <EmployeeColorPicker value={entry.color || '#6366f1'} disabled={savingColor} onChange={onColor} />
      </Field>}
      {entry && <Field label="状态" en="Status"
        hint={status === 'training'
          ? '排班和月工时都标 训 · Marked 训 in the schedule and monthly hours'
          : status === 'regular'
            ? regularNow
              ? '从这天起，排班和月工时都标 正 · Marked 正 from this date in the schedule and monthly hours'
              : `${entry.regularSince} 起标 正 · Marked 正 from ${entry.regularSince}`
            : '培训中标 训；转正从选的那天起标 正 · Training shows 训; 转正 shows 正 from the day you pick'}>
        <Segmented block aria-label="状态 Status" size={isMobile ? 'large' : 'middle'} value={status} disabled={savingStatus}
          options={[{ value: 'none', label: '无' }, { value: 'training', label: '培训中' }, { value: 'regular', label: '转正' }]}
          onChange={value => onStatus(value as EmployeeStatus, value === 'regular' ? today : undefined)} />
        {status === 'regular' && <div className="pc-ud-inline pc-ud-gap">
          <DatePicker aria-label="转正日期 Regular since" allowClear={false}
            value={dayjs(entry.regularSince)} disabled={savingStatus}
            inputReadOnly={isMobile} getPopupContainer={trigger => trigger.parentElement!}
            onChange={value => value && onStatus('regular', value.format('YYYY-MM-DD'))} />
          {regularNow && <span className="pc-ud-state"><RegularMark since={entry.regularSince} />已转正</span>}
        </div>}
      </Field>}
    </Group>

    <Group zh="门店" en="Stores" hint={<>
      排班：可以排这家店的班。门店操作：今日、库存等门店功能（在角色范围内）。库存查看：能看这家店的库存（库存、盘点、入店、产品和补货里的数字）。勾选马上保存。<br />
      Schedule: can be put on this store's shifts. Operations: Today, inventory and other store workflows within their role. Inventory view: sees this store's stock (库存, 盘点, 入店, stock numbers in Products and 补货). Ticks save immediately.
    </>}>
      {entry
        ? <Field label="参与排班" en="Can be scheduled">
          <span className="pc-ud-switch">
            <Switch aria-label="参与排班 Can be scheduled" checked={entry.isSchedulable !== 0} loading={savingSchedulable}
              onChange={onSchedulable} />
            <span>{entry.isSchedulable !== 0 ? '是' : '否'}</span>
          </span>
        </Field>
        : <p className="pc-ud-muted pc-ud-pad">排班设置在首次登录后出现 · Scheduling settings appear after their first sign-in.</p>}
      <table className="pc-ud-stores">
        <thead><tr>
          <th scope="col">门店</th>
          <th scope="col">排班<small>Schedule</small></th>
          <th scope="col">门店操作<small>Operations</small></th>
          <th scope="col">库存查看<small>Inventory view</small></th>
        </tr></thead>
        <tbody>{stores.map(store => <tr key={store.code}>
          <th scope="row"><b>{store.code}</b><small>{store.name !== store.code ? store.name : ''}</small></th>
          <td>{entry
            ? <Checkbox aria-label={`${user.username}: ${store.code} scheduling`} checked={entry.stores.includes(store.code)}
              onChange={event => onStores(event.target.checked
                ? [...entry.stores, store.code].sort()
                : entry.stores.filter(code => code !== store.code))} />
            : <span className="pc-ud-na" title="该用户首次登录后才能分配门店">—</span>}</td>
          <td>{grantCell(operations, store.code, 'operations access')}</td>
          <td>{alwaysSeesStock ? <span className="pc-ud-na">总是可见 Always</span> : grantCell(view, store.code, 'inventory view')}</td>
        </tr>)}</tbody>
      </table>
    </Group>

    <Group zh="账户" en="Account">
      <Field label="账户" en="Sign-in" hint={isSelf ? '不能停用自己的账户' : undefined}>
        <span className="pc-ud-switch">
          <Switch aria-label="账户启用 Account enabled" checked={user.is_active === 1} disabled={isSelf} onChange={onToggleActive} />
          <span>{user.is_active === 1 ? '启用' : '停用'}</span>
        </span>
      </Field>
      <Field label="密码" en="Password">
        {passwordOpen
          ? <div className="pc-ud-inline">
            <Input.Password aria-label="新密码 New password" autoFocus value={password} placeholder="至少8位 · At least 8"
              onChange={event => setPassword(event.target.value)} onPressEnter={() => void savePassword()} />
            <Button onClick={() => void savePassword()} disabled={savingPassword}>保存</Button>
            <Button variant="ghost" onClick={() => { setPasswordOpen(false); setPassword('') }}>取消</Button>
          </div>
          : <Button variant="outline" onClick={() => setPasswordOpen(true)}>重设密码 Reset password</Button>}
      </Field>
      <Field label="最后登录" en="Last sign-in">
        <span className={user.last_login ? undefined : 'pc-ud-muted'}>{user.last_login ? user.last_login.slice(0, 16) : '从未 · Never'}</span>
      </Field>
      <Field label="创建" en="Created">
        <span>{user.created_at ? user.created_at.slice(0, 16) : '—'}</span>
      </Field>
    </Group>

    <footer className="pc-ud-foot">
      <DeleteUserConfirm user={user} disabled={isSelf} onConfirm={onDelete} />
      <span className="pc-ud-hint">删除后，未来的班次会留在排班表上 · Their future shifts stay on the schedule.</span>
    </footer>
  </div>
}

export default function UsersPage({ embedded = false }: { embedded?: boolean }) {
  const isAdmin   = useHasRole('admin')
  const { user: me } = useAuth0()
  const isMobile  = useIsMobile()
  const navigate  = useNavigate()
  const [params, setParams] = useSearchParams()
  const { stores: allStores } = useAppStore()

  const [users, setUsers]     = useState<User[]>([])
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded]   = useState(false)
  const [query, setQuery]     = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm()
  const colorRequests = useRef(new Set<string>())
  const schedulableRequests = useRef(new Set<string>())
  const statusRequests = useRef(new Set<string>())
  const [savingColors, setSavingColors] = useState<Record<string, boolean>>({})
  const [savingSchedulable, setSavingSchedulable] = useState<Record<string, boolean>>({})
  const [savingStatus, setSavingStatus] = useState<Record<string, boolean>>({})
  const today = dayjs().format('YYYY-MM-DD')

  // Map: auth0_id → { empId, stores[] }
  const [empStoreMap, setEmpStoreMap] = useState<Record<string, EmpStoreEntry>>({})
  // Per-store grants an admin ticks: 门店操作权限 and 库存查看 (owner, 2026-11-01).
  const operations = useStoreGrants('/inventory/access', isAdmin, me?.sub, 'Store operations access')
  const view = useStoreGrants('/inventory/viewers', isAdmin, me?.sub, 'Inventory view')
  const accessSaving = operations.saving || view.saving
  const accessError = operations.error || view.error
  function loadAccess() { void operations.load(); void view.load() }

  function load() {
    if (!isAdmin) return
    setLoading(true)
    client.get('/users')
      .then(r => setUsers(r.data))
      .catch((err: any) => {
        message.error(err?.response?.data?.error ?? '加载用户列表失败')
      })
      .finally(() => { setLoading(false); setLoaded(true) })
  }

  function loadStores() {
    getEmployeeStores()
      .then(data => {
        const m: Record<string, EmpStoreEntry> = {}
        data.forEach(e => {
          m[e.auth0_id] = {
            empId: e.employee_id, name: e.name || '',
            stores: e.stores, color: e.color || '#6366f1',
            isSchedulable: e.is_schedulable ?? 1,
            regularSince: e.regular_since ?? null,
            isTraining: !!e.is_training,
          }
        })
        setEmpStoreMap(m)
      })
      .catch(() => {}) // non-fatal
  }

  useEffect(() => {
    load()
    loadStores()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin])

  function openNew() {
    form.resetFields(); setModalOpen(true)
  }

  async function handleCreate() {
    try {
      const vals = await form.validateFields()
      const resp = await client.post('/users', vals)
      if (resp.data?.warning) {
        message.warning(resp.data.warning, 6)
      } else {
        message.success('用户已创建')
      }
      setModalOpen(false)
      load()
      loadStores()
    } catch (err: any) {
      if (err?.errorFields) return
      message.error(err?.response?.data?.error ?? '操作失败')
    }
  }

  async function renameUser(u: User, name: string) {
    const entry = empStoreMap[u.id]
    if (!entry) return
    try {
      // Display name lives in the local employees table (shown on the schedule), not in Auth0.
      await renameEmployee(entry.empId, name)
      setEmpStoreMap(prev => updateEmployeeSetting(prev, u.id, { name }))
      message.success('显示名已保存')
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '显示名保存失败')
    }
  }

  async function changeRole(u: User, role: string) {
    // Changing your own role is blocked server-side (it would lock you out of this page).
    if (me?.sub === u.id || role === u.role) return
    try {
      await client.patch(`/users/${encodeURIComponent(u.id)}`, { role })
      setUsers(prev => prev.map(row => row.id === u.id ? { ...row, role } : row))
      message.success(`角色已改为 ${roleLabel(role)}`)
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '角色更新失败')
    }
  }

  async function changePassword(u: User, password: string) {
    try {
      await client.patch(`/users/${encodeURIComponent(u.id)}`, { password })
      message.success('密码已更新')
      return true
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '密码更新失败')
      return false
    }
  }

  async function toggleActive(u: User) {
    try {
      await client.patch(`/users/${encodeURIComponent(u.id)}`, { is_active: u.is_active ? 0 : 1 })
      load()
      loadStores() // blocking also deactivates the employee record
    } catch {
      message.error('操作失败')
    }
  }

  async function deleteUser(u: User) {
    try {
      await client.delete(`/users/${encodeURIComponent(u.id)}`)
      message.success('已删除')
      if (selectedId === u.id) closeDetail()
      load()
      loadStores()
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '删除失败')
    }
  }

  async function handleStoreChange(auth0Id: string, newCodes: string[]) {
    const entry = empStoreMap[auth0Id]
    if (!entry) return
    const previous = entry.stores
    try {
      await persistEmployeeSetting({
        optimistic: () => setEmpStoreMap(prev => updateEmployeeSetting(prev, auth0Id, { stores: newCodes })),
        persist: () => setEmployeeStores(entry.empId, newCodes),
        rollback: () => setEmpStoreMap(prev => updateEmployeeSetting(prev, auth0Id, { stores: previous })),
      })
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '门店分配失败')
    }
  }

  async function handleEmpColorChange(auth0Id: string, color: string) {
    const entry = empStoreMap[auth0Id]
    if (!entry || colorRequests.current.has(auth0Id)) return
    const previousColor = entry.color
    const normalized = normalizeEmployeeColor(color)
    colorRequests.current.add(auth0Id)
    setSavingColors(prev => ({ ...prev, [auth0Id]: true }))
    try {
      await persistEmployeeSetting({
        optimistic: () => setEmpStoreMap(prev => updateEmployeeSetting(prev, auth0Id, {
          color: normalized,
        })),
        persist: () => setEmployeeColor(entry.empId, normalized),
        rollback: () => setEmpStoreMap(prev => updateEmployeeSetting(prev, auth0Id, {
          color: previousColor,
        })),
      })
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '颜色更新失败')
    } finally {
      colorRequests.current.delete(auth0Id)
      setSavingColors(prev => ({ ...prev, [auth0Id]: false }))
    }
  }

  async function handleSchedulableChange(auth0Id: string, enabled: boolean) {
    const entry = empStoreMap[auth0Id]
    if (!entry || schedulableRequests.current.has(auth0Id)) return
    const previous = entry.isSchedulable
    schedulableRequests.current.add(auth0Id)
    setSavingSchedulable(prev => ({ ...prev, [auth0Id]: true }))
    try {
      await persistEmployeeSetting({
        optimistic: () => setEmpStoreMap(prev => updateEmployeeSetting(prev, auth0Id, {
          isSchedulable: enabled ? 1 : 0,
        })),
        persist: () => setEmployeeSchedulable(entry.empId, enabled),
        rollback: () => setEmpStoreMap(prev => updateEmployeeSetting(prev, auth0Id, {
          isSchedulable: previous,
        })),
      })
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '排班设置更新失败')
    } finally {
      schedulableRequests.current.delete(auth0Id)
      setSavingSchedulable(prev => ({ ...prev, [auth0Id]: false }))
    }
  }

  async function handleStatusChange(auth0Id: string, status: EmployeeStatus, regularSince?: string) {
    const entry = empStoreMap[auth0Id]
    if (!entry || statusRequests.current.has(auth0Id)) return
    const previous = { isTraining: entry.isTraining, regularSince: entry.regularSince }
    statusRequests.current.add(auth0Id)
    setSavingStatus(prev => ({ ...prev, [auth0Id]: true }))
    try {
      await persistEmployeeSetting({
        optimistic: () => setEmpStoreMap(prev => updateEmployeeSetting(prev, auth0Id, {
          isTraining: status === 'training', regularSince: status === 'regular' ? regularSince ?? null : null,
        })),
        persist: () => setEmployeeStatus(entry.empId, status, regularSince),
        rollback: () => setEmpStoreMap(prev => updateEmployeeSetting(prev, auth0Id, previous)),
      })
      message.success(status === 'regular' ? `转正日期已保存 · ${regularSince}` : status === 'training' ? '已设为培训中' : '状态已清除')
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '状态保存失败')
    } finally {
      statusRequests.current.delete(auth0Id)
      setSavingStatus(prev => ({ ...prev, [auth0Id]: false }))
    }
  }

  // Store rows for the detail grid: every real store, plus any store a grant list knows about.
  const storeRows: StoreRow[] = allStores
    .filter(s => s.code !== 'ALL')
    .map(s => ({ code: s.code, name: s.name || s.code }))
  for (const s of [...(operations.data?.stores ?? []), ...(view.data?.stores ?? [])]) {
    if (!storeRows.some(row => row.code === s.code)) storeRows.push({ code: s.code, name: s.name || s.code })
  }

  // Enabled accounts first, in the server's order; stopped accounts sink to the bottom.
  const needle = query.trim().toLowerCase()
  const people = [...users]
    .sort((a, b) => (a.is_active === 1 ? 0 : 1) - (b.is_active === 1 ? 0 : 1))
    .filter(u => !needle || u.username.toLowerCase().includes(needle)
      || (empStoreMap[u.id]?.name ?? '').toLowerCase().includes(needle))

  const selectedId = params.get('employee')
  // Desktop always shows someone; the phone shows the list until a person is tapped.
  const selected = users.find(u => u.id === selectedId) ?? (isMobile ? undefined : people[0])

  function select(u: User) {
    setParams(prev => {
      const next = new URLSearchParams(prev)
      next.set('employee', u.id)
      return next
    }, { replace: !isMobile })
  }
  function closeDetail() {
    // Back to the list: undo the push when there is one, so the phone's back gesture stays in step.
    if ((window.history.state?.idx ?? 0) > 0) navigate(-1)
    else setParams(prev => { const next = new URLSearchParams(prev); next.delete('employee'); return next }, { replace: true })
  }

  if (!isAdmin) {
    return <span style={{ color: '#6b7280' }}>仅管理员可访问此页面</span>
  }

  const actions = <>
    <Button variant="outline" aria-label="刷新" disabled={accessSaving} onClick={() => { load(); loadStores(); void loadAccess() }}>
      <RefreshCw className="h-4 w-4 mr-1" />刷新
    </Button>
    <Button aria-label="新建用户" onClick={openNew}><Plus className="h-4 w-4 mr-1" />新建用户 New user</Button>
  </>
  const showDetailOnly = isMobile && !!selected
  const selectedName = selected ? (empStoreMap[selected.id]?.name || selected.username) : ''

  const header = embedded ? null
    : showDetailOnly
      ? <PageHeader title={selectedName} sub="员工 Users" back={{ label: '员工', to: '/users' }} />
      : isMobile
        ? <PageHeader title="员工" sub={`Users · ${users.length} 个账户 · 只有管理员`} />
        : <PageHeader title="员工" sub={`Users · ${users.length} 个账户 · 只有管理员`} actions={actions} />

  const list = <section className="pc-users-list" aria-label="员工列表 People">
    <label className="pc-users-search">
      <Search className="h-4 w-4" aria-hidden="true" />
      <input type="search" value={query} onChange={event => setQuery(event.target.value)}
        placeholder="搜索员工 Search" aria-label="搜索员工 Search people" />
    </label>
    {loading && !loaded
      ? <ul className="pc-person-skeleton" aria-label="加载中 Loading">{[0, 1, 2, 3].map(i => <li key={i}><i /><span><b /><small /></span></li>)}</ul>
      : !people.length
        ? <p className="pc-users-empty">{users.length ? `没有叫 “${query.trim()}” 的员工 · No one matches` : '暂无用户 · No accounts yet. 新建用户 adds the first one.'}</p>
        : <ul>{people.map(u => <PersonRow key={u.id} user={u} entry={empStoreMap[u.id]} isSelf={me?.sub === u.id}
          selected={!isMobile && selected?.id === u.id} chevron={isMobile} today={today} onSelect={() => select(u)} />)}</ul>}
  </section>

  const detail = selected && <section className="pc-users-detail" aria-label={`${selectedName} 详情 Details`}>
    {isMobile && embedded && <button type="button" className="pc-users-back" onClick={closeDetail}>
      <ChevronLeft className="h-4 w-4" aria-hidden="true" />员工 Users
    </button>}
    <UserDetail
      key={selected.id}
      user={selected}
      entry={empStoreMap[selected.id]}
      isSelf={me?.sub === selected.id}
      isMobile={isMobile}
      showName={!isMobile || embedded}
      stores={storeRows}
      operations={operations}
      view={view}
      accessSaving={accessSaving}
      today={today}
      savingColor={!!savingColors[selected.id]}
      savingSchedulable={!!savingSchedulable[selected.id]}
      savingStatus={!!savingStatus[selected.id]}
      onRename={name => renameUser(selected, name)}
      onRole={role => changeRole(selected, role)}
      onPassword={password => changePassword(selected, password)}
      onToggleActive={() => toggleActive(selected)}
      onDelete={() => deleteUser(selected)}
      onStores={codes => handleStoreChange(selected.id, codes)}
      onColor={color => handleEmpColorChange(selected.id, color)}
      onSchedulable={enabled => handleSchedulableChange(selected.id, enabled)}
      onStatus={(status, date) => handleStatusChange(selected.id, status, date)}
    />
  </section>

  return (
    <div className="pc-users">
      {header}
      {(embedded || isMobile) && !showDetailOnly && <div className="pc-users-actions">{actions}</div>}
      {accessError && <Alert type="error" showIcon message={accessError} style={{ marginBottom: 16 }}
        action={<Button variant="outline" disabled={accessSaving} onClick={() => void loadAccess()}>Retry access</Button>} />}

      {isMobile
        ? showDetailOnly ? detail : list
        : <div className="pc-users-layout">{list}{detail}</div>}

      <Dialog open={modalOpen} onOpenChange={(o) => !o && setModalOpen(false)}>
        <DialogContent style={{ maxWidth: 400 }}>
          <DialogHeader>
            <DialogTitle>新建用户</DialogTitle>
          </DialogHeader>

          <Form form={form} layout="vertical" size="small">
            <Form.Item
              name="username"
              label="用户名"
              rules={[{ required: true, message: '请输入用户名' }]}
            >
              <Input placeholder="登录用户名" />
            </Form.Item>
            <Form.Item
              name="role"
              label="角色"
              rules={[{ required: true, message: '请选择角色' }]}
            >
              <Select
                options={ROLE_OPTIONS}
                getPopupContainer={(trigger) => trigger.parentElement!}
              />
            </Form.Item>
            <Form.Item
              name="password"
              label="密码"
              rules={[{ required: true, message: '请设置密码' }, { min: 8, message: '密码至少8位' }]}
              style={{ marginBottom: 0 }}
            >
              <Input.Password placeholder="至少8位" />
            </Form.Item>
          </Form>

          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>取消</Button>
            <Button onClick={handleCreate}>创建</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
