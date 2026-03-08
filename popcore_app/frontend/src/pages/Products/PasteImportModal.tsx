import { useState } from 'react'
import {
  Modal, Steps, Input, Button, Table, Select, Tag, Space,
  message, Alert, DatePicker, InputNumber, AutoComplete, Progress, Tooltip,
} from 'antd'
import { CheckCircleOutlined, WarningOutlined, DeleteOutlined } from '@ant-design/icons'
import dayjs, { Dayjs } from 'dayjs'
import client from '../../api/client'

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

/** Handles tab-sep, space-sep, and "名称2端" (name glued to number) formats */
function parseLine(line: string): ParsedItem {
  const t = line.trim()

  // Tab-separated (Excel/spreadsheet)
  if (t.includes('\t')) {
    const parts = t.split('\t').map(s => s.trim())
    return { rawName: parts[0], qty: Math.abs(parseInt(parts[1] || '1', 10)) || 1, notes: parts.slice(2).join(' ') }
  }

  // Number glued to end of name, optionally followed by 端/个/盒
  const glued = t.match(/^(.*\D)(\d+)[端个盒箱]?\s*(.*)$/)
  if (glued && glued[1].trim()) {
    return { rawName: glued[1].trim(), qty: parseInt(glued[2], 10) || 1, notes: glued[3].trim() }
  }

  // Standard space-sep
  const parts = t.split(/\s+/)
  return { rawName: parts[0], qty: Math.abs(parseInt(parts[1] || '1', 10)) || 1, notes: parts.slice(2).join(' ') }
}

function parsePasteText(text: string): ParsedItem[] {
  return text.split('\n').map(l => l.trim()).filter(Boolean).map(parseLine)
}

/** Search products by name — tries by_jizhanming first, falls back to search */
async function matchName(name: string): Promise<{ candidates: any[]; status: 'matched' | 'fuzzy' | 'unmatched' }> {
  try {
    const r = await client.get('/products/by_jizhanming', { params: { name } })
    if (r.data.length === 1) return { candidates: r.data, status: 'matched' }
    if (r.data.length > 1)  return { candidates: r.data, status: 'fuzzy' }
  } catch { /* fall through */ }
  // Fallback: full-text search
  try {
    const r2 = await client.get('/products/search', { params: { q: name, limit: 6 } })
    if (r2.data.length > 0) return { candidates: r2.data, status: 'fuzzy' }
  } catch { /* fall through */ }
  return { candidates: [], status: 'unmatched' }
}

/** Small inline search component for manually fixing unmatched rows */
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
      style={{ width: 180 }}
      placeholder="搜索产品..."
      options={opts}
      onSearch={search}
      onSelect={(_, opt) => onSelect(opt.product)}
    />
  )
}

