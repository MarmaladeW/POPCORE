import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Drawer, Form, Input, InputNumber, Select, Switch, message } from 'antd'

import client from '../../api/client'
import {
  childEntries, createEntry, searchEntries, topEntries, updateEntry,
  type DexChange, type DexDetail, type DexKind, type DexStep,
} from '../../api/dex'
import { EXTRA_FIELDS, RARITIES, extrasChange, kindText, toInput, type ExtraField } from '../../lib/dexFields'
import ProductModal from '../Products/ProductModal'
import { errorText } from './DexParts'

const PARTNER_KINDS: DexKind[] = ['series', 'product']
const CHARACTER_KINDS: DexKind[] = ['series', 'product', 'design']
type Option = { value: number; label: string }
interface ItemHit { id: number; sku: string; name_cn_en: string | null; jizhanming: string | null; stock_form: string | null }

// What the editor works on: a new entry of one kind under a parent (the page it was opened from), or an entry.
export type DexEditing =
  | { mode: 'create'; kind: DexKind; parent: DexDetail | null }
  | { mode: 'edit'; entry: DexDetail }

const itemLabel = (item: Pick<ItemHit, 'sku' | 'name_cn_en' | 'jizhanming'>) =>
  `${item.sku} · ${item.name_cn_en || item.jizhanming || ''}`

// A book design links a confirmed design (确认款); a book product links any other item except a sealed set.
const linkable = (kind: DexKind, form: string | null) =>
  kind === 'design' ? form === 'confirmed_design' : form !== 'confirmed_design' && form !== 'sealed_set'

function ExtraInputFor({ field }: { field: ExtraField }) {
  switch (field.input) {
    case 'long': return <Input.TextArea autoSize={{ minRows: 2, maxRows: 6 }} />
    case 'number': return <InputNumber style={{ width: '100%' }} />
    case 'list': return <Input placeholder="用逗号分开 Separate with commas" />
    case 'yesno': return <Select allowClear options={[{ value: true, label: '是 Yes' }, { value: false, label: '否 No' }]} />
    default: return <Input />
  }
}

