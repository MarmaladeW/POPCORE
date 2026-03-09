import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  Input, Select, Button, Space, Tag, Popconfirm,
  message, Typography, Row, Col, Tooltip, Checkbox, Spin, Empty,
} from 'antd'
import {
  PlusOutlined, ExportOutlined, DeleteOutlined, SearchOutlined, EditOutlined,
  PictureOutlined, SortAscendingOutlined,
} from '@ant-design/icons'
import type { ReactNode } from 'react'
import client from '../../api/client'
import { useAppStore } from '../../store'
import RoleGuard from '../../components/RoleGuard'
import ProductModal from './ProductModal'
import HiddenImagesModal from './HiddenImagesModal'
import PasteImportModal from './PasteImportModal'

const { Text } = Typography

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

type SortKey = 'jizhanming' | 'name_cn_en' | 'price' | ''
type SortDir = 'asc' | 'desc'

// Debounce hook
function useDebounce<T>(value: T, delay: number): T {
  const [dv, setDv] = useState<T>(value)
  useEffect(() => {
    const h = setTimeout(() => setDv(value), delay)
    return () => clearTimeout(h)
  }, [value, delay])
  return dv
}

// Highlight matching search term in text
function hl(text: string | null | undefined, term: string): ReactNode {
  if (!term || !text) return text ?? ''
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const re = new RegExp(`(${escaped})`, 'gi')
  const parts = text.split(re)
  return (
    <>
      {parts.map((p, i) =>
        re.test(p) ? <mark key={i} className="search-hl">{p}</mark> : p
      )}
    </>
  )
}

const SORT_OPTIONS = [
  { value: '', label: '默认排序' },
  { value: 'jizhanming_asc',  label: '记账名 A→Z' },
  { value: 'jizhanming_desc', label: '记账名 Z→A' },
  { value: 'price_asc',       label: '单价 低→高' },
  { value: 'price_desc',      label: '单价 高→低' },
  { value: 'name_cn_en_asc',  label: '名称 A→Z' },
  { value: 'name_cn_en_desc', label: '名称 Z→A' },
]

