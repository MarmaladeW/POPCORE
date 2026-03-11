import { useEffect, useState } from 'react'
import { Drawer, Spin, Tag, Typography, Space, Button, Divider, Badge } from 'antd'
import { EditOutlined, PictureOutlined, TrophyOutlined } from '@ant-design/icons'
import client from '../../api/client'
import RoleGuard from '../../components/RoleGuard'

const { Text, Title } = Typography

interface ProductDetail {
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
}

const TYPE_COLORS: Record<string, string> = {
  'Blind Box': 'purple',
  'MEGA':      'orange',
  'Figure':    'blue',
}

function Field({ label, value }: { label: string; value?: string | number | null }) {
  if (!value && value !== 0) return null
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 11, color: '#9ca3af', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 13, color: '#111827' }}>{value}</div>
    </div>
  )
}

interface Props {
  productId: number | null
  stockTotal: number
  onClose: () => void
  onEdit: (p: ProductDetail) => void
  onImages: (p: ProductDetail) => void
}

export default function ProductDetailDrawer({ productId, stockTotal, onClose, onEdit, onImages }: Props) {
  const [product, setProduct] = useState<ProductDetail | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!productId) { setProduct(null); return }
    setLoading(true)
    client.get(`/products/${productId}`)
      .then(r => setProduct(r.data))
      .finally(() => setLoading(false))
  }, [productId])

  const hasSecrets = product && product.hidden_count && product.hidden_count !== '0'

  return (
    <Drawer
      open={!!productId}
      onClose={onClose}
      width={420}
      title={
        product ? (
          <div style={{ lineHeight: 1.3 }}>
            <div style={{ fontSize: 15, fontWeight: 600, color: '#111827' }}>
              {product.jizhanming || product.name_cn_en || '—'}
              {product.is_bestseller ? (
                <TrophyOutlined style={{ color: '#F59E0B', marginLeft: 8, fontSize: 14 }} />
              ) : null}
            </div>
            <Text style={{ fontSize: 11, fontFamily: 'monospace', color: '#9ca3af' }}>{product.sku}</Text>
          </div>
        ) : 'Product Detail'
      }
      styles={{ body: { padding: '16px 24px' } }}
      footer={
        product ? (
          <Space>
            <RoleGuard minRole="manager">
              <Button icon={<EditOutlined />} onClick={() => onEdit(product)}>Edit</Button>
            </RoleGuard>
            <Button icon={<PictureOutlined />} onClick={() => onImages(product)}>Images</Button>
          </Space>
        ) : null
      }
    >
      <Spin spinning={loading}>
        {product && (
          <div>
            {/* Price + Stock */}
            <div style={{ display: 'flex', gap: 16, marginBottom: 16, alignItems: 'center' }}>
              <div>
                <div style={{ fontSize: 11, color: '#9ca3af' }}>Price (CA$)</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: '#6366F1' }}>
                  {product.price != null ? `$${product.price.toFixed(2)}` : '—'}
                </div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: '#9ca3af', marginBottom: 4 }}>Stock</div>
                {stockTotal === 0
                  ? <Badge count={stockTotal} showZero style={{ backgroundColor: '#ef4444' }} />
                  : stockTotal <= 3
                    ? <Badge count={stockTotal} showZero style={{ backgroundColor: '#F59E0B' }} />
                    : <Badge count={stockTotal} showZero style={{ backgroundColor: '#10B981' }} />
                }
              </div>
            </div>

            {/* Tags */}
            <Space wrap style={{ marginBottom: 16 }}>
              {product.ip_series  && <Tag color="blue">{product.ip_series}</Tag>}
              {product.product_type && <Tag color={TYPE_COLORS[product.product_type] ?? 'default'}>{product.product_type}</Tag>}
              {product.brand      && <Tag>{product.brand}</Tag>}
            </Space>

            {product.name_cn_en && product.jizhanming && (
              <div style={{ marginBottom: 16, padding: '8px 12px', background: '#f9fafb', borderRadius: 8 }}>
                <div style={{ fontSize: 11, color: '#9ca3af', marginBottom: 2 }}>Full Product Name</div>
                <div style={{ fontSize: 13, color: '#374151' }}>{product.name_cn_en}</div>
              </div>
            )}

            <Divider style={{ margin: '12px 0' }} />

            {/* Details grid */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 16px' }}>
              <Field label="Release Date"  value={product.release_date} />
              <Field label="Boxes / Dan"   value={product.boxes_per_dan} />
              <Field label="Edition Size"  value={product.edition_size} />
              <Field label="Channel"       value={product.channel} />
            </div>
            <Field label="Style Notes" value={product.style_notes} />

            {/* Secrets section */}
            {hasSecrets && (
              <>
                <Divider style={{ margin: '12px 0' }} />
                <div style={{ marginBottom: 8 }}>
                  <Text style={{ fontSize: 12, fontWeight: 600, color: '#374151' }}>
                    Secret Variants ({product.hidden_count})
                  </Text>
                </div>
                <Space wrap style={{ marginBottom: 8 }}>
                  {product.hidden_has_small ? (
                    <Tag color="gold">
                      Small Secret{product.hidden_prob_small ? ` (${product.hidden_prob_small})` : ''}
                    </Tag>
                  ) : null}
                  {product.hidden_has_large ? (
                    <Tag color="orange">
                      Large Secret{product.hidden_prob_large ? ` (${product.hidden_prob_large})` : ''}
                    </Tag>
                  ) : null}
                </Space>
                {product.hidden && <Field label="Hidden Info" value={product.hidden} />}
              </>
            )}

            {/* Notes */}
            {product.notes && (
              <>
                <Divider style={{ margin: '12px 0' }} />
                <div>
                  <div style={{ fontSize: 11, color: '#9ca3af', marginBottom: 4 }}>Notes</div>
                  <div style={{ fontSize: 13, color: '#374151', whiteSpace: 'pre-wrap' }}>{product.notes}</div>
                </div>
              </>
            )}
          </div>
        )}
      </Spin>
    </Drawer>
  )
}
