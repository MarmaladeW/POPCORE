import { useState, useEffect, useCallback } from 'react'
import {
  Button, Space, Tag, Popconfirm,
  message, Typography, Table, Badge, Spin, Modal, Tabs,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  PlusOutlined, ExportOutlined, DeleteOutlined,
  EditOutlined, PictureOutlined, FilterOutlined,
  SyncOutlined, ExclamationCircleOutlined,
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
  product_id:  number
  upstairs_qty: number
  instore_qty:  number
}

interface SyncChangedItem {
  sheet_jizhanming: string
  product_id:       number
  sku:              string
  old_jizhanming:   string
  new_jizhanming:   string
}

interface SyncReviewItem {
  sheet_jizhanming:   string
  product_id:         number
  sku:                string
  current_jizhanming: string
  score:              number
}

interface SyncDuplicatePair {
  product_a: { id: number; sku: string; jizhanming: string }
  product_b: { id: number; sku: string; jizhanming: string }
  score:     number
  severity:  'likely' | 'possible'
}

interface SyncResult {
  changed:    SyncChangedItem[]
  review:     SyncReviewItem[]
  unchanged:  number
  not_found:  string[]
  duplicates: SyncDuplicatePair[]
}

interface LastSync {
  last_sync_at:    string | null
  last_sync_count: string | null
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
  const { series, productTypes, selectedStore } = useAppStore()
  const [products,  setProducts]  = useState<Product[]>([])
  const [stockMap,  setStockMap]  = useState<Map<number, number>>(new Map())
  const [loading,   setLoading]   = useState(false)
  const [searchQ,      setSearchQ]      = useState('')
  const [searchSeries, setSearchSeries] = useState('')
  const [searchType,   setSearchType]   = useState('')
  const [selected,  setSelected]  = useState<number[]>([])

  const [exporting,      setExporting]      = useState(false)
  const [editProduct,    setEditProduct]    = useState<Product | null>(null)
  const [modalOpen,      setModalOpen]      = useState(false)
  const [imagesProduct,  setImagesProduct]  = useState<Product | null>(null)
  const [pasteOpen,      setPasteOpen]      = useState(false)
  const [detailId,       setDetailId]       = useState<number | null>(null)
  const [filtersVisible, setFiltersVisible] = useState(false)

  const [syncLoading,        setSyncLoading]        = useState(false)
  const [syncModalOpen,      setSyncModalOpen]      = useState(false)
  const [syncResult,         setSyncResult]         = useState<SyncResult | null>(null)
  const [checkedChangedKeys, setCheckedChangedKeys] = useState<string[]>([])
  const [checkedReviewKeys,  setCheckedReviewKeys]  = useState<string[]>([])
  const [syncActiveTab,      setSyncActiveTab]      = useState('changed')
  const [confirmLoading,     setConfirmLoading]     = useState(false)
  const [lastSync,           setLastSync]           = useState<LastSync>({ last_sync_at: null, last_sync_count: null })

