import { useState } from 'react'
import {
  Modal, Steps, Select, Input, Button, Table, Space, Tag, Alert, message,
} from 'antd'
import dayjs from 'dayjs'
import client from '../../api/client'

interface Props {
  open: boolean
  onClose: () => void
  onDone: () => void
}

interface MatchedItem {
  rawName: string
  qty: number
  notes: string
  product_id?: number
  sku?: string
  jizhanming?: string
  candidates?: any[]
  status: 'matched' | 'fuzzy' | 'unmatched'
}

function parsePaste(text: string) {
  return text.split('\n').map(l => l.trim()).filter(Boolean).map(line => {
    const parts = line.split(/[\t\s]+/)
    return { rawName: parts[0] || '', qty: parseInt(parts[1] || '1', 10) || 1, notes: parts.slice(2).join(' ') }
  })
}

export default function BatchStockModal({ open, onClose, onDone }: Props) {
  const [step, setStep]       = useState(0)
  const [op, setOp]           = useState<'ru_dian' | 'restock_upstairs'>('restock_upstairs')
  const [date, setDate]       = useState(dayjs().format('YYYY-MM-DD'))
  const [text, setText]       = useState('')
  const [items, setItems]     = useState<MatchedItem[]>([])
  const [matching, setMatch]  = useState(false)
  const [submitting, setSub]  = useState(false)
  const [results, setResults] = useState<any[]>([])

  function reset() { setStep(0); setText(''); setItems([]); setResults([]) }

  async function match() {
    const parsed = parsePaste(text)
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
    setMatch(false)
    setItems(out)
    setStep(1)
  }

  async function submit() {
    const toSub = items.filter(i => i.product_id)
    setSub(true)
    try {
      const r = await client.post('/stock/batch_operation', {
        operation: op, date,
        items: toSub.map(i => ({ product_id: i.product_id, qty: i.qty, notes: i.notes })),
      })
      setResults(r.data.results || [])
      setStep(2)
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '失败')
    } finally { setSub(false) }
  }

  return (
    <Modal
      title="批量库存导入"
      open={open}
      onCancel={() => { reset(); onClose() }}
      footer={null}
      width={680}
      destroyOnClose
    >
      <Steps current={step} size="small" style={{ marginBottom: 16 }}
        items={[{ title: '粘贴' }, { title: '确认' }, { title: '完成' }]} />

      {step === 0 && (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space>
            <Select value={op} onChange={setOp} style={{ width: 200 }}
              options={[
                { value: 'restock_upstairs', label: '入库（楼上）' },
                { value: 'ru_dian', label: '入店' },
              ]} />
            <Input type="date" value={date} onChange={e => setDate(e.target.value)} style={{ width: 150 }} />
          </Space>
          <Input.TextArea rows={8} value={text} onChange={e => setText(e.target.value)}
            placeholder="记账名 数量 [备注]" style={{ fontFamily: 'monospace' }} />
          <Button type="primary" loading={matching} onClick={match}>匹配产品</Button>
        </Space>
      )}

      {step === 1 && (
        <>
          {items.some(i => i.status === 'unmatched') && (
            <Alert type="warning" message={`${items.filter(i => i.status === 'unmatched').length} 行未匹配将被跳过`} style={{ marginBottom: 8 }} />
          )}
          <Table size="small" rowKey="rawName" dataSource={items} pagination={false} scroll={{ y: 300 }}
            columns={[
              { title: '名称', dataIndex: 'rawName', width: 120 },
              {
                title: '匹配', key: 'm', width: 170,
                render: (_, r, idx) => {
                  if (r.status === 'unmatched') return <Tag color="red">未匹配</Tag>
                  if (r.status === 'fuzzy' && r.candidates) return (
                    <Select size="small" value={r.product_id} style={{ width: 160 }}
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
              { title: '数量', dataIndex: 'qty', width: 60 },
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
            message={`成功 ${results.filter(r => r.ok).length} / 失败 ${results.filter(r => !r.ok).length}`}
            style={{ marginBottom: 8 }} />
          {results.filter(r => !r.ok).map((r, i) => (
            <Alert key={i} type="error" message={`${r.pid}: ${r.error}`} style={{ marginBottom: 4 }} />
          ))}
          <Button type="primary" onClick={() => { reset(); onDone() }}>完成</Button>
        </>
      )}
    </Modal>
  )
}
