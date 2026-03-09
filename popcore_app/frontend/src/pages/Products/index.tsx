import { useState, useEffect, useCallback } from 'react'
import {
  Input, Select, Button, Space, Tag, Popconfirm,
  message, Typography, Table, Badge,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  PlusOutlined, ExportOutlined, DeleteOutlined,
  SearchOutlined, EditOutlined, PictureOutlined,
} from '@ant-design/icons'
import client from '../../api/client'
import { useAppStore } from '../../store'
import RoleGuard from '../../components/RoleGuard'
import ProductModal from './ProductModal'
import HiddenImagesModal from './HiddenImagesModal'
import PasteImportModal from './PasteImportModal'

const { Title, Text } = Typography

interface Product {
  id: number
  sku: string
  jizhanming: string
  name_cn_en: string
  price: number | null
  ip_series: string
  product_type: string
  brand: string
  release_date: string
  hidden_count: string
  hidden_has_small: number
  hidden_has_large: number
}

interface StockRow {
  product_id:   number
  upstairs_dan: number
  instore_dan:  number
}

function useDebounce<T>(value: T, delay: number): T {
  const [dv, setDv] = useState<T>(value)
  useEffect(() => {
    const h = setTimeout(() => setDv(value), delay)
    return () => clearTimeout(h)
  }, [value, delay])
  return dv
}

const TYPE_COLORS: Record<string, string> = {
  'Blind Box': 'purple',
  'MEGA':      'orange',
  'Figure':    'blue',
}

function stockBadge(total: number) {
  if (total === 0)  return <Badge count={total} showZero style={{ backgroundColor: '#ef4444' }} />
  if (total <= 3)   return <Badge count={total} showZero style={{ backgroundColor: '#F59E0B' }} />
  return <Badge count={total} showZero style={{ backgroundColor: '#10B981' }} />
}

