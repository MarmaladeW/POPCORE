import { useState, useEffect, useCallback } from 'react'
import {
  Table, Input, Select, Button, Space, Tag, Image, Popconfirm,
  message, Typography, Row, Col, Tooltip,
} from 'antd'
import { PlusOutlined, ExportOutlined, DeleteOutlined, SearchOutlined, EditOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'
import { useAppStore } from '../../store'
import RoleGuard from '../../components/RoleGuard'
import ProductModal from './ProductModal'
import HiddenImagesModal from './HiddenImagesModal'
import PasteImportModal from './PasteImportModal'

const { Search } = Input
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

export default function ProductsPage() {
  const { series, productTypes } = useAppStore()
  const [products, setProducts]   = useState<Product[]>([])
  const [loading, setLoading]     = useState(false)
  const [q, setQ]                 = useState('')
  const [filterSeries, setFilterSeries] = useState<string>('')
  const [filterType, setFilterType]     = useState<string>('')
  const [selected, setSelected]   = useState<React.Key[]>([])

  // Modals
  const [editProduct, setEditProduct]       = useState<Product | null>(null)
  const [modalOpen, setModalOpen]           = useState(false)
  const [imagesProduct, setImagesProduct]   = useState<Product | null>(null)
  const [pasteOpen, setPasteOpen]           = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    const params: Record<string, string> = { limit: '200' }
    if (q) params.q = q
    if (filterSeries) params.series = filterSeries
    if (filterType)   params.product_type = filterType
    client.get('/products/search', { params })
      .then(r => setProducts(r.data))
      .finally(() => setLoading(false))
  }, [q, filterSeries, filterType])

  useEffect(() => { load() }, [load])

  function openNew() { setEditProduct(null); setModalOpen(true) }
  function openEdit(p: Product) { setEditProduct(p); setModalOpen(true) }

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
    if (q) params.set('q', q)
    window.location.href = `/api/products/export?${params}`
  }

  const columns: ColumnsType<Product> = [
    {
      title: 'SKU',
      dataIndex: 'sku',
      width: 110,
      render: (v) => <Text code>{v}</Text>,
    },
    {
      title: '记账名',
      dataIndex: 'jizhanming',
      width: 140,
      render: (v, r) => (
        <a onClick={() => openEdit(r)} style={{ cursor: 'pointer' }}>{v || '-'}</a>
      ),
    },
    {
      title: '产品名称',
      dataIndex: 'name_cn_en',
      ellipsis: true,
    },
    {
      title: '系列',
      dataIndex: 'ip_series',
      width: 120,
      render: (v) => v ? <Tag color="blue">{v}</Tag> : '-',
    },
    {
      title: '类型',
      dataIndex: 'product_type',
      width: 80,
    },
    {
      title: '单价',
      dataIndex: 'price',
      width: 80,
      align: 'right',
      render: (v) => v != null ? `C$${v}` : '-',
    },
    {
      title: '盲盒',
      dataIndex: 'hidden_count',
      width: 60,
      align: 'center',
      render: (v, r) => {
        if (!v || v === '0') return '-'
        const badges = []
        if (r.hidden_has_small) badges.push(<Tag key="s" color="gold" style={{ fontSize: 10 }}>小</Tag>)
        if (r.hidden_has_large) badges.push(<Tag key="l" color="orange" style={{ fontSize: 10 }}>大</Tag>)
        return <span>{v} {badges}</span>
      },
    },
    {
      title: '图',
      key: 'img',
      width: 50,
      align: 'center',
      render: (_, r) => (
        <Button size="small" type="link" onClick={() => setImagesProduct(r)}>
          图
        </Button>
      ),
    },
    {
      title: '',
      key: 'edit',
      width: 50,
      align: 'center',
      render: (_, r) => (
        <Button size="small" type="link" icon={<EditOutlined />} onClick={() => openEdit(r)} />
      ),
    },
  ]

  return (
    <div>
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col flex="auto">
          <Search
            placeholder="搜索 SKU / 记账名 / 产品名称"
            allowClear
            enterButton={<SearchOutlined />}
            onSearch={setQ}
            onChange={e => { if (!e.target.value) setQ('') }}
          />
        </Col>
        <Col>
          <Select
            placeholder="系列"
            allowClear
            style={{ width: 140 }}
            options={series.map(s => ({ value: s, label: s }))}
            onChange={v => setFilterSeries(v ?? '')}
          />
        </Col>
        <Col>
          <Select
            placeholder="类型"
            allowClear
            style={{ width: 120 }}
            options={productTypes.map(t => ({ value: t, label: t }))}
            onChange={v => setFilterType(v ?? '')}
          />
        </Col>
        <RoleGuard minRole="manager">
          <Col>
            <Space>
              <Button icon={<PlusOutlined />} type="primary" onClick={openNew}>
                新增产品
              </Button>
              <Tooltip title="粘贴导入">
                <Button onClick={() => setPasteOpen(true)}>导入</Button>
              </Tooltip>
              <Button icon={<ExportOutlined />} onClick={handleExport}>
                导出
              </Button>
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

      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={products}
        columns={columns}
        rowSelection={{
          selectedRowKeys: selected,
          onChange: setSelected,
        }}
        pagination={{ pageSize: 60, showSizeChanger: false, showTotal: t => `共 ${t} 条` }}
        scroll={{ x: 800 }}
      />

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
