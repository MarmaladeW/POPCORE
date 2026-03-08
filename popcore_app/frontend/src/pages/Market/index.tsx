import { useState, useEffect, useCallback } from 'react'
import {
  Table, Button, Space, Tag, Tabs, Switch, Statistic, Card,
  Row, Col, message, Badge, Tooltip, Typography,
} from 'antd'
import { SyncOutlined, ReloadOutlined, LinkOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'
import RoleGuard from '../../components/RoleGuard'

const { Text } = Typography

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

const STORE_COLORS: Record<string, string> = {
  popmart_ca: 'blue',
  mrpen:      'purple',
  whoopea:    'green',
}

export default function MarketPage() {
  const [overview, setOverview] = useState<OverviewRow[]>([])
  const [status, setStatus]     = useState<ScrapeStatus | null>(null)
  const [matchedOnly, setMatchedOnly] = useState(true)
  const [loading, setLoading]   = useState(false)
  const [scraping, setScraping] = useState(false)

  const loadOverview = useCallback(() => {
    setLoading(true)
    client.get('/market/overview', { params: { matched_only: matchedOnly ? 1 : 0 } })
      .then(r => setOverview(r.data))
      .finally(() => setLoading(false))
  }, [matchedOnly])

  const loadStatus = useCallback(() => {
    client.get('/market/status').then(r => {
      setStatus(r.data)
      setScraping(r.data.running)
    })
  }, [])

  useEffect(() => { loadOverview() }, [loadOverview])
  useEffect(() => { loadStatus() }, [loadStatus])

  // Poll while scraping
  useEffect(() => {
    if (!scraping) return
    const id = setInterval(() => {
      client.get('/market/status').then(r => {
        setStatus(r.data)
        if (!r.data.running) { setScraping(false); loadOverview() }
      })
    }, 5000)
    return () => clearInterval(id)
  }, [scraping, loadOverview])

  async function startScrape() {
    setScraping(true)
    try {
      await client.post('/market/scrape')
      message.info('爬取已启动，请稍候...')
    } catch (err: any) {
      setScraping(false)
      message.error(err?.response?.data?.error ?? '启动失败')
    }
  }

  const stores = status ? Object.keys(status.stores) : []
  const lastScraped = status?.stores
    ? Object.values(status.stores)
        .map(s => s.finished_at)
        .filter(Boolean)
        .sort()
        .at(-1)
    : null

  const overviewColumns: ColumnsType<OverviewRow> = [
    {
      title: '商店',
      dataIndex: 'store_key',
      width: 110,
      filters: stores.map(s => ({ text: s, value: s })),
      onFilter: (v, r) => r.store_key === v,
      render: (v, r) => <Tag color={STORE_COLORS[v] ?? 'default'}>{r.store_name || v}</Tag>,
    },
    {
      title: '标题',
      dataIndex: 'external_title',
      ellipsis: true,
      render: (v, r) => (
        <Space>
          <span>{v}</span>
          {r.url && (
            <a href={r.url} target="_blank" rel="noreferrer">
              <LinkOutlined />
            </a>
          )}
        </Space>
      ),
    },
    {
      title: '对应产品',
      dataIndex: 'jizhanming',
      width: 130,
      render: (v, r) => v
        ? <span>{v} {r.match_score && <Tag color="geekblue" style={{ fontSize: 11 }}>{r.match_score}</Tag>}</span>
        : <Text type="secondary">未匹配</Text>,
    },
    {
      title: '外部价格(CAD)',
      dataIndex: 'price_cad',
      width: 130,
      align: 'right',
      render: (v, r) => {
        if (v == null) return '-'
        return (
          <span>
            {r.on_sale ? <Tag color="red" style={{ marginRight: 4 }}>折扣</Tag> : null}
            C${v.toFixed(2)}
            {r.compare_at_price && r.compare_at_price > v
              ? <Text delete type="secondary" style={{ fontSize: 11, marginLeft: 4 }}>C${r.compare_at_price.toFixed(2)}</Text>
              : null}
          </span>
        )
      },
      sorter: (a, b) => (a.price_cad ?? 0) - (b.price_cad ?? 0),
    },
    {
      title: '我方单价',
      dataIndex: 'our_price',
      width: 90,
      align: 'right',
      render: v => v != null ? `C$${v}` : '-',
    },
    {
      title: '库存',
      dataIndex: 'in_stock',
      width: 70,
      align: 'center',
      render: v => v ? <Badge status="success" text="有" /> : <Badge status="default" text="无" />,
      filters: [{ text: '有库存', value: 1 }, { text: '缺货', value: 0 }],
      onFilter: (v, r) => r.in_stock === v,
    },
    {
      title: '更新',
      dataIndex: 'scraped_at',
      width: 130,
      render: v => v ? <Text type="secondary" style={{ fontSize: 11 }}>{v.slice(0, 16)}</Text> : '-',
    },
  ]

  const statusTab = (
    <div>
      <Row gutter={16} style={{ marginBottom: 20 }}>
        {stores.map(sk => {
          const s = status!.stores[sk]
          return (
            <Col key={sk} xs={24} sm={12} md={8}>
              <Card size="small" title={<Tag color={STORE_COLORS[sk] ?? 'default'}>{sk}</Tag>}>
                <Row gutter={8}>
                  <Col span={12}>
                    <Statistic title="爬取" value={s.products_scraped} />
                  </Col>
                  <Col span={12}>
                    <Statistic title="匹配" value={s.products_matched} />
                  </Col>
                </Row>
                <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
                  {s.status === 'error' && s.error_msg
                    ? <Text type="danger">{s.error_msg}</Text>
                    : <Tag color={s.status === 'done' ? 'green' : 'processing'}>{s.status}</Tag>}
                  {s.finished_at && <span style={{ marginLeft: 8 }}>{s.finished_at.slice(0, 16)}</span>}
                </div>
              </Card>
            </Col>
          )
        })}
      </Row>
      <RoleGuard minRole="manager">
        <Space>
          <Button
            type="primary"
            icon={scraping ? <SyncOutlined spin /> : <ReloadOutlined />}
            loading={scraping}
            onClick={startScrape}
          >
            {scraping ? '爬取中...' : '开始爬取'}
          </Button>
          <Button onClick={loadStatus} icon={<ReloadOutlined />}>刷新状态</Button>
        </Space>
      </RoleGuard>
      {lastScraped && (
        <div style={{ marginTop: 12, color: '#999', fontSize: 12 }}>
          最后更新：{lastScraped.slice(0, 16)}
        </div>
      )}
    </div>
  )

  const pricesTab = (
    <div>
      <Row gutter={12} align="middle" style={{ marginBottom: 12 }}>
        <Col>
          <Space>
            <span>仅显示已匹配</span>
            <Switch checked={matchedOnly} onChange={setMatchedOnly} />
          </Space>
        </Col>
        <Col>
          <Button icon={<ReloadOutlined />} onClick={loadOverview}>刷新</Button>
        </Col>
        <RoleGuard minRole="manager">
          <Col>
            <Button
              type="primary"
              icon={scraping ? <SyncOutlined spin /> : <SyncOutlined />}
              loading={scraping}
              onClick={startScrape}
            >
              {scraping ? '爬取中...' : '立即爬取'}
            </Button>
          </Col>
        </RoleGuard>
      </Row>
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={overview}
        columns={overviewColumns}
        pagination={{ pageSize: 80, showTotal: t => `共 ${t} 条` }}
        scroll={{ x: 900 }}
      />
    </div>
  )

  return (
    <Tabs
      items={[
        { key: 'prices', label: `市场价格 (${overview.length})`, children: pricesTab },
        { key: 'status', label: '爬取状态', children: statusTab },
      ]}
      onChange={k => { if (k === 'status') loadStatus() }}
    />
  )
}
