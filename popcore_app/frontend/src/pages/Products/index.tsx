import { useState, useEffect, useCallback } from 'react'
import {
  Button, Space, Tag, Popconfirm,
  message, Typography, Table, Badge, Spin,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  PlusOutlined, ExportOutlined, DeleteOutlined,
  EditOutlined, PictureOutlined, FilterOutlined,
} from '@ant-design/icons'
import client from '../../api/client'
import { useAppStore } from '../../store'
import RoleGuard from '../../components/RoleGuard'
import ProductModal from './ProductModal'
import HiddenImagesModal from './HiddenImagesModal'
import PasteImportModal from './PasteImportModal'
import ProductSearchBar from './ProductSearchBar'
import ProductDetailDrawer from './ProductDetailDrawer'
import { useIsMobile } from '../../hooks/useIsMobile'

const { Title, Text }  = Typography

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
  const isMobile = useIsMobile()
  const { series, productTypes } = useAppStore()
  const [products,  setProducts]  = useState<Product[]>([])
  const [stockMap,  setStockMap]  = useState<Map<number, number>>(new Map())
  const [loading,   setLoading]   = useState(false)
  const [searchQ,      setSearchQ]      = useState('')
  const [searchSeries, setSearchSeries] = useState('')
  const [searchType,   setSearchType]   = useState('')
  const [selected,  setSelected]  = useState<number[]>([])

  const [editProduct,    setEditProduct]    = useState<Product | null>(null)
  const [modalOpen,      setModalOpen]      = useState(false)
  const [imagesProduct,  setImagesProduct]  = useState<Product | null>(null)
  const [pasteOpen,      setPasteOpen]      = useState(false)
  const [detailId,       setDetailId]       = useState<number | null>(null)
  const [filtersVisible, setFiltersVisible] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    const params: Record<string, string> = {}
    if (searchQ)      params.q = searchQ
    if (searchSeries) params.series = searchSeries
    if (searchType)   params.product_type = searchType
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
    }).catch(() => {
      message.error('加载失败，请刷新页面')
    }).finally(() => setLoading(false))
  }, [searchQ, searchSeries, searchType])

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
    if (searchSeries) params.set('series', searchSeries)
    if (searchQ)      params.set('q', searchQ)
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
              {r.hidden_has_small ? <Tag color="gold" style={{ fontSize: 10, margin: '0 2px 0 0' }}>小隐藏</Tag> : null}
              {r.hidden_has_large ? <Tag color="orange" style={{ fontSize: 10, margin: 0 }}>大隐藏</Tag> : null}
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
          <RoleGuard minRole="manager">
            <Button
              type="text"
              size="small"
              icon={<EditOutlined />}
              onClick={() => openEdit(r)}
              style={{ color: '#6366F1' }}
            />
          </RoleGuard>
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
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <Title level={isMobile ? 4 : 3} style={{ margin: 0 }}>Products</Title>
          <Text style={{ color: '#6b7280', fontSize: 13 }}>{products.length} products</Text>
        </div>
        <Space size={8}>
          {/* Filter toggle on mobile */}
          {isMobile && (
            <Button
              icon={<FilterOutlined />}
              onClick={() => setFiltersVisible(v => !v)}
              type={filtersVisible ? 'primary' : 'default'}
              style={{ minWidth: 40 }}
            />
          )}
          <RoleGuard minRole="manager">
            <Button type="primary" icon={<PlusOutlined />} onClick={openNew}>
              {isMobile ? '' : 'Add Product'}
            </Button>
          </RoleGuard>
        </Space>
      </div>

      {/* Filters — always visible on desktop, toggle on mobile */}
      {(!isMobile || filtersVisible) && (
        <div style={{
          background: '#fff',
          borderRadius: 10,
          padding: isMobile ? '12px 16px' : '16px 20px',
          marginBottom: 16,
          boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
        }}>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', marginBottom: isMobile ? 0 : 12 }}>
            <ProductSearchBar
              series={series}
              productTypes={productTypes}
              onChange={(q, s, t) => { setSearchQ(q); setSearchSeries(s); setSearchType(t) }}
            />
            <RoleGuard minRole="manager">
              <Space size={6} style={{ marginLeft: isMobile ? 0 : 'auto', flexWrap: 'wrap' }}>
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
      )}

      {/* Table / Card list */}
      <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)', overflow: 'hidden' }}>
        {isMobile ? (
          <Spin spinning={loading}>
            {!loading && products.length === 0 && (
              <div style={{ textAlign: 'center', color: '#9ca3af', padding: '24px 16px', fontSize: 13 }}>No products found</div>
            )}
            {products.map(p => (
              <div key={p.id} style={{ padding: '12px 16px', borderBottom: '1px solid #f5f5f5', cursor: 'pointer' }} onClick={() => setDetailId(p.id)}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 500, fontSize: 13, color: '#111827' }}>{p.jizhanming || p.name_cn_en || '—'}</div>
                    {p.name_cn_en && p.jizhanming && (
                      <div style={{ fontSize: 11, color: '#6b7280', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name_cn_en}</div>
                    )}
                    <div style={{ fontSize: 11, color: '#9ca3af', fontFamily: 'monospace', marginTop: 1 }}>{p.sku}</div>
                  </div>
                  <div style={{ display: 'flex', gap: 4, flexShrink: 0, marginLeft: 8, alignItems: 'center' }} onClick={e => e.stopPropagation()}>
                    {stockBadge(stockMap.get(p.id) ?? 0)}
                    <Button type="text" size="small" icon={<PictureOutlined />} onClick={() => setImagesProduct(p)} style={{ color: '#6b7280' }} />
                    <RoleGuard minRole="manager"><Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(p)} style={{ color: '#6366F1' }} /></RoleGuard>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                  {p.ip_series && <Tag color="blue" style={{ fontSize: 10 }}>{p.ip_series}</Tag>}
                  {p.product_type && <Tag color={TYPE_COLORS[p.product_type] ?? 'default'} style={{ fontSize: 10 }}>{p.product_type}</Tag>}
                  {p.price != null && <Text style={{ fontSize: 12, color: '#6366F1', fontWeight: 600 }}>${p.price.toFixed(2)}</Text>}
                </div>
              </div>
            ))}
          </Spin>
        ) : (
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
            onRow={(r) => ({
              onClick: (e) => {
                const target = e.target as HTMLElement
                if (target.closest('button') || target.closest('.ant-checkbox')) return
                setDetailId(r.id)
              },
              style: { cursor: 'pointer' },
            })}
          />
        )}
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
      <ProductDetailDrawer
        productId={detailId}
        stockTotal={stockMap.get(detailId ?? 0) ?? 0}
        onClose={() => setDetailId(null)}
        onEdit={(p) => { setDetailId(null); openEdit(p as Product) }}
        onImages={(p) => { setDetailId(null); setImagesProduct(p as Product) }}
      />
    </div>
  )
}
