import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Input, Button, Table, Tag, Select, Space,
  Alert, message, AutoComplete, InputNumber, Tooltip, Tabs, Badge, Modal, Checkbox,
} from 'antd'
import {
  CheckCircleOutlined, WarningOutlined, DeleteOutlined,
  QuestionCircleOutlined, CloseCircleOutlined,
} from '@ant-design/icons'
import client from '../../api/client'
import { locationText, reasonText, type ReportStockLine, type ReportStockResult, type ReportStockSide } from '../../api/reportStock'
import { productNames } from '../../lib/productLabel'
import { useAppStore } from '../../store'
import { useIsMobile } from '../../hooks/useIsMobile'
import {
  parseReportBackend,
  type BackendProduct, type BackendCandidate, type ReportAnnotations,
  type BackendConfirmedItem, type BackendReviewItem, type BackendFailedItem, type FormatCheck,
} from '../../api/matcher'
import {
  cashDifference, formatCheckWhere, hasReportNotes, isReportRowReady, matchFeedback, PACKED_SECTIONS, positiveInteger, REPORT_NOTE_SECTIONS,
  reportPayloadQuantities, STOCK_LINE_SECTIONS, stockLineEffect, validReportQuantity,
} from './dailyReportReview'
import { placeText } from './namePicksView'

// ─── Section display metadata ─────────────────────────────────────────────────

const SECTION_META: Record<string, { label: string; color: string; live?: boolean }> = {
  pos:               { label: '卡机 POS',      color: 'blue'    },
  cash:              { label: '随手记 Non-POS',    color: 'green'   },
  refill:            { label: '出库入店',  color: 'geekblue', live: true },
  stock_in:          { label: '入店',      color: 'purple'  },
  stock_out:         { label: '出店',      color: 'orange'  },
  display_sold:      { label: '卖Display', color: 'magenta', live: true },
  break_display:     { label: '拆Display', color: 'red'     },
  display_stock_in:  { label: '入Display', color: 'cyan', live: true },
  claw_prize:        { label: '娃娃机出奖', color: 'gold', live: true },
}

// Section choices for a row: a legacy store keeps its old report sections.
function sectionOptions(live: boolean, stockOnly = false) {
  return Object.entries(SECTION_META)
    .filter(([key, meta]) => (live || !meta.live) && (!stockOnly || (STOCK_LINE_SECTIONS as readonly string[]).includes(key)))
    .map(([value, meta]) => ({ value, label: meta.label }))
}

const isStockLine = (section: string) => (STOCK_LINE_SECTIONS as readonly string[]).includes(section)

function sideText(side?: ReportStockSide) {
  if (!side) return null
  return `${side.sku ?? ''} ${side.name} · ${locationText[side.location] ?? side.location} ${side.before} → ${side.after}`
}

function SectionTag({ section }: { section: string }) {
  const m = SECTION_META[section]
  return m
    ? <Tag color={m.color} style={{ fontSize: 11, margin: 0 }}>{m.label}</Tag>
    : <Tag style={{ fontSize: 11, margin: 0 }}>{section}</Tag>
}

/** The stock a report is about to move, for the manager to confirm before it saves (owner, 2026-11-03). */
function StockLinesList({ lines }: { lines: ReportStockLine[] }) {
  return (
    <div role="list" aria-label="Stock lines" style={{ maxHeight: 420, overflowY: 'auto' }}>
      {lines.map(l => (
        <div role="listitem" key={l.line_key}
          style={{ padding: '8px 0', borderBottom: '1px solid #f0f0f0', display: 'grid', gap: 2, fontSize: 12 }}>
          <Space size={6} wrap>
            <SectionTag section={l.section} />
            <span style={{ fontSize: 13, fontWeight: 500, overflowWrap: 'anywhere' }}>{l.raw}</span>
          </Space>
          {l.from && <span style={{ overflowWrap: 'anywhere' }}>− {sideText(l.from)}</span>}
          {l.to && <span style={{ overflowWrap: 'anywhere' }}>+ {sideText(l.to)}</span>}
          <Space size={4} wrap>
            {l.moving > 0 && <Tag color="blue" style={{ margin: 0 }}>移 Move {l.moving}</Tag>}
            {l.moving < 0 && <Tag color="volcano" style={{ margin: 0 }}>退回 Put back {-l.moving}</Tag>}
            {l.short > 0 && <Tag color="orange" style={{ margin: 0 }}>
              {l.problem === 'not_counted' ? '还没盘点 Not counted' : '不够 Short'} {l.short}
            </Tag>}
            {l.moved_before > 0 && <span style={{ color: '#6b7280' }}>之前已移 Moved before {l.moved_before}</span>}
          </Space>
        </div>
      ))}
    </div>
  )
}

