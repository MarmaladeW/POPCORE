import { Alert, Button, Card, Form, InputNumber, Select, Space, Typography } from 'antd'
import { useRef, useState } from 'react'
import {
  actOnTransfer, createTransfer, requestKey,
  type GoodsProduct, type InventoryLocation, type WorkflowResult,
} from '../../api/goods'

const { Text } = Typography

export default function Transfers({ products, locations }: {
  products: GoodsProduct[]
  locations: InventoryLocation[]
}) {
  const [source, setSource] = useState<number>()
  const [destination, setDestination] = useState<number>()
  const [productId, setProductId] = useState<number>()
  const [quantity, setQuantity] = useState(1)
  const [delivered, setDelivered] = useState(1)
  const [transfer, setTransfer] = useState<WorkflowResult>()
  const [error, setError] = useState('')
  const keys = useRef({ create: requestKey(), dispatch: requestKey(), receive: requestKey() })
  const product = products.find(item => item.id === productId)

  async function create() {
    if (!source || !destination || !product?.stock_unit) return
    setError('')
    try {
      const result = await createTransfer({
        source_location_id: source, destination_location_id: destination,
        business_date: new Date().toLocaleDateString('en-CA'),
        lines: [{ product_id: product.id, unit: product.stock_unit, requested_quantity: quantity }],
      }, keys.current.create)
      setTransfer(result)
    } catch (reason) {
      setError((reason as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Transfer draft failed.')
    }
  }

  async function act(action: 'dispatch' | 'receive') {
    if (!transfer) return
    setError('')
    try {
      const amount = action === 'dispatch' ? quantity : delivered
      const result = await actOnTransfer(transfer.id, action, {
        expected_version: transfer.version,
        lines: [{ line_no: 1, quantity: amount }],
      }, keys.current[action])
      setTransfer(result)
      keys.current[action] = requestKey()
    } catch (reason) {
      setError((reason as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Transfer action failed. The draft is unchanged.')
    }
  }

  return (
    <Card title="Transfer stock">
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {error && <Alert type="error" showIcon message={error} />}
        <Form layout="vertical">
          <Form.Item label="Source" required><Select value={source} onChange={setSource} options={locations.map(item => ({ value: item.id, label: `${item.store_code} · ${item.name}` }))} /></Form.Item>
          <Form.Item label="Destination" required><Select value={destination} onChange={setDestination} options={locations.map(item => ({ value: item.id, label: `${item.store_code} · ${item.name}` }))} /></Form.Item>
          <Form.Item label="Product" required><Select showSearch optionFilterProp="label" value={productId} onChange={setProductId} options={products.map(item => ({ value: item.id, label: `${item.sku || ''} ${item.jizhanming || ''}` }))} /></Form.Item>
          <Form.Item label="Requested quantity"><InputNumber min={1} precision={0} value={quantity} onChange={value => setQuantity(value || 1)} /></Form.Item>
        </Form>
        <Text>Effect on dispatch: -{quantity} {product?.stock_unit || 'units'} from source saleable, +{quantity} in this transfer.</Text>
        {!transfer && <Button type="primary" disabled={!source || !destination || !product?.stock_unit} onClick={create}>Create transfer</Button>}
        {transfer && transfer.version === 1 && <Button type="primary" onClick={() => act('dispatch')}>Dispatch {quantity}</Button>}
        {transfer && transfer.version > 1 && transfer.status !== 'completed' && <Space><InputNumber min={1} max={quantity} precision={0} value={delivered} onChange={value => setDelivered(value || 1)} /><Button type="primary" onClick={() => act('receive')}>Receive delivered quantity</Button></Space>}
        {transfer && <Text>Transfer #{transfer.id}: {transfer.status}, version {transfer.version}</Text>}
      </Space>
    </Card>
  )
}
