import { useState, useEffect, useCallback } from 'react'
import {
  Tabs, Table, DatePicker, Switch, Tag, Typography, Space, Button,
  Modal, Descriptions, Empty, Spin,
} from 'antd'
import { HistoryOutlined, AuditOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import client from '../../api/client'

const { RangePicker } = DatePicker
const { Text } = Typography

// ── Types ─────────────────────────────────────────────────────────────────────

interface RestockSessionRow {
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

interface RestockItemRow {
  id: number
  product_id: number
  requested_qty: number
  warehouse_stock_snapshot: number
  found_qty: number | null
  pick_status: 'pending' | 'found' | 'not_found'
  sku: string
  jizhanming: string
  name_cn_en: string
  ip_series: string
  product_type: string
}

interface InventoryCheckRow {
  id: number
  date: string
  product_id: number
  sku: string
  jizhanming: string
  name_cn_en: string
  ip_series: string
  product_type: string
  theoretical_qty: number
  actual_qty: number
  discrepancy: number
  base_check_date: string
  created_at: string
}

// ── Constants ─────────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  pending:   'processing',
  submitted: 'warning',
  picking:   'orange',
  completed: 'success',
}
const STATUS_LABELS: Record<string, string> = {
  pending:   '录入中',
  submitted: '待拣货',
  picking:   '拣货中',
  completed: '已完成',
}
const PICK_COLORS: Record<string, string> = {
  pending:   'default',
  found:     'success',
  not_found: 'error',
}
const PICK_LABELS: Record<string, string> = {
  pending:   '未处理',
  found:     '已找到',
  not_found: '未找到',
}

// ── Restock History ───────────────────────────────────────────────────────────

function RestockHistory() {
  const [sessions, setSessions]     = useState<RestockSessionRow[]>([])
  const [total, setTotal]           = useState(0)
  const [page, setPage]             = useState(1)
  const [loading, setLoading]       = useState(false)
  const [dateRange, setDateRange]   = useState<[string, string] | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [detail, setDetail]         = useState<RestockSessionRow | null>(null)
  const [items, setItems]           = useState<RestockItemRow[]>([])
  const [detailLoading, setDetailLoading] = useState(false)

  const PAGE_SIZE = 20

  const load = useCallback(async (p = page, dr = dateRange) => {
    setLoading(true)
    try {
      const params: Record<string, string | number> = { page: p, page_size: PAGE_SIZE }
      if (dr) { params.date_from = dr[0]; params.date_to = dr[1] }
      const { data } = await client.get('/history/restock', { params })
      setSessions(data.sessions)
      setTotal(data.total)
    } finally {
      setLoading(false)
    }
  }, [page, dateRange])

  useEffect(() => { load() }, [load])

  async function openDetail(row: RestockSessionRow) {
    setDetail(row)
    setDetailOpen(true)
    setDetailLoading(true)
    try {
      const { data } = await client.get(`/restock/session/${row.id}`)
      setItems(data.items ?? [])
    } finally {
      setDetailLoading(false)
    }
  }

  const columns: ColumnsType<RestockSessionRow> = [
    {
      title: '日期', dataIndex: 'date', width: 110,
      render: v => <Text strong>{v}</Text>,
    },
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: v => <Tag color={STATUS_COLORS[v]}>{STATUS_LABELS[v]}</Tag>,
    },
    { title: '商品数', dataIndex: 'item_count', width: 80, align: 'center' },
    { title: '申请箱数', dataIndex: 'total_requested', width: 90, align: 'center' },
    {
      title: '实际找到', dataIndex: 'total_found', width: 90, align: 'center',
      render: (v, r) => (
        <span style={{ color: v < r.total_requested ? '#EF4444' : '#10B981', fontWeight: 600 }}>
          {v}
        </span>
      ),
    },
    {
      title: '完成时间', dataIndex: 'completed_at', width: 160,
      render: v => v ? dayjs(v).format('MM-DD HH:mm') : <Text type="secondary">—</Text>,
    },
    {
      title: '操作', width: 80, align: 'center',
      render: (_, row) => (
        <Button size="small" onClick={() => openDetail(row)}>详情</Button>
      ),
    },
  ]

  const itemColumns: ColumnsType<RestockItemRow> = [
    { title: 'SKU', dataIndex: 'sku', width: 110 },
    { title: '机展名', dataIndex: 'jizhanming', ellipsis: true },
    { title: '申请', dataIndex: 'requested_qty', width: 60, align: 'center' },
    {
      title: '仓库快照', dataIndex: 'warehouse_stock_snapshot', width: 80, align: 'center',
      render: v => <Text type="secondary">{v}</Text>,
    },
    {
      title: '找到', dataIndex: 'found_qty', width: 60, align: 'center',
      render: v => v ?? <Text type="secondary">—</Text>,
    },
    {
      title: '状态', dataIndex: 'pick_status', width: 80,
      render: v => <Tag color={PICK_COLORS[v]}>{PICK_LABELS[v]}</Tag>,
    },
  ]

  return (
    <>
      <Space style={{ marginBottom: 16, flexWrap: 'wrap' }}>
        <RangePicker
          onChange={(_, strs) => {
            const dr = strs[0] && strs[1] ? [strs[0], strs[1]] as [string, string] : null
            setDateRange(dr)
            setPage(1)
            load(1, dr)
          }}
        />
        <Button icon={<ReloadOutlined />} onClick={() => load(page, dateRange)}>刷新</Button>
      </Space>

      <Table
        rowKey="id"
        columns={columns}
        dataSource={sessions}
        loading={loading}
        size="small"
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total,
          showTotal: t => `共 ${t} 条`,
          onChange: p => { setPage(p); load(p, dateRange) },
        }}
        locale={{ emptyText: <Empty description="暂无补货记录" /> }}
      />

      <Modal
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={null}
        title={`补货详情 — ${detail?.date}`}
        width={720}
      >
        {detail && (
          <Descriptions size="small" bordered column={3} style={{ marginBottom: 16 }}>
            <Descriptions.Item label="状态">
              <Tag color={STATUS_COLORS[detail.status]}>{STATUS_LABELS[detail.status]}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="申请箱数">{detail.total_requested}</Descriptions.Item>
            <Descriptions.Item label="实际找到">
              <span style={{ color: detail.total_found < detail.total_requested ? '#EF4444' : '#10B981', fontWeight: 600 }}>
                {detail.total_found}
              </span>
            </Descriptions.Item>
            <Descriptions.Item label="提交时间" span={3}>
              {detail.submitted_at ? dayjs(detail.submitted_at).format('YYYY-MM-DD HH:mm') : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="完成时间" span={3}>
              {detail.completed_at ? dayjs(detail.completed_at).format('YYYY-MM-DD HH:mm') : '—'}
            </Descriptions.Item>
          </Descriptions>
        )}
        {detailLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
        ) : (
          <Table
            rowKey="id"
            columns={itemColumns}
            dataSource={items}
            size="small"
            pagination={false}
            scroll={{ y: 360 }}
          />
        )}
      </Modal>
    </>
  )
}

