import { useState, useRef, useCallback, useEffect } from 'react'
import {
  Input, Button, InputNumber, Table, Popconfirm, Modal,
  message, Space, Typography, Empty, Tag, Grid, Alert,
} from 'antd'
import { PlusOutlined, DeleteOutlined, SendOutlined, LockOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'
import type { RestockSession, RestockItem } from './index'

const { Text } = Typography
const { useBreakpoint } = Grid

interface SearchProduct {
  id:           number
  sku:          string
  jizhanming:   string
  name_cn_en:   string
  ip_series:    string
  product_type: string
  is_bestseller: number
  upstairs_dan: number   // warehouse stock (via include_stock=1)
}

interface Props {
  session:   RestockSession
  onRefresh: () => void
}

export default function RequestStep({ session, onRefresh }: Props) {
  const screens  = useBreakpoint()
  const isMobile = !screens.md

  const [searchValue,   setSearchValue]   = useState('')
  const [searchResults, setSearchResults] = useState<SearchProduct[]>([])
  const [searching,     setSearching]     = useState(false)
  const [submitting,    setSubmitting]    = useState(false)
  // Local edits to requested_qty before blur-save
  const [editQtyMap,    setEditQtyMap]    = useState<Record<number, number>>({})
  // qty for new items being added from search results
  const [addQtyMap,     setAddQtyMap]     = useState<Record<number, number>>({})

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [])

  const isReadOnly = session.status !== 'pending'
  const addedIds   = new Set(session.items.map(i => i.product_id))

  // ── Search (debounced 300ms) ───────────────────────────────────────────────

  const doSearch = useCallback(async (val: string) => {
    if (!val.trim()) { setSearchResults([]); return }
    setSearching(true)
    try {
      const { data } = await client.get<SearchProduct[]>('/products/search', {
        params: { q: val, include_stock: 1, limit: 20 },
      })
      setSearchResults(data)
    } finally {
      setSearching(false)
    }
  }, [])

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const val = e.target.value
    setSearchValue(val)
    if (!val.trim()) { setSearchResults([]); return }
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => doSearch(val), 300)
  }

  function handleClear() {
    setSearchValue('')
    setSearchResults([])
    if (debounceRef.current) clearTimeout(debounceRef.current)
  }

  // ── Add item ──────────────────────────────────────────────────────────────

  async function handleAdd(product: SearchProduct) {
    const qty = addQtyMap[product.id] ?? 1
    if (qty < 1) { message.warning('请输入有效数量'); return }
    try {
      await client.post('/restock/items', {
        session_id:    session.id,
        product_id:    product.id,
        requested_qty: qty,
      })
      message.success(`已添加：${product.jizhanming || product.name_cn_en}`)
      onRefresh()
    } catch {
      message.error('添加失败')
    }
  }

  // ── Inline qty edit (blur-save) ───────────────────────────────────────────

  async function handleQtyBlur(item: RestockItem) {
    const newQty = editQtyMap[item.id]
    if (newQty === undefined || newQty === item.requested_qty) return
    if (newQty < 1) {
      setEditQtyMap(prev => { const m = { ...prev }; delete m[item.id]; return m })
      return
    }
    try {
      await client.post('/restock/items', {
        session_id:    session.id,
        product_id:    item.product_id,
        requested_qty: newQty,
      })
      setEditQtyMap(prev => { const m = { ...prev }; delete m[item.id]; return m })
      onRefresh()
    } catch {
      message.error('更新数量失败')
    }
  }

  // ── Delete ────────────────────────────────────────────────────────────────

  async function handleDelete(item: RestockItem) {
    try {
      await client.delete(`/restock/items/${item.id}`)
      message.success('已删除')
      onRefresh()
    } catch {
      message.error('删除失败')
    }
  }

  // ── Submit (Modal confirm) ────────────────────────────────────────────────

  function handleSubmitClick() {
    if (session.items.length === 0) { message.warning('请至少添加一个补货产品'); return }
    Modal.confirm({
      title:   '确认提交补货申请？',
      content: `共 ${session.items.length} 件产品，提交后将锁定清单，仓库开始拣货。`,
      okText:  '确认提交',
      cancelText: '取消',
      onOk:    doSubmit,
    })
  }

  async function doSubmit() {
    setSubmitting(true)
    try {
      await client.post(`/restock/session/${session.id}/submit`)
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { error?: string }; status?: number }; message?: string; code?: string }
      const serverMsg = axiosErr?.response?.data?.error
      const status = axiosErr?.response?.status
      if (serverMsg) {
        message.error(`提交失败：${serverMsg}`)
      } else if (status) {
        message.error(`提交失败：HTTP ${status}`)
      } else {
        message.error(`提交失败：${axiosErr?.code ?? axiosErr?.message ?? '未知错误'}`)
      }
      setSubmitting(false)
      return
    }
    setSubmitting(false)
    message.success('补货申请已提交，仓库快照已锁定，进入拣货阶段')
    onRefresh()
  }

  // ── Read-only banner ──────────────────────────────────────────────────────

  function statusBanner() {
    if (session.status === 'submitted' || session.status === 'picking') {
      return (
        <Alert
          type="info" showIcon icon={<LockOutlined />}
          message="补货申请已提交，仓库拣货进行中"
          style={{ marginBottom: 16 }}
        />
      )
    }
    if (session.status === 'completed') {
      return (
        <Alert
          type="success" showIcon
          message={`本日补货已完成${session.completed_at ? `（${session.completed_at.slice(0, 16)}）` : ''}`}
          style={{ marginBottom: 16 }}
        />
      )
    }
    return null
  }

  // ── Columns ───────────────────────────────────────────────────────────────

  const searchCols: ColumnsType<SearchProduct> = [
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
      title: '仓库库存',
      dataIndex: 'upstairs_dan',
      width: 90,
      render: v => <Tag color={v > 0 ? 'blue' : 'red'}>{v} 端</Tag>,
    },
    {
      title: '数量',
      key: 'qty',
      width: 90,
      render: (_, r) => (
        <InputNumber
          min={1} max={999} precision={0}
          value={addQtyMap[r.id] ?? 1}
          onChange={v => setAddQtyMap(prev => ({ ...prev, [r.id]: v ?? 1 }))}
          style={{ width: 70 }}
          size="small"
        />
      ),
    },
    {
      title: '',
      key: 'add',
      width: 80,
      render: (_, r) => (
        addedIds.has(r.id)
          ? <Button size="small" disabled>已添加</Button>
          : (
            <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => handleAdd(r)}>
              添加
            </Button>
          )
      ),
    },
  ]

  const itemCols: ColumnsType<RestockItem> = [
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
      title: '申请数量',
      key: 'requested_qty',
      width: 120,
      render: (_, r) => {
        if (isReadOnly) return <Tag>{r.requested_qty} 端</Tag>
        return (
          <InputNumber
            min={1} max={999} precision={0}
            value={editQtyMap[r.id] ?? r.requested_qty}
            onChange={v => setEditQtyMap(prev => ({ ...prev, [r.id]: v ?? r.requested_qty }))}
            onBlur={() => handleQtyBlur(r)}
            onPressEnter={() => handleQtyBlur(r)}
            style={{ width: 90 }}
            size="small"
          />
        )
      },
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

  return (
    <div style={{ paddingTop: 16 }}>
      {/* Status banner for read-only states */}
      {isReadOnly && statusBanner()}

      {/* Search box (edit mode only) */}
      {!isReadOnly && (
        <div style={{ marginBottom: 16 }}>
          <Input
            placeholder="搜索产品（记账名、SKU、系列…）"
            value={searchValue}
            onChange={handleInputChange}
            allowClear
            onClear={handleClear}
            suffix={searching ? undefined : undefined}
            style={{ maxWidth: 480 }}
          />
          {searching && <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>搜索中…</Text>}
          {searchResults.length > 0 && (
            <Table
              size="small"
              columns={searchCols}
              dataSource={searchResults}
              rowKey="id"
              pagination={false}
              style={{ marginTop: 8, maxWidth: 640 }}
              scroll={{ x: true }}
            />
          )}
        </div>
      )}

      {/* Current items list */}
      <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text strong>补货清单 ({session.items.length} 项)</Text>
        {!isReadOnly && session.items.length > 0 && isMobile && (
          <Button
            type="primary" icon={<SendOutlined />}
            loading={submitting} onClick={handleSubmitClick}
          >
            提交
          </Button>
        )}
      </div>

      {session.items.length === 0
        ? <Empty description={isReadOnly ? '暂无补货项目' : '搜索并添加需要补货的产品'} style={{ padding: 40 }} />
        : (
          <Table
            size="small"
            columns={itemCols}
            dataSource={session.items}
            rowKey="id"
            pagination={false}
            scroll={{ x: true }}
          />
        )
      }

      {/* Bottom submit (desktop) */}
      {!isReadOnly && session.items.length > 0 && !isMobile && (
        <div style={{ marginTop: 16, textAlign: 'right' }}>
          <Space>
            <Text type="secondary" style={{ fontSize: 12 }}>提交后清单将锁定，仓库开始拣货</Text>
            <Button
              type="primary" size="large" icon={<SendOutlined />}
              loading={submitting} onClick={handleSubmitClick}
            >
              确认提交，生成仓库清单
            </Button>
          </Space>
        </div>
      )}
    </div>
  )
}
