import { useState, useEffect } from 'react'
import {
  Input, Button, Table, Tag, Select, Space,
  Alert, message, AutoComplete, InputNumber, Tooltip, Tabs, Badge,
} from 'antd'
import {
  CheckCircleOutlined, WarningOutlined, DeleteOutlined,
  QuestionCircleOutlined, CloseCircleOutlined,
} from '@ant-design/icons'
import client from '../../api/client'
import { useAppStore } from '../../store'
import {
  parseReportBackend, saveAlias, saveSectionAlias, getSectionAliases,
  type BackendProduct, type BackendCandidate,
  type BackendConfirmedItem, type BackendReviewItem, type BackendFailedItem,
  type SectionAlias,
} from '../../api/matcher'

// ─── Section display metadata ─────────────────────────────────────────────────

const SECTION_META: Record<string, { label: string; color: string }> = {
  pos:               { label: '卡机',      color: 'blue'    },
  cash:              { label: '随手记',    color: 'green'   },
  stock_in:          { label: '入店',      color: 'purple'  },
  stock_out:         { label: '出店',      color: 'orange'  },
  claw:              { label: '娃娃机',    color: 'gold'    },
  sell_display:      { label: '卖Display', color: 'cyan'    },
  break_display:     { label: '拆Display', color: 'red'     },
  employee_discount: { label: '员工折扣',  color: 'magenta' },
}

function SectionTag({ section }: { section: string }) {
  const m = SECTION_META[section]
  return m
    ? <Tag color={m.color} style={{ fontSize: 11, margin: 0 }}>{m.label}</Tag>
    : <Tag style={{ fontSize: 11, margin: 0 }}>{section}</Tag>
}

// ─── Extended item types (frontend state adds _key, accepted, overrides) ──────

type ActiveSection =
  | 'pos' | 'cash' | 'stock_in' | 'stock_out' | 'claw'
  | 'sell_display' | 'break_display' | 'employee_discount'

interface ConfirmedRow extends BackendConfirmedItem {
  _key: string
  qty_pos: number
  qty_cash: number
  qty: number
  notes: string
  removed?: boolean
}

interface ReviewRow extends BackendReviewItem {
  _key: string
  qty_pos: number
  qty_cash: number
  qty: number
  notes: string
  accepted: boolean          // user explicitly accepted this match
  product: BackendProduct    // may be overridden by user
  removed?: boolean
}

interface FailedRow extends BackendFailedItem {
  _key: string
  qty_pos: number
  qty_cash: number
  qty: number
  notes: string
  assigned_product?: BackendProduct  // manually assigned
  section: string
  removed?: boolean
}

interface UnknownSectionState {
  headerText: string
  resolvedSection: ActiveSection | 'skip' | null
}

// ─── Product picker ───────────────────────────────────────────────────────────

