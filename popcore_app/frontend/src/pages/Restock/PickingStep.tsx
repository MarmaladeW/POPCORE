import { useRef, useState } from 'react'
import {
  Table, InputNumber, Button, Tag, message, Modal,
  Space, Typography, Grid, Alert, Progress, Tooltip,
} from 'antd'
import { CheckOutlined, CloseOutlined, ShopOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'
import type { RestockSession, RestockItem } from './index'
import { withUnit } from './units'

const { Text } = Typography
const { useBreakpoint } = Grid

interface Props {
  session:   RestockSession
  live:      boolean   // the sheet's store is on the inventory ledger
  onRefresh: () => Promise<void> | void
  onReceive: () => void
}

export default function PickingStep({ session, live, onRefresh, onReceive }: Props) {
  const screens  = useBreakpoint()
  const isMobile = !screens.md

  const [localFoundQty, setLocalFoundQty] = useState<Record<number, number>>({})
  const [previewOpen,   setPreviewOpen]   = useState(false)
  const [completing,    setCompleting]    = useState(false)
  const requestKey = useRef(crypto.randomUUID())

  const isCompleted   = session.status === 'completed'
  // Once goods are dispatched the pick is fixed: they are received on the floor, never picked twice.
  const dispatched    = session.delivery?.lines.some(line => line.dispatched_quantity > 0) ?? false
  const locked        = isCompleted || dispatched
  const pendingCount  = session.items.filter(i => i.pick_status === 'pending').length
  const foundCount    = session.items.filter(i => i.pick_status === 'found').length
  const notFoundCount = session.items.filter(i => i.pick_status === 'not_found').length
  const doneCount     = foundCount + notFoundCount
  const totalCount    = session.items.length
  const allDone       = pendingCount === 0 && totalCount > 0

  const previewItems  = session.items.filter(
    i => i.pick_status === 'found' && (i.found_qty ?? 0) > 0
  )

  // What the box shows is what 找到 saves: the typed quantity, else the saved one, else the request.
  const shownFoundQty = (item: RestockItem) => localFoundQty[item.id] ?? (item.found_qty || item.requested_qty)

  // ── Pick update ───────────────────────────────────────────────────────────

  async function handlePickUpdate(item: RestockItem, pickStatus: 'found' | 'not_found') {
    const foundQty = pickStatus === 'found' ? shownFoundQty(item) : undefined
    try {
      await client.patch(`/restock/items/${item.id}/pick`, {
        pick_status: pickStatus,
        ...(foundQty !== undefined ? { found_qty: foundQty } : {}),
      })
      onRefresh()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error
      message.error(msg || '更新失败')
    }
  }

  // ── Complete ──────────────────────────────────────────────────────────────

  async function handleComplete() {
    setCompleting(true)
    try {
      if (!live) {
        // A store not on the ledger yet moves its legacy stock when the sheet completes.
        await client.post(`/restock/session/${session.id}/complete`)
        message.success('补货完成')
        setPreviewOpen(false)
        onRefresh()
        return
      }
      const foundLines = session.items.flatMap((item, index) => (
        item.pick_status === 'found' && (item.found_qty ?? 0) > 0
          ? [{ line_no: index + 1, quantity: item.found_qty }]
          : []
      ))
      const path = foundLines.length ? 'pick' : 'short-close'
      const lines = foundLines.length ? foundLines : session.items.map((item, index) => ({
        line_no: index + 1, quantity: item.requested_qty,
      }))
      await client.post(`/restock/session/${session.id}/${path}`, {
        expected_version: session.delivery?.version ?? 1,
        lines,
        ...(!foundLines.length ? { reason: 'All items marked not found' } : {}),
      }, { headers: { 'Idempotency-Key': requestKey.current } })
      requestKey.current = crypto.randomUUID()
      message.success(foundLines.length ? 'Physical pick confirmed' : 'No-stock restock closed')
      setPreviewOpen(false)
      await onRefresh()
      if (foundLines.length) onReceive()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error
      message.error(msg || '完成失败')
    } finally {
      setCompleting(false)
    }
  }

  // ── Columns ───────────────────────────────────────────────────────────────

  const columns: ColumnsType<RestockItem> = [
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
      title: '仓库快照',
      dataIndex: 'warehouse_stock_snapshot',
      width: 90,
      render: (v, r) => <Tag color="blue">{withUnit(v, r.stock_unit)}</Tag>,
    },
    {
      title: '申请数',
      dataIndex: 'requested_qty',
      width: 80,
      render: (v, r) => withUnit(v, r.stock_unit),
    },
    {
      title: '找到数量',
      key: 'found_qty',
      width: 130,
      render: (_, r) => {
        if (locked) {
          return r.pick_status === 'found'
            ? <Tag color="success">{withUnit(r.found_qty, r.stock_unit)}</Tag>
            : <Tag color="default">—</Tag>
        }
        return (
          <InputNumber
            min={1}
            max={r.requested_qty}   // ← max = requested_qty (not 999)
            precision={0}
            value={shownFoundQty(r)}
            onChange={v => setLocalFoundQty(prev => ({ ...prev, [r.id]: v ?? 1 }))}
            style={{ width: 85 }}
            size="small"
            disabled={r.pick_status === 'not_found'}
          />
        )
      },
    },
    {
      title: '状态',
      key: 'status',
      width: locked ? 90 : 180,
      render: (_, r) => {
        if (locked) {
          if (r.pick_status === 'found')     return <Tag color="success">已找到</Tag>
          if (r.pick_status === 'not_found') return <Tag color="error">缺货</Tag>
          return <Tag>待确认</Tag>
        }
        return (
          <Space size={4}>
            <Button
              size="small"
              type={r.pick_status === 'found' ? 'primary' : 'default'}
              icon={<CheckOutlined />}
              style={r.pick_status === 'found' ? { background: '#10B981', borderColor: '#10B981' } : {}}
              onClick={() => handlePickUpdate(r, 'found')}
            >
              找到
            </Button>
            <Button
              size="small" danger
              type={r.pick_status === 'not_found' ? 'primary' : 'default'}
              icon={<CloseOutlined />}
              onClick={() => handlePickUpdate(r, 'not_found')}
            >
              缺货
            </Button>
          </Space>
        )
      },
    },
  ]

  const previewCols: ColumnsType<RestockItem> = [
    {
      title: '产品',
      key: 'product',
      render: (_, r) => r.jizhanming || r.name_cn_en,
    },
    {
      title: '找到数量',
      dataIndex: 'found_qty',
      width: 100,
      render: (v, r) => <Tag color="success">{withUnit(v, r.stock_unit)}</Tag>,
    },
  ]

  return (
    <div style={{ paddingTop: 16 }}>
      {/* Instruction banner */}
      {!locked && (
        <Alert
          type="info"
          message="仓库拣货"
          description="请对照清单在仓库找货，逐一确认结果。全部处理完后点击「完成拣货」。"
          style={{ marginBottom: 16 }}
          showIcon
        />
      )}

      {/* Progress bar */}
      {!locked && totalCount > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <Text style={{ fontSize: 13 }}>
              已处理 <Text strong>{doneCount}</Text> / 共 <Text strong>{totalCount}</Text> 项
            </Text>
            <Space>
              <Tag color="success">找到 {foundCount}</Tag>
              <Tag color="error">缺货 {notFoundCount}</Tag>
              {pendingCount > 0 && <Tag color="default">待处理 {pendingCount}</Tag>}
            </Space>
          </div>
          <Progress
            percent={totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0}
            strokeColor={{ '0%': '#6366F1', '100%': '#10B981' }}
            size="small"
          />
        </div>
      )}

      {/* Stats + complete button (completed state) */}
      {locked && (
        <div style={{ marginBottom: 16 }}>
          <Space wrap>
            <Tag color="success">已找到 {foundCount}</Tag>
            <Tag color="error">缺货 {notFoundCount}</Tag>
          </Space>
        </div>
      )}

      {/* Dispatched: the goods are in transit and are received in the next step */}
      {dispatched && !isCompleted && (
        <Alert
          type="success"
          message="已确认拣货 · Pick confirmed"
          description="商品已转为运输中，请在「收货」确认到店数量。Receive what arrived on the floor."
          action={<Button type="primary" size="small" onClick={onReceive}>去收货 Receive</Button>}
          style={{ marginBottom: 16 }}
          showIcon
        />
      )}

      {/* "Complete picking" button */}
      {!locked && (
        <div style={{ marginBottom: 16, textAlign: 'right' }}>
          <Tooltip title={pendingCount > 0 ? `还有 ${pendingCount} 项未处理` : ''}>
            <Button
              type="primary" icon={<ShopOutlined />}
              disabled={!allDone}
              size={isMobile ? 'middle' : 'large'}
              onClick={() => setPreviewOpen(true)}
            >
              确认拣货
            </Button>
          </Tooltip>
        </div>
      )}

      {/* Items table */}
      <Table
        size="small"
        columns={columns}
        dataSource={session.items}
        rowKey="id"
        pagination={false}
        scroll={{ x: true }}
        rowClassName={r =>
          r.pick_status === 'found'     ? 'row-found'     :
          r.pick_status === 'not_found' ? 'row-not-found' : ''
        }
      />

      {isCompleted && (
        <Alert
          type="success"
          message={`补货完成！${session.completed_at ? `完成时间：${session.completed_at.slice(0, 16)}` : ''}`}
          style={{ marginTop: 16 }}
          showIcon
        />
      )}

      {/* Confirmation Modal with preview */}
      <Modal
        open={previewOpen}
        title="确认入店清单"
        okText="确认拣货"
        cancelText="取消"
        confirmLoading={completing}
        onOk={handleComplete}
        onCancel={() => setPreviewOpen(false)}
        width={500}
      >
        <p style={{ marginBottom: 12 }}>
          {live
            ? '以下商品将从仓库库存转为运输中，收货确认后才会进入门店库存：'
            : '以下商品将从仓库库存转入门店库存：'}
        </p>
        {previewItems.length === 0
          ? <Text type="secondary">没有找到任何商品（全部缺货）</Text>
          : (
            <Table
              size="small"
              columns={previewCols}
              dataSource={previewItems}
              rowKey="id"
              pagination={false}
            />
          )
        }
        {notFoundCount > 0 && (
          <Alert
            type="warning"
            message={`另有 ${notFoundCount} 件商品缺货，不会同步库存`}
            style={{ marginTop: 12 }}
            showIcon
          />
        )}
      </Modal>
    </div>
  )
}
