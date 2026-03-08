import { useState, useEffect } from 'react'
import {
  Table, Button, Space, Tag, Popconfirm, Modal, Form, Input, Select,
  Switch, message, Typography, Badge,
} from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useAuth0 } from '@auth0/auth0-react'
import { useHasRole } from '../../auth/useRole'
import client from '../../api/client'

const { Text } = Typography

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

const ROLE_COLORS: Record<string, string> = {
  admin:   'red',
  manager: 'orange',
  staff:   'blue',
  viewer:  'default',
}

export default function UsersPage() {
  const isAdmin = useHasRole('admin')
  const { user: me } = useAuth0()
  const [users, setUsers]   = useState<User[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editUser, setEditUser]   = useState<User | null>(null)
  const [form] = Form.useForm()

  function load() {
    if (!isAdmin) return
    setLoading(true)
    client.get('/users').then(r => setUsers(r.data)).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [isAdmin])

  function openNew()  { setEditUser(null); form.resetFields(); setModalOpen(true) }
  function openEdit(u: User) {
    setEditUser(u)
    form.setFieldsValue({ username: u.username, role: u.role })
    setModalOpen(true)
  }

  async function handleOk() {
    try {
      const vals = await form.validateFields()
      if (editUser) {
        const patch: any = { role: vals.role }
        if (vals.password) patch.password = vals.password
        await client.patch(`/users/${encodeURIComponent(editUser.id)}`, patch)
        message.success('更新成功')
      } else {
        await client.post('/users', vals)
        message.success('用户已创建')
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

  const columns: ColumnsType<User> = [
    {
      title: '用户名',
      dataIndex: 'username',
      render: (v, r) => (
        <Space>
          <Text strong={r.is_active === 1}>{v}</Text>
          {me?.sub === r.id && <Tag color="green">我</Tag>}
        </Space>
      ),
    },
    {
      title: '角色',
      dataIndex: 'role',
      width: 100,
      render: v => <Tag color={ROLE_COLORS[v] ?? 'default'}>{ROLE_OPTIONS.find(o => o.value === v)?.label ?? v}</Tag>,
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
      render: v => v ? v.slice(0, 16) : <Text type="secondary">从未</Text>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      render: (_, r) => (
        <Space>
          <Button size="small" onClick={() => openEdit(r)}>编辑</Button>
          <Popconfirm
            title={`删除用户 ${r.username}？`}
            disabled={me?.sub === r.id}
            onConfirm={() => deleteUser(r)}
            okButtonProps={{ danger: true }}
          >
            <Button size="small" danger disabled={me?.sub === r.id}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  if (!isAdmin) {
    return <Text type="secondary">仅管理员可访问此页面</Text>
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openNew}>新建用户</Button>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </Space>

      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={users}
        columns={columns}
        pagination={false}
      />

      <Modal
        title={editUser ? '编辑用户' : '新建用户'}
        open={modalOpen}
        onOk={handleOk}
        onCancel={() => setModalOpen(false)}
        okText={editUser ? '保存' : '创建'}
        width={400}
      >
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
          >
            <Select options={ROLE_OPTIONS} />
          </Form.Item>
          <Form.Item
            name="password"
            label={editUser ? '新密码（留空则不修改）' : '密码'}
            rules={editUser ? [] : [{ required: true, message: '请设置密码' }, { min: 8, message: '密码至少8位' }]}
          >
            <Input.Password placeholder={editUser ? '留空则不修改' : '至少8位'} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
