import { Alert, Button, Input, Space, Table, Tag, Typography, message } from 'antd'
import { useRef, useState } from 'react'
import client from '../../api/client'
import type { RestockSession } from './index'

const { Text } = Typography

export default function ReceivingStep({ session, onRefresh }: {
  session: RestockSession
  onRefresh: () => void
}) {
  const delivery = session.delivery
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const keys = useRef<Record<string, string>>({})

  if (!delivery) {
    return <Alert type="info" showIcon message="Confirm the physical pick before receiving." />
  }
  const activeDelivery = delivery

  const outstanding = activeDelivery.lines.filter(line => line.outstanding_transit > 0)
  const shortages = activeDelivery.lines.filter(
    line => line.requested_quantity > line.dispatched_quantity + line.short_quantity,
  )

  async function act(action: 'receive' | 'return' | 'short-close') {
    const lines = action === 'short-close'
      ? shortages.map(line => ({
        line_no: line.line_no,
        quantity: line.requested_quantity - line.dispatched_quantity - line.short_quantity,
      }))
      : outstanding.map(line => ({
        line_no: line.line_no, quantity: line.outstanding_transit,
      }))
    if (!lines.length) return
    const key = keys.current[action] || crypto.randomUUID()
    keys.current[action] = key
    setSaving(true)
    try {
      await client.post(`/restock/session/${session.id}/${action}`, {
        expected_version: activeDelivery.version,
        lines,
        ...(reason.trim() ? { reason: reason.trim() } : {}),
      }, { headers: { 'Idempotency-Key': key } })
      delete keys.current[action]
      message.success(action === 'receive' ? 'Received into floor stock' : 'Delivery updated')
      onRefresh()
    } catch (cause) {
      const text = (cause as { response?: { data?: { error?: string } } })?.response?.data?.error
      message.error(text || 'Delivery update failed; quantities are unchanged')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%', paddingTop: 16 }}>
      <Alert type="info" showIcon message="Confirm what physically arrived on the sales floor." />
      <Table
        size="small"
        rowKey="line_no"
        pagination={false}
        dataSource={activeDelivery.lines}
        columns={[
          { title: 'Product', render: (_, line) => {
            const item = session.items.find(value => value.product_id === line.product_id)
            return item?.jizhanming || item?.name_cn_en || `Product ${line.product_id}`
          } },
          { title: 'Requested', dataIndex: 'requested_quantity' },
          { title: 'Picked', dataIndex: 'dispatched_quantity' },
          { title: 'Received', dataIndex: 'received_quantity' },
          { title: 'In transit', dataIndex: 'outstanding_transit', render: value => <Tag color={value ? 'orange' : 'default'}>{value}</Tag> },
        ]}
      />
      {activeDelivery.status === 'completed' ? (
        <Alert type="success" showIcon message="Restock delivery is complete." />
      ) : (
        <>
          {outstanding.length > 0 && (
            <Space wrap>
              <Button type="primary" loading={saving} onClick={() => act('receive')}>Receive all in transit</Button>
              <Button loading={saving} onClick={() => act('return')}>Return all to source</Button>
            </Space>
          )}
          {shortages.length > 0 && (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Text>Unfilled requested quantities must be closed with a reason.</Text>
              <Input value={reason} onChange={event => setReason(event.target.value)} placeholder="Shortage reason" />
              <Button disabled={!reason.trim()} loading={saving} onClick={() => act('short-close')}>Close unfilled quantities</Button>
            </Space>
          )}
        </>
      )}
    </Space>
  )
}
