import { useState, useEffect, useCallback } from 'react'
import {
  Table, Button, Tag, Select, Card, Row, Col,
  message, Typography, Space, Badge, Grid,
} from 'antd'
import {
  SyncOutlined, ReloadOutlined, CheckCircleOutlined,
  CloseCircleOutlined, ClockCircleOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'
import RoleGuard from '../../components/RoleGuard'
import dayjs from 'dayjs'

const { Text, Title } = Typography
const { useBreakpoint } = Grid

interface ScrapeLogRow {
  id: number
  store_key: string
  status: string
  products_scraped: number
  products_matched: number
  error_msg: string | null
  started_at: string
  finished_at: string | null
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

function statusIcon(s: string) {
  if (s === 'done')    return <CheckCircleOutlined style={{ color: '#10b981' }} />
  if (s === 'error')   return <CloseCircleOutlined style={{ color: '#ef4444' }} />
  if (s === 'running') return <SyncOutlined spin style={{ color: '#6366F1' }} />
  return <ClockCircleOutlined style={{ color: '#9ca3af' }} />
}

function durationStr(started: string, finished: string | null) {
  if (!finished) return '—'
  const s = dayjs(finished).diff(dayjs(started), 'second')
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

export default function ScrapeLogPage() {
  const screens  = useBreakpoint()
  const isMobile = !screens.md
  const [logs,       setLogs]       = useState<ScrapeLogRow[]>([])
  const [status,     setStatus]     = useState<ScrapeStatus | null>(null)
  const [loading,    setLoading]    = useState(false)
  const [scraping,   setScraping]   = useState<Record<string, boolean>>({})
  const [filterSrc,  setFilterSrc]  = useState<string>('')
  const [filterStat, setFilterStat] = useState<string>('')

  const loadLogs = useCallback(() => {
    setLoading(true)
    client.get('/market/scrape_log')
      .then(r => setLogs(r.data))
      .finally(() => setLoading(false))
  }, [])

  const loadStatus = useCallback(() => {
    client.get('/market/status').then(r => {
      setStatus(r.data)
      if (r.data.running) setScraping(prev => ({ ...prev, all: true }))
    })
  }, [])

  useEffect(() => { loadLogs(); loadStatus() }, [loadLogs, loadStatus])

  // Poll while scraping
  useEffect(() => {
    const anyRunning = Object.values(scraping).some(Boolean)
    if (!anyRunning) return
    const id = setInterval(() => {
      client.get('/market/status').then(r => {
        setStatus(r.data)
        if (!r.data.running) {
          setScraping({})
          loadLogs()
          loadStatus()
        }
      })
    }, 5000)
    return () => clearInterval(id)
  }, [scraping, loadLogs, loadStatus])

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

  // Summary stats
  const total      = logs.length
  const successful = logs.filter(r => r.status === 'done').length
  const failed     = logs.filter(r => r.status === 'error').length
  const totalScraped = logs.reduce((a, r) => a + (r.products_scraped ?? 0), 0)

  const summaryCards = [
    { label: 'Total Runs',        value: total,        color: '#6366F1' },
    { label: 'Successful',        value: successful,   color: '#10B981' },
    { label: 'Failed',            value: failed,       color: '#ef4444' },
    { label: 'Products Scraped',  value: totalScraped, color: '#f59e0b' },
  ]

  // Filtered rows
  const filteredLogs = logs.filter(r => {
    if (filterSrc  && r.store_key !== filterSrc)  return false
    if (filterStat && r.status   !== filterStat)  return false
    return true
  })

  const columns: ColumnsType<ScrapeLogRow> = [
    {
      title: 'Started',
      dataIndex: 'started_at',
      width: 140,
      render: v => (
        <Text style={{ fontSize: 12 }}>
          {v ? dayjs(v).format('MM/DD HH:mm:ss') : '—'}
        </Text>
      ),
    },
    {
      title: 'Source',
      dataIndex: 'store_key',
      width: 110,
      render: v => (
        <Tag
          style={{
            background: `${STORE_COLORS[v] ?? '#6b7280'}18`,
            color: STORE_COLORS[v] ?? '#6b7280',
            border: `1px solid ${STORE_COLORS[v] ?? '#6b7280'}40`,
            borderRadius: 6,
            fontSize: 11,
          }}
        >
          {STORE_NAMES[v] ?? v}
        </Tag>
      ),
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 100,
      render: v => (
        <Space size={4}>
          {statusIcon(v)}
          <Text style={{
            fontSize: 12,
            color: v === 'done' ? '#10b981' : v === 'error' ? '#ef4444' : '#6366F1',
            textTransform: 'capitalize',
          }}>
            {v}
          </Text>
        </Space>
      ),
    },
    {
      title: 'Scraped',
      dataIndex: 'products_scraped',
      width: 80,
      align: 'right',
      render: v => <Text style={{ fontWeight: 600 }}>{v ?? 0}</Text>,
    },
    {
      title: 'Matched',
      dataIndex: 'products_matched',
      width: 80,
      align: 'right',
      render: v => <Text style={{ color: '#6366F1', fontWeight: 600 }}>{v ?? 0}</Text>,
    },
    {
      title: 'Duration',
      width: 80,
      align: 'right',
      render: (_, r) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {durationStr(r.started_at, r.finished_at)}
        </Text>
      ),
    },
    {
      title: 'Completed',
      dataIndex: 'finished_at',
      width: 140,
      render: v => v
        ? <Text style={{ fontSize: 12 }}>{dayjs(v).format('MM/DD HH:mm:ss')}</Text>
        : <Badge status="processing" text="Running" />,
    },
    {
      title: 'Message',
      dataIndex: 'error_msg',
      ellipsis: true,
      render: v => v
        ? <Text type="danger" style={{ fontSize: 12 }}>{v}</Text>
        : <Text type="secondary" style={{ fontSize: 12 }}>—</Text>,
    },
  ]

  const STORE_KEYS = ['popmart_ca', 'mrpen', 'whoopea']

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>Scrape Log</Title>
          <Text style={{ color: '#6b7280' }}>Monitor scraping jobs and health per source</Text>
        </div>
        <RoleGuard minRole="manager">
          <Button
            type="primary"
            icon={scraping.all ? <SyncOutlined spin /> : <SyncOutlined />}
            loading={!!scraping.all}
            onClick={() => startScrape()}
          >
            {scraping.all ? 'Scraping...' : 'Scrape All'}
          </Button>
        </RoleGuard>
      </div>

      {/* Summary cards */}
      <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
        {summaryCards.map(c => (
          <Col key={c.label} xs={12} sm={6}>
            <Card
              style={{ borderRadius: 10, borderLeft: `4px solid ${c.color}` }}
              bodyStyle={{ padding: '16px 20px' }}
            >
              <div style={{ fontSize: 12, color: '#9ca3af', marginBottom: 4 }}>{c.label}</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: '#111827' }}>{c.value}</div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* Per-source health cards */}
      <Row gutter={[12, 12]} style={{ marginBottom: 20 }}>
        {STORE_KEYS.map(sk => {
          const s = status?.stores?.[sk]
          const isRunning = !!scraping[sk] || !!scraping.all
          const storeLogs = logs.filter(r => r.store_key === sk)
          const successes = storeLogs.filter(r => r.status === 'done').length
          const errors    = storeLogs.filter(r => r.status === 'error').length
          return (
            <Col key={sk} xs={24} sm={8}>
              <Card
                size="small"
                style={{ borderRadius: 10, borderTop: `3px solid ${STORE_COLORS[sk]}` }}
                bodyStyle={{ padding: '14px 16px' }}
              >
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                  <div>
                    <div style={{ fontWeight: 600, color: '#111827', marginBottom: 8 }}>
                      {STORE_NAMES[sk]}
                    </div>
                    <Space size={8}>
                      <span style={{ fontSize: 12 }}>
                        <CheckCircleOutlined style={{ color: '#10b981', marginRight: 3 }} />
                        <Text style={{ color: '#10b981', fontWeight: 600 }}>{successes}</Text>
                        <Text type="secondary" style={{ fontSize: 11 }}> success</Text>
                      </span>
                      <span style={{ fontSize: 12 }}>
                        <CloseCircleOutlined style={{ color: '#ef4444', marginRight: 3 }} />
                        <Text style={{ color: '#ef4444', fontWeight: 600 }}>{errors}</Text>
                        <Text type="secondary" style={{ fontSize: 11 }}> failed</Text>
                      </span>
                    </Space>
                    {s?.finished_at && (
                      <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 6 }}>
                        Last run: {dayjs(s.finished_at).format('MMM D, HH:mm')}
                        {' · '}
                        <Tag color={s.status === 'done' ? 'green' : s.status === 'error' ? 'red' : 'processing'}>
                          {s.status}
                        </Tag>
                      </div>
                    )}
                  </div>
                  <RoleGuard minRole="manager">
                    <Button
                      size="small"
                      icon={isRunning ? <SyncOutlined spin /> : <ReloadOutlined />}
                      loading={isRunning}
                      onClick={() => startScrape(sk)}
                    >
                      Scrape
                    </Button>
                  </RoleGuard>
                </div>
              </Card>
            </Col>
          )
        })}
      </Row>

      {/* Log table */}
      <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)', overflow: 'hidden' }}>
        <div style={{ padding: '16px 20px', borderBottom: '1px solid #e5e7eb', display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontWeight: 600, fontSize: 14, color: '#111827', marginRight: 8 }}>
            Run History
          </span>
          <Select
            placeholder="All Sources"
            allowClear
            style={{ width: 130 }}
            options={STORE_KEYS.map(sk => ({ value: sk, label: STORE_NAMES[sk] }))}
            onChange={v => setFilterSrc(v ?? '')}
          />
          <Select
            placeholder="All Statuses"
            allowClear
            style={{ width: 130 }}
            options={[
              { value: 'done',    label: 'Done' },
              { value: 'error',   label: 'Error' },
              { value: 'running', label: 'Running' },
            ]}
            onChange={v => setFilterStat(v ?? '')}
          />
          <div style={{ marginLeft: isMobile ? 0 : 'auto' }}>
            <Button icon={<ReloadOutlined />} onClick={() => { loadLogs(); loadStatus() }}>
              Refresh
            </Button>
          </div>
        </div>
        <div style={{ padding: 20 }}>
          <Table
            rowKey="id"
            size="middle"
            loading={loading}
            dataSource={filteredLogs}
            columns={columns}
            pagination={{ pageSize: 50, showTotal: t => `${t} runs` }}
            scroll={{ x: 900 }}
          />
        </div>
      </div>
    </div>
  )
}
