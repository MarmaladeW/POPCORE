import { useEffect, useState } from 'react'
import { Card, Col, Row, Typography, Spin, Tag } from 'antd'
import {
  AppstoreOutlined,
  InboxOutlined,
  DollarOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartTooltip, Legend, ResponsiveContainer,
} from 'recharts'
import client from '../../api/client'
import dayjs from 'dayjs'
import { useIsMobile } from '../../hooks/useIsMobile'

const { Title, Text } = Typography

interface StockSummary {
  products_tracked:  number
  total_upstairs_dan: number
  total_instore_dan:  number
  low_stock_count:   number
  out_of_stock_count: number
}

interface SalesSummaryRow {
  date:          string
  product_count: number
  total_sold:    number
  total_pos:     number
  total_cash:    number
}

interface SalesRow {
  price:    number
  qty_pos:  number
  qty_cash: number
  qty_sold: number
  jizhanming: string
  sku: string
  ip_series: string
}

interface StockRow {
  product_id:    number
  sku:           string
  jizhanming:    string
  ip_series:     string
  upstairs_dan:  number
  instore_dan:   number
}

interface ProductRow {
  id:           number
  sku:          string
  jizhanming:   string
  ip_series:    string
  product_type: string
}

function StatCard({
  title, value, sub, icon, accentColor,
}: {
  title: string
  value: React.ReactNode
  sub?: React.ReactNode
  icon: React.ReactNode
  accentColor: string
}) {
  return (
    <Card
      style={{
        borderLeft: `4px solid ${accentColor}`,
        borderRadius: 10,
        height: '100%',
      }}
      bodyStyle={{ padding: '16px 20px' }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <Text style={{ fontSize: 12, color: '#6b7280' }}>{title}</Text>
          <div style={{ fontSize: 24, fontWeight: 700, color: accentColor, lineHeight: 1.2, marginTop: 4 }}>
            {value}
          </div>
          {sub && <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 4 }}>{sub}</div>}
        </div>
        <div style={{
          width: 38, height: 38, borderRadius: 10,
          background: `${accentColor}18`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 18, color: accentColor,
        }}>
          {icon}
        </div>
      </div>
    </Card>
  )
}

