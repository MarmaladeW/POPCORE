import { useEffect } from 'react'
import { Modal, Form, Input, InputNumber, Select, Row, Col, message } from 'antd'
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
        message.success('更新成功')
      } else {
        await client.post('/products', values)
        message.success('创建成功')
      }
      onSaved()
    } catch (err: any) {
      if (err?.errorFields) return // validation error, already shown
      message.error(err?.response?.data?.error ?? '操作失败')
    }
  }

  const seriesOptions = series.map(s => ({ value: s, label: s }))
  const typeOptions   = productTypes.map(t => ({ value: t, label: t }))

  return (
    <Modal
      title={isEdit ? '编辑产品' : '新增产品'}
      open={open}
      onOk={handleOk}
      onCancel={onClose}
      width={680}
      okText={isEdit ? '保存' : '创建'}
    >
      <Form form={form} layout="vertical" size="small">
        <Row gutter={12}>
          <Col span={8}>
            <Form.Item name="sku" label="SKU">
              <Input placeholder="留空自动生成" />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="jizhanming" label="记账名">
              <Input />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="price" label="单价">
              <InputNumber style={{ width: '100%' }} min={0} precision={2} />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="name_cn_en" label="产品名称">
          <Input />
        </Form.Item>
        <Row gutter={12}>
          <Col span={12}>
            <Form.Item name="ip_series" label="系列">
              <Select
                showSearch
                allowClear
                options={seriesOptions}
                optionFilterProp="label"
                dropdownRender={(menu) => (
                  <>
                    {menu}
                    <div style={{ padding: '4px 8px', borderTop: '1px solid #f0f0f0', fontSize: 12, color: '#999' }}>
                      直接输入新系列名
                    </div>
                  </>
                )}
                onSearch={(v) => form.setFieldValue('ip_series', v)}
              />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="product_type" label="类型">
              <Select
                showSearch
                allowClear
                options={typeOptions}
                optionFilterProp="label"
                onSearch={(v) => form.setFieldValue('product_type', v)}
              />
            </Form.Item>
          </Col>
        </Row>
        <Row gutter={12}>
          <Col span={8}>
            <Form.Item name="brand" label="品牌">
              <Input />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="release_date" label="发售日期">
              <Input placeholder="YYYY-MM-DD" />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="boxes_per_dan" label="每端盒数">
              <InputNumber style={{ width: '100%' }} min={1} />
            </Form.Item>
          </Col>
        </Row>
        <Row gutter={12}>
          <Col span={6}>
            <Form.Item name="hidden_count" label="盲盒款数">
              <Input placeholder="0" />
            </Form.Item>
          </Col>
          <Col span={6}>
            <Form.Item name="hidden_has_small" label="含小盲盒">
              <Select options={[{ value: 0, label: '无' }, { value: 1, label: '有' }]} />
            </Form.Item>
          </Col>
          <Col span={6}>
            <Form.Item name="hidden_has_large" label="含大盲盒">
              <Select options={[{ value: 0, label: '无' }, { value: 1, label: '有' }]} />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="notes" label="备注">
          <Input.TextArea rows={2} />
        </Form.Item>
      </Form>
    </Modal>
  )
}
