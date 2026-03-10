import { useState, useEffect, useCallback } from 'react'
import {
  Table, Input, Select, Button, Space, Tag, Tabs, Popconfirm,
  message, Typography, Row, Col, Card, Grid, Spin,
} from 'antd'
import {
  ReloadOutlined, ExportOutlined, DeleteOutlined,
  EditOutlined, CheckOutlined, CloseOutlined,
  InboxOutlined, ArrowUpOutlined, WarningOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'
import { useAppStore } from '../../store'
import RoleGuard from '../../components/RoleGuard'
import RestockModal from './RestockModal'
import BatchStockModal from './BatchStockModal'

const { Search } = Input
const { Text, Title } = Typography
const { useBreakpoint } = Grid

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
  price: number | null
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
  products_tracked:   number
  total_upstairs_dan: number
  total_instore_dan:  number
  low_stock_count:    number
  out_of_stock_count: number
}

const TXN_LABELS: Record<string, string> = {
  ru_dian:          'In-Store',
  restock_upstairs: 'Restock',
  adjust:           'Adjust',
}

const TXN_COLORS: Record<string, string> = {
  ru_dian:          'blue',
  restock_upstairs: 'green',
  adjust:           'orange',
}

function stockStatus(total: number) {
  if (total === 0)   return <Tag color="red">Out of Stock</Tag>
  if (total <= 3)    return <Tag color="orange">Low Stock</Tag>
  if (total <= 10)   return <Tag color="default">Normal</Tag>
  return <Tag color="green">Well Stocked</Tag>
}

