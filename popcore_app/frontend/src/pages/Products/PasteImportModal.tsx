import { useState } from 'react'
import {
  Modal, Steps, Input, Button, Table, Select, Tag, Space,
  message, Alert, DatePicker, InputNumber, AutoComplete, Progress,
  Tooltip, Typography, Divider,
} from 'antd'
import {
  CheckCircleOutlined, WarningOutlined, DeleteOutlined,
  ArrowRightOutlined, ImportOutlined,
} from '@ant-design/icons'
import dayjs, { Dayjs } from 'dayjs'
import client from '../../api/client'

const { Text } = Typography

interface ParsedItem {
  rawName: string
  qty: number
  notes: string
}

interface MatchedItem extends ParsedItem {
  _key: string
  product_id?: number
  sku?: string
  jizhanming?: string
  candidates?: any[]
  status: 'matched' | 'fuzzy' | 'unmatched'
}

interface Props {
  open: boolean
  onClose: () => void
  onDone: () => void
}

/** Parses tab-sep, space-sep, and "名称2端" formats */
function parseLine(line: string): ParsedItem {
  const t = line.trim()
  if (t.includes('\t')) {
    const parts = t.split('\t').map(s => s.trim())
    return { rawName: parts[0], qty: Math.abs(parseInt(parts[1] || '1', 10)) || 1, notes: parts.slice(2).join(' ') }
  }
  const glued = t.match(/^(.*\D)(\d+)[端个盒箱]?\s*(.*)$/)
  if (glued && glued[1].trim()) {
    return { rawName: glued[1].trim(), qty: parseInt(glued[2], 10) || 1, notes: glued[3].trim() }
  }
  const parts = t.split(/\s+/)
  return { rawName: parts[0], qty: Math.abs(parseInt(parts[1] || '1', 10)) || 1, notes: parts.slice(2).join(' ') }
}

function parsePasteText(text: string): ParsedItem[] {
  return text.split('\n').map(l => l.trim()).filter(Boolean).map(parseLine)
}

async function matchName(name: string): Promise<{ candidates: any[]; status: 'matched' | 'fuzzy' | 'unmatched' }> {
  try {
    const r = await client.get('/products/by_jizhanming', { params: { name } })
    if (r.data.length === 1) return { candidates: r.data, status: 'matched' }
    if (r.data.length > 1)  return { candidates: r.data, status: 'fuzzy' }
  } catch { /* fall through */ }
  try {
    const r2 = await client.get('/products/search', { params: { q: name, limit: 6 } })
    if (r2.data.length > 0) return { candidates: r2.data, status: 'fuzzy' }
  } catch { /* fall through */ }
  return { candidates: [], status: 'unmatched' }
}

function ProductPicker({ onSelect }: { onSelect: (p: any) => void }) {
  const [opts, setOpts] = useState<any[]>([])
  async function search(q: string) {
    if (!q) { setOpts([]); return }
    const r = await client.get('/products/search', { params: { q, limit: 8 } })
    setOpts(r.data.map((p: any) => ({ value: String(p.id), label: `${p.jizhanming} (${p.sku})`, product: p })))
  }
  return (
    <AutoComplete
      size="small"
      style={{ width: 200 }}
      placeholder="Search product..."
      options={opts}
      onSearch={search}
      onSelect={(_, opt) => onSelect(opt.product)}
    />
  )
}

const OPERATION_OPTIONS = [
  { value: 'restock_upstairs', label: 'Restock → Upstairs (入库楼上)' },
  { value: 'ru_dian',          label: 'Move → In-Store (楼上→店内)' },
]

