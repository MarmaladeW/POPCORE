import { useEffect, useState, useRef } from 'react'
import { Modal, Upload, Button, Image, Popconfirm, Select, Space, message, Spin, Tag } from 'antd'
import { UploadOutlined, DeleteOutlined } from '@ant-design/icons'
import client from '../../api/client'
import { useHasRole } from '../../auth/useRole'

interface HiddenImage {
  id: number
  product_id: number
  image_type: string
  filename: string
}

interface Props {
  open: boolean
  product: { id: number; jizhanming?: string; name_cn_en?: string } | null
  onClose: () => void
}

const TYPE_LABELS: Record<string, string> = {
  general: '普通',
  small:   '小盲盒',
  large:   '大盲盒',
}

export default function HiddenImagesModal({ open, product, onClose }: Props) {
  const [images, setImages]     = useState<HiddenImage[]>([])
  const [loading, setLoading]   = useState(false)
  const [imgType, setImgType]   = useState<string>('general')
  const canManage = useHasRole('manager')
  const fileRef = useRef<HTMLInputElement>(null)

  function load() {
    if (!product) return
    setLoading(true)
    client.get(`/products/${product.id}/hidden_images`)
      .then(r => setImages(r.data))
      .finally(() => setLoading(false))
  }

  useEffect(() => { if (open) load() }, [open, product?.id])

  async function handleDelete(imgId: number) {
    try {
      await client.delete(`/products/${product!.id}/hidden_images/${imgId}`)
      message.success('已删除')
      load()
    } catch {
      message.error('删除失败')
    }
  }

  async function handleUpload(file: File) {
    const fd = new FormData()
    fd.append('image', file)
    fd.append('image_type', imgType)
    try {
      // Use axios directly for multipart — need raw token
      const { data: tokenData } = await client.get('/__token_noop__').catch(() => ({ data: null }))
      // get token via the interceptor by calling any endpoint
      const response = await client.post(`/products/${product!.id}/hidden_images`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      if (response.data.ok) {
        message.success('上传成功')
        load()
      }
    } catch {
      message.error('上传失败')
    }
    return false
  }

  const typeColor: Record<string, string> = { general: 'default', small: 'gold', large: 'orange' }

  return (
    <Modal
      title={`盲盒图片 — ${product?.jizhanming || product?.name_cn_en || ''}`}
      open={open}
      onCancel={onClose}
      footer={null}
      width={Math.min(640, window.innerWidth - 32)}
    >
      {canManage && (
        <Space style={{ marginBottom: 12 }}>
          <Select
            value={imgType}
            onChange={setImgType}
            options={Object.entries(TYPE_LABELS).map(([v, l]) => ({ value: v, label: l }))}
            style={{ width: 110 }}
          />
          <Upload
            showUploadList={false}
            beforeUpload={handleUpload}
            accept="image/*"
          >
            <Button icon={<UploadOutlined />}>上传图片</Button>
          </Upload>
        </Space>
      )}
      {loading ? (
        <Spin />
      ) : (
        <Image.PreviewGroup>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
            {images.map(img => (
              <div key={img.id} style={{ position: 'relative' }}>
                <Image
                  width={100}
                  height={100}
                  src={`/hidden_imgs/${img.filename}`}
                  style={{ objectFit: 'cover', borderRadius: 6 }}
                />
                <Tag
                  color={typeColor[img.image_type]}
                  style={{ position: 'absolute', top: 2, left: 2, fontSize: 10 }}
                >
                  {TYPE_LABELS[img.image_type]}
                </Tag>
                {canManage && (
                  <Popconfirm title="删除此图片？" onConfirm={() => handleDelete(img.id)}>
                    <Button
                      danger
                      size="small"
                      icon={<DeleteOutlined />}
                      style={{ position: 'absolute', bottom: 2, right: 2 }}
                    />
                  </Popconfirm>
                )}
              </div>
            ))}
            {images.length === 0 && <span style={{ color: '#999' }}>暂无图片</span>}
          </div>
        </Image.PreviewGroup>
      )}
    </Modal>
  )
}
