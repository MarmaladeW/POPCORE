import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Form, Input, Modal, Pagination, Select, Skeleton, message } from 'antd'
import { Link } from 'react-router-dom'

import { createEntry, searchEntries, unlinkedItems, updateEntry, type DexUnlinkedItem, type DexUnlinkedPage } from '../../api/dex'
import { useHasRole } from '../../auth/useRole'
import PageHeader from '../../components/pc/PageHeader'
import { Panel } from '../../components/pc/Panel'
import { kindText } from '../../lib/dexFields'
import { errorText } from './DexParts'
import './dex.css'

const PER_PAGE = 50
const FORM_LABEL: Record<string, string> = {
  random_box: '盲盒 Blind box', ordinary: '普通 Item', confirmed_design: '确认款 Design',
}
const formLabel = (form: DexUnlinkedItem['stock_form']) => form ? FORM_LABEL[form] : '未分类 Not sorted yet'
const itemName = (item: DexUnlinkedItem) => item.name_cn_en || item.jizhanming || item.sku

// 不在图鉴里 Not in the book yet: POPCORE items no book entry links (never sealed sets). Managers put a box or an
// item in the book under a series, IP or company; a design goes under the book product its box is linked to.
export default function DexUnlinked() {
  const manager = useHasRole('manager')
  const [text, setText] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<DexUnlinkedPage>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [placing, setPlacing] = useState<DexUnlinkedItem | null>(null)

  useEffect(() => {
    const timer = window.setTimeout(() => { setQuery(text.trim()); setPage(1) }, 250)
    return () => window.clearTimeout(timer)
  }, [text])
  useEffect(() => {
    let live = true
    unlinkedItems(query, page, PER_PAGE).then(next => { if (live) { setData(next); setError('') } })
      .catch(cause => { if (live) setError(errorText(cause, 'Unable to load the list.')) })
    return () => { live = false }
  }, [query, page, attempt])

  const header = <PageHeader title="不在图鉴里" sub="Not in the book yet" back={{ label: '图鉴 Dex', to: '/dex' }} />
  if (error) return <div className="pc-dex">{header}
    <Alert role="alert" type="error" showIcon message={error} action={<Button onClick={() => setAttempt(n => n + 1)}>Retry</Button>} />
  </div>

  const [linking, setLinking] = useState(0)
  async function link(item: DexUnlinkedItem) {
    if (!item.suggestion) return
    setLinking(item.id)
    try {
      await updateEntry(item.suggestion.id, { product_id: item.id })
      placed(item)
    } catch (cause) {
      message.error(errorText(cause))
    } finally {
      setLinking(0)
    }
  }

  function placed(item: DexUnlinkedItem) {
    setPlacing(null)
    setAttempt(n => n + 1)
    message.success(`${item.sku} is in the book`)
  }

  return <div className="pc-dex">
    {header}
    <p className="pc-dex-note">POPCORE items that no book entry is linked to: boxes, items, designs and items not sorted yet.
      Sealed sets are never listed.</p>
    <Input.Search className="pc-dex-search" placeholder="搜索 Search ID, name or 记账名" aria-label="Search items not in the book"
      allowClear value={text} onChange={event => setText(event.target.value)} />
    <Panel zh="不在图鉴里" en="Not in the book" label="Items not in the book" count={data?.total ?? '…'} tone="warn">
      {!data ? <div className="pc-quiet"><Skeleton active /></div>
        : data.items.length === 0 ? <p className="pc-quiet">{query ? 'Nothing found.' : 'Every item is in the book.'}</p>
        : data.items.map(item => <div key={item.id} className="pc-row pc-dex-row pc-dex-item">
          <span className="pc-row-main">
            <strong>{item.sku} · {itemName(item)}</strong>
            <small>{[formLabel(item.stock_form), item.jizhanming && item.jizhanming !== itemName(item) ? `记账名 ${item.jizhanming}` : '',
              item.ip_series ? `系列 ${item.ip_series}` : '',
              item.stock_form === 'confirmed_design' ? (item.box_entry ? `盒子在图鉴 Box in the book: ${item.box_entry.name}` : 'Its box is not in the book') : '',
            ].filter(Boolean).join(' · ')}</small>
            {item.suggestion && <small className="pc-dex-suggest">建议 Suggested: {item.suggestion.path} · {item.suggestion.score}/100</small>}
          </span>
          {manager && <span className="pc-dex-actions">
            {item.suggestion && <Button size="small" type="primary" loading={linking === item.id}
              aria-label={`Link ${item.sku} to ${item.suggestion.path}`} onClick={() => link(item)}>连接 Link</Button>}
            <Button size="small" aria-label={`Put ${item.sku} in the book`} onClick={() => setPlacing(item)}>放入图鉴 Put in the book</Button>
          </span>}
        </div>)}
      {data && data.total > PER_PAGE && <div className="pc-dex-pages">
        <Pagination size="small" current={page} pageSize={PER_PAGE} total={data.total} showSizeChanger={false}
          onChange={setPage} />
      </div>}
    </Panel>
    <PutInBook key={placing?.id ?? 0} item={placing} onClose={() => setPlacing(null)} onDone={placed} />
  </div>
}