export default function PasteImportModal({ open, onClose, onDone }: Props) {
  const [step,      setStep]     = useState(0)
  const [operation, setOp]       = useState<'ru_dian' | 'restock_upstairs'>('restock_upstairs')
  const [date,      setDate]     = useState<Dayjs>(dayjs())
  const [pasteText, setPasteText] = useState('')
  const [items,     setItems]    = useState<MatchedItem[]>([])
  const [progress,  setProgress] = useState(0)
  const [matching,  setMatching] = useState(false)
  const [submitting, setSub]     = useState(false)
  const [results,   setResults]  = useState<any[]>([])

  function reset() {
    setStep(0); setPasteText(''); setItems([]); setResults([]); setProgress(0)
  }

  async function handleMatch() {
    const parsed = parsePasteText(pasteText)
    if (!parsed.length) { message.warning('Paste some items first'); return }
    setMatching(true)
    setProgress(0)
    let done = 0
    const matched: MatchedItem[] = await Promise.all(
      parsed.map(async (p, i) => {
        const result = await matchName(p.rawName)
        done++
        setProgress(Math.round((done / parsed.length) * 100))
        const top = result.candidates[0]
        return {
          ...p,
          _key: `${i}-${p.rawName}`,
          product_id: top?.id,
          sku: top?.sku,
          jizhanming: top?.jizhanming,
          candidates: result.candidates.length > 1 ? result.candidates : undefined,
          status: result.status,
        }
      })
    )
    setMatching(false)
    setItems(matched)
    setStep(1)
  }

  function updateItem(key: string, patch: Partial<MatchedItem>) {
    setItems(prev => prev.map(it => it._key === key ? { ...it, ...patch } : it))
  }

  function removeItem(key: string) {
    setItems(prev => prev.filter(it => it._key !== key))
  }

  async function handleSubmit() {
    const toSubmit = items.filter(i => i.product_id)
    if (!toSubmit.length) { message.warning('No matched items to submit'); return }
    setSub(true)
    try {
      const resp = await client.post('/stock/batch_operation', {
        operation,
        date: date.format('YYYY-MM-DD'),
        items: toSubmit.map(i => ({ product_id: i.product_id, qty: i.qty, notes: i.notes })),
      })
      setResults(resp.data.results || [])
      setStep(2)
    } catch (err: any) {
      const msg = err?.response?.data?.error ?? 'Submit failed'
      message.error(msg)
      if (err?.response?.status === 403) {
        message.error('Insufficient permissions — requires Staff role or above')
      }
    } finally {
      setSub(false)
    }
  }

  const okCount        = items.filter(i => i.product_id).length
  const unmatchedCount = items.filter(i => i.status === 'unmatched').length
  const fuzzyCount     = items.filter(i => i.status === 'fuzzy').length

  const reviewColumns = [
    {
      title: 'Pasted Name',
      dataIndex: 'rawName',
      width: 120,
      render: (v: string, r: MatchedItem) => (
        <Text
          style={{
            fontSize: 12,
            color: r.status === 'unmatched' ? '#ef4444' : '#374151',
            fontWeight: r.status === 'unmatched' ? 600 : 400,
          }}
        >
          {v}
        </Text>
      ),
    },
    {
      title: 'Matched Product',
      key: 'match',
      width: 220,
      render: (_: any, r: MatchedItem) => {
        if (r.status === 'unmatched') {
          return (
            <Space size={4}>
              <Tag color="red" style={{ fontSize: 10 }}>No match</Tag>
              <ProductPicker onSelect={p => updateItem(r._key, {
                product_id: p.id, sku: p.sku, jizhanming: p.jizhanming,
                status: 'matched', candidates: undefined,
              })} />
            </Space>
          )
        }
        if (r.candidates && r.candidates.length > 1) {
          return (
            <Select
              size="small"
              value={r.product_id}
              style={{ width: 210 }}
              onChange={v => {
                const cand = r.candidates!.find(c => c.id === v)
                updateItem(r._key, { product_id: v, jizhanming: cand?.jizhanming, sku: cand?.sku, status: 'matched' })
              }}
              options={r.candidates.map(c => ({ value: c.id, label: `${c.jizhanming} (${c.sku})` }))}
            />
          )
        }
        return (
          <Space size={4}>
            <CheckCircleOutlined style={{ color: '#10b981', fontSize: 13 }} />
            <Text style={{ fontSize: 12 }}>{r.jizhanming}</Text>
            <Text style={{ fontSize: 11, color: '#9ca3af', fontFamily: 'monospace' }}>{r.sku}</Text>
          </Space>
        )
      },
    },
    {
      title: 'Qty (端)',
      key: 'qty',
      width: 85,
      render: (_: any, r: MatchedItem) => (
        <InputNumber
          size="small"
          min={1}
          value={r.qty}
          onChange={v => updateItem(r._key, { qty: v ?? 1 })}
          style={{ width: 70 }}
        />
      ),
    },
    {
      title: 'Notes',
      dataIndex: 'notes',
      render: (v: string, r: MatchedItem) => (
        <Input
          size="small"
          value={v}
          onChange={e => updateItem(r._key, { notes: e.target.value })}
          placeholder="optional"
        />
      ),
    },
    {
      title: '',
      key: 'del',
      width: 36,
      render: (_: any, r: MatchedItem) => (
        <Tooltip title="Remove row">
          <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeItem(r._key)} />
        </Tooltip>
      ),
    },
  ]

  return (
    <Modal
      title={
        <Space>
          <ImportOutlined style={{ color: '#6366F1' }} />
          <span style={{ fontWeight: 700 }}>Paste Import — Stock</span>
        </Space>
      }
      open={open}
      onCancel={() => { reset(); onClose() }}
      footer={null}
      width={800}
      destroyOnClose
    >
      <Steps
        current={step}
        size="small"
        style={{ margin: '16px 0' }}
        items={[
          { title: 'Paste Data' },
          { title: 'Review & Match' },
          { title: 'Done' },
        ]}
      />

      {/* ── Step 0: Paste ── */}
      {step === 0 && (
        <div>
          <div style={{ display: 'flex', gap: 12, marginBottom: 14 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 4 }}>Operation</div>
              <Select
                value={operation}
                onChange={setOp}
                options={OPERATION_OPTIONS}
                style={{ width: '100%' }}
              />
            </div>
            <div>
              <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 4 }}>Date</div>
              <DatePicker
                value={date}
                onChange={d => setDate(d ?? dayjs())}
                allowClear={false}
                style={{ width: 150 }}
              />
            </div>
          </div>

          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 10 }}
            message={
              <div style={{ fontSize: 12 }}>
                <strong>Supported formats</strong> — one item per line:
                <div style={{ fontFamily: 'monospace', marginTop: 4, lineHeight: 2, color: '#374151' }}>
                  <div><span style={{ color: '#6366F1' }}>记账名{'  '}qty{'  '}[note]</span><span style={{ color: '#9ca3af' }}> ← space-separated</span></div>
                  <div><span style={{ color: '#6366F1' }}>记账名{'\t'}qty{'\t'}[note]</span><span style={{ color: '#9ca3af' }}> ← Excel paste</span></div>
                  <div><span style={{ color: '#6366F1' }}>记账名2端</span><span style={{ color: '#9ca3af' }}> ← number glued to name</span></div>
                </div>
              </div>
            }
          />

          <Input.TextArea
            rows={10}
            placeholder={'Dimoo花花 2\nSA草莓 1 damaged\nMolly精灵3端'}
            value={pasteText}
            onChange={e => setPasteText(e.target.value)}
            style={{ fontFamily: 'monospace', fontSize: 13, marginBottom: 12 }}
          />

          {matching && (
            <Progress percent={progress} style={{ marginBottom: 8 }} status="active" />
          )}

          <Button
            type="primary"
            icon={<ArrowRightOutlined />}
            loading={matching}
            onClick={handleMatch}
            disabled={!pasteText.trim()}
          >
            Match Products
          </Button>
        </div>
      )}

      {/* ── Step 1: Review ── */}
      {step === 1 && (
        <div>
          {/* Summary row */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
            <Tag color="green" style={{ fontSize: 12, padding: '2px 8px' }}>
              <CheckCircleOutlined /> {okCount} matched
            </Tag>
            {fuzzyCount > 0 && (
              <Tag color="blue" style={{ fontSize: 12, padding: '2px 8px' }}>
                {fuzzyCount} need selection
              </Tag>
            )}
            {unmatchedCount > 0 && (
              <Tag color="red" style={{ fontSize: 12, padding: '2px 8px' }}>
                <WarningOutlined /> {unmatchedCount} unmatched — search manually or remove
              </Tag>
            )}
          </div>

          <Table
            size="small"
            rowKey="_key"
            dataSource={items}
            columns={reviewColumns}
            pagination={false}
            scroll={{ y: 320 }}
            rowClassName={r =>
              r.status === 'unmatched'
                ? 'unmatched-row'
                : r.status === 'fuzzy'
                ? 'fuzzy-row'
                : ''
            }
          />

          <Divider style={{ margin: '12px 0' }} />

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Button onClick={() => setStep(0)}>← Back</Button>
            <Space>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {okCount > 0 ? `${okCount} item${okCount !== 1 ? 's' : ''} will be submitted` : 'No items ready to submit'}
              </Text>
              <Button
                type="primary"
                loading={submitting}
                onClick={handleSubmit}
                disabled={!okCount}
                icon={<ImportOutlined />}
              >
                Submit {okCount > 0 ? `(${okCount})` : ''}
              </Button>
            </Space>
          </div>
        </div>
      )}

      {/* ── Step 2: Done ── */}
      {step === 2 && (
        <div>
          <Alert
            type="success"
            icon={<CheckCircleOutlined />}
            showIcon
            message={`Import complete — ${results.filter(r => r.ok).length} succeeded, ${results.filter(r => !r.ok).length} failed`}
            style={{ marginBottom: 12 }}
          />
          {results.filter(r => !r.ok).map((r, i) => (
            <Alert
              key={i}
              type="error"
              message={`Product ${r.pid}: ${r.error}`}
              style={{ marginBottom: 4 }}
            />
          ))}
          <div style={{ marginTop: 16 }}>
            <Button type="primary" onClick={() => { reset(); onDone() }}>
              Done
            </Button>
          </div>
        </div>
      )}
    </Modal>
  )
}
