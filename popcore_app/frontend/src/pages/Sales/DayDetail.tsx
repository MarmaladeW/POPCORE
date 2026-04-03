import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Button, Card, Col, Row, Spin, Table, Tag, Typography,
} from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import client from '../../api/client'
import { useIsMobile } from '../../hooks/useIsMobile'

// Chart.js is loaded from CDN — declare as ambient global
declare const Chart: any

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

const SIDEBAR_BG = '#0D1B2A'

export default function DayDetailPage() {
  const { date }  = useParams<{ date: string }>()
  const navigate  = useNavigate()
  const isMobile  = useIsMobile()

  const [rows,    setRows]    = useState<SaleRow[]>([])
  const [loading, setLoading] = useState(true)

  const canvasRef      = useRef<HTMLCanvasElement>(null)
  const chartInstance  = useRef<any>(null)

  // Fetch sales for this date
  useEffect(() => {
    if (!date) return
    setLoading(true)
    client.get('/sales', { params: { date } })
      .then(r => setRows(r.data))
      .finally(() => setLoading(false))
  }, [date])

  // Build / rebuild Chart.js bar chart whenever rows change
  useEffect(() => {
    if (!canvasRef.current || typeof Chart === 'undefined') return

    // Destroy previous chart if any
    if (chartInstance.current) {
      chartInstance.current.destroy()
      chartInstance.current = null
    }

    if (!rows.length) return

    const top10 = [...rows]
      .sort((a, b) => b.qty_sold - a.qty_sold)
      .slice(0, 10)

    const truncate = (s: string, n: number) =>
      s.length > n ? s.slice(0, n) + '…' : s

    chartInstance.current = new Chart(canvasRef.current, {
      type: 'bar',
      data: {
        labels: top10.map(r => truncate(r.jizhanming || r.sku || '—', 14)),
        datasets: [{
          label: 'Units Sold',
          data: top10.map(r => r.qty_sold),
          backgroundColor: '#6366F1',
          borderRadius: 4,
          borderSkipped: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items: any[]) => {
                const idx = items[0]?.dataIndex ?? 0
                return top10[idx]?.jizhanming || top10[idx]?.sku || '—'
              },
            },
          },
        },
        scales: {
          x: {
            ticks: { maxRotation: 40, font: { size: 11 }, color: '#6b7280' },
            grid: { display: false },
          },
          y: {
            beginAtZero: true,
            ticks: { precision: 0, font: { size: 11 }, color: '#6b7280' },
            grid: { color: '#f0f0f0' },
          },
        },
      },
    })

    return () => {
      if (chartInstance.current) {
        chartInstance.current.destroy()
        chartInstance.current = null
      }
    }
  }, [rows])

  // KPIs
  const totalRevenue = rows.reduce((s, r) => s + (r.price ?? 0) * r.qty_sold, 0)
  const totalUnits   = rows.reduce((s, r) => s + r.qty_sold, 0)
  const productCount = rows.length

  const formattedDate = date ? dayjs(date).format('dddd, MMMM D, YYYY') : ''

  const columns: ColumnsType<SaleRow> = [
    {
      title: 'Product',
      key: 'product',
      render: (_, r) => (
        <div>
          <div style={{ fontWeight: 500, fontSize: 13 }}>{r.jizhanming || '—'}</div>
          <div style={{ fontSize: 11, color: '#9ca3af' }}>{r.sku}</div>
        </div>
      ),
    },
    {
      title: 'Series', dataIndex: 'ip_series', width: 110,
      render: v => v ? <Tag color="blue" style={{ fontSize: 11 }}>{v}</Tag> : '—',
    },
    {
      title: 'POS', dataIndex: 'qty_pos', width: 70, align: 'center',
      render: v => <Tag color="blue">{v}</Tag>,
    },
    {
      title: 'Cash', dataIndex: 'qty_cash', width: 70, align: 'center',
      render: v => <Tag color="cyan">{v}</Tag>,
    },
    {
      title: 'Total', dataIndex: 'qty_sold', width: 70, align: 'center',
      render: v => (
        <Text style={{ fontWeight: 700, color: v > 0 ? '#10B981' : '#9ca3af' }}>{v}</Text>
      ),
    },
    {
      title: 'Unit Price', dataIndex: 'price', width: 90, align: 'right',
      render: v => v != null ? <Text style={{ fontSize: 12 }}>CA${v}</Text> : '—',
    },
    {
      title: 'Line Total', width: 100, align: 'right',
      render: (_, r) => {
        const rev = (r.price ?? 0) * r.qty_sold
        return <Text style={{ color: '#6366F1', fontSize: 12 }}>CA${rev.toFixed(2)}</Text>
      },
    },
  ]

  const kpis = [
    { label: 'Total Revenue',   value: `CA$${totalRevenue.toFixed(2)}`, color: '#6366F1' },
    { label: 'Units Sold',      value: totalUnits,                       color: '#10B981' },
    { label: 'Products Tracked', value: productCount,                    color: '#f59e0b' },
  ]

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto' }}>

      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12,
        marginBottom: isMobile ? 16 : 24,
        flexWrap: 'wrap',
      }}>
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate('/sales')}
          style={{ flexShrink: 0 }}
        >
          Sales Log
        </Button>
        <div>
          <Title level={isMobile ? 4 : 3} style={{ margin: 0 }}>
            {formattedDate}
          </Title>
          {!isMobile && (
            <Text style={{ color: '#6b7280', fontSize: 13 }}>
              Daily sales breakdown
            </Text>
          )}
        </div>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" />
        </div>
      ) : rows.length === 0 ? (
        <Card style={{ borderRadius: 10, textAlign: 'center', padding: '40px 0' }}>
          <Text style={{ color: '#9ca3af', fontSize: 15 }}>
            No sales recorded for this date.
          </Text>
        </Card>
      ) : (
        <>
          {/* KPI strip */}
          <Row gutter={[isMobile ? 8 : 16, isMobile ? 8 : 16]} style={{ marginBottom: isMobile ? 16 : 20 }}>
            {kpis.map(k => (
              <Col key={k.label} xs={8} sm={8}>
                <Card
                  style={{ borderRadius: 10, borderTop: `3px solid ${k.color}` }}
                  bodyStyle={{ padding: isMobile ? '10px 12px' : '14px 20px' }}
                >
                  <div style={{ fontSize: isMobile ? 10 : 12, color: '#9ca3af' }}>{k.label}</div>
                  <div style={{
                    fontSize:   isMobile ? 15 : 20,
                    fontWeight: 700,
                    color:      k.color,
                    marginTop:  4,
                  }}>
                    {k.value}
                  </div>
                </Card>
              </Col>
            ))}
          </Row>

          {/* Bar chart */}
          <Card
            title={isMobile ? undefined : 'Units Sold by Product (Top 10)'}
            style={{ borderRadius: 10, marginBottom: isMobile ? 16 : 20 }}
            bodyStyle={{ padding: isMobile ? '8px 12px' : '12px 20px' }}
          >
            {isMobile && (
              <div style={{ fontSize: 12, fontWeight: 600, color: '#374151', marginBottom: 8 }}>
                Units Sold by Product (Top 10)
              </div>
            )}
            <div style={{ height: isMobile ? 200 : 260, position: 'relative' }}>
              <canvas ref={canvasRef} />
            </div>
          </Card>

          {/* Product table */}
          <div style={{
            background:   '#fff',
            borderRadius: 10,
            boxShadow:    '0 1px 3px rgba(0,0,0,0.06)',
            overflow:     'hidden',
          }}>
            <div style={{
              padding:      '12px 16px',
              borderBottom: '1px solid #f0f0f0',
              fontWeight:   600,
              color:        '#111827',
            }}>
              All Products — {rows.length} line{rows.length !== 1 ? 's' : ''}
            </div>
            {isMobile ? (
              rows.map(row => (
                <div key={row.id} style={{ padding: '12px 16px', borderBottom: '1px solid #f5f5f5' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 500, fontSize: 13, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {row.jizhanming || '—'}
                      </div>
                      <div style={{ fontSize: 11, color: '#9ca3af' }}>{row.sku}</div>
                    </div>
                    <div style={{ flexShrink: 0, marginLeft: 12, textAlign: 'right' }}>
                      <Text style={{ fontWeight: 700, fontSize: 18, color: row.qty_sold > 0 ? '#10B981' : '#d1d5db' }}>
                        {row.qty_sold}
                      </Text>
                      {row.price != null && (
                        <div style={{ fontSize: 11, color: '#6366F1' }}>
                          CA${((row.price ?? 0) * row.qty_sold).toFixed(2)}
                        </div>
                      )}
                    </div>
                  </div>
                  <div style={{ marginTop: 6, fontSize: 11, color: '#6b7280' }}>
                    POS: <strong>{row.qty_pos}</strong> &nbsp;·&nbsp; Cash: <strong>{row.qty_cash}</strong>
                    {row.ip_series ? <>&nbsp;·&nbsp;<Tag color="blue" style={{ fontSize: 10, margin: 0 }}>{row.ip_series}</Tag></> : null}
                  </div>
                </div>
              ))
            ) : (
              <Table
                rowKey="id"
                size="middle"
                dataSource={rows}
                columns={columns}
                pagination={false}
                scroll={{ x: 800 }}
              />
            )}
          </div>
        </>
      )}
    </div>
  )
}
