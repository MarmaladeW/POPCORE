import { Alert, Button, Card, Descriptions, Input, InputNumber, List, Select, Space, Spin, Tag, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { useHasRole } from '../../auth/useRole'
import {
  addSaleSource, allocateSale, fetchSale, newRequestKey, postSale,
  recordPhysicalReturn, recordRefund, reviewEvidence, reviewPayment, type SaleResult,
} from '../../api/salesDocuments'

const { Title } = Typography

export default function SaleDocumentPage() {
  const { id } = useParams()
  const saleId = Number(id)
  const navigate = useNavigate()
  const isManager = useHasRole('manager')
  const [sale, setSale] = useState<SaleResult | null>(null)
  const [error, setError] = useState('')
  const [reason, setReason] = useState('')
  const [productMappings, setProductMappings] = useState<Record<number, number>>({})
  const [actionReasons, setActionReasons] = useState<Record<string, string>>({})
  const [refundAmounts, setRefundAmounts] = useState<Record<number, number>>({})
  const [source, setSource] = useState({ source_system: '', source_account: '', source_reference: '' })
  const [returnLine, setReturnLine] = useState(1)
  const [returnQuantity, setReturnQuantity] = useState(1)
  const [returnDisposition, setReturnDisposition] = useState<'saleable'|'damaged'|'hold'>('saleable')
  const [working, setWorking] = useState(false)
  const postKey = useRef(newRequestKey())
  const allocationKey = useRef(newRequestKey())
  const actionKeys = useRef<Record<string, string>>({})

  function requestKey(intent: string) {
    return actionKeys.current[intent] ?? (actionKeys.current[intent] = newRequestKey())
  }

  async function runAction(intent: string, action: (key: string) => Promise<unknown>) {
    setWorking(true); setError('')
    try {
      await action(requestKey(intent))
      delete actionKeys.current[intent]
      refresh()
    } catch (error: any) {
      setError(error?._serverMessage || 'The action was not confirmed. Review current facts and retry safely.')
    } finally { setWorking(false) }
  }

  function refresh() {
    setError('')
    fetchSale(saleId).then(setSale).catch(() => setError('Unable to load this sale.'))
  }
  useEffect(refresh, [saleId]) // eslint-disable-line react-hooks/exhaustive-deps

  async function postDraft() {
    if (!sale) return
    setWorking(true)
    try {
      await postSale(saleId, sale.version, postKey.current)
      postKey.current = newRequestKey()
      refresh()
    } catch {
      setError('Posting was not confirmed. Retry to recover the same request safely.')
    } finally { setWorking(false) }
  }

  async function retryAllocation() {
    if (!sale || !reason.trim()) return
    setWorking(true)
    try {
      const mappings = Object.entries(productMappings).filter(([, product]) => product > 0)
        .map(([line, product]) => ({ line_no: Number(line), product_id: product }))
      await allocateSale(saleId, sale.version, reason.trim(), mappings, allocationKey.current)
      allocationKey.current = newRequestKey()
      setReason('')
      refresh()
    } catch {
      setError('Stock still cannot be allocated. Review stock, product mapping, and set selection.')
    } finally { setWorking(false) }
  }

  if (!sale && !error) return <Spin />
  if (!sale) return <Alert type="error" showIcon message={error} />
  const allocationColor = sale.allocation_status === 'allocated' ? 'green'
    : sale.allocation_status === 'pending' ? 'orange' : 'default'
  return (
    <div style={{ maxWidth: 800, margin: '0 auto' }}>
      <Title level={3}>Sale #{sale.sale_id}</Title>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
      <Card>
        <Descriptions column={1} size="small">
          <Descriptions.Item label="Document"><Tag>{sale.status}</Tag></Descriptions.Item>
          <Descriptions.Item label="Payment facts"><Tag>{sale.financial_status ?? 'draft'}</Tag></Descriptions.Item>
          <Descriptions.Item label="Stock"><Tag color={allocationColor}>{sale.allocation_status ?? 'draft'}</Tag></Descriptions.Item>
          <Descriptions.Item label="Collected">{sale.collected_cents == null ? 'Unknown' : `$${(sale.collected_cents / 100).toFixed(2)}`}</Descriptions.Item>
        </Descriptions>
        {sale.unresolved_reasons?.length ? (
          <Alert type="warning" showIcon message="Stock allocation needs review"
            description={sale.unresolved_reasons.join(', ')} style={{ marginTop: 12 }} />
        ) : null}
      </Card>
      <Card title="Items" style={{ marginTop: 16 }}>
        <List dataSource={sale.lines ?? []} renderItem={line => (
          <List.Item>{line.quantity} {line.unit ?? 'unmapped'} — {line.product_name_snapshot ?? line.raw_product_text}</List.Item>
        )} />
      </Card>
      <Card title="Source history" style={{ marginTop: 16 }}>
        <List locale={{ emptyText: 'No source links' }} dataSource={sale.sources ?? []}
          renderItem={sourceItem => <List.Item>{sourceItem.source_system} / {sourceItem.source_account} / {sourceItem.source_reference}</List.Item>} />
        {isManager && <Space wrap>
          <Input aria-label="Source system" placeholder="System" value={source.source_system}
            onChange={event => setSource({ ...source, source_system: event.target.value })} />
          <Input aria-label="Source account" placeholder="Account" value={source.source_account}
            onChange={event => setSource({ ...source, source_account: event.target.value })} />
          <Input aria-label="Source reference" placeholder="Reference" value={source.source_reference}
            onChange={event => setSource({ ...source, source_reference: event.target.value })} />
          <Input aria-label="Source link reason" placeholder="Reason" value={actionReasons.source ?? ''}
            onChange={event => setActionReasons({ ...actionReasons, source: event.target.value })} />
          <Button disabled={!Object.values(source).every(Boolean) || !actionReasons.source?.trim()}
            onClick={() => runAction('source', key => addSaleSource(saleId, sale.version, source, actionReasons.source, key))}>
            Link source
          </Button>
        </Space>}
      </Card>
      <Card title="Actual tenders" style={{ marginTop: 16 }}>
        {(sale.payments?.length ?? 0) === 0 ? (
          <Alert type="info" message="No tender amounts recorded" />
        ) : (
          <List dataSource={sale.payments} renderItem={payment => (
            <List.Item><Space direction="vertical" style={{ width: '100%' }}>
              <Space wrap><span>{payment.tender.replace('_', ' ')} — {payment.effective_amount_cents == null
                ? 'Unknown amount' : `$${(payment.effective_amount_cents / 100).toFixed(2)}`}</span>
                <Tag>{payment.state}</Tag><Button size="small"
                  onClick={() => navigate(`/sales/payments/${payment.id}/evidence`)}>Add evidence</Button></Space>
              {payment.evidence?.map(evidence => <Space key={evidence.id} wrap>
                <span>Evidence #{evidence.id}</span><Tag>{evidence.status}</Tag>
                {isManager && evidence.status === 'pending' && <>
                  <Input aria-label={`Evidence ${evidence.id} reason`} placeholder="Review reason"
                    value={actionReasons[`e${evidence.id}`] ?? ''}
                    onChange={event => setActionReasons({ ...actionReasons, [`e${evidence.id}`]: event.target.value })} />
                  <Button disabled={!actionReasons[`e${evidence.id}`]?.trim()}
                    onClick={() => runAction(`evidence-accept-${evidence.id}`, key => reviewEvidence(evidence.id, 'accepted', actionReasons[`e${evidence.id}`], key))}>Accept</Button>
                  <Button danger disabled={!actionReasons[`e${evidence.id}`]?.trim()}
                    onClick={() => runAction(`evidence-reject-${evidence.id}`, key => reviewEvidence(evidence.id, 'rejected', actionReasons[`e${evidence.id}`], key))}>Reject</Button>
                </>}
              </Space>)}
              {isManager && <Space wrap>
                <Input aria-label={`Payment ${payment.id} reason`} placeholder="Payment review or refund reason"
                  value={actionReasons[`p${payment.id}`] ?? ''}
                  onChange={event => setActionReasons({ ...actionReasons, [`p${payment.id}`]: event.target.value })} />
                <Button disabled={!actionReasons[`p${payment.id}`]?.trim()}
                  onClick={() => runAction(`verify-${payment.id}`, key => reviewPayment(payment.id, sale.version, 'verify', actionReasons[`p${payment.id}`], key))}>Verify</Button>
                <Button danger disabled={!actionReasons[`p${payment.id}`]?.trim()}
                  onClick={() => runAction(`reject-${payment.id}`, key => reviewPayment(payment.id, sale.version, 'reject', actionReasons[`p${payment.id}`], key))}>Reject</Button>
                <InputNumber aria-label={`Payment ${payment.id} refund cents`} min={1} precision={0}
                  placeholder="Refund cents" value={refundAmounts[payment.id]}
                  onChange={value => setRefundAmounts({ ...refundAmounts, [payment.id]: value ?? 0 })} />
                <Button disabled={!refundAmounts[payment.id] || !actionReasons[`p${payment.id}`]?.trim()}
                  onClick={() => runAction(`refund-${payment.id}`, key => recordRefund(payment.id, sale.version, refundAmounts[payment.id], actionReasons[`p${payment.id}`], key))}>Record refund</Button>
              </Space>}
            </Space></List.Item>
          )} />
        )}
        {sale.payment_difference_cents != null && sale.payment_difference_cents !== 0 && (
          <Alert type="warning" showIcon message="Tender total does not match collected total"
            description={`${sale.payment_difference_cents} cents difference`} />
        )}
      </Card>
      {sale.status === 'draft' && (
        <Button type="primary" loading={working} onClick={postDraft} style={{ marginTop: 16 }}>
          Record sale
        </Button>
      )}
      {isManager && sale.allocation_status === 'pending' && (
        <Card title="Manager stock allocation" style={{ marginTop: 16 }}>
          <Space wrap style={{ marginBottom: 12 }}>{(sale.lines ?? []).map(line => (
            <InputNumber key={line.line_no} aria-label={`Line ${line.line_no} verified product ID`}
              placeholder={`Line ${line.line_no} product ID`} min={1} precision={0}
              value={productMappings[line.line_no]}
              onChange={value => setProductMappings({ ...productMappings, [line.line_no]: value ?? 0 })} />
          ))}</Space>
          <Input value={reason} onChange={event => setReason(event.target.value)}
            placeholder="Allocation reason" />
          <Button type="primary" loading={working} disabled={!reason.trim()}
            onClick={retryAllocation} style={{ marginTop: 12 }}>
            Allocate stock
          </Button>
        </Card>
      )}
      {isManager && sale.allocation_status === 'allocated' && (
        <Card title="Physical return to inventory" style={{ marginTop: 16 }}>
          <Space wrap>
            <Select aria-label="Return line" value={returnLine}
              options={(sale.lines ?? []).map(line => ({ value: line.line_no, label: `Line ${line.line_no}` }))}
              onChange={setReturnLine} />
            <InputNumber aria-label="Return quantity" min={1} precision={0} value={returnQuantity}
              onChange={value => setReturnQuantity(value ?? 1)} />
            <Select aria-label="Return disposition" value={returnDisposition}
              options={['saleable', 'damaged', 'hold'].map(value => ({ value, label: value }))}
              onChange={setReturnDisposition} />
            <Input aria-label="Physical return reason" placeholder="Return reason"
              value={actionReasons.return ?? ''}
              onChange={event => setActionReasons({ ...actionReasons, return: event.target.value })} />
            <Button disabled={!actionReasons.return?.trim()}
              onClick={() => runAction('physical-return', key => recordPhysicalReturn(saleId, sale.version, returnLine, returnQuantity, returnDisposition, actionReasons.return, key))}>
              Record physical return
            </Button>
          </Space>
          <Typography.Text type="secondary">This changes inventory only. Record a refund separately under the matching tender.</Typography.Text>
        </Card>
      )}
      {(sale.returns?.length ?? 0) > 0 && <Card title="Physical return history" style={{ marginTop: 16 }}>
        <List dataSource={sale.returns} renderItem={item => <List.Item>
          Line {item.sale_line_no}: {item.quantity} to {item.disposition} — {item.reason}
        </List.Item>} />
      </Card>}
    </div>
  )
}