/** 名称记忆: what a remembered pick means for a review row, said where the manager decides (owner, 2026-11-03). */
function pickNote(row: ReviewRow, store: string): { text: string; tone?: 'warn' | 'out' } | null {
  const pick = row.pick
  if (!pick) return null
  const named = pick.product_ids.map(id => row.candidates.find(c => c.id === id))
    .map(c => c ? (c.jizhanming || c.name_cn_en || c.sku) : null).filter(Boolean).join(' / ')
  if (pick.state === 'disputed') return { text: `记住了两个产品 ${named}：你选的会替换两个 Your pick replaces both`, tone: 'out' }
  if (row.product && !pick.product_ids.includes(row.product.id)) return { text: `会替换记住的 Replaces the remembered ${named}`, tone: 'warn' }
  if (pick.state === 'stale') return { text: '记住后产品资料改过，请再确认 The product changed since it was picked', tone: 'warn' }
  if (pick.state === 'confirm') {
    const here = placeText({ store_code: store, section: row.section, display: !!(row.to_display || row.from_display) })
    return { text: `记住的 Remembered${pick.places.length ? ` at ${pick.places.map(placeText).join(', ')}` : ''} · ${here} 第一次，接受后这里也记住 first time here` }
  }
  return { text: '记住的 Remembered · 接受后照旧 accept to keep it' }
}

// ─── Extended item types (frontend state adds _key, accepted, overrides) ──────

