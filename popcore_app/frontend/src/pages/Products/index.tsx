import { useState, useEffect, useCallback, useRef } from 'react'
import {
  Button, Space, Tag, Popconfirm, Select, Dropdown,
  message, Typography, Table, Badge, Spin, Modal, Tabs, Alert,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  PlusOutlined, ExportOutlined, DeleteOutlined,
  EditOutlined, PictureOutlined,
  ExclamationCircleOutlined, DownOutlined,
} from '@ant-design/icons'
import client from '../../api/client'
import { useAppStore } from '../../store'
import RoleGuard from '../../components/RoleGuard'
import ProductModal from './ProductModal'
import HiddenImagesModal from './HiddenImagesModal'
import PasteImportModal from './PasteImportModal'
import { IdentityExportModal, IdentityImportModal } from './IdentitySheetModals'
import CountSheetModal from './CountSheetModal'
import ProductSearchBar from './ProductSearchBar'
import ProductDetailDrawer from './ProductDetailDrawer'
import { useIsMobile } from '../../hooks/useIsMobile'
import { useHasRole } from '../../auth/useRole'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { ProductDetailPane, StockFigure } from './ProductDetail'
import { ProductsTabs } from './ProductsNav'
import './products.css'

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
  stock_form?: 'sealed_set' | 'random_box' | 'confirmed_design' | 'piece' | null
  stock_unit?: 'set' | 'box' | 'piece' | null
  identity_status?: 'unverified' | 'verified'
  sub_item_count?: number
}

interface StockRow {
  id:           number  // /api/stock rows are keyed by the product's id
  upstairs_qty: number
  instore_qty:  number
}

interface SyncCandidate {
  id: number
  sku: string
  name_cn_en: string
  jizhanming: string | null
  sheet_ref: string | null
  product_type: string | null
  score: number
}

interface SyncChangedItem {
  key:              number
  ref:              string
  sheet_jizhanming: string
  sheet_name:       string
  product_id:       number | null
  sku:              string
  old_jizhanming:   string
  new_jizhanming:   string
  match_via:        'ref' | 'name' | 'jzm_b'
  score:            number
  prechecked:       boolean
  candidates:       SyncCandidate[]
  reason:           'ambiguous' | 'type_conflict' | 'fuzzy'
  expected_jizhanming: string | null
  expected_sheet_ref: string | null
  expected_name_cn_en: string | null
  expected_product_type: string | null
  sheet_ref:        string
}

interface SyncConflict {
  key:                string
  reason:             'multi_row' | 'ref_mismatch' | 'destination_collision'
  product_id:         number
  sku:                string
  product_jizhanming: string
  stored_ref?:        string
  rows: { key: number; ref: string; sheet_jizhanming: string; sheet_name: string; match_via: string; score: number }[]
}

interface SyncNewProduct {
  key:        number
  ref:        string
  jizhanming: string
  name:       string
}

interface SyncRefLearn {
  expected_jizhanming: string | null
  expected_sheet_ref: string | null
  expected_name_cn_en: string | null
  expected_product_type: string | null
  product_id: number
  sku:        string
  jizhanming: string
  sheet_ref:  string
}

interface SyncDuplicatePair {
  product_a: { id: number; sku: string; jizhanming: string }
  product_b: { id: number; sku: string; jizhanming: string }
  score:     number
  severity:  'likely' | 'possible'
}

interface SyncResult {
  sheet_status: 'ok' | 'unavailable' | 'error'
  changed:      SyncChangedItem[]
  review:       SyncChangedItem[]
  conflicts:    SyncConflict[]
  new_products: SyncNewProduct[]
  ref_learns:   SyncRefLearn[]
  unchanged:    number
  duplicates:   SyncDuplicatePair[]
}

