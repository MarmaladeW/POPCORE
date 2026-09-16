import { useState } from 'react'
import {
  Input, Button, Table, Tag, Select, Space, Checkbox,
  Alert, message, AutoComplete, InputNumber, Tooltip, Tabs, Badge,
} from 'antd'
import {
  CheckCircleOutlined, WarningOutlined, DeleteOutlined,
  QuestionCircleOutlined, CloseCircleOutlined,
} from '@ant-design/icons'
import client from '../../api/client'
import { useAppStore } from '../../store'
import {
  parseReportBackend,
  type BackendProduct, type BackendCandidate, type ReportAnnotations,
  type BackendConfirmedItem, type BackendReviewItem, type BackendFailedItem,
} from '../../api/matcher'
import { cashDifference, hasReportNotes, isReportRowReady, matchFeedback, positiveInteger, REPORT_NOTE_SECTIONS, validReportQuantity } from './dailyReportReview'

// ─── Section display metadata ─────────────────────────────────────────────────

const SECTION_META: Record<string, { label: string; color: string }> = {
  pos:               { label: '卡机 POS',      color: 'blue'    },
  cash:              { label: '随手记 Non-POS',    color: 'green'   },
  stock_in:          { label: '入店',      color: 'purple'  },
  stock_out:         { label: '出店',      color: 'orange'  },
  break_display:     { label: '拆Display', color: 'red'     },
}

function SectionTag({ section }: { section: string }) {
  const m = SECTION_META[section]
  return m
    ? <Tag color={m.color} style={{ fontSize: 11, margin: 0 }}>{m.label}</Tag>
    : <Tag style={{ fontSize: 11, margin: 0 }}>{section}</Tag>
}

// ─── Extended item types (frontend state adds _key, accepted, overrides) ──────

