import { useState, useEffect, useCallback } from 'react'
import { Modal, Tabs, Spin, Tag } from 'antd'
import { InboxOutlined, CheckSquareOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import client from '../../api/client'
import type { RestockSession } from './index'
import RequestStep from './RequestStep'
import PickingStep from './PickingStep'

interface Props {
  sessionId: number | null
  onClose: () => void   // called when modal closes; triggers list refresh in parent
}

const STATUS_COLORS: Record<string, string> = {
  pending:   'processing',
  submitted: 'warning',
  picking:   'orange',
  completed: 'success',
}
const STATUS_LABELS: Record<string, string> = {
  pending:   '录入中',
  submitted: '待拣货',
  picking:   '拣货中',
  completed: '已完成',
}

export default function SessionModal({ sessionId, onClose }: Props) {
  const [session, setSession] = useState<RestockSession | null>(null)
  const [loading, setLoading] = useState(false)
  const [activeTab, setActiveTab] = useState('request')

  const loadSession = useCallback(async () => {
    if (!sessionId) return
    setLoading(true)
    try {
      const { data } = await client.get<RestockSession>(`/restock/session/${sessionId}`)
      setSession(data)
      // Auto-switch tab based on status
      if (data.status === 'pending') setActiveTab('request')
      else setActiveTab('picking')
      // Auto-close when completed
      if (data.status === 'completed') {
        setTimeout(() => onClose(), 1200)
      }
    } finally {
      setLoading(false)
    }
  }, [sessionId, onClose])

  useEffect(() => {
    if (sessionId) {
      setSession(null)
      setActiveTab('request')
      loadSession()
    }
  }, [sessionId, loadSession])

  const status = session?.status ?? 'pending'

  const tabItems = [
    {
      key:      'request',
      label:    <span><InboxOutlined /> 录入补货</span>,
      children: session ? <RequestStep session={session} onRefresh={loadSession} /> : null,
    },
    {
      key:      'picking',
      label:    <span><CheckSquareOutlined /> 仓库拣货</span>,
      disabled: status === 'pending',
      children: session ? <PickingStep session={session} onRefresh={loadSession} /> : null,
    },
  ]

  const title = session
    ? <>
        补货单 #{session.id}
        <span style={{ marginLeft: 10, fontSize: 13, color: '#6b7280' }}>
          {dayjs(session.created_at).format('HH:mm')}
        </span>
        <Tag color={STATUS_COLORS[status]} style={{ marginLeft: 10 }}>
          {STATUS_LABELS[status]}
        </Tag>
      </>
    : '补货单'

  return (
    <Modal
      open={!!sessionId}
      onCancel={onClose}
      footer={null}
      title={title}
      width={900}
      destroyOnClose
      styles={{ body: { padding: '0 0 8px' } }}
    >
      {loading && !session ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
      ) : (
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={tabItems}
          size="middle"
          style={{ padding: '0 16px' }}
        />
      )}
    </Modal>
  )
}
