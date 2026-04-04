import { useEffect, useState } from 'react'
import { Modal, Form, Input, InputNumber, Select, Divider, Radio, message } from 'antd'
import { useIsMobile } from '../../hooks/useIsMobile'
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
  dan_per_xiang?: number | null
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
  const isMobile = useIsMobile()

  // Track whether this product is a blind box so we can show/hide hierarchy fields
  const [isBlindBox, setIsBlindBox] = useState(false)

  useEffect(() => {
    if (open) {
      const blindBox = product?.product_type === '盲盒'
      setIsBlindBox(blindBox)
      form.setFieldsValue({
        ...product,
        // If it's a blind box the selector shows '盲盒'; otherwise keep product_type as subtype
        _is_blind_box: blindBox ? 'yes' : 'no',
      })
    } else {
      form.resetFields()
      setIsBlindBox(false)
    }
  }, [open, product, form])

  async function handleOk() {
    try {
      const values = await form.validateFields()
      // Derive product_type from the blind-box toggle
      const payload = { ...values }
      delete payload._is_blind_box
      if (isBlindBox) {
        payload.product_type = '盲盒'
      } else {
        // For non-blind box, clear hierarchy fields so they don't pollute the record
        payload.boxes_per_dan = null
        payload.dan_per_xiang = null
      }
      if (isEdit) {
        await client.patch(`/products/${product!.id}`, payload)
        message.success('Product updated')
      } else {
        await client.post('/products', payload)
        message.success('Product created')
      }
      onSaved()
    } catch (err: any) {
      if (err?.errorFields) return
      message.error(err?.response?.data?.error ?? 'Save failed')
    }
  }

  const seriesOptions = series.map(s => ({ value: s, label: s }))
  // Non-blind-box type options: everything except '盲盒'
  const nonBlindTypes = productTypes.filter(t => t !== '盲盒')
  const typeOptions   = nonBlindTypes.map(t => ({ value: t, label: t }))

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
      getContainer={false}
    >
      <Form form={form} layout="vertical" style={{ marginTop: 8 }}>

        {/* — Product type selector — */}
        <Form.Item name="_is_blind_box" label="Product Type" style={{ marginBottom: 12 }}>
          <Radio.Group
            onChange={e => {
              const blind = e.target.value === 'yes'
              setIsBlindBox(blind)
              // Clear subtype field when switching to blind box
              if (blind) form.setFieldValue('product_type', '盲盒')
              else form.setFieldValue('product_type', '')
            }}
          >
            <Radio.Button value="yes" style={{ minHeight: 36, lineHeight: '34px' }}>盲盒 Blind Box</Radio.Button>
            <Radio.Button value="no"  style={{ minHeight: 36, lineHeight: '34px' }}>非盲盒 Non-Blind Box</Radio.Button>
          </Radio.Group>
        </Form.Item>

        {/* — Core identity — */}
        <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr', gap: '0 16px' }}>
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

        <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr 1fr', gap: '0 16px' }}>
          <Form.Item name="ip_series" label="Series">
            <Select
              showSearch
              allowClear
              options={seriesOptions}
              optionFilterProp="label"
              placeholder="Select or type..."
              getPopupContainer={trigger => trigger.parentElement!}
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
          {/* For non-blind box: free text subtype. For blind box: locked to '盲盒'. */}
          {isBlindBox ? (
            <Form.Item label="Type">
              <Input value="盲盒" disabled />
            </Form.Item>
          ) : (
            <Form.Item name="product_type" label="Type">
              <Select
                showSearch
                allowClear
                options={typeOptions}
                optionFilterProp="label"
                placeholder="Select or type..."
                getPopupContainer={trigger => trigger.parentElement!}
                onSearch={v => form.setFieldValue('product_type', v)}
              />
            </Form.Item>
          )}
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
        <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr 1fr', gap: '0 16px' }}>
          <Form.Item name="brand" label="Brand">
            <Input />
          </Form.Item>
          <Form.Item name="release_date" label="Release Date">
            <Input placeholder="YYYY-MM-DD" />
          </Form.Item>
        </div>

        {/* — Blind box hierarchy fields (only shown for blind boxes) — */}
        {isBlindBox && (
          <>
            <Divider style={{ margin: '4px 0 12px', borderColor: '#e0e7ff' }}>
              <span style={{ fontSize: 12, color: '#6366F1', fontWeight: 600 }}>盲盒规格 Blind Box Ratios</span>
            </Divider>
            <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr', gap: '0 16px' }}>
              <Form.Item name="boxes_per_dan" label="盒/端 (Boxes per Display)">
                <InputNumber
                  style={{ width: '100%', minHeight: 36 }}
                  min={1}
                  placeholder="e.g. 12"
                />
              </Form.Item>
              <Form.Item name="dan_per_xiang" label="端/箱 (Displays per Carton)">
                <InputNumber
                  style={{ width: '100%', minHeight: 36 }}
                  min={1}
                  placeholder="e.g. 4"
                />
              </Form.Item>
            </div>
            <div style={{ background: '#f0f0ff', borderRadius: 6, padding: '8px 12px', marginBottom: 12, fontSize: 12, color: '#4338ca' }}>
              {(() => {
                const bpd = form.getFieldValue('boxes_per_dan')
                const dpx = form.getFieldValue('dan_per_xiang')
                if (bpd && dpx) return `1箱 = ${dpx}端 = ${dpx * bpd}盒`
                if (bpd) return `1端 = ${bpd}盒`
                return '填写后将显示换算关系'
              })()}
            </div>
          </>
        )}

        <Divider style={{ margin: '4px 0 16px', borderColor: '#f0f0f0' }} />

        {/* — Hidden figures (blind box only) — */}
        {isBlindBox && (
          <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr 1fr', gap: '0 16px' }}>
            <Form.Item name="hidden_count" label="# Secret Variants">
              <Input placeholder="0" />
            </Form.Item>
            <Form.Item name="hidden_has_small" label="Has Small Secret">
              <Select options={YN_OPTIONS} getPopupContainer={trigger => trigger.parentElement!} />
            </Form.Item>
            <Form.Item name="hidden_has_large" label="Has Large Secret">
              <Select options={YN_OPTIONS} getPopupContainer={trigger => trigger.parentElement!} />
            </Form.Item>
          </div>
        )}

        <Form.Item name="notes" label="Notes" style={{ marginBottom: 0 }}>
          <Input.TextArea rows={2} placeholder="Optional internal notes..." />
        </Form.Item>

      </Form>
    </Modal>
  )
}
