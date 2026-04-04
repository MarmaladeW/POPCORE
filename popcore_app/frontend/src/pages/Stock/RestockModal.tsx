import { useState, useEffect, useMemo } from 'react'
import {
  Modal, Steps, Select, InputNumber, Input, Button, Space,
  AutoComplete, message, Alert, DatePicker,
} from 'antd'
import dayjs from 'dayjs'
import client from '../../api/client'

type Op = 'restock_upstairs' | 'ru_dian' | 'adjust'

interface InitialProduct {
  id: number
  jizhanming: string
  sku: string
  product_type?: string
  boxes_per_dan?: number | null
  dan_per_xiang?: number | null
}

interface Props {
  open: boolean
  onClose: () => void
  onDone: () => void
  initialProduct?: InitialProduct
}

export default function RestockModal({ open, onClose, onDone, initialProduct }: Props) {
  const [step, setStep]       = useState(0)
  const [op, setOp]           = useState<Op>('restock_upstairs')
  const [date, setDate]       = useState(dayjs())
  const [searchVal, setSearch] = useState('')
  const [options, setOptions] = useState<any[]>([])
  const [product, setProduct] = useState<any>(null)
  const [notes, setNotes]     = useState('')
  const [result, setResult]   = useState<any>(null)
  const [loading, setLoading] = useState(false)

  // Multi-unit inputs: 箱, 端, 盒
  const [qXiang, setQXiang] = useState<number>(0)
  const [qDan,   setQDan]   = useState<number>(0)
  const [qHe,    setQHe]    = useState<number>(0)
  // Adjust: new absolute qty (in 盒/units)
  const [newQty, setNewQty] = useState<number>(0)
  const [location, setLoc]  = useState<'upstairs' | 'instore'>('upstairs')

  const isBlindBox = product?.product_type === '盲盒'
  const bpd: number = (isBlindBox && product?.boxes_per_dan) ? product.boxes_per_dan : 1
  const dpx: number = (isBlindBox && product?.dan_per_xiang) ? product.dan_per_xiang : 0

  // Total qty in base unit (盒 for blind box, units for non-blind box)
  const totalQty = useMemo(() => {
    if (op === 'adjust') return newQty
    if (!isBlindBox) return qDan  // non-blind box: just a plain integer
    return qXiang * dpx * bpd + qDan * bpd + qHe
  }, [op, isBlindBox, qXiang, qDan, qHe, bpd, dpx, newQty])

  // Pre-seed product when opened from a row's quick-adjust button
  useEffect(() => {
    if (open && initialProduct) {
      setProduct(initialProduct)
      setSearch(`${initialProduct.jizhanming} (${initialProduct.sku})`)
    } else if (!open) {
      reset()
    }
  }, [open, initialProduct])

  function reset() {
    setStep(0); setProduct(null); setSearch(''); setNotes(''); setResult(null)
    setDate(dayjs()); setQXiang(0); setQDan(0); setQHe(0); setNewQty(0)
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
    try {
      let resp
      if (op === 'adjust') {
        resp = await client.post('/stock/adjust', {
          product_id: product.id, location, new_qty: newQty, date: dateStr, notes,
        })
      } else if (op === 'ru_dian') {
        resp = await client.post('/stock/ru_dian', {
          product_id: product.id, qty: totalQty, date: dateStr, notes,
        })
      } else {
        resp = await client.post('/stock/restock_upstairs', {
          product_id: product.id, qty: totalQty, date: dateStr, notes,
        })
      }
      setResult(resp.data)
      setStep(1)
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '操作失败')
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
          <InputNumber
            value={newQty}
            onChange={v => setNewQty(v ?? 0)}
            addonBefore={isBlindBox ? '新数量(盒)' : '新数量(件)'}
            style={{ width: '100%' }}
            min={0}
          />
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
        <div style={{ display: 'grid', gridTemplateColumns: dpx > 0 ? '1fr 1fr 1fr' : '1fr 1fr', gap: 8, marginBottom: 8 }}>
          {dpx > 0 && (
            <InputNumber
              value={qXiang}
              onChange={v => setQXiang(v ?? 0)}
              addonBefore="箱"
              style={{ width: '100%' }}
              min={0}
            />
          )}
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
            onSelect={(_, opt) => { setProduct(opt.product); setSearch(opt.label as string) }}
            style={{ width: '100%' }}
          />
          {product && (
            <div style={{ fontSize: 11, color: '#9ca3af', background: '#f9fafb', borderRadius: 6, padding: '4px 10px' }}>
              {product.product_type === '盲盒'
                ? `盲盒 · ${product.boxes_per_dan ?? '?'}盒/端${product.dan_per_xiang ? ` · ${product.dan_per_xiang}端/箱` : ''}`
                : `非盲盒 · ${product.product_type || '—'}`}
            </div>
          )}
          {renderQtyInput()}
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
            description={`楼上: ${result.upstairs_qty ?? '-'} | 店内: ${result.instore_qty ?? '-'}`}
            showIcon
            style={{ marginBottom: 12 }}
          />
          <Button type="primary" onClick={() => { reset(); onDone() }}>完成</Button>
        </div>
      )}
    </Modal>
  )
}