  const load = useCallback(() => {
    const sc = selectedStore?.code
    setLoading(true)
    const params: Record<string, string> = {}
    if (searchQ)      params.q = searchQ
    if (searchSeries) params.series = searchSeries
    if (searchType)   params.product_type = searchType
    const stockParams = sc ? { store_code: sc } : {}
    Promise.all([
      client.get('/products/search', { params }),
      client.get('/stock', { params: stockParams }),
    ]).then(([prodR, stockR]) => {
      setProducts(prodR.data)
      const m = new Map<number, number>()
      ;(stockR.data as StockRow[]).forEach(r => {
        m.set(r.product_id, (r.upstairs_qty ?? 0) + (r.instore_qty ?? 0))
      })
      setStockMap(m)
    }).catch(() => {
      message.error('加载失败，请刷新页面')
    }).finally(() => setLoading(false))
  }, [searchQ, searchSeries, searchType, selectedStore?.code])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    client.get('/products/sync-sheet/last-sync')
      .then(r => setLastSync(r.data))
      .catch(() => {/* non-admin users get 403 — silently ignore */})
  }, [])

  async function handleSyncSheet() {
    setSyncLoading(true)
    try {
      const r = await client.post('/products/sync-sheet')
      const data = r.data as SyncResult
      setSyncResult(data)
      setCheckedChangedKeys(data.changed.map(c => c.sheet_jizhanming))
      setCheckedReviewKeys([])
      if (data.changed.length > 0)        setSyncActiveTab('changed')
      else if (data.review.length > 0)    setSyncActiveTab('review')
      else if (data.not_found.length > 0) setSyncActiveTab('not_found')
      else                                setSyncActiveTab('duplicates')
      setSyncModalOpen(true)
    } catch (err: any) {
      message.error(err._serverMessage || '同步失败，请重试 / Sync failed, please retry')
    } finally {
      setSyncLoading(false)
    }
  }

  async function handleConfirmSync() {
    if (!syncResult) return
    setConfirmLoading(true)
    try {
      const changes = syncResult.changed
        .filter(c => checkedChangedKeys.includes(c.sheet_jizhanming))
        .map(c => ({ product_id: c.product_id, new_jizhanming: c.new_jizhanming }))
      const review_accepted = syncResult.review
        .filter(rv => checkedReviewKeys.includes(rv.sheet_jizhanming))
        .map(rv => ({ product_id: rv.product_id, new_jizhanming: rv.sheet_jizhanming }))
      const r = await client.post('/products/sync-sheet/confirm', { changes, review_accepted })
      message.success(`已更新 ${r.data.updated} 条记录 / Updated ${r.data.updated} records`)
      setSyncModalOpen(false)
      setSyncResult(null)
      const ls = await client.get('/products/sync-sheet/last-sync')
      setLastSync(ls.data)
      load()
    } catch (err: any) {
      message.error(err._serverMessage || '确认失败，请重试 / Confirm failed, please retry')
    } finally {
      setConfirmLoading(false)
    }
  }

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

  async function handleExport() {
    setExporting(true)
    try {
      const params = new URLSearchParams()
      if (searchSeries) params.set('series', searchSeries)
      if (searchQ)      params.set('q', searchQ)
      const res = await client.get(`/products/export?${params}`, { responseType: 'blob' })
      const url = window.URL.createObjectURL(new Blob([res.data]))
      const a = document.createElement('a')
      a.href = url
      a.download = `products_${new Date().toISOString().slice(0, 10)}.csv`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(url)
    } catch {
      message.error('Export failed — please try again')
    } finally {
      setExporting(false)
    }
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
      render: (v, r) => (
        <button
          onClick={e => { e.stopPropagation(); setDetailId(r.id) }}
          style={{
            background: 'none', border: 'none', padding: 0, cursor: 'pointer',
            fontSize: 13, color: '#6366F1', textAlign: 'left',
            textDecoration: 'none', fontFamily: 'inherit',
          }}
          onMouseEnter={e => (e.currentTarget.style.textDecoration = 'underline')}
          onMouseLeave={e => (e.currentTarget.style.textDecoration = 'none')}
        >
          {v || '—'}
        </button>
      ),
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
          <RoleGuard minRole="admin">
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2 }}>
              <Button icon={<SyncOutlined />} onClick={handleSyncSheet} loading={syncLoading}>
                {isMobile ? '' : '从表格同步 / Sync from Sheet'}
              </Button>
              {lastSync.last_sync_at && (
                <Text style={{ color: '#9ca3af', fontSize: 11 }}>
                  上次同步 / Last synced: {lastSync.last_sync_at}
                  {lastSync.last_sync_count != null && ` (${lastSync.last_sync_count} 条更新 / updated)`}
                </Text>
              )}
            </div>
          </RoleGuard>
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
                <Button icon={<ExportOutlined />} onClick={handleExport} loading={exporting}>Export</Button>
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

      {/* Sheet Sync Modal */}
      <Modal
        title="从表格同步记账名 / Sync Jizhanming from Sheet"
        open={syncModalOpen}
        onCancel={() => { setSyncModalOpen(false); setSyncResult(null) }}
        footer={null}
        width={780}
      >
        {syncResult && (() => {
          const hasContent = syncResult.changed.length > 0 || syncResult.review.length > 0
            || syncResult.not_found.length > 0 || syncResult.duplicates.length > 0
          const willUpdate = checkedChangedKeys.length + checkedReviewKeys.length

          if (!hasContent) {
            return (
              <>
                <div style={{ textAlign: 'center', padding: '24px 0', color: '#10B981', fontSize: 15 }}>
                  所有记账名已是最新，未发现重复商品 / All up to date, no duplicates found
                </div>
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
                  <Button onClick={() => { setSyncModalOpen(false); setSyncResult(null) }}>关闭 / Close</Button>
                </div>
              </>
            )
          }

          const tabItems = [
            {
              key: 'changed',
              label: (
                <span>
                  待更新 / To Update
                  {syncResult.changed.length > 0 && (
                    <Badge count={syncResult.changed.length} size="small" style={{ marginLeft: 6 }} />
                  )}
                </span>
              ),
              children: syncResult.changed.length === 0 ? (
                <div style={{ textAlign: 'center', color: '#9ca3af', padding: '24px 0', fontSize: 13 }}>
                  无待更新记录 / No updates needed
                </div>
              ) : (
                <Table<SyncChangedItem>
                  size="small"
                  dataSource={syncResult.changed}
                  rowKey="sheet_jizhanming"
                  pagination={false}
                  scroll={{ y: 280 }}
                  rowSelection={{
                    selectedRowKeys: checkedChangedKeys,
                    onChange: keys => setCheckedChangedKeys(keys as string[]),
                  }}
                  columns={[
                    {
                      title: '当前记账名 / Current',
                      dataIndex: 'old_jizhanming',
                      render: v => <Text type="secondary">{v || '—'}</Text>,
                    },
                    {
                      title: '新记账名 / New',
                      dataIndex: 'new_jizhanming',
                      render: v => <Text style={{ color: '#F59E0B', fontWeight: 500 }}>{v}</Text>,
                    },
                  ] as ColumnsType<SyncChangedItem>}
                />
              ),
            },
            {
              key: 'review',
              label: (
                <span>
                  待确认 / To Review
                  {syncResult.review.length > 0 && (
                    <Badge count={syncResult.review.length} size="small" color="orange" style={{ marginLeft: 6 }} />
                  )}
                </span>
              ),
              children: syncResult.review.length === 0 ? (
                <div style={{ textAlign: 'center', color: '#9ca3af', padding: '24px 0', fontSize: 13 }}>
                  无需确认记录 / No items to review
                </div>
              ) : (
                <Table<SyncReviewItem>
                  size="small"
                  dataSource={syncResult.review}
                  rowKey="sheet_jizhanming"
                  pagination={false}
                  scroll={{ y: 280 }}
                  rowSelection={{
                    selectedRowKeys: checkedReviewKeys,
                    onChange: keys => setCheckedReviewKeys(keys as string[]),
                  }}
                  columns={[
                    {
                      title: 'Sheet记账名 / Sheet Name',
                      dataIndex: 'sheet_jizhanming',
                    },
                    {
                      title: '最接近匹配 / Best Match',
                      dataIndex: 'current_jizhanming',
                      render: v => <Text type="secondary">{v || '—'}</Text>,
                    },
                    {
                      title: '匹配度 / Score',
                      dataIndex: 'score',
                      width: 120,
                      render: (v: number) => (
                        <Tag color={v >= 70 ? 'green' : 'orange'}>{v}%</Tag>
                      ),
                    },
                  ] as ColumnsType<SyncReviewItem>}
                />
              ),
            },
            {
              key: 'not_found',
              label: (
                <span>
                  未找到 / Not Found
                  {syncResult.not_found.length > 0 && (
                    <Badge count={syncResult.not_found.length} size="small" color="red" style={{ marginLeft: 6 }} />
                  )}
                </span>
              ),
              children: syncResult.not_found.length === 0 ? (
                <div style={{ textAlign: 'center', color: '#9ca3af', padding: '24px 0', fontSize: 13 }}>
                  无未找到记录 / Nothing unmatched
                </div>
              ) : (
                <div style={{ maxHeight: 300, overflowY: 'auto', padding: '8px 0' }}>
                  {syncResult.not_found.map((jzm, idx) => (
                    <div key={idx} style={{ padding: '4px 0', display: 'flex', alignItems: 'center', gap: 8 }}>
                      <ExclamationCircleOutlined style={{ color: '#EF4444', flexShrink: 0 }} />
                      <Text style={{ fontSize: 13 }}>{jzm}</Text>
                    </div>
                  ))}
                </div>
              ),
            },
            {
              key: 'duplicates',
              label: (
                <span>
                  重复商品 / Duplicates
                  {syncResult.duplicates.length > 0 && (
                    <Badge count={syncResult.duplicates.length} size="small" color="volcano" style={{ marginLeft: 6 }} />
                  )}
                </span>
              ),
              children: syncResult.duplicates.length === 0 ? (
                <div style={{ textAlign: 'center', color: '#9ca3af', padding: '24px 0', fontSize: 13 }}>
                  未发现重复商品 / No duplicates found
                </div>
              ) : (
                <Table<SyncDuplicatePair>
                  size="small"
                  dataSource={syncResult.duplicates}
                  rowKey={r => `${r.product_a.id}-${r.product_b.id}`}
                  pagination={false}
                  scroll={{ y: 280 }}
                  columns={[
                    {
                      title: '商品A / Product A',
                      render: (_, r) => (
                        <div>
                          <div style={{ fontWeight: 500 }}>{r.product_a.jizhanming}</div>
                          <Text type="secondary" style={{ fontSize: 11, fontFamily: 'monospace' }}>{r.product_a.sku}</Text>
                        </div>
                      ),
                    },
                    {
                      title: '商品B / Product B',
                      render: (_, r) => (
                        <div>
                          <div style={{ fontWeight: 500 }}>{r.product_b.jizhanming}</div>
                          <Text type="secondary" style={{ fontSize: 11, fontFamily: 'monospace' }}>{r.product_b.sku}</Text>
                        </div>
                      ),
                    },
                    {
                      title: '相似度 / Similarity',
                      width: 180,
                      render: (_, r) => (
                        <Tag color={r.severity === 'likely' ? 'red' : 'orange'}>
                          {r.severity === 'likely' ? '极可能重复 / Likely' : '可能重复 / Possible'} {r.score}%
                        </Tag>
                      ),
                    },
                  ] as ColumnsType<SyncDuplicatePair>}
                />
              ),
            },
          ]

          return (
            <>
              <Tabs
                activeKey={syncActiveTab}
                onChange={setSyncActiveTab}
                items={tabItems}
                size="small"
              />
              <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginTop: 12,
                paddingTop: 12,
                borderTop: '1px solid #f0f0f0',
              }}>
                <Text type="secondary" style={{ fontSize: 13 }}>
                  将更新 {willUpdate} 条记录 / Will update {willUpdate} records
                  {syncResult.unchanged > 0 && ` · 无变化 / Unchanged: ${syncResult.unchanged}`}
                </Text>
                <Space>
                  <Button onClick={() => { setSyncModalOpen(false); setSyncResult(null) }}>
                    取消 / Cancel
                  </Button>
                  {willUpdate > 0 && (
                    <Button type="primary" loading={confirmLoading} onClick={handleConfirmSync}>
                      确认更新 {willUpdate} 条记录 / Confirm {willUpdate} records
                    </Button>
                  )}
                </Space>
              </div>
            </>
          )
        })()}
      </Modal>
    </div>
  )
}