export default function PasteImportModal({ open, onClose, onDone }: Props) {
  const [step, setStep]         = useState(0)
  const [operation, setOp]      = useState<'ru_dian' | 'restock_upstairs'>('restock_upstairs')
  const [date, setDate]         = useState<Dayjs>(dayjs())
  const [pasteText, setPasteText] = useState('')
  const [items, setItems]       = useState<MatchedItem[]>([])
  const [progress, setProgress] = useState(0)
  const [matching, setMatching] = useState(false)
  const [submitting, setSub]    = useState(false)
  const [results, setResults]   = useState<any[]>([])

  function reset() {
    setStep(0); setPasteText(''); setItems([]); setResults([]); setProgress(0)
  }

  async function handleMatch() {
    const parsed = parsePasteText(pasteText)
    if (!parsed.length) { message.warning('请粘贴内容'); return }
    setMatching(true)
    setProgress(0)

    // Parallel matching with progress tracking
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
    if (!toSubmit.length) { message.warning('没有可提交的行'); return }
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
      message.error(err?.response?.data?.error ?? '提交失败')
    } finally {
      setSub(false)
    }
  }

  const okCount        = items.filter(i => i.product_id).length
  const unmatchedCount = items.filter(i => i.status === 'unmatched').length

  const reviewColumns = [
    {
      title: '输入名称', dataIndex: 'rawName', width: 110,
      render: (v: string, r: MatchedItem) => (
        <span style={{ color: r.status === 'unmatched' ? '#cf1322' : undefined }}>{v}</span>
      ),
    },
    {
      title: '匹配产品', key: 'match', width: 200,
      render: (_: any, r: MatchedItem) => {
        if (r.status === 'unmatched') {
          return (
            <Space size={4}>
              <Tag color="red">未匹配</Tag>
              <ProductPicker onSelect={p => updateItem(r._key, {
                product_id: p.id, sku: p.sku, jizhanming: p.jizhanming, status: 'matched', candidates: undefined,
              })} />
            </Space>
          )
        }
        if (r.candidates && r.candidates.length > 1) {
          return (
            <Select
              size="small"
              value={r.product_id}
              style={{ width: 190 }}
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
            <Tag color="green">✓</Tag>
            <span>{r.jizhanming}</span>
            <Tag>{r.sku}</Tag>
          </Space>
        )
      },
    },
    {
      title: '数量(端)', key: 'qty', width: 90,
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
      title: '备注', dataIndex: 'notes', width: 100,
      render: (v: string, r: MatchedItem) => (
        <Input
          size="small"
          value={v}
          onChange={e => updateItem(r._key, { notes: e.target.value })}
        />
      ),
    },
    {
      title: '',
      key: 'del',
      width: 40,
      render: (_: any, r: MatchedItem) => (
        <Tooltip title="移除此行">
          <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeItem(r._key)} />
        </Tooltip>
      ),
    },
  ]

  return (
    <Modal
      title="粘贴导入 — 库存"
      open={open}
      onCancel={() => { reset(); onClose() }}
      footer={null}
      width={760}
      destroyOnClose
    >
      <Steps
        current={step}
        size="small"
        style={{ marginBottom: 20 }}
        items={[{ title: '粘贴' }, { title: '确认' }, { title: '完成' }]}
      />

      {step === 0 && (
        <div>
          <Space style={{ marginBottom: 12 }}>
            <Select
              value={operation}
              onChange={setOp}
              options={[
                { value: 'restock_upstairs', label: '入库（楼上）' },
                { value: 'ru_dian',          label: '入店（楼上→店内）' },
              ]}
              style={{ width: 180 }}
            />
            <DatePicker
              value={date}
              onChange={d => setDate(d ?? dayjs())}
              allowClear={false}
              style={{ width: 150 }}
            />
          </Space>
          <Alert
            type="info"
            style={{ marginBottom: 8 }}
            message="支持格式：每行一条"
            description={
              <div style={{ fontFamily: 'monospace', fontSize: 12, lineHeight: 1.8 }}>
                {'记账名  数量  [备注]   ← 空格分隔\n'}
                {'记账名\t数量\t[备注]  ← 表格粘贴\n'}
                {'记账名2端           ← 数字紧跟名称'}
              </div>
            }
          />
          <Input.TextArea
            rows={10}
            placeholder={'Dimoo花花 2\nSA草莓 1 破损\nMolly精灵3端'}
            value={pasteText}
            onChange={e => setPasteText(e.target.value)}
            style={{ fontFamily: 'monospace', marginBottom: 12 }}
          />
          {matching && <Progress percent={progress} style={{ marginBottom: 8 }} />}
          <Button type="primary" loading={matching} onClick={handleMatch}>
            下一步 — 匹配产品
          </Button>
        </div>
      )}

      {step === 1 && (
        <div>
          {unmatchedCount > 0 && (
            <Alert
              type="warning"
              style={{ marginBottom: 12 }}
              message={`${unmatchedCount} 行未自动匹配 — 可手动搜索指定产品，或点删除按钮跳过`}
              icon={<WarningOutlined />}
              showIcon
            />
          )}
          <Table
            size="small"
            rowKey="_key"
            dataSource={items}
            columns={reviewColumns}
            pagination={false}
            scroll={{ y: 340 }}
            rowClassName={r => r.status === 'unmatched' ? 'ant-table-row-unmatched' : ''}
          />
          <Space style={{ marginTop: 12 }}>
            <Button onClick={() => setStep(0)}>返回</Button>
            <Button type="primary" loading={submitting} onClick={handleSubmit} disabled={!okCount}>
              提交 {okCount} 条
            </Button>
          </Space>
        </div>
      )}

      {step === 2 && (
        <div>
          <Alert
            type="success"
            icon={<CheckCircleOutlined />}
            showIcon
            message={`完成：${results.filter(r => r.ok).length} 成功，${results.filter(r => !r.ok).length} 失败`}
            style={{ marginBottom: 12 }}
          />
          {results.filter(r => !r.ok).map((r, i) => (
            <Alert key={i} type="error" message={`产品 ${r.pid}: ${r.error}`} style={{ marginBottom: 4 }} />
          ))}
          <Button type="primary" onClick={() => { reset(); onDone() }}>完成</Button>
        </div>
      )}
    </Modal>
  )
}
