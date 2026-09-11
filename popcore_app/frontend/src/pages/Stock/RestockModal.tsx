import { useState, useEffect, useMemo, useRef } from 'react'
import {
  Modal, Steps, Select, InputNumber, Input, Button, Space,
  AutoComplete, message, Alert, DatePicker,
} from 'antd'
import dayjs from 'dayjs'
import client from '../../api/client'
import { useAppStore } from '../../store'

type Op = 'restock_upstairs' | 'ru_dian' | 'adjust'

interface InitialProduct {
  id: number
  jizhanming: string
  sku: string
  product_type?: string
  boxes_per_dan?: number | null
}

interface Props {
  open: boolean
  onClose: () => void
  onDone: () => void
  initialProduct?: InitialProduct
}

export default function RestockModal({ open, onClose, onDone, initialProduct }: Props) {
  const { selectedStore } = useAppStore()
  const [step, setStep]       = useState(0)
  const [op, setOp]           = useState<Op>('restock_upstairs')
  const [date, setDate]       = useState(dayjs())
  const [searchVal, setSearch] = useState('')
  const [options, setOptions] = useState<any[]>([])
  const [product, setProduct] = useState<any>(null)
  const [notes, setNotes]     = useState('')
  const [result, setResult]   = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [inventoryMode, setInventoryMode] = useState<'legacy' | 'authoritative'>('legacy')
  const [inventoryLocations, setInventoryLocations] = useState<any[]>([])
  const [inventoryBalances, setInventoryBalances] = useState<any[]>([])
  const requestIntent = useRef<{ signature: string; key: string } | null>(null)

  // Multi-unit inputs: 端, 盒
  const [qDan,   setQDan]   = useState<number>(0)
  const [qHe,    setQHe]    = useState<number>(0)
  // Adjust: new absolute qty (in 盒/units)
  const [newQty, setNewQty] = useState<number>(0)
  const [location, setLoc]  = useState<'upstairs' | 'instore'>('upstairs')

  const isBlindBox = product?.product_type === '盲盒'
  const bpd: number = (isBlindBox && product?.boxes_per_dan) ? product.boxes_per_dan : 1

  // Total qty in base unit (盒 for blind box, units for non-blind box)
  const totalQty = useMemo(() => {
    if (inventoryMode === 'authoritative') return op === 'adjust' ? newQty : qDan
    if (op === 'adjust') {
      if (isBlindBox) return qDan * bpd + qHe
      return newQty
    }
    if (!isBlindBox) return qDan  // non-blind box: just a plain integer
    return qDan * bpd + qHe
  }, [op, isBlindBox, qDan, qHe, bpd, newQty, inventoryMode])

  // Pre-seed product when opened from a row's quick-adjust button
  useEffect(() => {
    if (open && initialProduct) {
      selectProduct(initialProduct)
      setSearch(`${initialProduct.jizhanming} (${initialProduct.sku})`)
    } else if (!open) {
      reset()
    }
  }, [open, initialProduct])

  function reset() {
    setStep(0); setProduct(null); setSearch(''); setNotes(''); setResult(null)
    setDate(dayjs()); setQDan(0); setQHe(0); setNewQty(0)
    setInventoryMode('legacy'); setInventoryLocations([]); setInventoryBalances([])
    requestIntent.current = null
  }

  async function selectProduct(selected: any) {
    setProduct(selected)
    const storeCode = selectedStore?.code
    if (!storeCode || storeCode === 'ALL') return
    try {
      const [detail, locations, balances] = await Promise.all([
        client.get(`/products/${selected.id}`),
        client.get('/inventory/locations', { params: { store_code: storeCode } }),
        client.get('/inventory/balances', { params: { store_code: storeCode } }),
      ])
      setProduct(detail.data)
      setInventoryLocations(locations.data)
      setInventoryBalances(balances.data.items)
      setInventoryMode(balances.data.mode)
    } catch (err: any) {
      if (err?.response?.data?.code === 'inventory_forbidden') {
        message.error('You do not have inventory access for this store')
      }
    }
  }

  async function searchProducts(v: string) {
    setSearch(v)
    if (!v) { setOptions([]); return }
    const r = await client.get('/products/search', { params: { q: v, limit: 8 } })
    setOptions(r.data.map((p: any) => ({
      value: p.jizhanming || p.name_cn_en,
      label: `${p.jizhanming || p.name_cn_en} (${p.sku})`,
      product: p,
    })))
  }

  async function handleSubmit() {
    if (!product) { message.warning('请选择产品'); return }
    if (op !== 'adjust' && totalQty <= 0) { message.warning('数量必须大于0'); return }
    setLoading(true)
    const dateStr = date.format('YYYY-MM-DD')
    const sc = selectedStore?.code
    try {
      let resp
      if (inventoryMode === 'authoritative') {
        if (product.identity_status !== 'verified' || !product.stock_unit) {
          message.error('Inventory identity must be verified before posting')
          return
        }
        const floor = inventoryLocations.find(l => l.code === 'floor')
        const back = inventoryLocations.find(l => l.code === 'upstairs' || l.code === 'warehouse')
        if (!floor?.opening_verified || !back?.opening_verified) {
          message.error('Reviewed opening balances are required before posting')
          return
        }
        const balance = (locationId: number) => inventoryBalances.find(
          b => b.product_id === product.id && b.location_id === locationId && b.disposition === 'saleable'
        )
        let payload: any
        if (op === 'restock_upstairs') {
          const to = balance(back.id)
          payload = { kind: 'receipt', business_date: dateStr, reason: notes || 'Stock receipt', lines: [{
            product_id: product.id, quantity: totalQty, unit: product.stock_unit,
            to_location_id: back.id, to_disposition: 'saleable',
            expected_versions: { to: to?.version ?? 0 },
          }] }
        } else if (op === 'ru_dian') {
          const from = balance(back.id); const to = balance(floor.id)
          payload = { kind: 'move', business_date: dateStr, reason: notes || 'Move to floor', lines: [{
            product_id: product.id, quantity: totalQty, unit: product.stock_unit,
            from_location_id: back.id, from_disposition: 'saleable',
            to_location_id: floor.id, to_disposition: 'saleable',
            expected_versions: { from: from?.version ?? 0, to: to?.version ?? 0 },
          }] }
        } else {
          const target = location === 'instore' ? floor : back
          const current = balance(target.id)
          const delta = totalQty - (current?.quantity ?? 0)
          if (!delta) { message.warning('New quantity matches the current balance'); return }
          if (!notes.trim()) { message.warning('A correction reason is required'); return }
          payload = { kind: 'correction', business_date: dateStr, reason: notes, lines: [{
            product_id: product.id, quantity: Math.abs(delta), unit: product.stock_unit,
            ...(delta < 0
              ? { from_location_id: target.id, from_disposition: 'saleable', expected_versions: { from: current?.version ?? 0 } }
              : { to_location_id: target.id, to_disposition: 'saleable', expected_versions: { to: current?.version ?? 0 } }),
          }] }
        }
        const signature = JSON.stringify(payload)
        if (!requestIntent.current || requestIntent.current.signature !== signature) {
          requestIntent.current = { signature, key: crypto.randomUUID() }
        }
        resp = await client.post('/inventory/commands', payload, {
          headers: { 'Idempotency-Key': requestIntent.current.key },
        })
      } else if (op === 'adjust') {
        resp = await client.post('/stock/adjust', {
          product_id: product.id, location, new_qty: totalQty, date: dateStr, notes, store_code: sc,
        })
      } else if (op === 'ru_dian') {
        resp = await client.post('/stock/ru_dian', {
          product_id: product.id, qty: totalQty, date: dateStr, notes, store_code: sc,
        })
      } else {
        resp = await client.post('/stock/restock_upstairs', {
          product_id: product.id, qty: totalQty, date: dateStr, notes, store_code: sc,
        })
      }
      setResult(resp.data)
      setStep(1)
    } catch (err: any) {
      const code = err?.response?.data?.code
      const labels: Record<string, string> = {
        insufficient_stock: 'Not enough stock. Your draft was kept.',
        stale_version: 'Stock changed. Review the latest balance before submitting again.',
        idempotency_conflict: 'This request key belongs to a different draft.',
        reconciliation_required: 'Inventory reconciliation is required before this action.',
        inventory_forbidden: 'You do not have inventory access for this store.',
        inventory_busy: 'Inventory is busy. Retry this same draft in a moment.',
      }
      message.error(labels[code] ?? err?.response?.data?.error ?? '操作失败')
    } finally {
      setLoading(false)
    }
  }

  const OP_LABELS: Record<Op, string> = {
    restock_upstairs: '入库（楼上）',
    ru_dian:          '入店（楼上 → 店内）',
    adjust:           '手动调整',
  }

  // Render the qty input section based on product type and operation
  function renderQtyInput() {
    if (inventoryMode === 'authoritative' && op === 'adjust') {
      return <>
        <Select value={location} onChange={setLoc} options={[
          { value: 'upstairs', label: 'Back stock' },
          { value: 'instore', label: 'Floor' },
        ]} style={{ width: '100%' }} />
        <InputNumber value={newQty} onChange={v => setNewQty(v ?? 0)}
          addonBefore={`New quantity (${product?.stock_unit ?? 'unit'})`} style={{ width: '100%' }} min={0} />
      </>
    }
    if (inventoryMode === 'authoritative' && op !== 'adjust') {
      return <InputNumber value={qDan} onChange={v => setQDan(v ?? 0)}
        addonBefore={`Quantity (${product?.stock_unit ?? 'unit'})`} style={{ width: '100%' }} min={1} />
    }
    if (op === 'adjust') {
      return (
        <>
          <Select
            value={location}
            onChange={setLoc}
            options={[
              { value: 'upstairs', label: '楼上' },
              { value: 'instore',  label: '店内' },
            ]}
            style={{ width: '100%' }}
          />
          {isBlindBox ? (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <InputNumber
                  value={qDan}
                  onChange={v => setQDan(v ?? 0)}
                  addonBefore="端数"
                  style={{ width: '100%' }}
                  min={0}
                />
                <InputNumber
                  value={qHe}
                  onChange={v => setQHe(v ?? 0)}
                  addonBefore="散盒"
                  style={{ width: '100%' }}
                  min={0}
                  max={bpd - 1}
                />
              </div>
              <div style={{ fontSize: 12, color: '#6366F1', background: '#f0f0ff', borderRadius: 6, padding: '4px 10px' }}>
                {qDan}端 × {bpd}盒/端 + {qHe}盒 = <strong>{totalQty}盒</strong>
              </div>
            </>
          ) : (
            <InputNumber
              value={newQty}
              onChange={v => setNewQty(v ?? 0)}
              addonBefore="新数量(件)"
              style={{ width: '100%' }}
              min={0}
            />
          )}
        </>
      )
    }

    if (!isBlindBox) {
      // Non-blind box: plain integer input
      return (
        <InputNumber
          value={qDan}
          onChange={v => setQDan(v ?? 0)}
          addonBefore="数量(件)"
          style={{ width: '100%' }}
          min={1}
        />
      )
    }

    // Blind box: multi-unit input
    return (
      <div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
          <InputNumber
            value={qDan}
            onChange={v => setQDan(v ?? 0)}
            addonBefore="端"
            style={{ width: '100%' }}
            min={0}
          />
          <InputNumber
            value={qHe}
            onChange={v => setQHe(v ?? 0)}
            addonBefore="盒"
            style={{ width: '100%' }}
            min={0}
          />
        </div>
        {totalQty > 0 && (
          <div style={{ fontSize: 12, color: '#6366F1', background: '#f0f0ff', borderRadius: 6, padding: '4px 10px' }}>
            合计: <strong>{totalQty} 盒</strong>
            {bpd > 1 && <span style={{ color: '#9ca3af' }}> ({Math.floor(totalQty / bpd)}端 {totalQty % bpd}盒)</span>}
          </div>
        )}
      </div>
    )
  }

  return (
    <Modal
      title="库存操作"
      open={open}
      onCancel={() => { reset(); onClose() }}
      footer={null}
      width={480}
      destroyOnClose
    >
      <Steps
        current={step}
        size="small"
        style={{ marginBottom: 20 }}
        items={[{ title: '填写' }, { title: '完成' }]}
      />

      {step === 0 && (
        <Space direction="vertical" style={{ width: '100%' }} size={10}>
          <Select
            value={op}
            onChange={v => setOp(v)}
            options={Object.entries(OP_LABELS).map(([v, l]) => ({ value: v, label: l }))}
            style={{ width: '100%' }}
          />
          <DatePicker
            value={date}
            onChange={d => setDate(d ?? dayjs())}
            style={{ width: '100%' }}
            allowClear={false}
          />
          <AutoComplete
            placeholder="搜索产品（记账名 / SKU）"
            value={searchVal}
            options={options}
            onSearch={searchProducts}
            onSelect={(_, opt) => { selectProduct(opt.product); setSearch(opt.label as string) }}
            style={{ width: '100%' }}
          />
          {product && (
            <div style={{ fontSize: 11, color: '#9ca3af', background: '#f9fafb', borderRadius: 6, padding: '4px 10px' }}>
              {product.product_type === '盲盒'
                ? `盲盒 · ${product.boxes_per_dan ?? '?'}盒/端`
                : `非盲盒 · ${product.product_type || '—'}`}
            </div>
          )}
          {renderQtyInput()}
          {product && totalQty > 0 && (
            <Alert type="info" showIcon message="Effect before posting"
              description={`${OP_LABELS[op]} · ${product.jizhanming || product.sku} · ${totalQty} ${inventoryMode === 'authoritative' ? product.stock_unit : (isBlindBox ? 'box' : 'piece')} · ${selectedStore?.name ?? 'No store selected'}`} />
          )}
          <Input
            placeholder="备注（可选）"
            value={notes}
            onChange={e => setNotes(e.target.value)}
          />
          <Button type="primary" loading={loading} onClick={handleSubmit} block>
            提交
          </Button>
        </Space>
      )}

      {step === 1 && result && (
        <div>
          <Alert
            type="success"
            message="操作成功"
            description={result.document_id
              ? `Inventory document ${result.document_id} posted. ${result.balances.map((b: any) => `${b.quantity} (v${b.version})`).join(' · ')}`
              : `楼上: ${result.upstairs_qty ?? '-'} | 店内: ${result.instore_qty ?? '-'}`}
            showIcon
            style={{ marginBottom: 12 }}
          />
          <Button type="primary" onClick={() => { reset(); onDone() }}>完成</Button>
        </div>
      )}
    </Modal>
  )
}
