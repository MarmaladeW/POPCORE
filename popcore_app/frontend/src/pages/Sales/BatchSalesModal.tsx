import { useState } from 'react'
import {
  Modal, Steps, Input, Button, Table, Select, Tag, Space,
  Alert, message, AutoComplete, InputNumber, Progress, Tooltip,
} from 'antd'
import { DeleteOutlined, WarningOutlined, CheckCircleOutlined } from '@ant-design/icons'
import client from '../../api/client'
import { batchMatch, isHeaderLine, saveAlias, detectSalesSection, MatchResult, SectionType } from '../../api/matcher'

interface Props {
  open: boolean
  date: string
  onClose: () => void
  onDone: () => void
}

type ActiveSection = Exclude<SectionType, 'ignore'>

interface MatchedItem {
  _key: string
  rawName: string
  qty_pos: number
  qty_cash: number
  qty: number          // for stock ops
  notes: string
  product_id?: number
  sku?: string
  jizhanming?: string
  candidates?: any[]
  status: 'matched' | 'fuzzy' | 'unmatched' | 'skipped'
  aliasSaved?: boolean
  section: ActiveSection
  box_size?: number    // trailing number stripped from name (stock sections only)
  flagged?: boolean    // true = qty could not be determined
}

type ParsedLine = {
  rawName: string; qty_pos: number; qty_cash: number; qty: number
  notes: string; section: ActiveSection; box_size?: number; flagged: boolean
}

/**
 * Explicit line parser — section-aware, no greedy fallbacks.
 *
 * Steps (in order, stops at first success):
 *  1. name*qty  — lastIndexOf('*'), unambiguous
 *  2. Stock sections only: trailing number stripped from name = box count
 *     "smiski hipper 12" → name="smiski hipper", box_size=12
 *     "比奇堡6端" → name="比奇堡", box_size=6
 *     No number → name=full string, qty=1 (single unit)
 *  3. Tab separator — Excel paste fallback
 *  4. Flag — qty unknown for sales sections; surfaces to user
 */
function parseItem(raw: string, section: ActiveSection): ParsedLine {
  const t = raw.trim()
  const isStock = section === 'stock_in' || section === 'stock_out' || section === 'claw'
  const base = { section, notes: '', qty_pos: 0, qty_cash: 0, flagged: false }

  // Step 1: * separator (universal, unambiguous)
  const starIdx = t.lastIndexOf('*')
  if (starIdx > 0) {
    const rawName = t.slice(0, starIdx).trim()
    const qty = parseInt(t.slice(starIdx + 1).trim(), 10) || 0
    if (rawName && qty > 0) {
      if (section === 'cash') return { ...base, rawName, qty, qty_cash: qty }
      return { ...base, rawName, qty, qty_pos: qty }
    }
  }

  // Step 2: Stock sections — trailing number = box count, strip from name
  if (isStock) {
    const stripped = t.replace(/[端个盒箱]+$/, '').trim()
    const m = stripped.match(/^(.*\D)\s*(\d+)$/)
    if (m && m[1].trim()) {
      const rawName = m[1].trim()
      const box_size = parseInt(m[2], 10)
      return { ...base, rawName, qty: box_size, qty_pos: box_size, box_size }
    }
    // No trailing number → single unit, not flagged
    return { ...base, rawName: stripped || t, qty: 1, qty_pos: 1 }
  }

  // Step 3: Tab separator (Excel paste)
  if (t.includes('\t')) {
    const parts = t.split('\t').map(s => s.trim())
    const qty_pos = parseInt(parts[1] || '0', 10) || 0
    const qty_cash = parseInt(parts[2] || '0', 10) || 0
    if (parts[0]) {
      const qty = section === 'cash' ? qty_cash : qty_pos
      return { ...base, rawName: parts[0], qty, qty_pos, qty_cash, notes: parts.slice(3).join(' ') }
    }
  }

  // Step 4: Flag — qty unknown, surface to user
  return { ...base, rawName: t, qty: 0, flagged: true }
}

