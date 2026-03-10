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
  product_id:          number
  sku:                 string
  jizhanming:          string
  name_cn_en:          string
  ip_series:           string
  product_type:        string
  current_instore_dan: number
  theoretical_qty:     number
  base_check_date:     string
  is_base_abnormal:    boolean
  check_id:            number | null
  actual_qty:          number | null
  discrepancy:         number | null
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
      // Pre-fill saved actual_qty (don't override user's in-progress edits)
      setActualQty(prev => {
        const prefill: Record<number, number> = { ...prev }
        for (const item of d.items) {
          if (item.actual_qty !== null && prefill[item.product_id] === undefined) {
            prefill[item.product_id] = item.actual_qty
          }
        }
        return prefill
      })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const items = data?.items ?? []

  // A row is "done" if the user has typed a value OR it was already saved
  const allFilled = items.length > 0 && items.every(i =>
    actualQty[i.product_id] !== undefined || i.check_id !== null
  )

  function localDiscrepancy(item: CheckItem): number | null {
    const actual = actualQty[item.product_id]
    if (actual === undefined) return item.discrepancy
    return actual - item.theoretical_qty
  }

  async function handleSubmitAll() {
    const toSave = items.filter(i =>
      actualQty[i.product_id] !== undefined && i.check_id === null
    )
    if (toSave.length === 0) {
      message.warning('没有需要提交的新数据（均已保存或未填写）')
      return
    }
    setSaving(true)
    try {
      const { data: res } = await client.post('/inventory-check/submit', {
        checks: toSave.map(item => ({
          product_id: item.product_id,
          actual_qty: actualQty[item.product_id],
        })),
      })
      const { saved, conflicts } = res as { saved: number; conflicts: number[] }
      if (saved > 0) message.success(`晚盘完成，已保存 ${saved} 项`)
      if (conflicts?.length > 0) message.warning(`${conflicts.length} 项今日已提交，跳过`)
      load()
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

  const savedCount  = items.filter(i => i.check_id !== null).length
  const filledCount = items.filter(i => actualQty[i.product_id] !== undefined).length
  const diffCount   = items.filter(i => {
    const d = localDiscrepancy(i)
    return d !== null && d !== 0
  }).length
  const allSaved = savedCount === items.length && items.length > 0

  const columns: ColumnsType<CheckItem> = [
    {
      title: '产品',
      key: 'product',
      render: (_, r) => (
        <div>
          <div style={{ fontWeight: 500 }}>{r.jizhanming || r.name_cn_en}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>{r.sku} · {r.ip_series}</Text>
          {r.is_base_abnormal && (
            <div style={{
              color: '#F59E0B', fontSize: 11,
              display: 'flex', alignItems: 'center', gap: 3, marginTop: 2,
            }}>
              <WarningOutlined />
              <span>基准日期：{r.base_check_date}，请核查</span>
            </div>
          )}
        </div>
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
      width: 120,
      render: (_, r) => {
        // Show Tag (locked) if already saved AND user hasn't started editing again
        if (r.check_id !== null && actualQty[r.product_id] === undefined) {
          return <Tag color="success">{r.actual_qty} 端</Tag>
        }
        return (
          <InputNumber
            min={0} max={9999} precision={0}
            value={actualQty[r.product_id] ?? (r.actual_qty ?? undefined)}
            placeholder="输入"
            onChange={v => setActualQty(prev => ({ ...prev, [r.product_id]: v ?? 0 }))}
            style={{ width: 90 }}
            size="small"
          />
        )
      },
    },
    {
      title: '差异',
      key: 'discrepancy',
      width: 80,
      render: (_, r) => {
        const diff = localDiscrepancy(r)
        if (diff === null) return <Text type="secondary">—</Text>
        if (diff === 0)    return <Tag color="success">正常</Tag>
        // Non-zero: red bold
        return (
          <span style={{ color: '#EF4444', fontWeight: 700 }}>
            {diff > 0 ? `+${diff}` : diff}
          </span>
        )
      },
    },
  ]

  if (loading) {
    return <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
  }

  return (
    <div style={{ paddingTop: 16 }}>
      <Alert
        type="info"
        message="晚盘核查"
        description="核实畅销品实际门店库存。理论库存 = 上次盘点实际值 − 期间销售 + 期间补货入店。"
        style={{ marginBottom: 12 }}
        showIcon
      />

      {allSaved && (
        <Alert type="success" message="今日晚盘已全部提交" style={{ marginBottom: 12 }} showIcon />
      )}

      {items.length === 0 ? (
        <Empty
          description={
            <span>
              暂无畅销品。请在 Products 页面为需要晚盘的商品开启
              <Tag style={{ margin: '0 4px' }}>is_bestseller</Tag>。
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
              {savedCount  > 0 && <Tag color="success">已保存 {savedCount}</Tag>}
              {filledCount > 0 && <Tag color="processing">已填写 {filledCount}</Tag>}
              {diffCount   > 0 && <Tag color="error">有差异 {diffCount}</Tag>}
            </Space>

            <Button
              type="primary" icon={<AuditOutlined />}
              loading={saving}
              onClick={handleSubmitAll}
              disabled={!allFilled || saving}
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
            rowClassName={r => {
              const diff = localDiscrepancy(r)
              return diff !== null && diff !== 0 ? 'row-discrepancy' : ''
            }}
          />
        </>
      )}
    </div>
  )
}
