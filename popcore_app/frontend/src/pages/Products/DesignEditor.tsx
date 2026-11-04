import { useEffect, useState } from 'react'
import { Button, Input, InputNumber, Popconfirm, Spin, Tag, message } from 'antd'
import client from '../../api/client'
import { deleteAlias, saveAlias } from '../../api/matcher'

export interface DesignItem {
  id: number; sku: string; design_name: string | null; jizhanming: string | null; price: number | null
  aliases?: { id: number; alias: string }[]
}

// The 整端·确认款 page lists designs without their prices and other names: the editor reads the design itself.
export function DesignEditorLoader({ designId, onSaved, onClose }: { designId: number; onSaved: () => void; onClose: () => void }) {
  const [design, setDesign] = useState<DesignItem | null>(null)
  const [error, setError] = useState('')
  const [version, setVersion] = useState(0)
  useEffect(() => {
    let current = true
    setError('')
    client.get(`/products/${designId}`)
      .then(r => { if (current) setDesign(r.data) })
      .catch(() => { if (current) setError('Unable to load this design.') })
    return () => { current = false }
  }, [designId, version])
  if (error) return <p className="pc-sub-error" role="alert">{error}</p>
  if (!design || design.id !== designId) return <Spin size="small" />
  // Its other names change only the design; a new name, price or removal changes the list, which reloads.
  return <DesignEditor key={`${design.id}:${version}`} design={design} onClose={onClose}
    onSaved={() => { onClose(); onSaved() }} onAliasesChanged={() => setVersion(value => value + 1)} />
}

// A manager edits one confirmed design from its row in the 子项 list (owner, 2026-11-03): its name (also once it has
// stock: the 记账名 and full name follow), its price, its other names, or removes it while it has no stock or sales.
export default function DesignEditor({ design, onSaved, onClose, onAliasesChanged = onSaved }: {
  design: DesignItem; onSaved: () => void; onClose: () => void; onAliasesChanged?: () => void
}) {
  const [name, setName] = useState(design.design_name ?? '')
  const [price, setPrice] = useState<number | null>(design.price)
  const [alias, setAlias] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const trimmed = name.trim()
  const changed = trimmed !== (design.design_name ?? '') || price !== design.price
  // Cents only; 39.99 × 100 is not a whole number in floating point, so compare with a tolerance.
  const validPrice = price == null || (Number.isFinite(price) && price >= 0 && price <= 1000000
    && Math.abs(price * 100 - Math.round(price * 100)) < 1e-6)
  const ready = changed && !!trimmed && trimmed.length <= 200 && validPrice

  async function act(work: () => Promise<unknown>, done: string, after = onSaved) {
    setBusy(true)
    setError('')
    try {
      await work()
      message.success(done)
      after()
    } catch (cause: any) {
      setError(cause?.response?.data?.error || cause?._serverMessage || 'Nothing was changed. Try again.')
    } finally {
      setBusy(false)
    }
  }
  const save = () => act(() => client.post(`/products/${design.id}/design/edit`, {
    ...(trimmed !== design.design_name ? { design_name: trimmed } : {}),
    ...(price !== design.price ? { price } : {}),
  }), `${trimmed} saved`)
  const addAlias = () => alias.trim() && act(async () => { await saveAlias(design.id, alias.trim()); setAlias('') },
    `Other name added to ${design.design_name}`, onAliasesChanged)

  return <div className="pc-design-edit" role="group" aria-label={`Edit ${design.design_name ?? design.sku}`}>
    <label>款式名字 Design name
      <Input aria-label="Design name" value={name} maxLength={200} disabled={busy}
        status={trimmed ? undefined : 'error'} onChange={event => setName(event.target.value)} /></label>
    <label>价格 Price (CA$)
      <InputNumber aria-label="Design price" min={0} max={1000000} step={0.01} precision={2} value={price}
        disabled={busy} status={validPrice ? undefined : 'error'} onChange={value => setPrice(value)} /></label>
    <div className="pc-design-aliases">
      <span>别名 Other names</span>
      {(design.aliases ?? []).map(a => <Tag key={a.id} closable={!busy}
        onClose={event => { event.preventDefault(); void act(() => deleteAlias(design.id, a.id), `${a.alias} removed`, onAliasesChanged) }}>
        {a.alias}</Tag>)}
      {!design.aliases?.length && <span className="pc-prod-muted">None</span>}
      <span className="pc-design-alias-add">
        <Input aria-label="Add other name" placeholder="加别名 Add other name" value={alias} disabled={busy}
          onChange={event => setAlias(event.target.value)} onPressEnter={addAlias} />
        <Button size="small" disabled={busy || !alias.trim()} onClick={addAlias}>添加 Add</Button>
      </span>
    </div>
    {error && <p className="pc-sub-error" role="alert">{error}</p>}
    <div className="pc-sub-add">
      <Button type="primary" size="small" disabled={!ready || busy} loading={busy} onClick={save}>保存 Save</Button>
      <Button size="small" disabled={busy} onClick={onClose}>完成 Done</Button>
      <Popconfirm title={`删除 ${design.design_name}？ Remove this design?`} okText="Remove" cancelText="Keep"
        description="Only a design with no stock or sales can be removed; its count before go-live goes with it."
        onConfirm={() => act(() => client.post(`/products/${design.id}/design/remove`), `${design.design_name} removed`)}>
        <Button size="small" danger disabled={busy}>删除 Remove</Button>
      </Popconfirm>
    </div>
  </div>
}
