import { Alert, Spin, Tabs } from 'antd'
import PageHeader from '../../components/pc/PageHeader'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import client from '../../api/client'
import { useAppStore } from '../../store'
import type { GoodsProduct, InventoryLocation } from '../../api/goods'
import Receiving from './Receiving'
import Transfers from './Transfers'
import Counts from './Counts'
import './goods.css'

export default function GoodsPage({ initialTab }: {
  initialTab: 'receiving' | 'transfers' | 'counts'
}) {
  const navigate = useNavigate()
  const phone = useIsPhone()
  const [params] = useSearchParams()
  const selectedStore = useAppStore(state => state.selectedStore)
  const [products, setProducts] = useState<GoodsProduct[]>([])
  const [locations, setLocations] = useState<InventoryLocation[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const resumed = params.has('receipt_id') || params.has('transfer_id') || params.has('count_id')

  useEffect(() => {
    if (!selectedStore || (selectedStore.code === 'ALL' && !resumed)) {
      setLoading(false)
      return
    }
    let current = true
    setProducts([])
    setLocations([])
    setLoading(true)
    setError('')
    Promise.all([
      selectedStore.code === 'ALL' ? Promise.resolve({ data: { items: [] } }) : client.get('/stock', { params: {
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
  }, [resumed, selectedStore])

  if (!selectedStore || (selectedStore.code === 'ALL' && !resumed)) {
    return <Alert type="info" showIcon message="Select one store before handling goods." />
  }
  if (loading) return <Spin />
  if (error) return <Alert type="error" showIcon message={error} />

  const localLocations = locations.filter(item => item.store_id === selectedStore.id)
  const counts = initialTab === 'counts'
  return (
    <div className="pc-goods">
      <PageHeader
        title={phone ? { receiving: '收货', transfers: '调入', counts: '盘点' }[initialTab] : counts ? '盘点' : '入店'}
        sub={phone ? undefined : `${counts ? 'Counts' : 'Receive goods'} · ${selectedStore.name}`}
        back={phone ? (counts ? { label: '库存', to: '/stock' } : { label: '入店', to: '/incoming' }) : undefined} />
      <Tabs
        className="pc-goods-tabs"
        activeKey={initialTab}
        onChange={key => navigate(`/goods/${key}`)}
        items={[
          { key: 'receiving', label: <>收货 <span>Receiving</span></>, children: (
            <Receiving products={products} locations={resumed ? locations : localLocations} storeId={selectedStore.id} />
          ) },
          { key: 'transfers', label: <>调拨 <span>Transfers</span></>, children: (
            <Transfers products={products} locations={locations} />
          ) },
          { key: 'counts', label: <>盘点 <span>Counts</span></>, children: (
            <Counts products={products} locations={resumed ? locations : localLocations} />
          ) },
        ]}
      />
    </div>
  )
}