type ActiveSection =
  | 'pos' | 'cash' | 'stock_in' | 'stock_out' | 'break_display'

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
  originalTopId: number
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
  const { selectedStore, stores } = useAppStore()
  const defaultStore = selectedStore?.code === 'ALL' ? '' : selectedStore?.code ?? ''

  const [step,          setStep]         = useState<'input' | 'review' | 'done'>('input')
  const [rawText,       setRawText]       = useState('')
  const [useLlm,        setUseLlm]        = useState(false)
  const [parsing,       setParsing]       = useState(false)
  const [submitting,    setSubmitting]    = useState(false)

  // Parsed data
  const [parsedDate,    setParsedDate]    = useState<string | null>(null)
  const [parsedStore,   setParsedStore]   = useState(defaultStore)
  const [confirmed,     setConfirmed]     = useState<ConfirmedRow[]>([])
  const [review,        setReview]        = useState<ReviewRow[]>([])
  const [failed,        setFailed]        = useState<FailedRow[]>([])
  const [unknowns,      setUnknowns]      = useState<UnknownSectionState[]>([])
  const [cashTotalReported, setCashTotalReported] = useState<number | null>(null)
  const [cashExpected, setCashExpected] = useState<number | null>(null)
  const [reportNotes, setReportNotes] = useState<ReportAnnotations>({})
  const [metadataErrors, setMetadataErrors] = useState<string[]>([])
  const [parserEngine,  setParserEngine]  = useState<'llm' | 'rules'>('rules')
  const [multiDay,      setMultiDay]      = useState(false)

  // ── Parse (call backend) ──────────────────────────────────────────────────

  async function handleParse() {
    if (!rawText.trim()) { message.warning('请粘贴日报内容'); return }
    setParsing(true)
    try {
      const res = await parseReportBackend(
        rawText, defaultStore, useLlm ? 'llm' : 'rules'
      )

      setParsedDate(res.detected_date ?? date)
      setParsedStore(res.store)

      const mk = (prefix: string, i: number, name: string) => `${prefix}-${i}-${name}`

      setConfirmed(res.confirmed.map((it, i) => ({
        ...it,
        _key:     mk('c', i, it.raw_name),
        qty:      it.flagged ? 0 : it.qty,
        loose_qty: it.loose_qty ?? 0,
        qty_pos:  it.qty_pos,
        qty_cash: it.qty_cash,
        notes:    it.note ?? '',
      })))

      setReview(res.review.map((it, i) => ({
        ...it,
        _key:     mk('r', i, it.raw_name),
        qty:      it.flagged ? 0 : it.qty,
        loose_qty: it.loose_qty ?? 0,
        qty_pos:  it.qty_pos,
        qty_cash: it.qty_cash,
        notes:    it.note ?? '',
        originalTopId: it.candidates[0]?.id ?? it.product.id,
        accepted: false,
      })))

      setFailed(res.failed.map((it, i) => ({
        ...it,
        _key:     mk('f', i, it.raw_name),
        qty:      it.flagged ? 0 : it.qty,
        loose_qty: it.loose_qty ?? 0,
        qty_pos:  it.qty_pos,
        qty_cash: it.qty_cash,
        notes:    it.note ?? '',
        section:  it.section ?? 'unknown',
      })))

      setCashTotalReported(res.cash_total_reported ?? null)
      setCashExpected(res.cash_expected_reported ?? null)
      setReportNotes(Object.fromEntries(REPORT_NOTE_SECTIONS.map(({ key }) => [key, res[key] ?? []])))
      setMetadataErrors(res.metadata_errors ?? [])
      setParserEngine(res.parser_engine ?? 'rules')
      setMultiDay(res.multi_day ?? false)

      setUnknowns(
        res.unknown_sections.map(h => ({ headerText: h, resolvedSection: null }))
      )

      if (res.confirmed.length + res.review.length + res.failed.length === 0
          && res.cash_total_reported == null && res.cash_expected_reported == null
          && !hasReportNotes(res)
          && !res.metadata_errors?.length && !res.unknown_sections.length && !res.multi_day) {
        message.warning('未找到可解析的产品行或报告备注')
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

  function classifyUnknown(headerText: string, resolved: ActiveSection | 'skip') {
    setUnknowns(prev =>
      prev.map(u => u.headerText === headerText ? { ...u, resolvedSection: resolved } : u)
    )
    // Classification applies only to this preview; abandoned reviews teach no aliases.
    setFailed(prev => prev.map(row => row.unknown_header !== headerText ? row :
      resolved === 'skip' ? { ...row, removed: true } : {
        ...row, section: resolved, unknown_header: null,
        qty_pos: resolved === 'cash' ? 0 : row.qty,
        qty_cash: resolved === 'cash' ? row.qty : 0,
      }
    ))
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

  function handleManualSelectReview(row: ReviewRow, p: BackendProduct) {
    patchReview(row._key, { product: p, accepted: true })
  }

  function handleManualSelectFailed(row: FailedRow, p: BackendProduct) {
    patchFailed(row._key, { assigned_product: p })
  }

  // ── Submit ────────────────────────────────────────────────────────────────

  async function handleSubmit() {
    if (!canSubmit) {
      message.warning('请检查日期、门店和现金金额，并解决或明确移除所有待处理行；多日报告须分开导入。')
      return
    }

    const submitDate = parsedDate
    const submitStore = parsedStore
    const payload = [
      ...confirmed.filter(r => !r.removed).map(r => buildPayloadItem(r, r.product, 'confirmed')),
      ...review.filter(r => !r.removed).map(r => ({
        ...buildPayloadItem(r, r.product, 'review'),
        ...matchFeedback(r.candidates, r.product.id, r.originalTopId, r.score),
      })),
      ...failed.filter(r => !r.removed).map(r => ({
        ...buildPayloadItem(r, r.assigned_product!, 'failed'),
        ...matchFeedback(r.candidates, r.assigned_product!.id, r.candidates[0]?.id, r.candidates[0]?.score ?? r.score),
      })),
    ]

    setSubmitting(true)
    try {
      await client.post('/sales/submit_daily_report', {
        date: submitDate,
        store_code: submitStore,
        mode: 'replace',
        classification: 'summary_only',
        report_metadata: {
          cash_actual: cashTotalReported,
          cash_expected: cashExpected,
          ...reportNotes,
        },
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
    row: { section: string; qty_pos: number; qty_cash: number; qty: number; notes: string; box_size?: number | null; loose_qty?: number; raw_name?: string },
    product: BackendProduct,
    source_bucket: string,
  ) {
    const base: any = {
      product_id: product.id, section: row.section, notes: row.notes,
      raw_name: row.raw_name ?? '', source_bucket,
    }
    if (row.section === 'cash') {
      base.qty_cash = row.qty
    } else if (row.section === 'stock_in') {
      base.box_size  = row.box_size
      base.num_boxes = row.qty
      base.loose_qty = row.loose_qty ?? 0
    } else if (row.section === 'break_display' || row.section === 'stock_out') {
      base.qty = row.qty
    } else {
      base.qty_pos = row.qty
    }
    return base
  }

  // ── Derived counts ────────────────────────────────────────────────────────

  const confirmedReady = confirmed.filter(r => !r.removed && isReportRowReady(r)).length
  const reviewAccepted = review.filter(r => !r.removed && isReportRowReady(r)).length
  const failedAssigned = failed.filter(r => !r.removed && isReportRowReady({ ...r, product: r.assigned_product })).length
  const totalReady = confirmedReady + reviewAccepted + failedAssigned
  const activeCount = [...confirmed, ...review, ...failed].filter(r => !r.removed).length
  const blockedRows = activeCount - totalReady
  const pendingReview = review.filter(r => !r.accepted && !r.removed).length
  const unresolvedUnknowns = unknowns.filter(u => u.resolvedSection === null).length
  const validCash = [cashTotalReported, cashExpected].every(v => v == null || (Number.isFinite(v) && v >= 0))
  const validDate = !!parsedDate && /^\d{4}-\d{2}-\d{2}$/.test(parsedDate)
    && !Number.isNaN(Date.parse(parsedDate)) && new Date(parsedDate).toISOString().slice(0, 10) === parsedDate
  const hasMetadata = cashTotalReported != null || cashExpected != null
    || hasReportNotes(reportNotes)
  const validStore = parsedStore !== 'ALL' && stores.some(store => store.code === parsedStore)
  const canSubmit = !multiDay && metadataErrors.length === 0 && validDate && validStore && validCash
    && unresolvedUnknowns === 0 && blockedRows === 0 && (totalReady > 0 || hasMetadata)

  // ── Columns ───────────────────────────────────────────────────────────────

  const qtyCell = (
    row: ConfirmedRow | ReviewRow | FailedRow,
    patchFn: (key: string, p: any) => void,
  ) => {
    const updateQuantity = (qty: number, boxSize = row.box_size, looseQty = row.loose_qty ?? 0) => patchFn(row._key, {
      qty, box_size: boxSize, loose_qty: looseQty,
      qty_pos: row.section === 'cash' ? 0 : qty,
      qty_cash: row.section === 'cash' ? qty : 0,
      flagged: !validReportQuantity({ ...row, qty, box_size: boxSize, loose_qty: looseQty }),
    })
    return (
      <Space size={4} wrap>
        {row.flagged && <Tag color="red" icon={<WarningOutlined />}>请核对数量/包装</Tag>}
        {row.section === 'stock_in' && <span>包数</span>}
        <InputNumber size="small" aria-valuemin={1} step={1} changeOnBlur={false}
          aria-label={`${row.raw_name} ${row.section === 'stock_in' ? '包数' : '数量'}`}
          value={row.qty || null} status={!positiveInteger(row.qty) ? 'error' : undefined}
          style={{ width: 70 }} onChange={v => updateQuantity(v ?? 0)} />
        {row.section === 'stock_in' && (
          <>
            <span>× 件/包</span>
            <InputNumber size="small" aria-valuemin={1} step={1} changeOnBlur={false}
              aria-label={`${row.raw_name} 包装规格`}
              value={row.box_size} status={!positiveInteger(row.box_size) ? 'error' : undefined}
              placeholder="必填" style={{ width: 75 }}
              onChange={v => updateQuantity(row.qty, v)} />
            <span>+ 散件</span>
            <InputNumber size="small" aria-valuemin={0} step={1} changeOnBlur={false}
              aria-label={`${row.raw_name} 散件`} value={row.loose_qty ?? 0} style={{ width: 70 }}
              status={!Number.isSafeInteger(row.loose_qty ?? 0) || (row.loose_qty ?? 0) < 0 ? 'error' : undefined}
              onChange={v => updateQuantity(row.qty, row.box_size, v ?? 0)} />
            {validReportQuantity(row) && <span>合计 {row.qty * row.box_size! + (row.loose_qty ?? 0)} 件</span>}
          </>
        )}
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
            <span style={{ fontSize: 13, fontWeight: 500 }}>{r.product.jizhanming || r.product.name_cn_en || r.product.sku}</span>
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
            <span style={{ fontSize: 13 }}>{r.product?.jizhanming || r.product?.name_cn_en || r.product?.sku}</span>
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
                label: `${c.name_cn_en || c.jizhanming || c.sku} · ${c.sku} (${c.score}%)`,
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
        const score = r.candidates.find(c => c.id === r.product?.id)?.score
        const color = r.accepted ? '#10B981' : (score ?? 0) >= 70 ? '#f59e0b' : '#ef4444'
        return <span style={{ fontWeight: 600, color, fontSize: 13 }}>{score == null ? '手动' : `${score}%`}</span>
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
      render: (_: any, r: ReviewRow) => (
        <Space size={4}>
          {r.accepted ? (
            <Button size="small" onClick={() => patchReview(r._key, { accepted: false })}>撤销</Button>
          ) : (
            <Button size="small" type="primary"
              disabled={!r.product?.id}
              onClick={() => patchReview(r._key, { accepted: true })}>
              接受
            </Button>
          )}
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
          onChange={v => patchFailed(r._key, { section: v, unknown_header: null })} />
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

  const cashDelta = cashDifference(cashTotalReported, cashExpected)

  // ── Render ────────────────────────────────────────────────────────────────

  if (step === 'input') {
    return (
      <div style={{ maxWidth: 800, margin: '0 auto' }}>
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 600, fontSize: 16, color: '#111827', marginBottom: 4 }}>
            Import Daily Report
          </div>
          <div style={{ fontSize: 13, color: '#6b7280' }}>
            Paste one day’s full report. Review the detected date and store before saving.
          </div>
        </div>
        <Input.TextArea
          rows={14}
          value={rawText}
          onChange={e => setRawText(e.target.value)}
          placeholder={`2026.04.01 DT汇总\n卡机汇总：\nchiikawa hipper*1\nsmiski hipper*2\n\n随手记汇总：\n星星人点亮场景*9\n\n入店：\ndimoo奇遇小夜灯 6*2\nsmiski cheer 12*1`}
          style={{ fontFamily: 'monospace', fontSize: 13, marginBottom: 12 }}
        />
        <Space direction="vertical" size={10}>
          <Checkbox checked={useLlm} onChange={event => setUseLlm(event.target.checked)}>
            Use optional AI parsing (sends this pasted report text to the configured Anthropic service)
          </Checkbox>
          <Button type="primary" size="large" loading={parsing} onClick={handleParse}>
            Parse Report
          </Button>
        </Space>
      </div>
    )
  }

  if (step === 'done') {
    return (
      <Alert
        type="success"
        icon={<CheckCircleOutlined />}
        showIcon
        message={`Report imported — ${totalReady} items and report notes saved`}
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
        <label>Date <Input type="date" aria-label="Report date" value={parsedDate ?? ''}
          onChange={e => setParsedDate(e.target.value || null)} style={{ width: 150 }} /></label>
        <label>Store <Select aria-label="Report store" value={parsedStore || undefined}
          onChange={setParsedStore} style={{ width: 130 }}
          options={stores.filter(s => s.code !== 'ALL').map(s => ({ value: s.code, label: s.code }))} /></label>
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

      {(!validDate || !validStore || !validCash) && (
        <Alert type="error" showIcon style={{ marginBottom: 8 }}
          message="请选择有效的日期和具体门店；现金金额须为空或非负数。" />
      )}

      {/* Replace-semantics notice */}
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 8, borderRadius: 8 }}
        message={`对账汇总（summary only）将替换 ${parsedDate ?? '未选日期'} · ${parsedStore || '未选门店'} 已有日报销售及本次报告元数据，不是追加。`}
        description="销售汇总不创建收款或扣减销售库存；入店/出店/拆 Display 仍属于库存操作。有库存历史的日报可能需先对账，服务器会阻止直接替换。随手记 Non-POS 包含现金、e-transfer、微信、支付宝，每行支付方式未指定。"
      />

      {metadataErrors.length > 0 && (
        <Alert type="error" showIcon style={{ marginBottom: 8 }}
          message="报告原文存在错误或冲突，已禁止提交。请返回修改原文后重新解析。"
          description={<div style={{ whiteSpace: 'pre-wrap' }}>{metadataErrors.join('\n')}</div>} />
      )}

      {/* Multi-day paste warning */}
      {multiDay && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 8, borderRadius: 8 }}
          message="检测到多个日期，已禁止提交。请返回并将报告按天分开导入。"
        />
      )}

      {blockedRows > 0 && (
        <Alert type="error" showIcon style={{ marginBottom: 8 }}
          message={`${blockedRows} 行尚未完成。请核对匹配、数量及包装，接受或明确移除每行；不会跳过未完成行。`} />
      )}
      <div style={{ marginBottom: 12 }}>
        <Space wrap>
          <label>Physical cash actual / 实收 <InputNumber aria-label="Physical cash actual"
            aria-valuemin={0} step={0.01} changeOnBlur={false} value={cashTotalReported} onChange={setCashTotalReported} /></label>
          <label>Physical cash expected / 应收 <InputNumber aria-label="Physical cash expected"
            aria-valuemin={0} step={0.01} changeOnBlur={false} value={cashExpected} onChange={setCashExpected} /></label>
          <Tag color={cashDelta == null ? 'default' : cashDelta === 0 ? 'green' : 'orange'}>
            Difference / 差额（实收 − 应收）: {cashDelta == null ? '—' : `CA$${cashDelta.toFixed(2)}`}
          </Tag>
        </Space>
      </div>
      {REPORT_NOTE_SECTIONS.map(({ key, label, description }) => !!reportNotes[key]?.length && (
        <Alert key={key} type="info" showIcon style={{ marginBottom: 12 }}
          message={label}
          description={<><div>{description}</div>
            <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{reportNotes[key]!.join('\n')}</div></>} />
      ))}
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="匹配修正仅在提交成功后保存；安全且无冲突的名称映射可用于以后的日报。"
        description="预览中的选择不会保存。请逐行核对同名记录，接受后再提交。" />

      {/* Unknown section classification alerts */}
      {unknowns.filter(u => u.resolvedSection === null).map(u => (
        <Alert
          key={u.headerText}
          type="warning"
          showIcon
          style={{ marginBottom: 8, borderRadius: 8 }}
          message={
            <Space wrap>
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
                    scroll={{ x: 850, y: 420 }}
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
                      scroll={{ x: 850, y: 400 }}
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
                      scroll={{ x: 850, y: 400 }}
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