export default function StockPage() {
  const screens  = useBreakpoint()
  const isMobile = !screens.md
  const { series } = useAppStore()
  const [stock,    setStock]   = useState<StockRow[]>([])
  const [txns,     setTxns]    = useState<Transaction[]>([])
  const [summary,  setSummary] = useState<Summary | null>(null)
  const [loading,  setLoading] = useState(false)
  const [q,        setQ]       = useState('')
  const [filterSeries, setFilterSeries] = useState('')
  const [selected, setSelected] = useState<React.Key[]>([])
  const [restockOpen, setRestockOpen] = useState(false)
  const [batchOpen,   setBatchOpen]   = useState(false)
  const [quickProduct, setQuickProduct] = useState<StockRow | null>(null)
  const [editingNotes, setEditingNotes] = useState<{ id: number; value: string } | null>(null)

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
      message.success(`Removed ${selected.length} stock records`)
      setSelected([])
      loadStock()
    } catch {
      message.error('Delete failed')
    }
  }

  function handleExport() {
    const params = new URLSearchParams()
    if (filterSeries) params.set('series', filterSeries)
    if (q) params.set('q', q)
    window.location.href = `/api/stock/export?${params}`
  }

  async function saveNotes(productId: number, notes: string) {
    try {
      await client.patch(`/stock/${productId}`, { notes })
      setEditingNotes(null)
      setStock(prev => prev.map(r => r.id === productId ? { ...r, stock_notes: notes } : r))
    } catch {
      message.error('Failed to save notes')
    }
  }

  const stockColumns: ColumnsType<StockRow> = [
    {
      title: 'SKU', dataIndex: 'sku', width: 110,
      render: v => <Text style={{ fontFamily: 'monospace', fontSize: 11, color: '#6b7280' }}>{v}</Text>,
    },
    {
      title: 'Product',
      render: (_, r) => (
        <div>
          <div style={{ fontWeight: 500, color: '#111827', fontSize: 13 }}>{r.jizhanming || '—'}</div>
          {r.product_type && <div style={{ fontSize: 11, color: '#9ca3af' }}>{r.product_type}</div>}
        </div>
      ),
    },
    {
      title: 'Series', dataIndex: 'ip_series', width: 120,
      render: v => v ? <Tag color="blue" style={{ fontSize: 11 }}>{v}</Tag> : '—',
    },
    {
      title: <><ArrowUpOutlined /> Upstairs</>,
      dataIndex: 'upstairs_dan',
      width: 90, align: 'center',
      sorter: (a, b) => a.upstairs_dan - b.upstairs_dan,
      render: v => (
        <span style={{ fontWeight: 600, color: v === 0 ? '#ef4444' : '#374151' }}>{v}</span>
      ),
    },
    {
      title: <><InboxOutlined /> In-Store</>,
      dataIndex: 'instore_dan',
      width: 90, align: 'center',
      sorter: (a, b) => a.instore_dan - b.instore_dan,
      render: v => (
        <span style={{ fontWeight: 600, color: v === 0 ? '#9ca3af' : '#374151' }}>{v}</span>
      ),
    },
    {
      title: 'Total',
      width: 80, align: 'center',
      sorter: (a, b) => (a.upstairs_dan + a.instore_dan) - (b.upstairs_dan + b.instore_dan),
      render: (_, r) => {
        const t = r.upstairs_dan + r.instore_dan
        return (
          <span style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: 28, height: 28, borderRadius: '50%', fontWeight: 700, fontSize: 13,
            background: t === 0 ? '#fef2f2' : t <= 3 ? '#fffbeb' : '#f0fdf4',
            color:      t === 0 ? '#ef4444' : t <= 3 ? '#d97706' : '#16a34a',
          }}>{t}</span>
        )
      },
    },
    {
      title: 'Stock Value',
      width: 110, align: 'right',
      render: (_, r) => {
        const total = r.upstairs_dan + r.instore_dan
        const val = r.price ? total * r.price : null
        return val != null
          ? <Text style={{ color: '#6366F1', fontSize: 12 }}>CA${val.toFixed(2)}</Text>
          : <Text type="secondary">—</Text>
      },
    },
    {
      title: 'Status',
      width: 120,
      render: (_, r) => stockStatus(r.upstairs_dan + r.instore_dan),
    },
    {
      title: 'Actions',
      key: 'action', width: 90, align: 'center',
      render: (_, r) => (
        <RoleGuard minRole="staff">
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => { setQuickProduct(r); setRestockOpen(true) }}
          >
            Adjust
          </Button>
        </RoleGuard>
      ),
    },
  ]

  const txnColumns: ColumnsType<Transaction> = [
    { title: 'Date', dataIndex: 'date', width: 100 },
    { title: 'SKU',  dataIndex: 'sku',  width: 110, render: v => <Text code>{v}</Text> },
    { title: 'Product', dataIndex: 'jizhanming', width: 130 },
    {
      title: 'Type', dataIndex: 'txn_type', width: 100,
      render: v => <Tag color={TXN_COLORS[v] ?? 'default'}>{TXN_LABELS[v] ?? v}</Tag>,
    },
    {
      title: 'Qty', dataIndex: 'dan_qty', width: 70, align: 'right',
      render: v => <Text type={v < 0 ? 'danger' : 'success'}>{v > 0 ? `+${v}` : v}</Text>,
    },
    { title: 'Location', dataIndex: 'location', width: 120 },
    { title: 'Notes', dataIndex: 'notes', ellipsis: true },
  ]

  const summaryCards = summary ? [
    { label: 'Upstairs Total', value: summary.total_upstairs_dan, color: '#6366F1',  icon: <ArrowUpOutlined /> },
    { label: 'In-Store Total', value: summary.total_instore_dan,  color: '#10B981',  icon: <InboxOutlined /> },
    { label: 'Total Stock Value', value: `CA$ ${(
        stock.reduce((acc, r) => acc + (r.price ?? 0) * (r.upstairs_dan + r.instore_dan), 0)
      ).toLocaleString('en-CA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
      color: '#6366F1', icon: null },
    { label: 'Low/Out of Stock', value: summary.low_stock_count + summary.out_of_stock_count, color: '#ef4444', icon: <WarningOutlined /> },
  ] : []

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>Stock Management</Title>
          <Text style={{ color: '#6b7280' }}>Manage upstairs warehouse and in-store inventory</Text>
        </div>
        <RoleGuard minRole="staff">
          <Button type="primary" icon={<EditOutlined />} onClick={() => { setQuickProduct(null); setRestockOpen(true) }}>
            Adjust Stock
          </Button>
        </RoleGuard>
      </div>

      {/* Summary cards */}
      {summary && (
        <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
          {summaryCards.map(c => (
            <Col key={c.label} xs={12} sm={6}>
              <Card style={{ borderRadius: 10, borderLeft: `4px solid ${c.color}` }} bodyStyle={{ padding: '16px 20px' }}>
                <div style={{ fontSize: 12, color: '#9ca3af', marginBottom: 4 }}>{c.label}</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: '#111827' }}>{c.value}</div>
              </Card>
            </Col>
          ))}
        </Row>
      )}

      {/* Tabs */}
      <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)', overflow: 'hidden' }}>
        <Tabs
          style={{ padding: '0 20px' }}
          items={[
            {
              key: 'overview',
              label: 'Current Stock',
              children: (
                <div>
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
                    <Search
                      placeholder="Search products"
                      allowClear
                      style={{ width: 220 }}
                      onSearch={setQ}
                      onChange={e => { if (!e.target.value) setQ('') }}
                    />
                    <Select
                      placeholder="All Series"
                      allowClear
                      style={{ width: 140 }}
                      options={series.map(s => ({ value: s, label: s }))}
                      onChange={v => setFilterSeries(v ?? '')}
                    />
                    <div style={{ marginLeft: isMobile ? 0 : 'auto', display: 'flex', gap: 6 }}>
                      <RoleGuard minRole="staff">
                        <Button onClick={() => setBatchOpen(true)}>Batch Import</Button>
                      </RoleGuard>
                      <RoleGuard minRole="manager">
                        <Button icon={<ExportOutlined />} onClick={handleExport}>Export</Button>
                        {selected.length > 0 && (
                          <Popconfirm
                            title={`Remove ${selected.length} stock records?`}
                            onConfirm={handleDeleteRows}
                            okButtonProps={{ danger: true }}
                          >
                            <Button danger icon={<DeleteOutlined />}>Remove ({selected.length})</Button>
                          </Popconfirm>
                        )}
                      </RoleGuard>
                    </div>
                  </div>
                  {isMobile ? (
                    <Spin spinning={loading}>
                      {!loading && stock.length === 0 && (
                        <div style={{ textAlign: 'center', color: '#9ca3af', padding: '24px 16px', fontSize: 13 }}>No stock records</div>
                      )}
                      {stock.map(row => {
                        const total = row.upstairs_dan + row.instore_dan
                        return (
                          <div key={row.id} style={{ padding: '12px 16px', borderBottom: '1px solid #f5f5f5' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6 }}>
                              <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ fontWeight: 500, fontSize: 13, color: '#111827' }}>{row.jizhanming || '—'}</div>
                                <div style={{ fontSize: 11, color: '#9ca3af', fontFamily: 'monospace' }}>{row.sku}</div>
                                {row.ip_series && <Tag color="blue" style={{ fontSize: 10, marginTop: 2 }}>{row.ip_series}</Tag>}
                              </div>
                              <div style={{ flexShrink: 0, marginLeft: 8 }}>{stockStatus(total)}</div>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                              <span style={{ fontSize: 12, color: '#6b7280' }}>
                                <ArrowUpOutlined /> <strong>{row.upstairs_dan}</strong>
                                <span style={{ margin: '0 6px' }}>·</span>
                                <InboxOutlined /> <strong>{row.instore_dan}</strong>
                                <span style={{ margin: '0 6px' }}>·</span>
                                合计 <strong style={{ color: total === 0 ? '#ef4444' : '#374151' }}>{total}</strong>
                              </span>
                              <RoleGuard minRole="staff">
                                <Button
                                  size="small"
                                  icon={<EditOutlined />}
                                  onClick={() => { setQuickProduct(row); setRestockOpen(true) }}
                                  style={{ marginLeft: 'auto' }}
                                >
                                  Adjust
                                </Button>
                              </RoleGuard>
                            </div>
                          </div>
                        )
                      })}
                    </Spin>
                  ) : (
                    <Table
                      rowKey="id"
                      size="middle"
                      loading={loading}
                      dataSource={stock}
                      columns={stockColumns}
                      rowSelection={{ selectedRowKeys: selected, onChange: setSelected }}
                      pagination={{ pageSize: 50, showTotal: t => `${t} products` }}
                      scroll={{ x: 1000 }}
                    />
                  )}
                </div>
              ),
            },
            {
              key: 'history',
              label: `Transaction History (${txns.length})`,
              children: (
                <div>
                  <Button icon={<ReloadOutlined />} style={{ marginBottom: 12 }} onClick={loadTxns}>
                    Refresh
                  </Button>
                  {isMobile ? (
                    <div>
                      {txns.length === 0 && (
                        <div style={{ textAlign: 'center', color: '#9ca3af', padding: '24px 16px', fontSize: 13 }}>No transactions</div>
                      )}
                      {txns.map(txn => (
                        <div key={txn.id} style={{ padding: '12px 16px', borderBottom: '1px solid #f5f5f5' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                            <span style={{ fontWeight: 500, fontSize: 13, color: '#111827' }}>{txn.jizhanming || '—'}</span>
                            <Tag color={TXN_COLORS[txn.txn_type] ?? 'default'}>{TXN_LABELS[txn.txn_type] ?? txn.txn_type}</Tag>
                          </div>
                          <div style={{ display: 'flex', gap: 12, fontSize: 12, color: '#6b7280', alignItems: 'center', flexWrap: 'wrap' }}>
                            <span>{txn.date}</span>
                            <Text code style={{ fontSize: 11 }}>{txn.sku}</Text>
                            <Text type={txn.dan_qty < 0 ? 'danger' : 'success'} style={{ fontWeight: 600 }}>
                              {txn.dan_qty > 0 ? `+${txn.dan_qty}` : txn.dan_qty}
                            </Text>
                            {txn.location && <span>{txn.location}</span>}
                          </div>
                          {txn.notes && <div style={{ fontSize: 12, color: '#9ca3af', marginTop: 4 }}>{txn.notes}</div>}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <Table
                      rowKey="id"
                      size="middle"
                      dataSource={txns}
                      columns={txnColumns}
                      pagination={{ pageSize: 50, showTotal: t => `${t} transactions` }}
                      scroll={{ x: 700 }}
                    />
                  )}
                </div>
              ),
            },
          ]}
          onChange={k => { if (k === 'history') loadTxns() }}
        />
      </div>

      <RestockModal
        open={restockOpen}
        initialProduct={quickProduct ? { id: quickProduct.id, jizhanming: quickProduct.jizhanming, sku: quickProduct.sku } : undefined}
        onClose={() => { setRestockOpen(false); setQuickProduct(null) }}
        onDone={() => { setRestockOpen(false); setQuickProduct(null); loadStock() }}
      />
      <BatchStockModal
        open={batchOpen}
        onClose={() => setBatchOpen(false)}
        onDone={() => { setBatchOpen(false); loadStock() }}
      />
    </div>
  )
}