type ActiveSection =
  | 'pos' | 'cash' | 'refill' | 'stock_in' | 'stock_out' | 'display_sold' | 'break_display' | 'display_stock_in' | 'claw_prize'

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
      label: `${productNames(p)} (${p.sku})`,
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
  const isMobile = useIsMobile()
  const defaultStore = selectedStore?.code === 'ALL' ? '' : selectedStore?.code ?? ''

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
  const [cashTotalReported, setCashTotalReported] = useState<number | null>(null)
  const [cashExpected, setCashExpected] = useState<number | null>(null)
  const [reportNotes, setReportNotes] = useState<ReportAnnotations>({})
  const [metadataErrors, setMetadataErrors] = useState<string[]>([])
  const [formatChecks,  setFormatChecks]  = useState<FormatCheck[]>([])
  const [multiDay,      setMultiDay]      = useState(false)
  // A live store's report takes its sales off the floor (owner, 2026-10-29): what moved, what could not.
  const [stockResult,   setStockResult]   = useState<ReportStockResult | null>(null)
  // At a live store the stock lines move stock too; the manager confirms them first (owner, 2026-11-03).
  const [stockMode,     setStockMode]     = useState<'live' | 'legacy'>('legacy')
  const [stockPreview,  setStockPreview]  = useState<{ stock: ReportStockResult | null; digest: string; changed?: boolean } | null>(null)
  const live = stockMode === 'live'

  // ── Parse (call backend) ──────────────────────────────────────────────────

  async function handleParse() {
    if (!rawText.trim()) { message.warning('请粘贴日报内容'); return }
    setParsing(true)
    try {
      const res = await parseReportBackend(
        rawText, defaultStore, 'rules', date
      )

      setParsedDate(res.detected_date ?? date)
      setParsedStore(res.store)
      setStockMode(res.stock_mode ?? 'legacy')

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
      setFormatChecks(res.format_checks ?? [])
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

  // A remembered line confirmed by itself; Change sends it back through review, where a new pick replaces it.
  function changeConfirmed(row: ConfirmedRow) {
    setConfirmed(prev => prev.filter(r => r._key !== row._key))
    setReview(prev => [...prev, {
      ...row, _key: `r-${row._key}`, candidates: [{ ...row.product, score: row.score }],
      originalTopId: row.product.id, accepted: false,
    }])
    message.info(`${row.raw_name}: 已移到 Review，选好再接受 Moved to Review; pick and accept it there`)
  }

  function handleManualSelectReview(row: ReviewRow, p: BackendProduct) {
    patchReview(row._key, { product: p, accepted: true })
  }

  function handleManualSelectFailed(row: FailedRow, p: BackendProduct) {
    patchFailed(row._key, { assigned_product: p })
  }

  // ── Submit ────────────────────────────────────────────────────────────────

  async function handleSubmit(confirmedDigest?: string) {
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

    const body = {
      date: submitDate,
      store_code: submitStore,
      mode: 'replace',
      classification: 'summary_only',
      stock_mode: stockMode,
      section_choices: unknowns.filter(u => u.resolvedSection !== null)
        .map(u => ({ header: u.headerText, section: u.resolvedSection })),
      report_metadata: {
        cash_actual: cashTotalReported,
        cash_expected: cashExpected,
        ...reportNotes,
      },
      items: payload,
    }
    setSubmitting(true)
    try {
      if (live && !confirmedDigest) {
        // Nothing saves yet: the server works out what the stock lines move, and the manager confirms it.
        const { data } = await client.post('/sales/submit_daily_report', { ...body, dry_run: true })
        if (data?.needs_confirmation) {
          setStockPreview({ stock: data.stock ?? null, digest: data.stock_digest })
          return
        }
      }
      const { data } = await client.post('/sales/submit_daily_report', { ...body, stock_digest: confirmedDigest })
      setStockPreview(null)
      setStockResult(data?.stock ?? null)
      setStep('done')
    } catch (err: any) {
      const refused = err?.response?.data
      if (refused?.code === 'stock_confirmation_required') {
        // Stock changed since the preview: show what it moves now.
        setStockPreview({ stock: refused.stock ?? null, digest: refused.stock_digest, changed: true })
      } else {
        setStockPreview(null)
        message.error(err?._serverMessage ?? err?.message ?? '提交失败')
      }
    } finally {
      setSubmitting(false)
    }
  }

  function buildPayloadItem(
    row: { section: string; qty_pos: number; qty_cash: number; qty: number; notes: string; box_size?: number | null; loose_qty?: number; raw_name?: string; to_display?: boolean; from_display?: boolean },
    product: BackendProduct,
    source_bucket: string,
  ) {
    return {
      product_id: product.id, section: row.section, notes: row.notes,
      raw_name: row.raw_name ?? '', source_bucket,
      ...reportPayloadQuantities(row),
    }
  }

  // ── Derived counts ────────────────────────────────────────────────────────

  const confirmedReady = confirmed.filter(r => !r.removed && isReportRowReady(r, live)).length
  const reviewAccepted = review.filter(r => !r.removed && isReportRowReady(r, live)).length
  const failedAssigned = failed.filter(r => !r.removed && isReportRowReady({ ...r, product: r.assigned_product }, live)).length
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
        {PACKED_SECTIONS.includes(row.section) && <span>包数</span>}
        <InputNumber size="small" aria-valuemin={1} step={1} changeOnBlur={false}
          aria-label={`${row.raw_name} ${PACKED_SECTIONS.includes(row.section) ? '包数' : '数量'}`}
          value={row.qty || null} status={!positiveInteger(row.qty) ? 'error' : undefined}
          style={{ width: 70 }} onChange={v => updateQuantity(v ?? 0)} />
        {PACKED_SECTIONS.includes(row.section) && (
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

  // A stock line shows what it moves; at a live store the manager can re-file it under another stock section.
  const sectionCell = (row: ReviewRow | FailedRow, patchFn: (key: string, p: any) => void, choose: boolean) => {
    const effect = live ? stockLineEffect(row) : null
    const shown = row.section === 'stock_out' ? 'from_display' : 'to_display'
    return (
      <Space direction="vertical" size={2}>
        {choose ? (
          <Select size="small" value={row.section} style={{ width: 110 }} aria-label={`${row.raw_name} 分区`}
            options={sectionOptions(live, live && isStockLine(row.section))}
            onChange={v => patchFn(row._key, {
              section: v, unknown_header: null,
              qty_pos: v === 'cash' ? 0 : row.qty, qty_cash: v === 'cash' ? row.qty : 0,
              box_size: PACKED_SECTIONS.includes(v) ? (row.box_size ?? 1) : row.box_size,
            })} />
        ) : <SectionTag section={row.section} />}
        {effect && <span style={{ fontSize: 11, color: '#6b7280' }}>{effect}</span>}
        {live && ['refill', 'stock_in', 'stock_out'].includes(row.section) && (
          <Checkbox checked={!!row[shown]} onChange={e => patchFn(row._key, { [shown]: e.target.checked })}>
            <span style={{ fontSize: 11 }}>{row.section === 'stock_out' ? '从 Display' : '放 Display'}</span>
          </Checkbox>
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
            {r.pick && <Tooltip title={`名称记忆：${r.pick.places.map(placeText).join(', ')} 确认过 Confirmed there before`}>
              <Tag style={{ fontSize: 11, margin: 0 }}>已记住 Remembered</Tag>
            </Tooltip>}
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
      title: '分区', key: 'sec', width: 110,
      render: (_: any, r: ConfirmedRow) => {
        const effect = live ? stockLineEffect(r) : null
        return effect ? <Space direction="vertical" size={2}><SectionTag section={r.section} />
          <span style={{ fontSize: 11, color: '#6b7280' }}>{effect}</span></Space> : <SectionTag section={r.section} />
      },
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
      title: '', key: 'del', width: 90,
      render: (_: any, r: ConfirmedRow) => (
        <Space size={4}>
          {r.pick && <Button size="small" aria-label={`Change ${r.raw_name}`} onClick={() => changeConfirmed(r)}>改 Change</Button>}
          <Tooltip title="移除">
            <Button size="small" type="text" danger icon={<DeleteOutlined />}
              onClick={() => patchConfirmed(r._key, { removed: true })} />
          </Tooltip>
        </Space>
      ),
    },
  ]

  // 名称记忆 note: under the match on desktop; on a phone under the written name, which a phone sees without scrolling.
  const pickNoteLine = (r: ReviewRow) => {
    const note = pickNote(r, parsedStore)
    return note && <span style={{ fontSize: 11, lineHeight: 1.45, overflowWrap: 'anywhere',
      color: note.tone === 'out' ? '#b4322a' : note.tone === 'warn' ? '#946200' : '#6b7280' }}>{note.text}</span>
  }

  const reviewColumns = [
    {
      title: '输入', dataIndex: 'raw_name', width: 130,
      render: (v: string, r: ReviewRow) => isMobile && r.pick
        ? <Space direction="vertical" size={2}><span style={{ fontSize: 13 }}>{v}</span>{pickNoteLine(r)}</Space>
        : <span style={{ fontSize: 13 }}>{v}</span>,
    },
    {
      title: '候选匹配', key: 'match', width: 300,
      render: (_: any, r: ReviewRow) => {
        const noteLine = !isMobile && pickNoteLine(r)
        if (r.accepted) return (
          <Space size={2} direction="vertical">
            <Space size={4}>
              <Tag color="green"><CheckCircleOutlined /></Tag>
              <span style={{ fontSize: 13 }}>{r.product?.jizhanming || r.product?.name_cn_en || r.product?.sku}</span>
              <Tag style={{ fontSize: 11 }}>{r.product?.sku}</Tag>
            </Space>
            {noteLine}
          </Space>
        )
        return (
          <Space size={4} direction="vertical" style={{ width: '100%' }}>
            {noteLine}
            <Select
              size="small" value={r.product?.id} style={{ width: 220 }}
              onChange={v => {
                const c = r.candidates?.find((x: BackendCandidate) => x.id === v)
                if (c) patchReview(r._key, { product: c as BackendProduct })
              }}
              options={(r.candidates || []).map((c: BackendCandidate) => ({
                value: c.id,
                label: `${productNames(c)} · ${c.sku} (${c.score}%)`,
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
      title: '分区', key: 'sec', width: 130,
      render: (_: any, r: ReviewRow) => sectionCell(r, patchReview, live && isStockLine(r.section)),
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
      title: '分区', key: 'sec', width: 130,
      render: (_: any, r: FailedRow) => sectionCell(r, patchFailed, true),
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
        description={<>
          {parsedDate && <div>Date: {parsedDate}  Store: {parsedStore}</div>}
          {stockResult && <div>
            {!!stockResult.lines?.length && <div>
              库存行 Stock lines: {stockResult.lines.filter(l => l.moving > 0).length} moved
              {stockResult.lines.some(l => l.moving < 0) ? `, ${stockResult.lines.filter(l => l.moving < 0).length} put back` : ''}
              {stockResult.moves_short?.length ? `, ${stockResult.moves_short.length} short` : ''}
            </div>}
            {!!stockResult.moves_short?.length && <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
              {stockResult.moves_short.map(row => <li key={row.product_id}>
                {row.sku ? `${row.sku} · ` : ''}{row.name}: {row.quantity} ({reasonText.move_short.zh} {row.where.map(code => locationText[code] ?? code).join(', ')})
              </li>)}
            </ul>}
            库存 Floor stock: {stockResult.taken} taken off{stockResult.returned ? `, ${stockResult.returned} put back` : ''}
            {stockResult.short.length > 0 && <>
              <div style={{ marginTop: 6 }}>未扣 Not taken off:</div>
              <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                {stockResult.short.map(row => <li key={row.product_id}>
                  {row.sku ? `${row.sku} · ` : ''}{row.name}: {row.quantity} ({reasonText[row.reason].zh} {reasonText[row.reason].en})
                </li>)}
              </ul>
            </>}
            {stockResult.counted_same_day.length > 0 && <>
              <div style={{ marginTop: 6 }}>当天盘点过 Taken off after the floor was counted today. If the count was after these sales, count again:</div>
              <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                {stockResult.counted_same_day.map(row => <li key={row.product_id}>
                  {row.sku ? `${row.sku} · ` : ''}{row.name}: {row.quantity}
                </li>)}
              </ul>
            </>}
            {(stockResult.short.length > 0 || stockResult.counted_same_day.length > 0 || !!stockResult.moves_short?.length) && <div style={{ marginTop: 6 }}>
              These wait on the manager home until checked: <Link to="/sales/report-stock">日报库存核对 Report stock checks</Link>
            </div>}
          </div>}
        </>}
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

  const previewLines = stockPreview?.stock?.lines ?? []
  const previewChanges = previewLines.filter(l => l.moving || l.short)
  const repasted = previewLines.some(l => l.moved_before > 0 || l.moving < 0)

  return (
    <div>
      <Modal open={!!stockPreview} width={860} title="确认库存变化 Confirm stock changes"
        okText="确认并保存 Confirm & save" cancelText="返回 Back" confirmLoading={submitting}
        onCancel={() => setStockPreview(null)}
        onOk={() => stockPreview && handleSubmit(stockPreview.digest)}>
        {stockPreview?.changed && <Alert type="warning" showIcon style={{ marginBottom: 8 }}
          message="库存在预览后变了，下面是现在会移的。Stock changed since the preview; this is what it moves now." />}
        {repasted && <Alert type="info" showIcon style={{ marginBottom: 8 }}
          message="这一天之前粘贴过：只移差额，删掉的行会退回。This day was pasted before: only the difference moves, and lines no longer on it are put back." />}
        {previewChanges.some(l => l.short) && <Alert type="warning" showIcon style={{ marginBottom: 8 }}
          message="不够的部分不会移，会留在 日报库存核对 等你处理。What a source lacks does not move and waits in Report stock checks." />}
        <StockLinesList lines={previewChanges} />
        {stockPreview?.stock && <div style={{ marginTop: 8, fontSize: 12, color: '#6b7280' }}>
          销售 Sales: {stockPreview.stock.taken} 从楼下扣 off the floor
          {stockPreview.stock.returned ? `, ${stockPreview.stock.returned} 退回 put back` : ''}
          {stockPreview.stock.from_display?.length ? `, ${stockPreview.stock.from_display.reduce((n, r) => n + r.quantity, 0)} 从 Display 卖出 sold from display` : ''}
          {stockPreview.stock.short.length ? `, ${stockPreview.stock.short.length} 未扣 not taken off` : ''}
        </div>}
      </Modal>
      {/* Summary bar */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 16 }}>
        <label>Date <Input type="date" aria-label="Report date" value={parsedDate ?? ''}
          onChange={e => setParsedDate(e.target.value || null)} style={{ width: 150 }} /></label>
        <label>Store <Select aria-label="Report store" value={parsedStore || undefined}
          onChange={setParsedStore} style={{ width: 130 }}
          options={stores.filter(s => s.code !== 'ALL').map(s => ({ value: s.code, label: s.code }))} /></label>
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
            <Button type="primary" loading={submitting} disabled={!canSubmit} onClick={() => handleSubmit()}>
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
        description={live
          ? '这家店已上线：销售从楼下扣；出库入店、入店、出店、卖/拆/入 Display、娃娃机出奖按行移库存，保存前会让你确认。重新粘贴同一天只移差额。出库和入娃娃机不移库存，请用 调拨 Transfer 和 娃娃机 补货 记录。随手记 Non-POS 包含现金、e-transfer、微信、支付宝，每行支付方式未指定。'
          : '销售汇总不创建收款或扣减销售库存；入店/出店/拆 Display 仍属于库存操作。有库存历史的日报可能需先对账，服务器会阻止直接替换。随手记 Non-POS 包含现金、e-transfer、微信、支付宝，每行支付方式未指定。'}
      />

      {metadataErrors.length > 0 && (
        <Alert type="error" showIcon style={{ marginBottom: 8 }}
          message="报告原文存在错误或冲突，已禁止提交。请返回修改原文后重新解析。"
          description={<div style={{ whiteSpace: 'pre-wrap' }}>{metadataErrors.join('\n')}</div>} />
      )}

      {/* Format checks (owner, 2026-11-06): what may move stock wrongly, by line in the paste. */}
      {formatChecks.length > 0 && (
        <Alert type={formatChecks.some(c => c.level === 'fix') ? 'warning' : 'info'} showIcon
          style={{ marginBottom: 8, borderRadius: 8 }}
          message={`原文检查 Format check：${formatChecks.filter(c => c.level === 'fix').length} 处要改 to fix，`
            + `${formatChecks.filter(c => c.level === 'check').length} 处请确认 to check`}
          description={<>
            <div>按行号对照原文。要改的多半写错了，会让库存移错；请确认的可能是故意的。Line numbers count from the top of the paste.</div>
            <ul aria-label="Format checks" style={{ margin: '6px 0 0', paddingLeft: 18 }}>
              {formatChecks.map((check, index) => (
                <li key={`${check.lines.join('-')}-${index}`} style={{ marginBottom: 6 }}>
                  <Tag color={check.level === 'fix' ? 'orange' : 'default'} style={{ marginRight: 6 }}>
                    {check.level === 'fix' ? '要改 Fix' : '确认 Check'}
                  </Tag>
                  <strong>{formatCheckWhere(check)}</strong>
                  {check.text.filter(Boolean).map((text, i) => (
                    <code key={i} style={{ display: 'block', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{text}</code>
                  ))}
                  <div style={{ overflowWrap: 'anywhere' }}>{check.problem}</div>
                </li>
              ))}
            </ul>
          </>} />
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
        message="名称记忆：你接受的名字会被记住。同一家店、同一个分区下次自动确认；别的店或分区先问一次，已经选好。"
        description={<>Picks are saved only when this report saves; a preview saves nothing. A different product replaces the old pick.
          See, change or forget picks in <Link to="/sales/name-picks">名称记忆 Name picks</Link>.</>} />

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
                  ...sectionOptions(false),
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