function ProductPicker({ onSelect, placeholder }: { onSelect: (p: BackendProduct) => void; placeholder?: string }) {
  const [opts, setOpts] = useState<any[]>([])
  async function search(q: string) {
    if (!q) { setOpts([]); return }
    const r = await client.get('/products/search', { params: { q, limit: 8 } })
    setOpts(r.data.map((p: any) => ({
      value: String(p.id),
      label: `${p.jizhanming || p.name_cn_en || p.sku} (${p.sku})`,
      product: p,
    })))
  }
  return (
    <AutoComplete
      size="small"
      style={{ width: 200 }}
      placeholder={placeholder ?? '搜索产品...'}
      options={opts}
      onSearch={search}
      onSelect={(_: any, opt: any) => onSelect(opt.product)}
    />
  )
}

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  date: string
  onComplete: (date: string, store: string) => void
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function DailyReportEntry({ date, onComplete }: Props) {
  const { selectedStore } = useAppStore()
  const defaultStore = selectedStore?.code ?? 'DT'

  const [step,          setStep]         = useState<'input' | 'review' | 'done'>('input')
  const [rawText,       setRawText]       = useState('')
  const [parsing,       setParsing]       = useState(false)
  const [submitting,    setSubmitting]    = useState(false)

  // Parsed data
  const [parsedDate,    setParsedDate]    = useState<string | null>(null)
  const [parsedStore,   setParsedStore]   = useState(defaultStore)
  const [confirmed,     setConfirmed]     = useState<ConfirmedRow[]>([])
  const [review,        setReview]        = useState<ReviewRow[]>([])
  const [failed,        setFailed]        = useState<FailedRow[]>([])
  const [unknowns,      setUnknowns]      = useState<UnknownSectionState[]>([])
  const [savedAliases,  setSavedAliases]  = useState<SectionAlias[]>([])
  const [cashTotalReported, setCashTotalReported] = useState<number | null>(null)
  const [parserEngine,  setParserEngine]  = useState<'llm' | 'rules'>('rules')
  const [multiDay,      setMultiDay]      = useState(false)

  useEffect(() => {
    getSectionAliases().then(setSavedAliases).catch(() => {})
  }, [])

  // ── Parse (call backend) ──────────────────────────────────────────────────

  async function handleParse() {
    if (!rawText.trim()) { message.warning('请粘贴日报内容'); return }
    setParsing(true)
    try {
      const res = await parseReportBackend(rawText, defaultStore)

      setParsedDate(res.detected_date)
      setParsedStore(res.store)

      const mk = (prefix: string, i: number, name: string) => `${prefix}-${i}-${name}`

      setConfirmed(res.confirmed.map((it, i) => ({
        ...it,
        _key:     mk('c', i, it.raw_name),
        qty:      it.qty,
        qty_pos:  it.qty_pos,
        qty_cash: it.qty_cash,
        notes:    it.note ?? '',
      })))

      setReview(res.review.map((it, i) => ({
        ...it,
        _key:     mk('r', i, it.raw_name),
        qty:      it.qty,
        qty_pos:  it.qty_pos,
        qty_cash: it.qty_cash,
        notes:    it.note ?? '',
        accepted: false,
      })))

      setFailed(res.failed.map((it, i) => ({
        ...it,
        _key:     mk('f', i, it.raw_name),
        qty:      it.qty,
        qty_pos:  it.qty_pos,
        qty_cash: it.qty_cash,
        notes:    it.note ?? '',
        section:  it.section ?? 'pos',
      })))

      setCashTotalReported(res.cash_total_reported ?? null)
      setParserEngine(res.parser_engine ?? 'rules')
      setMultiDay(res.multi_day ?? false)

      setUnknowns(
        res.unknown_sections.map(h => ({ headerText: h, resolvedSection: null }))
      )

      if (res.confirmed.length + res.review.length + res.failed.length === 0) {
        message.warning('未找到可解析的产品行')
        return
      }
      setStep('review')
    } catch (err: any) {
      message.error(err?._serverMessage ?? err?.message ?? '解析失败，请重试')
    } finally {
      setParsing(false)
    }
  }

  // ── Unknown section classification ─────────────────────────────────────────

  async function classifyUnknown(headerText: string, resolved: ActiveSection | 'skip') {
    setUnknowns(prev =>
      prev.map(u => u.headerText === headerText ? { ...u, resolvedSection: resolved } : u)
    )
    if (resolved === 'skip') {
      setFailed(prev => prev.filter(f => f.unknown_header !== headerText))
    } else {
      // Move unknown-header items from failed into confirmed/review based on their existing score
      const toReclassify = failed.filter(f => f.unknown_header === headerText)
      if (toReclassify.length) {
        setFailed(prev => prev.filter(f => f.unknown_header !== headerText))
        // Re-queue them as review items (user still needs to confirm)
        setReview(prev => [
          ...prev,
          ...toReclassify.map(f => ({
            ...f,
            _key:      f._key + '-reclassified',
            section:   resolved,
            score:     f.score,
            accepted:  false,
            product:   (f.candidates[0] ?? null) as unknown as BackendProduct,
            candidates: f.candidates as BackendCandidate[],
            warn_blank_jzm: false,
          } as ReviewRow)),
        ])
      }
    }
    try {
      await saveSectionAlias(headerText, resolved === 'skip' ? 'ignore' : resolved)
      setSavedAliases(await getSectionAliases())
    } catch { /* ignore */ }
  }

  // ── Item update helpers ───────────────────────────────────────────────────

  function patchConfirmed(key: string, patch: Partial<ConfirmedRow>) {
    setConfirmed(prev => prev.map(r => r._key === key ? { ...r, ...patch } : r))
  }
  function patchReview(key: string, patch: Partial<ReviewRow>) {
    setReview(prev => prev.map(r => r._key === key ? { ...r, ...patch } : r))
  }
  function patchFailed(key: string, patch: Partial<FailedRow>) {
    setFailed(prev => prev.map(r => r._key === key ? { ...r, ...patch } : r))
  }

  async function handleManualSelectReview(row: ReviewRow, p: BackendProduct) {
    patchReview(row._key, { product: p, accepted: true })
    try { await saveAlias(p.id, row.raw_name) } catch { /* ignore */ }
  }

  async function handleManualSelectFailed(row: FailedRow, p: BackendProduct) {
    patchFailed(row._key, { assigned_product: p })
    try { await saveAlias(p.id, row.raw_name) } catch { /* ignore */ }
  }

  // ── Submit ────────────────────────────────────────────────────────────────

  async function handleSubmit() {
    const unresolvedUnknowns = unknowns.filter(u => u.resolvedSection === null)
    if (unresolvedUnknowns.length) {
      message.warning(`请先处理 ${unresolvedUnknowns.length} 个未识别的章节`)
      return
    }

    const submitDate  = parsedDate ?? date
    const submitStore = parsedStore

    const payload: any[] = []

    // CONFIRMED items (auto-accepted, not removed, qty known)
    for (const r of confirmed) {
      if (r.removed || r.flagged || !r.product?.id) continue
      payload.push(buildPayloadItem(r, r.product, 'confirmed'))
    }

    // REVIEW items (user must have explicitly accepted)
    for (const r of review) {
      if (r.removed || !r.accepted || r.flagged || !r.product?.id) continue
      const item = buildPayloadItem(r, r.product, 'review')
      item.raw_name    = r.raw_name
      item.fuzzy_score = r.score
      item.top_score   = r.score
      item.was_top     = true
      payload.push(item)
    }

    // FAILED items where user manually assigned a product
    for (const r of failed) {
      if (r.removed || r.flagged || !r.assigned_product?.id) continue
      const item = buildPayloadItem(r, r.assigned_product, 'failed')
      item.raw_name    = r.raw_name
      item.fuzzy_score = 0
      item.top_score   = 0
      item.was_top     = false
      payload.push(item)
    }

    if (!payload.length) { message.warning('没有可提交的条目'); return }

    setSubmitting(true)
    try {
      await client.post('/sales/submit_daily_report', {
        date: submitDate,
        store_code: submitStore,
        mode: 'replace',
        items: payload,
      })
      setStep('done')
    } catch (err: any) {
      message.error(err?._serverMessage ?? err?.message ?? '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  function buildPayloadItem(
    row: { section: string; qty_pos: number; qty_cash: number; qty: number; notes: string; box_size?: number | null; raw_name?: string },
    product: BackendProduct,
    source_bucket: string,
  ) {
    const base: any = {
      product_id: product.id, section: row.section, notes: row.notes,
      raw_name: row.raw_name ?? '', source_bucket,
    }
    if (row.section === 'cash') {
      base.qty_cash = row.qty_cash || row.qty
    } else if (row.section === 'stock_in') {
      base.box_size  = row.box_size ?? 1
      base.num_boxes = row.qty
    } else if (row.section === 'break_display' || row.section === 'stock_out') {
      base.qty = row.qty || row.qty_pos
    } else {
      base.qty_pos = row.qty_pos || row.qty
    }
    return base
  }

  // ── Derived counts ────────────────────────────────────────────────────────

  const confirmedReady  = confirmed.filter(r => !r.removed && !r.flagged && r.product?.id).length
  const reviewAccepted  = review.filter(r => r.accepted && !r.removed && !r.flagged && r.product?.id).length
  const failedAssigned  = failed.filter(r => r.assigned_product && !r.removed && !r.flagged).length
  const totalReady      = confirmedReady + reviewAccepted + failedAssigned

  const pendingReview   = review.filter(r => !r.accepted && !r.removed).length
  const unresolvedUnknowns = unknowns.filter(u => u.resolvedSection === null).length

  const canSubmit = unresolvedUnknowns === 0 && totalReady > 0

  // ── Columns ───────────────────────────────────────────────────────────────

  const qtyCell = (
    row: ConfirmedRow | ReviewRow | FailedRow,
    patchFn: (key: string, p: any) => void,
  ) => {
    const isCash = row.section === 'cash'
    if (row.flagged) return (
      <Space size={4}>
        <Tag color="red" icon={<WarningOutlined />}>无数量</Tag>
        <InputNumber size="small" min={0} value={0} style={{ width: 55 }}
          onChange={v => {
            const q = v ?? 0
            patchFn(row._key, {
              qty: q,
              qty_pos:  isCash ? 0 : q,
              qty_cash: isCash ? q : 0,
              flagged: false,
            })
          }} />
      </Space>
    )
    if (row.section === 'stock_in' && row.box_size != null) return (
      <Space size={4}>
        <Tag style={{ fontSize: 11 }}>{row.box_size}盒/端</Tag>
        <span style={{ color: '#9ca3af' }}>×</span>
        <Tag style={{ fontSize: 11 }}>{row.qty}端</Tag>
        <span style={{ fontWeight: 600, color: '#6366F1' }}>={row.qty * row.box_size}盒</span>
      </Space>
    )
    return (
      <Space size={4}>
        <InputNumber size="small" min={0}
          value={isCash ? (row.qty_cash || row.qty) : (row.qty_pos || row.qty)}
          style={{ width: 65 }}
          onChange={v => {
            const q = v ?? 0
            patchFn(row._key, { qty: q, qty_pos: isCash ? 0 : q, qty_cash: isCash ? q : 0 })
          }} />
        {row.warn_stock && (
          <Tooltip title={`店内库存仅 ${row.warn_stock.instore}，超卖？可能记错记账名`}>
            <Tag color="orange" style={{ fontSize: 11, margin: 0 }}>库存{row.warn_stock.instore}</Tag>
          </Tooltip>
        )}
      </Space>
    )
  }

  const confirmedColumns = [
    {
      title: '产品', key: 'product', width: 220,
      render: (_: any, r: ConfirmedRow) => (
        <Space direction="vertical" size={0}>
          <Space size={4}>
            <Tag color="green" style={{ margin: 0 }}><CheckCircleOutlined /></Tag>
            <span style={{ fontSize: 13, fontWeight: 500 }}>{r.product.jizhanming}</span>
          </Space>
          <Space size={4}>
            <Tag style={{ fontSize: 11, margin: 0 }}>{r.product.sku}</Tag>
            {r.warn_blank_jzm && <Tag color="orange" style={{ fontSize: 11 }}>⚠ 记账名为空</Tag>}
          </Space>
        </Space>
      ),
    },
    {
      title: '输入', dataIndex: 'raw_name', width: 130,
      render: (v: string) => <span style={{ fontSize: 12, color: '#6b7280' }}>{v}</span>,
    },
    {
      title: '分区', key: 'sec', width: 80,
      render: (_: any, r: ConfirmedRow) => <SectionTag section={r.section} />,
    },
    {
      title: '数量', key: 'qty', width: 140,
      render: (_: any, r: ConfirmedRow) => qtyCell(r, patchConfirmed),
    },
    {
      title: '备注', key: 'notes', width: 100,
      render: (_: any, r: ConfirmedRow) => (
        <Input size="small" value={r.notes}
          onChange={e => patchConfirmed(r._key, { notes: e.target.value })} />
      ),
    },
    {
      title: '', key: 'del', width: 40,
      render: (_: any, r: ConfirmedRow) => (
        <Tooltip title="移除">
          <Button size="small" type="text" danger icon={<DeleteOutlined />}
            onClick={() => patchConfirmed(r._key, { removed: true })} />
        </Tooltip>
      ),
    },
  ]

  const reviewColumns = [
    {
      title: '输入', dataIndex: 'raw_name', width: 130,
      render: (v: string) => <span style={{ fontSize: 13 }}>{v}</span>,
    },
    {
      title: '候选匹配', key: 'match', width: 250,
      render: (_: any, r: ReviewRow) => {
        if (r.accepted) return (
          <Space size={4}>
            <Tag color="green"><CheckCircleOutlined /></Tag>
            <span style={{ fontSize: 13 }}>{r.product?.jizhanming}</span>
            <Tag style={{ fontSize: 11 }}>{r.product?.sku}</Tag>
          </Space>
        )
        return (
          <Space size={4} direction="vertical" style={{ width: '100%' }}>
            <Select
              size="small" value={r.product?.id} style={{ width: 220 }}
              onChange={v => {
                const c = r.candidates?.find((x: BackendCandidate) => x.id === v)
                if (c) patchReview(r._key, { product: c as BackendProduct })
              }}
              options={(r.candidates || []).map((c: BackendCandidate) => ({
                value: c.id,
                label: `${c.jizhanming || c.sku} (${c.score}%)`,
              }))}
            />
            <ProductPicker placeholder="或搜索其他产品..."
              onSelect={p => handleManualSelectReview(r, p)} />
          </Space>
        )
      },
    },
    {
      title: '分值', dataIndex: 'score', width: 70,
      render: (v: number, r: ReviewRow) => {
        const color = r.accepted ? '#10B981' : v >= 70 ? '#f59e0b' : '#ef4444'
        return <span style={{ fontWeight: 600, color, fontSize: 13 }}>{v}%</span>
      },
    },
    {
      title: '分区', key: 'sec', width: 80,
      render: (_: any, r: ReviewRow) => <SectionTag section={r.section} />,
    },
    {
      title: '数量', key: 'qty', width: 140,
      render: (_: any, r: ReviewRow) => qtyCell(r, patchReview),
    },
    {
      title: '操作', key: 'action', width: 120,
      render: (_: any, r: ReviewRow) => r.accepted ? (
        <Button size="small" onClick={() => patchReview(r._key, { accepted: false })}>
          撤销
        </Button>
      ) : (
        <Space size={4}>
          <Button size="small" type="primary"
            disabled={!r.product?.id}
            onClick={() => patchReview(r._key, { accepted: true })}>
            接受
          </Button>
          <Tooltip title="移除此行">
            <Button size="small" danger type="text" icon={<DeleteOutlined />}
              onClick={() => patchReview(r._key, { removed: true })} />
          </Tooltip>
        </Space>
      ),
    },
  ]

  const failedColumns = [
    {
      title: '输入', dataIndex: 'raw_name', width: 130,
      render: (v: string, r: FailedRow) => (
        <Space direction="vertical" size={0}>
          <span style={{ color: '#cf1322', fontSize: 13 }}>{v}</span>
          {r.unknown_header && (
            <span style={{ fontSize: 11, color: '#9ca3af' }}>章节: {r.unknown_header}</span>
          )}
        </Space>
      ),
    },
    {
      title: '原因', key: 'reason', width: 90,
      render: (_: any, r: FailedRow) => {
        const labels: Record<string, string> = {
          no_match:        '未匹配',
          empty_name:      '名称为空',
          low_score:       '分值过低',
          unknown_section: '未知章节',
        }
        return <Tag color="red">{labels[r.reason] ?? r.reason}</Tag>
      },
    },
    {
      title: '手动指定', key: 'assign', width: 240,
      render: (_: any, r: FailedRow) => r.assigned_product ? (
        <Space size={4}>
          <Tag color="green"><CheckCircleOutlined /></Tag>
          <span style={{ fontSize: 13 }}>{r.assigned_product.jizhanming}</span>
          <Tag style={{ fontSize: 11 }}>{r.assigned_product.sku}</Tag>
          <Button size="small" type="text" icon={<CloseCircleOutlined />}
            onClick={() => patchFailed(r._key, { assigned_product: undefined })} />
        </Space>
      ) : (
        <ProductPicker onSelect={p => handleManualSelectFailed(r, p)} />
      ),
    },
    {
      title: '分区', key: 'sec', width: 80,
      render: (_: any, r: FailedRow) => (
        <Select size="small" value={r.section} style={{ width: 80 }}
          options={Object.entries(SECTION_META).map(([k, v]) => ({ value: k, label: v.label }))}
          onChange={v => patchFailed(r._key, { section: v })} />
      ),
    },
    {
      title: '数量', key: 'qty', width: 100,
      render: (_: any, r: FailedRow) => qtyCell(r, patchFailed),
    },
    {
      title: '', key: 'del', width: 40,
      render: (_: any, r: FailedRow) => (
        <Tooltip title="丢弃此行">
          <Button size="small" type="text" danger icon={<DeleteOutlined />}
            onClick={() => patchFailed(r._key, { removed: true })} />
        </Tooltip>
      ),
    },
  ]

  // Cash checksum: reported 现金 total vs Σ(qty_cash × price) over ready items
  const cashComputed = (() => {
    let sum = 0
    for (const r of confirmed) {
      if (!r.removed && !r.flagged && r.section === 'cash' && r.product?.price != null)
        sum += (r.qty_cash || r.qty) * r.product.price
    }
    for (const r of review) {
      if (!r.removed && r.accepted && !r.flagged && r.section === 'cash' && r.product?.price != null)
        sum += (r.qty_cash || r.qty) * r.product.price
    }
    for (const r of failed) {
      if (!r.removed && !r.flagged && r.section === 'cash' && r.assigned_product?.price != null)
        sum += (r.qty_cash || r.qty) * r.assigned_product.price
    }
    return Math.round(sum * 100) / 100
  })()
  const cashDelta = cashTotalReported != null
    ? Math.round((cashTotalReported - cashComputed) * 100) / 100
    : null

  // ── Render ────────────────────────────────────────────────────────────────

  if (step === 'input') {
    return (
      <div style={{ maxWidth: 800, margin: '0 auto' }}>
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 600, fontSize: 16, color: '#111827', marginBottom: 4 }}>
            Import Daily Report
          </div>
          <div style={{ fontSize: 13, color: '#6b7280' }}>
            Paste the full end-of-day report. Date and store are detected automatically.
          </div>
        </div>
        <Input.TextArea
          rows={14}
          value={rawText}
          onChange={e => setRawText(e.target.value)}
          placeholder={`2026.04.01 DT汇总\n卡机汇总：\nchiikawa hipper*1\nsmiski hipper*2\n\n随手记汇总：\n星星人点亮场景*9\n\n入店：\ndimoo奇遇小夜灯 6*2\nsmiski cheer 12*1`}
          style={{ fontFamily: 'monospace', fontSize: 13, marginBottom: 12 }}
        />
        <Button type="primary" size="large" loading={parsing} onClick={handleParse}>
          Parse Report
        </Button>
      </div>
    )
  }

  if (step === 'done') {
    return (
      <Alert
        type="success"
        icon={<CheckCircleOutlined />}
        showIcon
        message={`Report imported — ${totalReady} items saved`}
        description={parsedDate ? `Date: ${parsedDate}  Store: ${parsedStore}` : undefined}
        action={
          <Button type="primary" onClick={() => onComplete(parsedDate ?? date, parsedStore)}>
            View Sales
          </Button>
        }
        style={{ borderRadius: 10 }}
      />
    )
  }

  // ── Review step ───────────────────────────────────────────────────────────

  const visibleConfirmed = confirmed.filter(r => !r.removed)
  const visibleReview    = review.filter(r => !r.removed)
  const visibleFailed    = failed.filter(r => !r.removed)

  return (
    <div>
      {/* Summary bar */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 16 }}>
        {parsedDate && (
          <Tag color="blue" style={{ fontSize: 13 }}>{parsedDate} · {parsedStore}</Tag>
        )}
        <Tag color={parserEngine === 'llm' ? 'geekblue' : 'default'} style={{ fontSize: 11 }}>
          {parserEngine === 'llm' ? 'AI 解析' : '规则解析'}
        </Tag>
        <Tag color="green" icon={<CheckCircleOutlined />}>{confirmedReady} confirmed</Tag>
        {pendingReview > 0 && (
          <Tag color="orange" icon={<WarningOutlined />}>{pendingReview} need review</Tag>
        )}
        {visibleFailed.length > 0 && (
          <Tag color="red" icon={<CloseCircleOutlined />}>{visibleFailed.length} failed</Tag>
        )}
        {unresolvedUnknowns > 0 && (
          <Tag color="red" icon={<QuestionCircleOutlined />}>{unresolvedUnknowns} unknown sections</Tag>
        )}
        <div style={{ marginLeft: 'auto' }}>
          <Space>
            <Button onClick={() => setStep('input')}>← Back</Button>
            <Button type="primary" loading={submitting} disabled={!canSubmit} onClick={handleSubmit}>
              Confirm & Log {totalReady} items
            </Button>
          </Space>
        </div>
      </div>

      {/* Replace-semantics notice */}
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 8, borderRadius: 8 }}
        message={`提交将替换 ${parsedDate ?? date} · ${parsedStore} 当天已有的销售记录（可安全重复导入）`}
      />

      {/* Multi-day paste warning */}
      {multiDay && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 8, borderRadius: 8 }}
          message="检测到多个日期 — 本次提交只会记录到一个日期。请将报告按天分开导入。"
        />
      )}

      {/* Cash checksum */}
      {cashTotalReported != null && (
        <Alert
          type={cashDelta === 0 ? 'success' : 'warning'}
          showIcon
          style={{ marginBottom: 8, borderRadius: 8 }}
          message={
            cashDelta === 0
              ? `现金对账 ✓ 报告 $${cashTotalReported} = 系统合计 $${cashComputed}`
              : `现金对账: 报告 $${cashTotalReported} · 随手记合计 $${cashComputed} · 差额 $${cashDelta}（差额可能来自税/折扣/记错产品）`
          }
        />
      )}

      {/* Unknown section classification alerts */}
      {unknowns.filter(u => u.resolvedSection === null).map(u => (
        <Alert
          key={u.headerText}
          type="warning"
          showIcon
          style={{ marginBottom: 8, borderRadius: 8 }}
          message={
            <Space>
              <span>Unknown section: <strong>{u.headerText}</strong> — what type?</span>
              <Select
                size="small"
                style={{ width: 130 }}
                placeholder="Classify..."
                options={[
                  ...Object.entries(SECTION_META).map(([k, v]) => ({ value: k, label: v.label })),
                  { value: 'skip', label: '跳过/忽略' },
                ]}
                onChange={(v: ActiveSection | 'skip') => classifyUnknown(u.headerText, v)}
              />
            </Space>
          }
        />
      ))}

      {/* Three-bucket tabs */}
      <Tabs
        defaultActiveKey="confirmed"
        items={[
          {
            key: 'confirmed',
            label: (
              <Badge count={visibleFailed.length === 0 && pendingReview === 0 ? 0 : undefined}
                style={{ backgroundColor: '#10B981' }}>
                <Space size={4}>
                  <CheckCircleOutlined style={{ color: '#10B981' }} />
                  <span>Confirmed</span>
                  <Tag color="green" style={{ margin: 0 }}>{visibleConfirmed.length}</Tag>
                </Space>
              </Badge>
            ),
            children: (
              <>
                {visibleConfirmed.length === 0 ? (
                  <Alert type="info" message="No confirmed items" style={{ borderRadius: 8 }} />
                ) : (
                  <Table
                    size="small"
                    rowKey="_key"
                    dataSource={visibleConfirmed}
                    columns={confirmedColumns}
                    pagination={false}
                    scroll={{ y: 420 }}
                  />
                )}
              </>
            ),
          },
          {
            key: 'review',
            label: (
              <Space size={4}>
                <WarningOutlined style={{ color: pendingReview > 0 ? '#f59e0b' : '#10B981' }} />
                <span>Review</span>
                <Tag color={pendingReview > 0 ? 'orange' : 'green'} style={{ margin: 0 }}>
                  {visibleReview.length}
                </Tag>
              </Space>
            ),
            children: (
              <>
                {visibleReview.length === 0 ? (
                  <Alert type="success" message="No items need review" style={{ borderRadius: 8 }} />
                ) : (
                  <>
                    <Alert
                      type="warning"
                      showIcon
                      style={{ marginBottom: 8, borderRadius: 8 }}
                      message={`${pendingReview} items need your confirmation. Accept or reject each match below.`}
                    />
                    <Table
                      size="small"
                      rowKey="_key"
                      dataSource={visibleReview}
                      columns={reviewColumns}
                      pagination={false}
                      scroll={{ y: 400 }}
                    />
                    {pendingReview > 0 && (
                      <div style={{ marginTop: 8 }}>
                        <Button
                          onClick={() => setReview(prev =>
                            prev.map(r => (!r.removed && r.product?.id) ? { ...r, accepted: true } : r)
                          )}>
                          Accept all with match
                        </Button>
                      </div>
                    )}
                  </>
                )}
              </>
            ),
          },
          {
            key: 'failed',
            label: (
              <Space size={4}>
                <CloseCircleOutlined style={{ color: visibleFailed.length > 0 ? '#ef4444' : '#10B981' }} />
                <span>Failed</span>
                <Tag color={visibleFailed.length > 0 ? 'red' : 'green'} style={{ margin: 0 }}>
                  {visibleFailed.length}
                </Tag>
              </Space>
            ),
            children: (
              <>
                {visibleFailed.length === 0 ? (
                  <Alert type="success" message="No failed items" style={{ borderRadius: 8 }} />
                ) : (
                  <>
                    <Alert
                      type="error"
                      showIcon
                      style={{ marginBottom: 8, borderRadius: 8 }}
                      message="These items could not be matched. Assign a product manually or discard."
                    />
                    <Table
                      size="small"
                      rowKey="_key"
                      dataSource={visibleFailed}
                      columns={failedColumns}
                      pagination={false}
                      scroll={{ y: 400 }}
                    />
                  </>
                )}
              </>
            ),
          },
        ]}
      />
    </div>
  )
}
