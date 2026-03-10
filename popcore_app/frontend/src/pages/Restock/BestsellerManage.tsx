import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Table, Input, Switch, Tag, message,
  Typography, Space, Grid, Spin,
} from 'antd'
import { StarOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'

const { Text } = Typography
const { useBreakpoint } = Grid

interface Product {
  id:            number
  sku:           string
  jizhanming:    string
  name_cn_en:    string
  ip_series:     string
  product_type:  string
  is_bestseller: number
  instore_dan?:  number
  upstairs_dan?: number
}

export default function BestsellerManage() {
  const screens  = useBreakpoint()
  const isMobile = !screens.md

  const [products,      setProducts]      = useState<Product[]>([])
  const [loading,       setLoading]       = useState(false)
  const [searchValue,   setSearchValue]   = useState('')
  const [onlyBest,      setOnlyBest]      = useState(false)
  const [togglingSet,   setTogglingSet]   = useState<Set<number>>(new Set())

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const fetchBestsellers = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await client.get<Product[]>('/bestsellers')
      setProducts(data)
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchSearch = useCallback(async (q: string) => {
    setLoading(true)
    try {
      const { data } = await client.get<Product[]>('/products/search', {
        params: { q, include_stock: 1, limit: 60 },
      })
      setProducts(data)
    } finally {
      setLoading(false)
    }
  }, [])

  // Load on mount and when onlyBest toggles
  useEffect(() => {
    if (onlyBest) {
      fetchBestsellers()
    } else {
      fetchSearch(searchValue)
    }
  }, [onlyBest]) // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced search
  function handleSearchChange(e: React.ChangeEvent<HTMLInputElement>) {
    const val = e.target.value
    setSearchValue(val)
    if (onlyBest) return  // onlyBest mode ignores search
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => fetchSearch(val), 300)
  }

  function handleClear() {
    setSearchValue('')
    if (!onlyBest) fetchSearch('')
  }

  // Toggle bestseller
  async function handleToggle(product: Product, checked: boolean) {
    setTogglingSet(prev => new Set(prev).add(product.id))
    // Optimistic update
    setProducts(prev => prev.map(p =>
      p.id === product.id ? { ...p, is_bestseller: checked ? 1 : 0 } : p
    ))
    try {
      await client.patch(`/products/${product.id}/bestseller`, { is_bestseller: checked })
    } catch {
      // Rollback on failure
      setProducts(prev => prev.map(p =>
        p.id === product.id ? { ...p, is_bestseller: checked ? 0 : 1 } : p
      ))
      message.error('保存失败，请重试')
    } finally {
      setTogglingSet(prev => { const s = new Set(prev); s.delete(product.id); return s })
    }
  }

  const columns: ColumnsType<Product> = [
    {
      title: '产品',
      key: 'product',
      render: (_, r) => (
        <div>
          <div style={{ fontWeight: 500 }}>
            {r.jizhanming || r.name_cn_en}
            {r.is_bestseller === 1 && (
              <StarOutlined style={{ color: '#F59E0B', marginLeft: 6, fontSize: 13 }} />
            )}
          </div>
          <Text type="secondary" style={{ fontSize: 12 }}>{r.sku}</Text>
        </div>
      ),
    },
    {
      title: '系列',
      dataIndex: 'ip_series',
      width: 120,
      render: v => v || '—',
    },
    ...(!isMobile ? [{
      title: '门店库存',
      key: 'instore',
      width: 100,
      render: (_: unknown, r: Product) => {
        const qty = r.instore_dan ?? 0
        return <Tag color={qty > 0 ? 'blue' : 'default'}>{qty} 端</Tag>
      },
    }] : []),
    {
      title: '参与晚盘',
      key: 'bestseller',
      width: 100,
      render: (_, r) => (
        <Switch
          checked={r.is_bestseller === 1}
          loading={togglingSet.has(r.id)}
          onChange={checked => handleToggle(r, checked)}
          checkedChildren="是"
          unCheckedChildren="否"
        />
      ),
    },
  ]

  return (
    <div style={{ paddingTop: 16 }}>
      <div style={{
        display: 'flex', gap: 12, marginBottom: 16,
        flexWrap: 'wrap', alignItems: 'center',
      }}>
        <Input
          placeholder="搜索产品（记账名、SKU…）"
          value={searchValue}
          onChange={handleSearchChange}
          allowClear
          onClear={handleClear}
          disabled={onlyBest}
          style={{ maxWidth: 320 }}
        />
        <Space>
          <Text style={{ fontSize: 13 }}>仅看畅销品</Text>
          <Switch
            checked={onlyBest}
            onChange={v => setOnlyBest(v)}
            checkedChildren="开"
            unCheckedChildren="关"
          />
        </Space>
        {loading && <Spin size="small" />}
      </div>

      <Table
        size="small"
        columns={columns}
        dataSource={products}
        rowKey="id"
        pagination={{ pageSize: 20, showSizeChanger: false }}
        scroll={{ x: true }}
        loading={loading}
      />
    </div>
  )
}
