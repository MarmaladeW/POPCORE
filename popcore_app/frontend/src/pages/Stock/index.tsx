import { useState, useEffect, useCallback } from 'react'
import {
  Table, Input, Select, Button, Space, Tag, Tabs, Popconfirm,
  message, Typography, Row, Col, Statistic, Card,
} from 'antd'
import { ReloadOutlined, ExportOutlined, DeleteOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'
import { useAppStore } from '../../store'
import RoleGuard from '../../components/RoleGuard'
import RestockModal from './RestockModal'
import BatchStockModal from './BatchStockModal'

const { Search } = Input
const { Text } = Typography

interface StockRow {
  id: number
  sku: string
  jizhanming: string
  name_cn_en: string
  ip_series: string
  product_type: string
  boxes_per_dan: number | null
  upstairs_dan: number
  instore_dan: number
  last_updated: string
  stock_notes: string
}

interface Transaction {
  id: number
  product_id: number
  txn_type: string
  dan_qty: number
  location: string
  date: string
  notes: string
  created_at: string
  jizhanming: string
  sku: string
}

interface Summary {
  products_tracked: number
  total_upstairs_dan: number
  total_instore_dan: number
  low_stock_count: number
  out_of_stock_count: number
}

const TXN_LABELS: Record<string, string> = {
  ru_dian:          '入店',
  restock_upstairs: '入库',
  adjust:           '调整',
}

export default function StockPage() {
  const { series } = useAppStore()
  const [stock, setStock]       = useState<StockRow[]>([])
  const [txns, setTxns]         = useState<Transaction[]>([])
  const [summary, setSummary]   = useState<Summary | null>(null)
  const [loading, setLoading]   = useState(false)
  const [q, setQ]               = useState('')
  const [filterSeries, setFilterSeries] = useState('')
  const [selected, setSelected] = useState<React.Key[]>([])
  const [restockOpen, setRestockOpen] = useState(false)
  const [batchOpen, setBatchOpen]     = useState(false)

  const loadStock = useCallback(() => {
    setLoading(true)
    const params: Record<string, string> = {}
    if (q) params.q = q
    if (filterSeries) params.series = filterSeries
    Promise.all([
      client.get('/stock', { params }),
      client.get('/stock/summary'),
    ]).then(([sResp, sumResp]) => {
      setStock(sResp.data)
      setSummary(sumResp.data)
    }).finally(() => setLoading(false))
  }, [q, filterSeries])

  const loadTxns = useCallback(() => {
    client.get('/stock/transactions', { params: { limit: 100 } })
      .then(r => setTxns(r.data))
  }, [])

  useEffect(() => { loadStock() }, [loadStock])

  async function handleDeleteRows() {
    try {
      await client.delete('/stock/rows', { data: selected })
      message.success(`已移除 ${selected.length} 条库存`)
      setSelected([])
      loadStock()
    } catch {
      message.error('删除失败')
    }
  }

  async function handleExport() {
    const params = new URLSearchParams()
    if (filterSeries) params.set('series', filterSeries)
    if (q) params.set('q', q)
    window.location.href = `/api/stock/export?${params}`
  }

  const stockColumns: ColumnsType<StockRow> = [
    { title: 'SKU', dataIndex: 'sku', width: 110, render: v => <Text code>{v}</Text> },
    { title: '记账名', dataIndex: 'jizhanming', width: 130 },
    {
      title: '系列', dataIndex: 'ip_series', width: 110,
      render: v => v ? <Tag color="blue">{v}</Tag> : '-',
    },
    {
      title: '楼上(端)', dataIndex: 'upstairs_dan', width: 90, align: 'center',
      render: v => <Tag color={v === 0 ? 'red' : 'green'}>{v}</Tag>,
      sorter: (a, b) => a.upstairs_dan - b.upstairs_dan,
    },
    {
      title: '店内(端)', dataIndex: 'instore_dan', width: 90, align: 'center',
      render: v => <Tag color={v === 0 ? 'default' : 'cyan'}>{v}</Tag>,
      sorter: (a, b) => a.instore_dan - b.instore_dan,
    },
    {
      title: '每端盒数', dataIndex: 'boxes_per_dan', width: 80, align: 'center',
      render: v => v ?? '-',
    },
    { title: '更新时间', dataIndex: 'last_updated', width: 150 },
    { title: '备注', dataIndex: 'stock_notes', ellipsis: true },
  ]

  const txnColumns: ColumnsType<Transaction> = [
    { title: '日期', dataIndex: 'date', width: 100 },
    { title: 'SKU', dataIndex: 'sku', width: 100, render: v => <Text code>{v}</Text> },
    { title: '记账名', dataIndex: 'jizhanming', width: 120 },
    {
      title: '操作', dataIndex: 'txn_type', width: 80,
      render: v => <Tag color={v === 'adjust' ? 'orange' : 'green'}>{TXN_LABELS[v] ?? v}</Tag>,
    },
    {
      title: '数量', dataIndex: 'dan_qty', width: 70, align: 'right',
      render: v => <Text type={v < 0 ? 'danger' : 'success'}>{v > 0 ? `+${v}` : v}</Text>,
    },
    { title: '位置', dataIndex: 'location', width: 120 },
    { title: '备注', dataIndex: 'notes', ellipsis: true },
  ]

  const tabItems = [
    {
      key: 'overview',
      label: '库存概览',
      children: (
        <div>
          {summary && (
            <Row gutter={16} style={{ marginBottom: 16 }}>
              {[
                { title: '追踪产品', value: summary.products_tracked },
                { title: '楼上(端)', value: summary.total_upstairs_dan },
                { title: '店内(端)', value: summary.total_instore_dan },
                { title: '低库存', value: summary.low_stock_count, valueStyle: { color: '#fa8c16' } },
                { title: '缺货', value: summary.out_of_stock_count, valueStyle: { color: '#cf1322' } },
              ].map(s => (
                <Col key={s.title} xs={12} sm={8} md={5}>
                  <Card size="small">
                    <Statistic {...s} />
                  </Card>
                </Col>
              ))}
            </Row>
          )}
          <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
            <Col flex="auto">
              <Search
                placeholder="搜索产品"
                allowClear
                onSearch={setQ}
                onChange={e => { if (!e.target.value) setQ('') }}
              />
            </Col>
            <Col>
              <Select
                placeholder="系列"
                allowClear
                style={{ width: 140 }}
                options={series.map(s => ({ value: s, label: s }))}
                onChange={v => setFilterSeries(v ?? '')}
              />
            </Col>
            <RoleGuard minRole="staff">
              <Col>
                <Space>
                  <Button type="primary" onClick={() => setRestockOpen(true)}>
                    入库 / 入店
                  </Button>
                  <Button onClick={() => setBatchOpen(true)}>批量导入</Button>
                </Space>
              </Col>
            </RoleGuard>
            <RoleGuard minRole="manager">
              <Col>
                <Space>
                  <Button icon={<ExportOutlined />} onClick={handleExport}>导出</Button>
                  {selected.length > 0 && (
                    <Popconfirm
                      title={`移除 ${selected.length} 条库存记录？`}
                      onConfirm={handleDeleteRows}
                      okButtonProps={{ danger: true }}
                    >
                      <Button danger icon={<DeleteOutlined />}>移除 ({selected.length})</Button>
                    </Popconfirm>
                  )}
                </Space>
              </Col>
            </RoleGuard>
          </Row>
          <Table
            rowKey="id"
            size="small"
            loading={loading}
            dataSource={stock}
            columns={stockColumns}
            rowSelection={{ selectedRowKeys: selected, onChange: setSelected }}
            pagination={{ pageSize: 60, showTotal: t => `共 ${t} 条` }}
            scroll={{ x: 800 }}
          />
        </div>
      ),
    },
    {
      key: 'history',
      label: '操作记录',
      children: (
        <div>
          <Button
            icon={<ReloadOutlined />}
            style={{ marginBottom: 12 }}
            onClick={loadTxns}
          >
            刷新
          </Button>
          <Table
            rowKey="id"
            size="small"
            dataSource={txns}
            columns={txnColumns}
            pagination={{ pageSize: 50, showTotal: t => `共 ${t} 条` }}
            scroll={{ x: 700 }}
          />
        </div>
      ),
    },
  ]

  return (
    <div>
      <Tabs
        items={tabItems}
        onChange={k => { if (k === 'history') loadTxns() }}
      />
      <RestockModal
        open={restockOpen}
        onClose={() => setRestockOpen(false)}
        onDone={() => { setRestockOpen(false); loadStock() }}
      />
      <BatchStockModal
        open={batchOpen}
        onClose={() => setBatchOpen(false)}
        onDone={() => { setBatchOpen(false); loadStock() }}
      />
    </div>
  )
}
