import { useState, useEffect } from 'react'
import {
  Input, Button, Tabs, Table, Tag, Select, Space,
  Alert, message, AutoComplete, InputNumber, Tooltip, Spin, Badge,
} from 'antd'
import {
  CheckCircleOutlined, WarningOutlined, DeleteOutlined, QuestionCircleOutlined,
} from '@ant-design/icons'
import client from '../../api/client'
import {
  batchMatch, saveAlias, saveSectionAlias, getSectionAliases,
  detectSectionHeader, cleanName, isHeaderLine,
  type MatchResult, type MatchCandidate, type SectionType, type SectionAlias,
} from '../../api/matcher'

// ─── Types ────────────────────────────────────────────────────────────────────

type ActiveSection = Exclude<SectionType, 'ignore' | 'unknown'>

interface MatchedItem {
  _key: string
  rawName: string
  section: ActiveSection
  unknownHeader?: string    // which unknown header this came from (before classification)
  qty_pos: number
  qty_cash: number
  qty: number               // break_display / stock ops
  box_size?: number         // stock_in: units per box
  num_boxes?: number        // stock_in: number of boxes
  notes: string
  product_id?: number
  sku?: string
  jizhanming?: string
  candidates?: MatchCandidate[]
  status: 'matched' | 'fuzzy' | 'unmatched' | 'skipped'
  flagged?: boolean
}

interface UnknownSectionInfo {
  headerText: string
  pendingType: ActiveSection | 'skip' | null
}

const SECTION_LABELS: Record<ActiveSection, { label: string; color: string }> = {
  pos:               { label: '卡机',     color: 'blue'    },
  cash:              { label: '随手记',   color: 'green'   },
  stock_in:          { label: '入店',     color: 'purple'  },
  stock_out:         { label: '出店',     color: 'orange'  },
  claw:              { label: '娃娃机',   color: 'gold'    },
  sell_display:      { label: '卖Display', color: 'cyan'   },
  break_display:     { label: '拆Display', color: 'red'    },
  employee_discount: { label: '员工折扣', color: 'magenta' },
}

const SECTION_OPTIONS = (Object.keys(SECTION_LABELS) as ActiveSection[]).map(k => ({
  value: k,
  label: SECTION_LABELS[k].label,
}))

// ─── Parsing helpers ──────────────────────────────────────────────────────────

const _DATE_RE   = /(\d{4})[.\-\/](\d{1,2})[.\-\/](\d{1,2})/
const _STORE_RE  = /\b([A-Za-z\u4e00-\u9fa5]{1,6})(?:店|汇总|DT|dt)/i

function extractDateStore(firstLine: string): { date: string | null; store: string } {
  const dm = firstLine.match(_DATE_RE)
  const date = dm ? `${dm[1]}-${dm[2].padStart(2,'0')}-${dm[3].padStart(2,'0')}` : null
  // e.g. "2026.04.01 DT汇总" → store="DT"
  const storeMatch = firstLine.match(/([A-Za-z\u4e00-\u9fff]+)(?:店|汇总)/)
  const store = storeMatch ? storeMatch[1].toUpperCase() : 'DT'
  return { date, store }
}

interface ParsedLine {
  rawName: string
  section: ActiveSection
  unknownHeader?: string
  qty_pos: number
  qty_cash: number
  qty: number
  box_size?: number
  num_boxes?: number
  notes: string
  flagged: boolean
}

