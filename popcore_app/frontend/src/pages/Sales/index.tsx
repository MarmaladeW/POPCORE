import { useState, useEffect, useCallback, useRef } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { useRole } from '../../auth/useRole'
import { productLabel } from '../../lib/productLabel'
import { savedLineValue, savedPriceSummary } from './historicalSales'
import {
  Table, Button, Space, Tag, Popconfirm, message,
  Typography, Row, Col, InputNumber, Card,
  DatePicker, AutoComplete, Spin, Alert,
} from 'antd'
import {
  ExportOutlined, DeleteOutlined,
  LeftOutlined, RightOutlined, ImportOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs, { Dayjs } from 'dayjs'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartTooltip, ResponsiveContainer,
  BarChart as HBarChart,
} from 'recharts'
import client from '../../api/client'
import type { ReportMetadata } from '../../api/matcher'
import { cashDifference, hasReportNotes, REPORT_NOTE_SECTIONS } from './dailyReportReview'
import RoleGuard from '../../components/RoleGuard'
import { useAppStore } from '../../store'
import DailyReportEntry from './DailyReportEntry'
import AliasManager from './AliasManager'
import { useIsMobile } from '../../hooks/useIsMobile'

const { Text, Title } = Typography

interface SaleRow {
  id: number
  product_id: number
  date: string
  qty_pos: number
  qty_cash: number
  qty_sold: number
  notes: string
  sku: string
  jizhanming: string
  name_cn_en: string
  unit_price: number | null
  ip_series: string
}

interface SummaryRow {
  date: string
  product_count: number
  total_sold: number
  total_pos: number
  total_cash: number
}

export default function SalesPage() {
  const { user } = useAuth0(), role = useRole()
  const selectedStore = useAppStore(state => state.selectedStore)
  const [params, setParams] = useSearchParams()
  const requestedDate = params.get('date')
  const date = requestedDate && /^\d{4}-\d{2}-\d{2}$/.test(requestedDate) && dayjs(requestedDate).format('YYYY-MM-DD') === requestedDate ? dayjs(requestedDate) : dayjs()
  const setDate = (next:Dayjs|((current:Dayjs)=>Dayjs)) => {
    const value = typeof next === 'function' ? next(date) : next
    const query = new URLSearchParams(params); query.set('date', value.format('YYYY-MM-DD')); setParams(query)
  }
  return <SalesScope key={`${user?.sub}|${role}|${selectedStore?.code}|${date.format('YYYY-MM-DD')}`} date={date} setDate={setDate} />
}