const SECTION_LABELS: Record<ActiveSection, { label: string; color: string }> = {
  pos:       { label: '卡机',  color: 'blue'   },
  cash:      { label: '现金',  color: 'green'  },
  stock_in:  { label: '入店',  color: 'purple' },
  stock_out: { label: '出店',  color: 'orange' },
  claw:      { label: '娃娃机', color: 'gold'  },
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
    // Step 1: split on newlines first, then commas — explicit two-stage
    const lines = text
      .split('\n')
      .flatMap(l => l.split(/[,，]/))
      .map(t => t.trim())
      .filter(Boolean)

    if (!lines.length) { message.warning('内容为空'); return }
    setMatch(true)
    setProgress(10)

    // ── Pre-scan to find all section boundaries ───────────────────────────
    const boundaries: Array<{ idx: number; section: ActiveSection }> = []
    for (let i = 0; i < lines.length; i++) {
      const sec = detectSalesSection(lines[i])
      if (sec !== null && sec !== 'ignore') {
        boundaries.push({ idx: i, section: sec as ActiveSection })
      }
    }

    function sectionAt(i: number): ActiveSection | 'ignore' {
      let cur: ActiveSection | 'ignore' = 'pos'
      for (const b of boundaries) {
        if (b.idx <= i) cur = b.section
        else break
      }
      const sec = detectSalesSection(lines[i])
      if (sec === 'ignore') return 'ignore'
      return cur
    }

    // ── Parse each line with section context ──────────────────────────────
    const parsed: ParsedLine[] = []
    for (let i = 0; i < lines.length; i++) {
      if (isHeaderLine(lines[i])) continue
      const section = sectionAt(i)
      if (section === 'ignore') continue
      parsed.push(parseItem(lines[i], section))
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
    const toSub = items.filter(i => i.product_id && !i.flagged)
    if (!toSub.length) { message.warning('没有可提交的行'); return }
    setSub(true)

    const salesItems = toSub.filter(i => i.section === 'pos' || i.section === 'cash')
    const stockGroups: Record<string, MatchedItem[]> = {
      ru_dian:      toSub.filter(i => i.section === 'stock_in'),
      out_dian:     toSub.filter(i => i.section === 'stock_out'),
      ru_dian_claw: toSub.filter(i => i.section === 'claw'),
    }

    try {
      const promises: Promise<any>[] = []
      if (salesItems.length) {
        promises.push(client.post('/sales/batch_upsert', salesItems.map(i => ({
          product_id: i.product_id, date,
          qty_pos: i.qty_pos, qty_cash: i.qty_cash, notes: i.notes,
        }))))
      }
      for (const [op, opItems] of Object.entries(stockGroups)) {
        if (opItems.length) {
          promises.push(client.post('/stock/batch_operation', {
            operation: op, date,
            items: opItems.map(i => ({ product_id: i.product_id, qty: i.qty, notes: i.notes })),
          }))
        }
      }
      await Promise.all(promises)
      setResults({ ok: toSub.length, fail: items.length - toSub.length })
      setStep(2)
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '失败')
    } finally { setSub(false) }
  }

  const okCount    = items.filter(i => i.product_id && !i.flagged).length
  const flaggedCount   = items.filter(i => i.flagged).length
  const unmatchedCount = items.filter(i => !i.flagged && (i.status === 'unmatched' || i.status === 'fuzzy')).length

  async function handleManualSelect(key: string, rawName: string, p: any) {
    updateItem(key, { product_id: p.id, sku: p.sku, jizhanming: p.jizhanming, status: 'matched', candidates: undefined })
    try { await saveAlias(p.id, rawName) } catch { /* silently ignore alias save errors */ }
  }

  const reviewColumns = [
    {
      title: '类型', key: 'sec', width: 72,
      render: (_: any, r: MatchedItem) => {
        const s = SECTION_LABELS[r.section]
        return <Tag color={s.color}>{s.label}</Tag>
      },
    },
    {
      title: '输入名称', dataIndex: 'rawName', width: 120,
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
      title: '卡机', key: 'pos', width: 68,
      render: (_: any, r: MatchedItem) => r.section !== 'pos' ? null : (
        <InputNumber size="small" min={0} value={r.qty_pos} style={{ width: 55 }}
          onChange={v => updateItem(r._key, { qty_pos: v ?? 0 })} />
      ),
    },
    {
      title: '现金/转账', key: 'cash', width: 80,
      render: (_: any, r: MatchedItem) => r.section !== 'cash' ? null : (
        <InputNumber size="small" min={0} value={r.qty_cash} style={{ width: 55 }}
          onChange={v => updateItem(r._key, { qty_cash: v ?? 0 })} />
      ),
    },
    {
      title: '数量', key: 'qty', width: 100,
      render: (_: any, r: MatchedItem) => {
        if (r.flagged) {
          return (
            <Space size={4}>
              <Tag color="red" icon={<WarningOutlined />}>无数量</Tag>
              <InputNumber size="small" min={0} value={0} style={{ width: 50 }}
                onChange={v => updateItem(r._key, { qty: v ?? 0, qty_pos: v ?? 0, flagged: false })} />
            </Space>
          )
        }
        if (r.section === 'pos') {
          const t = r.qty_pos + r.qty_cash
          return <Tag color={t > 0 ? 'green' : 'default'}>{t}</Tag>
        }
        if (r.section === 'cash') {
          const t = r.qty_pos + r.qty_cash
          return <Tag color={t > 0 ? 'green' : 'default'}>{t}</Tag>
        }
        // Stock sections
        return (
          <Space size={4}>
            {r.box_size != null && <Tag style={{ fontSize: 11 }}>{r.box_size}端</Tag>}
            <InputNumber size="small" min={1} value={r.qty} style={{ width: 55 }}
              onChange={v => updateItem(r._key, { qty: v ?? 1 })} />
          </Space>
        )
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
      title="批量导入"
      open={open}
      onCancel={() => { reset(); onClose() }}
      footer={null}
      width={860}
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
                {'卡机汇总         ← 以下行视为卡机销售（格式：名称*数量）\n'}
                {'Dimoo花花*3\n'}
                {'随手记汇总       ← 以下行视为现金/转账\n'}
                {'哭娃度假*1\n'}
                {'入店             ← 以下行视为入店（格式：名称数量 或 名称*数量）\n'}
                {'smiski hipper 12\n'}
                {'出店 / 娃娃机    ← 其他库存操作'}
              </div>
            }
          />
          <Input.TextArea
            rows={10}
            value={text}
            onChange={e => setText(e.target.value)}
            placeholder={'卡机汇总\nDimoo花花*3\n随手记汇总\n哭娃度假*1'}
            style={{ fontFamily: 'monospace' }}
          />
          {matching && <Progress percent={progress} />}
          <Button type="primary" loading={matching} onClick={match}>匹配产品</Button>
        </Space>
      )}

      {step === 1 && (
        <>
          {(unmatchedCount > 0 || flaggedCount > 0) && (
            <Alert type="warning" icon={<WarningOutlined />} showIcon style={{ marginBottom: 8 }}
              message={[
                unmatchedCount > 0 && `${unmatchedCount} 行未自动匹配`,
                flaggedCount > 0   && `${flaggedCount} 行缺少数量 — 请补填或删除`,
              ].filter(Boolean).join('；')} />
          )}
          <Table size="small" rowKey="_key" dataSource={items} columns={reviewColumns}
            pagination={false} scroll={{ y: 340 }} />
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
            message={`成功提交 ${results.ok} 条，跳过 ${results.fail} 条`} />
          <Button type="primary" onClick={() => { reset(); onDone() }}>完成</Button>
        </>
      )}
    </Modal>
  )
}
