import { useState, useEffect, useCallback } from 'react'
import {
  Table, InputNumber, Button, Tag, message,
  Typography, Alert, Space, Empty, Spin, Grid,
} from 'antd'
import { AuditOutlined, WarningOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'

const { Text } = Typography
const { useBreakpoint } = Grid

interface CheckItem {
  product_id:       number
  sku:              string
  jizhanming:       string
  name_cn_en:       string
  ip_series:        string
  product_type:     string
  current_instore_dan: number
  theoretical_qty:  number
  base_check_date:  string
  is_base_abnormal: boolean
  check_id:         number | null
  actual_qty:       number | null
  discrepancy:      number | null
}

interface TodayData {
  date:  string
  items: CheckItem[]
}

export default function EveningCheckStep() {
  const screens  = useBreakpoint()
  const isMobile = !screens.md

  const [data,      setData]      = useState<TodayData | null>(null)
  const [loading,   setLoading]   = useState(true)
  const [actualQty, setActualQty] = useState<Record<number, number>>({})
  const [saving,    setSaving]    = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data: d } = await client.get<TodayData>('/inventory-check/today')
      setData(d)
      // Pre-fill saved actual_qty
      const prefill: Record<number, number> = {}
      for (const item of d.items) {
        if (item.actual_qty !== null) prefill[item.product_id] = item.actual_qty
      }
      setActualQty(prefill)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  function localDiscrepancy(item: CheckItem): number | null {
    const actual = actualQty[item.product_id]
    if (actual === undefined) return item.discrepancy
    return actual - item.theoretical_qty
  }

  async function handleSubmitAll() {
    if (!data) return
    const toSave = data.items.filter(i => actualQty[i.product_id] !== undefined)
    if (toSave.length === 0) { message.warning('请先填写实际库存数量'); return }

    setSaving(true)
    try {
      const { data: res } = await client.post('/inventory-check/submit', {
        checks: toSave.map(item => ({
          product_id: item.product_id,
          actual_qty: actualQty[item.product_id],
        })),
      })
      if (res.conflicts?.length > 0 && res.saved === 0) {
        message.error('今日已提交晚盘，如需修改请联系管理员')
      } else {
        message.success(`晚盘完成，已保存 ${res.saved} 项${res.conflicts?.length ? `，${res.conflicts.length} 项冲突跳过` : ''}`)
        load()
      }
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response?.status
      if (status === 409) {
        message.error('今日已提交晚盘，如需修改请联系管理员')
      } else {
        message.error('提交失败')
      }
    } finally {
      setSaving(false)
    }
  }

  const hasAbnormal = data?.items.some(i => i.is_base_abnormal) ?? false

  const columns: ColumnsType<CheckItem> = [
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
      title: '基准日期',
      key: 'base',
      width: 130,
      render: (_, r) => (
        <Space size={4}>
          <Text style={{ fontSize: 12 }}>{r.base_check_date}</Text>
          {r.is_base_abnormal && (
            <WarningOutlined style={{ color: '#F59E0B', fontSize: 13 }} title="基准非昨日" />
          )}
        </Space>
      ),
    },
    {
      title: '理论库存',
      dataIndex: 'theoretical_qty',
      width: 90,
      render: v => <Tag color="blue">{v} 端</Tag>,
    },
    {
      title: '实际库存',
      key: 'actual_qty',
      width: 110,
      render: (_, r) => (
        r.check_id !== null && actualQty[r.product_id] === undefined
          ? <Tag color="success">{r.actual_qty} 端</Tag>
          : (
            <InputNumber
              min={0} max={9999}
              value={actualQty[r.product_id] ?? (r.actual_qty ?? undefined)}
              placeholder="输入"
              onChange={v => setActualQty(prev => ({ ...prev, [r.product_id]: v ?? 0 }))}
              style={{ width: 85 }}
              size="small"
            />
          )
      ),
    },
    {
      title: '差异',
      key: 'discrepancy',
      width: 80,
      render: (_, r) => {
        const diff = localDiscrepancy(r)
        if (diff === null) return '—'
        if (diff === 0) return <Tag color="success">正常</Tag>
        if (diff > 0)   return <Tag color="blue">+{diff}</Tag>
        return <Tag color="error">{diff}</Tag>
      },
    },
  ]

  if (loading) {
    return <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
  }

  const items = data?.items ?? []
  const filledCount = items.filter(i => actualQty[i.product_id] !== undefined).length
  const savedCount  = items.filter(i => i.check_id !== null).length
  const diffCount   = items.filter(i => {
    const diff = localDiscrepancy(i)
    return diff !== null && diff !== 0
  }).length

  return (
    <div style={{ paddingTop: 16 }}>
      <Alert
        type="info"
        message="晚盘核查"
        description="核实参与晚盘畅销品的实际门店库存。理论库存 = 上次盘点实际值 - 销售量 + 补货入店量。"
        style={{ marginBottom: 12 }}
        showIcon
      />

      {hasAbnormal && (
        <Alert
          type="warning"
          message="部分商品基准日期非昨日，理论值可能不准确，请核查后提交。"
          style={{ marginBottom: 12 }}
          showIcon
        />
      )}

      {items.length === 0 ? (
        <Empty
          description={
            <span>
              暂无畅销品。请在 Products 页面，将需要晚盘的产品 PATCH
              <Tag style={{ margin: '0 4px' }}>is_bestseller=true</Tag>。
            </span>
          }
          style={{ padding: 40 }}
        />
      ) : (
        <>
          <div style={{
            display: 'flex', gap: 12, marginBottom: 16,
            flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between',
          }}>
            <Space wrap>
              <Tag>共 {items.length} 项</Tag>
              <Tag color="success">已保存 {savedCount}</Tag>
              {filledCount > 0 && <Tag color="processing">已填写 {filledCount}</Tag>}
              {diffCount   > 0 && <Tag color="error">有差异 {diffCount}</Tag>}
            </Space>

            <Button
              type="primary" icon={<AuditOutlined />}
              loading={saving} onClick={handleSubmitAll}
              disabled={filledCount === 0}
              size={isMobile ? 'middle' : 'large'}
            >
              提交晚盘
            </Button>
          </div>

          <Table
            size="small"
            columns={columns}
            dataSource={items}
            rowKey="product_id"
            pagination={false}
            scroll={{ x: true }}
          />
        </>
      )}
    </div>
  )
}