function SalesScope({date,setDate}:{date:Dayjs;setDate:(next:Dayjs|((current:Dayjs)=>Dayjs))=>void}) {
  const isMobile = useIsMobile()
  const { selectedStore, stores, setSelectedStore } = useAppStore()
  const sc = selectedStore?.code
  const isAll = sc === 'ALL'

  const [sales,   setSales]   = useState<SaleRow[]>([])
  const [summary, setSummary] = useState<SummaryRow[]>([])
  const [loading, setLoading] = useState(false)
  const [reportMetadata, setReportMetadata] = useState<ReportMetadata | null>(null)
  const [metadataScope, setMetadataScope] = useState('')
  const [metadataError, setMetadataError] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [lastSuccess, setLastSuccess] = useState<Date | null>(null)
  const [addSearch,    setAddSearch]    = useState('')
  const [addOptions,   setAddOptions]   = useState<any[]>([])
  const [pendingAdd,   setPendingAdd]   = useState<{ id: number; label: string } | null>(null)
  const [pendingPos,   setPendingPos]   = useState(0)
  const [pendingCash,  setPendingCash]  = useState(0)
  const [importMode,   setImportMode]   = useState(false)
  const [aliasMode,    setAliasMode]    = useState(false)
  const [exportFrom,   setExportFrom]   = useState<Dayjs>(dayjs().subtract(30, 'day'))
  const [exportTo,    setExportTo]    = useState<Dayjs>(dayjs())
  const [localEdits,  setLocalEdits]  = useState<Record<number, { pos: number; cash: number }>>({})
  // Dates that have sales records, keyed by "YYYY-MM" month
  const [recordedDates, setRecordedDates] = useState<Set<string>>(new Set())
  const recordedFetchRef = useRef<string>('')
  const recordedRequestRef = useRef(0)
  const salesRequestRef = useRef(0)
  const salesScopeRef = useRef('')
  const searchRequestRef = useRef(0)
  const mounted = useRef(true)
  const savingRef = useRef(false)
  const [saving, setSaving] = useState(false)
  const [aliasSaving, setAliasSaving] = useState(false)
  const busy = saving || aliasSaving
  const [mutationError, setMutationError] = useState('')
  const [denied, setDenied] = useState(false)
  const editing = Object.keys(localEdits).length > 0
  const readOnly = !sc || isAll || loading || busy || denied

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; searchRequestRef.current += 1; window.dispatchEvent(new CustomEvent('popcore:checkout-busy', {detail:false})) }
  }, [])

  async function mutate(action:()=>Promise<unknown>, onSaved:()=>void) {
    if (savingRef.current || readOnly) return
    savingRef.current = true; setSaving(true); setMutationError('')
    window.dispatchEvent(new CustomEvent('popcore:checkout-busy', {detail:true}))
    try { await action(); if (mounted.current) onSaved() }
    catch (cause:any) { if (mounted.current) { setMutationError(cause?._serverMessage || 'Unable to save this historical report. Your changes are still here.'); if (cause?.response?.status === 403) setDenied(true) } }
    finally { if (mounted.current) { savingRef.current = false; setSaving(false); window.dispatchEvent(new CustomEvent('popcore:checkout-busy', {detail:false})) } }
  }

  const dateStr = date.format('YYYY-MM-DD')
  const metadataLoaded = metadataScope === `${sc}:${dateStr}` && reportMetadata !== null
  const hasSavedReport = sales.length > 0 || (metadataLoaded && (
    reportMetadata.cash_actual != null || reportMetadata.cash_expected != null
    || hasReportNotes(reportMetadata)
  ))

  const loadSales = useCallback(() => {
    if (!sc) return
    const requestId = ++salesRequestRef.current
    const scope = `${sc}:${dateStr}`
    if (salesScopeRef.current !== scope) {
      salesScopeRef.current = scope
      setSales([])
      setSummary([])
      setLastSuccess(null)
    }
    setLoading(true)
    setLoadError(null)
    setReportMetadata(null)
    setMetadataError(false)
    setMetadataScope(scope)
    if (sc !== 'ALL') {
      client.get<ReportMetadata>('/sales/report-metadata', { params: { date: dateStr, store: sc } })
        .then(response => {
          if (requestId === salesRequestRef.current) setReportMetadata(response.data)
        })
        .catch(() => {
          if (requestId === salesRequestRef.current) setMetadataError(true)
        })
    }
    Promise.all([
      client.get('/sales', { params: { date: dateStr, store_code: sc } }),
      client.get('/sales/summary', { params: { store_code: sc } }),
    ])
      .then(([salesResponse, summaryResponse]) => {
        if (requestId !== salesRequestRef.current) return
        setSales(salesResponse.data)
        setSummary(summaryResponse.data)
        setLastSuccess(new Date())
      })
      .catch(() => {
        if (requestId === salesRequestRef.current) setLoadError('Unable to load sales data.')
      })
      .finally(() => {
        if (requestId === salesRequestRef.current) setLoading(false)
      })
  }, [dateStr, sc])

  const fetchRecordedDates = useCallback((month: string) => {
    if (!sc || !month) return
    const key = `${sc}:${month}`
    if (recordedFetchRef.current === key) return
    recordedFetchRef.current = key
    const requestId = ++recordedRequestRef.current
    setRecordedDates(new Set())
    client.get('/sales/recorded-dates', { params: { store: sc, month } })
      .then(r => {
        if (requestId === recordedRequestRef.current) {
          setRecordedDates(new Set(r.data as string[]))
        }
      })
      .catch(() => { if (requestId === recordedRequestRef.current) recordedFetchRef.current = '' })
  }, [sc])

  useEffect(() => {
    loadSales()
    return () => { salesRequestRef.current += 1 }
  }, [loadSales])

  // Load recorded dates whenever the visible month changes
  useEffect(() => {
    fetchRecordedDates(date.format('YYYY-MM'))
  }, [date, fetchRecordedDates])

  async function searchToAdd(v: string) {
    setAddSearch(v); setPendingAdd(null); setAddOptions([])
    const requestId = ++searchRequestRef.current
    if (!v.trim()) return
    try {
      const response = await client.get('/products/search', { params: { q: v, limit: 8 } })
      if (mounted.current && requestId === searchRequestRef.current) setAddOptions(response.data.map((product:any) => ({value:String(product.id),label:`${productLabel(product)} (${product.sku})`})))
    } catch { if (mounted.current && requestId === searchRequestRef.current) setMutationError('Unable to search products. Try again.') }
  }

  async function confirmAdd() {
    if (!pendingAdd || editing) return
    const body = {product_id:pendingAdd.id,date:dateStr,qty_pos:pendingPos,qty_cash:pendingCash,notes:'',store_code:sc}
    await mutate(() => client.post('/sales/upsert', body), () => {
      setPendingAdd(null); setAddSearch(''); setAddOptions([]); setPendingPos(0); setPendingCash(0); loadSales()
    })
  }

  async function doExport() {
    try {
      const res = await client.get('/sales/export', {
        params: { from: exportFrom.format('YYYY-MM-DD'), to: exportTo.format('YYYY-MM-DD'), store_code: sc },
        responseType: 'blob',
      })
      const url = window.URL.createObjectURL(new Blob([res.data], { type: 'text/csv' }))
      const a   = document.createElement('a')
      a.href     = url
      a.download = `sales_${exportFrom.format('YYYY-MM-DD')}_${exportTo.format('YYYY-MM-DD')}.csv`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(url)
    } catch { message.error('Export failed') }
  }

  function setLocalQty(rowId: number, field: 'pos' | 'cash', val: number) {
    setLocalEdits(prev => ({
      ...prev,
      [rowId]: {
        pos:  prev[rowId]?.pos  ?? sales.find(s => s.id === rowId)?.qty_pos  ?? 0,
        cash: prev[rowId]?.cash ?? sales.find(s => s.id === rowId)?.qty_cash ?? 0,
        [field]: val,
      },
    }))
  }

  async function saveRow(row: SaleRow) {
    const local = localEdits[row.id]
    if (!local) return
    if (local.pos === row.qty_pos && local.cash === row.qty_cash) { cancelRow(row.id); return }
    const body = {product_id:row.product_id,date:dateStr,qty_pos:local.pos,qty_cash:local.cash,notes:row.notes,store_code:sc}
    await mutate(() => client.post('/sales/upsert', body), () => {
      setSales(previous => previous.map(item => item.id === row.id ? {...item,qty_pos:local.pos,qty_cash:local.cash,qty_sold:local.pos+local.cash} : item))
      setSummary(previous => previous.map(item => item.date === dateStr ? {...item,total_pos:item.total_pos+local.pos-row.qty_pos,total_cash:item.total_cash+local.cash-row.qty_cash,total_sold:item.total_sold+local.pos+local.cash-row.qty_sold} : item))
      setLocalEdits(previous => {const next = {...previous}; delete next[row.id]; return next})
    })
  }

  const cancelRow = (id:number) => { setLocalEdits(previous => {const next={...previous};delete next[id];return next});setMutationError('') }
  const rowActions = (row:SaleRow) => localEdits[row.id] ? <Space wrap>
    <Button size="small" aria-label={`Save ${row.jizhanming}`} disabled={readOnly} onClick={() => saveRow(row)}>Save</Button>
    <Button size="small" aria-label={`Cancel changes to ${row.jizhanming}`} disabled={busy} onClick={() => cancelRow(row.id)}>Cancel</Button>
  </Space> : null

  async function deleteRecord(id: number) {
    if (editing) return
    await mutate(() => client.delete(`/sales/record/${id}`), () => {message.success('Deleted');loadSales()})
  }

  async function clearDay() {
    if (editing) return
    await mutate(() => client.delete('/sales/clear_day', {params:{date:dateStr,store_code:sc}}), () => {message.success('Cleared');loadSales()})
  }

  const estimate = savedPriceSummary(sales)
  const estimateValue = !sales.length ? '—' : estimate.knownRows ? `CA$${estimate.knownValue.toFixed(2)}` : 'Unknown'
  const totalPos     = sales.reduce((s, r) => s + r.qty_pos, 0)
  const totalCash    = sales.reduce((s, r) => s + r.qty_cash, 0)
  const totalSold    = sales.reduce((s, r) => s + r.qty_sold, 0)

  // Weekly bar chart data (last 7 days from summary)
  const weeklyData = summary.slice(0, 7).reverse().map(r => ({
    date: dayjs(r.date).format('ddd MM/DD'),
    UnitsSold: r.total_sold,
  }))

  // Top products for today
  const topProducts = [...sales]
    .sort((a, b) => b.qty_sold - a.qty_sold)
    .slice(0, 6)
    .map(r => ({ name: r.jizhanming || r.sku, POS: r.qty_pos, 'Non-POS': r.qty_cash }))

  const entryColumns: ColumnsType<SaleRow> = [
    {
      title: 'Product',
      render: (_, r) => (
        <div>
          <div style={{ fontWeight: 500, fontSize: 13, color: '#111827' }}>{r.jizhanming || '—'}</div>
          <div style={{ fontSize: 11, color: '#9ca3af' }}>{r.sku}</div>
        </div>
      ),
    },
    {
      title: 'Series', dataIndex: 'ip_series', width: 110,
      render: v => v ? <Tag color="blue" style={{ fontSize: 11 }}>{v}</Tag> : '—',
    },
    {
      title: 'Saved unit price', dataIndex: 'unit_price', width: 80, align: 'right',
      render: v => v != null ? <Text style={{ fontSize: 12 }}>CA${v}</Text> : 'Unknown',
    },
    {
      title: 'POS Qty', dataIndex: 'qty_pos', width: 100, align: 'center',
      render: (v, r) => (
        <InputNumber
          size="small" min={0}
          disabled={readOnly}
          value={localEdits[r.id]?.pos ?? v}
          onChange={val => { if (!isAll) setLocalQty(r.id, 'pos', val ?? 0) }}
          aria-label={`POS quantity for ${r.jizhanming}`}
          onPressEnter={() => saveRow(r)}
          style={{ width: 65 }}
        />
      ),
    },
    {
      title: 'Non-POS Qty', dataIndex: 'qty_cash', width: 100, align: 'center',
      render: (v, r) => (
        <InputNumber
          size="small" min={0}
          disabled={readOnly}
          value={localEdits[r.id]?.cash ?? v}
          onChange={val => { if (!isAll) setLocalQty(r.id, 'cash', val ?? 0) }}
          aria-label={`Non-POS quantity for ${r.jizhanming}`}
          onPressEnter={() => saveRow(r)}
          style={{ width: 65 }}
        />
      ),
    },
    {
      title: 'Total Units', dataIndex: 'qty_sold', width: 90, align: 'center',
      render: v => <Text style={{ fontWeight: 600, color: v > 0 ? '#10B981' : '#9ca3af' }}>{v}</Text>,
    },
    {
      title: 'Saved-price estimate', width: 110, align: 'right',
      render: (_, r) => {
        const value = savedLineValue(r)
        return <Text style={{ color: '#6366F1', fontSize: 12 }}>{value == null ? 'Unknown' : `CA$${value.toFixed(2)}`}</Text>
      },
    },
    { title:'Changes',key:'changes',width:140,render:(_,row)=>rowActions(row) },
    {
      title: '', key: 'del', width: 50,
      render: (_, r) => isAll ? null : (
        <RoleGuard minRole="manager">
          <Popconfirm title="Delete this record?" onConfirm={() => deleteRecord(r.id)}>
            <Button size="small" danger type="text" disabled={readOnly || editing} aria-label="Delete historical row" icon={<DeleteOutlined />} />
          </Popconfirm>
        </RoleGuard>
      ),
    },
  ]

  const summaryColumns: ColumnsType<SummaryRow> = [
    { title: 'Date', dataIndex: 'date', width: 110, render:value=><Link to={`/sales/day/${value}`}>{value}</Link> },
    { title: 'Products', dataIndex: 'product_count', width: 90, align: 'center' },
    { title: 'POS', dataIndex: 'total_pos', width: 80, align: 'center', render: v => <Tag color="blue">{v}</Tag> },
    { title: 'Non-POS', dataIndex: 'total_cash', width: 80, align: 'center', render: v => <Tag color="cyan">{v}</Tag> },
    { title: 'Total Sold', dataIndex: 'total_sold', width: 90, align: 'center', render: v => <Tag color="green">{v}</Tag> },
  ]


  if (loadError && !lastSuccess) {
    return <Alert role="alert" type="error" showIcon message={loadError} action={<Button onClick={loadSales}>Retry</Button>} />
  }

  return (
    <div>
      <div style={{marginBottom:16}}>
        <Title level={3} style={{margin:0}}>Historical reports</Title>
        <p style={{margin:'6px 0'}}>Review pasted daily summaries for {selectedStore?.name}. Saved report prices estimate product value; payment totals and refunds are in Insights &amp; reports.</p>
        <Space wrap>
          <Link to="/sales/matching">Review past names</Link>
          <Link to="/reports">Insights &amp; reports</Link>
          <Button disabled={busy} onClick={() => setAliasMode(value => !value)}>{aliasMode ? 'Hide name mappings' : 'Manage name mappings'}</Button>
        </Space>
      </div>
      {/* Alias Manager panel */}
      {aliasMode && (
        <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)', padding: '20px', marginBottom: 20 }}>
          <AliasManager onBusyChange={setAliasSaving} />
        </div>
      )}

      {mutationError && <Alert role="alert" type="error" showIcon message={mutationError} style={{marginBottom:16}} />}
      {/* Header */}
      {isMobile ? (
        /* Mobile: Day-navigator with ‹ prev / date / next › */
        <div style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: pendingAdd ? 8 : 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <Button
                type="text"
                icon={<LeftOutlined />}
                aria-label="Previous report date"
                disabled={busy}
                onClick={() => setDate(d => d.subtract(1, 'day'))}
                style={{ color: '#374151', padding: '0 8px' }}
              />
              <DatePicker
                value={date}
                disabled={busy}
                onChange={d => setDate(d ?? dayjs())}
                allowClear={false}
                style={{ width: 128, fontWeight: 600, fontSize: 14 }}
                format="ddd, MMM D"
                onPanelChange={(val) => fetchRecordedDates(val.format('YYYY-MM'))}
                cellRender={(current, info) => {
                  if (info.type !== 'date') return info.originNode
                  const ds = (current as Dayjs).format('YYYY-MM-DD')
                  const hasData = recordedDates.has(ds)
                  return (
                    <div className="ant-picker-cell-inner" style={{ position: 'relative' }}>
                      {(current as Dayjs).date()}
                      {hasData && (
                        <span style={{
                          position: 'absolute', bottom: 2, left: '50%', transform: 'translateX(-50%)',
                          width: 5, height: 5, borderRadius: '50%', background: '#10B981', display: 'block',
                        }} />
                      )}
                    </div>
                  )
                }}
              />
              <Button
                type="text"
                icon={<RightOutlined />}
                aria-label="Next report date"
                onClick={() => setDate(d => d.add(1, 'day'))}
                disabled={busy || date.isSame(dayjs(), 'day')}
                style={{ color: '#374151', padding: '0 8px' }}
              />
            </div>
            {!isAll && (
              <RoleGuard minRole="staff">
                <AutoComplete
                  aria-label="Add historical product"
                  disabled={readOnly || editing}
                  placeholder="Add product..."
                  value={addSearch}
                  options={addOptions}
                  onSearch={searchToAdd}
                  onSelect={(val, opt) => {
                    searchRequestRef.current += 1
                    setPendingAdd({ id: Number(val), label: opt.label as string })
                    setPendingPos(0); setPendingCash(0)
                    setAddSearch(opt.label as string); setAddOptions([])
                  }}
                  onClear={() => { searchRequestRef.current += 1; setAddSearch(''); setAddOptions([]); setPendingAdd(null) }}
                  allowClear
                  style={{ width: 150 }}
                />
              </RoleGuard>
            )}
          </div>
          {!isAll && pendingAdd && (
            <div style={{
              display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6,
              background: '#f0f4ff', borderRadius: 6, padding: '8px 10px',
              marginBottom: 4,
            }}>
              <span style={{ fontSize: 13, color: '#6366F1', fontWeight: 500, flexShrink: 0, maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {pendingAdd.label}
              </span>
              <span style={{ fontSize: 13, color: '#6b7280' }}>POS</span>
              <InputNumber size="small" min={0} disabled={readOnly} value={pendingPos} onChange={v => setPendingPos(v ?? 0)} style={{ width: 70, fontSize: 16 }} />
              <span style={{ fontSize: 13, color: '#6b7280' }}>Non-POS</span>
              <InputNumber size="small" min={0} disabled={readOnly} value={pendingCash} onChange={v => setPendingCash(v ?? 0)} style={{ width: 70, fontSize: 16 }} />
              <Button size="small" type="primary" disabled={readOnly} onClick={confirmAdd}>Add</Button>
              <Button size="small" disabled={busy} onClick={() => { setPendingAdd(null); setAddSearch(''); setAddOptions([]) }}>✕</Button>
            </div>
          )}
        </div>
      ) : (
        /* Desktop: original header */
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20, gap: 12 }}>
          <div>
            <Title level={4} style={{ margin: 0 }}>Report date</Title>
            <Text style={{ color: '#6b7280' }}>Review quantities, then save each changed row.</Text>
          </div>
          <Space wrap size={[8, 8]}>
            <Button disabled={busy || editing || !!pendingAdd} onClick={loadSales}>Refresh</Button>
            <DatePicker
              value={date}
                disabled={busy}
              onChange={d => setDate(d ?? dayjs())}
              allowClear={false}
              style={{ width: 140 }}
              onPanelChange={(val) => fetchRecordedDates(val.format('YYYY-MM'))}
              cellRender={(current, info) => {
                if (info.type !== 'date') return info.originNode
                const ds = (current as Dayjs).format('YYYY-MM-DD')
                const hasData = recordedDates.has(ds)
                return (
                  <div className="ant-picker-cell-inner" style={{ position: 'relative' }}>
                    {(current as Dayjs).date()}
                    {hasData && (
                      <span style={{
                        position: 'absolute', bottom: 2, left: '50%', transform: 'translateX(-50%)',
                        width: 5, height: 5, borderRadius: '50%', background: '#10B981', display: 'block',
                      }} />
                    )}
                  </div>
                )
              }}
            />
            {!isAll && (
              <RoleGuard minRole="staff">
              <AutoComplete
                  aria-label="Add historical product"
                  disabled={readOnly || editing}
                placeholder="Search & add product..."
                value={addSearch}
                options={addOptions}
                onSearch={searchToAdd}
                onSelect={(val, opt) => {
                  searchRequestRef.current += 1
                    setPendingAdd({ id: Number(val), label: opt.label as string })
                  setPendingPos(0); setPendingCash(0)
                  setAddSearch(opt.label as string); setAddOptions([])
                }}
                onClear={() => { searchRequestRef.current += 1; setAddSearch(''); setAddOptions([]); setPendingAdd(null) }}
                allowClear
                style={{ width: 240 }}
              />
              {pendingAdd && (
                <Space size={4} style={{ background: '#f0f4ff', borderRadius: 6, padding: '4px 10px' }}>
                  <span style={{ fontSize: 12, color: '#6366F1', fontWeight: 500, maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {pendingAdd.label}
                  </span>
                  <span style={{ fontSize: 12, color: '#6b7280' }}>POS</span>
                  <InputNumber size="small" min={0} disabled={readOnly} value={pendingPos} onChange={v => setPendingPos(v ?? 0)} style={{ width: 60 }} />
                  <span style={{ fontSize: 12, color: '#6b7280' }}>Non-POS</span>
                  <InputNumber size="small" min={0} disabled={readOnly} value={pendingCash} onChange={v => setPendingCash(v ?? 0)} style={{ width: 60 }} />
                  <Button size="small" type="primary" disabled={readOnly} onClick={confirmAdd}>Add</Button>
                  <Button size="small" disabled={busy} onClick={() => { setPendingAdd(null); setAddSearch(''); setAddOptions([]) }}>✕</Button>
                </Space>
              )}
            </RoleGuard>
            )}
          </Space>
        </div>
      )}

      {isAll && (
        <div style={{
          background: '#fffbeb', border: '1px solid #f59e0b', borderRadius: 8,
          padding: '10px 16px', marginBottom: 16, color: '#92400e', fontSize: 13,
        }}>
          Viewing all stores combined. Select a store to add or edit sales.
        </div>
      )}

      {!!estimate.unknownRows && <p>{estimate.unknownRows} {estimate.unknownRows === 1 ? 'product has' : 'products have'} no saved price. The estimate includes only products with a saved price.</p>}
      {/* Stat cards — 3-col KPI strip on mobile, 4-col on desktop */}
      <Row gutter={[isMobile ? 8 : 16, isMobile ? 8 : 16]} style={{ marginBottom: isMobile ? 16 : 20 }}>
        {isMobile ? (
          // 3-column KPI strip on mobile: Revenue, Units, POS
          <>
            {[
              { label: 'Saved-price estimate', value: estimateValue, color: '#6366F1' },
              { label: 'Units Sold', value: totalSold,                       color: '#10B981' },
              { label: 'POS / Non-POS', value: `${totalPos} / ${totalCash}`,   color: '#f59e0b' },
            ].map(c => (
              <Col key={c.label} xs={8}>
                <Card style={{ borderRadius: 10, borderTop: `3px solid ${c.color}` }} bodyStyle={{ padding: '10px 12px' }}>
                  <div style={{ fontSize: 10, color: '#9ca3af' }}>{c.label}</div>
                  <div style={{ fontSize: 16, fontWeight: 700, color: c.color, marginTop: 2 }}>{c.value}</div>
                </Card>
              </Col>
            ))}
          </>
        ) : (
          // 4-column on desktop
          <>
            {[
              { label: 'Saved-price estimate', value: estimateValue, color: '#6366F1' },
              { label: 'Units Sold',     value: totalSold,                       color: '#10B981' },
              { label: 'POS Sales',      value: `${totalPos} units`,             color: '#6366F1' },
              { label: 'Non-POS Sales',     value: `${totalCash} units`,            color: '#10B981' },
            ].map(c => (
              <Col key={c.label} xs={12} sm={6}>
                <Card style={{ borderRadius: 10, borderTop: `3px solid ${c.color}` }} bodyStyle={{ padding: '14px 20px' }}>
                  <div style={{ fontSize: 12, color: '#9ca3af' }}>{c.label}</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: c.color, marginTop: 4 }}>{c.value}</div>
                </Card>
              </Col>
            ))}
          </>
        )}
      </Row>

      {!isAll && metadataScope === `${sc}:${dateStr}` && (
        metadataError ? (
          <Alert type="warning" showIcon style={{ marginBottom: 16 }}
            message="Unable to load physical cash and report notes."
            action={<Button size="small" onClick={loadSales}>Retry</Button>} />
        ) : reportMetadata && (
          <Card size="small" title={`Report notes — ${dateStr} · ${sc}`} style={{ marginBottom: 16 }}>
            <Space wrap>
              <Text>Physical cash actual: {reportMetadata.cash_actual == null ? '—' : `CA$${reportMetadata.cash_actual.toFixed(2)}`}</Text>
              <Text>Expected: {reportMetadata.cash_expected == null ? '—' : `CA$${reportMetadata.cash_expected.toFixed(2)}`}</Text>
              <Text>Difference (actual − expected): {(() => {
                const difference = reportMetadata.cash_difference ?? cashDifference(reportMetadata.cash_actual, reportMetadata.cash_expected)
                return difference == null ? '—' : `CA$${difference.toFixed(2)}`
              })()}</Text>
            </Space>
            <div style={{ marginTop: 8, color: '#6b7280' }}>
              Non-POS includes cash, e-transfer, WeChat and Alipay; tender is unspecified per row.
            </div>
            {REPORT_NOTE_SECTIONS.map(({ key, label, description }) => !!reportMetadata[key]?.length && (
              <div key={key} style={{ marginTop: 8 }}>
                <Text strong>{label}</Text>
                <div>{description}</div>
                <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{reportMetadata[key]!.join('\n')}</div>
              </div>
            ))}
          </Card>
        )
      )}

      {/* Charts */}
      <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
        <Col xs={24} lg={12}>
          <Card title="Latest 7 recorded dates" style={{ borderRadius: 10 }} bodyStyle={{ padding: '12px 16px 8px' }}>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={weeklyData} margin={{ top: 0, right: 10, left: -10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} />
                <RechartTooltip />
                <Bar dataKey="UnitsSold" fill="#6366F1" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title={`Top Products — ${date.format('MMM D')}`} style={{ borderRadius: 10 }} bodyStyle={{ padding: '12px 16px 8px' }}>
            <ResponsiveContainer width="100%" height={200}>
              <HBarChart data={topProducts} layout="vertical" margin={{ top: 0, right: 10, left: 60, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 10 }} />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={60} />
                <RechartTooltip />
                <Bar dataKey="POS"  fill="#6366F1" radius={[0, 4, 4, 0]} stackId="a" />
                <Bar dataKey="Non-POS" fill="#10B981" radius={[0, 4, 4, 0]} stackId="a" />
              </HBarChart>
            </ResponsiveContainer>
          </Card>
        </Col>
      </Row>

      {/* Daily Report Entry — shown after confirming no saved report, or on explicit re-import */}
      {loadError && (
        <Alert
          role="alert"
          type={lastSuccess ? 'warning' : 'error'}
          showIcon
          message={lastSuccess ? 'Showing previously loaded sales data' : loadError}
          description={lastSuccess ? `Last updated ${lastSuccess.toLocaleTimeString()}` : undefined}
          action={<Button onClick={loadSales}>Retry</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {!isAll && !loadError && (importMode || (!loading && metadataLoaded && !hasSavedReport)) && (
        <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)', padding: '20px 20px', marginBottom: 20 }}>
          {importMode && hasSavedReport && (
            <div style={{ marginBottom: 12 }}>
              <Button size="small" onClick={() => setImportMode(false)}>← Back to historical report</Button>
            </div>
          )}
          <DailyReportEntry
            key={`${dateStr}:${sc}`}
            date={dateStr}
            onComplete={(d, storeCode) => {
              setImportMode(false)
              if (d !== dateStr) setDate(dayjs(d))
              const reportStore = stores.find(store => store.code === storeCode)
              if (reportStore && storeCode !== sc) setSelectedStore(reportStore)
              if (d === dateStr && storeCode === sc) loadSales()
            }}
          />
        </div>
      )}

      {/* Sales table + log */}
      {(hasSavedReport || loading) && !importMode && (
      <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)', overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0', display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <div style={{ fontWeight: 600, color: '#111827' }}>
            Report quantities for {date.format(isMobile ? 'MMM D, YYYY' : 'dddd, MMMM D, YYYY')}
            <Text style={{ color: '#9ca3af', fontWeight: 400, fontSize: 13, marginLeft: 8 }}>
              {sales.length} products
            </Text>
          </div>
          <Space size={8}>
            {!isAll && (
              <Button size="small" disabled={busy || editing} icon={<ImportOutlined />} onClick={() => setImportMode(true)}>
                Re-import
              </Button>
            )}
            <RoleGuard minRole="manager">
              {!isAll && (
                <Popconfirm title={`Clear all sales and report notes for ${dateStr}?`} onConfirm={clearDay}>
                  <Button danger size="small" disabled={readOnly || editing}>Clear Day</Button>
                </Popconfirm>
              )}
              <Button
                size="small"
                icon={<ExportOutlined />}
                onClick={doExport}
              >
                Export
              </Button>
            </RoleGuard>
          </Space>
        </div>
        {isMobile ? (
          <Spin spinning={loading}>
            {sales.length === 0 && !loading && !loadError ? (
              <div style={{ textAlign: 'center', color: '#9ca3af', padding: '24px 16px', fontSize: 13 }}>
                No products added for this date
              </div>
            ) : sales.map(row => (
              <div key={row.id} style={{ padding: '12px 16px', borderBottom: '1px solid #f5f5f5' }}>
                {/* Product name row */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 500, fontSize: 13, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {row.jizhanming || '—'}
                    </div>
                    <div style={{ fontSize: 11, color: '#9ca3af' }}>{row.sku}</div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0, marginLeft: 8 }}>
                    <Text style={{ fontWeight: 700, fontSize: 18, color: row.qty_sold > 0 ? '#10B981' : '#d1d5db' }}>
                      {row.qty_sold}
                    </Text>
                    {!isAll && (
                      <RoleGuard minRole="manager">
                        <Popconfirm title="Delete this record?" onConfirm={() => deleteRecord(row.id)}>
                          <Button size="small" danger type="text" disabled={readOnly || editing} aria-label="Delete historical row" icon={<DeleteOutlined />} />
                        </Popconfirm>
                      </RoleGuard>
                    )}
                  </div>
                </div>
                {/* Qty inputs row */}
                <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 11, color: '#6b7280', width: 28 }}>POS</span>
                    <InputNumber
                      size="small" min={0}
                      disabled={readOnly}
                      value={localEdits[row.id]?.pos ?? row.qty_pos}
                      onChange={val => { if (!isAll) setLocalQty(row.id, 'pos', val ?? 0) }}
                      aria-label={`POS quantity for ${row.jizhanming}`}
                      onPressEnter={() => saveRow(row)}
                      style={{ width: 65 }}
                    />
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 11, color: '#6b7280', width: 50 }}>Non-POS</span>
                    <InputNumber
                      size="small" min={0}
                      disabled={readOnly}
                      value={localEdits[row.id]?.cash ?? row.qty_cash}
                      onChange={val => { if (!isAll) setLocalQty(row.id, 'cash', val ?? 0) }}
                      aria-label={`Non-POS quantity for ${row.jizhanming}`}
                      onPressEnter={() => saveRow(row)}
                      style={{ width: 65 }}
                    />
                  </div>
                  <Text style={{ fontSize: 11, color: '#6366F1', marginLeft: 'auto' }}>{savedLineValue(row) == null ? 'Price unknown' : `CA$${savedLineValue(row)!.toFixed(2)}`}</Text>
                </div>
                {rowActions(row)}
              </div>
            ))}
          </Spin>
        ) : (
          <Table
            rowKey="id"
            size="middle"
            loading={loading}
            dataSource={sales}
            columns={entryColumns}
            pagination={false}
            virtual
            scroll={{ x: 800, y: 500 }}
          />
        )}
      </div>
      )}

      {/* Sales Log — always visible */}
      <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)', marginTop: 16, overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0', display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <span style={{ fontWeight: 600, color: '#111827' }}>Sales Log</span>
          <RoleGuard minRole="manager">
            <Space wrap size={[8, 8]}>
              {!isMobile && <DatePicker value={exportFrom} onChange={d => setExportFrom(d ?? dayjs().subtract(30,'day'))} allowClear={false} style={{ width: 130 }} />}
              {!isMobile && <Text style={{ color: '#9ca3af' }}>to</Text>}
              {!isMobile && <DatePicker value={exportTo} onChange={d => setExportTo(d ?? dayjs())} allowClear={false} style={{ width: 130 }} />}
              <Button
                size="small"
                icon={<ExportOutlined />}
                onClick={doExport}
              >Export</Button>
            </Space>
          </RoleGuard>
        </div>
        <Table
          rowKey="date"
          size="middle"
          dataSource={summary}
          columns={summaryColumns}
          pagination={{ pageSize: 30, showTotal: t => `${t} days` }}
          scroll={{ x: 'max-content' }}
        />
      </div>
    </div>
  )
}
