import { useEffect, useState } from 'react'
import { LayoutGrid, Package, DollarSign, AlertTriangle } from 'lucide-react'
import { Spinner } from '../../components/Spinner'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartTooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import client from '../../api/client'
import dayjs from 'dayjs'
import { useIsMobile } from '../../hooks/useIsMobile'

interface StockSummary {
  products_tracked:   number
  total_upstairs_qty: number
  total_instore_qty:  number
  low_stock_count:    number
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
  product_id:   number
  sku:          string
  jizhanming:   string
  ip_series:    string
  upstairs_qty: number
  instore_qty:  number
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
    <Card style={{ borderLeft: `4px solid ${accentColor}`, height: '100%' }}>
      <CardContent style={{ padding: '16px 20px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
          <div>
            <p style={{ fontSize: 12, color: '#6b7280', margin: 0 }}>{title}</p>
            <div style={{ fontSize: 24, fontWeight: 700, color: accentColor, lineHeight: 1.2, marginTop: 4 }}>
              {value}
            </div>
            {sub && <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 4 }}>{sub}</div>}
          </div>
          <div style={{
            width: 38, height: 38, borderRadius: 10,
            background: `${accentColor}18`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: accentColor,
          }}>
            {icon}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export default function DashboardPage() {
  const isMobile = useIsMobile()
  const [stockSummary, setStockSummary] = useState<StockSummary | null>(null)
  const [salesSummary, setSalesSummary] = useState<SalesSummaryRow[]>([])
  const [todaySales,   setTodaySales]   = useState<SalesRow[]>([])
  const [lowStock,     setLowStock]     = useState<StockRow[]>([])
  const [productCount, setProductCount] = useState<number>(0)
  const [loading,      setLoading]      = useState(true)

  useEffect(() => {
    const today = dayjs().format('YYYY-MM-DD')
    Promise.all([
      client.get('/stock/summary'),
      client.get('/sales/summary'),
      client.get('/sales', { params: { date: today } }),
      client.get('/stock'),
      client.get('/products/count'),
    ]).then(([ss, summary, ts, stock, countRes]) => {
      setStockSummary(ss.data)
      setSalesSummary(summary.data)
      setTodaySales(ts.data)
      const stockRows: StockRow[] = stock.data
      setLowStock(
        stockRows
          .filter(r => (r.upstairs_qty + r.instore_qty) > 0 && (r.upstairs_qty + r.instore_qty) <= 3)
          .slice(0, 8)
      )
      setProductCount(countRes.data.count)
    }).finally(() => setLoading(false))
  }, [])

  const totalUnits = stockSummary
    ? (stockSummary.total_upstairs_qty ?? 0) + (stockSummary.total_instore_qty ?? 0)
    : 0

  const todayRevenue = todaySales.reduce(
    (acc, r) => acc + (r.price ?? 0) * (r.qty_sold ?? 0),
    0
  )
  const todayUnitsSold = todaySales.reduce((acc, r) => acc + (r.qty_sold ?? 0), 0)

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
        <Spinner />
      </div>
    )
  }

  const statCards = [
    {
      title: 'Total Products',
      value: productCount,
      sub: `${stockSummary?.low_stock_count ?? 0} low stock`,
      icon: <LayoutGrid size={18} />,
      accentColor: '#6366F1',
    },
    {
      title: 'Units in Stock',
      value: totalUnits.toLocaleString(),
      sub: `${stockSummary?.out_of_stock_count ?? 0} out of stock`,
      icon: <Package size={18} />,
      accentColor: '#10B981',
    },
    {
      title: "Today's Revenue",
      value: `CA$${todayRevenue.toLocaleString('en-CA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
      sub: `${todayUnitsSold} units sold`,
      icon: <DollarSign size={18} />,
      accentColor: '#6366F1',
    },
  ]

  return (
    <div>
      {/* Title */}
      {!isMobile && (
        <div style={{ marginBottom: 24 }}>
          <h3 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>Dashboard</h3>
          <span style={{ color: '#6b7280' }}>
            Overview for {dayjs().format('dddd, MMMM D, YYYY')}
          </span>
        </div>
      )}
      {isMobile && (
        <div style={{ marginBottom: 16 }}>
          <span style={{ color: '#6b7280', fontSize: 13 }}>
            {dayjs().format('ddd, MMM D, YYYY')}
          </span>
        </div>
      )}

      {/* Stat cards — 2-col on mobile, 3-col on sm+ */}
      <div
        className="grid grid-cols-2 sm:grid-cols-3"
        style={{ gap: 12, marginBottom: isMobile ? 16 : 24 }}
      >
        {statCards.map(c => (
          <StatCard key={c.title} {...c} />
        ))}
      </div>

      {/* Charts + Low Stock */}
      <div className="grid grid-cols-1 lg:grid-cols-7" style={{ gap: 16 }}>
        {/* Sales Trend */}
        <div className="lg:col-span-4">
          <Card>
            <CardHeader style={{ padding: isMobile ? '12px 12px 4px' : '16px 16px 4px' }}>
              <CardTitle style={{ fontSize: 14 }}>
                Sales Trend — Last {trendDays} Days
              </CardTitle>
            </CardHeader>
            <CardContent style={{ padding: isMobile ? '0 12px 12px' : '0 16px 16px' }}>
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
            </CardContent>
          </Card>
        </div>

        {/* Low Stock Alerts */}
        <div className="lg:col-span-3">
          <Card style={{ height: '100%' }}>
            <CardHeader style={{ padding: '16px 16px 8px' }}>
              <CardTitle style={{ fontSize: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
                <AlertTriangle size={16} style={{ color: '#F59E0B' }} />
                Low Stock Alerts
              </CardTitle>
            </CardHeader>
            <CardContent style={{ padding: '0 0 8px' }}>
              {lowStock.length === 0 ? (
                <div style={{ padding: '24px 16px', color: '#9ca3af', textAlign: 'center' }}>
                  No low stock items
                </div>
              ) : (
                lowStock.map(item => {
                  const total = item.upstairs_qty + item.instore_qty
                  return (
                    <div
                      key={item.product_id}
                      style={{
                        padding: '10px 16px',
                        borderBottom: '1px solid #f3f4f6',
                        display: 'flex',
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
                      <Badge variant={total === 0 ? 'destructive' : 'outline'}>
                        {total}
                      </Badge>
                    </div>
                  )
                })
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Today's Sales — card list on mobile */}
      {isMobile && todaySales.length > 0 && (
        <div style={{ marginTop: 16, background: '#fff', borderRadius: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)', overflow: 'hidden' }}>
          <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0', fontWeight: 600, color: '#111827' }}>
            Today's Sales
            <span style={{ color: '#9ca3af', fontWeight: 400, fontSize: 12, marginLeft: 8 }}>
              {todaySales.length} products
            </span>
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