export default function ProductsPage() {
  const { series, productTypes } = useAppStore()
  const [products,  setProducts]  = useState<Product[]>([])
  const [stockMap,  setStockMap]  = useState<Map<number, number>>(new Map())
  const [loading,   setLoading]   = useState(false)
  const [inputQ,    setInputQ]    = useState('')
  const debouncedQ                = useDebounce(inputQ, 300)
  const [filterSeries, setFilterSeries] = useState<string>('')
  const [filterType,   setFilterType]   = useState<string>('')
  const [selected,  setSelected]  = useState<number[]>([])

  const [editProduct,    setEditProduct]    = useState<Product | null>(null)
  const [modalOpen,      setModalOpen]      = useState(false)
  const [imagesProduct,  setImagesProduct]  = useState<Product | null>(null)
  const [pasteOpen,      setPasteOpen]      = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    const params: Record<string, string> = { limit: '200' }
    if (debouncedQ)   params.q = debouncedQ
    if (filterSeries) params.series = filterSeries
    if (filterType)   params.product_type = filterType
    Promise.all([
      client.get('/products/search', { params }),
      client.get('/stock'),
    ]).then(([prodR, stockR]) => {
      setProducts(prodR.data)
      const m = new Map<number, number>()
      ;(stockR.data as StockRow[]).forEach(r => {
        m.set(r.product_id, (r.upstairs_dan ?? 0) + (r.instore_dan ?? 0))
      })
      setStockMap(m)
    }).finally(() => setLoading(false))
  }, [debouncedQ, filterSeries, filterType])

  useEffect(() => { load() }, [load])

  function openNew()         { setEditProduct(null); setModalOpen(true) }
  function openEdit(p: Product) { setEditProduct(p); setModalOpen(true) }

  async function handleBulkDelete() {
    if (!selected.length) return
    try {
      await client.post('/products/bulk_delete', selected)
      message.success(`Deleted ${selected.length} products`)
      setSelected([])
      load()
    } catch {
      message.error('Delete failed')
    }
  }

  function handleExport() {
    const params = new URLSearchParams()
    if (filterSeries) params.set('series', filterSeries)
    if (debouncedQ)   params.set('q', debouncedQ)
    window.location.href = `/api/products/export?${params}`
  }

  const columns: ColumnsType<Product> = [
    {
      title: 'SKU',
      dataIndex: 'sku',
      width: 120,
      sorter: (a, b) => a.sku.localeCompare(b.sku),
      render: v => <Text style={{ fontFamily: 'monospace', fontSize: 12, color: '#6b7280' }}>{v}</Text>,
    },
    {
      title: '记账名 (Jizhanming)',
      dataIndex: 'jizhanming',
      sorter: (a, b) => (a.jizhanming ?? '').localeCompare(b.jizhanming ?? ''),
      render: (v, r) => (
        <div>
          <div style={{ fontWeight: 500, color: '#111827' }}>{v || '—'}</div>
          {r.hidden_count && r.hidden_count !== '0' && (
            <div style={{ marginTop: 2 }}>
              {r.hidden_has_small ? <Tag color="gold" style={{ fontSize: 10, margin: '0 2px 0 0' }}>Small Secret</Tag> : null}
              {r.hidden_has_large ? <Tag color="orange" style={{ fontSize: 10, margin: 0 }}>Large Secret</Tag> : null}
            </div>
          )}
        </div>
      ),
    },
    {
      title: 'Product Name',
      dataIndex: 'name_cn_en',
      ellipsis: true,
      sorter: (a, b) => (a.name_cn_en ?? '').localeCompare(b.name_cn_en ?? ''),
      render: v => <Text style={{ fontSize: 13 }}>{v || '—'}</Text>,
    },
    {
      title: 'Series',
      dataIndex: 'ip_series',
      width: 120,
      sorter: (a, b) => (a.ip_series ?? '').localeCompare(b.ip_series ?? ''),
      render: v => v ? <Tag color="blue" style={{ fontSize: 11 }}>{v}</Tag> : '—',
    },
    {
      title: 'Type',
      dataIndex: 'product_type',
      width: 100,
      render: v => v
        ? <Tag color={TYPE_COLORS[v] ?? 'default'} style={{ fontSize: 11 }}>{v}</Tag>
        : '—',
    },
    {
      title: 'Price (CA$)',
      dataIndex: 'price',
      width: 110,
      align: 'right',
      sorter: (a, b) => (a.price ?? 0) - (b.price ?? 0),
      render: v => v != null
        ? <Text style={{ color: '#6366F1', fontWeight: 600 }}>${v.toFixed(2)}</Text>
        : <Text type="secondary">—</Text>,
    },
    {
      title: 'Stock',
      width: 80,
      align: 'center',
      render: (_, r) => stockBadge(stockMap.get(r.id) ?? 0),
    },
    {
      title: 'Actions',
      width: 100,
      align: 'center',
      render: (_, r) => (
        <Space size={4}>
          <Button
            type="text"
            size="small"
            icon={<PictureOutlined />}
            onClick={() => setImagesProduct(r)}
            style={{ color: '#6b7280' }}
          />
          <Button
            type="text"
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(r)}
            style={{ color: '#6366F1' }}
          />
          <RoleGuard minRole="manager">
            <Popconfirm
              title="Delete this product?"
              onConfirm={async () => {
                try {
                  await client.post('/products/bulk_delete', [r.id])
                  message.success('Deleted')
                  load()
                } catch {
                  message.error('Failed')
                }
              }}
            >
              <Button type="text" size="small" icon={<DeleteOutlined />} danger />
            </Popconfirm>
          </RoleGuard>
        </Space>
      ),
    },
  ]

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>Products</Title>
          <Text style={{ color: '#6b7280' }}>{products.length} of {products.length} products</Text>
        </div>
        <RoleGuard minRole="manager">
          <Button type="primary" icon={<PlusOutlined />} onClick={openNew}>
            Add Product
          </Button>
        </RoleGuard>
      </div>

      {/* Filters */}
      <div style={{
        background: '#fff',
        borderRadius: 10,
        padding: '16px 20px',
        marginBottom: 16,
        boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
      }}>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
          <Input
            prefix={<SearchOutlined style={{ color: '#9ca3af' }} />}
            placeholder="Search name, SKU, 记账名..."
            allowClear
            value={inputQ}
            onChange={e => setInputQ(e.target.value)}
            style={{ width: 260 }}
          />
          <Select
            placeholder="All Series"
            allowClear
            value={filterSeries || undefined}
            style={{ width: 140 }}
            options={series.map(s => ({ value: s, label: s }))}
            onChange={v => setFilterSeries(v ?? '')}
          />
          <Select
            placeholder="All Types"
            allowClear
            value={filterType || undefined}
            style={{ width: 120 }}
            options={productTypes.map(t => ({ value: t, label: t }))}
            onChange={v => setFilterType(v ?? '')}
          />
          <RoleGuard minRole="manager">
            <Space size={6} style={{ marginLeft: 'auto' }}>
              <Button onClick={() => setPasteOpen(true)}>Import</Button>
              <Button icon={<ExportOutlined />} onClick={handleExport}>Export</Button>
              {selected.length > 0 && (
                <Popconfirm
                  title={`Delete ${selected.length} products? This cannot be undone.`}
                  onConfirm={handleBulkDelete}
                  okButtonProps={{ danger: true }}
                >
                  <Button danger icon={<DeleteOutlined />}>
                    Delete ({selected.length})
                  </Button>
                </Popconfirm>
              )}
            </Space>
          </RoleGuard>
        </div>
      </div>

      {/* Table */}
      <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)', overflow: 'hidden' }}>
        <Table
          rowKey="id"
          loading={loading}
          dataSource={products}
          columns={columns}
          size="middle"
          rowSelection={{
            selectedRowKeys: selected,
            onChange: keys => setSelected(keys as number[]),
          }}
          pagination={{ pageSize: 50, showTotal: t => `${t} products`, showSizeChanger: false }}
          scroll={{ x: 900 }}
        />
      </div>

      <ProductModal
        open={modalOpen}
        product={editProduct}
        onClose={() => setModalOpen(false)}
        onSaved={() => { setModalOpen(false); load() }}
      />
      <HiddenImagesModal
        open={!!imagesProduct}
        product={imagesProduct}
        onClose={() => setImagesProduct(null)}
      />
      <PasteImportModal
        open={pasteOpen}
        onClose={() => setPasteOpen(false)}
        onDone={() => { setPasteOpen(false); load() }}
      />
    </div>
  )
}
