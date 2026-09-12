import { useState, useEffect, useCallback, useRef } from 'react'
import {
  Tabs, Typography, Tag, Spin, Button, Table, Popconfirm,
  Alert, Empty, message, Grid, Space,
} from 'antd'
import {
  PlusOutlined, AuditOutlined, StarOutlined,
  HistoryOutlined, FolderOpenOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { useSearchParams } from 'react-router-dom'
import client from '../../api/client'
import { useHasRole } from '../../auth/useRole'
import { useAppStore } from '../../store'
import EveningCheckStep from './EveningCheckStep'
import BestsellerManage from './BestsellerManage'
import HistoryTab from './HistoryTab'
import SessionModal from './SessionModal'

const { Title } = Typography
const { useBreakpoint } = Grid

// ── Shared types (imported by child components) ───────────────────────────────

export interface RestockItem {
  id: number
  product_id: number
  session_id?: number
  requested_qty: number
  warehouse_stock_snapshot: number
  found_qty: number | null
  pick_status: 'pending' | 'found' | 'not_found'
  sku: string
  jizhanming: string
  name_cn_en: string
  ip_series: string
  product_type: string
  upstairs_qty: number
  instore_qty: number
}

export interface RestockSession {
  id: number
  store_id: number
  date: string
  status: 'pending' | 'submitted' | 'picking' | 'completed'
  created_at: string
  submitted_at: string | null
  completed_at: string | null
  items: RestockItem[]
  delivery?: {
    id: number
    version: number
    status: 'planned' | 'active' | 'completed' | 'cancelled'
    lines: Array<{
      line_no: number
      product_id: number
      native_unit: 'box' | 'set' | 'piece'
      requested_quantity: number
      dispatched_quantity: number
      received_quantity: number
      returned_quantity: number
      loss_quantity: number
      short_quantity: number
      outstanding_transit: number
    }>
  }
}

interface SessionSummary {
  id: number
  date: string
  status: 'pending' | 'submitted' | 'picking' | 'completed'
  created_at: string
  submitted_at: string | null
  completed_at: string | null
  item_count: number
  total_requested: number
  total_found: number
}

// ── Constants ─────────────────────────────────────────────────────────────────

const STATUS_LABELS: Record<string, string> = {
  pending:   '录入中',
  submitted: '待拣货',
  picking:   '拣货中',
  completed: '已完成',
}

const STATUS_COLORS: Record<string, string> = {
  pending:   'processing',
  submitted: 'warning',
  picking:   'orange',
  completed: 'success',
}

// ── Today's session list ──────────────────────────────────────────────────────

function TodaySessions() {
  const [params, setParams] = useSearchParams()
  const [sessions, setSessions]   = useState<SessionSummary[]>([])
  const [loading, setLoading]     = useState(true)
  const [creating, setCreating]   = useState(false)
  const requestedId = Number(params.get('session_id'))
  const [openId, setOpenId]       = useState<number | null>(Number.isInteger(requestedId) && requestedId > 0 ? requestedId : null)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [loadError, setLoadError] = useState('')
  const request = useRef(0)
  const { selectedStore } = useAppStore()

  const load = useCallback(async () => {
    const sc = selectedStore?.code
    if (!sc) return
    const current = ++request.current
    setSessions([])
    setLoadError('')
    setLoading(true)
    try {
      const { data } = await client.get<SessionSummary[]>('/restock/sessions/today', { params: { store_code: sc } })
      if (current === request.current) setSessions(data)
    } catch (cause: any) {
      if (current === request.current) setLoadError(cause?.response?.data?.error || 'Unable to load restock sessions for this store.')
    } finally {
      if (current === request.current) setLoading(false)
    }
  }, [selectedStore?.code])

  useEffect(() => { load(); return () => { request.current += 1 } }, [load])
  useEffect(() => { if (Number.isInteger(requestedId) && requestedId > 0) setOpenId(requestedId) }, [requestedId])

  async function handleCreate() {
    const sc = selectedStore?.code
    if (!sc) return
    setCreating(true)
    try {
      const { data } = await client.post('/restock/sessions', { store_code: sc })
      await load()
      setOpenId(data.id)
    } catch {
      message.error('创建失败，请重试')
    } finally {
      setCreating(false)
    }
  }

  async function doDelete(id: number) {
    setDeletingId(id)
    try {
      await client.delete(`/restock/session/${id}`)
      message.success('已撤销')
      await load()
    } catch (error: any) {
      message.error(error?._serverMessage || '撤销失败，请重试')
    } finally {
      setDeletingId(null)
    }
  }

  const columns: ColumnsType<SessionSummary> = [
    {
      title: '创建时间', dataIndex: 'created_at', width: 80,
      render: v => dayjs(v).format('HH:mm'),
    },
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: v => <Tag color={STATUS_COLORS[v]}>{STATUS_LABELS[v]}</Tag>,
    },
    {
      title: '商品种类', dataIndex: 'item_count', width: 80, align: 'center',
    },
    {
      title: '申请箱数', dataIndex: 'total_requested', width: 80, align: 'center',
    },
    {
      title: '实际找到', dataIndex: 'total_found', width: 80, align: 'center',
      render: (v, r) => r.status === 'completed'
        ? <span style={{ color: v < r.total_requested ? '#EF4444' : '#10B981', fontWeight: 600 }}>{v}</span>
        : <span style={{ color: '#9ca3af' }}>—</span>,
    },
    {
      title: '操作', width: 130, align: 'center',
      render: (_, s) => (
        <Space size={6}>
          <Button
            size="small"
            icon={<FolderOpenOutlined />}
            onClick={() => setOpenId(s.id)}
          >
            打开
          </Button>
          {s.status !== 'completed'
            ? (
              <Popconfirm
                title="确认撤销这条补货记录？"
                okText="撤销" okButtonProps={{ danger: true }}
                onConfirm={() => doDelete(s.id)}
              >
                <Button size="small" danger loading={deletingId === s.id}>撤销</Button>
              </Popconfirm>
            )
            : (
              <Tag>保留记录</Tag>
            )
          }
        </Space>
      ),
    },
  ]

  return (
    <>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ color: '#6b7280', fontSize: 13 }}>今日 {dayjs().format('MM月DD日')} 补货记录</span>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          loading={creating}
          onClick={handleCreate}
        >
          新建补货
        </Button>
      </div>
      {loadError && <Alert type="error" showIcon message={loadError} style={{ marginBottom: 12 }} />}

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
      ) : sessions.length === 0 ? (
        <Empty
          description="今日暂无补货记录"
          style={{ padding: '40px 0' }}
        >
          <Button type="primary" icon={<PlusOutlined />} loading={creating} onClick={handleCreate}>
            新建补货
          </Button>
        </Empty>
      ) : (
        <Table
          rowKey="id"
          columns={columns}
          dataSource={sessions}
          size="small"
          pagination={false}
        />
      )}

      <SessionModal
        sessionId={openId}
        onClose={() => { setOpenId(null); const next = new URLSearchParams(params); next.delete('session_id'); setParams(next); load() }}
      />
    </>
  )
}

