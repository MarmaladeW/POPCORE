import { useState } from 'react'
import {
  Modal, Steps, Select, Input, Button, Table, Space, Tag,
  Alert, message, DatePicker, InputNumber, AutoComplete, Progress, Tooltip,
} from 'antd'
import { DeleteOutlined, WarningOutlined, CheckCircleOutlined } from '@ant-design/icons'
import dayjs, { Dayjs } from 'dayjs'
import client from '../../api/client'
import { batchMatch, saveAlias, cleanName, isHeaderLine } from '../../api/matcher'

interface Props {
  open: boolean
  onClose: () => void
  onDone: () => void
}

interface MatchedItem {
  _key: string
  rawName: string
  qty: number
  notes: string
  product_id?: number
  sku?: string
  jizhanming?: string
  candidates?: any[]
  status: 'matched' | 'fuzzy' | 'unmatched' | 'skipped'
}

/** Handles name*qty, name：qty, name: qty, tab-sep, space-sep, and "名称2端" formats */
function parseLine(line: string) {
  const t = line.trim()
  if (t.includes('*')) {
    const star = t.lastIndexOf('*')
    const rawName = t.slice(0, star).trim()  // keep trailing : for server header detection
    const qty = parseInt(t.slice(star + 1).trim(), 10) || 1
    return { rawName, qty, notes: '' }
  }
  if (t.includes('\t')) {
    const parts = t.split('\t').map(s => s.trim())
    return { rawName: parts[0], qty: Math.abs(parseInt(parts[1] || '1', 10)) || 1, notes: parts.slice(2).join(' ') }
  }
  // Colon separator: "名称：数量" or "名称: 数量"
  const colonM = t.match(/^(.+?)\s*[：:]\s*(\d+)\s*(.*)$/)
  if (colonM && colonM[1].trim()) {
    return { rawName: colonM[1].trim(), qty: parseInt(colonM[2], 10) || 1, notes: colonM[3].trim() }
  }
  const glued = t.match(/^(.*\D)(\d+)[端个盒箱]?\s*(.*)$/)
  if (glued && glued[1].trim()) {
    return { rawName: glued[1].trim(), qty: parseInt(glued[2], 10) || 1, notes: glued[3].trim() }
  }
  const parts = t.split(/\s+/)
  return { rawName: parts[0], qty: Math.abs(parseInt(parts[1] || '1', 10)) || 1, notes: parts.slice(2).join(' ') }
}


