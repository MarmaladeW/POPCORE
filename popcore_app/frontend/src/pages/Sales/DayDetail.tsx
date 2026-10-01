import { useState, useEffect, useRef } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { useRole } from '../../auth/useRole'
import { savedLineValue, savedPriceSummary } from './historicalSales'
import {
  Alert, Button, Card, Col, Row, Spin, Table, Tag, Typography,
} from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import client from '../../api/client'
import { useIsMobile } from '../../hooks/useIsMobile'
import { useAppStore } from '../../store'

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
  unit_price: number | null
  ip_series: string
}

const SIDEBAR_BG = '#0D1B2A'

export default function DayDetailPage() {
  const { date }  = useParams<{ date: string }>()
  const { selectedStore } = useAppStore()
  const { user } = useAuth0(), role = useRole()
  if (!date || !selectedStore) return <Alert type="info" message="Select a store and date to review a historical report." />
  return <DayDetail key={`${user?.sub}|${role}|${selectedStore.code}|${date}`} date={date} storeCode={selectedStore.code} storeName={selectedStore.name} />
}

function DayDetail({date,storeCode,storeName}:{date:string;storeCode:string;storeName:string}) {
  const isMobile = useIsMobile()

  const [rows,    setRows]    = useState<SaleRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)

  const canvasRef      = useRef<HTMLCanvasElement>(null)
  const chartInstance  = useRef<any>(null)

  // Fetch sales for this date
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError(''); setRows([])
    client.get('/sales', { params: { date, store_code: storeCode }, signal: controller.signal })
      .then(r => { if (!controller.signal.aborted) setRows(r.data) })
      .catch(cause => { if (!controller.signal.aborted) setError(cause?._serverMessage || 'Unable to load this historical report.') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [date, storeCode, refresh])

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
  const estimate = savedPriceSummary(rows)
  const estimateValue = estimate.knownRows ? `CA$${estimate.knownValue.toFixed(2)}` : 'Unknown'
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
      title: 'Non-POS', dataIndex: 'qty_cash', width: 70, align: 'center',
      render: v => <Tag color="cyan">{v}</Tag>,
    },
    {
      title: 'Total', dataIndex: 'qty_sold', width: 70, align: 'center',
      render: v => (
        <Text style={{ fontWeight: 700, color: v > 0 ? '#10B981' : '#9ca3af' }}>{v}</Text>
      ),
    },
    {
      title: 'Saved unit price', dataIndex: 'unit_price', width: 90, align: 'right',
      render: v => v != null ? <Text style={{ fontSize: 12 }}>CA${v}</Text> : 'Unknown',
    },
    {
      title: 'Saved-price estimate', width: 100, align: 'right',
      render: (_, r) => {
        const value = savedLineValue(r)
        return <Text style={{ color: '#6366F1', fontSize: 12 }}>{value == null ? 'Unknown' : `CA$${value.toFixed(2)}`}</Text>
      },
    },
  ]

  const kpis = [
    { label: 'Saved-price estimate', value: estimateValue, color: '#6366F1' },
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
        <Link aria-label="Historical reports" to={`/sales?date=${date}`}><ArrowLeftOutlined /> Historical reports</Link>
        <div>
          <Title level={isMobile ? 4 : 3} style={{ margin: 0 }}>
            {formattedDate}
          </Title>
          {!isMobile && (
            <Text style={{ color: '#6b7280', fontSize: 13 }}>
              {storeName} · Historical pasted report
            </Text>
          )}
        </div>
      </div>

      <p>Saved report prices estimate product value. Payment totals and refunds are in <Link to="/reports">Insights &amp; reports</Link>.</p>
      {error ? <Alert role="alert" type="error" showIcon message={error} action={<Button onClick={() => setRefresh(value => value + 1)}>Retry</Button>} /> : loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" />
        </div>
      ) : rows.length === 0 ? (
        <Card style={{ borderRadius: 10, textAlign: 'center', padding: '40px 0' }}>
          <Text style={{ color: '#9ca3af', fontSize: 15 }}>
            No historical product quantities saved for this date.
          </Text>
        </Card>
      ) : (
        <>
          {!!estimate.unknownRows && <p>{estimate.unknownRows} {estimate.unknownRows === 1 ? 'product has' : 'products have'} no saved price. The estimate includes only products with a saved price.</p>}
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
                      <div style={{ fontSize: 11, color: '#6366F1' }}>
                        {savedLineValue(row) == null ? 'Price unknown' : `CA$${savedLineValue(row)!.toFixed(2)}`}
                      </div>
                    </div>
                  </div>
                  <div style={{ marginTop: 6, fontSize: 11, color: '#6b7280' }}>
                    POS: <strong>{row.qty_pos}</strong> &nbsp;·&nbsp; Non-POS: <strong>{row.qty_cash}</strong>
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
                virtual
                scroll={{ x: 800, y: 520 }}
              />
            )}
          </div>
        </>
      )}
    </div>
  )
}