// ── Page root ─────────────────────────────────────────────────────────────────

export default function RestockPage() {
  const screens  = useBreakpoint()
  const isMobile = !screens.md
  const isAdmin  = useHasRole('admin')

  const tabItems = [
    {
      key:      'today',
      label:    <span><PlusOutlined /> 今日补货</span>,
      children: <TodaySessions />,
    },
    {
      key:      'evening',
      label:    <span><AuditOutlined /> 晚盘核查</span>,
      children: <EveningCheckStep />,
    },
    {
      key:      'history',
      label:    <span><HistoryOutlined /> 历史记录</span>,
      children: <HistoryTab />,
    },
    ...(isAdmin ? [{
      key:      'bestsellers',
      label:    <span><StarOutlined /> 畅销品管理</span>,
      children: <BestsellerManage />,
    }] : []),
  ]

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>夜间补货 & 晚盘</Title>
      </div>

      <Tabs
        defaultActiveKey="today"
        items={tabItems}
        size={isMobile ? 'small' : 'middle'}
        style={{ background: '#fff', padding: isMobile ? '0 8px 8px' : '0 16px 16px', borderRadius: 8 }}
      />
      <RestockSuggestions />
    </div>
  )
}

interface Suggestion {product_id:number;sku:string;name:string;unit:string;floor_quantity:number;available_back_quantity:number;outstanding_inbound:number;min_quantity:number;max_quantity:number;suggested_quantity:number}
function RestockSuggestions(){
  const selectedStore=useAppStore(state=>state.selectedStore)
  const[data,setData]=useState<Suggestion[]>(),[error,setError]=useState('')
  useEffect(()=>{if(!selectedStore||selectedStore.code==='ALL'){setData(undefined);setError('Select one store to view floor suggestions.');return}let current=true;setData(undefined);setError('');client.get('/inventory/locations').then(response=>{const floor=response.data.find((location:{store_id:number;code:string})=>location.store_id===selectedStore.id&&location.code==='floor');if(!floor)throw new Error('floor');return client.get('/goods/restock-suggestions',{params:{location_id:floor.id}})}).then(response=>{if(current)setData(response.data.items)}).catch(cause=>{if(current)setError(cause?.response?.data?.error||'Restock suggestions are unavailable because reviewed floor/back-stock configuration is missing.')});return()=>{current=false}},[selectedStore])
  return <div style={{marginTop:16}}><Title level={5}>Floor suggestions</Title>{error&&<Alert type="info" showIcon message={error}/>} {data&&<Table rowKey="product_id" pagination={false} size="small" dataSource={data} columns={[{title:'Product',render:(_,row)=>`${row.name||row.sku} (${row.sku})`},{title:'Target',render:(_,row)=>`${row.min_quantity}–${row.max_quantity} ${row.unit}s`},{title:'Floor',dataIndex:'floor_quantity'},{title:'Available back',dataIndex:'available_back_quantity'},{title:'Inbound',dataIndex:'outstanding_inbound'},{title:'Suggested',render:(_,row)=><strong>{row.suggested_quantity} {row.unit}s</strong>}]}/>}<Typography.Text type="secondary">Read-only guidance. Suggestions never move stock or create a shipment; target editing requires a versioned read contract.</Typography.Text></div>
}