function ProductPicker({ onSelect }: { onSelect: (p: any) => void }) {
  const [opts, setOpts] = useState<any[]>([])
  async function search(q: string) {
    if (!q) { setOpts([]); return }
    const r = await client.get('/products/search', { params: { q, limit: 8 } })
    setOpts(r.data.map((p: any) => ({ value: String(p.id), label: `${p.jizhanming || p.name_cn_en || p.sku} (${p.sku})`, product: p })))
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

export default function BatchStockModal({ open, onClose, onDone }: Props) {
  const [step, setStep]       = useState(0)
  const [op, setOp]           = useState<'ru_dian' | 'restock_upstairs'>('restock_upstairs')
  const [date, setDate]       = useState<Dayjs>(dayjs())
  const [text, setText]       = useState('')
  const [items, setItems]     = useState<MatchedItem[]>([])
  const [progress, setProgress] = useState(0)
  const [matching, setMatch]  = useState(false)
  const [submitting, setSub]  = useState(false)
  const [results, setResults] = useState<any[]>([])

  function reset() { setStep(0); setText(''); setItems([]); setResults([]); setProgress(0) }

  async function match() {
    const tokens = text.split(/[\n,，]+/).map(t => t.trim()).filter(Boolean)
    if (!tokens.length) { message.warning('内容为空'); return }
    setMatch(true)
    setProgress(10)

    const validTokens = tokens.filter(t => !isHeaderLine(t))
    const parsed = validTokens.map(parseLine)
    const queries = parsed.map(p => p.rawName)

    let results
    try {
      results = await batchMatch(queries)
    } catch {
      message.error('匹配失败，请重试')
      setMatch(false)
      return
    }
    setProgress(100)

    const out: MatchedItem[] = results
      .map((r, i) => {
        if (r.status === 'skipped') return null
        const top = r.candidates[0]
        return {
          ...parsed[i],
          _key: `${i}-${r.query}`,
          product_id: top?.id,
          sku: top?.sku,
          jizhanming: top?.jizhanming,
          candidates: r.candidates.length > 1 ? r.candidates : undefined,
          status: r.status,
        } as MatchedItem
      })
      .filter(Boolean) as MatchedItem[]

    setMatch(false)
    setItems(out)
    setStep(1)
  }

  function updateItem(key: string, patch: Partial<MatchedItem>) {
    setItems(prev => prev.map(it => it._key === key ? { ...it, ...patch } : it))
  }

  function removeItem(key: string) {
    setItems(prev => prev.filter(it => it._key !== key))
  }

  async function submit() {
    const toSub = items.filter(i => i.product_id)
    if (!toSub.length) { message.warning('没有可提交的行'); return }
    setSub(true)
    try {
      const r = await client.post('/stock/batch_operation', {
        operation: op,
        date: date.format('YYYY-MM-DD'),
        items: toSub.map(i => ({ product_id: i.product_id, qty: i.qty, notes: i.notes })),
      })
      setResults(r.data.results || [])
      setStep(2)
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '失败')
    } finally { setSub(false) }
  }

  const okCount = items.filter(i => i.product_id).length
  const unmatchedCount = items.filter(i => i.status === 'unmatched' || i.status === 'fuzzy').length

  async function handleManualSelect(key: string, rawName: string, p: any) {
    updateItem(key, { product_id: p.id, sku: p.sku, jizhanming: p.jizhanming, status: 'matched', candidates: undefined })
    try { await saveAlias(p.id, rawName) } catch { /* silently ignore */ }
  }

  const reviewColumns = [
    {
      title: '输入名称', dataIndex: 'rawName', width: 110,
      render: (v: string, r: MatchedItem) => (
        <span style={{ color: r.status === 'unmatched' ? '#cf1322' : undefined }}>{v}</span>
      ),
    },
    {
      title: '匹配产品', key: 'm', width: 210,
      render: (_: any, r: MatchedItem) => {
        if (r.status === 'unmatched') return (
          <Space size={4}>
            <Tag color="red">未匹配</Tag>
            <ProductPicker onSelect={p => handleManualSelect(r._key, r.rawName, p)} />
          </Space>
        )
        if (r.candidates && r.candidates.length > 1) return (
          <Select
            size="small"
            value={r.product_id}
            style={{ width: 200 }}
            onChange={v => {
              const c = r.candidates!.find(x => x.id === v)
              handleManualSelect(r._key, r.rawName, c)
            }}
            options={r.candidates.map(c => ({ value: c.id, label: `${c.jizhanming || c.name_cn_en || c.sku} (${c.sku})` }))}
          />
        )
        return <Space size={4}><Tag color="green">✓</Tag><span>{r.jizhanming}</span><Tag>{r.sku}</Tag></Space>
      },
    },
    {
      title: '数量(端)', key: 'qty', width: 90,
      render: (_: any, r: MatchedItem) => (
        <InputNumber size="small" min={1} value={r.qty} style={{ width: 70 }}
          onChange={v => updateItem(r._key, { qty: v ?? 1 })} />
      ),
    },
    {
      title: '备注', dataIndex: 'notes', width: 100,
      render: (v: string, r: MatchedItem) => (
        <Input size="small" value={v} onChange={e => updateItem(r._key, { notes: e.target.value })} />
      ),
    },
    {
      title: '', key: 'del', width: 40,
      render: (_: any, r: MatchedItem) => (
        <Tooltip title="移除"><Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeItem(r._key)} /></Tooltip>
      ),
    },
  ]

  return (
    <Modal
      title="批量库存导入"
      open={open}
      onCancel={() => { reset(); onClose() }}
      footer={null}
      width={760}
      destroyOnClose
    >
      <Steps current={step} size="small" style={{ marginBottom: 16 }}
        items={[{ title: '粘贴' }, { title: '确认' }, { title: '完成' }]} />

      {step === 0 && (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space>
            <Select value={op} onChange={setOp} style={{ width: 200 }}
              options={[
                { value: 'restock_upstairs', label: '入库（楼上）' },
                { value: 'ru_dian', label: '入店' },
              ]} />
            <DatePicker value={date} onChange={d => setDate(d ?? dayjs())} allowClear={false} style={{ width: 150 }} />
          </Space>
          <Alert type="info" message="支持格式"
            description={
              <div style={{ fontFamily: 'monospace', fontSize: 12, lineHeight: 1.8 }}>
                {'记账名  数量  [备注]   ← 空格分隔\n'}
                {'记账名\t数量          ← 表格粘贴\n'}
                {'记账名2端            ← 数字紧跟名称'}
              </div>
            }
          />
          <Input.TextArea rows={8} value={text} onChange={e => setText(e.target.value)}
            placeholder={'Dimoo花花 2\nSA草莓 1\nMolly精灵3端'} style={{ fontFamily: 'monospace' }} />
          {matching && <Progress percent={progress} />}
          <Button type="primary" loading={matching} onClick={match}>匹配产品</Button>
        </Space>
      )}

      {step === 1 && (
        <>
          {unmatchedCount > 0 && (
            <Alert type="warning" icon={<WarningOutlined />} showIcon style={{ marginBottom: 8 }}
              message={`${unmatchedCount} 行未自动匹配 — 可手动搜索指定产品，或点删除按钮跳过`} />
          )}
          <Table size="small" rowKey="_key" dataSource={items} columns={reviewColumns}
            pagination={false} scroll={{ y: 320 }} />
          <Space style={{ marginTop: 12 }}>
            <Button onClick={() => setStep(0)}>返回</Button>
            <Button type="primary" loading={submitting} onClick={submit} disabled={!okCount}>
              提交 {okCount} 条
            </Button>
          </Space>
        </>
      )}

      {step === 2 && (
        <>
          <Alert type="success" icon={<CheckCircleOutlined />} showIcon style={{ marginBottom: 8 }}
            message={`成功 ${results.filter(r => r.ok).length} 条 / 失败 ${results.filter(r => !r.ok).length} 条`} />
          {results.filter(r => !r.ok).map((r, i) => (
            <Alert key={i} type="error" message={`${r.pid}: ${r.error}`} style={{ marginBottom: 4 }} />
          ))}
          <Button type="primary" onClick={() => { reset(); onDone() }}>完成</Button>
        </>
      )}
    </Modal>
  )
}
