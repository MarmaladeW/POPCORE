import { Alert, Button, Input, InputNumber, Space, Table, Tag, Typography, message } from 'antd'
import { useEffect, useState } from 'react'
import useCheckoutMutation from '../Sales/useCheckoutMutation'
import type { RestockSession } from './index'

const { Text } = Typography

export default function ReceivingStep({ session, onRefresh, onBusy }: {
  session: RestockSession
  onRefresh: () => void
  onBusy: (busy: boolean) => void
}) {
  const delivery = session.delivery
  const [reason, setReason] = useState('')
  const [quantities, setQuantities] = useState<Record<number, number>>({})
  const mutation = useCheckoutMutation(() => {
    message.success('Delivery updated')
    setQuantities({})
    onRefresh()
  })
  useEffect(() => {
    onBusy(mutation.pending)
    return () => onBusy(false)
  }, [mutation.pending, onBusy])

  if (!delivery) {
    return <Alert type="info" showIcon message="Confirm the physical pick before receiving." />
  }
  const activeDelivery = delivery

  const outstanding = activeDelivery.lines.filter(line => line.outstanding_transit > 0)
  const shortages = activeDelivery.lines.filter(
    line => line.requested_quantity > line.dispatched_quantity + line.short_quantity,
  )

  const quantity = (line: typeof outstanding[number]) => quantities[line.line_no] ?? line.outstanding_transit
  const validQuantities = outstanding.some(line => quantity(line) > 0) && outstanding.every(line => (
    Number.isSafeInteger(quantity(line)) && quantity(line) >= 0 && quantity(line) <= line.outstanding_transit
  ))

  function act(action: 'receive' | 'return' | 'short-close') {
    if (mutation.pending || (action === 'short-close' ? !reason.trim() : !validQuantities)) return
    const lines = action === 'short-close'
      ? shortages.map(line => ({
        line_no: line.line_no,
        quantity: line.requested_quantity - line.dispatched_quantity - line.short_quantity,
      }))
      : outstanding.filter(line => quantity(line) > 0).map(line => ({
        line_no: line.line_no, quantity: quantity(line),
      }))
    if (!lines.length) return
    mutation.run(`/restock/session/${session.id}/${action}`, {
      expected_version: activeDelivery.version,
      lines,
      ...(reason.trim() ? { reason: reason.trim() } : {}),
    })
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%', paddingTop: 16 }}>
      <Alert type="info" showIcon message="Confirm what physically arrived on the sales floor." />
      {mutation.notice}
      <Table
        size="small"
        rowKey="line_no"
        pagination={false}
        scroll={{ x: 650 }}
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
          { title: 'Quantity to receive / return', render: (_, line) => line.outstanding_transit > 0
            ? <InputNumber aria-label={`Line ${line.line_no} quantity`} min={0} max={line.outstanding_transit} precision={0}
                disabled={mutation.pending} value={quantity(line)}
                onChange={value => setQuantities(previous => ({ ...previous, [line.line_no]: value ?? 0 }))} />
            : '—' },
        ]}
      />
      {activeDelivery.status === 'completed' ? (
        <Alert type="success" showIcon message="Restock delivery is complete." />
      ) : (
        <>
          {outstanding.length > 0 && (
            <Space direction="vertical">
              <Text>Enter what arrived or returned. Use 0 to leave an item in transit.</Text>
              <Space wrap>
                <Button type="primary" loading={mutation.saving} disabled={mutation.pending || !validQuantities} onClick={() => act('receive')}>Receive quantities</Button>
                <Button loading={mutation.saving} disabled={mutation.pending || !validQuantities} onClick={() => act('return')}>Return quantities to source</Button>
              </Space>
            </Space>
          )}
          {shortages.length > 0 && (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Text>Unfilled requested quantities must be closed with a reason.</Text>
              <Input disabled={mutation.pending} value={reason} onChange={event => setReason(event.target.value)} placeholder="Shortage reason" />
              <Button disabled={mutation.pending || !reason.trim()} loading={mutation.saving} onClick={() => act('short-close')}>Close unfilled quantities</Button>
            </Space>
          )}
        </>
      )}
    </Space>
  )
}