// ── Inventory Check History ───────────────────────────────────────────────────

function InventoryCheckHistory() {
  const [rows, setRows]           = useState<InventoryCheckRow[]>([])
  const [loading, setLoading]     = useState(false)
  const [dateRange, setDateRange] = useState<[string, string] | null>(null)
  const [onlyDiff, setOnlyDiff]   = useState(false)

  const load = useCallback(async (dr = dateRange, od = onlyDiff) => {
    setLoading(true)
    try {
      const params: Record<string, string | number> = {}
      if (dr) { params.date_from = dr[0]; params.date_to = dr[1] }
      if (od) params.only_discrepancy = 1
      const { data } = await client.get('/history/inventory-check', { params })
      setRows(data)
    } finally {
      setLoading(false)
    }
  }, [dateRange, onlyDiff])

  useEffect(() => { load() }, [load])

  const columns: ColumnsType<InventoryCheckRow> = [
    {
      title: '日期', dataIndex: 'date', width: 110,
      render: v => <Text strong>{v}</Text>,
    },
    { title: 'SKU', dataIndex: 'sku', width: 110 },
    { title: '机展名', dataIndex: 'jizhanming', ellipsis: true },
    { title: '系列', dataIndex: 'ip_series', width: 120, ellipsis: true },
    {
      title: '理论库存', dataIndex: 'theoretical_qty', width: 90, align: 'center',
      render: v => <Text type="secondary">{v}</Text>,
    },
    { title: '实盘数', dataIndex: 'actual_qty', width: 80, align: 'center' },
    {
      title: '差异', dataIndex: 'discrepancy', width: 70, align: 'center',
      render: v => v !== 0
        ? <span style={{ color: '#EF4444', fontWeight: 700 }}>{v > 0 ? `+${v}` : v}</span>
        : <Text type="secondary">0</Text>,
    },
    {
      title: '基准日期', dataIndex: 'base_check_date', width: 110,
      render: v => <Text type="secondary">{v ?? '—'}</Text>,
    },
    {
      title: '录入时间', dataIndex: 'created_at', width: 140,
      render: v => dayjs(v).format('MM-DD HH:mm'),
    },
  ]

  return (
    <>
      <Space style={{ marginBottom: 16, flexWrap: 'wrap' }} align="center">
        <RangePicker
          onChange={(_, strs) => {
            const dr = strs[0] && strs[1] ? [strs[0], strs[1]] as [string, string] : null
            setDateRange(dr)
            load(dr, onlyDiff)
          }}
        />
        <Space>
          <span style={{ fontSize: 13 }}>仅看有差异</span>
          <Switch
            checked={onlyDiff}
            onChange={v => { setOnlyDiff(v); load(dateRange, v) }}
          />
        </Space>
        <Button icon={<ReloadOutlined />} onClick={() => load(dateRange, onlyDiff)}>刷新</Button>
      </Space>

      <Table
        rowKey="id"
        columns={columns}
        dataSource={rows}
        loading={loading}
        size="small"
        pagination={{ pageSize: 50, showTotal: t => `共 ${t} 条` }}
        locale={{ emptyText: <Empty description="暂无晚盘记录" /> }}
        rowClassName={r => r.discrepancy !== 0 ? 'row-discrepancy' : ''}
      />
    </>
  )
}

// ── Combined History Tab ──────────────────────────────────────────────────────

export default function HistoryTab() {
  const subTabs = [
    {
      key:      'restock',
      label:    <span><HistoryOutlined /> 补货记录</span>,
      children: <RestockHistory />,
    },
    {
      key:      'inventory',
      label:    <span><AuditOutlined /> 晚盘记录</span>,
      children: <InventoryCheckHistory />,
    },
  ]

  return (
    <Tabs
      defaultActiveKey="restock"
      items={subTabs}
      size="small"
      style={{ marginTop: 4 }}
    />
  )
}