function parseLine(raw: string, section: ActiveSection): ParsedLine {
  const t   = raw.trim()
  const base = { section, notes: '', qty_pos: 0, qty_cash: 0, qty: 0, flagged: false }

  // ── employee_discount: extract "购入 NAME*QTY" ─────────────────────────────
  if (section === 'employee_discount') {
    const m = t.match(/购入\s*(.+?)[\*＊]\s*(\d+)/)
    if (m) {
      const qty = parseInt(m[2], 10) || 1
      return { ...base, rawName: m[1].trim(), qty, qty_pos: qty, notes: 'employee_discount' }
    }
    // No "购入" pattern — skip line
    return { ...base, rawName: t, qty: 0, flagged: true }
  }

  // ── stock_in: try "NAME BOX_SIZE*NUM_BOXES" first ─────────────────────────
  if (section === 'stock_in') {
    // e.g. "dimoo奇遇小夜灯 6*2" → name=dimoo奇遇小夜灯, box_size=6, num_boxes=2
    const stockM = t.match(/^(.+?)\s+(\d+)[\*＊](\d+)\s*$/)
    if (stockM) {
      const box_size  = parseInt(stockM[2], 10)
      const num_boxes = parseInt(stockM[3], 10)
      const qty       = box_size * num_boxes
      return { ...base, rawName: stockM[1].trim(), qty, qty_pos: qty, box_size, num_boxes }
    }
    // Fall through to standard *qty parse
  }

  // ── Standard: NAME*QTY ────────────────────────────────────────────────────
  const starIdx = t.lastIndexOf('*')
  if (starIdx > 0) {
    const rawName = t.slice(0, starIdx).trim()
    const qty     = parseInt(t.slice(starIdx + 1).trim(), 10) || 0
    if (rawName && qty > 0) {
      if (section === 'cash') return { ...base, rawName, qty, qty_cash: qty }
      if (section === 'stock_in') {
        // Simple *N format for stock_in means num_boxes=qty, box_size=1
        return { ...base, rawName, qty, qty_pos: qty, box_size: 1, num_boxes: qty }
      }
      return { ...base, rawName, qty, qty_pos: qty }
    }
  }

  // ── Tab-separated (Excel paste fallback) ─────────────────────────────────
  if (t.includes('\t')) {
    const parts  = t.split('\t').map(s => s.trim())
    const qty_pos  = parseInt(parts[1] || '0', 10) || 0
    const qty_cash = parseInt(parts[2] || '0', 10) || 0
    if (parts[0]) {
      return { ...base, rawName: parts[0], qty: qty_pos + qty_cash, qty_pos, qty_cash }
    }
  }

  // ── Flag: quantity unknown ────────────────────────────────────────────────
  return { ...base, rawName: t, qty: 0, flagged: true }
}

function parseReport(
  text: string,
  savedAliases: SectionAlias[],
): { detectedDate: string | null; store: string; parsed: ParsedLine[]; unknownSections: UnknownSectionInfo[] } {
  const lines = text.split('\n').map(l => l.trim()).filter(Boolean)
  if (!lines.length) return { detectedDate: null, store: 'DT', parsed: [], unknownSections: [] }

  const { date: detectedDate, store } = extractDateStore(lines[0])

  // Scan section boundaries
  type Boundary = { idx: number; section: SectionType; headerText: string }
  const boundaries: Boundary[] = []
  for (let i = 0; i < lines.length; i++) {
    const sec = detectSectionHeader(lines[i], savedAliases)
    if (sec !== null) boundaries.push({ idx: i, section: sec, headerText: lines[i] })
  }

  function sectionAt(lineIdx: number): { section: SectionType; headerText: string } {
    let cur: SectionType = 'pos'
    let hdr = ''
    for (const b of boundaries) {
      if (b.idx <= lineIdx) { cur = b.section; hdr = b.headerText }
      else break
    }
    return { section: cur, headerText: hdr }
  }

  const parsed: ParsedLine[]             = []
  const unknownSet: Map<string, boolean> = new Map()

  for (let i = 0; i < lines.length; i++) {
    if (isHeaderLine(lines[i], savedAliases)) continue
    const { section, headerText } = sectionAt(i)
    if (section === 'ignore') continue

    if (section === 'unknown') {
      // Collect as unknown — product names queued under this header
      const line = parseLine(lines[i], 'pos') // parse as pos temporarily
      parsed.push({ ...line, section: 'pos', unknownHeader: headerText })
      unknownSet.set(headerText, true)
      continue
    }

    if (section === 'stock_out') continue  // not yet implemented

    // For employee_discount, don't comma-split the line
    if (section === 'employee_discount') {
      const p = parseLine(lines[i], 'employee_discount')
      if (!p.flagged) parsed.push(p)
      continue
    }

    // For other sections, comma-split lines (handles "item1, item2" patterns)
    const subLines = lines[i].split(/[,，]/).map(s => s.trim()).filter(Boolean)
    for (const sub of subLines) {
      if (!sub || isHeaderLine(sub, savedAliases)) continue
      parsed.push(parseLine(sub, section as ActiveSection))
    }
  }

  const unknownSections: UnknownSectionInfo[] = Array.from(unknownSet.keys()).map(h => ({
    headerText: h,
    pendingType: null,
  }))

  return { detectedDate, store, parsed, unknownSections }
}

