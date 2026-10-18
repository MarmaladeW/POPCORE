import { useState } from 'react'
import { Modal, Input, Button, Table, List, Tag, Space, Alert, Typography, Select, Checkbox, Segmented, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'
import { newRequestKey } from '../../api/salesDocuments'
import { useAppStore } from '../../store'
import { useIsMobile } from '../../hooks/useIsMobile'

type Status = 'update' | 'unchanged' | 'skipped' | 'rejected'

interface Identity {
  series_id: number | null
  series_name: string | null
  stock_form: string | null
  stock_unit: string | null
  design_name: string | null
  identity_status: string
  new_series?: boolean
}

export interface IdentityRow {
  line: number
  product_id: number | null
  sku: string
  name: string | null
  status: Status
  reason: string | null
  code: string | null
  before: Identity | null
  values: Identity | null
}

interface IdentityPreview {
  rows: IdentityRow[]
  update: number
  unchanged: number
  skipped: number
  rejected: number
  new_series: string[]
}

const STATUS: Record<Status, { label: string; color: string }> = {
  update: { label: 'Verify', color: 'green' },
  unchanged: { label: 'Unchanged', color: 'default' },
  skipped: { label: 'Skipped', color: 'default' },
  rejected: { label: 'Rejected', color: 'red' },
}
const SHEET_HEADER = 'product_id,SKU,name,记账名,legacy_series,series_id,series,stock_form,stock_unit,design_name,identity_status'

export function IdentityExportModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const stores = useAppStore(state => state.stores)
  const [storeCode, setStoreCode] = useState('')
  const [unverified, setUnverified] = useState(true)
  const [pending, setPending] = useState(false)

  async function download() {
    setPending(true)
    try {
      const params = new URLSearchParams()
      if (storeCode) params.set('store_code', storeCode)
      if (unverified) params.set('unverified', '1')
      const res = await client.get(`/products/identity-sheet?${params}`, { responseType: 'blob' })
      const url = window.URL.createObjectURL(new Blob([res.data], { type: 'text/csv' }))
      const a = document.createElement('a')
      a.href = url
      a.download = ['identity_sheet', storeCode || 'all', ...(unverified ? ['unverified'] : []),
        new Date().toISOString().slice(0, 10)].join('_') + '.csv'
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(url)
      onClose()
    } catch {
      message.error('Identity sheet export failed — please try again')
    } finally {
      setPending(false)
    }
  }

  return <Modal title="Export identity sheet" open={open} footer={null} onCancel={() => { if (!pending) onClose() }}>
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Typography.Text type="secondary">
        A CSV of each product's inventory identity for review. Fill in series, stock_form and design_name, then use Import identity sheet.
      </Typography.Text>
      <label style={{ display: 'grid', gap: 4 }}>
        <span>Products</span>
        <Select aria-label="Identity sheet products" value={storeCode} onChange={setStoreCode} style={{ width: '100%' }}
          options={[{ value: '', label: 'All products' },
            ...stores.filter(s => s.code !== 'ALL').map(s => ({ value: s.code, label: `With legacy stock at ${s.code} (${s.name})` }))]} />
      </label>
      <Checkbox checked={unverified} onChange={event => setUnverified(event.target.checked)}>Unverified products only</Checkbox>
      <Button type="primary" loading={pending} onClick={download} style={{ minHeight: 44 }}>Download identity sheet</Button>
    </Space>
  </Modal>
}

function describe(identity: Identity | null) {
  if (!identity) return '—'
  const series = identity.series_name ? `${identity.series_name}${identity.new_series ? ' (new)' : ''}` : 'No series'
  return [series, identity.stock_form ?? 'no form', identity.stock_unit ?? 'no unit', identity.design_name]
    .filter(Boolean).join(' · ')
}

function IdentityCell({ row }: { row: IdentityRow }) {
  return <span style={{ overflowWrap: 'anywhere' }}>
    {row.status === 'update' && <><Typography.Text delete type="secondary">{describe(row.before)}</Typography.Text>{' → '}</>}
    {row.status === 'rejected' || row.status === 'skipped'
      ? <Typography.Text type={row.status === 'rejected' ? 'danger' : 'secondary'}>{row.reason}</Typography.Text>
      : describe(row.values ?? row.before)}
  </span>
}

