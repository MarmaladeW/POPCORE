import { useState, useEffect, useCallback, useRef } from 'react'
import {
  Table, Input, Select, Button, Space, Tag, Popconfirm,
  message, Typography, Row, Col, Card, Spin, Alert,
} from 'antd'
import {
  ReloadOutlined, ExportOutlined, DeleteOutlined,
  EditOutlined,
  InboxOutlined, ArrowUpOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'
import { useAppStore } from '../../store'
import RoleGuard from '../../components/RoleGuard'
import RestockModal from './RestockModal'
import BatchStockModal from './BatchStockModal'
import { useIsMobile } from '../../hooks/useIsMobile'
import { useNavigate } from 'react-router-dom'

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
  upstairs_qty: number
  instore_qty: number
  last_updated: string
  stock_notes: string
  price: number | null
  stock_unit?: 'set' | 'box' | 'piece' | null
  identity_status?: 'unverified' | 'verified'
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
  total_upstairs_qty:  number | null
  total_instore_qty:   number | null
  low_stock_count:     number
  out_of_stock_count:  number
  total_stock_value:   number | null
  mode?: 'legacy' | 'authoritative'
  complete?: boolean
  unit_totals?: { unit: string; floor_qty: number; back_qty: number; total_qty: number }[]
}

interface InventoryLocation {
  id: number
  name: string
  code: string
  opening_verified: boolean
}

