import { useState } from 'react'
import {
  Modal, Steps, Input, Button, Table, Select, Tag, Space,
  Alert, message, AutoComplete, InputNumber, Progress, Tooltip,
} from 'antd'
import { DeleteOutlined, WarningOutlined, CheckCircleOutlined } from '@ant-design/icons'
import client from '../../api/client'
import { batchMatch, isHeaderLine, saveAlias, cleanName, MatchResult } from '../../api/matcher'

interface Props {
  open: boolean
  date: string
  onClose: () => void
  onDone: () => void
}

interface MatchedItem {
  _key: string
  rawName: string
  qty_pos: number
  qty_cash: number
  notes: string
  product_id?: number
  sku?: string
  jizhanming?: string
  candidates?: any[]
  status: 'matched' | 'fuzzy' | 'unmatched' | 'skipped'
  aliasSaved?: boolean
}

/** Handles name*qty, name：qty, name: qty, tab-sep, space-sep, and mixed formats for sales lines */
function parseLine(line: string) {
  const t = line.trim()
  if (t.includes('*')) {
    const star = t.lastIndexOf('*')
    const rawName = t.slice(0, star).trim()  // keep trailing : for server header detection
    const qty_pos = parseInt(t.slice(star + 1).trim(), 10) || 0
    return { rawName, qty_pos, qty_cash: 0, notes: '' }
  }
  if (t.includes('\t')) {
    const parts = t.split('\t').map(s => s.trim())
    return {
      rawName: parts[0],
      qty_pos:  parseInt(parts[1] || '0', 10) || 0,
      qty_cash: parseInt(parts[2] || '0', 10) || 0,
      notes: parts.slice(3).join(' '),
    }
  }
  // Colon separator: "名称：数量" or "名称: 数量 数量2"
  const colonM = t.match(/^(.+?)\s*[：:]\s*(\d+)\s*(\d*)\s*(.*)$/)
  if (colonM && colonM[1].trim()) {
    return {
      rawName:  colonM[1].trim(),
      qty_pos:  parseInt(colonM[2] || '0', 10) || 0,
      qty_cash: parseInt(colonM[3] || '0', 10) || 0,
      notes: colonM[4].trim(),
    }
  }
  // Name glued to first number — treat first digit block after last CJK char as qty_pos
  const glued = t.match(/^(.*\D)(\d+)\s*(\d*)\s*(.*)$/)
  if (glued && glued[1].trim()) {
    return {
      rawName: glued[1].trim(),
      qty_pos:  parseInt(glued[2] || '0', 10) || 0,
      qty_cash: parseInt(glued[3] || '0', 10) || 0,
      notes: glued[4].trim(),
    }
  }
  const parts = t.split(/\s+/)
  return {
    rawName: parts[0],
    qty_pos:  parseInt(parts[1] || '0', 10) || 0,
    qty_cash: parseInt(parts[2] || '0', 10) || 0,
    notes: parts.slice(3).join(' '),
  }
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

export default function BatchSalesModal({ open, date, onClose, onDone }: Props) {
  const [step, setStep]       = useState(0)
  const [text, setText]       = useState('')
  const [items, setItems]     = useState<MatchedItem[]>([])
  const [progress, setProgress] = useState(0)
  const [matching, setMatch]  = useState(false)
  const [submitting, setSub]  = useState(false)
  const [results, setResults] = useState<{ ok: number; fail: number }>({ ok: 0, fail: 0 })

  function reset() { setStep(0); setText(''); setItems([]); setProgress(0) }

  async function match() {
    const lines = text.split(/[\n,，]+/).map(t => t.trim()).filter(Boolean)
    if (!lines.length) { message.warning('内容为空'); return }
    setMatch(true)
    setProgress(10)

    // Section-aware parsing: track 卡机 vs 现金/转账 sections
    // When only one number is given in a line, assign it to the correct qty field based on current section
    let section: 'pos' | 'cash' = 'pos'
    const parsed: Array<{ rawName: string; qty_pos: number; qty_cash: number; notes: string }> = []

    for (const line of lines) {
      if (isHeaderLine(line)) {
        const lower = cleanName(line).toLowerCase()
        if (/^(现金|转账|cash|随手记)/.test(lower)) {
          section = 'cash'
        } else if (/^(卡机|pos|刷卡)/.test(lower)) {
          section = 'pos'
        }
        continue
      }
      const p = parseLine(line)
      // If only one number was found (qty_pos only), assign based on current section
      if (section === 'cash' && p.qty_pos > 0 && p.qty_cash === 0) {
        parsed.push({ ...p, qty_cash: p.qty_pos, qty_pos: 0 })
      } else {
        parsed.push(p)
      }
    }

    if (!parsed.length) { message.warning('内容为空'); setMatch(false); return }
    const queries = parsed.map(p => p.rawName)

    let matchResults: MatchResult[]
    try {
      matchResults = await batchMatch(queries)
    } catch {
      message.error('匹配失败，请重试')
      setMatch(false)
      return
    }
    setProgress(100)

    const out: MatchedItem[] = matchResults
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
      await client.post('/sales/batch_upsert', toSub.map(i => ({
        product_id: i.product_id, date,
        qty_pos: i.qty_pos, qty_cash: i.qty_cash, notes: i.notes,
      })))
      setResults({ ok: toSub.length, fail: items.length - toSub.length })
      setStep(2)
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '失败')
    } finally { setSub(false) }
  }

  const okCount = items.filter(i => i.product_id).length
  const unmatchedCount = items.filter(i => i.status === 'unmatched' || i.status === 'fuzzy').length

  async function handleManualSelect(key: string, rawName: string, p: any) {
    updateItem(key, { product_id: p.id, sku: p.sku, jizhanming: p.jizhanming, status: 'matched', candidates: undefined })
    try { await saveAlias(p.id, rawName) } catch { /* silently ignore alias save errors */ }
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
      title: '卡机', key: 'pos', width: 80,
      render: (_: any, r: MatchedItem) => (
        <InputNumber size="small" min={0} value={r.qty_pos} style={{ width: 65 }}
          onChange={v => updateItem(r._key, { qty_pos: v ?? 0 })} />
      ),
    },
    {
      title: '现金/转账', key: 'cash', width: 90,
      render: (_: any, r: MatchedItem) => (
        <InputNumber size="small" min={0} value={r.qty_cash} style={{ width: 65 }}
          onChange={v => updateItem(r._key, { qty_cash: v ?? 0 })} />
      ),
    },
    {
      title: '合计', key: 'total', width: 60, align: 'center' as const,
      render: (_: any, r: MatchedItem) => {
        const t = r.qty_pos + r.qty_cash
        return <Tag color={t > 0 ? 'green' : 'default'}>{t}</Tag>
      },
    },
    {
      title: '备注', dataIndex: 'notes', width: 90,
      render: (v: string, r: MatchedItem) => (
        <Input size="small" value={v} onChange={e => updateItem(r._key, { notes: e.target.value })} />
      ),
    },
    {
      title: '', key: 'del', width: 40,
      render: (_: any, r: MatchedItem) => (
        <Tooltip title="移除">
          <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeItem(r._key)} />
        </Tooltip>
      ),
    },
  ]

  return (
    <Modal
      title="批量销售导入"
      open={open}
      onCancel={() => { reset(); onClose() }}
      footer={null}
      width={800}
      destroyOnClose
    >
      <Steps current={step} size="small" style={{ marginBottom: 16 }}
        items={[{ title: '粘贴' }, { title: '确认' }, { title: '完成' }]} />

      {step === 0 && (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Alert
            type="info"
            message={`导入日期：${date}`}
            description={
              <div style={{ fontFamily: 'monospace', fontSize: 12, lineHeight: 1.8 }}>
                {'格式：记账名  卡机数  现金数  [备注]\n'}
                {'示例：Dimoo花花  3  1\n'}
                {'      SA草莓\t0\t2\t破损'}
              </div>
            }
          />
          <Input.TextArea
            rows={8}
            value={text}
            onChange={e => setText(e.target.value)}
            placeholder={'Dimoo花花  3  1\nSA草莓  0  2\nMolly精灵  1  0  赠品'}
            style={{ fontFamily: 'monospace' }}
          />
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
            message={`成功提交 ${results.ok} 条，跳过 ${results.fail} 条未匹配`} />
          <Button type="primary" onClick={() => { reset(); onDone() }}>完成</Button>
        </>
      )}
    </Modal>
  )
}
