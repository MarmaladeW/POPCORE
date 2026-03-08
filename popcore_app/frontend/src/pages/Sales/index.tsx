import { useState, useEffect, useCallback } from 'react'
import {
  Table, Input, Button, Space, Tag, Tabs, Popconfirm, message,
  Typography, Row, Col, InputNumber, Statistic, Card, DatePicker,
} from 'antd'
import {
  PlusOutlined, ExportOutlined, DeleteOutlined, SearchOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
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
  const [date, setDate]         = useState(dayjs().format('YYYY-MM-DD'))
  const [sales, setSales]       = useState<SaleRow[]>([])
  const [summary, setSummary]   = useState<SummaryRow[]>([])
  const [loading, setLoading]   = useState(false)
  const [addSearch, setAddSearch] = useState('')
  const [addOptions, setAddOptions] = useState<any[]>([])
  const [editingId, setEditingId]   = useState<number | null>(null)
  const [batchOpen, setBatchOpen]   = useState(false)
  const [exportFrom, setExportFrom] = useState(dayjs().subtract(30, 'day').format('YYYY-MM-DD'))
  const [exportTo, setExportTo]     = useState(dayjs().format('YYYY-MM-DD'))

  const loadSales = useCallback(() => {
    setLoading(true)
    client.get('/sales', { params: { date } })
      .then(r => setSales(r.data))
      .finally(() => setLoading(false))
  }, [date])

  const loadSummary = useCallback(() => {
    client.get('/sales/summary').then(r => setSummary(r.data))
  }, [])

  useEffect(() => { loadSales() }, [loadSales])

  async function searchToAdd(v: string) {
    setAddSearch(v)
    if (!v) { setAddOptions([]); return }
    const r = await client.get('/products/search', { params: { q: v, limit: 8 } })
    setAddOptions(r.data)
  }

  async function addProduct(pid: number) {
    try {
      await client.post('/sales/add_product', { product_id: pid, date })
      setAddSearch('')
      setAddOptions([])
      loadSales()
    } catch {
      message.error('添加失败')
    }
  }

  async function upsert(row: SaleRow, field: 'qty_pos' | 'qty_cash', val: number) {
    const update = {
      product_id: row.product_id,
      date,
      qty_pos:  field === 'qty_pos'  ? val : row.qty_pos,
      qty_cash: field === 'qty_cash' ? val : row.qty_cash,
      notes: row.notes,
    }
    try {
      await client.post('/sales/upsert', update)
      setSales(prev => prev.map(s =>
        s.id === row.id ? {
          ...s,
          [field]: val,
          qty_sold: (field === 'qty_pos' ? val : s.qty_pos) + (field === 'qty_cash' ? val : s.qty_cash),
        } : s
      ))
    } catch {
      message.error('更新失败')
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
      await client.delete('/sales/clear_day', { params: { date } })
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
      render: v => v != null ? `¥${v}` : '-',
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
          value={v}
          onBlur={e => upsert(r, 'qty_pos', parseInt(e.target.value || '0', 10))}
          onPressEnter={e => upsert(r, 'qty_pos', parseInt((e.target as HTMLInputElement).value || '0', 10))}
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
          value={v}
          onBlur={e => upsert(r, 'qty_cash', parseInt(e.target.value || '0', 10))}
          onPressEnter={e => upsert(r, 'qty_cash', parseInt((e.target as HTMLInputElement).value || '0', 10))}
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
          <Input
            type="date"
            value={date}
            onChange={e => setDate(e.target.value)}
            style={{ width: 160 }}
          />
        </Col>
        <RoleGuard minRole="staff">
          <Col flex="auto">
            <div style={{ position: 'relative' }}>
              <Input
                prefix={<SearchOutlined />}
                placeholder="搜索产品并添加"
                value={addSearch}
                onChange={e => searchToAdd(e.target.value)}
                style={{ width: 280 }}
              />
              {addOptions.length > 0 && (
                <div style={{
                  position: 'absolute', top: '100%', left: 0, width: 340,
                  background: '#fff', border: '1px solid #d9d9d9',
                  borderRadius: 4, boxShadow: '0 2px 8px rgba(0,0,0,.15)', zIndex: 100,
                }}>
                  {addOptions.map(p => (
                    <div
                      key={p.id}
                      onClick={() => addProduct(p.id)}
                      style={{ padding: '6px 12px', cursor: 'pointer', borderBottom: '1px solid #f0f0f0' }}
                      onMouseEnter={e => (e.currentTarget.style.background = '#f5f5f5')}
                      onMouseLeave={e => (e.currentTarget.style.background = '')}
                    >
                      {p.jizhanming} <Text type="secondary" style={{ fontSize: 12 }}>({p.sku})</Text>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Col>
          <Col>
            <Button onClick={() => setBatchOpen(true)}>批量导入</Button>
          </Col>
        </RoleGuard>
        <RoleGuard minRole="manager">
          <Col>
            <Popconfirm title={`清空 ${date} 的所有销售记录？`} onConfirm={clearDay}>
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
        date={date}
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
              <Input
                type="date"
                value={exportFrom}
                onChange={e => setExportFrom(e.target.value)}
                style={{ width: 150 }}
              />
              <Text>至</Text>
              <Input
                type="date"
                value={exportTo}
                onChange={e => setExportTo(e.target.value)}
                style={{ width: 150 }}
              />
              <Button
                icon={<ExportOutlined />}
                onClick={() => window.location.href = `/api/sales/export?from=${exportFrom}&to=${exportTo}`}
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
