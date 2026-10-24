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
import { RestockHistory } from './HistoryTab'
import { Link } from 'react-router-dom'
import SessionModal from './SessionModal'
import PageHeader from '../../components/pc/PageHeader'
import { SectionHeading } from '../../components/pc/Panel'
import Icon from '../../components/pc/Icon'
import { useIsPhone } from '../../components/pc/useIsPhone'
import './restock.css'

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

  const rows = sessions.map(s => <div key={s.id} className="pc-restock-row">
    <button type="button" className="pc-restock-open" onClick={() => setOpenId(s.id)} aria-label={`打开 补货单 #${s.id}`}>
      <strong>{dayjs(s.created_at).format('HH:mm')} · {s.item_count} 种 · 申请 {s.total_requested} 箱</strong>
      <small>已找到 {s.status === 'completed' ? <b className={s.total_found < s.total_requested ? 'pc-restock-short' : 'pc-restock-full'}>{s.total_found}</b> : '—'}</small>
    </button>
    <span className="pc-hm-status"><i className={`pc-dot pc-dot-${s.status === 'completed' ? 'ok' : 'warn'}`} aria-hidden="true" />{STATUS_LABELS[s.status]}</span>
    <span className="pc-restock-actions">
      <Button size="small" icon={<FolderOpenOutlined />} onClick={() => setOpenId(s.id)}>打开</Button>
      {s.status !== 'completed'
        ? <Popconfirm title="确认撤销这条补货记录？" okText="撤销" okButtonProps={{ danger: true }} onConfirm={() => doDelete(s.id)}>
          <Button size="small" danger loading={deletingId === s.id}>撤销</Button>
        </Popconfirm>
        : <span className="pc-restock-kept">保留记录</span>}
    </span>
  </div>)

  return (
    <>
      <Button className="pc-restock-new" type="primary" size="large" icon={<PlusOutlined />} loading={creating} onClick={handleCreate}>新建补货单 New restock sheet</Button>
      {loadError && <Alert type="error" showIcon message={loadError} style={{ marginBottom: 12 }} />}
      <SectionHeading zh="今日补货单" en={`Today's sheets · ${dayjs().format('MM月DD日')}`} />
      {loading ? (
        <div className="pc-panel pc-quiet"><Spin /></div>
      ) : sessions.length === 0 ? (
        <div className="pc-panel pc-quiet"><strong>今日暂无补货记录</strong>No restock sheets yet today</div>
      ) : (
        <div className="pc-panel pc-restock-list">{rows}</div>
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
  const selectedStore = useAppStore(state => state.selectedStore)

  const tabItems = [
    {
      key:      'today',
      label:    <>今日补货 <small>Today</small></>,
      children: <TodaySessions />,
    },
    {
      key:      'history',
      label:    <>历史记录 <small>History</small></>,
      children: <RestockHistory />,
    },
  ]

  return (
    <div className="pc-restock">
      <PageHeader title="夜间补货" sub={isMobile ? `Restock · ${dayjs().format('M月D日')}` : `Restock${selectedStore?.name && selectedStore.code !== 'ALL' ? ` · ${selectedStore.name}` : ''}`} />
      <div className="pc-restock-grid">
        <RestockSuggestions />
        <div>
          <Tabs className="pc-restock-page-tabs" defaultActiveKey="today" items={tabItems} size={isMobile ? 'small' : 'middle'} />
          <p className="pc-restock-note">晚盘 Evening count is now in <Link to="/stock/count">库存 → 晚盘</Link>.</p>
        </div>
      </div>
    </div>
  )
}

interface Suggestion {product_id:number;sku:string;name:string;unit:string;floor_quantity:number;available_back_quantity:number;outstanding_inbound:number;min_quantity:number;max_quantity:number;suggested_quantity:number}
function RestockSuggestions(){
  const selectedStore=useAppStore(state=>state.selectedStore)
  const[data,setData]=useState<Suggestion[]>(),[error,setError]=useState('')
  useEffect(()=>{if(!selectedStore||selectedStore.code==='ALL'){setData(undefined);setError('Select one store to view floor suggestions.');return}let current=true;setData(undefined);setError('');client.get('/inventory/locations').then(response=>{const floor=response.data.find((location:{store_id:number;code:string})=>location.store_id===selectedStore.id&&location.code==='floor');if(!floor)throw new Error('floor');return client.get('/goods/restock-suggestions',{params:{location_id:floor.id}})}).then(response=>{if(current)setData(response.data.items)}).catch(cause=>{if(current)setError(cause?.response?.data?.error||'Restock suggestions are unavailable because reviewed floor/back-stock configuration is missing.')});return()=>{current=false}},[selectedStore])
  const head=(zh:string,en:string)=><span className="pc-th">{zh}<small>{en}</small></span>
  const phone=useIsPhone()
  const body=<>{error&&<Alert type="info" showIcon message={error}/>} {data&&<Table className="pc-restock-table" rowKey="product_id" pagination={false} size="small" dataSource={data} columns={[{title:head('产品','Product'),render:(_,row)=><span className="pc-td-name">{row.name||row.sku}<small>{row.sku}</small></span>},{title:head('目标','Target'),align:'right',render:(_,row)=>`${row.min_quantity}–${row.max_quantity} ${row.unit}s`},{title:head('楼面','Floor'),align:'right',dataIndex:'floor_quantity'},{title:head('楼上','Back'),align:'right',className:'pc-kraft-cell',dataIndex:'available_back_quantity'},{title:head('在途','Inbound'),align:'right',dataIndex:'outstanding_inbound'},{title:head('建议','Suggest'),align:'right',render:(_,row)=><strong>{row.suggested_quantity} {row.unit}s</strong>}]}/>}<p className="pc-restock-note">Read-only guidance. Suggestions never move stock or create a shipment; target editing requires a versioned read contract.</p></>
  // Phone: one row that opens the list (Figma Restock · Phone 1); desktop: the table beside today's sheets.
  if(phone)return <details className="pc-restock-suggest pc-restock-fold" aria-label="Floor suggestions"><summary><Icon name="up-2" size={18}/><span className="pc-nav-text"><strong>楼面建议补货 {data?data.length:'—'} 项</strong><small>Floor suggestions · 楼上有货</small></span><Icon name="right" size={16}/></summary>{body}</details>
  return <section className="pc-restock-suggest" aria-label="Floor suggestions"><SectionHeading zh="楼面建议" en={`Floor suggestions${data?` · ${data.length}`:''}`}/>{body}</section>
}