const MATCH_VIA_META: Record<string, { label: string; color: string }> = {
  ref:   { label: '编号',   color: 'green'  },
  name:  { label: '产品名', color: 'blue'   },
  jzm_b: { label: '记账名', color: 'orange' },
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

// Stock as dot plus number: red 0, yellow low, green otherwise (Figma Products · Catalog).
function stockBadge(total: number | null) {
  return <StockFigure value={total} />
}

const UNIT_NAMES: Record<string, string> = { set: '端', box: '盒', piece: '个' }

function Secrets({ product }: { product: { hidden_count?: string; hidden_has_small?: number; hidden_has_large?: number } }) {
  if (!product.hidden_count || product.hidden_count === '0') return null
  return <>{product.hidden_has_small ? <span className="pc-prod-secret">小隐藏</span> : null}{product.hidden_has_large ? <span className="pc-prod-secret">大隐藏</span> : null}</>
}

// Sub-items (sealed set, confirmed designs) are listed in the product's detail, not as catalog rows.
function SubCount({ product }: { product: { sub_item_count?: number } }) {
  if (!product.sub_item_count) return null
  return <span className="pc-prod-subcount" title="Sealed set and confirmed designs">+{product.sub_item_count} 子项</span>
}

export default function ProductsPage() {
  const isMobile = useIsMobile()
  const manager = useHasRole('manager')
  const { series, productTypes, selectedStore } = useAppStore()
  const [products,  setProducts]  = useState<Product[]>([])
  const [stockMap,  setStockMap]  = useState<Map<number, number> | null>(null)
  const [stockError, setStockError] = useState(false)
  const [loading,   setLoading]   = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [lastSuccess, setLastSuccess] = useState<Date | null>(null)
  const requestRef = useRef(0)
  const scopeRef = useRef('')
  const [searchQ,      setSearchQ]      = useState('')
  const [searchSeries, setSearchSeries] = useState('')
  const [searchType,   setSearchType]   = useState('')
  const [selected,  setSelected]  = useState<number[]>([])

  const [exporting,      setExporting]      = useState(false)
  const [editProduct,    setEditProduct]    = useState<Product | null>(null)
  const [modalOpen,      setModalOpen]      = useState(false)
  const [imagesProduct,  setImagesProduct]  = useState<Product | null>(null)
  const [pasteOpen,      setPasteOpen]      = useState(false)
  const [identityExportOpen, setIdentityExportOpen] = useState(false)
  const [identityImportOpen, setIdentityImportOpen] = useState(false)
  const [countSheetOpen, setCountSheetOpen] = useState(false)
  const [detailId,       setDetailId]       = useState<number | null>(() => { const id = new URLSearchParams(window.location.search).get('product_id'); return id && /^\d+$/.test(id) ? Number(id) : null })

  const [syncLoading,        setSyncLoading]        = useState(false)
  const [syncModalOpen,      setSyncModalOpen]      = useState(false)
  const [syncResult,         setSyncResult]         = useState<SyncResult | null>(null)
  const [checkedChangedKeys, setCheckedChangedKeys] = useState<number[]>([])
  const [checkedReviewKeys,  setCheckedReviewKeys]  = useState<number[]>([])
  const [checkedNewKeys,     setCheckedNewKeys]     = useState<number[]>([])
  const [syncActiveTab,      setSyncActiveTab]      = useState('changed')
  const [confirmLoading,     setConfirmLoading]     = useState(false)
  const [lastSync,           setLastSync]           = useState<LastSync>({ last_sync_at: null, last_sync_count: null })

  const load = useCallback(() => {
    const sc = selectedStore?.code
    const requestId = ++requestRef.current
    const scope = `${sc ?? ''}:${searchQ}:${searchSeries}:${searchType}`
    if (scopeRef.current !== scope) {
      scopeRef.current = scope
      setProducts([])
      setStockMap(null)
      setLastSuccess(null)
    }
    setLoading(true)
    setLoadError(null)
    const params: Record<string, string> = { top_level: '1' }  // sub-items show in their product's detail
    if (searchQ)      params.q = searchQ
    if (searchSeries) params.series = searchSeries
    if (searchType)   params.product_type = searchType
    const stockParams = sc ? { store_code: sc } : {}
    setStockMap(null)
    setStockError(false)
    client.get('/products/search', { params }).then(prodR => {
      if (requestId !== requestRef.current) return
      setProducts(prodR.data)
      setLastSuccess(new Date())
    }).catch(() => {
      if (requestId === requestRef.current) setLoadError('Unable to load products.')
    }).finally(() => {
      if (requestId === requestRef.current) setLoading(false)
    })
    client.get('/stock', { params: stockParams }).then(stockR => {
      if (requestId !== requestRef.current) return
      const m = new Map<number, number>()
      ;(stockR.data as StockRow[]).forEach(r => {
        m.set(r.id, (r.upstairs_qty ?? 0) + (r.instore_qty ?? 0))
      })
      setStockMap(m)
    }).catch(() => {
      if (requestId === requestRef.current) setStockError(true)
    })
  }, [searchQ, searchSeries, searchType, selectedStore?.code])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    client.get('/products/sync-sheet/last-sync')
      .then(r => setLastSync(r.data))
      .catch(() => {/* users below manager get 403 — silently ignore */})
  }, [])

  async function handleSyncSheet() {
    setSyncLoading(true)
    try {
      const r = await client.post('/products/sync-sheet')
      const data = r.data as SyncResult
      setSyncResult(data)
      // Only unambiguous exact identities without conflicting clues are pre-checked.
      setCheckedChangedKeys(data.changed.filter(c => c.prechecked).map(c => c.key))
      setCheckedReviewKeys([])
      setCheckedNewKeys([])
      if (data.changed.length > 0)           setSyncActiveTab('changed')
      else if (data.review.length > 0)       setSyncActiveTab('review')
      else if (data.new_products.length > 0) setSyncActiveTab('new_products')
      else if (data.conflicts.length > 0)    setSyncActiveTab('conflicts')
      else                                   setSyncActiveTab('duplicates')
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
      const pick = (c: SyncChangedItem) =>
        ({ product_id: c.product_id, new_jizhanming: c.new_jizhanming, sheet_ref: c.sheet_ref,
          expected_jizhanming: c.expected_jizhanming, expected_sheet_ref: c.expected_sheet_ref,
          expected_name_cn_en: c.expected_name_cn_en, expected_product_type: c.expected_product_type })
      const changes = syncResult.changed
        .filter(c => checkedChangedKeys.includes(c.key)).map(pick)
      const review_accepted = syncResult.review
        .filter(rv => checkedReviewKeys.includes(rv.key) && rv.product_id != null).map(pick)
      const create_products = syncResult.new_products
        .filter(n => checkedNewKeys.includes(n.key))
        .map(n => ({ sheet_ref: n.ref, jizhanming: n.jizhanming, name_cn_en: n.name }))
      const r = await client.post('/products/sync-sheet/confirm', {
        changes, review_accepted, create_products,
        ref_learns: syncResult.ref_learns,
      })
      message.success(
        `已更新 ${r.data.updated} · 新建 ${r.data.created} · 记住编号 ${r.data.refs_learned}`
        + ` / Updated ${r.data.updated}, created ${r.data.created}, refs learned ${r.data.refs_learned}`)
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

  const label = (r: Product) => r.jizhanming || r.sku
  const columns: ColumnsType<Product> = [
    {
      title: '记账名 · SKU',
      dataIndex: 'jizhanming',
      width: 230,
      ellipsis: true,
      sorter: (a, b) => (a.jizhanming ?? '').localeCompare(b.jizhanming ?? ''),
      render: (v, r) => <span className="pc-prod-name"><strong>{v || '—'}<Secrets product={r} /><SubCount product={r} /></strong><small>{r.sku}</small></span>,
    },
    {
      title: '产品名',
      dataIndex: 'name_cn_en',
      ellipsis: true,
      sorter: (a, b) => (a.name_cn_en ?? '').localeCompare(b.name_cn_en ?? ''),
      render: (v, r) => <button type="button" className="pc-prod-link" onClick={e => { e.stopPropagation(); setDetailId(r.id) }}>{v || '—'}</button>,
    },
    {
      title: '系列 · 类型',
      dataIndex: 'ip_series',
      width: 170,
      ellipsis: true,
      sorter: (a, b) => (a.ip_series ?? '').localeCompare(b.ip_series ?? ''),
      render: (v, r) => <span className="pc-prod-muted">{[v, r.product_type].filter(Boolean).join(' · ') || '—'}</span>,
    },
    {
      title: '库存身份',
      width: 120,
      render: (_, r) => <span>
        <span className={r.identity_status === 'verified' ? 'pc-prod-id' : 'pc-prod-id pc-prod-id-warn'}>{r.identity_status === 'verified' ? '已核对' : '未核对'}<span className="pc-sr"> {r.identity_status === 'verified' ? 'Verified' : 'Unverified'}</span></span>
        <span className="pc-prod-unit" title={r.stock_form?.replaceAll('_', ' ')}>{r.stock_unit ? UNIT_NAMES[r.stock_unit] || r.stock_unit : '—'}</span>
      </span>,
    },
    {
      title: '价格',
      dataIndex: 'price',
      width: 90,
      align: 'right',
      sorter: (a, b) => (a.price ?? 0) - (b.price ?? 0),
      render: v => v != null ? `$${v.toFixed(2)}` : <span className="pc-prod-muted">—</span>,
    },
    {
      title: '库存',
      width: 80,
      align: 'right',
      render: (_, r) => stockBadge(stockMap ? stockMap.get(r.id) ?? 0 : null),
    },
    {
      title: <span className="pc-sr">Actions</span>,
      width: manager ? 110 : 48,
      align: 'right',
      render: (_, r) => (
        <span className="pc-prod-actions">
          <Button type="text" size="small" aria-label={`View images for ${label(r)}`} icon={<PictureOutlined />} onClick={e => { e.stopPropagation(); setImagesProduct(r) }} />
          <RoleGuard minRole="manager">
            <Button type="text" size="small" aria-label={`Edit ${label(r)}`} icon={<EditOutlined />} onClick={e => { e.stopPropagation(); openEdit(r) }} />
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
              <Button type="text" size="small" aria-label={`Delete ${label(r)}`} icon={<DeleteOutlined />} danger onClick={e => e.stopPropagation()} />
            </Popconfirm>
          </RoleGuard>
        </span>
      ),
    },
  ]

  // 工具 Tools: catalog import/export, identity sheet export/import and the count sheet (managers).
  const tools = {
    items: [
      { key: 'import', label: '导入目录 Import' },
      { key: 'export', label: exporting ? '导出中… Exporting' : '导出目录 Export', disabled: exporting },
      { type: 'divider' as const },
      { key: 'identity-export', label: '导出身份表 Export identity sheet' },
      { key: 'identity-import', label: '导入身份表 Import identity sheet' },
      { type: 'divider' as const },
      { key: 'count', label: '盘点表 Count sheet' },
    ],
    onClick: ({ key }: { key: string }) => {
      if (key === 'import') setPasteOpen(true)
      else if (key === 'export') handleExport()
      else if (key === 'identity-export') setIdentityExportOpen(true)
      else if (key === 'identity-import') setIdentityImportOpen(true)
      else if (key === 'count') setCountSheetOpen(true)
    },
  }
  const headerActions = <>
    <Button aria-label="Refresh" onClick={load}><Icon name="refresh" size={16} />刷新</Button>
    <RoleGuard minRole="manager">
      <div className="pc-prod-sync">
        <Button aria-label="Sync from Google Sheet" onClick={handleSyncSheet} loading={syncLoading}>{!syncLoading && <Icon name="refresh" size={16} />}同步表格 Sync sheet</Button>
        {lastSync.last_sync_at && <small>上次 {lastSync.last_sync_at}{lastSync.last_sync_count != null && ` · ${lastSync.last_sync_count} 条更新`}</small>}
      </div>
    </RoleGuard>
    <RoleGuard minRole="manager">
      <Dropdown menu={tools} trigger={['click']}><Button>工具 Tools <DownOutlined /></Button></Dropdown>
    </RoleGuard>
    <RoleGuard minRole="manager">
      <Button type="primary" aria-label="Add Product" onClick={openNew}><Icon name="plus" size={16} />新增产品 Add product</Button>
    </RoleGuard>
  </>
  const showPane = !isMobile && detailId != null

  return (
    <div className="pc-prod">
      <PageHeader title="产品" sub={`Products · ${products.length} 个产品`} actions={headerActions} />
      <ProductsTabs />
      {isMobile && manager && <div className="pc-prod-toolbar pc-prod-phone-actions">{headerActions}</div>}

      {stockError && <Alert role="alert" type="warning" showIcon
        message="Stock unavailable. Catalog tools are still available; stock quantities are unknown."
        style={{ marginBottom: 16 }} />}

      {loadError && (
        <Alert
          role="alert"
          type={lastSuccess ? 'warning' : 'error'}
          showIcon
          message={lastSuccess ? 'Showing previously loaded products' : loadError}
          description={lastSuccess ? `Last updated ${lastSuccess.toLocaleTimeString()}` : undefined}
          action={<Button onClick={load}>Retry</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      <div className="pc-prod-toolbar">
        <ProductSearchBar
          series={series}
          productTypes={productTypes}
          onChange={(q, s, t) => { setSearchQ(q); setSearchSeries(s); setSearchType(t) }}
        />
        {!isMobile && <span className="pc-prod-hint">库存 = 本店合计 · 点一行看详情</span>}
      </div>

      <div className={showPane ? 'pc-prod-grid pc-prod-grid-detail' : 'pc-prod-grid'}>
        <div className="pc-prod-list">
          {manager && selected.length > 0 && (
            <div className="pc-prod-selection">
              <span>已选 {selected.length} 个产品 · {selected.length} selected</span>
              <Button onClick={() => setSelected([])}>取消选择</Button>
              <Popconfirm
                title={`Delete ${selected.length} products? This cannot be undone.`}
                onConfirm={handleBulkDelete}
                okButtonProps={{ danger: true }}
              >
                <Button>删除 {selected.length} 个 Delete ({selected.length})</Button>
              </Popconfirm>
            </div>
          )}
          {isMobile ? (
            <Spin spinning={loading}>
              {!loading && !loadError && products.length === 0 && <p className="pc-quiet">No products found</p>}
              <ul className="pc-prod-rows">
                {products.map(p => (
                  <li key={p.id}>
                    <button type="button" className="pc-prod-open" onClick={() => setDetailId(p.id)}>
                      <span className="pc-prod-name">
                        <strong>{p.jizhanming || p.name_cn_en || 'Unnamed product'}<Secrets product={p} /><SubCount product={p} /></strong>
                        {p.name_cn_en && p.jizhanming && <small>{p.name_cn_en}</small>}
                        <small>{p.sku}</small>
                      </span>
                      {stockBadge(stockMap ? stockMap.get(p.id) ?? 0 : null)}
                      <span className="pc-prod-price">{p.price != null ? `$${p.price.toFixed(2)}` : '—'}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </Spin>
          ) : (
            <Table
              rowKey="id"
              loading={loading}
              dataSource={products}
              columns={columns}
              size="middle"
              rowSelection={manager ? {
                selectedRowKeys: selected,
                onChange: keys => setSelected(keys as number[]),
              } : undefined}
              rowClassName={r => r.id === detailId ? 'pc-prod-row-active' : ''}
              pagination={{ pageSize: 50, showTotal: t => `${t} products`, showSizeChanger: false }}
              scroll={{ x: 820 }}
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
        {showPane && <ProductDetailPane
          key={detailId}
          productId={detailId!}
          stockTotal={stockMap ? stockMap.get(detailId!) ?? 0 : null}
          onClose={() => setDetailId(null)}
          onOpen={setDetailId}
          onEdit={(p) => openEdit(p as unknown as Product)}
          onImages={(p) => setImagesProduct(p as unknown as Product)}
        />}
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
      <IdentityExportModal open={identityExportOpen} onClose={() => setIdentityExportOpen(false)} />
      <IdentityImportModal
        open={identityImportOpen}
        onClose={() => setIdentityImportOpen(false)}
        onDone={() => { setIdentityImportOpen(false); load() }}
      />
      <RoleGuard minRole="manager">
        <CountSheetModal
          open={countSheetOpen}
          storeCode={selectedStore?.code}
          onClose={() => setCountSheetOpen(false)}
        />
      </RoleGuard>
      <ProductDetailDrawer
        productId={isMobile ? detailId : null}
        stockTotal={stockMap ? stockMap.get(detailId ?? 0) ?? 0 : null}
        onClose={() => setDetailId(null)}
        onOpen={setDetailId}
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
          const sheetDown  = syncResult.sheet_status !== 'ok'
          const hasContent = syncResult.changed.length > 0 || syncResult.review.length > 0
            || syncResult.new_products.length > 0 || syncResult.conflicts.length > 0
            || syncResult.duplicates.length > 0 || syncResult.ref_learns.length > 0
          const willUpdate = checkedChangedKeys.length + checkedReviewKeys.length
          const willCreate = checkedNewKeys.length
          const willLearn  = syncResult.ref_learns.length
          const canConfirm = !sheetDown && (willUpdate + willCreate + willLearn > 0)

          const sheetDownAlert = sheetDown && (
            <div style={{
              background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 8,
              padding: '10px 14px', marginBottom: 12, color: '#B91C1C', fontSize: 13,
            }}>
              <ExclamationCircleOutlined style={{ marginRight: 8 }} />
              {syncResult.sheet_status === 'unavailable'
                ? '无法访问 Google 表格：服务账号凭证未配置。 / Sheet unreachable: service-account credentials are not configured.'
                : '无法访问 Google 表格：请求失败，请稍后重试。 / Sheet unreachable: the API request failed — try again later.'}
              <span style={{ display: 'block', marginTop: 2, color: '#991B1B' }}>
                本次结果不代表记账名已是最新。 / This is NOT an "all up to date" result.
              </span>
            </div>
          )

          if (!hasContent) {
            return (
              <>
                {sheetDownAlert}
                {!sheetDown && (
                  <div style={{ textAlign: 'center', padding: '24px 0', color: '#10B981', fontSize: 15 }}>
                    所有记账名已是最新，未发现重复商品 / All up to date, no duplicates found
                  </div>
                )}
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
                  <Button onClick={() => { setSyncModalOpen(false); setSyncResult(null) }}>关闭 / Close</Button>
                </div>
              </>
            )
          }

          const viaTag = (r: SyncChangedItem) => {
            const m = MATCH_VIA_META[r.match_via] ?? { label: r.match_via, color: 'default' }
            return <Tag color={m.color}>{m.label} {r.score}%</Tag>
          }
          const renameColumns: ColumnsType<SyncChangedItem> = [
            {
              title: '编号', dataIndex: 'ref', width: 64,
              render: v => <Text type="secondary" style={{ fontFamily: 'monospace', fontSize: 12 }}>{v || '—'}</Text>,
            },
            {
              title: '产品名 / Sheet Name', dataIndex: 'sheet_name', ellipsis: true,
              render: v => <Text type="secondary" style={{ fontSize: 12 }}>{v || '—'}</Text>,
            },
            {
              title: '当前记账名 / Current', dataIndex: 'old_jizhanming',
              render: v => <Text type="secondary">{v || '—'}</Text>,
            },
            {
              title: '新记账名 / New', dataIndex: 'new_jizhanming',
              render: v => <Text style={{ color: '#F59E0B', fontWeight: 500 }}>{v}</Text>,
            },
            {
              title: '产品 / Catalog product', key: 'candidate', width: 260,
              render: (_, r) => r.prechecked ? r.sku : (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Select
                    aria-label={`Catalog product for Sheet row ${r.key}`}
                    style={{ width: '100%' }}
                    placeholder="选择产品 / Choose product"
                    value={r.product_id ?? undefined}
                    options={r.candidates.map(c => ({
                      value: c.id,
                      label: `${c.sku} · ${c.name_cn_en || c.jizhanming || '—'} · ${c.score}%`,
                    }))}
                    onChange={id => {
                      const candidate = r.candidates.find(c => c.id === id)!
                      setCheckedReviewKeys(keys => keys.filter(key => key !== r.key))
                      setSyncResult(current => current && ({ ...current,
                        review: current.review.map(row => row.key !== r.key ? row : {
                          ...row, product_id: id, sku: candidate.sku, score: candidate.score,
                          old_jizhanming: candidate.jizhanming || '',
                          new_jizhanming: row.sheet_jizhanming || candidate.jizhanming || '',
                          expected_jizhanming: candidate.jizhanming,
                          expected_sheet_ref: candidate.sheet_ref,
                          expected_name_cn_en: candidate.name_cn_en,
                          expected_product_type: candidate.product_type,
                        }),
                      }))
                    }}
                  />
                  <Text type="warning" style={{ fontSize: 12 }}>
                    {r.reason === 'type_conflict' ? '类型或版本冲突 / Type or variant conflict'
                      : r.reason === 'ambiguous' ? '多个精确匹配 / Multiple exact matches'
                      : '请核对产品 / Verify product identity'}
                  </Text>
                </Space>
              ),
            },
            { title: '匹配 / Match', key: 'via', width: 110, render: (_, r) => viaTag(r) },
          ]

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
                  rowKey="key"
                  pagination={false}
                  scroll={{ x: 920, y: 280 }}
                  rowSelection={{
                    selectedRowKeys: checkedChangedKeys,
                    onChange: keys => setCheckedChangedKeys(keys as number[]),
                  }}
                  columns={renameColumns}
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
                <>
                  <div style={{ fontSize: 12, color: '#9ca3af', marginBottom: 6 }}>
                    请先选择正确产品，再勾选确认。 / Choose the correct product, then check the row to approve.
                  </div>
                  <Table<SyncChangedItem>
                    size="small"
                    dataSource={syncResult.review}
                    rowKey="key"
                    pagination={false}
                    scroll={{ x: 920, y: 260 }}
                    rowSelection={{
                      selectedRowKeys: checkedReviewKeys,
                      getCheckboxProps: row => ({ disabled: row.product_id == null }),
                      onChange: keys => setCheckedReviewKeys(keys as number[]),
                    }}
                    columns={renameColumns}
                  />
                </>
              ),
            },
            {
              key: 'new_products',
              label: (
                <span>
                  新产品 / New
                  {syncResult.new_products.length > 0 && (
                    <Badge count={syncResult.new_products.length} size="small" color="red" style={{ marginLeft: 6 }} />
                  )}
                </span>
              ),
              children: syncResult.new_products.length === 0 ? (
                <div style={{ textAlign: 'center', color: '#9ca3af', padding: '24px 0', fontSize: 13 }}>
                  表格中没有目录外的产品 / No sheet rows without a catalogue match
                </div>
              ) : (
                <>
                  <div style={{ fontSize: 12, color: '#9ca3af', marginBottom: 6 }}>
                    这些表格行没有匹配到任何产品。勾选后将创建新产品（自动生成 SKU，并记住编号）。
                    / Checked rows are created as new products with auto-generated SKUs.
                  </div>
                  <Table<SyncNewProduct>
                    size="small"
                    dataSource={syncResult.new_products}
                    rowKey="key"
                    pagination={false}
                    scroll={{ y: 260 }}
                    rowSelection={{
                      selectedRowKeys: checkedNewKeys,
                      onChange: keys => setCheckedNewKeys(keys as number[]),
                    }}
                    columns={[
                      {
                        title: '编号', dataIndex: 'ref', width: 64,
                        render: v => <Text type="secondary" style={{ fontFamily: 'monospace', fontSize: 12 }}>{v || '—'}</Text>,
                      },
                      { title: '记账名 / Jizhanming', dataIndex: 'jizhanming' },
                      {
                        title: '产品名称 / Name', dataIndex: 'name', ellipsis: true,
                        render: v => <Text type="secondary" style={{ fontSize: 12 }}>{v || '—'}</Text>,
                      },
                    ] as ColumnsType<SyncNewProduct>}
                  />
                </>
              ),
            },
            {
              key: 'conflicts',
              label: (
                <span>
                  冲突 / Conflicts
                  {syncResult.conflicts.length > 0 && (
                    <Badge count={syncResult.conflicts.length} size="small" color="volcano" style={{ marginLeft: 6 }} />
                  )}
                </span>
              ),
              children: syncResult.conflicts.length === 0 ? (
                <div style={{ textAlign: 'center', color: '#9ca3af', padding: '24px 0', fontSize: 13 }}>
                  无冲突 / No conflicts
                </div>
              ) : (
                <>
                  <div style={{ fontSize: 12, color: '#9ca3af', marginBottom: 6 }}>
                    以下记录存在产品、编号或记账名冲突，需先在表格中修正后重新同步。
                    / Fix these rows in the sheet, then sync again.
                  </div>
                  <div style={{ maxHeight: 280, overflowY: 'auto' }}>
                    {syncResult.conflicts.map(c => (
                      <div key={c.key} style={{
                        border: '1px solid #FDE68A', background: '#FFFBEB',
                        borderRadius: 8, padding: '8px 12px', marginBottom: 8,
                      }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                          <Tag color={c.reason === 'multi_row' ? 'volcano' : 'red'}>
                            {c.reason === 'multi_row' ? '多行认领同一产品'
                              : c.reason === 'destination_collision' ? '记账名已被占用 / Name already in use'
                              : '编号不一致'}
                          </Tag>
                          <Text style={{ fontWeight: 500 }}>{c.product_jizhanming || '—'}</Text>
                          <Text type="secondary" style={{ fontSize: 11, fontFamily: 'monospace' }}>{c.sku}</Text>
                          {c.reason === 'ref_mismatch' && (
                            <Text type="secondary" style={{ fontSize: 11 }}>已记录编号: {c.stored_ref}</Text>
                          )}
                        </div>
                        {c.rows.map(r => (
                          <div key={r.key} style={{ fontSize: 12, color: '#92400E', paddingLeft: 4 }}>
                            行{r.key} · 编号 {r.ref || '—'} · 记账名 “{r.sheet_jizhanming}” · 产品名 “{r.sheet_name || '—'}” · {r.match_via} {r.score}%
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                </>
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
              {sheetDownAlert}
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
                  更新 {willUpdate} · 新建 {willCreate}
                  {willLearn > 0 && ` · 将记住 ${willLearn} 个编号`}
                  {syncResult.unchanged > 0 && ` · 无变化 ${syncResult.unchanged}`}
                </Text>
                <Space>
                  <Button onClick={() => { setSyncModalOpen(false); setSyncResult(null) }}>
                    取消 / Cancel
                  </Button>
                  {canConfirm && (
                    <Button type="primary" loading={confirmLoading} onClick={handleConfirmSync}>
                      确认 / Confirm{willUpdate + willCreate > 0 ? ` (${willUpdate + willCreate})` : ''}
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