export function IdentityImportModal({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const isMobile = useIsMobile()
  const [text, setText] = useState('')
  const [preview, setPreview] = useState<IdentityPreview | null>(null)
  const [requestKey, setRequestKey] = useState('')
  const [filter, setFilter] = useState<'all' | Status>('all')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [uncertain, setUncertain] = useState(false)

  function reset() { setText(''); setPreview(null); setError(''); setUncertain(false); setFilter('all') }

  async function readFile(file: File | undefined) {
    if (!file) return
    setText(await file.text())
    setPreview(null)
  }

  async function handlePreview() {
    if (pending || !text.trim()) return
    setPending(true)
    setError('')
    setPreview(null)
    setUncertain(false)
    try {
      const response = await client.post<IdentityPreview>('/products/identity-import/preview', { text })
      setPreview(response.data)
      setRequestKey(newRequestKey())
      setFilter(response.data.rejected ? 'rejected' : 'all')
    } catch (err: any) {
      setError(err._serverMessage || 'Unable to preview the identity sheet. Check the headers and retry.')
    } finally {
      setPending(false)
    }
  }

  async function handleConfirm() {
    const rows = preview?.rows.filter(row => row.status === 'update') ?? []
    if (pending || !rows.length) return
    setPending(true)
    setError('')
    try {
      const response = await client.post<{ ok: boolean; updated: number; unchanged: number; series_created: string[] }>(
        '/products/identity-import/confirm', { rows }, { headers: { 'Idempotency-Key': requestKey } },
      )
      if (!response.data.ok) throw new Error('Identity import was not confirmed')
      const created = response.data.series_created.length ? `, ${response.data.series_created.length} series created` : ''
      message.success(`Identity verified for ${response.data.updated} products${created}`)
      reset()
      onDone()
    } catch (err: any) {
      if (!err.response) {
        // The server may have saved it; retrying the same key returns the stored result.
        setUncertain(true)
        setError('Unable to confirm this import. Retry to check its result.')
      } else {
        setError((err._serverMessage || 'Identity import could not be confirmed.') + ' Nothing was changed. Preview again before applying.')
        setPreview(null)
      }
    } finally {
      setPending(false)
    }
  }

  const columns: ColumnsType<IdentityRow> = [
    { title: 'Row', dataIndex: 'line', width: 56 },
    { title: 'Product', width: 200, render: (_, row) => <span style={{ overflowWrap: 'anywhere' }}>
      <div style={{ fontFamily: 'monospace', fontSize: 12 }}>{row.sku || '—'}</div>
      {row.name && <div style={{ fontSize: 12, color: '#596273' }}>{row.name}</div>}
    </span> },
    { title: 'Result', dataIndex: 'status', width: 100,
      render: (status: Status) => <Tag color={STATUS[status].color}>{STATUS[status].label}</Tag> },
    { title: 'Identity', width: 320, render: (_, row) => <IdentityCell row={row} /> },
  ]

  const visible = preview ? preview.rows.filter(row => filter === 'all' || row.status === filter) : []

  return <Modal title="Import identity sheet" open={open} footer={null}
    width={900} closable={!pending} maskClosable={!pending} keyboard={!pending}
    onCancel={() => { if (!pending) { reset(); onClose() } }}>
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert type="info" showIcon message="Verify inventory identity in bulk from the exported identity sheet."
        description={<>
          <div>Rows are matched by product_id or exact SKU. Every row with identity entered is marked verified; remove or blank rows you have not reviewed. A blank stock_unit takes the unit of its stock_form. A series that does not exist yet is created under the exact name. name, 记账名, legacy_series and identity_status are for reference and are not imported. Stock is never changed.</div>
          <div style={{ overflowWrap: 'anywhere', marginTop: 8 }}>{SHEET_HEADER}</div>
        </>} />
      {error && <Alert role="alert" type="error" showIcon message={error} />}
      {!preview ? <>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span>Identity sheet file</span>
          <input type="file" accept=".csv,.tsv,.txt,text/csv" disabled={pending}
            onChange={event => { readFile(event.target.files?.[0]); event.target.value = '' }} />
        </label>
        <Input.TextArea aria-label="Identity sheet CSV or TSV" rows={9} value={text} disabled={pending}
          placeholder={'product_id,SKU,series,stock_form,stock_unit,design_name\n12,SP00012,Example Series,random_box,box,'}
          onChange={event => setText(event.target.value)} style={{ fontFamily: 'monospace', fontSize: 16 }} />
        <Button type="primary" loading={pending} disabled={pending || !text.trim()}
          onClick={handlePreview} style={{ minHeight: 44 }}>Preview identity import</Button>
      </> : <>
        <Typography.Text>
          Review before applying: {preview.update} to verify · {preview.unchanged} unchanged · {preview.skipped} skipped · {preview.rejected} rejected
        </Typography.Text>
        {preview.new_series.length > 0 && <Alert type="warning" showIcon
          message={`Creates ${preview.new_series.length} new series: ${preview.new_series.join(', ')}`}
          description="Check the spelling. Existing series are matched only by exact name." />}
        <Segmented<'all' | Status> aria-label="Filter identity rows" value={filter} onChange={setFilter}
          options={[{ value: 'all', label: `All (${preview.rows.length})` },
            { value: 'update', label: `Verify (${preview.update})` },
            { value: 'rejected', label: `Rejected (${preview.rejected})` },
            { value: 'unchanged', label: `Unchanged (${preview.unchanged})` },
            { value: 'skipped', label: `Skipped (${preview.skipped})` }]}
          style={{ maxWidth: '100%', overflowX: 'auto' }} />
        {isMobile
          ? <List<IdentityRow> size="small" dataSource={visible} rowKey="line"
            pagination={{ pageSize: 20, showSizeChanger: false, size: 'small' }}
            renderItem={row => <List.Item className="pc-identity-row" style={{ display: 'block' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ fontFamily: 'monospace', fontSize: 12, overflowWrap: 'anywhere' }}>Row {row.line} · {row.sku || '—'}</span>
                <Tag color={STATUS[row.status].color} style={{ marginInlineEnd: 0 }}>{STATUS[row.status].label}</Tag>
              </div>
              {row.name && <div style={{ fontSize: 12, color: '#596273', overflowWrap: 'anywhere' }}>{row.name}</div>}
              <div style={{ marginTop: 4 }}><IdentityCell row={row} /></div>
            </List.Item>} />
          : <Table<IdentityRow> rowKey="line" size="small" dataSource={visible} columns={columns}
            pagination={{ pageSize: 20, showSizeChanger: false }} scroll={{ x: 676 }} />}
        <Space wrap>
          <Button disabled={pending || uncertain} onClick={() => { setPreview(null); setError('') }} style={{ minHeight: 44 }}>Edit sheet</Button>
          <Button type="primary" loading={pending} disabled={pending || !preview.update}
            onClick={handleConfirm} style={{ minHeight: 44 }}>
            {uncertain ? 'Retry' : `Verify ${preview.update} products`}
          </Button>
        </Space>
      </>}
    </Space>
  </Modal>
}
