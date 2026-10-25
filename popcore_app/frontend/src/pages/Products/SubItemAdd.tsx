import { useState } from 'react'
import { Button, Input, InputNumber } from 'antd'
import useCheckoutMutation from '../Sales/useCheckoutMutation'

// Managers add a product's sub-items: confirmed designs (<SKU>-01, -02…) and its 整端 sealed set
// (<SKU>-SET, 1 set = N boxes). Neither adds stock; counts and receipts do that.
export default function SubItemAdd({ productId, canAddSealedSet, onSaved }: {
  productId: number; canAddSealedSet: boolean; onSaved: () => void
}) {
  const [form, setForm] = useState<'designs' | 'sealed_set'>()
  const [designText, setDesignText] = useState('')
  const [boxes, setBoxes] = useState<number | null>(null)
  const mutation = useCheckoutMutation(() => { setForm(undefined); setDesignText(''); setBoxes(null); onSaved() })
  const locked = mutation.pending || mutation.saving
  const names = designText.split('\n').map(name => name.trim()).filter(Boolean)
  const duplicate = new Set(names.map(name => name.normalize('NFKC').toLocaleLowerCase())).size !== names.length
  const ready = form === 'designs' ? names.length > 0 && names.length <= 100 && !duplicate
    : Number.isInteger(boxes) && boxes! >= 1 && boxes! <= 100
  function save() {
    if (!ready || locked) return
    void (form === 'designs'
      ? mutation.run(`/products/${productId}/designs`, { design_names: names })
      : mutation.run(`/products/${productId}/sealed-set`, { boxes_per_set: boxes }))
  }

  if (!form) return <div className="pc-sub-add">
    <Button size="small" onClick={() => setForm('designs')}>+ 确认款 Add designs</Button>
    {canAddSealedSet && <Button size="small" onClick={() => setForm('sealed_set')}>+ 整端 Add sealed set</Button>}
  </div>
  return <div className="pc-sub-add-form">
    {form === 'designs' ? <label>款式名字，每行一个 · Design names, one per line
      <Input.TextArea aria-label="Design names, one per line" rows={4} value={designText} disabled={locked}
        onChange={event => setDesignText(event.target.value)} /></label>
      : <label>一端几盒 · Boxes per sealed set
        <InputNumber aria-label="Boxes per sealed set" min={1} max={100} precision={0} value={boxes} disabled={locked}
          onChange={value => setBoxes(value)} /></label>}
    {form === 'designs' && duplicate && <p className="pc-sub-error">Each design name must be unique.</p>}
    {mutation.notice}
    <div className="pc-sub-add">
      <Button type="primary" size="small" disabled={!ready || locked} loading={mutation.saving} onClick={save}>
        {form === 'designs' ? `保存 ${names.length || ''} 款 Save` : '保存 Save'}</Button>
      <Button size="small" disabled={locked} onClick={() => setForm(undefined)}>取消 Cancel</Button>
    </div>
  </div>
}
