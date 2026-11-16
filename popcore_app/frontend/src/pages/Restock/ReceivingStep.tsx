import { Alert, Button, Input, InputNumber, Space, Table, Tag, Typography, message } from 'antd'
import { useEffect, useState } from 'react'
import useCheckoutMutation from '../Sales/useCheckoutMutation'
import type { RestockSession } from './index'
import { useIsPhone } from '../../components/pc/useIsPhone'
import QtyStepper from './QtyStepper'
import { partLabel } from './series'
import { MAX_QUANTITY, setAsBoxes } from './units'

const { Text } = Typography

export default function ReceivingStep({ session, onRefresh, onBusy }: {
  session: RestockSession
  onRefresh: () => void
  onBusy: (busy: boolean) => void
}) {
  const delivery = session.delivery
  const phone = useIsPhone()
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

  type Line = typeof activeDelivery.lines[number]
  const open = activeDelivery.status !== 'completed'
  const outstanding = activeDelivery.lines.filter(line => line.outstanding_transit > 0)
  const shortages = activeDelivery.lines.filter(
    line => line.requested_quantity > line.dispatched_quantity + line.short_quantity,
  )
  // The floor counts what really came down (owner, 2026-11-15): a line in transit starts at what the
  // pick recorded and can be changed down or up; a line the pick left out starts at 0 and can still be
  // counted when it did come down. More than the pick comes straight off upstairs.
  const unfilled = (line: Line) => line.requested_quantity > line.dispatched_quantity + line.short_quantity
  const countable = open ? activeDelivery.lines.filter(line => line.outstanding_transit > 0 || unfilled(line)) : []
  const canCount = (line: Line) => countable.includes(line)

  const quantity = (line: Line) => quantities[line.line_no] ?? line.outstanding_transit
  const validQuantities = countable.some(line => quantity(line) > 0) && countable.every(line => (
    Number.isSafeInteger(quantity(line)) && quantity(line) >= 0 && quantity(line) <= MAX_QUANTITY
  ))
  // Only what is in transit can go back upstairs.
  const canReturn = validQuantities && countable.every(line => quantity(line) <= line.outstanding_transit)
  const item = (line: Line) => session.items.find(value => value.product_id === line.product_id)
  const asked = (line: Line) => item(line)?.requested_qty ?? line.requested_quantity

  // What a line's count means, said under it: a set goes on the floor as boxes, and a count that
  // differs from the pick says where the difference goes.
  function notes(line: Line) {
    const result: string[] = []
    if (canCount(line)) {
      const boxes = setAsBoxes(quantity(line), line.boxes_per_set)
      if (boxes) result.push(`按盒上架 ${boxes}`)
      const difference = quantity(line) - line.outstanding_transit
      if (difference > 0) result.push(`比拣货多 ${difference}，多的直接从楼上扣 +${difference} from upstairs`)
      if (difference < 0 && quantity(line) > 0) result.push(`少 ${-difference}，留在在途：没下来就退回楼上`)
    } else if (line.landed_boxes) {
      result.push(`已上架 ${line.landed_boxes} 盒 (${setAsBoxes(line.received_quantity, line.boxes_per_set)})`)
    }
    return result
  }

  function act(action: 'receive' | 'return' | 'short-close') {
    if (mutation.pending || (action === 'short-close' ? !reason.trim() : !(action === 'return' ? canReturn : validQuantities))) return
    const lines = action === 'short-close'
      ? shortages.map(line => ({
        line_no: line.line_no,
        quantity: line.requested_quantity - line.dispatched_quantity - line.short_quantity,
      }))
      : countable.filter(line => quantity(line) > 0).map(line => ({
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
    <Space className="pc-recv" direction="vertical" size="middle" style={{ width: '100%', paddingTop: 16 }}>
      <Alert type="info" showIcon message="按楼下实际看到的数量确认 · Confirm what physically arrived on the sales floor."
        description={open ? '数量和拣货不一样就改成实际看到的。整端到楼下按盒上架。Change a quantity to what you actually see.' : undefined} />
      {mutation.notice}
      {phone ? <ul className="pc-rq-groups pc-recv-lines" aria-label="Delivery lines">
        {activeDelivery.lines.map(line => {
          const sheet = item(line)
          const name = sheet?.family_title || sheet?.jizhanming || sheet?.name_cn_en || `Product ${line.product_id}`
          const part = sheet && sheet.role && sheet.role !== 'item' ? partLabel(sheet.role, sheet.design_name) : ''
          return <li key={line.line_no} className="pc-recv-line">
            <span className="pc-pick-line-text">
              <span className="pc-rq-line-name">{name}{part && <small> · {part}</small>}</span>
              <small>申请 {asked(line)} · 已拣 {line.dispatched_quantity} · 已收 {line.received_quantity}
                {' · '}<b className={line.outstanding_transit ? 'pc-recv-transit' : undefined}>在途 {line.outstanding_transit}</b></small>
              {notes(line).map(note => <small key={note} className="pc-set-lands">{note}</small>)}
            </span>
            {canCount(line) && <QtyStepper label={`Line ${line.line_no} quantity`} min={0} max={MAX_QUANTITY}
              disabled={mutation.pending} value={quantity(line)}
              onChange={value => setQuantities(previous => ({ ...previous, [line.line_no]: value }))} />}
          </li>
        })}
      </ul> : <Table
        size="small"
        rowKey="line_no"
        pagination={false}
        scroll={{ x: 650 }}
        dataSource={activeDelivery.lines}
        columns={[
          { title: '产品 Product', render: (_, line) => <>
            {item(line)?.jizhanming || item(line)?.name_cn_en || `Product ${line.product_id}`}
            {notes(line).map(note => <div key={note} className="pc-set-lands">{note}</div>)}
          </> },
          { title: '申请 Requested', render: (_, line) => asked(line) },
          { title: '已拣 Picked', dataIndex: 'dispatched_quantity' },
          { title: '已收 Received', dataIndex: 'received_quantity' },
          { title: '在途 In transit', dataIndex: 'outstanding_transit', render: value => <Tag color={value ? 'orange' : 'default'}>{value}</Tag> },
          { title: '实际到店 Quantity to receive / return', render: (_, line) => canCount(line)
            ? <InputNumber aria-label={`Line ${line.line_no} quantity`} min={0} max={MAX_QUANTITY} precision={0}
                disabled={mutation.pending} value={quantity(line)}
                onChange={value => setQuantities(previous => ({ ...previous, [line.line_no]: value ?? 0 }))} />
            : '—' },
        ]}
      />}
      {activeDelivery.status === 'completed' ? (
        <Alert type="success" showIcon message="Restock delivery is complete." />
      ) : (
        <>
          {countable.length > 0 && (
            <Space direction="vertical">
              <Text>填实际到店的数量：可以比拣货少，也可以比拣货多；0 表示先留在在途。Enter what arrived or returned. Use 0 to leave an item in transit.</Text>
              <Space wrap className="pc-recv-actions">
                <Button type="primary" loading={mutation.saving} disabled={mutation.pending || !validQuantities} onClick={() => act('receive')}>确认到店 Receive quantities</Button>
                {outstanding.length > 0 && <Button loading={mutation.saving} disabled={mutation.pending || !canReturn} onClick={() => act('return')}>退回楼上 Return quantities to source</Button>}
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
