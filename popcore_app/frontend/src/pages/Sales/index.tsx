import { useState, useEffect, useCallback } from 'react'
import {
  Table, Button, Space, Tag, Popconfirm, message,
  Typography, Row, Col, InputNumber, Card,
  DatePicker, AutoComplete, Spin,
} from 'antd'
import {
  PlusOutlined, ExportOutlined, DeleteOutlined,
  LeftOutlined, RightOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs, { Dayjs } from 'dayjs'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartTooltip, ResponsiveContainer,
  BarChart as HBarChart,
} from 'recharts'
import client from '../../api/client'
import RoleGuard from '../../components/RoleGuard'
import BatchSalesModal from './BatchSalesModal'
import { useIsMobile } from '../../hooks/useIsMobile'

const { Text, Title } = Typography

interface SaleRow {
  id: number
  product_id: number
  date: string
  qty_pos: number
  qty_cash: number
  qty_sold: number
  notes: string
  sku: string
  jizhanming: string
  name_cn_en: string
  price: number | null
  ip_series: string
}

interface SummaryRow {
  date: string
  product_count: number
  total_sold: number
  total_pos: number
  total_cash: number
}

export default function SalesPage() {
  const isMobile = useIsMobile()

  const [date,    setDate]    = useState<Dayjs>(dayjs())
  const [sales,   setSales]   = useState<SaleRow[]>([])
  const [summary, setSummary] = useState<SummaryRow[]>([])
  const [loading, setLoading] = useState(false)
  const [addSearch,   setAddSearch]   = useState('')
  const [addOptions,  setAddOptions]  = useState<any[]>([])
  const [batchOpen,   setBatchOpen]   = useState(false)
  const [exportFrom,  setExportFrom]  = useState<Dayjs>(dayjs().subtract(30, 'day'))
  const [exportTo,    setExportTo]    = useState<Dayjs>(dayjs())
  const [localEdits,  setLocalEdits]  = useState<Record<number, { pos: number; cash: number }>>({})

  const dateStr = date.format('YYYY-MM-DD')

  const loadSales = useCallback(() => {
    setLoading(true)
    client.get('/sales', { params: { date: dateStr } })
      .then(r => setSales(r.data))
      .finally(() => setLoading(false))
  }, [dateStr])

  const loadSummary = useCallback(() => {
    client.get('/sales/summary').then(r => setSummary(r.data))
  }, [])

  useEffect(() => { loadSales(); loadSummary() }, [loadSales, loadSummary])

  async function searchToAdd(v: string) {
    setAddSearch(v)
    if (!v) { setAddOptions([]); return }
    const r = await client.get('/products/search', { params: { q: v, limit: 8 } })
    setAddOptions(r.data.map((p: any) => ({
      value: String(p.id),
      label: `${p.jizhanming} (${p.sku})`,
    })))
  }

  async function addProduct(pid: number) {
    try {
      await client.post('/sales/add_product', { product_id: pid, date: dateStr })
      setAddSearch(''); setAddOptions([])
      loadSales()
    } catch { message.error('Failed to add product') }
  }

  function setLocalQty(rowId: number, field: 'pos' | 'cash', val: number) {
    setLocalEdits(prev => ({
      ...prev,
      [rowId]: {
        pos:  prev[rowId]?.pos  ?? sales.find(s => s.id === rowId)?.qty_pos  ?? 0,
        cash: prev[rowId]?.cash ?? sales.find(s => s.id === rowId)?.qty_cash ?? 0,
        [field]: val,
      },
    }))
  }

  async function upsert(row: SaleRow, field: 'qty_pos' | 'qty_cash', val: number) {
    const local   = localEdits[row.id]
    const newPos  = field === 'qty_pos'  ? val : (local?.pos  ?? row.qty_pos)
    const newCash = field === 'qty_cash' ? val : (local?.cash ?? row.qty_cash)
    setSales(prev => prev.map(s =>
      s.id === row.id ? { ...s, qty_pos: newPos, qty_cash: newCash, qty_sold: newPos + newCash } : s
    ))
    setLocalEdits(prev => { const n = { ...prev }; delete n[row.id]; return n })
    try {
      await client.post('/sales/upsert', { product_id: row.product_id, date: dateStr, qty_pos: newPos, qty_cash: newCash, notes: row.notes })
    } catch { message.error('Update failed'); loadSales() }
  }

  async function deleteRecord(id: number) {
    try {
      await client.delete(`/sales/record/${id}`)
      message.success('Deleted')
      loadSales()
    } catch { message.error('Delete failed') }
  }

  async function clearDay() {
    try {
      await client.delete('/sales/clear_day', { params: { date: dateStr } })
      message.success('Cleared')
      loadSales()
    } catch { message.error('Failed') }
  }

  const totalRevenue = sales.reduce((s, r) => s + (r.price ?? 0) * r.qty_sold, 0)
  const totalPos     = sales.reduce((s, r) => s + r.qty_pos, 0)
  const totalCash    = sales.reduce((s, r) => s + r.qty_cash, 0)
  const totalSold    = sales.reduce((s, r) => s + r.qty_sold, 0)

  // Weekly bar chart data (last 7 days from summary)
  const weeklyData = summary.slice(0, 7).reverse().map(r => ({
    date: dayjs(r.date).format('ddd MM/DD'),
    Revenue: r.total_sold, // proxy — no revenue in summary, use qty
  }))

  // Top products for today
  const topProducts = [...sales]
    .sort((a, b) => b.qty_sold - a.qty_sold)
    .slice(0, 6)
    .map(r => ({ name: r.jizhanming || r.sku, POS: r.qty_pos, Cash: r.qty_cash }))

  const entryColumns: ColumnsType<SaleRow> = [
    {
      title: 'Product',
      render: (_, r) => (
        <div>
          <div style={{ fontWeight: 500, fontSize: 13, color: '#111827' }}>{r.jizhanming || '—'}</div>
          <div style={{ fontSize: 11, color: '#9ca3af' }}>{r.sku}</div>
        </div>
      ),
    },
    {
      title: 'Series', dataIndex: 'ip_series', width: 110,
      render: v => v ? <Tag color="blue" style={{ fontSize: 11 }}>{v}</Tag> : '—',
    },
    {
      title: 'Price', dataIndex: 'price', width: 80, align: 'right',
      render: v => v != null ? <Text style={{ fontSize: 12 }}>CA${v}</Text> : '—',
    },
    {
      title: 'POS Qty', dataIndex: 'qty_pos', width: 100, align: 'center',
      render: (v, r) => (
        <InputNumber
          size="small" min={0}
          value={localEdits[r.id]?.pos ?? v}
          onChange={val => setLocalQty(r.id, 'pos', val ?? 0)}
          onBlur={() => upsert(r, 'qty_pos', localEdits[r.id]?.pos ?? v)}
          onPressEnter={() => upsert(r, 'qty_pos', localEdits[r.id]?.pos ?? v)}
          style={{ width: 65 }}
        />
      ),
    },
    {
      title: 'Cash Qty', dataIndex: 'qty_cash', width: 100, align: 'center',
      render: (v, r) => (
        <InputNumber
          size="small" min={0}
          value={localEdits[r.id]?.cash ?? v}
          onChange={val => setLocalQty(r.id, 'cash', val ?? 0)}
          onBlur={() => upsert(r, 'qty_cash', localEdits[r.id]?.cash ?? v)}
          onPressEnter={() => upsert(r, 'qty_cash', localEdits[r.id]?.cash ?? v)}
          style={{ width: 65 }}
        />
      ),
    },
    {
      title: 'Total Units', dataIndex: 'qty_sold', width: 90, align: 'center',
      render: v => <Text style={{ fontWeight: 600, color: v > 0 ? '#10B981' : '#9ca3af' }}>{v}</Text>,
    },
    {
      title: 'Revenue', width: 90, align: 'right',
      render: (_, r) => {
        const rev = (r.price ?? 0) * r.qty_sold
        return <Text style={{ color: '#6366F1', fontSize: 12 }}>CA${rev.toFixed(2)}</Text>
      },
    },
    {
      title: '', key: 'del', width: 50,
      render: (_, r) => (
        <RoleGuard minRole="manager">
          <Popconfirm title="Delete this record?" onConfirm={() => deleteRecord(r.id)}>
            <Button size="small" danger type="text" icon={<DeleteOutlined />} />
          </Popconfirm>
        </RoleGuard>
      ),
    },
  ]

  const summaryColumns: ColumnsType<SummaryRow> = [
    { title: 'Date', dataIndex: 'date', width: 110 },
    { title: 'Products', dataIndex: 'product_count', width: 90, align: 'center' },
    { title: 'POS', dataIndex: 'total_pos', width: 80, align: 'center', render: v => <Tag color="blue">{v}</Tag> },
    { title: 'Cash', dataIndex: 'total_cash', width: 80, align: 'center', render: v => <Tag color="cyan">{v}</Tag> },
    { title: 'Total Sold', dataIndex: 'total_sold', width: 90, align: 'center', render: v => <Tag color="green">{v}</Tag> },
  ]

  return (
    <div>
      {/* Header */}
      {isMobile ? (
        /* Mobile: Day-navigator with ‹ prev / date / next › */
        <div style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <Button
                type="text"
                icon={<LeftOutlined />}
                onClick={() => setDate(d => d.subtract(1, 'day'))}
                style={{ color: '#374151', padding: '0 8px' }}
              />
              <span style={{ fontWeight: 600, fontSize: 15, color: '#111827', minWidth: 110, textAlign: 'center' }}>
                {date.format('ddd, MMM D')}
              </span>
              <Button
                type="text"
                icon={<RightOutlined />}
                onClick={() => setDate(d => d.add(1, 'day'))}
                disabled={date.isSame(dayjs(), 'day')}
                style={{ color: '#374151', padding: '0 8px' }}
              />
            </div>
            <RoleGuard minRole="staff">
              <Space size={6}>
                <AutoComplete
                  placeholder="Add product..."
                  value={addSearch}
                  options={addOptions}
                  onSearch={searchToAdd}
                  onSelect={(val) => { setAddSearch(''); setAddOptions([]); addProduct(Number(val)) }}
                  onClear={() => { setAddSearch(''); setAddOptions([]) }}
                  allowClear
                  style={{ width: 150 }}
                />
                <Button icon={<PlusOutlined />} type="primary" onClick={() => setBatchOpen(true)} />
              </Space>
            </RoleGuard>
          </div>
        </div>
      ) : (
        /* Desktop: original header */
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20, gap: 12 }}>
          <div>
            <Title level={3} style={{ margin: 0 }}>Daily Sales</Title>
            <Text style={{ color: '#6b7280' }}>Track POS and cash sales by product</Text>
          </div>
          <Space wrap size={[8, 8]}>
            <DatePicker
              value={date}
              onChange={d => setDate(d ?? dayjs())}
              allowClear={false}
              style={{ width: 140 }}
            />
            <RoleGuard minRole="staff">
              <AutoComplete
                placeholder="Search & add product..."
                value={addSearch}
                options={addOptions}
                onSearch={searchToAdd}
                onSelect={(val, opt) => { addProduct(Number(val)); setAddSearch(opt.label as string) }}
                onClear={() => { setAddSearch(''); setAddOptions([]) }}
                allowClear
                style={{ width: 240 }}
              />
              <Button icon={<PlusOutlined />} type="primary" onClick={() => setBatchOpen(true)}>
                Add Entry
              </Button>
            </RoleGuard>
          </Space>
        </div>
      )}

      {/* Stat cards — 3-col KPI strip on mobile, 4-col on desktop */}
      <Row gutter={[isMobile ? 8 : 16, isMobile ? 8 : 16]} style={{ marginBottom: isMobile ? 16 : 20 }}>
        {isMobile ? (
          // 3-column KPI strip on mobile: Revenue, Units, POS
          <>
            {[
              { label: 'Revenue',    value: `CA$${totalRevenue.toFixed(0)}`, color: '#6366F1' },
              { label: 'Units Sold', value: totalSold,                       color: '#10B981' },
              { label: 'POS / Cash', value: `${totalPos} / ${totalCash}`,   color: '#f59e0b' },
            ].map(c => (
              <Col key={c.label} xs={8}>
                <Card style={{ borderRadius: 10, borderTop: `3px solid ${c.color}` }} bodyStyle={{ padding: '10px 12px' }}>
                  <div style={{ fontSize: 10, color: '#9ca3af' }}>{c.label}</div>
                  <div style={{ fontSize: 16, fontWeight: 700, color: c.color, marginTop: 2 }}>{c.value}</div>
                </Card>
              </Col>
            ))}
          </>
        ) : (
          // 4-column on desktop
          <>
            {[
              { label: 'Total Revenue',  value: `CA$${totalRevenue.toFixed(2)}`, color: '#6366F1' },
              { label: 'Units Sold',     value: totalSold,                       color: '#10B981' },
              { label: 'POS Sales',      value: `${totalPos} units`,             color: '#6366F1' },
              { label: 'Cash Sales',     value: `${totalCash} units`,            color: '#10B981' },
            ].map(c => (
              <Col key={c.label} xs={12} sm={6}>
                <Card style={{ borderRadius: 10, borderTop: `3px solid ${c.color}` }} bodyStyle={{ padding: '14px 20px' }}>
                  <div style={{ fontSize: 12, color: '#9ca3af' }}>{c.label}</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: c.color, marginTop: 4 }}>{c.value}</div>
                </Card>
              </Col>
            ))}
          </>
        )}
      </Row>

      {/* Charts */}
      <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
        <Col xs={24} lg={12}>
          <Card title="Weekly Revenue (Last 7 Days)" style={{ borderRadius: 10 }} bodyStyle={{ padding: '12px 16px 8px' }}>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={weeklyData} margin={{ top: 0, right: 10, left: -10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} />
                <RechartTooltip />
                <Bar dataKey="Revenue" fill="#6366F1" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title={`Top Products — ${date.format('MMM D')}`} style={{ borderRadius: 10 }} bodyStyle={{ padding: '12px 16px 8px' }}>
            <ResponsiveContainer width="100%" height={200}>
              <HBarChart data={topProducts} layout="vertical" margin={{ top: 0, right: 10, left: 60, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 10 }} />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={60} />
                <RechartTooltip />
                <Bar dataKey="POS"  fill="#6366F1" radius={[0, 4, 4, 0]} stackId="a" />
                <Bar dataKey="Cash" fill="#10B981" radius={[0, 4, 4, 0]} stackId="a" />
              </HBarChart>
            </ResponsiveContainer>
          </Card>
        </Col>
      </Row>

      {/* Sales table + log */}
      <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)', overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0', display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <div style={{ fontWeight: 600, color: '#111827' }}>
            Sales for {date.format(isMobile ? 'MMM D, YYYY' : 'dddd, MMMM D, YYYY')}
            <Text style={{ color: '#9ca3af', fontWeight: 400, fontSize: 13, marginLeft: 8 }}>
              {sales.length} products
            </Text>
          </div>
          <RoleGuard minRole="manager">
            <Space size={8}>
              <Popconfirm title={`Clear all sales for ${dateStr}?`} onConfirm={clearDay}>
                <Button danger size="small">Clear Day</Button>
              </Popconfirm>
              <Button
                size="small"
                icon={<ExportOutlined />}
                onClick={() => window.location.href = `/api/sales/export?from=${exportFrom.format('YYYY-MM-DD')}&to=${exportTo.format('YYYY-MM-DD')}`}
              >
                Export
              </Button>
            </Space>
          </RoleGuard>
        </div>
        {isMobile ? (
          <Spin spinning={loading}>
            {sales.length === 0 && !loading ? (
              <div style={{ textAlign: 'center', color: '#9ca3af', padding: '24px 16px', fontSize: 13 }}>
                No products added for this date
              </div>
            ) : sales.map(row => (
              <div key={row.id} style={{ padding: '12px 16px', borderBottom: '1px solid #f5f5f5' }}>
                {/* Product name row */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 500, fontSize: 13, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {row.jizhanming || '—'}
                    </div>
                    <div style={{ fontSize: 11, color: '#9ca3af' }}>{row.sku}</div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0, marginLeft: 8 }}>
                    <Text style={{ fontWeight: 700, fontSize: 18, color: row.qty_sold > 0 ? '#10B981' : '#d1d5db' }}>
                      {row.qty_sold}
                    </Text>
                    <RoleGuard minRole="manager">
                      <Popconfirm title="Delete this record?" onConfirm={() => deleteRecord(row.id)}>
                        <Button size="small" danger type="text" icon={<DeleteOutlined />} />
                      </Popconfirm>
                    </RoleGuard>
                  </div>
                </div>
                {/* Qty inputs row */}
                <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 11, color: '#6b7280', width: 28 }}>POS</span>
                    <InputNumber
                      size="small" min={0}
                      value={localEdits[row.id]?.pos ?? row.qty_pos}
                      onChange={val => setLocalQty(row.id, 'pos', val ?? 0)}
                      onBlur={() => upsert(row, 'qty_pos', localEdits[row.id]?.pos ?? row.qty_pos)}
                      onPressEnter={() => upsert(row, 'qty_pos', localEdits[row.id]?.pos ?? row.qty_pos)}
                      style={{ width: 65 }}
                    />
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 11, color: '#6b7280', width: 34 }}>Cash</span>
                    <InputNumber
                      size="small" min={0}
                      value={localEdits[row.id]?.cash ?? row.qty_cash}
                      onChange={val => setLocalQty(row.id, 'cash', val ?? 0)}
                      onBlur={() => upsert(row, 'qty_cash', localEdits[row.id]?.cash ?? row.qty_cash)}
                      onPressEnter={() => upsert(row, 'qty_cash', localEdits[row.id]?.cash ?? row.qty_cash)}
                      style={{ width: 65 }}
                    />
                  </div>
                  {row.price != null && (
                    <Text style={{ fontSize: 11, color: '#6366F1', marginLeft: 'auto' }}>
                      CA${((row.price ?? 0) * row.qty_sold).toFixed(2)}
                    </Text>
                  )}
                </div>
              </div>
            ))}
          </Spin>
        ) : (
          <Table
            rowKey="id"
            size="middle"
            loading={loading}
            dataSource={sales}
            columns={entryColumns}
            pagination={false}
            scroll={{ x: 800, y: 500 }}
          />
        )}
      </div>

      {/* Summary section */}
      <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)', marginTop: 16, overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0', display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <span style={{ fontWeight: 600, color: '#111827' }}>Sales Log</span>
          <RoleGuard minRole="manager">
            <Space wrap size={[8, 8]}>
              <DatePicker value={exportFrom} onChange={d => setExportFrom(d ?? dayjs().subtract(30,'day'))} allowClear={false} style={{ width: 130 }} />
              <Text style={{ color: '#9ca3af' }}>to</Text>
              <DatePicker value={exportTo} onChange={d => setExportTo(d ?? dayjs())} allowClear={false} style={{ width: 130 }} />
              <Button
                size="small"
                icon={<ExportOutlined />}
                onClick={() => window.location.href = `/api/sales/export?from=${exportFrom.format('YYYY-MM-DD')}&to=${exportTo.format('YYYY-MM-DD')}`}
              >Export</Button>
            </Space>
          </RoleGuard>
        </div>
        <Table
          rowKey="date"
          size="middle"
          dataSource={summary}
          columns={summaryColumns}
          pagination={{ pageSize: 30, showTotal: t => `${t} days` }}
          scroll={{ x: 'max-content' }}
        />
      </div>

      <BatchSalesModal
        open={batchOpen}
        date={dateStr}
        onClose={() => setBatchOpen(false)}
        onDone={() => { setBatchOpen(false); loadSales() }}
      />
    </div>
  )
}
