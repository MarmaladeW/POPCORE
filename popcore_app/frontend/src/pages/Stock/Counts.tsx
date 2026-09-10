import { Alert, Button, Card, Form, Input, InputNumber, Select, Space, Typography } from 'antd'
import { useRef, useState } from 'react'
import OperationScanInput from '../../components/OperationScanInput'
import { useHasRole } from '../../auth/useRole'
import {
  actOnCount, createCount, requestKey, resolveGoodsBarcode,
  type GoodsProduct, type InventoryLocation, type WorkflowResult,
} from '../../api/goods'

const { Text } = Typography

export default function Counts({ products, locations }: {
  products: GoodsProduct[]
  locations: InventoryLocation[]
}) {
  const isManager = useHasRole('manager')
  const [locationId, setLocationId] = useState<number>()
  const [productId, setProductId] = useState<number>()
  const [observed, setObserved] = useState(0)
  const [reason, setReason] = useState('')
  const [count, setCount] = useState<WorkflowResult>()
  const [error, setError] = useState('')
  const keys = useRef({ create: requestKey(), submit: requestKey(), approve: requestKey() })
  const product = products.find(item => item.id === productId)

  async function scan(code: string) {
    try {
      const result = await resolveGoodsBarcode(code, 'move')
      if (result.status !== 'exact') {
        setError('Scan needs an exact product selection. The count draft is unchanged.')
        return
      }
      const candidate = result.candidates[0]
      setProductId(candidate.product_id)
      setObserved(value => (
        productId === candidate.product_id ? value + candidate.quantity_per_scan
          : candidate.quantity_per_scan
      ))
    } catch {
      setError('Unable to resolve the scan. The count draft is unchanged.')
    }
  }

  async function start() {
    if (!locationId || !product?.stock_unit) return
    try {
      setError('')
      const result = await createCount({
        location_id: locationId, business_date: new Date().toLocaleDateString('en-CA'),
        lines: [{ product_id: product.id, unit: product.stock_unit, observed_quantity: observed }],
      }, keys.current.create)
      setCount(result)
    } catch (cause) {
      setError((cause as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Count draft failed.')
    }
  }

  async function act(action: 'submit' | 'approve') {
    if (!count) return
    try {
      setError('')
      const result = await actOnCount(count.id, action, {
        expected_version: count.version,
        ...(action === 'approve' ? { reason } : {}),
      }, keys.current[action])
      setCount(result)
      keys.current[action] = requestKey()
    } catch (cause) {
      setError((cause as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Count action failed. The observation is unchanged.')
    }
  }

  return (
    <Card title="Physical count">
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {error && <Alert type="error" showIcon message={error} />}
        <OperationScanInput onScan={scan} />
        <Form layout="vertical">
          <Form.Item label="Location" required><Select value={locationId} onChange={setLocationId} options={locations.map(item => ({ value: item.id, label: item.name }))} /></Form.Item>
          <Form.Item label="Product" required><Select showSearch optionFilterProp="label" value={productId} onChange={setProductId} options={products.map(item => ({ value: item.id, label: `${item.sku || ''} ${item.jizhanming || ''}` }))} /></Form.Item>
          <Form.Item label="Observed quantity"><InputNumber min={0} precision={0} value={observed} onChange={value => setObserved(value || 0)} /></Form.Item>
        </Form>
        {!count && <Button type="primary" disabled={!locationId || !product?.stock_unit} onClick={start}>Save observation</Button>}
        {count?.status === 'draft' && <Button type="primary" onClick={() => act('submit')}>Submit for review</Button>}
        {count?.status === 'submitted' && isManager && <Space direction="vertical" style={{ width: '100%' }}><Input value={reason} onChange={event => setReason(event.target.value)} placeholder="Review reason" /><Button type="primary" disabled={!reason.trim()} onClick={() => act('approve')}>Approve adjustment</Button></Space>}
        {count && <Text>Count #{count.id}: {count.status}, version {count.version}</Text>}
      </Space>
    </Card>
  )
}
