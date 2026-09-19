import { useState } from 'react'
import { Modal, Input, Button, Table, Tag, Space, Alert, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'

interface ImportRow {
  sku: string
  action: 'create' | 'update'
  product_id: number | null
  before: Record<string, string | number | null> | null
  values: Record<string, string | number | null>
}

interface ImportPreview {
  rows: ImportRow[]
  created: number
  updated: number
}

interface Props {
  open: boolean
  onClose: () => void
  onDone: () => void
}

const FIELD_LABELS: Record<string, string> = {
  sku: 'SKU', jizhanming: '记账名', name_cn_en: '产品名称', ip_series: '系列',
  product_type: '类型', brand: '品牌', price: '单价', release_date: '发售时间',
  edition_size: '版本/限量', channel: '渠道', notes: '备注', boxes_per_dan: '每端盒数',
}
const HEADERS = 'SKU,记账名,产品名称,系列,类型,品牌,单价,发售时间,版本/限量,渠道,备注'

export default function PasteImportModal({ open, onClose, onDone }: Props) {
  const [text, setText] = useState('')
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  function reset() { setText(''); setPreview(null); setError('') }

  async function handlePreview() {
    if (pending || !text.trim()) return
    setPending(true)
    setError('')
    setPreview(null)
    try {
      const response = await client.post<ImportPreview>('/products/import/preview', { text })
      setPreview(response.data)
    } catch (err: any) {
      setError(err._serverMessage || 'Unable to preview the catalog import. Check the headers and retry.')
    } finally {
      setPending(false)
    }
  }

  async function handleConfirm() {
    if (pending || !preview?.rows.length) return
    setPending(true)
    setError('')
    try {
      const response = await client.post<{ ok: boolean; created: number; updated: number }>(
        '/products/import/confirm', { rows: preview.rows },
      )
      if (!response.data.ok) throw new Error('Import was not confirmed')
      message.success(`Catalog imported: ${response.data.created} created, ${response.data.updated} updated`)
      reset()
      onDone()
    } catch (err: any) {
      setError((err._serverMessage || 'Catalog import could not be confirmed.') + ' Preview again before applying. Your pasted text is preserved.')
      setPreview(null)
    } finally {
      setPending(false)
    }
  }

  function valueCell(row: ImportRow, key: string) {
    const previous = row.before?.[key]
    const value = key in row.values ? row.values[key] : previous
    return <span style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
      {row.action === 'update' && previous !== value && <><Typography.Text delete type="secondary">{previous ?? '—'}</Typography.Text>{' → '}</>}
      {value == null || value === '' ? '—' : String(value)}
    </span>
  }

  const columns: ColumnsType<ImportRow> = [
    { title: 'SKU', dataIndex: 'sku', width: 130 },
    { title: 'Action', dataIndex: 'action', width: 90,
      render: action => <Tag color={action === 'create' ? 'green' : 'blue'}>{action === 'create' ? 'Create' : 'Update'}</Tag> },
    { title: '记账名', width: 170, render: (_, row) => valueCell(row, 'jizhanming') },
    { title: '产品名称 / Name', width: 220, render: (_, row) => valueCell(row, 'name_cn_en') },
    { title: 'Other catalog changes', width: 260, render: (_, row) => {
      const fields = Object.keys(row.values).filter(key => !['sku', 'jizhanming', 'name_cn_en'].includes(key)
        && (row.action === 'create' ? row.values[key] != null && row.values[key] !== '' : row.before?.[key] !== row.values[key]))
      return fields.length ? fields.map(key => <div key={key}><strong>{FIELD_LABELS[key] || key}: </strong>{valueCell(row, key)}</div>) : '—'
    } },
  ]

  return <Modal title="Import catalog / 导入产品目录" open={open} footer={null}
    width={900} closable={!pending} maskClosable={!pending} keyboard={!pending}
    onCancel={() => { if (!pending) { reset(); onClose() } }}>
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert type="info" showIcon message="Paste catalog CSV or tab-separated spreadsheet cells, including the header row."
        description={<>
          <div>Use the Products export format. Required header: SKU. Other columns are optional; omitted columns keep their current values. Explicitly provided blank cells clear that field. Existing SKUs update catalog metadata; new SKUs create products. Inventory quantities are unchanged.</div>
          <div style={{ overflowWrap: 'anywhere', marginTop: 8 }}>{HEADERS}</div>
        </>} />
      {error && <Alert role="alert" type="error" showIcon message={error} />}
      {!preview ? <>
        <Input.TextArea aria-label="Catalog CSV or TSV" rows={9} value={text} disabled={pending}
          placeholder={HEADERS + '\nNEW-001,示例,Example Product,Example,Figure,POPCORE,20,,,, '}
          onChange={event => setText(event.target.value)} style={{ fontFamily: 'monospace', fontSize: 16 }} />
        <Button type="primary" aria-label="Preview catalog import" loading={pending} disabled={pending || !text.trim()}
          onClick={handlePreview} style={{ minHeight: 44 }}>Preview catalog import</Button>
      </> : <>
        <Typography.Text>Review before applying: {preview.created} create · {preview.updated} update</Typography.Text>
        <Table<ImportRow> rowKey="sku" size="small" dataSource={preview.rows} columns={columns}
          pagination={{ pageSize: 20 }} scroll={{ x: 870 }} />
        <Space wrap>
          <Button disabled={pending} onClick={() => { setPreview(null); setError('') }} style={{ minHeight: 44 }}>Edit pasted data</Button>
          <Button type="primary" aria-label="Apply catalog import" loading={pending} disabled={pending || !preview.rows.length}
            onClick={handleConfirm} style={{ minHeight: 44 }}>Apply catalog import</Button>
        </Space>
      </>}
    </Space>
  </Modal>
}
