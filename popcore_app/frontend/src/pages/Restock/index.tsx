import { useState, useEffect, useCallback } from 'react'
import {
  Tabs, Typography, Tag, Spin, Button, Table, Popconfirm,
  Empty, Modal, message, Grid, Space,
} from 'antd'
import {
  PlusOutlined, AuditOutlined, StarOutlined,
  HistoryOutlined, FolderOpenOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import client from '../../api/client'
import { useHasRole } from '../../auth/useRole'
import EveningCheckStep from './EveningCheckStep'
import BestsellerManage from './BestsellerManage'
import HistoryTab from './HistoryTab'
import SessionModal from './SessionModal'

const { Title } = Typography
const { useBreakpoint } = Grid

// ── Shared types (imported by child components) ───────────────────────────────

export interface RestockItem {
  id: number
  product_id: number
  session_id?: number
  requested_qty: number
  warehouse_stock_snapshot: number
  found_qty: number | null
  pick_status: 'pending' | 'found' | 'not_found'
  sku: string
  jizhanming: string
  name_cn_en: string
  ip_series: string
  product_type: string
  upstairs_dan: number
  instore_dan: number
}

export interface RestockSession {
  id: number
  date: string
  status: 'pending' | 'submitted' | 'picking' | 'completed'
  created_at: string
  submitted_at: string | null
  completed_at: string | null
  items: RestockItem[]
}

interface SessionSummary {
  id: number
  date: string
  status: 'pending' | 'submitted' | 'picking' | 'completed'
  created_at: string
  submitted_at: string | null
  completed_at: string | null
  item_count: number
  total_requested: number
  total_found: number
}

// ── Constants ─────────────────────────────────────────────────────────────────

const STATUS_LABELS: Record<string, string> = {
  pending:   '录入中',
  submitted: '待拣货',
  picking:   '拣货中',
  completed: '已完成',
}

const STATUS_COLORS: Record<string, string> = {
  pending:   'processing',
  submitted: 'warning',
  picking:   'orange',
  completed: 'success',
}

// ── Today's session list ──────────────────────────────────────────────────────

function TodaySessions() {
  const [sessions, setSessions]   = useState<SessionSummary[]>([])
  const [loading, setLoading]     = useState(true)
  const [creating, setCreating]   = useState(false)
  const [openId, setOpenId]       = useState<number | null>(null)
  const [deletingId, setDeletingId] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await client.get<SessionSummary[]>('/restock/sessions/today')
      setSessions(data)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function handleCreate() {
    setCreating(true)
    try {
      const { data } = await client.post('/restock/sessions')
      await load()
      setOpenId(data.id)
    } catch {
      message.error('创建失败，请重试')
    } finally {
      setCreating(false)
    }
  }

  async function handleDelete(s: SessionSummary) {
    if (s.status === 'completed') {
      Modal.confirm({
        title:   '撤销已完成的补货？',
        content: '此操作将撤销库存变动（恢复仓库库存），且无法恢复。确认继续？',
        okText:  '确认撤销',
        okButtonProps: { danger: true },
        onOk: () => doDelete(s.id),
      })
    } else {
      await doDelete(s.id)
    }
  }

  async function doDelete(id: number) {
    setDeletingId(id)
    try {
      await client.delete(`/restock/session/${id}`)
      message.success('已撤销')
      await load()
    } catch {
      message.error('撤销失败，请重试')
    } finally {
      setDeletingId(null)
    }
  }

  const columns: ColumnsType<SessionSummary> = [
    {
      title: '创建时间', dataIndex: 'created_at', width: 80,
      render: v => dayjs(v).format('HH:mm'),
    },
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: v => <Tag color={STATUS_COLORS[v]}>{STATUS_LABELS[v]}</Tag>,
    },
    {
      title: '商品种类', dataIndex: 'item_count', width: 80, align: 'center',
    },
    {
      title: '申请箱数', dataIndex: 'total_requested', width: 80, align: 'center',
    },
    {
      title: '实际找到', dataIndex: 'total_found', width: 80, align: 'center',
      render: (v, r) => r.status === 'completed'
        ? <span style={{ color: v < r.total_requested ? '#EF4444' : '#10B981', fontWeight: 600 }}>{v}</span>
        : <span style={{ color: '#9ca3af' }}>—</span>,
    },
    {
      title: '操作', width: 130, align: 'center',
      render: (_, s) => (
        <Space size={6}>
          <Button
            size="small"
            icon={<FolderOpenOutlined />}
            onClick={() => setOpenId(s.id)}
          >
            打开
          </Button>
          {s.status !== 'completed'
            ? (
              <Popconfirm
                title="确认撤销这条补货记录？"
                okText="撤销" okButtonProps={{ danger: true }}
                onConfirm={() => doDelete(s.id)}
              >
                <Button size="small" danger loading={deletingId === s.id}>撤销</Button>
              </Popconfirm>
            )
            : (
              <Button
                size="small" danger
                loading={deletingId === s.id}
                onClick={() => handleDelete(s)}
              >
                撤销
              </Button>
            )
          }
        </Space>
      ),
    },
  ]

  return (
    <>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ color: '#6b7280', fontSize: 13 }}>今日 {dayjs().format('MM月DD日')} 补货记录</span>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          loading={creating}
          onClick={handleCreate}
        >
          新建补货
        </Button>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
      ) : sessions.length === 0 ? (
        <Empty
          description="今日暂无补货记录"
          style={{ padding: '40px 0' }}
        >
          <Button type="primary" icon={<PlusOutlined />} loading={creating} onClick={handleCreate}>
            新建补货
          </Button>
        </Empty>
      ) : (
        <Table
          rowKey="id"
          columns={columns}
          dataSource={sessions}
          size="small"
          pagination={false}
        />
      )}

      <SessionModal
        sessionId={openId}
        onClose={() => { setOpenId(null); load() }}
      />
    </>
  )
}

// ── Page root ─────────────────────────────────────────────────────────────────

export default function RestockPage() {
  const screens  = useBreakpoint()
  const isMobile = !screens.md
  const isAdmin  = useHasRole('admin')

  const tabItems = [
    {
      key:      'today',
      label:    <span><PlusOutlined /> 今日补货</span>,
      children: <TodaySessions />,
    },
    {
      key:      'evening',
      label:    <span><AuditOutlined /> 晚盘核查</span>,
      children: <EveningCheckStep />,
    },
    {
      key:      'history',
      label:    <span><HistoryOutlined /> 历史记录</span>,
      children: <HistoryTab />,
    },
    ...(isAdmin ? [{
      key:      'bestsellers',
      label:    <span><StarOutlined /> 畅销品管理</span>,
      children: <BestsellerManage />,
    }] : []),
  ]

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>夜间补货 & 晚盘</Title>
      </div>

      <Tabs
        defaultActiveKey="today"
        items={tabItems}
        size={isMobile ? 'small' : 'middle'}
        style={{ background: '#fff', padding: isMobile ? '0 8px 8px' : '0 16px 16px', borderRadius: 8 }}
      />
    </div>
  )
}