export default function ProductsPage() {
  const { series, productTypes } = useAppStore()
  const [products, setProducts]   = useState<Product[]>([])
  const [loading, setLoading]     = useState(false)
  const [inputQ, setInputQ]       = useState('')
  const debouncedQ                = useDebounce(inputQ, 300)
  const [filterSeries, setFilterSeries] = useState<string>('')
  const [filterType, setFilterType]     = useState<string>('')
  const [sortValue, setSortValue]       = useState<string>('')
  const [selected, setSelected]   = useState<number[]>([])

  // Modals
  const [editProduct, setEditProduct]       = useState<Product | null>(null)
  const [modalOpen, setModalOpen]           = useState(false)
  const [imagesProduct, setImagesProduct]   = useState<Product | null>(null)
  const [pasteOpen, setPasteOpen]           = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    const params: Record<string, string> = { limit: '200' }
    if (debouncedQ)   params.q = debouncedQ
    if (filterSeries) params.series = filterSeries
    if (filterType)   params.product_type = filterType
    client.get('/products/search', { params })
      .then(r => setProducts(r.data))
      .finally(() => setLoading(false))
  }, [debouncedQ, filterSeries, filterType])

  useEffect(() => { load() }, [load])

  function openNew() { setEditProduct(null); setModalOpen(true) }
  function openEdit(p: Product) { setEditProduct(p); setModalOpen(true) }

  function clearAll() {
    setInputQ('')
    setFilterSeries('')
    setFilterType('')
  }

  const hasFilters = !!(inputQ || filterSeries || filterType)

  async function handleBulkDelete() {
    if (!selected.length) return
    try {
      await client.post('/products/bulk_delete', selected)
      message.success(`已删除 ${selected.length} 个产品`)
      setSelected([])
      load()
    } catch {
      message.error('删除失败')
    }
  }

  async function handleExport() {
    const params = new URLSearchParams()
    if (filterSeries) params.set('series', filterSeries)
    if (debouncedQ)   params.set('q', debouncedQ)
    window.location.href = `/api/products/export?${params}`
  }

  function toggleSelect(id: number) {
    setSelected(prev =>
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
    )
  }

  // Client-side sort — split on last '_' so 'name_cn_en_asc' → key='name_cn_en', dir='asc'
  const displayProducts = useMemo(() => {
    if (!sortValue) return products
    const lastUs = sortValue.lastIndexOf('_')
    const key    = sortValue.substring(0, lastUs) as 'jizhanming' | 'name_cn_en' | 'price'
    const dir    = sortValue.substring(lastUs + 1) as SortDir
    return [...products].sort((a, b) => {
      if (key === 'price') {
        const va = a.price ?? 0, vb = b.price ?? 0
        return dir === 'asc' ? va - vb : vb - va
      }
      const va = String(a[key] ?? ''), vb = String(b[key] ?? '')
      const cmp = va.localeCompare(vb, 'zh-CN')
      return dir === 'asc' ? cmp : -cmp
    })
  }, [products, sortValue])

  const searchTerm = debouncedQ

  return (
    <div>
      {/* ── Toolbar ── */}
      <div style={{ position: 'sticky', top: 0, zIndex: 10, background: '#fff', paddingBottom: 12 }}>
        <Row gutter={[8, 8]} align="middle" style={{ marginBottom: 8 }}>
          <Col flex="auto">
            <Input
              prefix={<SearchOutlined style={{ color: '#bbb' }} />}
              placeholder="搜索 SKU / 记账名 / 产品名称"
              allowClear
              value={inputQ}
              onChange={e => setInputQ(e.target.value)}
              style={{ borderRadius: 8 }}
            />
          </Col>
          <Col>
            <Select
              placeholder="类型"
              allowClear
              value={filterType || undefined}
              style={{ width: 110 }}
              options={productTypes.map(t => ({ value: t, label: t }))}
              onChange={v => setFilterType(v ?? '')}
            />
          </Col>
          <Col>
            <Select
              suffixIcon={<SortAscendingOutlined />}
              placeholder="排序"
              value={sortValue || undefined}
              style={{ width: 130 }}
              options={SORT_OPTIONS}
              onChange={v => setSortValue(v ?? '')}
            />
          </Col>
          <RoleGuard minRole="manager">
            <Col>
              <Space size={6}>
                <Button icon={<PlusOutlined />} type="primary" onClick={openNew}>
                  新增
                </Button>
                <Tooltip title="粘贴导入">
                  <Button onClick={() => setPasteOpen(true)}>导入</Button>
                </Tooltip>
                <Button icon={<ExportOutlined />} onClick={handleExport}>导出</Button>
                {selected.length > 0 && (
                  <Popconfirm
                    title={`确定删除 ${selected.length} 个产品？此操作不可撤销。`}
                    onConfirm={handleBulkDelete}
                    okText="删除"
                    okButtonProps={{ danger: true }}
                  >
                    <Button danger icon={<DeleteOutlined />}>
                      删除 ({selected.length})
                    </Button>
                  </Popconfirm>
                )}
              </Space>
            </Col>
          </RoleGuard>
        </Row>

        {/* Series chip filters */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
          {series.map(s => (
            <Tag
              key={s}
              className="series-chip"
              color={filterSeries === s ? 'blue' : 'default'}
              onClick={() => setFilterSeries(filterSeries === s ? '' : s)}
            >
              {s}
            </Tag>
          ))}
          {hasFilters && (
            <Tag
              className="series-chip"
              color="red"
              onClick={clearAll}
              style={{ borderStyle: 'dashed' }}
            >
              清除全部筛选
            </Tag>
          )}
        </div>
      </div>

      {/* ── Card Grid ── */}
      <Spin spinning={loading}>
        {displayProducts.length === 0 && !loading ? (
          <Empty description="没有找到产品" style={{ marginTop: 60 }} />
        ) : (
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
            gap: 12,
            marginTop: 16,
          }}>
            {displayProducts.map(p => {
              const isSelected = selected.includes(p.id)
              return (
                <div
                  key={p.id}
                  className="product-card"
                  style={{
                    outline: isSelected ? '2px solid #6366f1' : undefined,
                  }}
                >
                  {/* Select checkbox */}
                  <RoleGuard minRole="manager">
                    <div style={{ position: 'absolute', top: 10, right: 10 }}>
                      <Checkbox
                        checked={isSelected}
                        onChange={() => toggleSelect(p.id)}
                        onClick={e => e.stopPropagation()}
                      />
                    </div>
                  </RoleGuard>

                  {/* Jizhanming — primary identity */}
                  <div
                    style={{
                      fontSize: 16,
                      fontWeight: 700,
                      color: '#1e1b4b',
                      marginBottom: 4,
                      paddingRight: 24,
                      lineHeight: 1.3,
                      cursor: 'pointer',
                    }}
                    onClick={() => openEdit(p)}
                  >
                    {hl(p.jizhanming || '—', searchTerm)}
                  </div>

                  {/* Price badge */}
                  <div style={{ marginBottom: 8 }}>
                    {p.price != null ? (
                      <Tag color="geekblue" style={{ fontWeight: 600, fontSize: 13 }}>
                        C${p.price}
                      </Tag>
                    ) : (
                      <Tag color="default" style={{ fontSize: 12 }}>—</Tag>
                    )}
                    {p.hidden_count && p.hidden_count !== '0' && (
                      <>
                        {p.hidden_has_small ? <Tag color="gold" style={{ fontSize: 10 }}>小盲</Tag> : null}
                        {p.hidden_has_large ? <Tag color="orange" style={{ fontSize: 10 }}>大盲</Tag> : null}
                      </>
                    )}
                  </div>

                  {/* SKU */}
                  <div style={{ marginBottom: 6 }}>
                    <Text type="secondary" style={{ fontSize: 11, fontFamily: 'monospace' }}>
                      {hl(p.sku, searchTerm)}
                    </Text>
                  </div>

                  {/* Name */}
                  {p.name_cn_en && (
                    <div style={{ marginBottom: 6 }}>
                      <Text type="secondary" style={{ fontSize: 12, lineHeight: 1.4, display: 'block' }}>
                        {hl(p.name_cn_en, searchTerm)}
                      </Text>
                    </div>
                  )}

                  {/* Series + Type tags */}
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 10 }}>
                    {p.ip_series && <Tag color="blue" style={{ fontSize: 10, margin: 0 }}>{p.ip_series}</Tag>}
                    {p.product_type && <Tag style={{ fontSize: 10, margin: 0 }}>{p.product_type}</Tag>}
                  </div>

                  {/* Actions */}
                  <div style={{ display: 'flex', gap: 4, borderTop: '1px solid #f5f5f5', paddingTop: 8 }}>
                    <Button
                      size="small"
                      type="link"
                      icon={<PictureOutlined />}
                      onClick={() => setImagesProduct(p)}
                      style={{ padding: '0 4px', fontSize: 12 }}
                    >
                      图
                    </Button>
                    <Button
                      size="small"
                      type="link"
                      icon={<EditOutlined />}
                      onClick={() => openEdit(p)}
                      style={{ padding: '0 4px', fontSize: 12 }}
                    >
                      编辑
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </Spin>

      {/* Result count */}
      {!loading && displayProducts.length > 0 && (
        <div style={{ textAlign: 'right', marginTop: 12 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>共 {displayProducts.length} 个产品</Text>
        </div>
      )}

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
        onDone={() => { setPasteOpen(false) }}
      />
    </div>
  )
}
