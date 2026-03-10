import { useState } from 'react'
import {
  Table, InputNumber, Button, Tag, message, Modal,
  Space, Typography, Grid, Alert, Progress, Tooltip,
} from 'antd'
import { CheckOutlined, CloseOutlined, ShopOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'
import type { RestockSession, RestockItem } from './index'

const { Text } = Typography
const { useBreakpoint } = Grid

interface Props {
  session:   RestockSession
  onRefresh: () => void
}

export default function PickingStep({ session, onRefresh }: Props) {
  const screens  = useBreakpoint()
  const isMobile = !screens.md

  const [localFoundQty, setLocalFoundQty] = useState<Record<number, number>>({})
  const [previewOpen,   setPreviewOpen]   = useState(false)
  const [completing,    setCompleting]    = useState(false)

  const isCompleted   = session.status === 'completed'
  const pendingCount  = session.items.filter(i => i.pick_status === 'pending').length
  const foundCount    = session.items.filter(i => i.pick_status === 'found').length
  const notFoundCount = session.items.filter(i => i.pick_status === 'not_found').length
  const doneCount     = foundCount + notFoundCount
  const totalCount    = session.items.length
  const allDone       = pendingCount === 0 && totalCount > 0

  const previewItems  = session.items.filter(
    i => i.pick_status === 'found' && (i.found_qty ?? 0) > 0
  )

  // ── Pick update ───────────────────────────────────────────────────────────

  async function handlePickUpdate(item: RestockItem, pickStatus: 'found' | 'not_found') {
    const foundQty = pickStatus === 'found'
      ? (localFoundQty[item.id] ?? item.requested_qty)
      : undefined
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
      const { data } = await client.post(`/restock/session/${session.id}/complete`)
      message.success(`入店完成，共同步 ${data.synced} 件产品`)
      setPreviewOpen(false)
      onRefresh()
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
      render: v => <Tag color="blue">{v} 端</Tag>,
    },
    {
      title: '申请数',
      dataIndex: 'requested_qty',
      width: 80,
      render: v => `${v} 端`,
    },
    {
      title: '找到数量',
      key: 'found_qty',
      width: 130,
      render: (_, r) => {
        if (isCompleted) {
          return r.pick_status === 'found'
            ? <Tag color="success">{r.found_qty} 端</Tag>
            : <Tag color="default">—</Tag>
        }
        return (
          <InputNumber
            min={1}
            max={r.requested_qty}   // ← max = requested_qty (not 999)
            precision={0}
            value={localFoundQty[r.id] ?? r.requested_qty}
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
      width: isCompleted ? 90 : 180,
      render: (_, r) => {
        if (isCompleted) {
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
      render: v => <Tag color="success">{v} 端</Tag>,
    },
  ]

  return (
    <div style={{ paddingTop: 16 }}>
      {/* Instruction banner */}
      {!isCompleted && (
        <Alert
          type="info"
          message="仓库拣货"
          description="请对照清单在仓库找货，逐一确认结果。全部处理完后点击「完成拣货」。"
          style={{ marginBottom: 16 }}
          showIcon
        />
      )}

      {/* Progress bar */}
      {!isCompleted && totalCount > 0 && (
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
      {isCompleted && (
        <div style={{ marginBottom: 16 }}>
          <Space wrap>
            <Tag color="success">已找到 {foundCount}</Tag>
            <Tag color="error">缺货 {notFoundCount}</Tag>
          </Space>
        </div>
      )}

      {/* "Complete picking" button */}
      {!isCompleted && (
        <div style={{ marginBottom: 16, textAlign: 'right' }}>
          <Tooltip title={pendingCount > 0 ? `还有 ${pendingCount} 项未处理` : ''}>
            <Button
              type="primary" icon={<ShopOutlined />}
              disabled={!allDone}
              size={isMobile ? 'middle' : 'large'}
              onClick={() => setPreviewOpen(true)}
            >
              完成拣货
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
        okText="确认入店"
        cancelText="取消"
        confirmLoading={completing}
        onOk={handleComplete}
        onCancel={() => setPreviewOpen(false)}
        width={500}
      >
        <p style={{ marginBottom: 12 }}>
          以下商品将从仓库移入门店，库存同步后不可撤销：
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
