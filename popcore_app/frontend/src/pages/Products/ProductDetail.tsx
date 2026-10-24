import { useEffect, useState, type ReactNode } from 'react'
import { Button, Input, Spin, Tag } from 'antd'
import client from '../../api/client'
import RoleGuard from '../../components/RoleGuard'
import { saveAlias, deleteAlias } from '../../api/matcher'

export interface ProductDetailData {
  id: number
  sku: string
  name_cn_en: string
  jizhanming: string
  price: number | null
  ip_series: string
  product_type: string
  brand: string
  release_date: string
  edition_size: string
  channel: string
  hidden: string
  style_notes: string
  notes: string
  boxes_per_dan: number | null
  hidden_count: string
  hidden_has_small: number
  hidden_has_large: number
  hidden_prob_small: string
  hidden_prob_large: string
  is_bestseller: number
  aliases?: { id: number; alias: string }[]
  stock_form?: string | null
  stock_unit?: string | null
  design_name?: string | null
  identity_status?: 'unverified' | 'verified'
  barcodes?: { id: number; code: string; code_kind: string; input_unit: string; quantity_per_scan: number }[]
  conversions?: { id: number; target_sku: string; output_per_input: number; version: number }[]
}

// One product's facts and aliases; the drawer (phone) and the pane beside the table (desktop) share it.
export function useProductDetail(productId: number | null) {
  const [product, setProduct] = useState<ProductDetailData | null>(null)
  const [loading, setLoading] = useState(false)
  const [aliases, setAliases] = useState<{ id: number; alias: string }[]>([])
  const [aliasInput, setAliasInput] = useState('')
  useEffect(() => {
    if (!productId) { setProduct(null); setAliases([]); return }
    setLoading(true)
    client.get(`/products/${productId}`)
      .then(r => { setProduct(r.data); setAliases(r.data.aliases ?? []) })
      .finally(() => setLoading(false))
  }, [productId])
  async function addAlias() {
    if (!aliasInput.trim() || !product) return
    try {
      await saveAlias(product.id, aliasInput.trim())
      const r = await client.get(`/products/${product.id}`)
      setAliases(r.data.aliases ?? [])
      setAliasInput('')
    } catch { /* duplicate or empty alias — ignore */ }
  }
  async function removeAlias(aliasId: number) {
    if (!product) return
    await deleteAlias(product.id, aliasId)
    setAliases(prev => prev.filter(a => a.id !== aliasId))
  }
  return { product, loading, aliases, aliasInput, setAliasInput, addAlias, removeAlias }
}

const forms: Record<string, string> = { sealed_set: '整端', random_box: '盲盒单盒', confirmed_design: '确认款', ordinary: '普通' }

function Fact({ label, value }: { label: ReactNode; value?: ReactNode }) {
  if (value == null || value === '') return null
  return <div><dt>{label}</dt><dd>{value}</dd></div>
}

export function StockFigure({ value }: { value: number | null }) {
  if (value == null) return <span className="pc-prod-muted">Unknown</span>
  const tone = value === 0 ? 'out' : value <= 3 ? 'warn' : 'ok'
  return <span className={value === 0 ? 'pc-prod-stock pc-prod-stock-out' : 'pc-prod-stock'}><i className={`pc-dot pc-dot-${tone}`} aria-hidden="true" />{value}</span>
}

