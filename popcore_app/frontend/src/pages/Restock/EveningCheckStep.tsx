import { useState, useEffect, useCallback } from 'react'
import {
  Table, InputNumber, Button, Tag, message,
  Typography, Alert, Space, Empty, Spin, DatePicker, Grid,
} from 'antd'
import { AuditOutlined, SaveOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs, { type Dayjs } from 'dayjs'
import client from '../../api/client'

const { Text } = Typography
const { useBreakpoint } = Grid

interface CheckItem {
  product_id:     number
  sku:            string
  jizhanming:     string
  name_cn_en:     string
  ip_series:      string
  product_type:   string
  theoretical_qty: number
  actual_qty:     number | null
  discrepancy:    number | null
  base_check_date: string | null
  check_id:       number | null
}

interface TodayData {
  date:  string
  items: CheckItem[]
}

export default function EveningCheckStep() {
  const screens  = useBreakpoint()
  const isMobile = !screens.md

  const [data,         setData]         = useState<TodayData | null>(null)
  const [loading,      setLoading]      = useState(true)
  const [actualQty,    setActualQty]    = useState<Record<number, number>>({})
  const [baseDate,     setBaseDate]     = useState<Dayjs>(dayjs())
  const [savingSet,    setSavingSet]    = useState<Set<number>>(new Set())
  const [savingAll,    setSavingAll]    = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data: d } = await client.get<TodayData>('/inventory_checks/today')
      setData(d)
      // Pre-fill any already-saved actual_qty
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

  function getDiscrepancy(item: CheckItem): number | null {
    const actual = actualQty[item.product_id]
    if (actual === undefined) return item.discrepancy
    return actual - item.theoretical_qty
  }

  async function saveOne(item: CheckItem) {
    const actual = actualQty[item.product_id]
    if (actual === undefined) { message.warning('请先输入实际数量'); return }
    setSavingSet(prev => new Set(prev).add(item.product_id))
    try {
      await client.post('/inventory_checks', {
        product_id:      item.product_id,
        actual_qty:      actual,
        base_check_date: baseDate.format('YYYY-MM-DD'),
      })
      message.success(`${item.jizhanming || item.name_cn_en} 已保存`)
      load()
    } catch {
      message.error('保存失败')
    } finally {
      setSavingSet(prev => { const s = new Set(prev); s.delete(item.product_id); return s })
    }
  }

  async function saveAll() {
    if (!data) return
    const toSave = data.items.filter(i => actualQty[i.product_id] !== undefined)
    if (toSave.length === 0) { message.warning('请先填写实际库存数量'); return }
    setSavingAll(true)
    try {
      await Promise.all(toSave.map(item =>
        client.post('/inventory_checks', {
          product_id:      item.product_id,
          actual_qty:      actualQty[item.product_id],
          base_check_date: baseDate.format('YYYY-MM-DD'),
        })
      ))
      message.success(`晚盘完成，已保存 ${toSave.length} 项`)
      load()
    } catch {
      message.error('部分保存失败')
    } finally {
      setSavingAll(false)
    }
  }

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
        <InputNumber
          min={0} max={9999}
          value={actualQty[r.product_id] ?? undefined}
          placeholder="输入"
          onChange={v => setActualQty(prev => ({
            ...prev,
            [r.product_id]: v ?? 0,
          }))}
          style={{ width: 85 }}
          size="small"
        />
      ),
    },
    {
      title: '差异',
      key: 'discrepancy',
      width: 80,
      render: (_, r) => {
        const diff = getDiscrepancy(r)
        if (diff === null) return '—'
        if (diff === 0) return <Tag color="success">正常</Tag>
        if (diff > 0)   return <Tag color="blue">+{diff}</Tag>
        return <Tag color="error">{diff}</Tag>
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_, r) => (
        <Button
          size="small" type="text" icon={<SaveOutlined />}
          loading={savingSet.has(r.product_id)}
          onClick={() => saveOne(r)}
          disabled={actualQty[r.product_id] === undefined}
        >
          保存
        </Button>
      ),
    },
  ]

  if (loading) {
    return <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
  }

  const items = data?.items ?? []
  const filledCount  = items.filter(i => actualQty[i.product_id] !== undefined).length
  const savedCount   = items.filter(i => i.check_id !== null).length
  const normalCount  = items.filter(i => {
    const diff = getDiscrepancy(i)
    return diff !== null && diff === 0
  }).length
  const diffCount = items.filter(i => {
    const diff = getDiscrepancy(i)
    return diff !== null && diff !== 0
  }).length

  return (
    <div style={{ paddingTop: 16 }}>
      <Alert
        type="info"
        message="晚盘核查"
        description="核实畅销品（标记为「参与晚盘」）的实际门店库存，与系统理论值对比。"
        style={{ marginBottom: 16 }}
        showIcon
      />

      {items.length === 0 ? (
        <Empty
          description={
            <span>
              暂无畅销品。请在「Products」页面，将需要参与晚盘的产品标记为
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
              <Tag color="success">已保存 {savedCount}</Tag>
              {filledCount > 0 && <Tag color="processing">已填写 {filledCount}</Tag>}
              {normalCount > 0 && <Tag color="success">正常 {normalCount}</Tag>}
              {diffCount   > 0 && <Tag color="error">有差异 {diffCount}</Tag>}
            </Space>

            <Space>
              <DatePicker
                value={baseDate}
                onChange={v => v && setBaseDate(v)}
                placeholder="盘点基准日期"
                allowClear={false}
                size="small"
              />
              <Button
                type="primary" icon={<AuditOutlined />}
                loading={savingAll} onClick={saveAll}
                disabled={filledCount === 0}
                size={isMobile ? 'middle' : 'large'}
              >
                提交晚盘
              </Button>
            </Space>
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