type Option = { value: number; label: string }

// Mounted afresh for each item (keyed by the caller), so the form starts from that item.
function PutInBook({ item, onClose, onDone }: {
  item: DexUnlinkedItem | null; onClose: () => void; onDone: (item: DexUnlinkedItem) => void
}) {
  const [form] = Form.useForm()
  const [parents, setParents] = useState<Option[]>([])
  const [saving, setSaving] = useState(false)
  const asked = useRef(0)
  const design = item?.stock_form === 'confirmed_design'

  function findParents(text: string) {
    if (!text.trim()) return
    const ask = ++asked.current
    searchEntries(text, ['series', 'ip', 'company']).then(hits => {
      if (ask !== asked.current) return
      setParents(hits.map(hit => ({
        value: hit.id,
        label: `${hit.name} · ${kindText(hit.kind)}${hit.path.length ? ` · ${hit.path.map(step => step.name).join(' › ')}` : ''}`,
      })))
    }).catch(() => {})
  }

  async function save() {
    if (!item) return
    let values
    try { values = await form.validateFields() } catch { return }
    setSaving(true)
    try {
      if (design) {
        await createEntry({ kind: 'design', name: values.name, parent_id: item.box_entry!.id, product_id: item.id })
      } else {
        await createEntry({ kind: 'product', name: values.name, parent_id: values.parent_id,
          blind_box: item.stock_form === 'random_box', product_id: item.id })
      }
      onDone(item)
    } catch (cause) {
      message.error(errorText(cause))
    } finally {
      setSaving(false)
    }
  }

  const blocked = design && !item?.box_entry
  return <Modal open={!!item} title={item ? `放入图鉴 Put ${item.sku} in the book` : ''} onCancel={onClose}
    okText="放入 Put in" cancelText="取消 Cancel" onOk={save} confirmLoading={saving} okButtonProps={{ disabled: blocked }}>
    {item && blocked && <Alert type="info" showIcon message="先放入它的盒子 Put its box in first"
      description={item.box ? <>This design goes under the book product of its box, {item.box.sku}. Put {item.box.sku} in
        the book first.</> : 'This design has no box in POPCORE.'} />}
    {item && !blocked && <Form form={form} layout="vertical" requiredMark={false}
      initialValues={{ name: design ? (item.design_name || itemName(item)) : itemName(item) }}>
      <p className="pc-dex-note">{design
        ? <>A book design under <Link to={`/dex/${item.box_entry!.id}`}>{item.box_entry!.name}</Link>, linked to {item.sku}.</>
        : <>A book product{item.stock_form === 'random_box' ? ' (blind box)' : ''}, linked to {item.sku}.</>}</p>
      <Form.Item name="name" label="名称 Name" rules={[{ required: true, whitespace: true, message: 'A name is required.' }]}>
        <Input />
      </Form.Item>
      {!design && <Form.Item name="parent_id" label="放在哪里 Goes under (series, IP or company)"
        rules={[{ required: true, message: 'Pick where it goes.' }]}>
        <Select showSearch filterOption={false} onSearch={findParents} options={parents} aria-label="Goes under"
          placeholder="搜索图鉴 Search the book" notFoundContent="输入名称搜索 Type a name" />
      </Form.Item>}
    </Form>}
  </Modal>
}
