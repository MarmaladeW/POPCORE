import { useState } from 'react'
import {
  Table, InputNumber, Button, Tag, message,
  Space, Typography, Popconfirm, Grid, Alert,
} from 'antd'
import { CheckOutlined, CloseOutlined, ShopOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'
import type { RestockSession, RestockItem } from './index'

const { Text } = Typography
const { useBreakpoint } = Grid

interface Props {
  session: RestockSession
  onRefresh: () => void
}

export default function PickingStep({ session, onRefresh }: Props) {
  const screens  = useBreakpoint()
  const isMobile = !screens.md

  const [localFoundQty, setLocalFoundQty] = useState<Record<number, number>>({})
  const [completing, setCompleting] = useState(false)

  const isCompleted = session.status === 'completed'

  async function handlePickUpdate(item: RestockItem, pickStatus: 'found' | 'not_found') {
    const foundQty = pickStatus === 'found' ? (localFoundQty[item.id] ?? item.requested_qty) : 0
    try {
      await client.patch(`/restock/sessions/${session.id}/items/${item.id}`, {
        pick_status: pickStatus,
        found_qty:   foundQty,
      })
      onRefresh()
    } catch {
      message.error('更新失败')
    }
  }

  async function handleComplete() {
    setCompleting(true)
    try {
      const { data } = await client.post(`/restock/sessions/${session.id}/complete`)
      message.success(`入店完成，共同步 ${data.synced} 件产品`)
      onRefresh()
    } catch {
      message.error('完成失败')
    } finally {
      setCompleting(false)
    }
  }

  const pendingCount  = session.items.filter(i => i.pick_status === 'pending').length
  const foundCount    = session.items.filter(i => i.pick_status === 'found').length
  const notFoundCount = session.items.filter(i => i.pick_status === 'not_found').length

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
      width: 120,
      render: (_, r) => {
        if (isCompleted) {
          return r.pick_status === 'found'
            ? <Tag color="success">{r.found_qty} 端</Tag>
            : <Tag color="default">—</Tag>
        }
        return (
          <InputNumber
            min={0} max={999}
            value={localFoundQty[r.id] ?? r.requested_qty}
            onChange={v => setLocalFoundQty(prev => ({ ...prev, [r.id]: v ?? 0 }))}
            style={{ width: 80 }}
            size="small"
            disabled={r.pick_status === 'not_found'}
          />
        )
      },
    },
    {
      title: '状态',
      key: 'status',
      width: isCompleted ? 100 : 180,
      render: (_, r) => {
        if (isCompleted) {
          return r.pick_status === 'found'
            ? <Tag color="success">已找到</Tag>
            : r.pick_status === 'not_found'
              ? <Tag color="error">缺货</Tag>
              : <Tag>待确认</Tag>
        }
        return (
          <Space size={4}>
            <Button
              size="small" type={r.pick_status === 'found' ? 'primary' : 'default'}
              icon={<CheckOutlined />}
              style={r.pick_status === 'found' ? { background: '#10B981', borderColor: '#10B981' } : {}}
              onClick={() => handlePickUpdate(r, 'found')}
            >
              找到
            </Button>
            <Button
              size="small" danger type={r.pick_status === 'not_found' ? 'primary' : 'default'}
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

  return (
    <div style={{ paddingTop: 16 }}>
      {!isCompleted && (
        <Alert
          type="info"
          message="仓库拣货"
          description="请对照清单在仓库找货，逐一确认结果。完成后点击「确认入店」同步库存。"
          style={{ marginBottom: 16 }}
          showIcon
        />
      )}

      <div style={{
        display: 'flex', gap: 16, marginBottom: 16, flexWrap: 'wrap',
        alignItems: 'center', justifyContent: 'space-between',
      }}>
        <Space wrap>
          <Tag>共 {session.items.length} 项</Tag>
          <Tag color="success">已找到 {foundCount}</Tag>
          <Tag color="error">缺货 {notFoundCount}</Tag>
          {!isCompleted && <Tag color="default">待确认 {pendingCount}</Tag>}
        </Space>

        {!isCompleted && (
          <Popconfirm
            title="确认入店"
            description={`将同步 ${foundCount} 件产品的库存（仓库→门店）`}
            onConfirm={handleComplete}
            okText="确认入店"
            cancelText="取消"
            disabled={foundCount === 0}
          >
            <Button
              type="primary" icon={<ShopOutlined />}
              loading={completing} disabled={foundCount === 0}
              size={isMobile ? 'middle' : 'large'}
            >
              确认入店
            </Button>
          </Popconfirm>
        )}
      </div>

      <Table
        size="small"
        columns={columns}
        dataSource={session.items}
        rowKey="id"
        pagination={false}
        scroll={{ x: true }}
        rowClassName={r =>
          r.pick_status === 'found'
            ? 'row-found'
            : r.pick_status === 'not_found'
              ? 'row-not-found'
              : ''
        }
      />

      {isCompleted && (
        <Alert
          type="success"
          message={`补货完成！${session.completed_at ? `完成时间：${session.completed_at}` : ''}`}
          style={{ marginTop: 16 }}
          showIcon
        />
      )}
    </div>
  )
}
