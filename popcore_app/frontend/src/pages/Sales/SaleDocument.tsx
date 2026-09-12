import { Alert, Button, Card, Descriptions, Image, Input, InputNumber, List, Select, Space, Spin, Tag, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { useBeforeUnload, useNavigate, useParams } from 'react-router-dom'

import { searchGoodsProducts, type GoodsProduct } from '../../api/goods'
import { addSaleSource, allocateSale, fetchPaymentEvidence, fetchSale, newRequestKey, postSale, recordPhysicalReturn, recordRefund, reviewEvidence, reviewPayment, type SaleResult } from '../../api/salesDocuments'
import { useHasRole } from '../../auth/useRole'
import { formatCents, parseMoneyToCents } from '../../lib/money'
import { useHistoryReconciliationGuard } from '../../lib/reconciliationNavigation'
import { useAppStore } from '../../store'

const { Title, Text } = Typography
const responseStatus = (cause: unknown) => (cause as { response?: { status?: number } })?.response?.status
type PendingAction = { name: string; key: string; run: (key: string) => Promise<unknown> }

function EvidencePreview({ id, status }: { id: number; status: string }) {
  const [url, setUrl] = useState(''), [error, setError] = useState(''), [loading, setLoading] = useState(true)
  useEffect(() => {
    const controller = new AbortController(); let objectUrl = ''
    setLoading(true); setError(''); setUrl('')
    fetchPaymentEvidence(id, controller.signal).then(blob => {
      objectUrl = URL.createObjectURL(blob); setUrl(objectUrl)
    }).catch(cause => { if ((cause as { code?: string })?.code !== 'ERR_CANCELED') setError('Private evidence preview is unavailable or access was denied.') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [id])
  return <Space direction="vertical"><Space><Text>Evidence #{id}</Text><Tag>{status}</Tag></Space>{loading ? <Spin size="small" /> : error ? <Alert type="warning" showIcon message={error} /> : <Image width={180} src={url} alt={`Private payment evidence ${id}`} />}</Space>
}

export default function SaleDocumentPage() {
  const { id } = useParams(), saleId = Number(id), navigate = useNavigate(), isManager = useHasRole('manager')
  const selectedStore = useAppStore(state => state.selectedStore)
  const setSelectedStore = useAppStore(state => state.setSelectedStore)
  const [sale, setSale] = useState<SaleResult | null>(null), [error, setError] = useState('')
  const [productMappings, setProductMappings] = useState<Record<number, number>>({}), [products, setProducts] = useState<GoodsProduct[]>([])
  const [actionReasons, setActionReasons] = useState<Record<string, string>>({}), [refundAmounts, setRefundAmounts] = useState<Record<number, string>>({})
  const [source, setSource] = useState({ source_system: '', source_account: '', source_reference: '' })
  const [returnLine, setReturnLine] = useState(1), [returnQuantity, setReturnQuantity] = useState(1)
  const [returnDisposition, setReturnDisposition] = useState<'saleable'|'damaged'|'hold'>('saleable')
  const [working, setWorking] = useState(false), [pendingName, setPendingName] = useState(''), [refreshNeeded, setRefreshNeeded] = useState(false)
  const pendingAction = useRef<PendingAction | null>(null), scope = useRef(selectedStore), restoringStore = useRef(false)

  useHistoryReconciliationGuard(Boolean(pendingName), 'This request is not confirmed. Leave it reconciliation-pending and exit this page?')
  useBeforeUnload(event => { if (pendingName) event.preventDefault() })
  useEffect(() => {
    if (!pendingName) return
    const confirmLink = (event: MouseEvent) => {
      const link = (event.target as HTMLElement).closest('a')
      if (link && link.target !== '_blank' && !window.confirm('This request is not confirmed. Leave it reconciliation-pending and exit this page?')) event.preventDefault()
    }
    document.addEventListener('click', confirmLink, true)
    return () => document.removeEventListener('click', confirmLink, true)
  }, [pendingName])

  async function load(signal?: AbortSignal) {
    const detail = await fetchSale(saleId, signal); setSale(detail); setRefreshNeeded(false); return detail
  }
  useEffect(() => {
    if (restoringStore.current && selectedStore?.code === scope.current?.code) { restoringStore.current = false; return }
    if (pendingAction.current && selectedStore?.code !== scope.current?.code && scope.current) {
      restoringStore.current = true; setError('Resolve or acknowledge the unconfirmed sale action before changing stores.'); setSelectedStore(scope.current); return
    }
    scope.current = selectedStore
    setSale(null); setError(''); setRefreshNeeded(false); pendingAction.current = null; setPendingName('')
    if (!Number.isInteger(saleId) || saleId < 1) { setError('Sale reference is invalid.'); return }
    const controller = new AbortController()
    load(controller.signal).catch(cause => { if ((cause as { code?: string })?.code !== 'ERR_CANCELED') setError('Unable to load this sale.') })
    return () => controller.abort()
  }, [saleId, selectedStore?.code]) // eslint-disable-line react-hooks/exhaustive-deps

  async function refresh() {
    try { await load(); setError(''); return true } catch { setRefreshNeeded(true); setError('Saved; refresh needed before another action.'); return false }
  }
  async function runAction(name: string, run: (key: string) => Promise<unknown>) {
    if (working || refreshNeeded || (pendingAction.current && pendingAction.current.name !== name)) return
    const intent = pendingAction.current ?? { name, key: newRequestKey(), run }; pendingAction.current = intent
    setPendingName(name); setWorking(true); setError('')
    try {
      await intent.run(intent.key); pendingAction.current = null; setPendingName(''); await refresh()
    } catch (cause) {
      const status = responseStatus(cause)
      if (status === 409) {
        pendingAction.current = null; setPendingName(''); setActionReasons({})
        if (await refresh()) setError('Saved facts changed. Review the refreshed sale before creating a new request.')
      } else if (status && status < 500) {
        pendingAction.current = null; setPendingName(''); setError((cause as { _serverMessage?: string })?._serverMessage || 'The request was rejected. Review the saved facts.')
      } else setError('The action was not confirmed. Retry sends the identical request and payload.')
    } finally { setWorking(false) }
  }
  async function searchProducts(value: string) { if (value.trim()) setProducts((await searchGoodsProducts(value)).filter(product => product.identity_status === 'verified')) }
  const busy = working || refreshNeeded || !!pendingName
  const refund = (id: number) => { try { return parseMoneyToCents(refundAmounts[id] ?? '') } catch { return null } }

  if (!sale && !error) return <Spin />
  if (!sale) return <Alert type="error" showIcon message={error} />
  const allocationColor = sale.allocation_status === 'allocated' ? 'green' : sale.allocation_status === 'pending' ? 'orange' : 'default'
  const provenanceBlocked = sale.unresolved_reasons?.includes('fresh_set_selection_required')
  const selectedReturnLine = sale.lines?.find(line => line.line_no === returnLine)
  return <div className="pc-page" style={{ maxWidth: 840, margin: '0 auto' }}>
    <div className="pc-page-heading"><Title level={3}>Sale #{sale.sale_id}</Title><Text type="secondary">{sale.business_date || 'Saved sale document'}</Text></div>
    {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} action={pendingAction.current ? <Button onClick={() => runAction(pendingAction.current!.name, pendingAction.current!.run)}>Retry identical request</Button> : refreshNeeded ? <Button onClick={refresh}>Retry refresh</Button> : undefined} />}
    <Card><Descriptions column={1} size="small">
      <Descriptions.Item label="Document"><Tag>{sale.status}</Tag></Descriptions.Item>
      <Descriptions.Item label="Payment facts"><Tag>{sale.financial_status ?? 'draft'}</Tag></Descriptions.Item>
      <Descriptions.Item label="Stock"><Tag color={allocationColor}>{sale.allocation_status ?? 'draft'}</Tag></Descriptions.Item>
      <Descriptions.Item label="Collected">{formatCents(sale.collected_cents)}</Descriptions.Item>
    </Descriptions>{sale.unresolved_reasons?.length ? <Alert type="warning" showIcon message="Stock allocation needs review" description={sale.unresolved_reasons.join(', ')} style={{ marginTop: 12 }} /> : null}</Card>
    <Card title="Items" style={{ marginTop: 16 }}><List dataSource={sale.lines ?? []} renderItem={line => <List.Item>{line.quantity} {line.unit ?? 'unmapped'} — {line.product_name_snapshot ?? line.raw_product_text}</List.Item>} /></Card>
    <Card title="Source history" style={{ marginTop: 16 }}>
      <List locale={{ emptyText: 'No source links' }} dataSource={sale.sources ?? []} renderItem={item => <List.Item>{item.source_system} / {item.source_account} / {item.source_reference}</List.Item>} />
      {isManager && <Space wrap><Input aria-label="Source system" placeholder="System" disabled={busy} value={source.source_system} onChange={event => setSource({ ...source, source_system: event.target.value })} /><Input aria-label="Source account" placeholder="Account" disabled={busy} value={source.source_account} onChange={event => setSource({ ...source, source_account: event.target.value })} /><Input aria-label="Source reference" placeholder="Reference" disabled={busy} value={source.source_reference} onChange={event => setSource({ ...source, source_reference: event.target.value })} /><Input aria-label="Source link reason" placeholder="Separate source-link reason" disabled={busy} value={actionReasons.source ?? ''} onChange={event => setActionReasons({ ...actionReasons, source: event.target.value })} /><Button disabled={busy || !Object.values(source).every(Boolean) || !actionReasons.source?.trim()} onClick={() => runAction('source', key => addSaleSource(saleId, sale.version, source, actionReasons.source, key))}>Link source</Button></Space>}
    </Card>
    <Card title="Payment and evidence review" style={{ marginTop: 16 }}>
      {(sale.payments?.length ?? 0) === 0 ? <Alert type="info" message="No tender amounts recorded" /> : <List dataSource={sale.payments} renderItem={payment => <List.Item><Space direction="vertical" style={{ width: '100%' }}>
        <Space wrap><Text strong>{payment.tender.replace('_', ' ')} · {formatCents(payment.effective_amount_cents)}</Text><Tag>{payment.state}</Tag><Button size="small" disabled={busy} onClick={() => navigate(`/sales/payments/${payment.id}/evidence?sale_id=${sale.sale_id}`)}>Add evidence</Button></Space>
        {payment.evidence?.map(evidence => <div key={evidence.id}><EvidencePreview id={evidence.id} status={evidence.status} />{isManager && evidence.status === 'pending' && <Space wrap>
          <Input aria-label={`Evidence ${evidence.id} acceptance reason`} disabled={busy} placeholder="Acceptance reason" value={actionReasons[`ea${evidence.id}`] ?? ''} onChange={event => setActionReasons({ ...actionReasons, [`ea${evidence.id}`]: event.target.value })} />
          <Button disabled={busy || !actionReasons[`ea${evidence.id}`]?.trim()} onClick={() => runAction(`evidence-accept-${evidence.id}`, key => reviewEvidence(evidence.id, 'accepted', actionReasons[`ea${evidence.id}`], key))}>Accept</Button>
          <Input aria-label={`Evidence ${evidence.id} rejection reason`} disabled={busy} placeholder="Rejection reason" value={actionReasons[`er${evidence.id}`] ?? ''} onChange={event => setActionReasons({ ...actionReasons, [`er${evidence.id}`]: event.target.value })} />
          <Button danger disabled={busy || !actionReasons[`er${evidence.id}`]?.trim()} onClick={() => runAction(`evidence-reject-${evidence.id}`, key => reviewEvidence(evidence.id, 'rejected', actionReasons[`er${evidence.id}`], key))}>Reject evidence</Button>
        </Space>}</div>)}
        {isManager && <><Space wrap><Input aria-label={`Payment ${payment.id} verification reason`} disabled={busy} placeholder="Verification reason" value={actionReasons[`pv${payment.id}`] ?? ''} onChange={event => setActionReasons({ ...actionReasons, [`pv${payment.id}`]: event.target.value })} /><Button disabled={busy || !actionReasons[`pv${payment.id}`]?.trim()} onClick={() => runAction(`verify-${payment.id}`, key => reviewPayment(payment.id, sale.version, 'verify', actionReasons[`pv${payment.id}`], key))}>Verify</Button></Space>
          <Space wrap><Input aria-label={`Payment ${payment.id} rejection reason`} disabled={busy} placeholder="Rejection reason" value={actionReasons[`pr${payment.id}`] ?? ''} onChange={event => setActionReasons({ ...actionReasons, [`pr${payment.id}`]: event.target.value })} /><Button danger disabled={busy || !actionReasons[`pr${payment.id}`]?.trim()} onClick={() => runAction(`reject-${payment.id}`, key => reviewPayment(payment.id, sale.version, 'reject', actionReasons[`pr${payment.id}`], key))}>Reject payment</Button></Space></>}
      </Space></List.Item>} />}
      {sale.payment_difference_cents != null && sale.payment_difference_cents !== 0 && <Alert type="warning" showIcon message="Tender total does not match collected total" description={`${formatCents(sale.payment_difference_cents)} difference`} />}
    </Card>
    {isManager && (sale.payments ?? []).map(payment => <Card key={payment.id} title={`${payment.tender.replace('_', ' ')} refund`} style={{ marginTop: 16 }}><Space wrap><Input aria-label={`Payment ${payment.id} refund amount ($)`} prefix="$" inputMode="decimal" disabled={busy} placeholder="0.00" value={refundAmounts[payment.id]} onChange={event => setRefundAmounts({ ...refundAmounts, [payment.id]: event.target.value })} /><Input aria-label={`Payment ${payment.id} refund reason`} disabled={busy} placeholder="Separate refund reason" value={actionReasons[`pf${payment.id}`] ?? ''} onChange={event => setActionReasons({ ...actionReasons, [`pf${payment.id}`]: event.target.value })} /><Button disabled={busy || !refund(payment.id) || !actionReasons[`pf${payment.id}`]?.trim()} onClick={() => runAction(`refund-${payment.id}`, key => recordRefund(payment.id, sale.version, refund(payment.id)!, actionReasons[`pf${payment.id}`], key))}>Record monetary refund</Button></Space><Text type="secondary">This records money only and does not return stock.</Text></Card>)}
    {sale.status === 'draft' && <Button type="primary" loading={working} disabled={busy} onClick={() => runAction('post', key => postSale(saleId, sale.version, key))} style={{ marginTop: 16 }}>Record sale</Button>}
    {isManager && sale.allocation_status === 'pending' && <Card title="Manager stock allocation" style={{ marginTop: 16 }}>
      {provenanceBlocked && <Alert type="warning" showIcon message="Opened-set provenance is required" description="No authorized opened-set list is available. Allocation stays blocked rather than guessing an ID or consuming loose stock." />}
      {!provenanceBlocked && <><Space wrap style={{ marginBottom: 12 }}>{(sale.lines ?? []).map(line => <Select key={line.line_no} aria-label={`Line ${line.line_no} verified product`} showSearch filterOption={false} onSearch={searchProducts} disabled={busy} placeholder="Search verified products" value={productMappings[line.line_no]} onChange={value => setProductMappings({ ...productMappings, [line.line_no]: value })} options={products.map(product => ({ value: product.id, label: `${product.sku || ''} ${product.jizhanming || ''}` }))} />)}</Space><Input aria-label="Allocation reason" disabled={busy} value={actionReasons.allocation ?? ''} onChange={event => setActionReasons({ ...actionReasons, allocation: event.target.value })} placeholder="Separate allocation reason" /><Button type="primary" loading={working} disabled={busy || !actionReasons.allocation?.trim() || (sale.lines ?? []).some(line => !productMappings[line.line_no])} onClick={() => runAction('allocation', key => allocateSale(saleId, sale.version, actionReasons.allocation, Object.entries(productMappings).map(([line, product]) => ({ line_no: Number(line), product_id: product })), key))} style={{ marginTop: 12 }}>Allocate stock</Button></>}
    </Card>}
    {isManager && sale.allocation_status === 'allocated' && <Card title="Physical return to inventory" style={{ marginTop: 16 }}><Space wrap><Select aria-label="Return line" disabled={busy} value={returnLine} options={(sale.lines ?? []).map(line => ({ value: line.line_no, label: `Line ${line.line_no}: ${line.product_name_snapshot || line.raw_product_text || 'product'} (${line.unit ?? 'unmapped unit'})` }))} onChange={setReturnLine} /><InputNumber aria-label={`Return quantity (${selectedReturnLine?.unit ?? 'unmapped unit'})`} disabled={busy} min={1} precision={0} value={returnQuantity} onChange={value => setReturnQuantity(value ?? 1)} /><Select aria-label="Return disposition" disabled={busy} value={returnDisposition} options={['saleable', 'damaged', 'hold'].map(value => ({ value, label: value }))} onChange={setReturnDisposition} /><Input aria-label="Physical return reason" disabled={busy} placeholder="Separate physical-return reason" value={actionReasons.return ?? ''} onChange={event => setActionReasons({ ...actionReasons, return: event.target.value })} /><Button disabled={busy || !actionReasons.return?.trim()} onClick={() => runAction('physical-return', key => recordPhysicalReturn(saleId, sale.version, returnLine, returnQuantity, returnDisposition, actionReasons.return, key))}>Record physical return</Button></Space><Text type="secondary">Quantity is in {selectedReturnLine?.unit ?? 'the saved native unit'}s. This returns stock to the chosen disposition. It does not refund money.</Text></Card>}
    {(sale.returns?.length ?? 0) > 0 && <Card title="Physical return history" style={{ marginTop: 16 }}><List dataSource={sale.returns} renderItem={item => <List.Item>Line {item.sale_line_no}: {item.quantity} to {item.disposition} — {item.reason}</List.Item>} /></Card>}
  </div>
}
