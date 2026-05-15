import { useState, useEffect, useCallback } from 'react'
import {
  Table, Button, Space, Tag, Popconfirm, message,
  AutoComplete, Input, Typography, Alert,
} from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import client from '../../api/client'
import RoleGuard from '../../components/RoleGuard'

const { Text } = Typography

interface AliasRow {
  id: number
  alias: string
  alias_norm: string
  created_by: string | null
  created_at: string
  product_id: number
  jizhanming: string
  sku: string
}

interface ProductOption {
  value: string
  label: string
  product: { id: number; jizhanming: string; sku: string }
}

export default function AliasManager() {
  const [aliases,      setAliases]      = useState<AliasRow[]>([])
  const [loading,      setLoading]      = useState(false)
  const [aliasInput,   setAliasInput]   = useState('')
  const [productOpts,  setProductOpts]  = useState<ProductOption[]>([])
  const [selectedPid,  setSelectedPid]  = useState<number | null>(null)
  const [selectedJzm,  setSelectedJzm]  = useState('')
  const [productSearch, setProductSearch] = useState('')
  const [saving,       setSaving]       = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await client.get('/products/aliases')
      setAliases(r.data)
    } catch {
      message.error('Failed to load aliases')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function searchProducts(q: string) {
    setProductSearch(q)
    setSelectedPid(null)
    setSelectedJzm('')
    if (!q) { setProductOpts([]); return }
    const r = await client.get('/products/search', { params: { q, limit: 10 } })
    setProductOpts(r.data.map((p: any) => ({
      value: String(p.id),
      label: `${p.jizhanming || p.name_cn_en || p.sku} (${p.sku})`,
      product: p,
    })))
  }

  async function handleAdd() {
    const alias = aliasInput.trim()
    if (!alias) { message.warning('请输入别名'); return }
    if (!selectedPid) { message.warning('请选择对应产品'); return }
    setSaving(true)
    try {
      await client.post('/products/aliases', { product_id: selectedPid, alias })
      message.success('Alias saved')
      setAliasInput('')
      setProductSearch('')
      setSelectedPid(null)
      setSelectedJzm('')
      setProductOpts([])
      load()
    } catch (err: any) {
      message.error(err?._serverMessage ?? 'Failed to save alias')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(row: AliasRow) {
    try {
      await client.delete(`/products/${row.product_id}/aliases/${row.id}`)
      message.success('Alias deleted')
      load()
    } catch {
      message.error('Failed to delete alias')
    }
  }

  const columns = [
    {
      title: '别名 (staff types)',
      dataIndex: 'alias',
      width: 180,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: '→ 产品记账名',
      key: 'product',
      render: (_: any, r: AliasRow) => (
        <Space size={4}>
          <span style={{ fontWeight: 500 }}>{r.jizhanming}</span>
          <Tag style={{ fontSize: 11 }}>{r.sku}</Tag>
        </Space>
      ),
    },
    {
      title: '来源',
      dataIndex: 'created_by',
      width: 100,
      render: (v: string | null) => (
        <Tag color={v === 'system_seed' ? 'blue' : 'default'} style={{ fontSize: 11 }}>
          {v === 'system_seed' ? '预置' : (v || '手动')}
        </Tag>
      ),
    },
    {
      title: '',
      key: 'del',
      width: 60,
      render: (_: any, r: AliasRow) => (
        <RoleGuard minRole="manager">
          <Popconfirm
            title={`Delete alias "${r.alias}"?`}
            onConfirm={() => handleDelete(r)}>
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </RoleGuard>
      ),
    },
  ]

  return (
    <div>
      <RoleGuard minRole="manager">
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12, borderRadius: 8 }}
          message="Product Alias Manager"
          description="Aliases map what staff type (e.g. 'smiski hipper') to the canonical jizhanming. Exact alias matches always score 100 — they take priority over fuzzy matching."
        />

        {/* Add new alias */}
        <div style={{
          background: '#f9fafb', borderRadius: 8, padding: '12px 16px', marginBottom: 16,
          display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: 10,
        }}>
          <div>
            <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 4 }}>
              Staff shorthand (alias)
            </div>
            <Input
              value={aliasInput}
              onChange={e => setAliasInput(e.target.value)}
              placeholder="e.g. smiski hipper"
              style={{ width: 180 }}
              onPressEnter={handleAdd}
            />
          </div>
          <div style={{ fontSize: 13, color: '#9ca3af', alignSelf: 'center', paddingBottom: 2 }}>→</div>
          <div>
            <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 4 }}>
              Canonical product (搜索记账名)
            </div>
            <AutoComplete
              value={selectedJzm || productSearch}
              style={{ width: 240 }}
              options={productOpts}
              onSearch={searchProducts}
              placeholder="Search product..."
              onSelect={(val: string, opt: any) => {
                setSelectedPid(opt.product.id)
                setSelectedJzm(opt.label)
                setProductSearch(opt.label)
              }}
            />
          </div>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            loading={saving}
            onClick={handleAdd}
            disabled={!aliasInput.trim() || !selectedPid}>
            Add Alias
          </Button>
        </div>

        {/* Alias table */}
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={aliases}
          columns={columns}
          pagination={{ pageSize: 20, showTotal: t => `${t} aliases` }}
        />
      </RoleGuard>
    </div>
  )
}
