import { useState, useEffect, useCallback, useRef } from 'react'
import { Alert, Modal, Tabs, Spin, Tag, Steps } from 'antd'
import { InboxOutlined, CheckSquareOutlined, CheckCircleOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import client from '../../api/client'
import type { RestockSession } from './index'
import RequestStep from './RequestStep'
import PickingStep from './PickingStep'
import ReceivingStep from './ReceivingStep'

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
  const [error, setError] = useState('')
  const request = useRef(0)

  const loadSession = useCallback(async () => {
    if (!sessionId) return
    const current = ++request.current
    setLoading(true)
    setError('')
    try {
      const [{ data }, locations] = await Promise.all([
        client.get<RestockSession>(`/restock/session/${sessionId}`),
        client.get<Array<{ store_id: number }>>('/inventory/locations'),
      ])
      if (current !== request.current) return
      if (!locations.data.some(location => location.store_id === data.store_id)) {
        setSession(null)
        setError('You do not have inventory access to this restock session.')
        return
      }
      setSession(data)
      // Auto-switch tab based on status
      if (data.status === 'pending') setActiveTab('request')
      else setActiveTab('picking')
    } catch (cause: any) {
      if (current !== request.current) return
      setError(cause?.response?.data?.error || 'Unable to load this restock record.')
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    if (sessionId) {
      setSession(null)
      setActiveTab('request')
      loadSession()
    }
    return () => { request.current += 1 }
  }, [sessionId, loadSession])

  const status = session?.status ?? 'pending'

  const tabItems = [
    {
      key:      'request',
      label:    <span><InboxOutlined /> 录入补货</span>,
      children: session && !error && !loading ? <RequestStep session={session} onRefresh={loadSession} /> : null,
    },
    {
      key:      'picking',
      label:    <span><CheckSquareOutlined /> 仓库拣货</span>,
      disabled: status === 'pending',
      children: session && !error && !loading ? <PickingStep session={session} onRefresh={loadSession} /> : null,
    },
    {
      key: 'receiving',
      label: 'Receive',
      disabled: !session?.delivery,
      children: session && !error && !loading ? <ReceivingStep session={session} onRefresh={loadSession} /> : null,
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

  const stepIndex = status === 'pending' ? 0 : status === 'completed' ? 2 : 1
  const stepStatus = status === 'completed' ? 'finish' : 'process'

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
      ) : error && !session ? (
        <Alert type="error" showIcon message={error} />
      ) : (
        <>
          <div style={{ padding: '12px 24px 4px', borderBottom: '1px solid #f0f0f0' }}>
            <Steps
              size="small"
              current={stepIndex}
              status={stepStatus}
              items={[
                { title: '录入补货', icon: <InboxOutlined />, description: '填写补货数量' },
                { title: '仓库拣货', icon: <CheckSquareOutlined />, description: '上楼清点实数' },
                { title: '完成', icon: <CheckCircleOutlined />, description: '库存已更新' },
              ]}
            />
          </div>
          {error && <Alert type="error" showIcon message={error} />}
          <Tabs
            activeKey={activeTab}
            onChange={setActiveTab}
            items={tabItems}
            size="middle"
            style={{ padding: '0 16px' }}
          />
        </>
      )}
    </Modal>
  )
}
