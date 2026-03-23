import { useState, useEffect, useCallback } from 'react'
import {
  Table, Button, Tag, Card, Row, Col,
  message, Typography, Switch, Space, Badge,
} from 'antd'
import {
  SyncOutlined, ReloadOutlined, LinkOutlined,
  RiseOutlined, FallOutlined, ShopOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'
import RoleGuard from '../../components/RoleGuard'
import dayjs from 'dayjs'
import { useIsMobile } from '../../hooks/useIsMobile'

const { Text, Title } = Typography

interface OverviewRow {
  id: number
  store_key: string
  store_name: string
  external_title: string
  price_cad: number | null
  compare_at_price: number | null
  on_sale: number
  in_stock: number
  url: string
  match_score: number | null
  scraped_at: string
  product_id: number | null
  jizhanming: string | null
  our_price: number | null
  ip_series: string | null
}

interface ScrapeStatus {
  running: boolean
  stores: Record<string, {
    status: string
    products_scraped: number
    products_matched: number
    error_msg: string | null
    finished_at: string | null
  }>
}

const STORE_KEYS = ['popmart_ca', 'mrpen', 'whoopea'] as const
const STORE_NAMES: Record<string, string> = {
  popmart_ca: 'PopMart CA',
  mrpen:      'MrPen',
  whoopea:    'Whoopea',
}
const STORE_COLORS: Record<string, string> = {
  popmart_ca: '#3b82f6',
  mrpen:      '#8b5cf6',
  whoopea:    '#10b981',
}

export default function MarketPage() {
  const isMobile = useIsMobile()
  const [overview,    setOverview]    = useState<OverviewRow[]>([])
  const [status,      setStatus]      = useState<ScrapeStatus | null>(null)
  const [matchedOnly, setMatchedOnly] = useState(true)
  const [loading,     setLoading]     = useState(false)
  const [scraping,    setScraping]    = useState<Record<string, boolean>>({})
  const [activeSource, setActiveSource] = useState<string>('all')

  const loadOverview = useCallback(() => {
    setLoading(true)
    client.get('/market/overview', { params: { matched_only: matchedOnly ? 1 : 0 } })
      .then(r => setOverview(r.data))
      .finally(() => setLoading(false))
  }, [matchedOnly])

  const loadStatus = useCallback(() => {
    client.get('/market/status').then(r => {
      setStatus(r.data)
      if (r.data.running) {
        setScraping(prev => ({ ...prev, all: true }))
      }
    })
  }, [])

  useEffect(() => { loadOverview() }, [loadOverview])
  useEffect(() => { loadStatus() },  [loadStatus])

  // Poll while any scrape is running
  useEffect(() => {
    const anyRunning = Object.values(scraping).some(Boolean)
    if (!anyRunning) return
    const id = setInterval(() => {
      client.get('/market/status').then(r => {
        setStatus(r.data)
        if (!r.data.running) {
          setScraping({})
          loadOverview()
        }
      })
    }, 5000)
    return () => clearInterval(id)
  }, [scraping, loadOverview])

  async function startScrape(storeKey?: string) {
    const key = storeKey ?? 'all'
    setScraping(prev => ({ ...prev, [key]: true }))
    try {
      const body = storeKey ? { stores: [storeKey] } : {}
      await client.post('/market/scrape', body)
      message.info(`Scraping ${storeKey ? STORE_NAMES[storeKey] : 'all stores'}...`)
    } catch (err: any) {
      setScraping(prev => ({ ...prev, [key]: false }))
      message.error(err?.response?.data?.error ?? 'Scrape failed')
    }
  }

  // Derive stat cards from overview
  const matched = overview.filter(r => r.product_id != null)
  const cheaper  = matched.filter(r => r.our_price != null && r.price_cad != null && r.our_price < r.price_cad).length
  const pricier  = matched.filter(r => r.our_price != null && r.price_cad != null && r.our_price > r.price_cad).length
  const diffs    = matched.filter(r => r.our_price != null && r.price_cad != null).map(r => r.our_price! - r.price_cad!)
  const avgDiff  = diffs.length ? diffs.reduce((a, b) => a + b, 0) / diffs.length : null

  const summaryCards = [
    {
      label: 'Products Tracked',
      value: matched.length,
      color: '#6366F1',
      icon: <ShopOutlined />,
    },
    {
      label: 'Cheaper than Market',
      value: cheaper,
      color: '#10B981',
      icon: <FallOutlined />,
      sub: `${matched.length ? Math.round(cheaper / matched.length * 100) : 0}%`,
    },
    {
      label: 'Overpriced vs Market',
      value: pricier,
      color: '#ef4444',
      icon: <RiseOutlined />,
      sub: `${matched.length ? Math.round(pricier / matched.length * 100) : 0}%`,
    },
    {
      label: 'Avg Price Diff',
      value: avgDiff != null ? `${avgDiff >= 0 ? '+' : ''}CA$${avgDiff.toFixed(2)}` : '—',
      color: avgDiff != null && avgDiff < 0 ? '#10B981' : '#f59e0b',
      icon: null,
      sub: 'vs market avg',
    },
  ]

  // Filter rows by active source
  const filteredRows = activeSource === 'all'
    ? overview
    : overview.filter(r => r.store_key === activeSource)

  const columns: ColumnsType<OverviewRow> = [
    {
      title: 'Product',
      dataIndex: 'jizhanming',
      width: 160,
      render: (v, r) => v
        ? (
          <div>
            <div style={{ fontWeight: 500, color: '#111827', fontSize: 13 }}>{v}</div>
            {r.ip_series && <div style={{ fontSize: 11, color: '#9ca3af' }}>{r.ip_series}</div>}
          </div>
        )
        : <Text type="secondary" style={{ fontSize: 12 }}>Unmatched</Text>,
    },
    {
      title: 'External Title',
      dataIndex: 'external_title',
      ellipsis: true,
      render: (v, r) => (
        <Space size={4}>
          <span style={{ fontSize: 12 }}>{v}</span>
          {r.url && (
            <a href={r.url} target="_blank" rel="noreferrer">
              <LinkOutlined style={{ color: '#9ca3af', fontSize: 11 }} />
            </a>
          )}
        </Space>
      ),
    },
    {
      title: 'Source',
      dataIndex: 'store_key',
      width: 110,
      render: (v, r) => (
        <Tag
          style={{
            background: `${STORE_COLORS[v] ?? '#6b7280'}18`,
            color: STORE_COLORS[v] ?? '#6b7280',
            border: `1px solid ${STORE_COLORS[v] ?? '#6b7280'}40`,
            borderRadius: 6,
            fontSize: 11,
          }}
        >
          {r.store_name || STORE_NAMES[v] || v}
        </Tag>
      ),
    },
    {
      title: 'Market Price',
      dataIndex: 'price_cad',
      width: 120,
      align: 'right',
      sorter: (a, b) => (a.price_cad ?? 0) - (b.price_cad ?? 0),
      render: (v, r) => {
        if (v == null) return <Text type="secondary">—</Text>
        const diff = r.our_price != null ? r.our_price - v : null
        const marketIsLower  = diff != null && diff > 0   // market price is lower than ours
        const marketIsHigher = diff != null && diff < 0   // market price is higher than ours
        return (
          <div style={{ textAlign: 'right' }}>
            <span style={{
              fontWeight: 600, fontSize: 13,
              color: marketIsHigher ? '#10b981' : marketIsLower ? '#ef4444' : '#374151',
            }}>
              CA${v.toFixed(2)}
            </span>
            {r.on_sale ? <Tag color="red" style={{ marginLeft: 4, fontSize: 10 }}>Sale</Tag> : null}
            {r.compare_at_price && r.compare_at_price > v
              ? <Text delete type="secondary" style={{ fontSize: 11, marginLeft: 4 }}>
                  CA${r.compare_at_price.toFixed(2)}
                </Text>
              : null}
          </div>
        )
      },
    },
    {
      title: 'Our Price',
      dataIndex: 'our_price',
      width: 90,
      align: 'right',
      render: v => v != null
        ? <Text style={{ color: '#6366F1', fontWeight: 600 }}>CA${v.toFixed(2)}</Text>
        : <Text type="secondary">—</Text>,
    },
    {
      title: 'Diff',
      width: 80,
      align: 'right',
      render: (_, r) => {
        if (r.our_price == null || r.price_cad == null) return <Text type="secondary">—</Text>
        const d = r.our_price - r.price_cad
        return (
          <Text style={{ color: d > 0 ? '#ef4444' : '#10b981', fontWeight: 600, fontSize: 12 }}>
            {d > 0 ? '+' : ''}{d.toFixed(2)}
          </Text>
        )
      },
    },
    {
      title: 'Stock',
      dataIndex: 'in_stock',
      width: 70,
      align: 'center',
      render: v => v
        ? <Badge status="success" text="In Stock" />
        : <Badge status="default" text="Out" />,
    },
    {
      title: 'Updated',
      dataIndex: 'scraped_at',
      width: 100,
      render: v => v
        ? <Text type="secondary" style={{ fontSize: 11 }}>{dayjs(v).format('MM/DD HH:mm')}</Text>
        : '—',
    },
  ]

  const lastScrapedByStore = (sk: string) => {
    const s = status?.stores?.[sk]
    if (!s?.finished_at) return null
    return dayjs(s.finished_at).format('MMM D, HH:mm')
  }

  const sourceFilterKeys = ['all', ...STORE_KEYS]

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <Title level={isMobile ? 4 : 3} style={{ margin: 0 }}>Market Prices</Title>
          {!isMobile && (
            <Text style={{ color: '#6b7280' }}>
              Competitor pricing across {STORE_KEYS.length} stores
            </Text>
          )}
        </div>
        <RoleGuard minRole="manager">
          <Button
            type="primary"
            icon={scraping.all ? <SyncOutlined spin /> : <SyncOutlined />}
            loading={!!scraping.all}
            onClick={() => startScrape()}
            style={isMobile ? { width: '100%', marginTop: 8 } : undefined}
          >
            {scraping.all ? 'Scraping...' : 'Scrape All'}
          </Button>
        </RoleGuard>
      </div>

      {/* Stat cards */}
      <Row gutter={[isMobile ? 8 : 16, isMobile ? 8 : 16]} style={{ marginBottom: isMobile ? 16 : 20 }}>
        {summaryCards.map(c => (
          <Col key={c.label} xs={12} sm={6}>
            <Card
              style={{ borderRadius: 10, borderLeft: `4px solid ${c.color}` }}
              bodyStyle={{ padding: isMobile ? '12px 14px' : '16px 20px' }}
            >
              <div style={{ fontSize: isMobile ? 11 : 12, color: '#9ca3af', marginBottom: 4 }}>{c.label}</div>
              <div style={{ fontSize: isMobile ? 18 : 22, fontWeight: 700, color: '#111827' }}>{c.value}</div>
              {c.sub && <div style={{ fontSize: 10, color: '#9ca3af', marginTop: 2 }}>{c.sub}</div>}
            </Card>
          </Col>
        ))}
      </Row>

      {/* Per-source scrape cards */}
      <Row gutter={[12, 12]} style={{ marginBottom: isMobile ? 16 : 20 }}>
        {STORE_KEYS.map(sk => {
          const s = status?.stores?.[sk]
          const isRunning = !!scraping[sk] || !!scraping.all
          return (
            <Col key={sk} xs={24} sm={8}>
              <Card
                size="small"
                style={{ borderRadius: 10, borderTop: `3px solid ${STORE_COLORS[sk]}` }}
                bodyStyle={{ padding: '12px 16px' }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, color: '#111827', fontSize: 13 }}>
                      {STORE_NAMES[sk]}
                    </div>
                    {s ? (
                      <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 2 }}>
                        {s.products_scraped} scraped · {s.products_matched} matched
                        {lastScrapedByStore(sk) && ` · ${lastScrapedByStore(sk)}`}
                      </div>
                    ) : (
                      <div style={{ fontSize: 11, color: '#9ca3af' }}>No data yet</div>
                    )}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0, marginLeft: 8 }}>
                    {s?.status && (
                      <Tag color={s.status === 'done' ? 'green' : s.status === 'error' ? 'red' : 'processing'}>
                        {s.status}
                      </Tag>
                    )}
                    <RoleGuard minRole="manager">
                      <Button
                        size="small"
                        icon={isRunning ? <SyncOutlined spin /> : <ReloadOutlined />}
                        loading={isRunning}
                        onClick={() => startScrape(sk)}
                        style={isMobile ? { width: '100%' } : undefined}
                      >
                        Scrape
                      </Button>
                    </RoleGuard>
                  </div>
                </div>
                {s?.error_msg && (
                  <Text type="danger" style={{ fontSize: 11, display: 'block', marginTop: 6 }}>
                    {s.error_msg}
                  </Text>
                )}
              </Card>
            </Col>
          )
        })}
      </Row>

      {/* Main data section */}
      <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)', overflow: 'hidden' }}>

        {/* Source filter — horizontal scroll pills on mobile, tabs on desktop */}
        <div style={{
          padding: isMobile ? '12px 16px 0' : '16px 20px 0',
          borderBottom: '1px solid #e5e7eb',
        }}>
          {isMobile ? (
            <div style={{
              display:    'flex',
              gap:        8,
              overflowX:  'auto',
              WebkitOverflowScrolling: 'touch' as any,
              paddingBottom: 12,
              scrollbarWidth: 'none',
            }}>
              {sourceFilterKeys.map(k => {
                const count = k === 'all' ? overview.length : overview.filter(r => r.store_key === k).length
                const isActive = activeSource === k
                return (
                  <button
                    key={k}
                    onClick={() => setActiveSource(k)}
                    style={{
                      flexShrink:   0,
                      padding:      '6px 14px',
                      borderRadius: 20,
                      border:       `1px solid ${isActive ? (STORE_COLORS[k] ?? '#6366F1') : '#e5e7eb'}`,
                      background:   isActive ? `${(STORE_COLORS[k] ?? '#6366F1')}18` : '#fff',
                      color:        isActive ? (STORE_COLORS[k] ?? '#6366F1') : '#6b7280',
                      fontSize:     13,
                      fontWeight:   isActive ? 600 : 400,
                      cursor:       'pointer',
                      whiteSpace:   'nowrap',
                    }}
                  >
                    {k === 'all' ? `All (${count})` : `${STORE_NAMES[k]} (${count})`}
                  </button>
                )
              })}
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <div style={{ display: 'flex', gap: 0 }}>
                {sourceFilterKeys.map(k => {
                  const count = k === 'all' ? overview.length : overview.filter(r => r.store_key === k).length
                  const isActive = activeSource === k
                  return (
                    <button
                      key={k}
                      onClick={() => setActiveSource(k)}
                      style={{
                        padding:      '10px 16px',
                        border:       'none',
                        borderBottom: isActive ? '2px solid #6366F1' : '2px solid transparent',
                        background:   'transparent',
                        cursor:       'pointer',
                        fontSize:     14,
                        fontWeight:   isActive ? 600 : 400,
                        color:        isActive ? '#6366F1' : '#6b7280',
                        marginBottom: -1,
                      }}
                    >
                      {k === 'all' ? `All Sources (${count})` : (
                        <span>
                          <span style={{ color: STORE_COLORS[k] }}>● </span>
                          {STORE_NAMES[k]} ({count})
                        </span>
                      )}
                    </button>
                  )
                })}
              </div>
              <Space size={8} style={{ marginBottom: 8 }}>
                <Space size={4}>
                  <Text style={{ fontSize: 12, color: '#6b7280' }}>Matched only</Text>
                  <Switch size="small" checked={matchedOnly} onChange={setMatchedOnly} />
                </Space>
                <Button size="small" icon={<ReloadOutlined />} onClick={loadOverview}>
                  Refresh
                </Button>
              </Space>
            </div>
          )}
        </div>

        {/* Matched-only toggle + refresh on mobile */}
        {isMobile && (
          <div style={{ padding: '10px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid #f0f0f0' }}>
            <Space size={6}>
              <Text style={{ fontSize: 13, color: '#6b7280' }}>Matched only</Text>
              <Switch size="small" checked={matchedOnly} onChange={setMatchedOnly} />
            </Space>
            <Button size="small" icon={<ReloadOutlined />} onClick={loadOverview}>Refresh</Button>
          </div>
        )}

        {/* Data: cards on mobile, table on desktop */}
        {isMobile ? (
          <div>
            {loading && (
              <div style={{ textAlign: 'center', padding: 32, color: '#9ca3af' }}>Loading...</div>
            )}
            {!loading && filteredRows.length === 0 && (
              <div style={{ textAlign: 'center', padding: 32, color: '#9ca3af', fontSize: 13 }}>No data</div>
            )}
            {filteredRows.map(row => {
              const diff = row.our_price != null && row.price_cad != null ? row.our_price - row.price_cad : null
              const storeColor = STORE_COLORS[row.store_key] ?? '#6b7280'
              return (
                <div key={row.id} style={{ padding: '14px 16px', borderBottom: '1px solid #f5f5f5' }}>
                  {/* Product name */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      {row.jizhanming ? (
                        <>
                          <div style={{ fontWeight: 600, fontSize: 13, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {row.jizhanming}
                          </div>
                          {row.ip_series && <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 1 }}>{row.ip_series}</div>}
                        </>
                      ) : (
                        <div style={{ fontSize: 12, color: '#9ca3af', fontStyle: 'italic' }}>Unmatched</div>
                      )}
                      <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {row.external_title}
                      </div>
                    </div>
                    {/* Competitor badge */}
                    <Tag
                      style={{
                        background:   `${storeColor}18`,
                        color:        storeColor,
                        border:       `1px solid ${storeColor}40`,
                        borderRadius: 6,
                        fontSize:     11,
                        flexShrink:   0,
                        marginLeft:   8,
                      }}
                    >
                      {STORE_NAMES[row.store_key] || row.store_key}
                    </Tag>
                  </div>
                  {/* Price row */}
                  <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                    <div>
                      <div style={{ fontSize: 10, color: '#9ca3af' }}>Market</div>
                      <div style={{ fontSize: 15, fontWeight: 700, color: diff != null && diff < 0 ? '#10b981' : diff != null && diff > 0 ? '#ef4444' : '#374151' }}>
                        {row.price_cad != null ? `CA$${row.price_cad.toFixed(2)}` : '—'}
                        {row.on_sale ? <Tag color="red" style={{ marginLeft: 4, fontSize: 10 }}>Sale</Tag> : null}
                      </div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, color: '#9ca3af' }}>Ours</div>
                      <div style={{ fontSize: 15, fontWeight: 700, color: '#6366F1' }}>
                        {row.our_price != null ? `CA$${row.our_price.toFixed(2)}` : '—'}
                      </div>
                    </div>
                    {diff != null && (
                      <div>
                        <div style={{ fontSize: 10, color: '#9ca3af' }}>Diff</div>
                        <div style={{ fontSize: 13, fontWeight: 700, color: diff > 0 ? '#ef4444' : '#10b981' }}>
                          {diff > 0 ? '▲' : '▼'} {Math.abs(diff).toFixed(2)}
                        </div>
                      </div>
                    )}
                    <div style={{ marginLeft: 'auto' }}>
                      {row.in_stock
                        ? <Badge status="success" text={<span style={{ fontSize: 11 }}>In Stock</span>} />
                        : <Badge status="default" text={<span style={{ fontSize: 11 }}>Out</span>} />
                      }
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <div style={{ padding: 20 }}>
            <Table
              rowKey="id"
              size="middle"
              loading={loading}
              dataSource={filteredRows}
              columns={columns}
              pagination={{ pageSize: 80, showTotal: t => `${t} products` }}
              scroll={{ x: 950 }}
            />
          </div>
        )}
      </div>
    </div>
  )
}
