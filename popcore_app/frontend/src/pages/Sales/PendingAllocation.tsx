import { Button, Card, Checkbox, Input, Select, Space, Typography } from 'antd'
import { useEffect, useState } from 'react'

import { searchGoodsProducts, type GoodsProduct } from '../../api/goods'
import { fetchBoxOptions, type AllocationMapping, type SaleResult } from '../../api/salesDocuments'
import { productLabel } from '../../lib/productLabel'
import { BoxQuestionButtons } from './BoxChoices'
import { boxAnswerFields, boxAnswerReady, needsBoxAnswer, type BoxAnswer, type BoxOptions } from './boxChoice'

const { Text } = Typography

/** A paid sale waiting for stock: a manager gives each line its product, says where blind boxes over half a set
 *  came from (owner, 2026-10-27), or records a line with no product, such as a Clover payment without items, as a
 *  charge that moves no stock. Every line keeps its product unless the manager picks another. */
export default function PendingAllocation({ sale, busy, working, allocate }: {
  sale: SaleResult; busy: boolean; working: boolean; allocate: (reason: string, mappings: AllocationMapping[]) => void
}) {
  const [products, setProducts] = useState<GoodsProduct[]>([]), [picked, setPicked] = useState<Record<number, number>>({})
  const [nonStock, setNonStock] = useState<Record<number, boolean>>({}), [answers, setAnswers] = useState<Record<number, BoxAnswer>>({})
  const [options, setOptions] = useState<Record<number, BoxOptions | null>>({}), [reason, setReason] = useState('')
  const lines = sale.lines ?? []
  const productFor = (line: typeof lines[number]) => picked[line.line_no] ?? line.product_id ?? undefined
  const wanted = [...new Set(lines.map(productFor).filter((id): id is number => !!id))].filter(id => !(id in options))
  useEffect(() => {
    if (!sale.store_id || !wanted.length) return
    const controller = new AbortController()
    for (const id of wanted) {
      fetchBoxOptions(sale.store_id, id, controller.signal).then(value => setOptions(old => ({ ...old, [id]: value }))).catch(() => undefined)
    }
    return () => controller.abort()
  }, [sale.store_id, wanted.join(',')]) // eslint-disable-line react-hooks/exhaustive-deps
  async function searchProducts(value: string) { if (value.trim()) setProducts((await searchGoodsProducts(value)).filter(product => product.identity_status === 'verified')) }

  const rows = lines.map(line => {
    const product = productFor(line), boxOptions = product ? options[product] : undefined
    const asks = needsBoxAnswer(boxOptions, line.quantity), answer = answers[line.line_no]
    const charge = !line.product_id && !!nonStock[line.line_no]
    const ready = charge || (!!product && boxOptions !== undefined && (!asks || boxAnswerReady(answer, boxOptions!)))
    const mapping: AllocationMapping = charge ? { line_no: line.line_no, non_stock: true }
      : { line_no: line.line_no, product_id: product, ...(asks && answer && boxOptions ? boxAnswerFields(answer, boxOptions) : {}) }
    return { line, product, boxOptions, asks, answer, charge, ready, mapping }
  })
  return <Card title="Manager stock allocation" style={{ marginTop: 16 }}>
    {rows.map(({ line, product, boxOptions, asks, answer, charge }) => <div key={line.line_no} style={{ marginBottom: 16 }}>
      <Space direction="vertical" style={{ width: '100%' }}>
        <Text strong>Line {line.line_no}: {line.quantity} × {line.product_name_snapshot ?? line.raw_product_text}</Text>
        {!line.product_id && <Checkbox checked={charge} disabled={busy} onChange={event => setNonStock({ ...nonStock, [line.line_no]: event.target.checked })}>
          Charge without stock <Text type="secondary">· no product, no stock moves</Text></Checkbox>}
        {!charge && <Select aria-label={`Line ${line.line_no} verified product`} showSearch filterOption={false} onSearch={searchProducts}
          disabled={busy} placeholder="Search verified products" value={product} style={{ minWidth: 260, maxWidth: '100%' }}
          onChange={value => setPicked({ ...picked, [line.line_no]: value })}
          options={[...(line.product_id && !products.some(item => item.id === line.product_id) ? [{ value: line.product_id, label: line.product_name_snapshot ?? `Product #${line.product_id}` }] : []),
            ...products.map(item => ({ value: item.id, label: productLabel(item) }))]} />}
        {!charge && asks && boxOptions && <BoxQuestionButtons name={line.product_name_snapshot ?? 'Blind box'} quantity={line.quantity}
          setSize={boxOptions.set_size} fromDisplay={false} options={boxOptions} choice={answer?.choice ?? null} openSetId={answer?.openSetId ?? null}
          disabled={busy} answer={(choice, openSetId) => setAnswers({ ...answers, [line.line_no]: { choice, openSetId } })} />}
      </Space>
    </div>)}
    <Input aria-label="Allocation reason" disabled={busy} value={reason} onChange={event => setReason(event.target.value)} placeholder="Separate allocation reason" />
    <Button type="primary" loading={working} disabled={busy || !reason.trim() || rows.some(row => !row.ready)}
      onClick={() => allocate(reason, rows.map(row => row.mapping))} style={{ marginTop: 12 }}>Allocate stock</Button>
  </Card>
}
