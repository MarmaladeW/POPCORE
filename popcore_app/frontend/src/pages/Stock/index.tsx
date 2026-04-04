import { useState, useEffect, useCallback } from 'react'
import {
  Table, Input, Select, Button, Space, Tag, Popconfirm,
  message, Typography, Row, Col, Card, Spin,
} from 'antd'
import {
  ReloadOutlined, ExportOutlined, DeleteOutlined,
  EditOutlined,
  InboxOutlined, ArrowUpOutlined, WarningOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'
import { useAppStore } from '../../store'
import RoleGuard from '../../components/RoleGuard'
import RestockModal from './RestockModal'
import BatchStockModal from './BatchStockModal'
import { useIsMobile } from '../../hooks/useIsMobile'

const { Search } = Input
const { Text, Title } = Typography

interface StockRow {
  id: number
  sku: string
  jizhanming: string
  name_cn_en: string
  ip_series: string
  product_type: string
  boxes_per_dan: number | null
  dan_per_xiang: number | null
  upstairs_qty: number
  instore_qty: number
  last_updated: string
  stock_notes: string
  price: number | null
}

interface Transaction {
  id: number
  product_id: number
  txn_type: string
  qty: number
  location: string
  date: string
  notes: string
  created_at: string
  jizhanming: string
  sku: string
}

interface Summary {
  products_tracked:    number
  total_upstairs_qty:  number
  total_instore_qty:   number
  low_stock_count:     number
  out_of_stock_count:  number
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

/** Format a raw qty number into a human-readable breakdown based on product type. */
function formatQty(qty: number, row: Pick<StockRow, 'product_type' | 'boxes_per_dan' | 'dan_per_xiang'>): string {
  if (row.product_type !== '盲盒' || !row.boxes_per_dan) {
    return `${qty} 件`
  }
  const bpd = row.boxes_per_dan
  const dpx = row.dan_per_xiang
  if (dpx) {
    const xiang = Math.floor(qty / (bpd * dpx))
    const drem  = Math.floor((qty % (bpd * dpx)) / bpd)
    const he    = qty % bpd
    return [
      xiang > 0 ? `${xiang}箱` : '',
      drem  > 0 ? `${drem}端`  : '',
      he    > 0 ? `${he}盒`    : '',
      xiang === 0 && drem === 0 && he === 0 ? '0盒' : '',
    ].filter(Boolean).join(' ')
  }
  const duan = Math.floor(qty / bpd)
  const he   = qty % bpd
  return [
    duan > 0 ? `${duan}端` : '',
    he   > 0 ? `${he}盒`  : '',
    duan === 0 && he === 0 ? '0盒' : '',
  ].filter(Boolean).join(' ')
}

function stockStatus(total: number) {
  if (total === 0)   return <Tag color="red">Out of Stock</Tag>
  if (total <= 3)    return <Tag color="orange">Low Stock</Tag>
  if (total <= 10)   return <Tag color="default">Normal</Tag>
  return <Tag color="green">Well Stocked</Tag>
}

export default function StockPage() {
  const isMobile = useIsMobile()
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
  const [activeTab, setActiveTab] = useState<'overview' | 'history'>('overview')

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
      dataIndex: 'upstairs_qty',
      width: 110, align: 'center',
      sorter: (a, b) => a.upstairs_qty - b.upstairs_qty,
      render: (v, r) => (
        <span style={{ fontWeight: 600, color: v === 0 ? '#ef4444' : '#374151', fontSize: 12 }}>
          {formatQty(v, r)}
        </span>
      ),
    },
    {
      title: <><InboxOutlined /> In-Store</>,
      dataIndex: 'instore_qty',
      width: 110, align: 'center',
      sorter: (a, b) => a.instore_qty - b.instore_qty,
      render: (v, r) => (
        <span style={{ fontWeight: 600, color: v === 0 ? '#9ca3af' : '#374151', fontSize: 12 }}>
          {formatQty(v, r)}
        </span>
      ),
    },
    {
      title: 'Total',
      width: 110, align: 'center',
      sorter: (a, b) => (a.upstairs_qty + a.instore_qty) - (b.upstairs_qty + b.instore_qty),
      render: (_, r) => {
        const t = r.upstairs_qty + r.instore_qty
        return (
          <span style={{
            display: 'inline-block', borderRadius: 6, fontWeight: 700, fontSize: 12,
            padding: '2px 8px',
            background: t === 0 ? '#fef2f2' : t <= 3 ? '#fffbeb' : '#f0fdf4',
            color:      t === 0 ? '#ef4444' : t <= 3 ? '#d97706' : '#16a34a',
          }}>{formatQty(t, r)}</span>
        )
      },
    },
    {
      title: 'Stock Value',
      width: 110, align: 'right',
      render: (_, r) => {
        // Value is price × total boxes (or units) for non-blind box
        const total = r.upstairs_qty + r.instore_qty
        const val = r.price ? total * r.price : null
        return val != null
          ? <Text style={{ color: '#6366F1', fontSize: 12 }}>CA${val.toFixed(2)}</Text>
          : <Text type="secondary">—</Text>
      },
    },
    {
      title: 'Status',
      width: 120,
      render: (_, r) => stockStatus(r.upstairs_qty + r.instore_qty),
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
      title: 'Qty', dataIndex: 'qty', width: 70, align: 'right',
      render: v => <Text type={v < 0 ? 'danger' : 'success'}>{v > 0 ? `+${v}` : v}</Text>,
    },
    { title: 'Location', dataIndex: 'location', width: 120 },
    { title: 'Notes', dataIndex: 'notes', ellipsis: true },
  ]

  const summaryCards = summary ? [
    { label: 'Upstairs Total', value: summary.total_upstairs_qty, color: '#6366F1',  icon: <ArrowUpOutlined /> },
    { label: 'In-Store Total', value: summary.total_instore_qty,  color: '#10B981',  icon: <InboxOutlined /> },
    { label: 'Total Stock Value', value: `CA$ ${(
        stock.reduce((acc, r) => acc + (r.price ?? 0) * (r.upstairs_qty + r.instore_qty), 0)
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

        {/* Mobile: pill-style tab switcher */}
        {isMobile ? (
          <div style={{ padding: '12px 16px 0', borderBottom: '1px solid #f0f0f0' }}>
            <div style={{
              display: 'inline-flex',
              background: '#f3f4f6',
              borderRadius: 8,
              padding: 3,
            }}>
              {([
                { key: 'overview', label: 'Current Stock' },
                { key: 'history', label: `History (${txns.length})` },
              ] as const).map(t => (
                <button
                  key={t.key}
                  onClick={() => { setActiveTab(t.key); if (t.key === 'history') loadTxns() }}
                  style={{
                    padding:      '6px 14px',
                    minHeight:    44,
                    borderRadius: 6,
                    border:       'none',
                    cursor:       'pointer',
                    fontSize:     13,
                    fontWeight:   activeTab === t.key ? 600 : 400,
                    background:   activeTab === t.key ? '#fff' : 'transparent',
                    color:        activeTab === t.key ? '#111827' : '#6b7280',
                    boxShadow:    activeTab === t.key ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
                    transition:   'all 0.15s',
                  }}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        ) : (
          /* Desktop: inline tab labels */
          <div style={{ padding: '0 20px', borderBottom: '1px solid #f0f0f0', display: 'flex', gap: 0 }}>
            {([
              { key: 'overview', label: 'Current Stock' },
              { key: 'history', label: `Transaction History (${txns.length})` },
            ] as const).map(t => (
              <button
                key={t.key}
                onClick={() => { setActiveTab(t.key); if (t.key === 'history') loadTxns() }}
                style={{
                  padding:       '14px 16px',
                  border:        'none',
                  borderBottom:  activeTab === t.key ? '2px solid #6366F1' : '2px solid transparent',
                  background:    'transparent',
                  cursor:        'pointer',
                  fontSize:      14,
                  fontWeight:    activeTab === t.key ? 600 : 400,
                  color:         activeTab === t.key ? '#6366F1' : '#6b7280',
                  marginBottom:  -1,
                  transition:    'all 0.15s',
                }}
              >
                {t.label}
              </button>
            ))}
          </div>
        )}

        <div style={{ padding: isMobile ? '12px 0' : '16px 20px' }}>
          {activeTab === 'overview' ? (
            <div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14, padding: isMobile ? '0 16px' : 0 }}>
                <Search
                  placeholder="Search products"
                  allowClear
                  style={{ width: isMobile ? '100%' : 220 }}
                  onSearch={setQ}
                  onChange={e => { if (!e.target.value) setQ('') }}
                />
                <Select
                  placeholder="All Series"
                  allowClear
                  style={{ width: isMobile ? '100%' : 140 }}
                  options={series.map(s => ({ value: s, label: s }))}
                  onChange={v => setFilterSeries(v ?? '')}
                />
                <div style={{ marginLeft: isMobile ? 0 : 'auto', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
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
                    const total = row.upstairs_qty + row.instore_qty
                    const stockVal = row.price ? total * row.price : null
                    return (
                      <div key={row.id} style={{ padding: '12px 16px', borderBottom: '1px solid #f5f5f5' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontWeight: 500, fontSize: 13, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row.jizhanming || '—'}</div>
                            <div style={{ fontSize: 11, color: '#9ca3af', fontFamily: 'monospace', marginTop: 1 }}>{row.sku}</div>
                            {row.ip_series && <Tag color="blue" style={{ fontSize: 10, marginTop: 4 }}>{row.ip_series}</Tag>}
                          </div>
                          <div style={{ flexShrink: 0, marginLeft: 8 }}>{stockStatus(total)}</div>
                        </div>
                        {/* All quantities visible at a glance */}
                        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                          <div style={{ display: 'flex', gap: 16 }}>
                            <div style={{ textAlign: 'center' }}>
                              <div style={{ fontSize: 10, color: '#9ca3af' }}>Upstairs</div>
                              <div style={{ fontSize: 12, fontWeight: 700, color: row.upstairs_qty === 0 ? '#ef4444' : '#374151' }}>{formatQty(row.upstairs_qty, row)}</div>
                            </div>
                            <div style={{ textAlign: 'center' }}>
                              <div style={{ fontSize: 10, color: '#9ca3af' }}>In-Store</div>
                              <div style={{ fontSize: 12, fontWeight: 700, color: row.instore_qty === 0 ? '#9ca3af' : '#374151' }}>{formatQty(row.instore_qty, row)}</div>
                            </div>
                            <div style={{ textAlign: 'center' }}>
                              <div style={{ fontSize: 10, color: '#9ca3af' }}>Total</div>
                              <div style={{ fontSize: 12, fontWeight: 700, color: total === 0 ? '#ef4444' : total <= 3 ? '#d97706' : '#16a34a' }}>{formatQty(total, row)}</div>
                            </div>
                            {stockVal != null && (
                              <div style={{ textAlign: 'center' }}>
                                <div style={{ fontSize: 10, color: '#9ca3af' }}>Value</div>
                                <div style={{ fontSize: 12, fontWeight: 600, color: '#6366F1' }}>CA${stockVal.toFixed(0)}</div>
                              </div>
                            )}
                          </div>
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
          ) : (
            <div>
              <Button icon={<ReloadOutlined />} style={{ marginBottom: 12, marginLeft: isMobile ? 16 : 0 }} onClick={loadTxns}>
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
                        <Text type={txn.qty < 0 ? 'danger' : 'success'} style={{ fontWeight: 600 }}>
                          {txn.qty > 0 ? `+${txn.qty}` : txn.qty}
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
          )}
        </div>
      </div>

      <RestockModal
        open={restockOpen}
        initialProduct={quickProduct ? { id: quickProduct.id, jizhanming: quickProduct.jizhanming, sku: quickProduct.sku, product_type: quickProduct.product_type, boxes_per_dan: quickProduct.boxes_per_dan, dan_per_xiang: quickProduct.dan_per_xiang } : undefined}
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
