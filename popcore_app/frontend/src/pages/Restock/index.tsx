import { useState, useEffect, useCallback } from 'react'
import { Tabs, Typography, Tag, Spin, Grid } from 'antd'
import { InboxOutlined, CheckSquareOutlined, AuditOutlined } from '@ant-design/icons'
import client from '../../api/client'
import RequestStep from './RequestStep'
import PickingStep from './PickingStep'
import EveningCheckStep from './EveningCheckStep'

const { Title, Text } = Typography
const { useBreakpoint } = Grid

export interface RestockItem {
  id: number
  product_id: number
  session_id?: number
  requested_qty: number
  warehouse_stock_snapshot: number
  found_qty: number | null
  pick_status: 'pending' | 'found' | 'not_found'
  sku: string
  jizhanming: string
  name_cn_en: string
  ip_series: string
  product_type: string
  upstairs_dan: number
  instore_dan: number
}

export interface RestockSession {
  id: number
  date: string
  status: 'pending' | 'submitted' | 'picking' | 'completed'
  created_at: string
  submitted_at: string | null
  completed_at: string | null
  items: RestockItem[]
}

const STATUS_LABELS: Record<string, string> = {
  pending:   '录入中',
  submitted: '待拣货',
  picking:   '拣货中',
  completed: '已完成',
}

const STATUS_COLORS: Record<string, string> = {
  pending:   'processing',
  submitted: 'warning',
  picking:   'orange',
  completed: 'success',
}

export default function RestockPage() {
  const screens  = useBreakpoint()
  const isMobile = !screens.md

  const [session, setSession] = useState<RestockSession | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('request')

  const loadSession = useCallback(async () => {
    try {
      const { data } = await client.get<RestockSession>('/restock/session/today')
      const { data: full } = await client.get<RestockSession>(`/restock/session/${data.id}`)
      setSession(full)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadSession() }, [loadSession])

  // Auto-switch tab based on status
  useEffect(() => {
    if (!session) return
    if (session.status === 'pending') setActiveTab('request')
    else if (session.status === 'submitted' || session.status === 'picking') setActiveTab('picking')
    else if (session.status === 'completed') setActiveTab('picking')
  }, [session?.status])

  const status = session?.status ?? 'pending'

  const tabItems = [
    {
      key:      'request',
      label:    <span><InboxOutlined /> 录入补货</span>,
      children: session
        ? <RequestStep session={session} onRefresh={loadSession} />
        : null,
    },
    {
      key:      'picking',
      label:    <span><CheckSquareOutlined /> 仓库拣货</span>,
      disabled: status === 'pending',
      children: session
        ? <PickingStep session={session} onRefresh={loadSession} />
        : null,
    },
    {
      key:      'evening',
      label:    <span><AuditOutlined /> 晚盘核查</span>,
      children: <EveningCheckStep />,
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <Title level={4} style={{ margin: 0 }}>夜间补货 & 晚盘</Title>
        {session && (
          <>
            <Text type="secondary" style={{ fontSize: 13 }}>{session.date}</Text>
            <Tag color={STATUS_COLORS[status]}>{STATUS_LABELS[status]}</Tag>
          </>
        )}
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
      ) : (
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={tabItems}
          size={isMobile ? 'small' : 'middle'}
          style={{ background: '#fff', padding: isMobile ? '0 8px 8px' : '0 16px 16px', borderRadius: 8 }}
        />
      )}
    </div>
  )
}
