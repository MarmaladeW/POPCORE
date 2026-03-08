import { useState, useEffect } from 'react'
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
  const [qty, setQty]         = useState<number>(1)
  const [newDan, setNewDan]   = useState<number>(0)
  const [location, setLoc]    = useState<'upstairs' | 'instore'>('upstairs')
  const [notes, setNotes]     = useState('')
  const [result, setResult]   = useState<any>(null)
  const [loading, setLoading] = useState(false)

  // Pre-seed product when opened from a row's quick-adjust button
  useEffect(() => {
    if (open && initialProduct) {
      setProduct(initialProduct)
      setSearch(`${initialProduct.jizhanming} (${initialProduct.sku})`)
    } else if (!open) {
      // reset on close
      setStep(0); setProduct(null); setSearch(''); setQty(1); setNotes(''); setResult(null)
      setDate(dayjs())
    }
  }, [open, initialProduct])

  function reset() {
    setStep(0); setProduct(null); setSearch(''); setQty(1); setNotes(''); setResult(null)
    setDate(dayjs())
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
    setLoading(true)
    const dateStr = date.format('YYYY-MM-DD')
    try {
      let resp
      if (op === 'adjust') {
        resp = await client.post('/stock/adjust', {
          product_id: product.id, location, new_dan: newDan, date: dateStr, notes,
        })
      } else if (op === 'ru_dian') {
        resp = await client.post('/stock/ru_dian', {
          product_id: product.id, dan_qty: qty, date: dateStr, notes,
        })
      } else {
        resp = await client.post('/stock/restock_upstairs', {
          product_id: product.id, dan_qty: qty, date: dateStr, notes,
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
          {op === 'adjust' ? (
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
                value={newDan}
                onChange={v => setNewDan(v ?? 0)}
                addonBefore="新数量(端)"
                style={{ width: '100%' }}
                min={0}
              />
            </>
          ) : (
            <InputNumber
              value={qty}
              onChange={v => setQty(v ?? 1)}
              addonBefore="数量(端)"
              style={{ width: '100%' }}
              min={1}
            />
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
            description={`楼上: ${result.upstairs_dan ?? '-'} 端 | 店内: ${result.instore_dan ?? '-'} 端`}
            showIcon
            style={{ marginBottom: 12 }}
          />
          <Button type="primary" onClick={() => { reset(); onDone() }}>完成</Button>
        </div>
      )}
    </Modal>
  )
}