export default function DashboardPage() {
  const isMobile = useIsMobile()
  const [stockSummary, setStockSummary] = useState<StockSummary | null>(null)
  const [salesSummary, setSalesSummary] = useState<SalesSummaryRow[]>([])
  const [todaySales,   setTodaySales]   = useState<SalesRow[]>([])
  const [lowStock,     setLowStock]     = useState<StockRow[]>([])
  const [products,     setProducts]     = useState<ProductRow[]>([])
  const [loading,      setLoading]      = useState(true)

  useEffect(() => {
    const today = dayjs().format('YYYY-MM-DD')
    Promise.all([
      client.get('/stock/summary'),
      client.get('/sales/summary'),
      client.get('/sales', { params: { date: today } }),
      client.get('/stock'),
      client.get('/products/search', { params: { limit: 500 } }),
    ]).then(([ss, summary, ts, stock, prods]) => {
      setStockSummary(ss.data)
      setSalesSummary(summary.data)
      setTodaySales(ts.data)
      const stockRows: StockRow[] = stock.data
      setLowStock(
        stockRows
          .filter(r => (r.upstairs_dan + r.instore_dan) > 0 && (r.upstairs_dan + r.instore_dan) <= 3)
          .slice(0, 8)
      )
      setProducts(prods.data)
    }).finally(() => setLoading(false))
  }, [])

  const totalUnits = stockSummary
    ? (stockSummary.total_upstairs_dan ?? 0) + (stockSummary.total_instore_dan ?? 0)
    : 0

  const todayRevenue = todaySales.reduce(
    (acc, r) => acc + (r.price ?? 0) * (r.qty_sold ?? 0),
    0
  )
  const todayUnitsSold = todaySales.reduce((acc, r) => acc + (r.qty_sold ?? 0), 0)

  // On mobile show 7-day trend, desktop 14-day
  const trendDays = isMobile ? 7 : 14
  const trendData = salesSummary
    .slice(0, trendDays)
    .reverse()
    .map(r => ({
      date:  dayjs(r.date).format(isMobile ? 'M/D' : 'MM/DD'),
      POS:   r.total_pos,
      Cash:  r.total_cash,
    }))

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 300 }}>
        <Spin size="large" />
      </div>
    )
  }

  const statCards = [
    {
      title: 'Total Products',
      value: products.length,
      sub: `${stockSummary?.low_stock_count ?? 0} low stock`,
      icon: <AppstoreOutlined />,
      accentColor: '#6366F1',
    },
    {
      title: 'Units in Stock',
      value: totalUnits.toLocaleString(),
      sub: `${stockSummary?.out_of_stock_count ?? 0} out of stock`,
      icon: <InboxOutlined />,
      accentColor: '#10B981',
    },
    {
      title: "Today's Revenue",
      value: `CA$${todayRevenue.toLocaleString('en-CA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
      sub: `${todayUnitsSold} units sold`,
      icon: <DollarOutlined />,
      accentColor: '#F59E0B',
    },
  ]

  return (
    <div>
      {/* Title */}
      {!isMobile && (
        <div style={{ marginBottom: 24 }}>
          <Title level={3} style={{ margin: 0 }}>Dashboard</Title>
          <Text style={{ color: '#6b7280' }}>
            Overview for {dayjs().format('dddd, MMMM D, YYYY')}
          </Text>
        </div>
      )}
      {isMobile && (
        <div style={{ marginBottom: 16 }}>
          <Text style={{ color: '#6b7280', fontSize: 13 }}>
            {dayjs().format('ddd, MMM D, YYYY')}
          </Text>
        </div>
      )}

      {/* Stat cards — 2×2 grid on mobile, 3-col on desktop */}
      <Row gutter={[12, 12]} style={{ marginBottom: isMobile ? 16 : 24 }}>
        {statCards.map(c => (
          <Col key={c.title} xs={12} sm={8}>
            <StatCard {...c} />
          </Col>
        ))}
      </Row>

      {/* Charts + Low Stock */}
      <Row gutter={[16, 16]}>
        {/* Sales Trend */}
        <Col xs={24} lg={14}>
          <Card
            title={`Sales Trend — Last ${trendDays} Days`}
            style={{ borderRadius: 10 }}
            bodyStyle={{ padding: isMobile ? '12px 12px 8px' : '16px 16px 8px' }}
          >
            <ResponsiveContainer width="100%" height={isMobile ? 180 : 240}>
              <LineChart data={trendData} margin={{ top: 5, right: isMobile ? 8 : 20, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="date" tick={{ fontSize: isMobile ? 10 : 11 }} />
                <YAxis tick={{ fontSize: isMobile ? 10 : 11 }} width={isMobile ? 28 : 40} />
                <RechartTooltip />
                <Legend />
                <Line type="monotone" dataKey="POS"  stroke="#6366F1" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="Cash" stroke="#10B981" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </Card>
        </Col>

        {/* Low Stock Alerts */}
        <Col xs={24} lg={10}>
          <Card
            title={
              <span>
                <WarningOutlined style={{ color: '#F59E0B', marginRight: 8 }} />
                Low Stock Alerts
              </span>
            }
            style={{ borderRadius: 10, height: '100%' }}
            bodyStyle={{ padding: '8px 0' }}
          >
            {lowStock.length === 0 ? (
              <div style={{ padding: '24px 16px', color: '#9ca3af', textAlign: 'center' }}>
                No low stock items
              </div>
            ) : (
              lowStock.map(item => {
                const total = item.upstairs_dan + item.instore_dan
                return (
                  <div
                    key={item.product_id}
                    style={{
                      padding:    '10px 16px',
                      borderBottom: '1px solid #f3f4f6',
                      display:    'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                    }}
                  >
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 500, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {item.jizhanming || item.sku}
                      </div>
                      <div style={{ fontSize: 11, color: '#9ca3af' }}>
                        {item.ip_series} · {item.sku}
                      </div>
                    </div>
                    <Tag
                      color={total === 0 ? 'red' : 'orange'}
                      style={{ borderRadius: 6, fontWeight: 600, flexShrink: 0 }}
                    >
                      {total}
                    </Tag>
                  </div>
                )
              })
            )}
          </Card>
        </Col>
      </Row>

      {/* Today's Sales — card list on mobile */}
      {isMobile && todaySales.length > 0 && (
        <div style={{ marginTop: 16, background: '#fff', borderRadius: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)', overflow: 'hidden' }}>
          <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0', fontWeight: 600, color: '#111827' }}>
            Today's Sales
            <Text style={{ color: '#9ca3af', fontWeight: 400, fontSize: 12, marginLeft: 8 }}>
              {todaySales.length} products
            </Text>
          </div>
          {todaySales
            .sort((a, b) => b.qty_sold - a.qty_sold)
            .slice(0, 10)
            .map((row, i) => (
              <div key={i} style={{ padding: '10px 16px', borderBottom: '1px solid #f5f5f5', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500, fontSize: 13, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {row.jizhanming || row.sku}
                  </div>
                  <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 1 }}>
                    POS: {row.qty_pos} · Cash: {row.qty_cash}
                  </div>
                </div>
                <div style={{ textAlign: 'right', flexShrink: 0, marginLeft: 12 }}>
                  <div style={{ fontWeight: 700, fontSize: 16, color: row.qty_sold > 0 ? '#10B981' : '#d1d5db' }}>
                    {row.qty_sold}
                  </div>
                  {row.price != null && (
                    <div style={{ fontSize: 11, color: '#6366F1' }}>
                      CA${((row.price ?? 0) * row.qty_sold).toFixed(2)}
                    </div>
                  )}
                </div>
              </div>
            ))}
        </div>
      )}
    </div>
  )
}
