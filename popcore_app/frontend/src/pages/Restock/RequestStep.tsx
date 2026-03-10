import { useState, useRef } from 'react'
import {
  Input, Button, InputNumber, Table, Popconfirm,
  message, Space, Typography, Empty, Tag, Grid,
} from 'antd'
import { PlusOutlined, DeleteOutlined, SendOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'
import type { RestockSession, RestockItem } from './index'

const { Search } = Input
const { Text } = Typography
const { useBreakpoint } = Grid

interface SearchProduct {
  id: number
  sku: string
  jizhanming: string
  name_cn_en: string
  ip_series: string
  product_type: string
}

interface Props {
  session: RestockSession
  onRefresh: () => void
}

export default function RequestStep({ session, onRefresh }: Props) {
  const screens  = useBreakpoint()
  const isMobile = !screens.md

  const [searchResults, setSearchResults] = useState<SearchProduct[]>([])
  const [searching,     setSearching]     = useState(false)
  const [submitting,    setSubmitting]    = useState(false)
  const [qtyMap,        setQtyMap]        = useState<Record<number, number>>({})
  const searchRef = useRef<string>('')

  const isReadOnly = session.status !== 'pending'

  async function handleSearch(value: string) {
    searchRef.current = value
    if (!value.trim()) { setSearchResults([]); return }
    setSearching(true)
    try {
      const { data } = await client.get<SearchProduct[]>('/products/search', {
        params: { q: value, limit: 20 },
      })
      setSearchResults(data)
    } finally {
      setSearching(false)
    }
  }

  async function handleAdd(product: SearchProduct) {
    const qty = qtyMap[product.id] ?? 1
    if (qty <= 0) { message.warning('请输入有效数量'); return }
    try {
      await client.post(`/restock/sessions/${session.id}/items`, {
        product_id:    product.id,
        requested_qty: qty,
      })
      message.success(`已添加：${product.jizhanming || product.name_cn_en}`)
      onRefresh()
    } catch {
      message.error('添加失败')
    }
  }

  async function handleDelete(item: RestockItem) {
    try {
      await client.delete(`/restock/sessions/${session.id}/items/${item.id}`)
      message.success('已删除')
      onRefresh()
    } catch {
      message.error('删除失败')
    }
  }

  async function handleSubmit() {
    if (session.items.length === 0) { message.warning('请至少添加一个补货产品'); return }
    setSubmitting(true)
    try {
      await client.post(`/restock/sessions/${session.id}/submit`)
      message.success('补货申请已提交，进入拣货阶段')
      onRefresh()
    } catch {
      message.error('提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  const itemColumns: ColumnsType<RestockItem> = [
    {
      title: '产品',
      key: 'product',
      render: (_, r) => (
        <div>
          <div style={{ fontWeight: 500 }}>{r.jizhanming || r.name_cn_en}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>{r.sku}</Text>
        </div>
      ),
    },
    {
      title: '仓库库存',
      dataIndex: 'warehouse_stock_snapshot',
      width: 90,
      render: v => <Tag color={v > 0 ? 'blue' : 'red'}>{v} 端</Tag>,
    },
    {
      title: '申请数量',
      dataIndex: 'requested_qty',
      width: 90,
      render: v => <Tag>{v} 端</Tag>,
    },
    ...(!isReadOnly ? [{
      title: '',
      key: 'action',
      width: 50,
      render: (_: unknown, r: RestockItem) => (
        <Popconfirm title="确认删除？" onConfirm={() => handleDelete(r)} okText="删除" cancelText="取消">
          <Button type="text" danger icon={<DeleteOutlined />} size="small" />
        </Popconfirm>
      ),
    }] : []),
  ]

  const searchColumns: ColumnsType<SearchProduct> = [
    {
      title: '产品',
      key: 'product',
      render: (_, r) => (
        <div>
          <div style={{ fontWeight: 500 }}>{r.jizhanming || r.name_cn_en}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>{r.sku} · {r.ip_series}</Text>
        </div>
      ),
    },
    {
      title: '数量',
      key: 'qty',
      width: 100,
      render: (_, r) => (
        <InputNumber
          min={1} max={999}
          value={qtyMap[r.id] ?? 1}
          onChange={v => setQtyMap(prev => ({ ...prev, [r.id]: v ?? 1 }))}
          style={{ width: 70 }}
          size="small"
        />
      ),
    },
    {
      title: '',
      key: 'add',
      width: 70,
      render: (_, r) => (
        <Button
          type="primary" size="small" icon={<PlusOutlined />}
          onClick={() => handleAdd(r)}
        >
          添加
        </Button>
      ),
    },
  ]

  return (
    <div style={{ paddingTop: 16 }}>
      {!isReadOnly && (
        <div style={{ marginBottom: 16 }}>
          <Search
            placeholder="搜索产品（记账名、SKU、系列…）"
            onSearch={handleSearch}
            onChange={e => { if (!e.target.value) setSearchResults([]) }}
            loading={searching}
            allowClear
            style={{ maxWidth: 480 }}
          />
          {searchResults.length > 0 && (
            <Table
              size="small"
              columns={searchColumns}
              dataSource={searchResults}
              rowKey="id"
              pagination={false}
              style={{ marginTop: 8, maxWidth: 600 }}
              scroll={{ x: true }}
            />
          )}
        </div>
      )}

      <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text strong>补货清单 ({session.items.length} 项)</Text>
        {!isReadOnly && session.items.length > 0 && (
          <Button
            type="primary" icon={<SendOutlined />}
            loading={submitting} onClick={handleSubmit}
          >
            {isMobile ? '提交' : '提交补货申请'}
          </Button>
        )}
      </div>

      {session.items.length === 0 ? (
        <Empty description={isReadOnly ? '暂无补货项目' : '搜索并添加需要补货的产品'} style={{ padding: 40 }} />
      ) : (
        <Table
          size="small"
          columns={itemColumns}
          dataSource={session.items}
          rowKey="id"
          pagination={false}
          scroll={{ x: true }}
        />
      )}

      {!isReadOnly && session.items.length > 0 && (
        <div style={{ marginTop: 16, textAlign: 'right' }}>
          <Button
            type="primary" size="large" icon={<SendOutlined />}
            loading={submitting} onClick={handleSubmit}
          >
            提交补货申请
          </Button>
        </div>
      )}
    </div>
  )
}