interface InventoryBalance {
  product_id: number
  location_id: number
  location_name: string
  location_code: string
  disposition: string
  quantity: number
  unit: string
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

/** Format a raw qty number into 端/盒 breakdown for blind boxes, or plain 件 for others. */
function formatUnit(qty: number, unit: string) {
  const plural = unit === 'box' ? 'boxes' : `${unit}s`
  return `${qty} ${qty === 1 ? unit : plural}`
}

function formatQty(qty: number, row: Pick<StockRow, 'product_type' | 'boxes_per_dan' | 'stock_unit'>): string {
  if (row.stock_unit) return formatUnit(qty, row.stock_unit)
  if (row.product_type !== '盲盒' || !row.boxes_per_dan) {
    return `${qty} 件`
  }
  const bpd  = row.boxes_per_dan
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
  const navigate = useNavigate()
  const isMobile = useIsMobile()
  const { series, selectedStore } = useAppStore()
  const sc = selectedStore?.code
  const isAll = sc === 'ALL'
  const [stock,    setStock]   = useState<StockRow[]>([])
  const [txns,     setTxns]    = useState<Transaction[]>([])
  const [summary,  setSummary] = useState<Summary | null>(null)
  const [locations, setLocations] = useState<InventoryLocation[]>([])
  const [balances, setBalances] = useState<InventoryBalance[]>([])
  const [loading,  setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [lastSuccess, setLastSuccess] = useState<Date | null>(null)
  const requestRef = useRef(0)
  const transactionRequestRef = useRef(0)
  const scopeRef = useRef('')
  const [q,        setQ]       = useState('')
  const [filterSeries, setFilterSeries] = useState('')
  const [page,     setPage]    = useState(1)
  const [total,    setTotal]   = useState(0)
  const PAGE_SIZE = 100
  const [selected, setSelected] = useState<React.Key[]>([])
  const [restockOpen, setRestockOpen] = useState(false)
  const [batchOpen,   setBatchOpen]   = useState(false)
  const [quickProduct, setQuickProduct] = useState<StockRow | null>(null)
  const [editingNotes, setEditingNotes] = useState<{ id: number; value: string } | null>(null)
  const [activeTab, setActiveTab] = useState<'overview' | 'history'>('overview')
  const [exporting, setExporting] = useState(false)

  const loadStock = useCallback(() => {
    if (!sc) return
    const requestId = ++requestRef.current
    const scope = `${sc}:${q}:${filterSeries}:${page}`
    if (scopeRef.current !== scope) {
      scopeRef.current = scope
      setStock([])
      setSummary(null)
      setLocations([])
      setBalances([])
      setTotal(0)
      setLastSuccess(null)
    }
    setLoading(true)
    setLoadError(null)
    const params: Record<string, string | number> = { page, page_size: PAGE_SIZE, store_code: sc }
    if (q) params.q = q
    if (filterSeries) params.series = filterSeries
    Promise.all([
      client.get('/stock', { params }),
      client.get('/stock/summary', { params: { store_code: sc } }),
      client.get('/inventory/locations', { params: isAll ? {} : { store_code: sc } })
        .catch(() => ({ data: [] })),
      isAll
        ? Promise.resolve({ data: { items: [] } })
        : client.get('/inventory/balances', { params: { store_code: sc } })
            .catch(() => ({ data: { items: [] } })),
    ]).then(([sResp, sumResp, locationResp, balanceResp]) => {
      if (requestId !== requestRef.current) return
      setStock(sResp.data.items)
      setTotal(sResp.data.total)
      setSummary(sumResp.data)
      setLocations(Array.isArray(locationResp.data) ? locationResp.data : [])
      setBalances(Array.isArray(balanceResp.data?.items) ? balanceResp.data.items : [])
      setLastSuccess(new Date())
    }).catch(() => {
      if (requestId === requestRef.current) setLoadError('Unable to load stock data.')
    }).finally(() => {
      if (requestId === requestRef.current) setLoading(false)
    })
  }, [isAll, q, filterSeries, page, sc])

  const loadTxns = useCallback(() => {
    if (!sc) return
    const requestId = ++transactionRequestRef.current
    client.get('/stock/transactions', { params: { limit: 100, store_code: sc } })
      .then(r => {
        if (requestId === transactionRequestRef.current) setTxns(r.data)
      })
  }, [sc])

  useEffect(() => { setPage(1) }, [q, filterSeries])
  useEffect(() => { loadStock() }, [loadStock])
  useEffect(() => {
    transactionRequestRef.current++
    setTxns([])
    if (activeTab === 'history') loadTxns()
  }, [activeTab, loadTxns, sc])

  async function handleDeleteRows() {
    try {
      await client.delete('/stock/rows', { data: { store_code: sc, product_ids: selected } })
      message.success(`Removed ${selected.length} stock records`)
      setSelected([])
      loadStock()
    } catch {
      message.error('Delete failed')
    }
  }

  async function handleExport() {
    setExporting(true)
    try {
      const params = new URLSearchParams()
      if (filterSeries) params.set('series', filterSeries)
      if (q) params.set('q', q)
      if (sc) params.set('store_code', sc)
      const res = await client.get(`/stock/export?${params}`, { responseType: 'blob' })
      const url = window.URL.createObjectURL(new Blob([res.data]))
      const a = document.createElement('a')
      a.href = url
      a.download = `stock_${new Date().toISOString().slice(0, 10)}.csv`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(url)
    } catch {
      message.error('Export failed — please try again')
    } finally {
      setExporting(false)
    }
  }

  async function saveNotes(productId: number, notes: string) {
    try {
      await client.patch(`/stock/${productId}`, { notes, store_code: sc })
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
          {r.identity_status && (
            <Tag color={r.identity_status === 'verified' ? 'green' : 'orange'} style={{ fontSize: 10 }}>
              {r.identity_status === 'verified' ? 'Verified identity' : 'Unverified identity'}
            </Tag>
          )}
        </div>
      ),
    },
    {
      title: 'Series', dataIndex: 'ip_series', width: 120,
      render: v => v ? <Tag color="blue" style={{ fontSize: 11 }}>{v}</Tag> : '—',
    },
    {
      title: 'Location balances', width: 190,
      render: (_, r) => {
        const facts = balances.filter(balance => balance.product_id === r.id)
        return facts.length ? <Space direction="vertical" size={2}>{facts.map(balance => <Text key={`${balance.location_id}:${balance.disposition}`} style={{ fontSize: 12 }}>
          {balance.location_name || locations.find(location => location.id === balance.location_id)?.name || balance.location_code} · {balance.disposition}: {formatUnit(balance.quantity, balance.unit)}
        </Text>)}</Space> : <Text type="secondary">Unavailable</Text>
      },
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
      render: (_, r) => isAll ? null : (
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

  const byUnit = (side: 'back_qty' | 'floor_qty') => (summary?.unit_totals ?? [])
    .map(row => `${row[side]} ${row.unit}${row[side] === 1 ? '' : 's'}`).join(' · ') || 'Unavailable'
  const summaryCards = summary ? [
    { label: 'Upstairs / Back Stock', value: summary.mode === 'authoritative' ? byUnit('back_qty') : summary.total_upstairs_qty },
    { label: 'Floor Stock', value: summary.mode === 'authoritative' ? byUnit('floor_qty') : summary.total_instore_qty },
    { label: 'Total Stock Value', value: summary.total_stock_value == null ? 'Unknown' : `CA$ ${summary.total_stock_value.toLocaleString('en-CA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` },
    { label: 'Low/Out of Stock', value: summary.low_stock_count + summary.out_of_stock_count },
  ] : []
  const openingReady = locations.length > 0 && locations.every(location => location.opening_verified)

  if (loadError && !lastSuccess) {
    return <Alert role="alert" type="error" showIcon message={loadError} action={<Button onClick={loadStock}>Retry</Button>} />
  }

  return (
    <div>
      {/* Header */}
      <div className="pc-page-actions" style={{ marginBottom: 20 }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>Inventory</Title>
          <Text type="secondary">Verified products, native units, and store locations</Text>
        </div>
        <Space>
          {!isAll && (
            <RoleGuard minRole="staff">
              <Space wrap>
                <Button onClick={() => navigate('/goods/receiving')}>Receive</Button>
                <Button onClick={() => navigate('/goods/transfers')}>Transfer</Button>
                <Button onClick={() => navigate('/goods/counts')}>Count</Button>
              </Space>
            </RoleGuard>
          )}
          <Button onClick={loadStock}>Refresh</Button>
          {!isAll && (
            <RoleGuard minRole="staff">
              <Button type="primary" icon={<EditOutlined />} onClick={() => { setQuickProduct(null); setRestockOpen(true) }}>
                Adjust Stock
              </Button>
            </RoleGuard>
          )}
        </Space>
      </div>

      {isAll && (
        <div style={{
          background: '#fffbeb', border: '1px solid #f59e0b', borderRadius: 8,
          padding: '10px 16px', marginBottom: 16, color: '#92400e', fontSize: 13,
        }}>
          Viewing all stores combined. Please select a specific store to make changes.
        </div>
      )}

      {summary?.mode && <Alert
        type={summary.mode === 'authoritative' && summary.complete && openingReady ? 'success' : 'warning'}
        showIcon
        message={summary.mode === 'legacy' ? 'Legacy inventory view'
          : !summary.complete ? 'Inventory identity review incomplete'
          : locations.length === 0 ? 'Opening readiness unavailable'
          : !openingReady ? 'Opening review incomplete'
          : 'Authoritative inventory'}
        description={summary.mode === 'legacy'
          ? 'Quantities use the legacy stock model until reviewed opening balances are available.'
          : !summary.complete
            ? 'Some rows lack verified product identity or a native unit. Quantities remain separated and no opening value is inferred.'
            : locations.length === 0
              ? 'Opening status could not be confirmed. Refresh or ask a manager to verify inventory access before posting.'
              : !openingReady
              ? 'Reviewed opening balances are required for every authorized location before posting inventory.'
              : 'Changes use verified product identities, native units, locations, dispositions, and reviewed opening balances.'}
        style={{ marginBottom: 16 }}
      />}

      {loadError && (
        <Alert
          role="alert"
          type={lastSuccess ? 'warning' : 'error'}
          showIcon
          message={lastSuccess ? 'Showing previously loaded stock data' : loadError}
          description={lastSuccess ? `Last updated ${lastSuccess.toLocaleTimeString()}` : undefined}
          action={<Button onClick={loadStock}>Retry</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {/* Summary cards */}
      {summary && (
        <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
          {summaryCards.map(c => (
            <Col key={c.label} xs={12} sm={6}>
              <Card style={{ borderRadius: 8, borderColor: '#DDE2EA' }} bodyStyle={{ padding: '16px 20px' }}>
                <div style={{ fontSize: 12, color: '#596273', marginBottom: 4 }}>{c.label}</div>
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
                  onClick={() => setActiveTab(t.key)}
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
                onClick={() => setActiveTab(t.key)}
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
                  {!isAll && (
                    <RoleGuard minRole="staff">
                      <Button onClick={() => setBatchOpen(true)}>Batch Import</Button>
                    </RoleGuard>
                  )}
                  <RoleGuard minRole="manager">
                    {!isAll && <Button icon={<ExportOutlined />} onClick={handleExport} loading={exporting}>Export</Button>}
                    {!isAll && selected.length > 0 && (
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
                  {!loading && !loadError && stock.length === 0 && (
                    <div style={{ textAlign: 'center', color: '#9ca3af', padding: '24px 16px', fontSize: 13 }}>No stock records</div>
                  )}
                  {stock.map(row => {
                    const total = row.upstairs_qty + row.instore_qty
                    const stockVal = row.price ? total * row.price : null
                    return (
                      <div key={row.id} style={{ padding: '12px 16px', borderBottom: '1px solid #f5f5f5' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontWeight: 600, fontSize: 14, color: '#20242D', overflowWrap: 'anywhere' }}>{row.jizhanming || row.name_cn_en || 'Unnamed product'}</div>
                            {row.name_cn_en && row.name_cn_en !== row.jizhanming && <div style={{ fontSize: 12, color: '#596273', marginTop: 2, overflowWrap: 'anywhere' }}>{row.name_cn_en}</div>}
                            <div style={{ fontSize: 11, color: '#687386', fontFamily: 'monospace', marginTop: 2 }}>{row.sku}</div>
                            {row.ip_series && <Tag color="blue" style={{ fontSize: 10, marginTop: 4 }}>{row.ip_series}</Tag>}
                            {row.identity_status && <Tag color={row.identity_status === 'verified' ? 'green' : 'orange'} style={{ fontSize: 10, marginTop: 4 }}>
                              {row.identity_status === 'verified' ? `Verified · ${row.stock_unit}` : 'Unverified identity'}
                            </Tag>}
                            {balances.filter(balance => balance.product_id === row.id).map(balance => <div key={`${balance.location_id}:${balance.disposition}`} style={{ marginTop: 4, color: '#596273', fontSize: 12 }}>
                              {balance.location_name || locations.find(location => location.id === balance.location_id)?.name || balance.location_code} · {balance.disposition}: {formatUnit(balance.quantity, balance.unit)}
                            </div>)}
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
                          {!isAll && (
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
                          )}
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
                  pagination={{
                    current: page, pageSize: PAGE_SIZE, total,
                    onChange: (p) => setPage(p),
                    showTotal: t => `${t} products`,
                  }}
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
        initialProduct={quickProduct ? { id: quickProduct.id, jizhanming: quickProduct.jizhanming, sku: quickProduct.sku, product_type: quickProduct.product_type, boxes_per_dan: quickProduct.boxes_per_dan } : undefined}
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
