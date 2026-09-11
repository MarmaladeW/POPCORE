import { Alert, Button, Spin, Tabs, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import client from '../../api/client'
import { useAppStore } from '../../store'
import type { GoodsProduct, InventoryLocation } from '../../api/goods'
import Receiving from './Receiving'
import Transfers from './Transfers'
import Counts from './Counts'

const { Title, Text } = Typography

export default function GoodsPage({ initialTab }: {
  initialTab: 'receiving' | 'transfers' | 'counts'
}) {
  const navigate = useNavigate()
  const selectedStore = useAppStore(state => state.selectedStore)
  const [products, setProducts] = useState<GoodsProduct[]>([])
  const [locations, setLocations] = useState<InventoryLocation[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!selectedStore || selectedStore.code === 'ALL') {
      setLoading(false)
      return
    }
    let current = true
    setLoading(true)
    setError('')
    Promise.all([
      client.get('/stock', { params: {
        store_code: selectedStore.code, page: 1, page_size: 500,
      } }),
      client.get('/inventory/locations'),
    ]).then(([stockResponse, locationResponse]) => {
      if (!current) return
      setProducts(stockResponse.data.items)
      setLocations(locationResponse.data)
    }).catch(() => {
      if (current) setError('Unable to load goods setup. Check your connection and store access.')
    }).finally(() => {
      if (current) setLoading(false)
    })
    return () => { current = false }
  }, [selectedStore])

  if (!selectedStore || selectedStore.code === 'ALL') {
    return <Alert type="info" showIcon message="Select one store before handling goods." />
  }
  if (loading) return <Spin />
  if (error) return <Alert type="error" showIcon message={error} />

  const localLocations = locations.filter(item => item.store_id === selectedStore.id)
  return (
    <div>
      <Button onClick={() => navigate('/stock')} style={{ marginBottom: 12 }}>Back to stock</Button>
      <Title level={3} style={{ margin: 0 }}>Goods handling</Title>
      <Text type="secondary">{selectedStore.name}</Text>
      <Tabs
        activeKey={initialTab}
        onChange={key => navigate(`/goods/${key}`)}
        items={[
          { key: 'receiving', label: 'Receiving', children: (
            <Receiving products={products} locations={localLocations} storeId={selectedStore.id} />
          ) },
          { key: 'transfers', label: 'Transfers', children: (
            <Transfers products={products} locations={locations} />
          ) },
          { key: 'counts', label: 'Counts', children: (
            <Counts products={products} locations={localLocations} />
          ) },
        ]}
      />
    </div>
  )
}
