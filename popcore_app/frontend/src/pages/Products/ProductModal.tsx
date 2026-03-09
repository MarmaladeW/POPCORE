import { useEffect } from 'react'
import { Modal, Form, Input, InputNumber, Select, Divider, message } from 'antd'
import client from '../../api/client'
import { useAppStore } from '../../store'

interface Product {
  id?: number
  sku?: string
  jizhanming?: string
  name_cn_en?: string
  price?: number | null
  ip_series?: string
  product_type?: string
  brand?: string
  release_date?: string
  notes?: string
  boxes_per_dan?: number | null
  hidden_count?: string
  hidden_has_small?: number
  hidden_has_large?: number
  hidden_prob_small?: string
  hidden_prob_large?: string
}

interface Props {
  open: boolean
  product: Product | null
  onClose: () => void
  onSaved: () => void
}

const YN_OPTIONS = [
  { value: 0, label: 'No' },
  { value: 1, label: 'Yes' },
]

export default function ProductModal({ open, product, onClose, onSaved }: Props) {
  const [form] = Form.useForm()
  const { series, productTypes } = useAppStore()
  const isEdit = !!product?.id

  useEffect(() => {
    if (open) {
      form.setFieldsValue(product ?? {})
    } else {
      form.resetFields()
    }
  }, [open, product, form])

  async function handleOk() {
    try {
      const values = await form.validateFields()
      if (isEdit) {
        await client.patch(`/products/${product!.id}`, values)
        message.success('Product updated')
      } else {
        await client.post('/products', values)
        message.success('Product created')
      }
      onSaved()
    } catch (err: any) {
      if (err?.errorFields) return
      message.error(err?.response?.data?.error ?? 'Save failed')
    }
  }

  const seriesOptions   = series.map(s => ({ value: s, label: s }))
  const typeOptions     = productTypes.map(t => ({ value: t, label: t }))

  return (
    <Modal
      title={
        <div style={{ fontWeight: 700, fontSize: 16 }}>
          {isEdit ? 'Edit Product' : 'Add Product'}
          {isEdit && product?.sku && (
            <span style={{ marginLeft: 10, fontFamily: 'monospace', fontSize: 12, color: '#9ca3af', fontWeight: 400 }}>
              {product.sku}
            </span>
          )}
        </div>
      }
      open={open}
      onOk={handleOk}
      onCancel={onClose}
      width={Math.min(640, window.innerWidth - 32)}
      okText={isEdit ? 'Save' : 'Create'}
      cancelText="Cancel"
      destroyOnClose
    >
      <Form form={form} layout="vertical" style={{ marginTop: 8 }}>

        {/* — Core identity — */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 16px' }}>
          <Form.Item name="sku" label="SKU">
            <Input placeholder="Auto-generated if blank" style={{ fontFamily: 'monospace' }} />
          </Form.Item>
          <Form.Item name="jizhanming" label="记账名 (Internal Name)">
            <Input />
          </Form.Item>
        </div>

        <Form.Item name="name_cn_en" label="Full Product Name">
          <Input placeholder="e.g. DIMOO Memories We Hold Series" />
        </Form.Item>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0 16px' }}>
          <Form.Item name="ip_series" label="Series">
            <Select
              showSearch
              allowClear
              options={seriesOptions}
              optionFilterProp="label"
              placeholder="Select or type..."
              dropdownRender={menu => (
                <>
                  {menu}
                  <div style={{ padding: '4px 8px', borderTop: '1px solid #f0f0f0', fontSize: 11, color: '#9ca3af' }}>
                    Type to add new series
                  </div>
                </>
              )}
              onSearch={v => form.setFieldValue('ip_series', v)}
            />
          </Form.Item>
          <Form.Item name="product_type" label="Type">
            <Select
              showSearch
              allowClear
              options={typeOptions}
              optionFilterProp="label"
              placeholder="Select or type..."
              onSearch={v => form.setFieldValue('product_type', v)}
            />
          </Form.Item>
          <Form.Item name="price" label="Price (CA$)">
            <InputNumber
              style={{ width: '100%' }}
              min={0}
              precision={2}
              prefix="$"
              placeholder="0.00"
            />
          </Form.Item>
        </div>

        <Divider style={{ margin: '4px 0 16px', borderColor: '#f0f0f0' }} />

        {/* — Details — */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0 16px' }}>
          <Form.Item name="brand" label="Brand">
            <Input />
          </Form.Item>
          <Form.Item name="release_date" label="Release Date">
            <Input placeholder="YYYY-MM-DD" />
          </Form.Item>
          <Form.Item name="boxes_per_dan" label="Boxes / Dan (端)">
            <InputNumber style={{ width: '100%' }} min={1} placeholder="e.g. 12" />
          </Form.Item>
        </div>

        <Divider style={{ margin: '4px 0 16px', borderColor: '#f0f0f0' }} />

        {/* — Hidden figures — */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0 16px' }}>
          <Form.Item name="hidden_count" label="# Secret Variants">
            <Input placeholder="0" />
          </Form.Item>
          <Form.Item name="hidden_has_small" label="Has Small Secret">
            <Select options={YN_OPTIONS} />
          </Form.Item>
          <Form.Item name="hidden_has_large" label="Has Large Secret">
            <Select options={YN_OPTIONS} />
          </Form.Item>
        </div>

        <Form.Item name="notes" label="Notes" style={{ marginBottom: 0 }}>
          <Input.TextArea rows={2} placeholder="Optional internal notes..." />
        </Form.Item>

      </Form>
    </Modal>
  )
}
