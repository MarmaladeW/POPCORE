import { useState, useEffect, useCallback } from 'react'
import {
  Table, Button, Space, Tag, Tabs, Popconfirm, message,
  Typography, Row, Col, InputNumber, Statistic, Card,
  DatePicker, AutoComplete,
} from 'antd'
import {
  PlusOutlined, ExportOutlined, DeleteOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs, { Dayjs } from 'dayjs'
import client from '../../api/client'
import RoleGuard from '../../components/RoleGuard'
import BatchSalesModal from './BatchSalesModal'

const { Text } = Typography

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
  const [date, setDate]         = useState<Dayjs>(dayjs())
  const [sales, setSales]       = useState<SaleRow[]>([])
  const [summary, setSummary]   = useState<SummaryRow[]>([])
  const [loading, setLoading]   = useState(false)
  const [addSearch, setAddSearch] = useState('')
  const [addOptions, setAddOptions] = useState<any[]>([])
  const [batchOpen, setBatchOpen]   = useState(false)
  const [exportFrom, setExportFrom] = useState<Dayjs>(dayjs().subtract(30, 'day'))
  const [exportTo, setExportTo]     = useState<Dayjs>(dayjs())
  // Track in-progress edits locally so InputNumber updates visually before API confirms
  const [localEdits, setLocalEdits] = useState<Record<number, { pos: number; cash: number }>>({})

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

  useEffect(() => { loadSales() }, [loadSales])

  async function searchToAdd(v: string) {
    setAddSearch(v)
    if (!v) { setAddOptions([]); return }
    const r = await client.get('/products/search', { params: { q: v, limit: 8 } })
    setAddOptions(r.data.map((p: any) => ({
      value: String(p.id),
      label: `${p.jizhanming} (${p.sku})`,
      product: p,
    })))
  }

  async function addProduct(pid: number) {
    try {
      await client.post('/sales/add_product', { product_id: pid, date: dateStr })
      setAddSearch('')
      setAddOptions([])
      loadSales()
    } catch {
      message.error('添加失败')
    }
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
    const local = localEdits[row.id]
    const newPos  = field === 'qty_pos'  ? val : (local?.pos  ?? row.qty_pos)
    const newCash = field === 'qty_cash' ? val : (local?.cash ?? row.qty_cash)
    // Optimistic update in canonical state
    setSales(prev => prev.map(s =>
      s.id === row.id ? { ...s, qty_pos: newPos, qty_cash: newCash, qty_sold: newPos + newCash } : s
    ))
    setLocalEdits(prev => { const n = { ...prev }; delete n[row.id]; return n })
    try {
      await client.post('/sales/upsert', {
        product_id: row.product_id,
        date: dateStr,
        qty_pos: newPos,
        qty_cash: newCash,
        notes: row.notes,
      })
    } catch {
      message.error('更新失败')
      loadSales() // revert on failure
    }
  }

  async function deleteRecord(id: number) {
    try {
      await client.delete(`/sales/record/${id}`)
      message.success('已删除')
      loadSales()
    } catch {
      message.error('删除失败')
    }
  }

  async function clearDay() {
    try {
      await client.delete('/sales/clear_day', { params: { date: dateStr } })
      message.success('已清空')
      loadSales()
    } catch {
      message.error('失败')
    }
  }

  const totalPos  = sales.reduce((s, r) => s + r.qty_pos, 0)
  const totalCash = sales.reduce((s, r) => s + r.qty_cash, 0)
  const totalSold = sales.reduce((s, r) => s + r.qty_sold, 0)

  const entryColumns: ColumnsType<SaleRow> = [
    {
      title: '系列', dataIndex: 'ip_series', width: 100,
      render: v => v ? <Tag color="blue">{v}</Tag> : '-',
    },
    { title: '记账名', dataIndex: 'jizhanming', width: 130 },
    { title: 'SKU', dataIndex: 'sku', width: 100, render: v => <Text code>{v}</Text> },
    {
      title: '单价', dataIndex: 'price', width: 70, align: 'right',
      render: v => v != null ? `C$${v}` : '-',
    },
    {
      title: '卡机',
      dataIndex: 'qty_pos',
      width: 100,
      align: 'center',
      render: (v, r) => (
        <InputNumber
          size="small"
          min={0}
          value={localEdits[r.id]?.pos ?? v}
          onChange={val => setLocalQty(r.id, 'pos', val ?? 0)}
          onBlur={() => upsert(r, 'qty_pos', localEdits[r.id]?.pos ?? v)}
          onPressEnter={() => upsert(r, 'qty_pos', localEdits[r.id]?.pos ?? v)}
          style={{ width: 70 }}
        />
      ),
    },
    {
      title: '现金/转账',
      dataIndex: 'qty_cash',
      width: 110,
      align: 'center',
      render: (v, r) => (
        <InputNumber
          size="small"
          min={0}
          value={localEdits[r.id]?.cash ?? v}
          onChange={val => setLocalQty(r.id, 'cash', val ?? 0)}
          onBlur={() => upsert(r, 'qty_cash', localEdits[r.id]?.cash ?? v)}
          onPressEnter={() => upsert(r, 'qty_cash', localEdits[r.id]?.cash ?? v)}
          style={{ width: 70 }}
        />
      ),
    },
    {
      title: '合计', dataIndex: 'qty_sold', width: 70, align: 'center',
      render: v => <Tag color={v > 0 ? 'green' : 'default'}>{v}</Tag>,
    },
    {
      title: '',
      key: 'del',
      width: 50,
      render: (_, r) => (
        <RoleGuard minRole="manager">
          <Popconfirm title="删除此记录？" onConfirm={() => deleteRecord(r.id)}>
            <Button size="small" danger type="text" icon={<DeleteOutlined />} />
          </Popconfirm>
        </RoleGuard>
      ),
    },
  ]

  const summaryColumns: ColumnsType<SummaryRow> = [
    { title: '日期', dataIndex: 'date', width: 110 },
    { title: '产品数', dataIndex: 'product_count', width: 80, align: 'center' },
    {
      title: '卡机', dataIndex: 'total_pos', width: 80, align: 'center',
      render: v => <Tag color="blue">{v}</Tag>,
    },
    {
      title: '现金/转账', dataIndex: 'total_cash', width: 100, align: 'center',
      render: v => <Tag color="cyan">{v}</Tag>,
    },
    {
      title: '总销量', dataIndex: 'total_sold', width: 80, align: 'center',
      render: v => <Tag color="green">{v}</Tag>,
    },
  ]

  const entryTab = (
    <div>
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }} align="middle">
        <Col>
          <DatePicker
            value={date}
            onChange={d => setDate(d ?? dayjs())}
            allowClear={false}
            style={{ width: 160 }}
          />
        </Col>
        <RoleGuard minRole="staff">
          <Col>
            <AutoComplete
              placeholder="搜索产品并添加"
              value={addSearch}
              options={addOptions}
              onSearch={searchToAdd}
              onSelect={(val, opt) => { addProduct(Number(val)); setAddSearch(opt.label as string) }}
              onClear={() => { setAddSearch(''); setAddOptions([]) }}
              allowClear
              style={{ width: 280 }}
            />
          </Col>
          <Col>
            <Button icon={<PlusOutlined />} onClick={() => setBatchOpen(true)}>批量导入</Button>
          </Col>
        </RoleGuard>
        <RoleGuard minRole="manager">
          <Col>
            <Popconfirm title={`清空 ${dateStr} 的所有销售记录？`} onConfirm={clearDay}>
              <Button danger>清空当日</Button>
            </Popconfirm>
          </Col>
        </RoleGuard>
      </Row>

      <Row gutter={12} style={{ marginBottom: 12 }}>
        {[
          { title: '卡机', value: totalPos },
          { title: '现金/转账', value: totalCash },
          { title: '总销量', value: totalSold },
          { title: '产品数', value: sales.length },
        ].map(s => (
          <Col key={s.title} xs={12} sm={6}>
            <Card size="small"><Statistic {...s} /></Card>
          </Col>
        ))}
      </Row>

      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={sales}
        columns={entryColumns}
        pagination={false}
        scroll={{ x: 700, y: 500 }}
      />

      <BatchSalesModal
        open={batchOpen}
        date={dateStr}
        onClose={() => setBatchOpen(false)}
        onDone={() => { setBatchOpen(false); loadSales() }}
      />
    </div>
  )

  const logTab = (
    <div>
      <Row gutter={12} align="middle" style={{ marginBottom: 12 }}>
        <Col>
          <Button onClick={loadSummary}>加载销售记录</Button>
        </Col>
        <RoleGuard minRole="manager">
          <Col>
            <Space>
              <DatePicker
                value={exportFrom}
                onChange={d => setExportFrom(d ?? dayjs().subtract(30, 'day'))}
                allowClear={false}
                style={{ width: 150 }}
              />
              <Text>至</Text>
              <DatePicker
                value={exportTo}
                onChange={d => setExportTo(d ?? dayjs())}
                allowClear={false}
                style={{ width: 150 }}
              />
              <Button
                icon={<ExportOutlined />}
                onClick={() => window.location.href = `/api/sales/export?from=${exportFrom.format('YYYY-MM-DD')}&to=${exportTo.format('YYYY-MM-DD')}`}
              >
                导出
              </Button>
            </Space>
          </Col>
        </RoleGuard>
      </Row>
      <Table
        rowKey="date"
        size="small"
        dataSource={summary}
        columns={summaryColumns}
        pagination={{ pageSize: 30, showTotal: t => `共 ${t} 天` }}
      />
    </div>
  )

  return (
    <Tabs
      items={[
        { key: 'entry', label: '销售录入', children: entryTab },
        { key: 'log',   label: '销售记录', children: logTab },
      ]}
      onChange={k => { if (k === 'log') loadSummary() }}
    />
  )
}