export function ProductDetailBody({ detail, stockTotal }: { detail: ReturnType<typeof useProductDetail>; stockTotal: number | null }) {
  const { product, loading, aliases, aliasInput, setAliasInput, addAlias, removeAlias } = detail
  const hasSecrets = product && product.hidden_count && product.hidden_count !== '0'
  return <Spin spinning={loading}>
    {product && <div className="pc-prod-detail-body">
      <div className="pc-prod-facts">
        <div><span>价格 Price (CA$)</span><strong>{product.price != null ? `$${product.price.toFixed(2)}` : '—'}</strong></div>
        <div><span>库存 Stock</span><strong><StockFigure value={stockTotal} /></strong></div>
      </div>
      <dl className="pc-prod-dl">
        <Fact label="全名 Full name" value={product.name_cn_en} />
        <Fact label="系列 · 类型" value={[product.ip_series, product.product_type].filter(Boolean).join(' · ')} />
        <Fact label="品牌 Brand" value={product.brand} />
      </dl>
      <h3 className="pc-prod-section">库存身份 <small>Inventory Identity</small></h3>
      <dl className="pc-prod-dl">
        <Fact label="状态" value={product.identity_status === 'verified' ? <>已核对 <span>Verified</span></> : <>未核对 <span>Unverified</span></>} />
        <Fact label="形态 · 单位" value={[product.stock_form ? forms[product.stock_form] || product.stock_form.replaceAll('_', ' ') : '', product.stock_unit ? `Unit: ${product.stock_unit}` : ''].filter(Boolean).join(' · ')} />
        <Fact label="确认款 Design" value={product.design_name} />
        {(product.conversions ?? []).map(c => <Fact key={`c${c.id}`} label="换算" value={`Conversion v${c.version}: 1 ${product.stock_unit} → ${c.output_per_input} boxes (${c.target_sku})`} />)}
        {(product.barcodes ?? []).map(b => <Fact key={`b${b.id}`} label="条码" value={`${b.code} · ${b.code_kind} · ${b.quantity_per_scan} ${b.input_unit}`} />)}
      </dl>
      <h3 className="pc-prod-section">详情 <small>Details</small></h3>
      <dl className="pc-prod-dl">
        <Fact label="发售日期" value={product.release_date} />
        <Fact label="盒/端 Boxes / Dan" value={product.boxes_per_dan} />
        <Fact label="限量 Edition" value={product.edition_size} />
        <Fact label="渠道 Channel" value={product.channel} />
        <Fact label="款式备注" value={product.style_notes} />
        {hasSecrets && <Fact label={`隐藏款 Secrets (${product.hidden_count})`} value={<>
          {product.hidden_has_small ? <span className="pc-prod-secret">小隐藏{product.hidden_prob_small ? ` (${product.hidden_prob_small})` : ''}</span> : null}{' '}
          {product.hidden_has_large ? <span className="pc-prod-secret">大隐藏{product.hidden_prob_large ? ` (${product.hidden_prob_large})` : ''}</span> : null}
          {product.hidden && <div>{product.hidden}</div>}
        </>} />}
        <Fact label="备注 Notes" value={product.notes} />
      </dl>
      <h3 className="pc-prod-section">别名 <small>Also Known As</small></h3>
      <div className="pc-prod-aliases">
        {aliases.map(a => <RoleGuard key={a.id} minRole="manager" fallback={<Tag>{a.alias}</Tag>}>
          <Tag closable onClose={() => removeAlias(a.id)}>{a.alias}</Tag>
        </RoleGuard>)}
        {aliases.length === 0 && <span className="pc-prod-muted">None</span>}
      </div>
      <RoleGuard minRole="manager">
        <div className="pc-prod-alias-add">
          <Input aria-label="Add alias" placeholder="加别名 Add alias..." value={aliasInput} onChange={e => setAliasInput(e.target.value)} onPressEnter={addAlias} />
          <Button onClick={addAlias}>添加</Button>
        </div>
      </RoleGuard>
    </div>}
  </Spin>
}

// Desktop: the detail beside the catalog table (Figma Products · Catalog).
export function ProductDetailPane({ productId, stockTotal, onClose, onEdit, onImages }: {
  productId: number; stockTotal: number | null; onClose: () => void
  onEdit: (p: ProductDetailData) => void; onImages: (p: ProductDetailData) => void
}) {
  const detail = useProductDetail(productId)
  const product = detail.product
  return <aside className="pc-prod-detail" aria-label="Product detail">
    <header className="pc-prod-detail-head">
      <span><strong>{product ? product.jizhanming || product.name_cn_en || '—' : 'Product Detail'}</strong>{product && <small>{product.sku}</small>}</span>
      {product && <Button onClick={() => onImages(product)}>图片 Images</Button>}
      {product && <RoleGuard minRole="manager"><Button type="primary" onClick={() => onEdit(product)}>编辑 Edit</Button></RoleGuard>}
      <Button type="text" aria-label="Close product detail" onClick={onClose}>×</Button>
    </header>
    <ProductDetailBody detail={detail} stockTotal={stockTotal} />
  </aside>
}
