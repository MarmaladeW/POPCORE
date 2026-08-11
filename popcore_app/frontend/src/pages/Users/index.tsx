import { useState, useEffect } from 'react'
import {
  Table, Space, Popconfirm, Form, Input, Select,
  Switch, message, Tag,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { Plus, RefreshCw } from 'lucide-react'
import { useAuth0 } from '@auth0/auth0-react'
import { useHasRole } from '../../auth/useRole'
import { useIsMobile } from '../../hooks/useIsMobile'
import client from '../../api/client'
import { useAppStore } from '../../store'
import { getEmployeeStores, setEmployeeStores } from '../Schedule/scheduleApi'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
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

const ROLE_OPTIONS = [
  { value: 'viewer',  label: '查看者' },
  { value: 'staff',   label: '店员' },
  { value: 'manager', label: '经理' },
  { value: 'admin',   label: '管理员' },
]

const ROLE_BADGE_STYLE: Record<string, React.CSSProperties> = {
  admin:   { background: '#fee2e2', color: '#991b1b', borderColor: '#fca5a5' },
  manager: { background: '#ffedd5', color: '#9a3412', borderColor: '#fdba74' },
  staff:   { background: '#dbeafe', color: '#1e40af', borderColor: '#93c5fd' },
  viewer:  { background: '#f3f4f6', color: '#374151', borderColor: '#d1d5db' },
}

const ROLE_AVATAR: Record<string, { bg: string; fg: string }> = {
  admin:   { bg: '#fee2e2', fg: '#991b1b' },
  manager: { bg: '#ffedd5', fg: '#9a3412' },
  staff:   { bg: '#dbeafe', fg: '#1e40af' },
  viewer:  { bg: '#f3f4f6', fg: '#6b7280' },
}

const ME_BADGE_STYLE: React.CSSProperties = {
  background: '#dcfce7', color: '#166534', borderColor: '#86efac',
}

// Store badge colours consistent with task spec
const STORE_TAG_COLORS: Record<string, string> = {
  DT: 'blue',
  MK: 'green',
  MT: 'orange',
}

interface EmpStoreEntry {
  empId:  number
  stores: string[]
  color:  string
}

/** Store badges (read-only). Top-level component: defining these inside
 *  UsersPage gave them a new identity every render, so React remounted the
 *  select on each state change and the dropdown closed after every pick. */
function StoreBadges({ codes }: { codes: string[] }) {
  if (codes.length === 0) {
    return <span style={{ fontSize: 11, color: '#9ca3af' }}>未分配</span>
  }
  return (
    <>
      {codes.map(code => (
        <Tag key={code} color={STORE_TAG_COLORS[code] ?? 'default'} style={{ fontSize: 11, marginRight: 2 }}>
          {code}
        </Tag>
      ))}
    </>
  )
}

/** Store edit control (manager+). */
function StoreSelect({ entry, storeOptions, onChange }: {
  entry?: EmpStoreEntry
  storeOptions: { value: string; label: string }[]
  onChange: (vals: string[]) => void
}) {
  if (!entry) {
    return (
      <span style={{ fontSize: 11, color: '#9ca3af' }} title="该用户首次登录后才能分配门店">—</span>
    )
  }
  return (
    <Select
      mode="multiple"
      size="small"
      style={{ minWidth: 120 }}
      value={entry.stores}
      onChange={onChange}
      options={storeOptions}
      placeholder="未分配"
      maxTagCount={3}
    />
  )
}

export default function UsersPage() {
  const isAdmin   = useHasRole('admin')
  const isManager = useHasRole('manager')
  const { user: me } = useAuth0()
  const isMobile  = useIsMobile()
  const { stores: allStores } = useAppStore()

  const [users, setUsers]     = useState<User[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editUser, setEditUser]   = useState<User | null>(null)
  const [form] = Form.useForm()

  // Map: auth0_id → { empId, stores[] }
  const [empStoreMap, setEmpStoreMap] = useState<Record<string, EmpStoreEntry>>({})

  function load() {
    if (!isAdmin) return
    setLoading(true)
    client.get('/users')
      .then(r => setUsers(r.data))
      .catch((err: any) => {
        message.error(err?.response?.data?.error ?? '加载用户列表失败')
      })
      .finally(() => setLoading(false))
  }

  function loadStores() {
    getEmployeeStores()
      .then(data => {
        const m: Record<string, EmpStoreEntry> = {}
        data.forEach(e => {
          m[e.auth0_id] = { empId: e.employee_id, stores: e.stores, color: e.color || '#6366f1' }
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

  const editingSelf = !!editUser && me?.sub === editUser.id

  function openNew() { setEditUser(null); form.resetFields(); setModalOpen(true) }
  function openEdit(u: User) {
    setEditUser(u)
    // Reset first — otherwise a password typed for a previous user (or in the
    // create dialog) lingers in the field and gets saved onto this user.
    form.resetFields()
    form.setFieldsValue({ username: u.username, role: u.role })
    setModalOpen(true)
  }

  async function handleOk() {
    try {
      const vals = await form.validateFields()
      if (editUser) {
        const patch: any = {}
        // Changing your own role is blocked server-side (it would lock you
        // out of this page) — only send it for other users.
        if (me?.sub !== editUser.id) patch.role = vals.role
        if (vals.password) patch.password = vals.password
        if (Object.keys(patch).length === 0) {
          setModalOpen(false)
          return
        }
        await client.patch(`/users/${encodeURIComponent(editUser.id)}`, patch)
        message.success('更新成功')
      } else {
        const resp = await client.post('/users', vals)
        if (resp.data?.warning) {
          message.warning(resp.data.warning, 6)
        } else {
          message.success('用户已创建')
        }
      }
      setModalOpen(false)
      load()
    } catch (err: any) {
      if (err?.errorFields) return
      message.error(err?.response?.data?.error ?? '操作失败')
    }
  }

  async function toggleActive(u: User) {
    try {
      await client.patch(`/users/${encodeURIComponent(u.id)}`, { is_active: u.is_active ? 0 : 1 })
      load()
    } catch {
      message.error('操作失败')
    }
  }

  async function deleteUser(u: User) {
    try {
      await client.delete(`/users/${encodeURIComponent(u.id)}`)
      message.success('已删除')
      load()
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '删除失败')
    }
  }

  async function handleStoreChange(auth0Id: string, newCodes: string[]) {
    const entry = empStoreMap[auth0Id]
    if (!entry) return
    try {
      await setEmployeeStores(entry.empId, newCodes)
      setEmpStoreMap(prev => ({
        ...prev,
        [auth0Id]: { ...prev[auth0Id], stores: newCodes },
      }))
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '门店分配失败')
    }
  }

  async function handleEmpColorChange(auth0Id: string, color: string) {
    const entry = empStoreMap[auth0Id]
    if (!entry) return
    setEmpStoreMap(prev => ({
      ...prev,
      [auth0Id]: { ...prev[auth0Id], color },
    }))
    try {
      await client.patch(`/employees/${entry.empId}/color`, { color })
    } catch {
      message.error('颜色更新失败')
    }
  }

  const storeOptions = allStores
    .filter(s => s.code !== 'ALL')
    .map(s => ({ value: s.code, label: s.code }))

  const columns: ColumnsType<User> = [
    {
      title: '用户名',
      dataIndex: 'username',
      render: (v, r) => (
        <Space>
          <span style={{ fontWeight: r.is_active === 1 ? 600 : 400 }}>{v}</span>
          {me?.sub === r.id && <Badge variant="outline" style={ME_BADGE_STYLE}>我</Badge>}
        </Space>
      ),
    },
    {
      title: '角色',
      dataIndex: 'role',
      width: 100,
      render: v => (
        <Badge variant="outline" style={ROLE_BADGE_STYLE[v] ?? ROLE_BADGE_STYLE.viewer}>
          {ROLE_OPTIONS.find(o => o.value === v)?.label ?? v}
        </Badge>
      ),
    },
    {
      title: '门店',
      key: 'stores',
      width: 180,
      render: (_, r) => isManager
        ? (
          <StoreSelect
            entry={empStoreMap[r.id]}
            storeOptions={storeOptions}
            onChange={(vals) => handleStoreChange(r.id, vals)}
          />
        )
        : <StoreBadges codes={empStoreMap[r.id]?.stores ?? []} />,
    },
    {
      title: '颜色',
      key: 'color',
      width: 70,
      align: 'center' as const,
      render: (_: unknown, r: User) => {
        const entry = empStoreMap[r.id]
        if (!entry) return null
        const color = entry.color || '#6366f1'
        return isManager ? (
          <label style={{ cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4 }} title="更改员工颜色">
            <span style={{ width: 16, height: 16, borderRadius: '50%', background: color, display: 'inline-block', border: '1px solid #e5e7eb', flexShrink: 0 }} />
            <input
              type="color"
              value={color}
              onChange={e => handleEmpColorChange(r.id, e.target.value)}
              style={{ width: 0, height: 0, padding: 0, border: 'none', opacity: 0, position: 'absolute', pointerEvents: 'none' }}
            />
          </label>
        ) : (
          <span style={{ width: 16, height: 16, borderRadius: '50%', background: color, display: 'inline-block', border: '1px solid #e5e7eb' }} />
        )
      },
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 90,
      align: 'center',
      render: (v, r) => (
        <Switch
          checked={v === 1}
          size="small"
          disabled={me?.sub === r.id}
          onChange={() => toggleActive(r)}
        />
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 160,
      render: v => v ? v.slice(0, 16) : '-',
    },
    {
      title: '最后登录',
      dataIndex: 'last_login',
      width: 160,
      render: v => v ? v.slice(0, 16) : <span style={{ color: '#9ca3af' }}>从未</span>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      render: (_, r) => (
        <Space>
          <Button size="sm" variant="outline" onClick={() => openEdit(r)}>编辑</Button>
          <Popconfirm
            title={`删除用户 ${r.username}？`}
            disabled={me?.sub === r.id}
            onConfirm={() => deleteUser(r)}
            okButtonProps={{ danger: true }}
          >
            <Button size="sm" variant="destructive" disabled={me?.sub === r.id}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  if (!isAdmin) {
    return <span style={{ color: '#6b7280' }}>仅管理员可访问此页面</span>
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <Button onClick={openNew}><Plus className="h-4 w-4 mr-1" />新建用户</Button>
        <Button variant="outline" onClick={() => { load(); loadStores() }}>
          <RefreshCw className="h-4 w-4 mr-1" />刷新
        </Button>
      </div>

      {isMobile ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {loading ? (
            <span style={{ color: '#9ca3af', fontSize: 14 }}>加载中…</span>
          ) : users.length === 0 ? (
            <span style={{ color: '#9ca3af', fontSize: 14 }}>暂无用户</span>
          ) : users.map(u => {
            const av = ROLE_AVATAR[u.role] ?? ROLE_AVATAR.viewer
            return (
              <Card key={u.id}>
                <CardContent style={{ padding: '12px 14px' }}>
                  {/* Row 1: avatar initials + username + role badge */}
                  <div className="flex items-center gap-2">
                    <div style={{
                      width: 36, height: 36, borderRadius: '50%', flexShrink: 0,
                      background: av.bg, color: av.fg,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontWeight: 700, fontSize: 13,
                    }}>
                      {u.username.slice(0, 2).toUpperCase()}
                    </div>
                    <span className="font-medium text-sm flex-1 min-w-0 truncate">
                      {u.username}
                      {me?.sub === u.id && (
                        <Badge variant="outline" style={ME_BADGE_STYLE} className="ml-1.5 align-middle">我</Badge>
                      )}
                    </span>
                    <Badge variant="outline" style={ROLE_BADGE_STYLE[u.role] ?? ROLE_BADGE_STYLE.viewer}>
                      {ROLE_OPTIONS.find(o => o.value === u.role)?.label ?? u.role}
                    </Badge>
                  </div>

                  {/* Row 2: store badges */}
                  <div className="flex items-center gap-1 mt-1 pl-11 flex-wrap">
                    {isManager
                      ? (
                        <StoreSelect
                          entry={empStoreMap[u.id]}
                          storeOptions={storeOptions}
                          onChange={(vals) => handleStoreChange(u.id, vals)}
                        />
                      )
                      : <StoreBadges codes={empStoreMap[u.id]?.stores ?? []} />
                    }
                  </div>

                  {/* Row 3: employee color (managers only) */}
                  {isManager && empStoreMap[u.id] && (
                    <div className="flex items-center gap-1.5 mt-1 pl-11">
                      <label className="flex items-center gap-1.5 cursor-pointer" title="更改员工颜色">
                        <span style={{
                          width: 14, height: 14, borderRadius: '50%',
                          background: empStoreMap[u.id].color || '#6366f1',
                          display: 'inline-block', border: '1px solid #e5e7eb', flexShrink: 0,
                        }} />
                        <input
                          type="color"
                          value={empStoreMap[u.id].color || '#6366f1'}
                          onChange={e => handleEmpColorChange(u.id, e.target.value)}
                          style={{ width: 0, height: 0, padding: 0, border: 'none', opacity: 0, position: 'absolute', pointerEvents: 'none' }}
                        />
                        <span className="text-xs text-muted-foreground">员工颜色</span>
                      </label>
                    </div>
                  )}

                  {/* Row 4: last login */}
                  <div className="text-xs text-muted-foreground mt-1 pl-11">
                    最后登录：{u.last_login ? u.last_login.slice(0, 16) : '从未'}
                  </div>

                  {/* Row 4: status toggle (left) + actions (right) */}
                  <div className="flex items-center mt-2 pl-11">
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <Switch
                        checked={u.is_active === 1}
                        size="small"
                        disabled={me?.sub === u.id}
                        onChange={() => toggleActive(u)}
                      />
                      <span>{u.is_active === 1 ? '启用' : '禁用'}</span>
                    </div>
                    <div className="ml-auto flex gap-2">
                      <Button size="sm" variant="outline" onClick={() => openEdit(u)}>编辑</Button>
                      <Popconfirm
                        title={`删除用户 ${u.username}？`}
                        disabled={me?.sub === u.id}
                        onConfirm={() => deleteUser(u)}
                        okButtonProps={{ danger: true }}
                      >
                        <Button size="sm" variant="destructive" disabled={me?.sub === u.id}>删除</Button>
                      </Popconfirm>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      ) : (
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={users}
          columns={columns}
          pagination={false}
        />
      )}

      <Dialog open={modalOpen} onOpenChange={(o) => !o && setModalOpen(false)}>
        <DialogContent style={{ maxWidth: 400 }}>
          <DialogHeader>
            <DialogTitle>{editUser ? '编辑用户' : '新建用户'}</DialogTitle>
          </DialogHeader>

          <Form form={form} layout="vertical" size="small">
            <Form.Item
              name="username"
              label="用户名"
              rules={[{ required: !editUser, message: '请输入用户名' }]}
            >
              <Input disabled={!!editUser} placeholder="登录用户名" />
            </Form.Item>
            <Form.Item
              name="role"
              label="角色"
              rules={[{ required: true, message: '请选择角色' }]}
              extra={editingSelf ? '不能修改自己的角色（防止失去管理员权限）' : undefined}
            >
              <Select
                options={ROLE_OPTIONS}
                disabled={editingSelf}
                getPopupContainer={(trigger) => trigger.parentElement!}
              />
            </Form.Item>
            <Form.Item
              name="password"
              label={editUser ? '新密码（留空则不修改）' : '密码'}
              rules={editUser ? [] : [{ required: true, message: '请设置密码' }, { min: 8, message: '密码至少8位' }]}
              style={{ marginBottom: 0 }}
            >
              <Input.Password placeholder={editUser ? '留空则不修改' : '至少8位'} />
            </Form.Item>
          </Form>

          <DialogFooter>
            <Button variant="outline" onClick={() => setModalOpen(false)}>取消</Button>
            <Button onClick={handleOk}>{editUser ? '保存' : '创建'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