// ─── ProductPicker ────────────────────────────────────────────────────────────

function ProductPicker({ onSelect }: { onSelect: (p: any) => void }) {
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
    <AutoComplete size="small" style={{ width: 190 }} placeholder="搜索产品..."
      options={opts} onSearch={search}
      onSelect={(_: any, opt: any) => onSelect(opt.product)} />
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

interface Props {
  date: string
  onComplete: (date: string, store: string) => void
}

export default function DailyReportEntry({ date, onComplete }: Props) {
  const [step,        setStep]        = useState<'input' | 'review' | 'done'>('input')
  const [rawText,     setRawText]     = useState('')
  const [parsedDate,  setParsedDate]  = useState<string | null>(null)
  const [parsedStore, setParsedStore] = useState('DT')
  const [items,       setItems]       = useState<MatchedItem[]>([])
  const [unknowns,    setUnknowns]    = useState<UnknownSectionInfo[]>([])
  const [savedAliases, setSavedAliases] = useState<SectionAlias[]>([])
  const [matching,    setMatching]    = useState(false)
  const [submitting,  setSubmitting]  = useState(false)
  const [activeTab,   setActiveTab]   = useState<string>('')

  useEffect(() => {
    getSectionAliases().then(setSavedAliases).catch(() => {})
  }, [])

  function updateItem(key: string, patch: Partial<MatchedItem>) {
    setItems(prev => prev.map(it => it._key === key ? { ...it, ...patch } : it))
  }
  function removeItem(key: string) {
    setItems(prev => prev.filter(it => it._key !== key))
  }

  async function handleManualSelect(key: string, rawName: string, p: any) {
    updateItem(key, { product_id: p.id, sku: p.sku, jizhanming: p.jizhanming, status: 'matched', candidates: undefined })
    try { await saveAlias(p.id, rawName) } catch { /* ignore */ }
  }

  async function handleParse() {
    if (!rawText.trim()) { message.warning('请粘贴日报内容'); return }
    setMatching(true)

    const { detectedDate, store, parsed, unknownSections } = parseReport(rawText, savedAliases)
    setParsedDate(detectedDate)
    setParsedStore(store)
    setUnknowns(unknownSections)

    if (!parsed.length) {
      message.warning('未找到可解析的产品行')
      setMatching(false)
      return
    }

    let matchResults: MatchResult[]
    try {
      matchResults = await batchMatch(parsed.map(p => p.rawName))
    } catch {
      message.error('匹配失败，请重试')
      setMatching(false)
      return
    }

    const out: MatchedItem[] = matchResults.map((r, i) => {
      if (r.status === 'skipped') return null
      const top = r.candidates[0]
      return {
        ...parsed[i],
        _key: `${i}-${r.query}`,
        product_id:  top?.id,
        sku:         top?.sku,
        jizhanming:  top?.jizhanming,
        candidates:  r.candidates.length > 1 ? r.candidates : undefined,
        status:      r.status,
      } as MatchedItem
    }).filter(Boolean) as MatchedItem[]

    setItems(out)
    setMatching(false)

    // Set default tab to first non-unknown section with items
    const firstSec = out.find(i => i.section !== ('unknown' as any))?.section ?? out[0]?.section
    if (firstSec) setActiveTab(firstSec)

    setStep('review')
  }

  async function classifyUnknown(headerText: string, newSection: ActiveSection | 'skip') {
    // Update unknown section state
    setUnknowns(prev => prev.map(u =>
      u.headerText === headerText ? { ...u, pendingType: newSection } : u
    ))
    // Reclassify items belonging to this unknown section
    if (newSection !== 'skip') {
      setItems(prev => prev.map(it =>
        it.unknownHeader === headerText ? { ...it, section: newSection } : it
      ))
    } else {
      // Skip: remove those items
      setItems(prev => prev.filter(it => it.unknownHeader !== headerText))
    }
    // Save alias so it's auto-resolved next time
    try {
      await saveSectionAlias(headerText, newSection === 'skip' ? 'ignore' : newSection)
      setSavedAliases(await getSectionAliases())
    } catch { /* ignore */ }
  }

  async function handleSubmit() {
    const unresolved = unknowns.filter(u => u.pendingType === null)
    if (unresolved.length) {
      message.warning(`请先处理 ${unresolved.length} 个未识别的章节`)
      return
    }

    const toSubmit = items.filter(i => i.product_id && !i.flagged && i.section !== ('unknown' as any))
    if (!toSubmit.length) { message.warning('没有可提交的条目'); return }

    setSub(true)
    const submitDate  = parsedDate ?? date
    const submitStore = parsedStore

    const payload = toSubmit.map(i => {
      const base: any = { product_id: i.product_id, section: i.section, notes: i.notes }
      if (i.section === 'pos' || i.section === 'sell_display' || i.section === 'claw' || i.section === 'employee_discount') {
        base.qty_pos = i.qty_pos
      } else if (i.section === 'cash') {
        base.qty_cash = i.qty_cash
      } else if (i.section === 'break_display') {
        base.qty = i.qty
      } else if (i.section === 'stock_in') {
        base.box_size  = i.box_size  ?? 1
        base.num_boxes = i.num_boxes ?? i.qty
      }
      return base
    })

    try {
      await client.post('/sales/submit_daily_report', {
        date: submitDate, store: submitStore, items: payload,
      })
      setStep('done')
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '提交失败')
    } finally {
      setSub(false)
    }
  }

  // setSub helper (avoids extra state)
  function setSub(v: boolean) { setSubmitting(v) }

  // ── Derived counts ───────────────────────────────────────────────────────
  const unresolvedUnknowns = unknowns.filter(u => u.pendingType === null).length
  const unresolvedItems    = items.filter(i => i.status === 'unmatched' || i.flagged).length
  const totalReady         = items.filter(i => i.product_id && !i.flagged && i.section !== ('unknown' as any)).length

  // ── Section tabs ─────────────────────────────────────────────────────────
  const sectionOrder: Array<ActiveSection | 'unknown'> = [
    'pos','cash','claw','sell_display','employee_discount','break_display','stock_in',
  ]

  const itemsBySection = (sec: string) => items.filter(i =>
    sec === 'unknown' ? i.section === ('unknown' as any) : i.section === sec
  )

  const unknownHeaders = [...new Set(
    items.filter(i => i.unknownHeader).map(i => i.unknownHeader!)
  )]

  // Build tabs
  const tabSections = [
    ...sectionOrder.filter(s => itemsBySection(s).length > 0 && s !== 'unknown'),
    ...unknownHeaders,
  ]

  // ── Review table columns ─────────────────────────────────────────────────
  function reviewColumns(section: ActiveSection) {
    const isStock = section === 'stock_in' || section === 'break_display'
    const isCash  = section === 'cash'
    return [
      {
        title: '输入名称', dataIndex: 'rawName', width: 140,
        render: (v: string, r: MatchedItem) => (
          <span style={{ color: r.status === 'unmatched' ? '#cf1322' : undefined, fontSize: 13 }}>{v}</span>
        ),
      },
      {
        title: '匹配产品', key: 'm', width: 220,
        render: (_: any, r: MatchedItem) => {
          if (r.status === 'unmatched') return (
            <Space size={4}>
              <Tag color="red">未匹配</Tag>
              <ProductPicker onSelect={p => handleManualSelect(r._key, r.rawName, p)} />
            </Space>
          )
          if (r.candidates && r.candidates.length > 1) return (
            <Select size="small" value={r.product_id} style={{ width: 200 }}
              onChange={v => {
                const c = r.candidates!.find(x => x.id === v)
                if (c) handleManualSelect(r._key, r.rawName, c)
              }}
              options={r.candidates.map(c => ({ value: c.id, label: `${c.jizhanming || c.name_cn_en || c.sku} (${c.sku})` }))}
            />
          )
          return (
            <Space size={4}>
              <Tag color="green"><CheckCircleOutlined /></Tag>
              <span style={{ fontSize: 13 }}>{r.jizhanming}</span>
              <Tag style={{ fontSize: 11 }}>{r.sku}</Tag>
            </Space>
          )
        },
      },
      {
        title: isStock ? '数量' : (isCash ? '现金/转账' : '数量'), key: 'qty', width: 130,
        render: (_: any, r: MatchedItem) => {
          if (r.flagged) return (
            <Space size={4}>
              <Tag color="red" icon={<WarningOutlined />}>无数量</Tag>
              <InputNumber size="small" min={0} value={0} style={{ width: 55 }}
                onChange={v => {
                  const q = v ?? 0
                  updateItem(r._key, {
                    qty: q, qty_pos: isCash ? 0 : q, qty_cash: isCash ? q : 0, flagged: false,
                  })
                }} />
            </Space>
          )
          if (section === 'stock_in' && r.box_size != null && r.num_boxes != null) return (
            <Space size={4}>
              <Tag style={{ fontSize: 11 }}>{r.box_size}端</Tag>
              <span style={{ color: '#9ca3af' }}>×</span>
              <Tag style={{ fontSize: 11 }}>{r.num_boxes}箱</Tag>
              <span style={{ fontWeight: 600, color: '#6366F1' }}>={r.qty}</span>
            </Space>
          )
          if (section === 'break_display') return (
            <InputNumber size="small" min={1} value={r.qty || 1} style={{ width: 65 }}
              onChange={v => updateItem(r._key, { qty: v ?? 1 })} />
          )
          if (isCash) return (
            <InputNumber size="small" min={0} value={r.qty_cash} style={{ width: 65 }}
              onChange={v => updateItem(r._key, { qty_cash: v ?? 0, qty: v ?? 0 })} />
          )
          return (
            <InputNumber size="small" min={0} value={r.qty_pos} style={{ width: 65 }}
              onChange={v => updateItem(r._key, { qty_pos: v ?? 0, qty: v ?? 0 })} />
          )
        },
      },
      {
        title: '备注', dataIndex: 'notes', width: 90,
        render: (v: string, r: MatchedItem) => (
          <Input size="small" value={v}
            onChange={e => updateItem(r._key, { notes: e.target.value })} />
        ),
      },
      {
        title: '', key: 'del', width: 40,
        render: (_: any, r: MatchedItem) => (
          <Tooltip title="移除">
            <Button size="small" type="text" danger icon={<DeleteOutlined />}
              onClick={() => removeItem(r._key)} />
          </Tooltip>
        ),
      },
    ]
  }

  // ── Render ────────────────────────────────────────────────────────────────

  if (step === 'input') {
    return (
      <div style={{ maxWidth: 800, margin: '0 auto' }}>
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 600, fontSize: 16, color: '#111827', marginBottom: 4 }}>
            Import Daily Report
          </div>
          <div style={{ fontSize: 13, color: '#6b7280' }}>
            Paste the full end-of-day report. Date and store will be detected automatically.
          </div>
        </div>
        <Input.TextArea
          rows={14}
          value={rawText}
          onChange={e => setRawText(e.target.value)}
          placeholder={`2026.04.01 DT汇总\n卡机汇总：\nchiikawa hipper*1\nsmiski hipper*2\n\n随手记汇总：\n星星人点亮场景*9\n\n入店：\ndimoo奇遇小夜灯 6*2\nsmiski cheer 12*1`}
          style={{ fontFamily: 'monospace', fontSize: 13, marginBottom: 12 }}
        />
        <Button type="primary" size="large" loading={matching} onClick={handleParse}>
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

  const canSubmit = unresolvedUnknowns === 0 && totalReady > 0

  return (
    <div>
      {/* Summary bar */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 16 }}>
        {parsedDate && (
          <Tag color="blue" style={{ fontSize: 13 }}>{parsedDate} · {parsedStore}</Tag>
        )}
        <Tag color="green">{totalReady} ready</Tag>
        {unresolvedItems > 0 && <Tag color="orange">{unresolvedItems} need attention</Tag>}
        {unresolvedUnknowns > 0 && (
          <Tag color="red" icon={<QuestionCircleOutlined />}>{unresolvedUnknowns} unknown sections</Tag>
        )}
        <div style={{ marginLeft: 'auto' }}>
          <Space>
            <Button onClick={() => setStep('input')}>← Back</Button>
            <Button type="primary" loading={submitting} disabled={!canSubmit} onClick={handleSubmit}>
              Confirm & Save {totalReady} items
            </Button>
          </Space>
        </div>
      </div>

      {/* Unknown section alerts */}
      {unknowns.filter(u => u.pendingType === null).map(u => (
        <Alert
          key={u.headerText}
          type="warning"
          showIcon
          style={{ marginBottom: 8, borderRadius: 8 }}
          message={
            <Space>
              <span>Unknown section: <strong>{u.headerText}</strong> — what type is this?</span>
              <Select
                size="small"
                style={{ width: 120 }}
                placeholder="Classify..."
                options={[...SECTION_OPTIONS, { value: 'skip', label: '跳过/忽略' }]}
                onChange={(v: ActiveSection | 'skip') => classifyUnknown(u.headerText, v)}
              />
            </Space>
          }
        />
      ))}

      <Spin spinning={false}>
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={tabSections.map(sectionKey => {
            const isUnknownHeader = !Object.keys(SECTION_LABELS).includes(sectionKey)
            const sectionItems = isUnknownHeader
              ? items.filter(i => i.unknownHeader === sectionKey)
              : items.filter(i => i.section === sectionKey)

            const issueCount = sectionItems.filter(i => i.status === 'unmatched' || i.flagged).length
            const sec        = isUnknownHeader ? null : SECTION_LABELS[sectionKey as ActiveSection]

            const label = isUnknownHeader
              ? <span style={{ color: '#f59e0b' }}>❓ {sectionKey}</span>
              : <Badge count={issueCount} size="small" offset={[4, -2]}>
                  <Tag color={sec!.color} style={{ margin: 0 }}>{sec!.label}</Tag>
                </Badge>

            return {
              key:   sectionKey,
              label,
              children: (
                <Table
                  size="small"
                  rowKey="_key"
                  dataSource={sectionItems}
                  columns={reviewColumns(
                    isUnknownHeader ? 'pos' : sectionKey as ActiveSection
                  )}
                  pagination={false}
                  scroll={{ y: 400 }}
                />
              ),
            }
          })}
        />
      </Spin>
    </div>
  )
}
