import { useState } from 'react'
import { Modal, Steps, Input, Button, Table, Select, Tag, Space, message, Alert } from 'antd'
import { CheckCircleOutlined, WarningOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import client from '../../api/client'

interface ParsedItem {
  rawName: string
  qty: number
  notes: string
}

interface MatchedItem extends ParsedItem {
  product_id?: number
  sku?: string
  jizhanming?: string
  candidates?: any[]
  status: 'matched' | 'fuzzy' | 'unmatched'
}

interface Props {
  open: boolean
  onClose: () => void
  onDone: () => void
}

function parsePasteText(text: string): ParsedItem[] {
  return text
    .split('\n')
    .map(l => l.trim())
    .filter(Boolean)
    .map(line => {
      // Format: "记账名 数量 [备注]"  or  "记账名\t数量"
      const parts = line.split(/[\t\s]+/)
      const rawName = parts[0] || ''
      const qty     = parseInt(parts[1] || '1', 10) || 1
      const notes   = parts.slice(2).join(' ')
      return { rawName, qty, notes }
    })
}

export default function PasteImportModal({ open, onClose, onDone }: Props) {
  const [step, setStep]         = useState(0)
  const [operation, setOp]      = useState<'ru_dian' | 'restock_upstairs'>('restock_upstairs')
  const [date, setDate]         = useState(dayjs().format('YYYY-MM-DD'))
  const [pasteText, setPasteText] = useState('')
  const [items, setItems]       = useState<MatchedItem[]>([])
  const [matching, setMatching] = useState(false)
  const [submitting, setSub]    = useState(false)
  const [results, setResults]   = useState<any[]>([])

  function reset() {
    setStep(0); setPasteText(''); setItems([]); setResults([])
  }

  async function handleMatch() {
    const parsed = parsePasteText(pasteText)
    if (!parsed.length) { message.warning('请粘贴内容'); return }
    setMatching(true)
    const matched: MatchedItem[] = []
    for (const p of parsed) {
      const resp = await client.get('/products/by_jizhanming', { params: { name: p.rawName } })
      const candidates = resp.data
      if (candidates.length === 1) {
        matched.push({ ...p, product_id: candidates[0].id, sku: candidates[0].sku,
          jizhanming: candidates[0].jizhanming, status: 'matched' })
      } else if (candidates.length > 1) {
        matched.push({ ...p, candidates, product_id: candidates[0].id, sku: candidates[0].sku,
          jizhanming: candidates[0].jizhanming, status: 'fuzzy' })
      } else {
        matched.push({ ...p, status: 'unmatched' })
      }
    }
    setMatching(false)
    setItems(matched)
    setStep(1)
  }

  async function handleSubmit() {
    const toSubmit = items.filter(i => i.product_id && i.status !== 'unmatched')
    if (!toSubmit.length) { message.warning('没有可提交的行'); return }
    setSub(true)
    try {
      const resp = await client.post('/stock/batch_operation', {
        operation,
        date,
        items: toSubmit.map(i => ({ product_id: i.product_id, qty: i.qty, notes: i.notes })),
      })
      setResults(resp.data.results || [])
      setStep(2)
    } catch (err: any) {
      message.error(err?.response?.data?.error ?? '提交失败')
    } finally {
      setSub(false)
    }
  }

  const unmatchedCount = items.filter(i => i.status === 'unmatched').length
  const okCount        = items.filter(i => i.product_id).length

  return (
    <Modal
      title="粘贴导入 — 库存"
      open={open}
      onCancel={() => { reset(); onClose() }}
      footer={null}
      width={720}
      destroyOnClose
    >
      <Steps
        current={step}
        size="small"
        style={{ marginBottom: 20 }}
        items={[{ title: '粘贴' }, { title: '确认' }, { title: '完成' }]}
      />

      {step === 0 && (
        <div>
          <Space style={{ marginBottom: 12 }}>
            <Select
              value={operation}
              onChange={setOp}
              options={[
                { value: 'restock_upstairs', label: '入库（楼上）' },
                { value: 'ru_dian',          label: '入店（楼上→店内）' },
              ]}
              style={{ width: 180 }}
            />
            <Input
              type="date"
              value={date}
              onChange={e => setDate(e.target.value)}
              style={{ width: 150 }}
            />
          </Space>
          <Input.TextArea
            rows={10}
            placeholder={'每行一条：记账名 数量 [备注]\n例如：\nDimoo花花 2\nSA草莓 1 破损'}
            value={pasteText}
            onChange={e => setPasteText(e.target.value)}
            style={{ fontFamily: 'monospace', marginBottom: 12 }}
          />
          <Button type="primary" loading={matching} onClick={handleMatch}>
            下一步 — 匹配产品
          </Button>
        </div>
      )}

      {step === 1 && (
        <div>
          {unmatchedCount > 0 && (
            <Alert
              type="warning"
              style={{ marginBottom: 12 }}
              message={`${unmatchedCount} 行未匹配，将被跳过`}
              icon={<WarningOutlined />}
              showIcon
            />
          )}
          <Table
            size="small"
            rowKey="rawName"
            dataSource={items}
            pagination={false}
            scroll={{ y: 320 }}
            columns={[
              {
                title: '输入名称', dataIndex: 'rawName', width: 120,
              },
              {
                title: '匹配产品', key: 'match', width: 160,
                render: (_, r, idx) => {
                  if (r.status === 'unmatched') return <Tag color="red">未匹配</Tag>
                  if (r.status === 'fuzzy' && r.candidates) {
                    return (
                      <Select
                        size="small"
                        value={r.product_id}
                        style={{ width: 150 }}
                        onChange={v => {
                          const cand = r.candidates!.find(c => c.id === v)
                          setItems(prev => prev.map((it, i) => i === idx
                            ? { ...it, product_id: v, jizhanming: cand?.jizhanming, sku: cand?.sku, status: 'matched' }
                            : it
                          ))
                        }}
                        options={r.candidates!.map(c => ({
                          value: c.id,
                          label: `${c.jizhanming} (${c.sku})`,
                        }))}
                      />
                    )
                  }
                  return <span>{r.jizhanming} <Tag>{r.sku}</Tag></span>
                },
              },
              { title: '数量', dataIndex: 'qty', width: 60 },
              { title: '备注', dataIndex: 'notes' },
            ]}
          />
          <Space style={{ marginTop: 12 }}>
            <Button onClick={() => setStep(0)}>返回</Button>
            <Button type="primary" loading={submitting} onClick={handleSubmit}
              disabled={!okCount}>
              提交 {okCount} 条
            </Button>
          </Space>
        </div>
      )}

      {step === 2 && (
        <div>
          <Alert
            type="success"
            icon={<CheckCircleOutlined />}
            showIcon
            message={`完成：${results.filter(r => r.ok).length} 成功，${results.filter(r => !r.ok).length} 失败`}
            style={{ marginBottom: 12 }}
          />
          {results.filter(r => !r.ok).map((r, i) => (
            <Alert key={i} type="error" message={`产品 ${r.pid}: ${r.error}`} style={{ marginBottom: 4 }} />
          ))}
          <Button type="primary" onClick={() => { reset(); onDone() }}>完成</Button>
        </div>
      )}
    </Modal>
  )
}
