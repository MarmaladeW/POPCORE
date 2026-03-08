import { useState } from 'react'
import {
  Modal, Steps, Input, Button, Table, Select, Tag, Space, message, Alert,
} from 'antd'
import client from '../../api/client'

interface Props {
  open: boolean
  date: string
  onClose: () => void
  onDone: () => void
}

interface MatchedItem {
  rawName: string
  qty_pos: number
  qty_cash: number
  notes: string
  product_id?: number
  sku?: string
  jizhanming?: string
  candidates?: any[]
  status: 'matched' | 'fuzzy' | 'unmatched'
}

function parseLine(line: string) {
  // Format: 记账名  卡机数  现金数  [备注]
  const parts = line.split(/[\t\s]+/)
  return {
    rawName: parts[0] || '',
    qty_pos:  parseInt(parts[1] || '0', 10) || 0,
    qty_cash: parseInt(parts[2] || '0', 10) || 0,
    notes: parts.slice(3).join(' '),
  }
}

export default function BatchSalesModal({ open, date, onClose, onDone }: Props) {
  const [step, setStep]       = useState(0)
  const [text, setText]       = useState('')
  const [items, setItems]     = useState<MatchedItem[]>([])
  const [matching, setMatch]  = useState(false)
  const [submitting, setSub]  = useState(false)
  const [results, setResults] = useState<{ ok: number; fail: number }>({ ok: 0, fail: 0 })

  function reset() { setStep(0); setText(''); setItems([]) }

  async function match() {
    const parsed = text.split('\n').map(l => l.trim()).filter(Boolean).map(parseLine)
    if (!parsed.length) { message.warning('内容为空'); return }
    setMatch(true)
    const out: MatchedItem[] = []
    for (const p of parsed) {
      const r = await client.get('/products/by_jizhanming', { params: { name: p.rawName } })
      const cands = r.data
      if (cands.length === 1) {
        out.push({ ...p, product_id: cands[0].id, sku: cands[0].sku, jizhanming: cands[0].jizhanming, status: 'matched' })
      } else if (cands.length > 1) {
        out.push({ ...p, candidates: cands, product_id: cands[0].id, sku: cands[0].sku, jizhanming: cands[0].jizhanming, status: 'fuzzy' })
      } else {
        out.push({ ...p, status: 'unmatched' })
      }
    }
    setMatch(false); setItems(out); setStep(1)
  }

  async function submit() {
    const toSub = items.filter(i => i.product_id)
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

  return (
    <Modal title="批量销售导入" open={open} onCancel={() => { reset(); onClose() }}
      footer={null} width={680} destroyOnClose>
      <Steps current={step} size="small" style={{ marginBottom: 16 }}
        items={[{ title: '粘贴' }, { title: '确认' }, { title: '完成' }]} />

      {step === 0 && (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Alert type="info" message="格式：记账名  卡机数  现金/转账数  [备注]" />
          <Input.TextArea rows={8} value={text} onChange={e => setText(e.target.value)}
            placeholder={'Dimoo花花  3  1\nSA草莓  0  2'} style={{ fontFamily: 'monospace' }} />
          <Button type="primary" loading={matching} onClick={match}>匹配产品</Button>
        </Space>
      )}

      {step === 1 && (
        <>
          {items.some(i => i.status === 'unmatched') && (
            <Alert type="warning" message={`${items.filter(i => i.status === 'unmatched').length} 行未匹配`} style={{ marginBottom: 8 }} />
          )}
          <Table size="small" rowKey="rawName" dataSource={items} pagination={false} scroll={{ y: 280 }}
            columns={[
              { title: '名称', dataIndex: 'rawName', width: 110 },
              {
                title: '匹配', key: 'm', width: 180,
                render: (_, r, idx) => {
                  if (r.status === 'unmatched') return <Tag color="red">未匹配</Tag>
                  if (r.status === 'fuzzy' && r.candidates) return (
                    <Select size="small" value={r.product_id} style={{ width: 170 }}
                      onChange={v => {
                        const c = r.candidates!.find(x => x.id === v)
                        setItems(p => p.map((it, i) => i === idx
                          ? { ...it, product_id: v, jizhanming: c?.jizhanming, sku: c?.sku, status: 'matched' }
                          : it))
                      }}
                      options={r.candidates!.map(c => ({ value: c.id, label: `${c.jizhanming} (${c.sku})` }))} />
                  )
                  return <span>{r.jizhanming} <Tag>{r.sku}</Tag></span>
                },
              },
              { title: '卡机', dataIndex: 'qty_pos', width: 60 },
              { title: '现金', dataIndex: 'qty_cash', width: 60 },
              { title: '备注', dataIndex: 'notes' },
            ]} />
          <Space style={{ marginTop: 12 }}>
            <Button onClick={() => setStep(0)}>返回</Button>
            <Button type="primary" loading={submitting} onClick={submit}
              disabled={!items.some(i => i.product_id)}>
              提交 {items.filter(i => i.product_id).length} 条
            </Button>
          </Space>
        </>
      )}

      {step === 2 && (
        <>
          <Alert type="success" showIcon
            message={`成功 ${results.ok} 条，跳过 ${results.fail} 条`} style={{ marginBottom: 8 }} />
          <Button type="primary" onClick={() => { reset(); onDone() }}>完成</Button>
        </>
      )}
    </Modal>
  )
}
