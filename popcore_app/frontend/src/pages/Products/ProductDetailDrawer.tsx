import { Button, Drawer, Grid, Space } from 'antd'
import RoleGuard from '../../components/RoleGuard'
import { ProductDetailBody, useProductDetail, type ProductDetailData } from './ProductDetail'

const { useBreakpoint } = Grid

interface Props {
  productId: number | null
  stockTotal: number | null
  onClose: () => void
  onEdit: (p: ProductDetailData) => void
  onImages: (p: ProductDetailData) => void
  onOpen?: (id: number) => void
}

// Phone: the product detail as a full-screen sheet with 图片 and 编辑 (desktop shows it beside the table).
export default function ProductDetailDrawer({ productId, stockTotal, onClose, onEdit, onImages, onOpen }: Props) {
  const detail = useProductDetail(productId)
  const product = detail.product
  const screens = useBreakpoint()
  const isMobile = !screens.md
  return (
    <Drawer
      open={!!productId}
      onClose={onClose}
      width={isMobile ? '100%' : 420}
      style={isMobile ? { position: 'fixed', top: 0, bottom: 0 } : undefined}
      title={product ? <span className="pc-prod-name"><strong>{product.jizhanming || product.name_cn_en || '—'}</strong><small>{product.sku}</small></span> : 'Product Detail'}
      styles={{ body: { padding: 0 } }}
      footer={product ? <Space>
        <RoleGuard minRole="manager"><Button type="primary" onClick={() => onEdit(product)}>编辑 Edit</Button></RoleGuard>
        <Button onClick={() => onImages(product)}>图片 Images</Button>
      </Space> : null}
    >
      <ProductDetailBody detail={detail} stockTotal={stockTotal} onOpen={onOpen} />
    </Drawer>
  )
}