export default function DexEditor({ editing, onClose, onSaved }: {
  editing: DexEditing | null; onClose: () => void; onSaved: (entry: DexDetail, created: boolean) => void
}) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [partners, setPartners] = useState<Option[]>([])
  const [characters, setCharacters] = useState<Option[]>([])
  const [parents, setParents] = useState<Option[]>([])
  const [items, setItems] = useState<Option[]>([])
  // The Add Product form's starting values while it is open (kept stable: the form resets when they change).
  const [newItem, setNewItem] = useState<{ name_cn_en: string; jizhanming: string } | null>(null)
  const askedParents = useRef(0)
  const askedItems = useRef(0)

  const entry = editing?.mode === 'edit' ? editing.entry : null
  const kind = editing ? (editing.mode === 'edit' ? editing.entry.kind : editing.kind) : null
  const context = editing ? (editing.mode === 'edit' ? editing.entry : editing.parent) : null
  const ip: DexStep | null = context?.ip ?? null
  const extraFields = kind ? EXTRA_FIELDS[kind] : []

  const initialValues = useMemo(() => !kind ? {} : {
      name: entry?.name ?? '',
      parent_id: entry?.parent_id ?? undefined,
      partner_id: entry?.partner_id ?? undefined,
      character_id: entry?.character_id ?? undefined,
      blind_box: entry?.blind_box ?? false,
      rarity: entry?.rarity ?? undefined,
      product_id: entry?.product_id ?? undefined,
      ...Object.fromEntries(EXTRA_FIELDS[kind].map(field => [`x_${field.key}`, toInput(field, entry?.extras[field.key])])),
  }, [editing]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!editing || !kind) return
    setItems(entry?.product ? [{ value: entry.product.id, label: itemLabel({ sku: entry.product.sku, name_cn_en: entry.product.name, jizhanming: null }) }] : [])
    const parentStep = entry?.path[entry.path.length - 1]
    setParents(parentStep ? [{ value: parentStep.id, label: `${parentStep.name} · ${kindText(parentStep.kind)}` }] : [])
    let live = true
    if (PARTNER_KINDS.includes(kind)) {
      topEntries('partner').then(rows => { if (live) setPartners(rows.map(row => ({ value: row.id, label: row.name }))) }).catch(() => {})
    }
    return () => { live = false }
  }, [editing]) // eslint-disable-line react-hooks/exhaustive-deps

  // Characters offered: the IP's, and the chosen partner's (contract 1.2: a character may sit under a partner).
  // A design has no partner of its own; it takes its product's.
  const watchedPartner: number | undefined = Form.useWatch('partner_id', form)
  const partnerId = kind && PARTNER_KINDS.includes(kind) ? watchedPartner : context?.partner?.id
  useEffect(() => {
    if (!editing || !kind || !CHARACTER_KINDS.includes(kind)) { setCharacters([]); return }
    let live = true
    const owners = [ip?.id, partnerId].filter((id): id is number => typeof id === 'number')
    Promise.all(owners.map(id => childEntries(id))).then(groups => {
      if (live) setCharacters(groups.flat().filter(row => row.kind === 'character').map(row => ({ value: row.id, label: row.name })))
    }).catch(() => {})
    return () => { live = false }
  }, [editing, partnerId]) // eslint-disable-line react-hooks/exhaustive-deps

  function findParents(text: string) {
    if (!entry || !text.trim()) return
    const ask = ++askedParents.current
    searchEntries(text, entry.allowed_parents).then(hits => {
      if (ask !== askedParents.current) return
      setParents(hits.filter(hit => hit.id !== entry.id).map(hit => ({
        value: hit.id,
        label: `${hit.name} · ${kindText(hit.kind)}${hit.path.length ? ` · ${hit.path.map(step => step.name).join(' › ')}` : ''}`,
      })))
    }).catch(() => {})
  }

  function findItems(text: string) {
    if (!kind || !text.trim()) return
    const ask = ++askedItems.current
    client.get<ItemHit[]>('/products/search', { params: { q: text, limit: 30 } }).then(({ data }) => {
      if (ask !== askedItems.current) return
      setItems(data.filter(item => linkable(kind, item.stock_form)).map(item => ({ value: item.id, label: itemLabel(item) })))
    }).catch(() => {})
  }

  async function save() {
    if (!editing || !kind) return
    let values
    try { values = await form.validateFields() } catch { return }
    const body: DexChange = { name: values.name }
    if (PARTNER_KINDS.includes(kind)) body.partner_id = values.partner_id ?? null
    if (CHARACTER_KINDS.includes(kind)) body.character_id = values.character_id ?? null
    if (kind === 'product') body.blind_box = !!values.blind_box
    if (kind === 'design') body.rarity = values.rarity ?? null
    if (kind === 'product' || kind === 'design') body.product_id = values.product_id ?? null
    const extras = extrasChange(kind, entry?.extras ?? {},
      Object.fromEntries(extraFields.map(field => [field.key, values[`x_${field.key}`]])))
    if (Object.keys(extras).length) body.extras = extras
    setSaving(true)
    try {
      if (editing.mode === 'edit') {
        if (values.parent_id !== undefined && values.parent_id !== editing.entry.parent_id) body.parent_id = values.parent_id
        onSaved(await updateEntry(editing.entry.id, body), false)
      } else {
        onSaved(await createEntry({ ...body, kind, parent_id: editing.parent?.id ?? null }), true)
      }
    } catch (cause) {
      message.error(errorText(cause))
    } finally {
      setSaving(false)
    }
  }

  // The new item fills the link; Save links it with the rest of the changes.
  function linkNewItem(created?: { id: number; sku: string }) {
    setNewItem(null)
    if (!created) return
    setItems([{ value: created.id, label: `${created.sku} · ${form.getFieldValue('name')}` }])
    form.setFieldValue('product_id', created.id)
    message.info(`${created.sku}: 保存 Save to link it`)
  }

  const title = !kind ? '' : entry ? `编辑 Edit ${kindText(kind)}` : `添加 Add ${kindText(kind)}`
  return <Drawer open={!!editing} onClose={onClose} title={title} width={Math.min(520, window.innerWidth)} destroyOnClose
    className="pc-dex-editor"
    extra={<Button type="primary" loading={saving} onClick={save}>保存 Save</Button>}>
    {kind && <Form form={form} layout="vertical" initialValues={initialValues} onFinish={save}>
      <Form.Item name="name" label="名称 Name" rules={[{ required: true, whitespace: true, message: 'A name is required' }]}>
        <Input autoFocus maxLength={200} />
      </Form.Item>
      {entry && entry.allowed_parents.length > 0 && <Form.Item name="parent_id" label="移到 Move under"
        extra={`Only a ${entry.allowed_parents.map(kindText).join(' or ')}. Type to search.`}>
        <Select showSearch filterOption={false} onSearch={findParents} options={parents} />
      </Form.Item>}
      {PARTNER_KINDS.includes(kind) && <Form.Item name="partner_id" label="联名 Partner">
        <Select allowClear showSearch optionFilterProp="label" options={partners} placeholder="—" />
      </Form.Item>}
      {CHARACTER_KINDS.includes(kind) && <Form.Item name="character_id" label="角色 Character"
        extra={ip ? `From ${ip.name}` : 'Characters come from the IP above; this entry has none.'}>
        <Select allowClear showSearch optionFilterProp="label" options={characters} disabled={!ip && !partnerId} placeholder="—" />
      </Form.Item>}
      {kind === 'product' && <Form.Item name="blind_box" label="盲盒 Blind box" valuePropName="checked"><Switch /></Form.Item>}
      {kind === 'design' && <Form.Item name="rarity" label="稀有度 Rarity"><Select allowClear options={RARITIES} placeholder="—" /></Form.Item>}
      {(kind === 'product' || kind === 'design') && <Form.Item label="POPCORE 商品 POPCORE item"
        extra={kind === 'design' ? 'A design links a confirmed design (确认款).' : 'A box or an ordinary item, not a design or a sealed set.'}>
        <div className="pc-dex-link">
          <Form.Item name="product_id" noStyle>
            <Select allowClear showSearch filterOption={false} onSearch={findItems} options={items}
              placeholder="搜索 Search POPCORE ID or name" aria-label="POPCORE item" />
          </Form.Item>
          {kind === 'product' && <Button onClick={() => {
            const name = String(form.getFieldValue('name') || '').trim()
            setNewItem({ name_cn_en: name, jizhanming: name })
          }}>新建商品 New POPCORE item</Button>}
        </div>
      </Form.Item>}
      {extraFields.length > 0 && <fieldset className="pc-dex-extras">
        <legend>图鉴资料 Collector facts</legend>
        {extraFields.map(field => <Form.Item key={field.key} name={`x_${field.key}`} label={field.label}>
          <ExtraInputFor field={field} />
        </Form.Item>)}
      </fieldset>}
    </Form>}
    {kind === 'product' && <ProductModal open={!!newItem} onClose={() => setNewItem(null)} onSaved={linkNewItem} product={newItem} />}
  </Drawer>
}
