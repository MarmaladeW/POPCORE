import { Alert, Button, Card, Form, Input, InputNumber, Select, Space, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import OperationScanInput from '../../components/OperationScanInput'
import {
  createReceipt, postReceipt, requestKey, resolveGoodsBarcode,
  type GoodsProduct, type InventoryLocation,
} from '../../api/goods'

const { Text } = Typography

export default function Receiving({ products, locations, storeId }: {
  products: GoodsProduct[]
  locations: InventoryLocation[]
  storeId: number
}) {
  const [productId, setProductId] = useState<number>()
  const [locationId, setLocationId] = useState<number>()
  const [quantity, setQuantity] = useState(0)
  const [expected, setExpected] = useState<number | null>(null)
  const [reference, setReference] = useState('')
  const [disposition, setDisposition] = useState<'saleable' | 'damaged' | 'hold'>('saleable')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const keys = useRef({ create: requestKey(), post: requestKey() })
  const product = products.find(item => item.id === productId)
  const dirty = Boolean(productId || locationId || quantity || expected !== null || reference)

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  async function scan(code: string) {
    setError('')
    try {
      const result = await resolveGoodsBarcode(code)
      if (result.status !== 'exact') {
        setError(result.status === 'unknown' ? 'Unknown barcode. Select a product.' : 'Barcode is ambiguous. Select the exact product.')
        return
      }
      const candidate = result.candidates[0]
      setProductId(candidate.product_id)
      setQuantity(current => (
        productId === candidate.product_id
          ? current + candidate.quantity_per_scan
          : candidate.quantity_per_scan
      ))
    } catch {
      setError('Unable to resolve the scan. The draft is unchanged.')
    }
  }

  async function submit() {
    if (!product || !product.stock_unit || !locationId) return
    setSaving(true)
    setError('')
    try {
      const field = `${disposition}_quantity`
      const draft = await createReceipt({
        store_id: storeId,
        destination_location_id: locationId,
        business_date: new Date().toLocaleDateString('en-CA'),
        shipment_reference: reference || null,
        lines: [{
          product_id: product.id, unit: product.stock_unit,
          expected_quantity: expected,
          saleable_quantity: 0, damaged_quantity: 0, hold_quantity: 0,
          [field]: quantity,
          discrepancy_note: expected !== null && expected !== quantity ? 'Shipment quantity differs' : null,
        }],
      }, keys.current.create)
      await postReceipt(draft.id, draft.version, keys.current.post)
      keys.current = { create: requestKey(), post: requestKey() }
      setQuantity(0)
      setExpected(null)
      setReference('')
    } catch (reason) {
      setError((reason as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Receipt failed. Your draft is still here.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card title="Receive shipment">
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {error && <Alert type="error" showIcon message={error} />}
        <OperationScanInput onScan={scan} disabled={saving} />
        <Form layout="vertical">
          <Form.Item label="Destination" required>
            <Select value={locationId} onChange={setLocationId} options={locations.map(item => ({ value: item.id, label: item.name }))} />
          </Form.Item>
          <Form.Item label="Product" required>
            <Select showSearch optionFilterProp="label" value={productId} onChange={setProductId} options={products.map(item => ({ value: item.id, label: `${item.sku || ''} ${item.jizhanming || ''}` }))} />
          </Form.Item>
          <Space wrap>
            <Form.Item label="Actual quantity"><InputNumber min={0} precision={0} value={quantity} onChange={value => setQuantity(value || 0)} /></Form.Item>
            <Form.Item label="Expected (optional)"><InputNumber min={0} precision={0} value={expected} onChange={value => setExpected(value)} /></Form.Item>
            <Form.Item label="Condition"><Select style={{ width: 130 }} value={disposition} onChange={setDisposition} options={['saleable', 'damaged', 'hold'].map(value => ({ value, label: value }))} /></Form.Item>
          </Space>
          <Form.Item label="Shipment reference"><Input value={reference} onChange={event => setReference(event.target.value)} /></Form.Item>
        </Form>
        <Text>Effect: +{quantity} {product?.stock_unit || 'units'} at {locations.find(item => item.id === locationId)?.name || 'selected destination'} ({disposition}).</Text>
        <Button type="primary" loading={saving} disabled={!product?.stock_unit || !locationId || quantity < 1} onClick={submit}>Review and post receipt</Button>
      </Space>
    </Card>
  )
}
