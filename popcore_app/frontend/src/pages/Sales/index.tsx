import { useState, useEffect, useCallback, useRef } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { useRole } from '../../auth/useRole'
import { productLabel } from '../../lib/productLabel'
import { savedLineValue, savedPriceSummary } from './historicalSales'
import {
  Table, Button, Space, Tag, Popconfirm, message,
  Typography, InputNumber, Card,
  DatePicker, AutoComplete, Spin, Alert,
} from 'antd'
import {
  ExportOutlined, DeleteOutlined,
  LeftOutlined, RightOutlined, ImportOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs, { Dayjs } from 'dayjs'
import { BarChart, Bar, Cell, XAxis, Tooltip as RechartTooltip, ResponsiveContainer } from 'recharts'
import client from '../../api/client'
import type { ReportMetadata } from '../../api/matcher'
import { cashDifference, hasReportNotes, REPORT_NOTE_SECTIONS } from './dailyReportReview'
import RoleGuard from '../../components/RoleGuard'
import { useAppStore } from '../../store'
import DailyReportEntry from './DailyReportEntry'
import AliasManager from './AliasManager'
import { useIsMobile } from '../../hooks/useIsMobile'
import PageHeader from '../../components/pc/PageHeader'
import Icon from '../../components/pc/Icon'
import { SalesTabs, dayTitle } from './SalesNav'
import './historicalReports.css'

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
  const mobileTap = isMobile ? { minHeight: 44 } : undefined
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
    date: dayjs(r.date).format('D'), day: r.date,
    UnitsSold: r.total_sold,
  }))


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



  if (loadError && !lastSuccess) {
    return <Alert role="alert" type="error" showIcon message={loadError} action={<Button onClick={loadSales}>Retry</Button>} />
  }

  const daysPanel = <section className="pc-panel pc-hist-days" aria-label="Recorded dates">
    <div className="pc-panel-head"><h2><span>记录日</span> <small>Recorded dates</small></h2><span className="pc-panel-count">{summary.length} days</span></div>
    <ul>
      {summary.map(row => <li key={row.date} className={row.date === dateStr ? 'pc-hist-day-current' : undefined}>
        <button type="button" disabled={busy || editing || !!pendingAdd} aria-current={row.date === dateStr ? 'date' : undefined} onClick={() => setDate(dayjs(row.date))}>
          <span><strong>{dayTitle(row.date)}</strong><small>{row.date}</small></span><b>{row.total_sold} 件</b>
        </button>
        <Link to={`/sales/day/${row.date}`} aria-label={`Open the ${row.date} report`}><Icon name="right" size={14} /></Link>
      </li>)}
      {!summary.length && <li className="pc-quiet">No recorded dates yet.</li>}
    </ul>
    <RoleGuard minRole="manager">
      <div className="pc-hist-export">
        {!isMobile && <DatePicker aria-label="Export from" value={exportFrom} onChange={d => setExportFrom(d ?? dayjs().subtract(30,'day'))} allowClear={false} />}
        {!isMobile && <Text type="secondary">to</Text>}
        {!isMobile && <DatePicker aria-label="Export to" value={exportTo} onChange={d => setExportTo(d ?? dayjs())} allowClear={false} />}
        <Button style={mobileTap} icon={<ExportOutlined />} onClick={doExport}>Export</Button>
      </div>
    </RoleGuard>
  </section>
  const chartPanel = <section className="pc-panel pc-hist-chart" aria-label="Latest 7 recorded dates">
    <div className="pc-panel-head"><h2><span>最近 7 天</span> <small>Latest 7 recorded dates</small></h2></div>
    <div className="pc-hist-chart-body">
      <ResponsiveContainer width="100%" height={120}>
        <BarChart aria-label="Bar chart of units sold on the latest 7 recorded dates" data={weeklyData} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
          <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#9aa1ab' }} axisLine={false} tickLine={false} />
          <RechartTooltip cursor={{ fill: '#f2f3f5' }} />
          <Bar dataKey="UnitsSold" name="Units sold" maxBarSize={22} isAnimationActive={false}>
            {weeklyData.map(row => <Cell key={row.day} fill={row.day === dateStr ? '#1f2328' : '#dfe2e7'} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  </section>
  const headerActions = <>
    <Link className="pc-btn" to="/sales/matching">核对旧名称 Review past names</Link>
    <Button disabled={busy} aria-pressed={aliasMode} onClick={() => setAliasMode(value => !value)}>{aliasMode ? '收起名称对应 Hide name mappings' : '名称对应 Manage name mappings'}</Button>
    {!isAll && <Button type="primary" disabled={busy || editing} onClick={() => setImportMode(true)}><Icon name="plus" size={16} />导入日报 Import report</Button>}
  </>

  return (
    <div className="pc-hist">
      <PageHeader title="销售" sub={isMobile ? 'Historical reports' : `Sales · ${selectedStore?.name ?? ''} · pasted daily reports`} actions={headerActions} />
      <SalesTabs />
      <p className="pc-hist-intro">Review pasted daily summaries for {selectedStore?.name}. Saved report prices estimate product value; payment totals and refunds are in 报表 Insights.</p>
      {isMobile && <div className="pc-hist-phone-actions">{headerActions}</div>}
      {/* Alias Manager panel */}
      {aliasMode && (
        <div className="pc-panel pc-hist-aliases">
          <AliasManager onBusyChange={setAliasSaving} />
        </div>
      )}

      {mutationError && <Alert role="alert" type="error" showIcon message={mutationError} style={{marginBottom:16}} />}
      {isAll && <p className="pc-notice">Viewing all stores combined. Select a store to add or edit sales.</p>}
      <div className="pc-hist-grid">
      <aside className="pc-hist-side">{chartPanel}{!isMobile && daysPanel}</aside>
      <div className="pc-hist-main">
      {/* Date and add-product toolbar */}
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
                aria-label="Report date"
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
              aria-label="Report date"
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


      {!!estimate.unknownRows && <p className="pc-hist-unknown">{estimate.unknownRows} {estimate.unknownRows === 1 ? 'product has' : 'products have'} no saved price. The estimate includes only products with a saved price.</p>}
      <div className="pc-figures pc-figures-4">
        <div><span>件数 Units sold</span><strong>{totalSold}</strong><small>{sales.length} 款</small></div>
        <div><span>POS 件数</span><strong>{totalPos}</strong><small>卡/现金</small></div>
        <div><span>非 POS 件数 Non-POS</span><strong>{totalCash}</strong><small>电子支付</small></div>
        <div><span>估值 <span>Saved-price estimate</span></span><strong>{estimateValue}</strong><small>按保存的价格估算</small></div>
      </div>

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
        <div className="pc-panel pc-hist-entry">
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
      <section className="pc-panel pc-hist-log" aria-label="Sales log">
        <div className="pc-panel-head">
          <h2><span>销售明细</span> <small>Report quantities for {date.format(isMobile ? 'MMM D, YYYY' : 'dddd, MMMM D, YYYY')} · {sales.length} products</small></h2>
          <Space size={8}>
            {!isAll && (
              <Button size="small" style={mobileTap} disabled={busy || editing} icon={<ImportOutlined />} onClick={() => setImportMode(true)}>
                Re-import
              </Button>
            )}
            <RoleGuard minRole="manager">
              {!isAll && (
                <Popconfirm title={`Clear all sales and report notes for ${dateStr}?`} onConfirm={clearDay}>
                  <Button danger size="small" style={mobileTap} disabled={readOnly || editing}>Clear Day</Button>
                </Popconfirm>
              )}
              <Button
                size="small"
                style={mobileTap}
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
      </section>
      )}

      {isMobile && daysPanel}
      <p className="pc-hist-note">The estimate uses saved prices, not money collected. Payment totals and refunds are in Insights.</p>
      </div>
      </div>
    </div>
  )
}
